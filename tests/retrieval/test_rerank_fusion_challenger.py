from __future__ import annotations

import math
import inspect
import unittest

from genshin_corpus.retrieval.evidence_assembly import EvidenceAssemblyConfig
from genshin_corpus.retrieval.rerank_fusion_challenger import (
    FusionConfig,
    FIXED_FUSION_CONFIG,
    Q049_RESCUE,
    RerankFusionError,
    compute_run_identity,
    fuse_rankings,
)


def _fixture_rows(pool_depth: int = 500, *, special: dict[str, tuple[int, int]] | None = None):
    special = special or {}
    ids = [f"u{i:03d}" for i in range(1, pool_depth + 1)]
    for unit_id, (hybrid_rank, _) in special.items():
        if unit_id in ids:
            current = ids.index(unit_id)
            ids[current], ids[hybrid_rank - 1] = ids[hybrid_rank - 1], ids[current]
        else:
            ids[hybrid_rank - 1] = unit_id
    hybrid_rank_by_id = {unit_id: index for index, unit_id in enumerate(ids, 1)}
    hybrid = [
        {"unit_id": unit_id, "rank": rank, "retrieval": {"fusion": {"method": "rrf", "config_identity": "rrf60"}}}
        for rank, unit_id in enumerate(ids, 1)
    ]
    # Keep the fixture adversarial enough to exercise rescue behavior: absent
    # specials, rerank order is not correlated with Hybrid order.
    rerank_order = list(reversed(ids))
    for unit_id, (_, rerank_rank) in sorted(special.items(), key=lambda item: item[1][1], reverse=True):
        rerank_order.remove(unit_id)
        rerank_order.insert(rerank_rank - 1, unit_id)
    reranked = [
        {
            "unit_id": unit_id,
            "rank": rank,
            "rerank_rank": rank,
            "original_hybrid_rank": hybrid_rank_by_id[unit_id],
            "rerank_score": 1.0 / rank,
            "retrieval": {"original_hybrid_rank": hybrid_rank_by_id[unit_id], "rerank_rank": rank, "rerank_score": 1.0 / rank},
        }
        for rank, unit_id in enumerate(rerank_order, 1)
    ]
    return hybrid, reranked


class RerankFusionChallengerTests(unittest.TestCase):
    @staticmethod
    def _identity_binding() -> dict[str, object]:
        return {
            "r1_manifest_sha256": "r1",
            "r1_run_identity": "r1-run",
            "c1_manifest_sha256": "c1",
            "c1_run_identity": "c1-run",
            "r7_manifest_sha256": "r7",
            "r7_run_identity": "r7-run",
            "ru_manifest_sha256": "ru",
            "comparison_artifacts": {"Q001": {"path": "q.json", "sha256": "comparison-a"}},
            "candidate_artifacts": {"Q001": {"path": "c.json", "sha256": "candidate-a"}},
            "historical_result_artifacts": {"Q001": {"path": "r.json", "sha256": "result-a"}},
            "r7_result_artifacts": {"Q005": {"path": "r7.json", "sha256": "gate-a"}},
        }

    def test_run_identity_is_deterministic_and_binds_each_effective_input(self) -> None:
        binding = self._identity_binding()
        implementation = {"path": "challenger.py", "sha256": "implementation-a"}
        first = compute_run_identity(binding, implementation=implementation)
        self.assertEqual(first, compute_run_identity(binding, implementation=implementation))
        for section, key in (
            ("comparison_artifacts", "Q001"),
            ("candidate_artifacts", "Q001"),
            ("historical_result_artifacts", "Q001"),
            ("r7_result_artifacts", "Q005"),
        ):
            changed = self._identity_binding()
            changed[section][key]["sha256"] = "changed"
            self.assertNotEqual(first, compute_run_identity(changed, implementation=implementation), section)
        self.assertNotEqual(first, compute_run_identity(binding, implementation={"path": "challenger.py", "sha256": "implementation-b"}))

    def test_exact_weighted_reciprocal_rank_calculation(self) -> None:
        config = FusionConfig(pool_depth=2, output_depth=2)
        hybrid = [{"unit_id": "a", "rank": 1}, {"unit_id": "b", "rank": 2}]
        reranked = [
            {"unit_id": "a", "rank": 2, "rerank_rank": 2, "original_hybrid_rank": 1, "rerank_score": 0.2},
            {"unit_id": "b", "rank": 1, "rerank_rank": 1, "original_hybrid_rank": 2, "rerank_score": 0.9},
        ]
        result = fuse_rankings(hybrid, reranked, config=config)
        self.assertEqual(result[0]["unit_id"], "b")
        self.assertAlmostEqual(result[0]["fusion_score"], 0.35 / 62 + 0.65 / 61)
        self.assertAlmostEqual(result[1]["fusion_score"], 0.35 / 61 + 0.65 / 62)

    def test_invalid_missing_duplicate_unknown_and_nonfinite_bindings_fail_closed(self) -> None:
        hybrid, reranked = _fixture_rows(3)
        with self.assertRaises(RerankFusionError):
            fuse_rankings(hybrid, reranked[:-1], config=FusionConfig(pool_depth=3, output_depth=2))
        duplicate = list(reranked)
        duplicate[1] = dict(duplicate[0])
        with self.assertRaises(RerankFusionError):
            fuse_rankings(hybrid, duplicate, config=FusionConfig(pool_depth=3, output_depth=2))
        unknown = list(reranked)
        unknown[0] = dict(unknown[0], unit_id="unknown")
        with self.assertRaises(RerankFusionError):
            fuse_rankings(hybrid, unknown, config=FusionConfig(pool_depth=3, output_depth=2))
        nonfinite = list(reranked)
        nonfinite[0] = dict(nonfinite[0], rerank_score=math.nan)
        with self.assertRaises(RerankFusionError):
            fuse_rankings(hybrid, nonfinite, config=FusionConfig(pool_depth=3, output_depth=2))

    def test_deterministic_tie_break_and_repeated_replay(self) -> None:
        config = FusionConfig(hybrid_weight=0.5, rerank_weight=0.5, pool_depth=3, output_depth=3)
        hybrid = [{"unit_id": "z", "rank": 1}, {"unit_id": "a", "rank": 2}, {"unit_id": "b", "rank": 3}]
        reranked = [
            {"unit_id": "z", "rank": 1, "rerank_rank": 1, "original_hybrid_rank": 1, "rerank_score": 0.1},
            {"unit_id": "a", "rank": 2, "rerank_rank": 2, "original_hybrid_rank": 2, "rerank_score": 0.1},
            {"unit_id": "b", "rank": 3, "rerank_rank": 3, "original_hybrid_rank": 3, "rerank_score": 0.1},
        ]
        first = fuse_rankings(hybrid, reranked, config=config)
        second = fuse_rankings(hybrid, reranked, config=config)
        self.assertEqual(first, second)
        self.assertEqual([row["unit_id"] for row in first], ["z", "a", "b"])

    def test_fusion_uses_rank_not_absolute_reranker_score(self) -> None:
        hybrid, reranked = _fixture_rows(3)
        altered = [dict(row, rerank_score=10_000.0 - row["rerank_rank"] * 7.0) for row in reranked]
        config = FusionConfig(pool_depth=3, output_depth=3)
        first = fuse_rankings(hybrid, reranked, config=config)
        second = fuse_rankings(hybrid, altered, config=config)
        self.assertEqual(
            [(row["unit_id"], row["rank"], row["fusion_score"]) for row in first],
            [(row["unit_id"], row["rank"], row["fusion_score"]) for row in second],
        )

    def test_original_and_rerank_provenance_are_preserved(self) -> None:
        hybrid, reranked = _fixture_rows(2)
        result = fuse_rankings(hybrid, reranked, config=FusionConfig(pool_depth=2, output_depth=2))[0]
        self.assertEqual(result["retrieval"]["fusion"]["config_identity"], "rrf60")
        self.assertIn("original_hybrid_rank", result)
        self.assertIn("rerank_rank", result)
        self.assertIn("rerank_score", result)
        self.assertEqual(result["fusion_stage"]["config_identity"], FusionConfig(pool_depth=2, output_depth=2).identity)

    def test_fixed_output_depth_and_assembly_budget_contract(self) -> None:
        hybrid, reranked = _fixture_rows()
        result = fuse_rankings(hybrid, reranked)
        self.assertEqual(len(result), 20)
        self.assertEqual(FIXED_FUSION_CONFIG.output_depth, 20)
        assembly = EvidenceAssemblyConfig().to_dict()
        self.assertEqual(assembly["total_context_chars"], 12000)
        self.assertEqual(assembly["per_block_chars"], 3000)

    def test_q049_deep_rescue_is_retained(self) -> None:
        hybrid, reranked = _fixture_rows(500, special={Q049_RESCUE: (388, 1)})
        fused = fuse_rankings(hybrid, reranked)
        rescue = next(row for row in fused if row["unit_id"] == Q049_RESCUE)
        self.assertLessEqual(rescue["rank"], 20)
        self.assertEqual(rescue["original_hybrid_rank"], 388)
        self.assertEqual(rescue["rerank_rank"], 1)

    def test_q068_strong_original_anchor_beats_competitor(self) -> None:
        hybrid, reranked = _fixture_rows(500, special={"anchor": (2, 14), "competitor": (366, 3)})
        fused = fuse_rankings(hybrid, reranked)
        ranks = {row["unit_id"]: row["rank"] for row in fused}
        self.assertIn("anchor", ranks)
        self.assertIn("competitor", ranks)
        self.assertLess(ranks["anchor"], ranks["competitor"])

    def test_q056_decisive_evidence_non_loss_fixture(self) -> None:
        hybrid, reranked = _fixture_rows(500, special={"decisive": (4, 4), "competing": (51, 1)})
        fused = fuse_rankings(hybrid, reranked)
        decisive = next(row for row in fused if row["unit_id"] == "decisive")
        self.assertLessEqual(decisive["rank"], 20)

    def test_provider_free_runner_has_zero_call_accounting(self) -> None:
        from genshin_corpus.retrieval import rerank_fusion_challenger as challenger

        self.assertFalse(hasattr(challenger, "DashScopeQwenRerankTransport"))
        source = inspect.getsource(challenger)
        self.assertNotIn("DashScopeQwenRerankTransport", source)
        self.assertNotIn("requests.", source)


if __name__ == "__main__":
    unittest.main()
