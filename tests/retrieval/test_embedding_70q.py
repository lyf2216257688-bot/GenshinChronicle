from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import uuid4

from genshin_corpus.rag.__main__ import main as query_main
from genshin_corpus.rag.embedding_70q import PROFILES, export_review, tally_review, write_jsonl
from genshin_corpus.ui.runtime import PreparedStateOwner


class EmbeddingComparisonTests(TestCase):
    def test_review_export_keeps_references_and_pending_verdicts_separate(self):
        root = Path("tmp") / f"paired-evidence-{uuid4().hex}"
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root)
        question = SimpleNamespace(question_id="Q001", question="问题")
        ref = root / "review-only.jsonl"
        ref.write_text(json.dumps({**vars(question), "reference_answer": "仅供复核"}), encoding="utf-8")
        for profile in PROFILES:
            path = root / profile / "Q001/rag_result.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "status": "succeeded", "execution_mode": "evidence_only",
                "audit": {"embedding_profile": profile, "config": {}},
                "query": {"question_text": question.question, "request_label": "Q001"},
                "telemetry": {"provider_call_counts": {"generation": 0}},
                "retrieval_trace": {"windows": {"lexical": []}},
                "timing_seconds": {"rerank": 1.0},
                "evidence_packet": {"schema_version": "phase04-evidence-packet-0.1",
                                    "evidence": [{"evidence_id": "E01", "text": "原文", "members": []}]},
            }), encoding="utf-8")
        summary = export_review(root, [question], ref, "fixture", {})
        self.assertEqual(summary["identical_evidence_blocks"], 1)
        self.assertEqual(summary["human_review"], "pending")
        review = json.loads((root / "review/blinded-packets.jsonl").read_bytes())
        self.assertEqual(review["reference_answer"], "仅供复核")
        self.assertNotIn("side_a", review)
        decision = json.loads((root / "review/decisions-template.jsonl").read_bytes())
        self.assertEqual(decision["status"], "pending")
        self.assertIsNone(decision["verdict"])
        self.assertEqual(decision["run_identity"], "fixture")

    def test_tally_uses_the_blind_key_and_rejects_cross_run_decisions(self):
        root = Path("tmp") / f"paired-tally-{uuid4().hex}"
        review_root = root / "review"
        review_root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root)
        (root / "manifest.json").write_text(json.dumps({
            "status": "complete", "completed": dict.fromkeys(PROFILES, 70), "run_identity": "fixture",
        }), encoding="utf-8")
        ids = [f"Q{i:03}" for i in range(1, 71)]
        write_jsonl(review_root / "blinded-packets.jsonl", [
            {"question_id": qid, "packet_a": [{"evidence_id": "E01"}], "packet_b": []} for qid in ids
        ])
        write_jsonl(review_root / "unblinding.jsonl", [
            {"question_id": qid, "side_a": PROFILES[1], "side_b": PROFILES[0]} for qid in ids
        ])
        decisions = [{"question_id": qid, "run_identity": "fixture", "status": "pending", "verdict": None} for qid in ids]
        decisions[0].update(status="reviewed", verdict="a_better", evidence_ids=["A:E01"],
                            side_a_support="sufficient", side_b_support="insufficient", rationale="A 有原文。")
        decision_path = review_root / "reviewed.jsonl"
        write_jsonl(decision_path, decisions)
        self.assertEqual(tally_review(root, decision_path)["verdict_counts"], {f"{PROFILES[1]}_better": 1, "pending": 69})
        decisions[0]["run_identity"] = "wrong-run"
        write_jsonl(decision_path, decisions)
        with self.assertRaisesRegex(ValueError, "different run"):
            tally_review(root, decision_path)

    def test_cli_uses_shared_default_and_evidence_only_without_generation_flag(self):
        state = SimpleNamespace(closed=False, close=Mock())
        owner = PreparedStateOwner(state)
        with patch.dict("os.environ", {}, clear=True), patch(
            "genshin_corpus.rag.__main__.prepare_production_state", return_value=owner,
        ) as prepare, patch(
            "genshin_corpus.rag.__main__.execute_query",
            return_value={"status": "succeeded", "audit": {"embedding_profile": PROFILES[0]}},
        ) as execute, redirect_stdout(StringIO()) as output:
            self.assertEqual(query_main(["问题"]), 0)
        self.assertEqual(prepare.call_args.args[0][-1], PROFILES[0])
        self.assertEqual(execute.call_args.args[1].execution_mode, "evidence_only")
        self.assertEqual(json.loads(output.getvalue())["embedding_profile"], PROFILES[0])
        state.close.assert_called_once()
