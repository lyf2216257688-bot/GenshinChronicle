import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from genshin_corpus.chunking.snapshot_ranges import NAMES, read_range, register_flat
from genshin_corpus.evidence_io import sha, write


class SnapshotRangeTests(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.source_rows = [{"content_id": str(i), "observation_id": str(i) * 64,
                             "record_sha256": str(i) * 64} for i in (1, 2)]
        self.clean = self.root / "clean.json"
        write(self.clean, {"schema_version": "gc-clean-1", "status": "complete", "records": self.source_rows})
        self.manifest = self.root / "chunk.json"

    def exports(self, chunk_order):
        for name in NAMES:
            path = self.root / name
            values = chunk_order if name == "chunks-balanced.jsonl" else []
            path.write_bytes(b"".join(json.dumps({"content_id": str(i), "observation_id": str(i) * 64}).encode() + b"\n"
                                      for i in values))
        write(self.manifest, {"schema_version": "gc-chunking-pilot-2", "status": "complete", "clean_manifest": str(self.clean),
                              "clean_manifest_sha256": sha(self.clean), "selection": {"content_ids": ["1", "2"],
                              "profiles": [{"name": "balanced"}], "bge_document_token_cap": 2048},
                              "code_hashes": {}, "tokenizers": [], "tokenizers_version": "fixture",
                              "sources": [{**row, "before_sha256": row["record_sha256"], "after_sha256": row["record_sha256"]}
                                          for row in self.source_rows],
                              "artifacts": {name: sha(self.root / name) for name in NAMES}})

    def test_valid_ranges_include_zero_candidate_sources_and_detect_tampering(self):
        self.exports([1, 1])
        index = register_flat(self.manifest)
        self.assertEqual(len(read_range(index, "1", "chunks-balanced.jsonl")), 2)
        self.assertEqual(read_range(index, "2", "chunks-balanced.jsonl"), [])
        (self.root / "chunks-balanced.jsonl").write_bytes(b"not a chunk")
        with self.assertRaisesRegex(ValueError, "range hash"):
            read_range(index, "1", "chunks-balanced.jsonl")

    def test_rejects_noncontiguous_ranges(self):
        self.exports([1, 2, 1])
        with self.assertRaisesRegex(ValueError, "noncontiguous"):
            register_flat(self.manifest)


if __name__ == "__main__":
    unittest.main()
