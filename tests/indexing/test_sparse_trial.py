"""Mathematical Sparse checks on tiny weights, never the local Raw corpus."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from genshin_corpus.evidence_io import sha
from genshin_corpus.indexing.sparse_trial import SparseSession, build_trial, csr_weights


class SparseTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "sparse"
        self.rows = [{"input_row": i * 3, "chunk_id": str(i), "dense_text_sha256": str(i), "granularities": g}
                     for i, g in enumerate((["micro", "macro"], ["micro"], ["macro"], ["micro", "macro"]))]
        self.vectors = [{1: 2., 5: .5}, {1: 1.}, {5: 4.}, {}]
        self.contract = {"version": "fixture", "normalization": "none"}

    def build(self, **changes):
        return build_trial(self.rows, self.vectors, self.root, input_bundle_id="bundle", contract=self.contract,
                           vocabulary_size=8, shard_rows=2, **changes)

    def test_scores_known_dot_and_route_filter_before_topk(self):
        manifest = self.build()
        result = SparseSession(self.root).search({1: 3., 5: 2.}, space_id=manifest["space_id"], top_k=1)
        self.assertEqual(result["micro"][0]["chunk_id"], "0")
        self.assertAlmostEqual(result["micro"][0]["score"], 7.)
        self.assertEqual(result["macro"][0]["chunk_id"], "2")
        self.assertAlmostEqual(result["macro"][0]["score"], 8.)

    def test_empty_vectors_and_no_matches_do_not_fill_with_zero_scores(self):
        manifest = self.build()
        self.assertEqual(manifest["empty_rows"], 1)
        result = SparseSession(self.root).search({7: 1.}, space_id=manifest["space_id"])
        self.assertEqual(result, {"micro": [], "macro": []})

    def test_same_score_tie_follows_input_order(self):
        self.vectors = [{1: 1.}] * 4
        manifest = self.build()
        result = SparseSession(self.root).search({1: 1.}, space_id=manifest["space_id"])
        self.assertEqual([r["input_row"] for r in result["micro"]], [0, 3, 9])

    def test_resume_preserves_every_committed_byte(self):
        result = self.build(stop_after=1)
        self.assertEqual(result["status"], "interrupted_for_test")
        hashes = {p: sha(p) for p in self.root.rglob('*') if p.is_file()}
        self.build()
        self.assertTrue(all(sha(p) == checksum for p, checksum in hashes.items()))
        self.assertEqual(SparseSession(self.root).manifest["counts"], {"documents": 4, "micro": 3, "macro": 3})

    def test_changed_resume_vector_or_source_binding_is_rejected(self):
        self.build(stop_after=1)
        self.vectors[0][1] = 3.
        with self.assertRaisesRegex(ValueError, "binding"):
            self.build()

    def test_corruption_and_foreign_query_fail_closed(self):
        manifest = self.build()
        session = SparseSession(self.root)
        with self.assertRaisesRegex(ValueError, "Foreign"):
            session.search({1: 1.}, space_id="other")
        path = next(self.root.glob("shards/*/weights.npz"))
        with path.open("ab") as handle:
            handle.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "changed"):
            session.search({1: 1.}, space_id=manifest["space_id"])
        with self.assertRaises(ValueError):
            SparseSession(self.root)

    def test_trial_cap_and_duplicate_membership_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "1–128"):
            build_trial(self.rows * 33, self.vectors * 33, self.root, input_bundle_id="b",
                        contract={}, vocabulary_size=8)
        self.rows[0]["granularities"] = ["micro", "micro"]
        with self.assertRaises(ValueError):
            self.build()

    def test_invalid_token_weight_and_fp32_overflow_are_rejected(self):
        for vector in ({-1: 1.}, {8: 1.}, {1: -1.}, {1: float("nan")}, {1: 1e100}):
            with self.assertRaises(ValueError):
                csr_weights([vector], 8)


if __name__ == "__main__":
    unittest.main()
