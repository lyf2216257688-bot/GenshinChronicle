"""Regression coverage for the byte-only historical Clean transition."""

import hashlib
from importlib.util import find_spec
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from genshin_corpus.chunking import snapshot_build as chunks
from genshin_corpus.clean import snapshot_build as clean
from genshin_corpus.clean.pipeline import _bytes, build_clean_run
from genshin_corpus.evidence_io import read, sha, write
from genshin_corpus.indexing import snapshot_lexical as lexical, snapshot_vectors as vectors
from genshin_corpus.indexing.snapshot_compare import compare_lexical, compare_vectors
from genshin_corpus.indexing.snapshot_inputs import SnapshotInput, build_inputs
from genshin_corpus.retrieval.clean_based.snapshot import open_release, package, pin
from scripts.retrieval import snapshot_recovery as recovery
from tests.clean import test_snapshot_diff as fixtures


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="r-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        factory = fixtures.SnapshotDiffTests()
        factory.root = self.root
        self.old = factory.collect("old", {10: [1]}, dialogue=True)
        self.current = factory.collect("current", {10: [1, 2]}, dialogue=True)
        output = self.root / "baseline"
        build_clean_run(raw_run_root=self.old, output_root=output, clean_run_id="old", workers=1)
        self.previous = output / "mihoyo_obc/zh-cn/old/metadata/manifest.json"
        manifest = read(self.previous)
        entry = manifest["records"][0]
        self.original = self.previous.parent.parent / entry["record_path"]
        record = read(self.original)
        diagnostics = record["diagnostics"]
        slots = [i for i, d in enumerate(diagnostics) if d["code"] == "DIALOGUE_ORPHAN_NODE"]
        self.assertEqual(len(slots), 2)
        a, b = slots
        diagnostics[a], diagnostics[b] = diagnostics[b], diagnostics[a]
        self.original.write_bytes(_bytes(record, pretty=False))
        entry["record_sha256"] = sha(self.original)
        self.previous.write_text(json.dumps(manifest), encoding="utf-8")

    def test_new_incremental_replays_legacy_order_without_rewriting_the_original(self):
        before = sha(self.original)
        inc = self.root / "fresh-inc"
        full = self.root / "fresh-full"
        a = clean.build_snapshot(self.current, self.old, self.previous, inc, mode="incremental")
        b = clean.build_snapshot(self.current, self.old, self.previous, full, mode="full")
        self.assertEqual(a["logical_id"], b["logical_id"])
        self.assertEqual(sha(self.original), before)
        self.assertEqual(a["counts"], {"built": 2})
        self.assertEqual(a["records"][0]["effective_observation"]["run_id"], "old")
        self.assertEqual(read(inc / "plan.json")["serialization_replays"]["1"]["record"]["sha256"], before)
        self.assertEqual(clean.validate_snapshot(inc / "manifest.json"), a)
        record = read(self.original)
        with self.assertRaisesRegex(ValueError, "Unsupported historical Clean serialization"):
            clean.serialization_target(_bytes(record, pretty=True), record)

    def test_changed_historical_evidence_blocks_replay(self):
        record = read(self.original)
        record["document"]["title"]["text"] = "foreign text"
        self.original.write_bytes(_bytes(record, pretty=False))
        manifest = read(self.previous)
        manifest["records"][0]["record_sha256"] = sha(self.original)
        self.previous.write_text(json.dumps(manifest), encoding="utf-8")
        output = self.root / "rejected"
        with self.assertRaisesRegex(ValueError, "Historical Clean replay differs"):
            clean.build_snapshot(self.current, self.old, self.previous, output, mode="incremental")
        self.assertFalse((output / "manifest.json").exists())

    @unittest.skipUnless(all(find_spec(name) for name in ("tokenizers", "numpy", "scipy", "whoosh", "jieba")),
                         "Requires the local tokenizers, numerical and lexical test runtimes")
    def test_independent_recovery_resumes_without_encoders_or_bucket_writes(self):
        import numpy as np
        from tokenizers import Tokenizer
        from tokenizers.models import WordLevel
        from tokenizers.pre_tokenizers import WhitespaceSplit

        class LocalEncoder:
            contract = {"dense": {"backbone": {"dimensions": 2}}, "sparse": {"vocabulary_size": 2}}

            def encode(self, texts, channels):
                values = {"dense": np.tile(np.asarray([[1, 0]], dtype=np.float32), (len(texts), 1)),
                          "sparse": [{0: 1.0} for _ in texts]}
                return {channel: values[channel] for channel in channels}

        binary = self.root / "tokenizer.json"
        tokenizer = Tokenizer(WordLevel(vocab={"[UNK]": 0}, unk_token="[UNK]"))
        tokenizer.pre_tokenizer = WhitespaceSplit()
        tokenizer.save(str(binary))
        tokens = (binary, binary)
        selection = self.root / "chunk.json"
        config_repo = Path(__file__).resolve().parents[2]
        write(selection, read(config_repo / "config/chunking-full-20261107-balanced.json"))
        effective, segment = lexical.lexical_runtime(config_repo, self.root / "scratch")
        origin = {"root": str(self.root / "o"), "arms": {}}
        old_rules = clean.legacy_rules(clean._rules())
        previous_sha = sha(self.previous)
        for arm in recovery.ARMS:
            old = Path(origin["root"]) / arm
            with patch.object(clean, "_rules", return_value=old_rules), patch.object(
                clean, "serialization_target", side_effect=lambda body, _: hashlib.sha256(body).hexdigest()
            ):
                clean.build_snapshot(self.current, self.old, self.previous, old / "clean", mode=arm)
                chunks.build_snapshot(old / "clean/manifest.json", selection, tokens, old / "chunk", mode=arm)
            build_inputs(old / "chunk/manifest.json", old / "inputs")
            source = SnapshotInput(old / "inputs/manifest.json")
            vectors.build_vectors(source, old / "vectors", LocalEncoder())
            lexical.build_lexical(source, old / "bm25f", effective, segment)
            origins = {stage: pin(old / stage / "manifest.json") for stage in ("clean", "chunk", "inputs", "vectors", "bm25f")}
            package(source.path, old / "vectors/manifest.json", old / "bm25f/manifest.json", old / "release.json")
            origins["release"] = pin(old / "release.json")
            origin["arms"][arm] = origins

        with self.assertRaisesRegex(ValueError, "Independent Clean identities differ"):
            recovery.clean_parity(*(arm["clean"]["path"] for arm in origin["arms"].values()),
                                  self.root / "old-clean-parity.json")
        with self.assertRaisesRegex(ValueError, "Independent Clean identities differ"):
            recovery.upstream_parity(*(arm["inputs"]["path"] for arm in origin["arms"].values()),
                                     self.root / "old-parity.json")
        repaired = self.root / "n"
        releases, inputs = [], []
        first = origin["arms"]["incremental"]
        with patch.object(recovery, "recover_chunk", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                recovery.recover_upstream(first, selection, tokens, repaired / "incremental")
        completed = repaired / "incremental/clean/manifest.json"
        completed_sha = sha(completed)
        for arm in recovery.ARMS:
            new = repaired / arm
            recovery.recover_upstream(origin["arms"][arm], selection, tokens, new)
            clean.validate_snapshot(new / "clean/manifest.json")
            chunks.validate_snapshot_manifest(new / "chunk/manifest.json")
            inputs.append(new / "inputs/manifest.json")
        self.assertEqual(sha(completed), completed_sha)
        self.assertEqual(set(read(completed.with_name("normalization.json"))["changes"]), {"1"})
        recovery.upstream_parity(*inputs, self.root / "new-parity.json")
        with patch.object(vectors, "build_vectors", side_effect=AssertionError("encoding invoked")), patch.object(
            lexical, "build_lexical", side_effect=AssertionError("indexing invoked")
        ):
            for arm, source in zip(recovery.ARMS, inputs, strict=True):
                output = repaired / arm / "indexes"
                recovery.recover_indexes(origin["arms"][arm], Path(origin["root"]) / arm, source, output)
                releases.append(output / "release.json")
            with self.assertRaisesRegex(ValueError, "outside its original arm"):
                recovery.recover_indexes(first, Path(origin["root"]) / "full", inputs[0], self.root / "wrong")
        loaded = [open_release(path) for path in releases]
        self.assertTrue(all(item[2]["vectors"]["encoded_keys"] == {"dense": 0, "sparse": 0}
                            and item[2]["bm25f"]["built_buckets"] == 0 for item in loaded))
        vi = [vectors.VectorIndex(item[0]["vectors"]["path"], item[1]) for item in loaded]
        self.assertGreater(compare_vectors(*vi)["dense"], 0)
        li = [lexical.LexicalIndex(item[0]["bm25f"]["path"], item[1], effective, segment) for item in loaded]
        try:
            self.assertGreater(compare_lexical(*li, ["同文"])["scores"], 0)
        finally:
            for index in li:
                index.close()
        recovery.retain_assets(origin, releases, self.root / "assets.json")
        proof = read(self.root / "assets.json")
        files = [set(proof["arms"][arm]["vector_files"]) for arm in recovery.ARMS]
        self.assertFalse(files[0] & files[1])
        self.assertEqual(sha(self.previous), previous_sha)
        for stages in origin["arms"].values():
            for stage in stages.values():
                self.assertEqual(sha(stage["path"]), stage["sha256"])
