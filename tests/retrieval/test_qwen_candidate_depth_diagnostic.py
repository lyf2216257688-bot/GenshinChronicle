from __future__ import annotations

import unittest
from unittest.mock import patch

from genshin_corpus.retrieval import qwen_candidate_depth_diagnostic as diagnostic


class QwenCandidateDepthDiagnosticTests(unittest.TestCase):
    def _windows(self, *, carrier_in_hybrid: bool) -> dict[str, list[dict[str, object]]]:
        unit_id = "carrier-1"
        return {
            "lexical": [{"unit_id": unit_id, "rank": 1, "retrieval": {"mode": "lexical"}}],
            "dense": [{"unit_id": unit_id, "rank": 2, "retrieval": {"mode": "dense"}}],
            "hybrid": ([{"unit_id": unit_id, "rank": 1, "retrieval": {"mode": "hybrid"}}] if carrier_in_hybrid else []),
        }

    def _result(self, depth: int, windows: dict[str, list[dict[str, object]]], state: dict[str, dict[str, int | None]], top20: dict[str, list[str]]) -> dict[str, object]:
        visible = ["carrier-1"] if depth == 20 or depth == 50 else []
        direct = ["carrier-1"] if visible else []
        outcomes = diagnostic._carrier_outcomes(
            [{"unit_id": "carrier-1"}], windows, visible, direct, state, depth
        )
        if depth == 20:
            top20["Q005"] = visible
        return {
            "question_id": "Q005",
            "depth": depth,
            "candidate_windows": windows,
            "packet_summary": {"visible_unit_ids": visible, "direct_root_ids": direct},
            "carrier_outcomes": outcomes,
            "complete_carrier_set_hybrid_exposed": bool(outcomes[0]["hybrid_rank"] is not None),
            "complete_carrier_set_packet_visible": bool(outcomes[0]["packet_visible"]),
        }

    def test_resume_after_completed_top20_restores_top20_and_first_exposure(self) -> None:
        source_state: dict[str, dict[str, int | None]] = {}
        source_top20: dict[str, list[str]] = {}
        result = self._result(20, self._windows(carrier_in_hybrid=True), source_state, source_top20)
        restored_state: dict[str, dict[str, int | None]] = {}
        restored_top20: dict[str, list[str]] = {}
        diagnostic._restore_completed_state(result, [{"unit_id": "carrier-1"}], restored_state, restored_top20)
        self.assertEqual(restored_top20, {"Q005": ["carrier-1"]})
        self.assertEqual(restored_state["carrier-1"], {"lexical": 20, "dense": 20, "hybrid": 20, "packet": 20})

    def test_resume_after_later_completed_depth_preserves_prior_exposure_state(self) -> None:
        source_state: dict[str, dict[str, int | None]] = {}
        source_top20: dict[str, list[str]] = {}
        first = self._result(20, self._windows(carrier_in_hybrid=False), source_state, source_top20)
        second = self._result(50, self._windows(carrier_in_hybrid=True), source_state, source_top20)
        restored_state: dict[str, dict[str, int | None]] = {}
        restored_top20: dict[str, list[str]] = {}
        diagnostic._restore_completed_state(first, [{"unit_id": "carrier-1"}], restored_state, restored_top20)
        diagnostic._restore_completed_state(second, [{"unit_id": "carrier-1"}], restored_state, restored_top20)
        self.assertEqual(restored_top20, {"Q005": ["carrier-1"]})
        self.assertEqual(restored_state["carrier-1"], {"lexical": 20, "dense": 20, "hybrid": 50, "packet": 20})

    def test_baseline_allows_only_explicit_diagnostic_owned_source_paths(self) -> None:
        owned = sorted(diagnostic.DIAGNOSTIC_OWNED_PATHS)

        def git_output(*args: str) -> str:
            if args[:2] == ("branch", "--show-current"):
                return "main"
            if args[:2] == ("rev-parse", "HEAD"):
                return "head"
            if args[:2] == ("diff", "--name-only"):
                return "docs/current-phase.md\n" + "\n".join(owned)
            if args[:2] == ("status", "--porcelain=v1"):
                return "?? " + owned[0] + "\n?? " + owned[1]
            raise AssertionError(args)

        with patch.object(diagnostic, "_git_output", side_effect=git_output), patch.object(
            diagnostic, "_diagnostic_owned_file_descriptors", return_value=[{"path": path, "sha256": "x"} for path in owned]
        ):
            state = diagnostic._baseline_state()
        self.assertEqual(state["source_or_test_delta_from_accepted"], [])
        self.assertEqual(state["worktree_source_or_test_delta"], [])
        self.assertEqual([row["path"] for row in state["diagnostic_owned_files"]], owned)

    def test_baseline_rejects_unrelated_retrieval_source_drift(self) -> None:
        def git_output(*args: str) -> str:
            if args[:2] == ("branch", "--show-current"):
                return "main"
            if args[:2] == ("rev-parse", "HEAD"):
                return "head"
            if args[:2] == ("diff", "--name-only"):
                return ""
            if args[:2] == ("status", "--porcelain=v1"):
                return " M src/genshin_corpus/retrieval/candidate_retrieval.py"
            raise AssertionError(args)

        with patch.object(diagnostic, "_git_output", side_effect=git_output):
            with self.assertRaises(diagnostic.CandidateDepthDiagnosticError):
                diagnostic._baseline_state()


if __name__ == "__main__":
    unittest.main()
