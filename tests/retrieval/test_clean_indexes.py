"""Original/prepared search parity on small checked-in-source derivatives."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from genshin_corpus.evidence_io import read
from genshin_corpus.indexing import ChunkInputSource, prepare_inputs
from genshin_corpus.indexing import benchmark_dense, benchmark_lexical
from genshin_corpus.retrieval.clean_based.indexes import DenseSession, LexicalSession
from tests.indexing.test_benchmark import FakeEncoder
from tests.indexing.test_indexing import build_source


class SessionParityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "input/manifest.json"
        prepare_inputs(ChunkInputSource(build_source(self.root, long_story=True)), self.input.parent)
        self.config = read(Path(__file__).parents[2] / "config/index-benchmark-v1.json")
        self.config.update(minimum_free_gib=0, minimum_available_ram_gib=0, minimum_running_ram_gib=0,
                           shard_rows=3, padded_token_budget=100000, max_batch=2)

    def test_dense_one_scan_two_routes_matches_original_and_detects_change(self):
        encoder = FakeEncoder()
        root = self.root / "dense"
        benchmark_dense.preflight(encoder, self.input, root, self.config)
        benchmark_dense.build(encoder, self.input, root, self.config)
        session = DenseSession(root / "manifest.json")
        query = encoder.encode(["旅行者提问"], role="query")[0]
        result = session.search(query, space_id=encoder.space_id, top_k=10)
        session.verify_reference(query, 10, result)
        with self.assertRaises(ValueError):
            session.search(query, space_id="other", top_k=10)
        path = next(root.glob("shards/*/vectors.npy"))
        with path.open("ab") as handle:
            handle.write(b"changed")
        with self.assertRaises(ValueError):
            session.search(query, space_id=encoder.space_id, top_k=10)

    @unittest.skipUnless(importlib.util.find_spec("whoosh"), "Whoosh3 runtime required")
    def test_lexical_canonical_manifest_order_and_no_index_writes(self):
        root = self.root / "lexical"
        benchmark_lexical.build(self.input, root, self.config)
        from genshin_corpus.evidence_io import inventory
        before = inventory(root)
        session = LexicalSession(root / "manifest.json", self.root / "query-cache")
        try:
            result = session.search("旅行者", top_k=5)
            session.verify_reference("旅行者", 5, result)
            self.assertEqual(session.search("！！！", top_k=5), {"micro": [], "macro": []})
        finally:
            session.close()
        self.assertEqual(inventory(root), before)


if __name__ == "__main__":
    unittest.main()
