"""Offline MCP acceptance using a tiny Clean corpus and deterministic frozen rankings."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import json
import os
from queue import Queue
import sqlite3
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from genshin_corpus.evidence_io import digest, read, sha, write
from genshin_corpus.generation.clean_evidence import EvidenceReader, assemble, fragment_content
from genshin_corpus.mcp_offline.archive import Archive
from genshin_corpus.mcp_offline.protocol import serve
from genshin_corpus.mcp_offline.study import Studies
from genshin_corpus.retrieval.clean_based.query_v2 import fuse_v2
from genshin_corpus.retrieval.clean_based.rerank_v2 import rank_v2
from tests.generation.test_clean_evidence import EvidenceFixture, CONTEXT, BUDGET


class OfflineStudies(EvidenceFixture):
    def setUp(self):
        super().setUp()
        def pin(path):
            return {"path": path.relative_to(self.root).as_posix(), "sha256": sha(path)}
        def save(name, obj):
            path = self.root / (name + ".json")
            write(path, obj)
            return pin(path)
        inputs = self.root / "inputs/manifest.json"
        index = save("index", {"input_manifest": str(inputs), "input_bundle_id": self.reader.bundle_id})
        index_ref = {"manifest": index["path"], "manifest_sha256": index["sha256"]}
        _, handles, receipts = self.receipts()
        self.golden = assemble([self.ranked], receipts[:6], context=CONTEXT, budget=BUDGET)
        original = save("original", self.ranked)
        sub = replace(self.query, query_id="sub", kind="subquestion", round=1, parent_query_ids=("original",),
                      parent_evidence_refs=(next(iter(handles.values())),), trigger_gap="核查发言条件")
        hits = [{**h, "query_id": sub.query_id, "round": sub.round, "kind": sub.kind} for h in self.hits]
        candidates = fuse_v2(sub, hits, self.docs, self.ranked["profile"],
                             input_bundle_id=self.reader.bundle_id, code={"fixture":True})
        subrank = rank_v2(candidates, self.scores(candidates), self.selection)
        self.catalog = {"version": "gc-mcp-frozen-catalog-1", "source": "synthetic-fixture", "raw_run": "fixture-run",
            "input": pin(inputs), "assets": {"retrieval": save("retrieval", self.ranked["profile"]),
            "reranking": save("reranking", self.selection), "generation": save("generation", {"version": "gc-g5-official-4"}),
            "index": save("indices", {"source": {"input_bundle_id": self.reader.bundle_id}, "dense": index_ref, "lexical": index_ref})},
            "cases": [{"id": "tiny", "question": self.query.original_question,
                       "initial_sources": save("first", {"packet": self.golden, "assembly_context": CONTEXT}),
                       "follow_ups": {"sub": sub.value()}, "groups": {"original": original, "sub": save("sub", subrank)}}]}
        self.catalog_path = self.root / "config/mcp-offline-v1.json"
        write(self.catalog_path, self.catalog)
        self.service = self.reconnect()

    def test_empty_frozen_query_has_no_invented_evidence(self):
        ranked = rank_v2(fuse_v2(self.query, [], {}, self.ranked["profile"],
                                 input_bundle_id=self.reader.bundle_id, code={}), None, self.selection)
        empty = assemble([ranked], [], context=CONTEXT, budget=BUDGET)
        case = deepcopy(self.catalog["cases"][0])
        case["id"] = "empty"
        for name, obj in (("empty-ranked",ranked), ("empty-first",{"assembly_context":CONTEXT,"packet":empty})):
            path = self.root / (name + ".json")
            write(path, obj)
            ref = {"path":path.name,"sha256":sha(path)}
            if name == "empty-ranked":
                case["groups"] = {"original":ref}
                case["follow_ups"] = {}
            else:
                case["initial_sources"] = ref
        self.catalog["cases"].append(case)
        self.catalog_path.write_text(json.dumps(self.catalog), encoding="utf8")
        self.service = self.reconnect()
        result = self.call("open_study", case="empty", request_id="zero")
        self.assertEqual((result["packet_id"],result["projection"]["body"]), (empty["packet_id"],[]))

    def reconnect(self):
        return Studies(Archive(self.root), self.root / "audit")

    def call(self, name, **args):
        return self.service.call(name, {"study": "a", **args}, lambda: None)

    def open(self, **kw):
        return self.call("open_study", case="tiny", request_id="open", **kw)

    def test_first_packet_exact_and_all_ranked_candidates_reachable(self):
        first = self.open()
        self.assertEqual(first["packet_id"], self.golden["packet_id"])
        self.assertTrue(first["first_packet_parity"])
        rows = self.call("browse_candidates", query="original")["rows"]
        self.assertEqual([r["rank"] for r in rows], list(range(1, len(rows) + 1)))
        result = self.call("check_answer", packet_ref=first["packet_ref"], answer="核对 [E01]")
        self.assertEqual(result["citations"]["E01"], self.golden["citations"]["E01"])
        self.assertEqual(result["semantic_support"], "NOT_EVALUATED")
        self.assertEqual(self.call("check_answer", packet_ref=first["packet_ref"], answer="缺少引用")["status"], "MISSING")
        with self.assertRaises(ValueError):
            self.call("check_answer", packet_ref=first["packet_ref"], answer="[E99999]")

    def test_source_expansion_pagination_and_selection(self):
        self.open()
        _, handles, _ = self.receipts()
        direct = EvidenceReader(self.reader, verified_parent_ranges=True)
        direct.register(self.ranked)
        rows = self.call("browse_candidates", query="original")["rows"]
        target = next((n for n,c in enumerate(self.ranked["candidates"]) if c["source"]["graph_locator"]), None)
        self.assertIsNotNone(target)
        h = rows[target]["handle"]
        for scope in ("chunk", "parent", "natural_parent", "graph"):
            with self.subTest(scope=scope), patch("genshin_corpus.mcp_offline.study.PAGE_CHARS", 2000):
                expected = direct.read(list(handles.values())[target], scope)
                first = self.call("read_source", request_id=scope, handle=h, scope=scope)
                projection = first["projection"]
                if "body" in projection:
                    body = projection["body"]
                else:
                    parts = [projection["partial_json_text"]]
                    while projection["next_offset"] is not None:
                        offset = projection["next_offset"]
                        projection = self.call("get_read", read_ref=first["read_ref"], offset=offset)["projection"]
                        parts.append(projection["partial_json_text"])
                    body = json.loads("".join(parts))
                self.assertEqual(body, fragment_content(expected["fragments"][0]))
                self.assertEqual(first["receipt_id"], expected["receipt_id"])
        assembled = self.call("assemble_packet", fragment_ids=[first["fragment_id"]], request_id="assemble")
        self.assertNotEqual(assembled["packet_id"], self.golden["packet_id"])

    def test_ancestry_session_and_packet_scopes(self):
        a = self.open()
        b = self.open(study="b")
        h = self.call("browse_candidates", query="original")["rows"][0]["handle"]
        with self.assertRaises(ValueError):
            self.call("read_source", study="b", request_id="bad", handle=h, scope="chunk")
        with self.assertRaises(ValueError):
            self.call("get_packet", packet_ref=b["packet_ref"])
        args = {"query":"sub", "parent_query":"original", "evidence_handles":[], "gap":"缺失前提", "request_id":"sub"}
        with self.assertRaises(ValueError):
            self.call("follow_query", **args)
        args["evidence_handles"] = a["available_queries"][0]["evidence_handles"]
        updated = self.call("follow_query", **args)
        self.assertEqual(updated["query_ids"], ["original","sub"])
        with self.assertRaises(ValueError):
            self.call("check_answer", packet_ref=a["packet_ref"], answer="[E01]")
        with self.assertRaises(ValueError):
            self.call("follow_query", **{**args,"query":"arbitrary","request_id":"unknown"})

    def test_transactional_recovery_concurrency_and_tamper(self):
        services = [self.service, self.reconnect()]
        args = {"case":"tiny", "request_id":"open", "study":"a"}
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda s: s.call("open_study", args, lambda: None), services))
        self.assertEqual(replies[0], replies[1])
        self.service = self.reconnect()
        self.assertEqual(replies[0], self.call("request_status", request_id="open")["result"])
        with self.assertRaises(ValueError):
            self.call("open_study", case="other", request_id="open")
        row = self.call("browse_candidates", query="original")["rows"][0]
        before = read(self.root / "original.json")["candidates"][0]["source"]["clean_record"]
        path = self.reader.clean_root / before["record_path"]
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            self.call("read_source", request_id="drift", handle=row["handle"], scope="chunk")
        self.assertEqual(self.call("request_status", request_id="drift")["status"], "no_committed_result")
        with closing(sqlite3.connect(self.service.path)) as db, db:
            db.execute("UPDATE artifacts SET value='{}' WHERE hash=(SELECT result FROM operations LIMIT 1)")
        with self.assertRaisesRegex(ValueError, "Audit artifact changed"):
            self.call("request_status", request_id="open")

    def test_cancel_before_commit_releases_study_and_version_drift_fails(self):
        completed = threading.Event()
        original = self.service.execute
        def before_commit(*args):
            result = original(*args)
            completed.set()
            return result
        def stop():
            if completed.is_set():
                raise TimeoutError()
        with patch.object(self.service, "execute", before_commit), self.assertRaises(TimeoutError):
            self.service.call("open_study", {"case":"tiny","study":"a","request_id":"open"}, stop)
        with closing(sqlite3.connect(self.service.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM artifacts").fetchone(), (0,))
        self.open()
        self.catalog_path.write_bytes(self.catalog_path.read_bytes()+b" ")
        self.service = self.reconnect()
        with self.assertRaisesRegex(ValueError, "different release"):
            self.call("request_status", request_id="open")

    def test_stdio_entrypoint_discovers_without_assets_and_calls_without_provider(self):
        proc = subprocess.Popen([sys.executable, "-X", "utf8", "-B", "-m", "genshin_corpus.mcp_offline",
                                 "--repo", str(self.root), "--audit-name", "transport"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env={k:v for k,v in os.environ.items() if "API_KEY" not in k})
        def send(value):
            proc.stdin.write((json.dumps({"jsonrpc":"2.0", **value})+"\n").encode())
            proc.stdin.flush()
            if "id" in value:
                reply = json.loads(proc.stdout.readline())
                self.assertEqual(reply["id"], value["id"])
                return reply
        try:
            self.assertIn("error", send({"id":1,"method":"tools/list"}))
            init = send({"id":2,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},
                        "clientInfo":{"name":"fixture","version":"1"}}})
            self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
            send({"method":"notifications/initialized"})
            self.assertIn("open_study", [t["name"] for t in send({"id":3,"method":"tools/list"})["result"]["tools"]])
            self.assertFalse((self.root/"data").exists())
            params = {"name":"open_study","arguments":{"study":"offline","case":"tiny","request_id":"one"}}
            result = send({"id":4,"method":"tools/call","params":params})["result"]
            self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
            self.assertEqual(result["structuredContent"]["packet_id"], self.golden["packet_id"])
            params["arguments"]["path"] = "../outside"
            self.assertEqual(send({"id":5,"method":"tools/call","params":params})["error"]["code"], -32602)
        finally:
            proc.stdin.close()
            proc.wait(timeout=10)
            self.assertEqual((proc.returncode, proc.stderr.read()), (0,b""))
            proc.stdout.close()
            proc.stderr.close()


class TransportCancellation(unittest.TestCase):
    def test_discovery_without_assets_weights_or_network(self):
        # The cold entry point must answer discovery without opening an evidence or inference runtime.
        probe = """import runpy, sys
def audit(event, args):
    if event in ('subprocess.Popen', 'socket.connect'):
        raise AssertionError(event)
sys.addaudithook(audit)
runpy.run_module('genshin_corpus.mcp_offline', run_name='__main__')
for name in ('jsonschema', 'torch', 'transformers', 'genshin_corpus.mcp_offline.archive'):
    assert name not in sys.modules, name
"""
        messages = [
            {"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25",
                                                         "clientInfo": {}, "capabilities": {}}},
            {"method": "notifications/initialized"},
            {"id": 2, "method": "tools/list"},
        ]
        wire = "".join(json.dumps({"jsonrpc": "2.0", **msg}) + "\n" for msg in messages)
        result = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", probe, "--repo", "absent-corpus",
                                 "--audit-name", "discovery"], input=wire.encode(), capture_output=True,
                                timeout=12, check=True)
        init, discovered = map(json.loads, result.stdout.splitlines())
        self.assertEqual(init["result"]["protocolVersion"], "2025-11-25")
        self.assertIn("open_study", [tool["name"] for tool in discovered["result"]["tools"]])
        self.assertEqual(result.stderr, b"")

    def test_busy_call_and_cancel_leave_transport_available(self):
        incoming, replies = Queue(), Queue()
        entered, finished = threading.Event(), threading.Event()
        class Input:
            def readline(self, size):
                return incoming.get(timeout=8)
        class Output:
            def write(self, wire):
                replies.put(json.loads(wire))
            def flush(self):
                pass
        class Slow:
            def call(self, name, args, check):
                entered.set()
                try:
                    while not finished.wait(.01):
                        check()
                finally:
                    finished.set()
        def post(method, rid=None, **params):
            value = {"jsonrpc":"2.0","method":method,"params":params}
            if rid is not None:
                value["id"] = rid
            incoming.put(json.dumps(value).encode())
        thread = threading.Thread(target=serve, args=(Slow,Input(),Output()))
        thread.start()
        try:
            post("initialize", 1, protocolVersion="2025-11-25", clientInfo={}, capabilities={})
            self.assertIn("result", replies.get(timeout=3))
            post("notifications/initialized")
            post("tools/call", 2, name="list_cases", arguments={})
            self.assertTrue(entered.wait(5))
            post("tools/call", 3, name="list_cases", arguments={})
            self.assertEqual(replies.get(timeout=3)["error"]["code"], -32001)
            post("notifications/cancelled", requestId=2)
            self.assertTrue(finished.wait(3))
            post("ping", 4)
            self.assertEqual(replies.get(timeout=3)["id"], 4)
        finally:
            finished.set()
            incoming.put(b"")
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
