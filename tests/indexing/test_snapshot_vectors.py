from hashlib import sha256
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from genshin_corpus.evidence_io import sha, write
from genshin_corpus.indexing import snapshot_vectors
from genshin_corpus.indexing.snapshot_compare import compare_vectors


@unittest.skipUnless(find_spec("numpy") and find_spec("scipy"), "Requires the numerical index runtime")
class VersionedVectorTests(unittest.TestCase):
    def test_reuse_add_retire_and_resume_without_encoding_committed_blocks_again(self):
        import numpy as np

        class Source:
            def __init__(self, directory, values):
                self.path = directory / "manifest.json"
                self.manifest = {"input_bundle_id": directory.name,
                                 "counts": {"documents": len(values), "micro": len(values), "macro": 0}}
                self.rows, self.documents = [], []
                for number, text in enumerate(values):
                    row = {"input_row": number, "granularities": ["micro"], "source_item": {"content_id": str(number)},
                           "dense_text_sha256": sha256(text.encode()).hexdigest()}
                    self.rows.append(row)
                    self.documents.append({"row": row, "projected": {"dense_text": text}})
                write(self.path, self.manifest)

            def iter_documents(self, sources):
                yield from (doc for doc in self.documents if doc["row"]["source_item"]["content_id"] in sources)

        class Encoder:
            contract = {"dense": {"backbone": {"dimensions": 4}}, "sparse": {"vocabulary_size": 4}}

            def __init__(self, fail_at=None):
                self.calls, self.fail_at = [], fail_at

            def encode(self, texts, channels):
                self.calls.extend(texts)
                if self.fail_at in texts:
                    raise RuntimeError("injected interruption")
                ids = [ord(text) - ord("a") for text in texts]
                options = {"dense": np.eye(4, dtype=np.float32)[ids], "sparse": [{i: 1.0} for i in ids]}
                return {channel: options[channel] for channel in channels}

        with TemporaryDirectory() as workspace, patch.object(snapshot_vectors, "BLOCK_ROWS", 1):
            root = Path(workspace).resolve()
            old = Source(root / "old-input", ["a", "b", "a"])
            current = Source(root / "input", ["a", "c", "d", "a"])
            build = snapshot_vectors.build_vectors
            build(old, root / "old", Encoder())
            sealed = root / "old/manifest.json"
            previous = (sealed, old)
            previous_digest = sha(sealed)
            with self.assertRaisesRegex(RuntimeError, "injected interruption"):
                build(current, root / "incremental", Encoder("d"), previous=previous)
            self.assertFalse((root / "incremental/manifest.json").exists())
            encoder = Encoder()
            result = build(current, root / "incremental", encoder, previous=previous)
            self.assertEqual(encoder.calls, ["d"])
            self.assertEqual(result["reused_keys"], {"dense": 1, "sparse": 1})
            self.assertEqual(result["encoded_keys"], {"dense": 2, "sparse": 2})
            self.assertEqual(sha(sealed), previous_digest)
            build(current, root / "full", Encoder())
            incremental = snapshot_vectors.VectorIndex(root / "incremental/manifest.json", current)
            whole = snapshot_vectors.VectorIndex(root / "full/manifest.json", current)
            self.assertEqual(compare_vectors(incremental, whole), {"dense": 3, "sparse": 3})
            self.assertEqual(incremental.search("sparse", {1: 1.0}, 10)["micro"], [])
            self.assertEqual(incremental.search("sparse", {0: 1.0}, 10)["micro"],
                             [{"input_row": 0, "score": 1.0}, {"input_row": 3, "score": 1.0}])


if __name__ == "__main__":
    unittest.main()
