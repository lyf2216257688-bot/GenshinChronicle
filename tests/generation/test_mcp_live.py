"""Live tool contracts with real small source fixtures and controlled retrieval."""

from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from genshin_corpus.evidence_io import digest
from genshin_corpus.mcp_live.study import LiveStudies, Research
from genshin_corpus.retrieval.clean_based.query_v2 import fuse_v2, validate_ancestry
from genshin_corpus.retrieval.clean_based.rerank_v2 import rank_v2
from genshin_corpus.retrieval.clean_based.source import FileBinding
from tests.generation.test_clean_evidence import EvidenceFixture, profile


class FixtureReleases:
    def __init__(self, fixture):
        self.points = {}
        self.calls = []
        for key in ("older", "newer"):
            self.points[key] = SimpleNamespace(key=key, binding=digest(key), files=FileBinding(),
                                                reader=fixture.reader, spec={"raw_run": "fixture-run"})
            self.points[key].summary = lambda key=key: {"release": key, "binding": digest(key), "raw_run": "fixture-run"}

            def search(query, parents, refs, root, check, key=key):
                validate_ancestry(query, parents, refs)
                self.calls.append((key, query.value()))
                hits = [] if query.search_text == "无结果" else [
                    {**hit, "question_id": query.turn_id, "query_id": query.query_id,
                     "round": query.round, "kind": query.kind} for hit in fixture.hits]
                value = fuse_v2(query, hits, fixture.docs, profile(), input_bundle_id=fixture.reader.bundle_id, code={})
                return rank_v2(value, fixture.scores(value) if hits else None, fixture.selection)

            self.points[key].search = search

    def selected(self, key):
        return self.points[key]

    def resolve(self, key, binding):
        release = self.selected(key)
        if release.binding != binding:
            raise ValueError("Study bound to a different release or implementation")
        return release

    def catalog(self):
        return list(self.points)

    def close(self):
        pass


class LiveResearch(EvidenceFixture):
    def setUp(self):
        super().setUp()
        self.releases = FixtureReleases(self)
        self.audit = self.root / "audit"
        self.service = LiveStudies(self.releases, self.audit, "older")

    def call(self, name, study="s", check=lambda: None, **args):
        return self.service.call(name, {"study": study, **args}, check)

    def test_live_question_and_free_follow_up(self):
        question = "敌人来临前后，人物说了什么？"
        first = self.call("open_study", request_id="start", question=question)
        h = self.call("browse_candidates", query="original")["rows"][0]["handle"]
        result = self.call("follow_query", request_id="more", parent_query="original", gap="核对触发条件",
                           search_text="出现 条件", rerank_text="人物在何种条件下说话？", evidence_handles=[h])
        self.assertEqual(result["query_ids"], ["original", "more"])
        self.assertEqual(result["query"]["original_question"], question)
        self.assertEqual(result["query"]["search_text"], "出现 条件")
        self.assertEqual(result["query"]["rerank_text"], "人物在何种条件下说话？")
        self.assertEqual(result["query"]["round"], 1)
        self.assertNotEqual(first["packet_ref"], result["packet_ref"])
        self.assertEqual(self.call("get_packet", packet_ref=first["packet_ref"])["projection"], first["projection"])
        with self.assertRaisesRegex(ValueError, "superseded"):
            self.call("check_answer", packet_ref=first["packet_ref"], answer="[E01]")

    def test_release_switch_rollback_and_source_isolation(self):
        first = self.call("open_study", request_id="start", question="任意标题")
        h = self.call("browse_candidates", query="original")["rows"][0]["handle"]
        self.service = LiveStudies(self.releases, self.audit, "newer")
        new = self.call("open_study", study="new", request_id="start", question="任意标题")
        self.assertNotEqual(first["binding"], new["binding"])
        self.assertEqual((first["release"], new["release"]), ("older", "newer"))
        with self.assertRaisesRegex(ValueError, "Foreign Packet"):
            self.call("get_packet", study="new", packet_ref=first["packet_ref"])
        with self.assertRaisesRegex(ValueError, "outside study"):
            self.call("read_source", study="new", request_id="bad", handle=h, scope="chunk")
        with self.assertRaisesRegex(ValueError, "Foreign follow-up"):
            self.call("follow_query", study="new", request_id="bad", parent_query="original",
                      gap="补查", search_text="新问题", evidence_handles=[h])
        old_read = self.call("read_source", request_id="source", handle=h, scope="chunk")
        self.assertEqual(old_read["release"], "older")
        self.service = LiveStudies(self.releases, self.audit, "older")
        self.assertEqual(self.call("request_status", study="new", request_id="start")["result"], new)
        self.assertEqual(self.call("get_read", read_ref=old_read["read_ref"]), old_read)
        self.assertEqual(len(self.releases.calls), 2)

    def test_cancelled_commit_and_worker_error_are_recoverable(self):
        executed = False
        run = Research.run

        def mark_completed(*args, **kwargs):
            nonlocal executed
            result = run(*args, **kwargs)
            executed = True
            return result

        def check():
            if executed:
                raise TimeoutError("Interrupted before commit")

        with patch.object(Research, "run", mark_completed), self.assertRaises(TimeoutError):
            self.call("open_study", request_id="start", question="问题", check=check)
        self.assertEqual(self.call("request_status", request_id="start"),
                         {"status": "no_committed_result", "last_failure_type": "TimeoutError"})
        with closing(sqlite3.connect(self.service.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM artifacts").fetchone(), (0,))
        with patch.object(self.releases.points["older"], "search", side_effect=OSError("Worker failed")):
            with self.assertRaises(OSError):
                self.call("open_study", request_id="start", question="问题")
        first = self.call("open_study", request_id="start", question="问题")
        self.assertEqual(self.call("open_study", request_id="start", question="问题"), first)
        with self.assertRaisesRegex(ValueError, "different arguments"):
            self.call("open_study", request_id="start", question="其他问题")

    def test_empty_and_corrupt_source(self):
        empty = self.call("open_study", request_id="empty", question="无结果")
        self.assertEqual(empty["projection"]["body"], [])
        self.assertEqual(self.call("check_answer", packet_ref=empty["packet_ref"], answer="无查证结果")["status"], "MISSING")
        self.call("open_study", study="real", request_id="start", question="问题")
        handle = self.call("browse_candidates", study="real", query="original")["rows"][0]["handle"]
        clean_path = self.reader.clean_root / self.ranked["candidates"][0]["source"]["clean_record"]["record_path"]
        clean_path.write_bytes(clean_path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "Clean record changed"):
            self.call("read_source", study="real", request_id="reread", handle=handle, scope="chunk")


class LiveDiscovery(unittest.TestCase):
    def test_discovery_does_not_load_runtime_or_open_audit(self):
        probe = """import runpy, sys
def audit(event, args):
    if event in ('subprocess.Popen', 'socket.connect'):
        raise AssertionError(event)
sys.addaudithook(audit)
runpy.run_module('genshin_corpus.mcp_live', run_name='__main__')
for name in ('jsonschema', 'torch', 'transformers', 'genshin_corpus.mcp_live.runtime'):
    assert name not in sys.modules, name
"""
        wire = b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{}}}\n'
        wire += b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        wire += b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
        result = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", probe, "--repo", str(Path("nonexistent")),
                                 "--audit-name", "discovery", "--release", "20261124"], input=wire,
                                capture_output=True, timeout=12, check=True)
        self.assertIn(b'"follow_query"', result.stdout)
