import json
import os
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient
from genshin_corpus.evidence_io import digest, read
from genshin_corpus.local_chat import contract, credentials, presentation
from genshin_corpus.local_chat.api import REPO, create_app
from genshin_corpus.local_chat.batches import ExecutionView
from tests.scratch import scratch_root


class ChatProductTests(unittest.TestCase):
    def setUp(self):
        scratch = scratch_root()
        # Windows' ordinary path spelling cannot remove the nested sealed attempt files.
        self.temp = TemporaryDirectory(dir=("\\\\?\\" + str(scratch)) if os.name == "nt" else scratch)
        self.addCleanup(self.temp.cleanup)
        self.open()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))

    def open(self):
        app = create_app(self.temp.name)
        self.client = TestClient(app, base_url="http://127.0.0.1", headers={"X-GC-Client": "local-chat-v1"})
        self.client.__enter__()
        self.svc = app.state.service

    def api(self, method, path, data=None, expect=200):
        response = self.client.request(method, "/api/v1" + path, json=data)
        self.assertEqual(response.status_code, expect, response.text[:800])
        return response.json()

    def chat(self):
        return self.api("POST", "/chats", {"mode": "MOCK"})["id"]

    def grant(self):
        return self.api("POST", "/batches", {"mode": "MOCK", "requests": 6, "minutes": 30, "confirm_live": False})

    def send(self, sid, text, **extra):
        data = self.api("GET", f"/chats/{sid}/draft")
        data.update(text=text, **extra)
        return data, self.api("POST", f"/chats/{sid}/turns", data)["id"]

    def finish(self, tid):
        self.assertTrue(self.svc.worker.wait_idle(30))
        turn = self.api("GET", "/turns/" + tid)
        if turn["state"] == "interrupted":
            logs = self.svc.store.root.glob("executor_runs/*/worker.log")
            self.fail(max(logs, key=lambda p: p.stat().st_mtime).read_text(encoding="utf8")[-2200:])
        return turn

    def test_across_grants_restart_context_evidence_and_feedback(self):
        first, sid = self.grant(), self.chat()
        form, tid = self.send(sid, "旧假定是甲。")
        one = self.finish(tid)
        self.assertTrue(one["has_answer"])
        self.assertEqual(self.api("POST", f"/chats/{sid}/turns", form)["id"], tid)
        _, corr = self.send(sid, "撤回甲。仅考虑此处的叙述。", kind="correction_intent", target=one["user_message"])
        self.assertTrue(self.finish(corr)["has_answer"])
        c = one["citations"][0]
        url = f"/turns/{tid}/packets/{c['packet_id']}/citations/{c['citation_id']}"
        source = self.api("GET", url)
        self.assertTrue(source["texts"] and source["parent_texts"] and source["locators"])
        self.assertTrue(source["lines"])
        self.assertTrue(all(line["text"] in source["texts"] for line in source["lines"]))
        feedback = self.api("POST", f"/turns/{tid}/feedback", {"note": "需要再读上下文", "category": "citation", "citation": c})
        self.assertTrue(self.api("GET", f"/turns/{tid}/trace")["dispositions"])
        self.api("POST", "/batches/" + first["id"] + "/close")
        self.client.__exit__(None, None, None)
        self.open()
        second = self.grant()
        _, follow = self.send(sid, "刚才这个判断依据是什么？")
        last = self.finish(follow)
        self.assertEqual(last["batch_id"], second["id"])
        store = self.svc.store
        ctx = store.load(presentation.publication(store, follow)[1]["context_ref"])
        self.assertIn(one["user_message"], ctx["organization"]["original_message_ids"])
        self.assertIn("撤回甲", str(ctx["research_state"]["notes"]))
        self.assertTrue(ctx["research_state"]["needs_review"])
        self.assertNotIn("旧假定是甲", str(ctx["recent"]))
        binding = store.binding(second["id"])
        ops = list((store.root / "batches" / second["id"] / "campaigns" / digest(binding["campaign"]) / "operations").glob("*/prepared.json"))
        wire = json.dumps(read(ops[0])["body"]["manifest"]["wire"]["messages"], ensure_ascii=False)
        self.assertIn("撤回甲", wire)
        self.assertNotIn("旧假定是甲", wire)
        self.assertEqual(self.api("GET", url), source)
        other_packet = last["citations"][0]["packet_id"]
        self.assertNotEqual(other_packet, c["packet_id"])
        self.api("GET", url.replace(c["packet_id"], other_packet), expect=404)
        self.assertEqual(self.api("GET", "/feedback")["items"][0], feedback)
        self.assertEqual(self.api("GET", f"/chats/{self.chat()}/turns")["turns"], [])
        page = self.api("GET", f"/chats/{sid}/turns?limit=1")
        self.assertEqual(page["turns"][0]["id"], follow)
        self.assertEqual(len(self.api("GET", f"/chats/{sid}/turns?before={page['before']}")["turns"]), 2)
        self.api("GET", url.replace(c["packet_id"], "old-packet"), expect=404)
        sources = store.load(presentation.publication(store, tid)[1]["sources_ref"])
        rid = sources["packet"]["citations"][c["citation_id"]]["receipt_id"]
        (store.root / sources["reads"][rid]["path"]).write_text("{}", encoding="utf8")
        self.api("GET", url, expect=409)

    def test_draft_conflicts_restart_without_dispatch_and_expiry(self):
        batch, sid = self.grant(), self.chat()
        original = self.api("GET", f"/chats/{sid}/draft")
        saved = self.api("PUT", f"/chats/{sid}/draft", {**original, "text": "待回答"})
        self.api("PUT", f"/chats/{sid}/draft", original, expect=409)
        with patch.object(self.svc.worker, "schedule"):
            tid = self.api("POST", f"/chats/{sid}/turns", saved)["id"]
        self.client.__exit__(None, None, None)
        self.open()
        self.assertTrue(self.api("GET", "/turns/" + tid)["can_resume"])
        self.assertFalse(self.api("POST", f"/chats/{sid}/turns", saved)["watching"])
        self.assertEqual(self.api("GET", "/batches")["items"][0]["requests"], 0)
        with patch("genshin_corpus.local_chat.batches.time.time", return_value=batch["expires"] + 1):
            self.api("POST", f"/turns/{tid}/resume", expect=409)
        self.api("POST", f"/turns/{tid}/resume")
        self.assertTrue(self.finish(tid)["has_answer"])
        self.assertEqual(self.api("GET", "/batches")["items"][0]["requests"], 1)
        events = self.client.get(f"/api/v1/turns/{tid}/events")
        self.assertIn("text/event-stream", events.headers["content-type"])
        stream = [json.loads(line[6:]) for line in events.text.splitlines() if line.startswith("data: ")]
        self.assertTrue(any(ev["kind"] == "draft" for ev in stream))
        after = stream[1]["sequence"]
        replay = self.client.get(f"/api/v1/turns/{tid}/events", headers={"Last-Event-ID": str(after)})
        self.assertTrue(all(json.loads(line[6:])["sequence"] > after for line in replay.text.splitlines() if line.startswith("data: ")))

    def test_stop_and_unknown_do_not_resend(self):
        self.grant()
        sid = self.chat()
        with patch.object(self.svc.worker, "schedule"):
            _, tid = self.send(sid, "取消这个轮次")
        self.api("POST", f"/turns/{tid}/stop")
        self.assertEqual(self.finish(tid)["state"], "cancelled")
        with patch.object(self.svc.worker, "schedule"):
            _, tid = self.send(sid, "测试中断的响应")
        from genshin_corpus.rag.clean_conversation_graph import run_turn
        from genshin_corpus.rag.clean_mock import FixturePipeline, MockOpener, fixture_inputs
        from genshin_corpus.retrieval.clean_based.source import InputReader
        store = self.svc.store
        view = ExecutionView(store, store.binding(store.input_for(tid)["batch_id"]), tid)
        with InputReader(fixture_inputs(REPO, store.root / "mock_corpus")) as reader:
            run_turn(view, tid, FixturePipeline(reader, REPO), opener_factory=lambda *a: MockOpener("未完成", disconnect=True))
        turn = self.api("GET", "/turns/" + tid)
        self.assertEqual(turn["state"], "unknown")
        self.assertFalse(turn["has_answer"] or turn["can_resume"])
        self.api("POST", f"/turns/{tid}/resume", expect=409)
        self.assertEqual(self.api("GET", "/batches")["items"][0]["requests"], 1)

    def test_parked_turn_keeps_original_grant_and_does_not_block_new_work(self):
        first, sid = self.grant(), self.chat()
        with patch.object(self.svc.worker, "schedule"):
            _, parked = self.send(sid, "昨天尚未开始的问题")
        self.client.__exit__(None, None, None)
        self.open()
        # Simulate tomorrow for the old permit only. The new grant has its own expiry.
        with patch("genshin_corpus.local_chat.batches.time.time", return_value=first["expires"] + 1):
            second = self.grant()
            self.assertFalse(self.api("GET", "/turns/" + parked)["can_resume"])
        _, tid = self.send(sid, "继续先前的讨论")
        self.assertTrue(self.finish(tid)["has_answer"])
        self.assertEqual(self.api("GET", "/turns/" + tid)["batch_id"], second["id"])
        self.assertEqual(self.api("GET", "/turns/" + parked)["batch_id"], first["id"])
        self.assertEqual(self.svc.store.job(parked)["status"], "interrupted")

    def test_contract_isolation_and_consent(self):
        from genshin_corpus.local_chat.store import ProductStore
        historical = self.svc.store.root / "old-workbench"
        historical.mkdir()
        receipt = historical / "result.json"
        receipt.write_bytes(b"original")
        with self.assertRaises(ValueError):
            ProductStore(historical)
        self.assertEqual(list(historical.iterdir()), [receipt])
        self.assertEqual(receipt.read_bytes(), b"original")
        self.assertEqual(contract.specification(), json.loads((REPO / "frontend/openapi.json").read_text(encoding="utf8")))
        self.api("POST", "/batches", {"mode": "LIVE", "requests": 4, "minutes": 5, "confirm_live": False}, 422)
        self.api("POST", "/chats", {"mode": "OTHER"}, 422)
        sid = self.chat()
        self.api("GET", f"/chats/{sid}/turns?limit=500", expect=422)
        self.api("POST", f"/chats/{sid}/turns", {**self.api("GET", f"/chats/{sid}/draft"), "text": "hi"}, 409)
        self.assertEqual(self.client.post("/api/v1/chats", headers={"Origin": "http://foreign.invalid"},
                                         json={"mode": "MOCK"}).status_code, 403)
        self.grant()
        with patch("genshin_corpus.local_chat.batches.adapter_identity", return_value={"version": "different"}):
            self.assertEqual(self.api("GET", "/batches")["items"][0]["state"], "read_only")
            self.api("POST", f"/chats/{sid}/turns", {**self.api("GET", f"/chats/{sid}/draft"), "text": "追问"}, 409)
            self.assertEqual(self.api("GET", f"/chats/{sid}/turns")["turns"], [])

    def test_g7_failure_shapes_use_only_published_bindings(self):
        from tests.local_chat.citation_cases import citation_cases
        self.grant()
        unbound, invalid, unsupported = citation_cases(self.svc.store, REPO, self.chat())
        first = self.api("GET", "/turns/" + unbound)
        self.assertTrue(first["has_answer"])
        self.assertEqual(first["state"], "partial")
        self.assertEqual(first["citations"], [])
        second = self.api("GET", "/turns/" + invalid)
        self.assertFalse(second["has_answer"] or second["citations"])
        self.assertEqual(second["stop_reason"], "citation_validation_failed")
        third = self.api("GET", "/turns/" + unsupported)
        self.assertTrue(third["has_answer"])
        c = third["citations"][0]
        detail = self.api("GET", f"/turns/{unsupported}/packets/{c['packet_id']}/citations/E01")
        self.assertEqual(detail["context"]["来源与支持范围"]["semantic_support"], "NOT_EVALUATED")

    def test_two_tabs_share_submission_and_closing_grant_stops_queue(self):
        from concurrent.futures import ThreadPoolExecutor
        b, sid = self.grant(), self.chat()
        draft = {**self.api("GET", f"/chats/{sid}/draft"), "text": "同时发送"}
        with patch.object(self.svc.worker, "schedule") as schedule, ThreadPoolExecutor(2) as pool:
            turns = list(pool.map(lambda _: self.api("POST", f"/chats/{sid}/turns", draft), range(2)))
        self.assertEqual(turns[0]["id"], turns[1]["id"])
        self.assertEqual(schedule.call_count, 1)
        other = self.chat()
        foreign = {**self.api("GET", f"/chats/{other}/draft"), "text": "纠正", "target": turns[0]["user_message"],
                   "kind": "correction_fact"}
        self.api("POST", f"/chats/{other}/turns", foreign, 422)
        self.api("POST", "/batches/" + b["id"] + "/close")
        self.assertEqual(self.finish(turns[0]["id"])["state"], "cancelled")
        self.assertEqual(self.api("GET", "/batches")["items"][0]["requests"], 0)

    def test_finite_request_quota_is_shared_by_distinct_chats(self):
        grant = self.api("POST", "/batches", {"mode": "MOCK", "requests": 2, "minutes": 10, "confirm_live": False})
        first, second = self.chat(), self.chat()
        for sid in (first, second):
            _, tid = self.send(sid, "读取本会话的原文")
            t = self.finish(tid)
            self.assertTrue(t["has_answer"])
            self.assertEqual(t["batch_id"], grant["id"])
            self.assertEqual(len(self.api("GET", f"/chats/{sid}/turns")["turns"]), 1)
        remaining = self.api("GET", "/batches")["items"][0]
        self.assertEqual((remaining["state"], remaining["requests"]), ("exhausted", 2))
        draft = self.api("GET", f"/chats/{first}/draft")
        self.api("POST", f"/chats/{first}/turns", {**draft, "text": "额度用尽后的追问"}, 409)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_live_settings_store_only_protected_key_inside_data_root(self):
        endpoint = credentials.OFFICIAL_ENDPOINT
        key = "test-key-for-local-storage-only"
        folder = self.svc.store.root
        file = folder / credentials.FILE_NAME
        with patch.dict(os.environ, {credentials.KEY_ENV: ""}):
            info = self.api("GET", "/runtime")
            self.assertEqual(info["data_root"], str(folder))
            self.assertEqual(info["key_source"], "missing")
            for invalid in ("http://api.deepseek.com", "https://api.deepseek.com@other.invalid",
                            endpoint + "?key=extra"):
                self.api("PUT", "/live-settings", {"endpoint": invalid, "api_key": key}, 422)
            self.api("PUT", "/live-settings", {"endpoint": endpoint, "api_key": "test\r\ninjected"}, 422)
            self.assertFalse(file.exists())
            self.assertEqual(self.api("PUT", "/live-settings",
                {"endpoint": credentials.OFFICIAL_BASE, "api_key": key})["key_source"], "local")
            self.assertEqual(credentials.read_credential(folder).key, key)
            self.assertNotIn(key.encode(), file.read_bytes())
            self.assertNotIn(key, self.client.get("/api/v1/runtime").text)
            self.assertFalse(self.api("GET", "/batches")["items"])

            self.client.__exit__(None, None, None)
            self.open()
            self.assertTrue(self.api("GET", "/runtime")["key_configured"])
            self.assertEqual(credentials.read_credential(folder).key, key)
            self.assertEqual(self.api("DELETE", "/live-settings")["key_source"], "missing")
            self.assertFalse(file.exists())

    @unittest.skipUnless(os.getenv("GC_TEST_LOCAL_PROFILE") == "1", "requires local sealed G3 route evidence")
    def test_official_g5_entry_with_in_memory_transport_and_no_socket(self):
        """Exercise real permit validation, G5, shared budget and worker entry without a provider connection."""
        import socket
        import sys
        from genshin_corpus.local_chat import worker
        from genshin_corpus.rag import clean_mock
        from genshin_corpus.retrieval.clean_based.source import InputReader
        from tests.generation.test_clean_research import Pipeline
        store = self.svc.store
        sid = self.api("POST", "/chats", {"mode": "LIVE"})["id"]
        key = "offline-test-only"
        with patch.dict(os.environ, {credentials.KEY_ENV: key if os.name != "nt" else ""}), \
                patch.object(socket.socket, "connect", side_effect=AssertionError("No provider socket")) as network:
            if os.name == "nt":
                self.api("PUT", "/live-settings", {"endpoint": credentials.OFFICIAL_ENDPOINT, "api_key": key})
            grant = contract.Grant(mode="LIVE", requests=2, minutes=5, confirm_live=True)
            b = self.svc.open_batch(grant)
            if os.name == "nt":
                self.api("DELETE", "/live-settings", expect=409)
            with patch.object(self.svc.worker, "schedule"):
                d = contract.Draft(**{**store.get_draft(sid), "text": "测试本地接线"})
                tid = self.svc.submit(sid, d)["id"]
            folder = store.root / "executor_runs" / "offline-wiring"
            folder.mkdir(parents=True)
            manifest = clean_mock.fixture_inputs(REPO, store.root / "mock_corpus")
            sender = clean_mock.MockOpener("离线协议夹具[E01]")
            class TinyPipeline(Pipeline):
                def close(self): pass
            with InputReader(manifest) as reader, patch.object(sys, "argv", ["worker", "--root", str(store.root),
                    "--repo", str(REPO), "--turn", tid, "--run", str(folder)]), \
                    patch("genshin_corpus.rag.clean_research_live.ResearchPipeline", return_value=TinyPipeline(reader, REPO)), \
                    patch("genshin_corpus.generation.clean_chat_transport.build_opener", return_value=sender), \
                    patch.object(sender, "open", wraps=sender.open) as sent, \
                    patch.object(clean_mock, "MODEL", "deepseek-flash"):
                worker.main()
            self.assertEqual(sender.calls, 1)
            request = sent.call_args.args[0]
            self.assertEqual(request.full_url, credentials.OFFICIAL_ENDPOINT)
            self.assertTrue(request.get_header("Authorization") == "Bearer " + key)
            self.assertEqual(self.svc.turn(tid)["answer"], "离线协议夹具[E01]")
            citation = self.svc.turn(tid)["citations"][0]
            self.assertTrue(self.svc.citation(tid, citation["packet_id"], citation["citation_id"])["texts"])
            self.assertTrue(self.svc.trace(tid)["queries"])
            self.assertEqual(self.svc.batches()["items"][0]["requests"], 1)
            self.svc.close_batch(b["id"])
            network.assert_not_called()
