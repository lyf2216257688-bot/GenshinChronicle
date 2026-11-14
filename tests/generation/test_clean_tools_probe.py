"""Offline coverage of the separately authorized G3 tools-first probe, with sockets blocked."""
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import URLError

from genshin_corpus.evidence_io import read, sha, verify
from genshin_corpus.generation import clean_tools_probe as probe
from . import test_clean_chat as f


def native_opener(*, stream=False):
    if stream: return f.Opener(f.sse(f.stream_values(tools=True)), stream=True)
    message = {"role": "assistant", "content": None, "tool_calls": [f.call(), f.call("c2", arguments='{"handle":"tide"}')]}
    return f.Opener(json.dumps(f.response(message, "tool_calls")).encode())


def final_opener(text, *, stream=False):
    if stream:
        values = [f.chunk({"role": "assistant", "content": ""}), f.chunk({"content": text}),
                  f.chunk({}, "stop"), f.chunk({}, choices=[], usage=f.response()["usage"])]
        return f.Opener(f.sse(values), stream=True)
    return f.Opener(json.dumps(f.response({"role": "assistant", "content": text})).encode())


def openers():
    return [native_opener(), final_opener("64"), final_opener(probe.TEXT, stream=True),
            native_opener(stream=True), final_opener("64", stream=True)]


class ToolsProbeTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[2] / "tmp-g3"
        scratch.mkdir(exist_ok=True)
        self.tmp = TemporaryDirectory(dir=scratch); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name); self.root = self.base / "data/generation/p"
        guard = patch.object(socket.socket, "connect", side_effect=AssertionError("No network"))
        mocked = guard.start(); self.addCleanup(guard.stop); self.addCleanup(mocked.assert_not_called)
        with patch.object(Path, "cwd", return_value=self.base): self.preparation = probe.prepare(self.root)

    def approve_fixture(self):
        probe.approve(self.root, self.preparation["preparation_sha256"], "OFFLINE_FIXTURE_NOT_USER_AUTHORIZATION")

    def run_fixture(self, replies, **kwargs):
        with patch.dict("os.environ", {"TOKENMETRO_API_KEY": "fixture-secret"}), patch(
                "genshin_corpus.generation.clean_chat_transport.build_opener", side_effect=replies) as build:
            result = probe.run(self.root, notify=lambda value: None, **kwargs)
        return result, build.call_count

    def test_preparation_is_offline_five_new_disabled_requests_and_exact_budget(self):
        self.assertFalse((self.root / "authorization.json").exists())
        self.assertFalse((self.root / "sessions.sqlite").exists())
        plan = probe.prepared(self.root)
        self.assertEqual(sum(c["max_requests"] for c in plan["cases"]), 5)
        self.assertEqual(plan["limits"]["campaign_tokens"], 5 * (8192 + 1024))
        for case in plan["cases"]:
            self.assertEqual(case["initial_manifest"]["wire"]["thinking"], {"type": "disabled"})
            self.assertNotIn(case["id"], {c[0] for c in probe.clean_probe.CASES})
        with self.assertRaises(FileNotFoundError): probe.run(self.root)
        with self.assertRaises(ValueError): probe.approve(self.root, "wrong", "fixture")
        self.assertFalse((self.root / "authorization.json").exists())

    def test_local_stream_cut_checks_allow_valid_sse_spacing_and_escaped_json_keys(self):
        req = next(q for _, q, _ in probe.requests() if q.stream and not q.tools)
        raw = final_opener(probe.TEXT, stream=True).body
        for body in (raw, raw.replace(b"data: ", b"data:"), raw.replace(b'"usage"', b'"\\u0075sage"')):
            checked = probe.stream_replay_checks(req.manifest(), body)
            self.assertTrue(checked["bytewise_equal"])
            self.assertEqual(len(checked["rejections"]), 3)

    def test_five_attempts_full_native_loops_streams_receipts_and_replay(self):
        self.approve_fixture(); replies = openers()
        result, sends = self.run_fixture(replies)
        self.assertEqual(sends, 5)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertTrue(all(c["status"] == "OBSERVED_LIMITED_PASS" for c in result["cases"]))
        self.assertEqual(result["accounting"]["request_reservations"], 5)
        self.assertEqual(result["accounting"]["known_tokens"], 150)
        self.assertEqual(len(list((self.root / "tool-receipts").glob("*/receipt.json"))), 4)
        for index in (1, 4):
            messages = json.loads(replies[index].calls[0][0].data)["messages"]
            self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "tool", "tool"])
            self.assertEqual([json.loads(m["content"])["value"] for m in messages[-2:]], [23, 41])
            self.assertEqual({m["tool_call_id"] for m in messages[-2:]}, {c["id"] for c in messages[2]["tool_calls"]})
        self.assertEqual(len(result["cases"][1]["stream_replay_checks"]), 1)
        self.assertEqual(len(result["cases"][2]["stream_replay_checks"]), 2)
        self.assertIn("NOT_TESTED", result["thinking_tools"])
        self.assertEqual(result["active_research"], "NOT_EVALUATED")
        verify(self.root / "report")
        with patch("genshin_corpus.generation.clean_chat_transport.build_opener", side_effect=AssertionError("No send")):
            self.assertEqual(probe.run(self.root), result)

    def test_known_case_failure_does_not_block_predefined_independent_cases(self):
        self.approve_fixture()
        result, sends = self.run_fixture([final_opener("no tools")] + openers()[2:])
        self.assertEqual(sends, 4)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual([c["status"] for c in result["cases"]],
                         ["NOT_QUALIFIED", "OBSERVED_LIMITED_PASS", "OBSERVED_LIMITED_PASS"])
        self.assertEqual(result["cases"][0]["tool_receipts"], [])

    def test_protocol_failure_halts_batch_without_retry(self):
        self.approve_fixture()
        wrong = f.Opener(json.dumps(f.response(model="wrong")).encode())
        result, sends = self.run_fixture([wrong])
        self.assertEqual(sends, 1)
        self.assertEqual(result["status"], "STOPPED")
        self.assertEqual(result["accounting"]["request_reservations"], 1)
        self.assertFalse((self.root / "tool-receipts").exists())

    def test_partial_stream_unknown_keeps_reservation_and_never_advances_tools(self):
        self.approve_fixture()
        broken = f.Opener(stream=True, pieces=[f.sse([f.chunk({"role": "assistant", "content": "半截"})], done=False), URLError("fixture")])
        result, sends = self.run_fixture(openers()[:2] + [broken])
        self.assertEqual(sends, 3)
        self.assertEqual(result["status"], "STOPPED")
        self.assertEqual(result["accounting"]["request_reservations"], 3)
        self.assertEqual(result["cases"][-1]["outcomes"][0]["state"], "UNKNOWN")
        self.assertEqual(len(result["cases"]), 2)
        self.assertEqual(len(list((self.root / "tool-receipts").glob("*/receipt.json"))), 2)

    def test_crash_after_tool_receipt_reuses_sealed_first_request(self):
        self.approve_fixture()
        original = probe.tool_reply
        count = 0
        def crash(*args, **kwargs):
            nonlocal count
            reply = original(*args, **kwargs); count += 1
            if count == 1: raise SystemExit("fixture crash")
            return reply
        first = openers()[0]
        with patch.object(probe, "tool_reply", side_effect=crash), self.assertRaises(SystemExit):
            self.run_fixture([first])
        self.assertEqual(len(first.calls), 1)
        receipt = next((self.root / "tool-receipts").glob("*/receipt.json")); before = sha(receipt)
        with self.assertRaises(ValueError): probe.run(self.root)
        result, sends = self.run_fixture(openers()[1:], continue_probe=True)
        self.assertEqual(sends, 4)
        self.assertEqual(sha(receipt), before)
        self.assertEqual(result["accounting"]["request_reservations"], 5)
        self.assertEqual(result["status"], "COMPLETE")

    def test_changed_preparation_or_expired_permit_cannot_dispatch(self):
        with patch.object(probe, "runner_identity", return_value={}):
            with self.assertRaises(ValueError): probe.approve(self.root, self.preparation["preparation_sha256"], "fixture")
        self.approve_fixture()
        expires = read(self.root / "authorization.json")["expires_at"]
        with patch("genshin_corpus.generation.clean_chat_transport.time.time", return_value=expires + 1):
            result, sends = self.run_fixture([])
        self.assertEqual(sends, 0)
        self.assertEqual(result["status"], "STOPPED")
        self.assertEqual(result["accounting"]["request_reservations"], 0)


if __name__ == "__main__": unittest.main()
