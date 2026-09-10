from __future__ import annotations

import json
import inspect
from hashlib import sha256
import unittest

from genshin_corpus.retrieval import bge_qwen_dense_comparison as comparison
from genshin_corpus.retrieval.candidate_retrieval import hybrid_candidates


class BgeQwenDenseComparisonTests(unittest.TestCase):
    lexical_identity = "lexical"
    dense_identity = "dense"

    def _arm(self, mode: str, identity: str) -> list[dict]:
        return [
            {
                "unit_id": f"u{rank:02d}",
                "rank": rank,
                "retrieval": {"mode": mode, "arm_build_identity": identity, "score": 1.0 / rank},
            }
            for rank in range(1, 21)
        ]

    def test_frozen_window_validation_requires_exact_rrf_reconstruction(self) -> None:
        lexical = self._arm("lexical", self.lexical_identity)
        dense = list(reversed(self._arm("dense", self.dense_identity)))
        for rank, row in enumerate(dense, 1):
            row["rank"] = rank
        hybrid = hybrid_candidates(
            lexical,
            dense,
            lexical_build_identity=self.lexical_identity,
            dense_build_identity=self.dense_identity,
            top_k=20,
            rrf_k=60,
        )
        self.assertEqual(
            comparison._validate_arm_rows(lexical, question_id="Q001", mode="lexical", expected_build_identity=self.lexical_identity),
            lexical,
        )
        self.assertEqual(
            comparison._validate_hybrid_rows(
                hybrid,
                question_id="Q001",
                lexical_identity=self.lexical_identity,
                dense_identity=self.dense_identity,
            ),
            hybrid,
        )
        self.assertEqual(
            hybrid_candidates(
                lexical,
                dense,
                lexical_build_identity=self.lexical_identity,
                dense_build_identity=self.dense_identity,
                top_k=20,
                rrf_k=60,
            ),
            hybrid,
        )

    def test_rank_delta_records_membership_and_rank_changes_without_scoring(self) -> None:
        control = self._arm("dense", self.dense_identity)
        challenger = self._arm("dense", self.dense_identity)[1:] + [{
            "unit_id": "u21", "rank": 20,
            "retrieval": {"mode": "dense", "arm_build_identity": self.dense_identity, "score": 0.0},
        }]
        for rank, row in enumerate(challenger, 1):
            row["rank"] = rank
        delta = comparison._rank_delta(control, challenger)
        self.assertEqual(delta["control_only"], ["u01"])
        self.assertEqual(delta["challenger_only"], ["u21"])
        self.assertEqual(delta["shared_rank_changes"][0], {"unit_id": "u02", "control_rank": 2, "challenger_rank": 1})

    def test_blinded_review_omits_model_labels_and_unblinding_is_separate(self) -> None:
        projection = {"schema_version": "fixture", "instruction": "i", "evidence": [], "citation_policy": {}, "evidence_packet_schema_version": "p"}
        rows = [{
            "question_id": "Q001",
            "bge": {"packet_summary": {"generation_visible_projection": projection}},
            "qwen_packet": {"packet_summary": {"generation_visible_projection": {**projection, "instruction": "j"}}},
        }]
        review, unblinding = comparison._blinded_records(rows, "a" * 64, {"Q001": "original question"})
        review_row = json.loads(review)
        unblinding_row = json.loads(unblinding)
        self.assertEqual(review_row["question"], "original question")
        self.assertNotIn("bge", review.decode("utf-8").lower())
        self.assertNotIn("qwen", review.decode("utf-8").lower())
        self.assertEqual({unblinding_row["side_a"], unblinding_row["side_b"]}, {"bge", "qwen"})

    def _accepted_qwen_corpus_manifest(self) -> dict:
        return {
            "arm_build_identity": comparison.ACCEPTED_QWEN_CORPUS_ARM_BUILD_IDENTITY,
            "row_count": 535802,
            "embedding_dimension": 2048,
            "retrieval_unit_build_identity": comparison.frozen_comparison.ACCEPTED_RU_BUILD_IDENTITY,
            "artifacts": {
                "vectors": {"sha256": comparison.ACCEPTED_QWEN_CORPUS_VECTORS_SHA256},
                "rows": {"sha256": comparison.ACCEPTED_QWEN_CORPUS_ROWS_SHA256, "row_count": 535802},
            },
        }

    def test_accepted_qwen_corpus_bindings_pass(self) -> None:
        comparison._validate_accepted_qwen_corpus_binding(self._accepted_qwen_corpus_manifest())

    def test_wrong_qwen_corpus_arm_identity_fails_closed(self) -> None:
        manifest = self._accepted_qwen_corpus_manifest()
        manifest["arm_build_identity"] = "unaccepted-arm"
        with self.assertRaisesRegex(comparison.BgeQwenDenseComparisonError, "accepted arm/vector/rows/RU"):
            comparison._validate_accepted_qwen_corpus_binding(manifest)

    def test_wrong_qwen_corpus_rows_identity_fails_closed(self) -> None:
        manifest = self._accepted_qwen_corpus_manifest()
        manifest["artifacts"]["rows"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(comparison.BgeQwenDenseComparisonError, "accepted arm/vector/rows/RU"):
            comparison._validate_accepted_qwen_corpus_binding(manifest)

    def test_runner_does_not_introduce_bge_encoding_bm25_provider_or_generation_call_path(self) -> None:
        source = inspect.getsource(comparison)
        for prohibited in ("encode_dense_query(", "lexical_candidates(", "DashScopeQwenEmbeddingTransport", "GenerationProvider"):
            self.assertNotIn(prohibited, source)

    def test_streaming_sha256_matches_standard_digest(self) -> None:
        body = (b"qwen-corpus-binding" * 70000) + b"final"

        class Handle:
            def __init__(self) -> None:
                self.offset = 0
                self.read_sizes: list[int] = []

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, size: int) -> bytes:
                self.read_sizes.append(size)
                chunk = body[self.offset:self.offset + size]
                self.offset += len(chunk)
                return chunk

        class InMemoryPath:
            def __init__(self) -> None:
                self.handle = Handle()

            def open(self, mode: str):
                self.mode = mode
                return self.handle

        path = InMemoryPath()
        self.assertEqual(comparison._sha256_file(path), sha256(body).hexdigest())
        self.assertEqual(path.mode, "rb")
        self.assertEqual(path.handle.read_sizes[0], 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
