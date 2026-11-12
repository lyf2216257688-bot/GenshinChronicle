"""Streaming/recovery fault tests with tiny weights; no local source/model required."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from genshin_corpus.evidence_io import digest, read, sha
from genshin_corpus.indexing.sparse_full import (
    VERSION, StreamingSparseSession, build_stream, encode_shard, validate_store)
from genshin_corpus.indexing.sparse_job import Tee
from genshin_corpus.indexing import sparse_job
from genshin_corpus.retrieval.local_qwen3_build import build_lock


class StreamingSparseTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "index"
        self.rows = [{"input_row": i, "chunk_id": str(i), "granularities": g, "dense_text_sha256": str(i)}
                     for i, g in enumerate((["micro", "macro"], ["micro"], ["macro"], ["micro", "macro"], ["micro"]))]
        self.vectors = [{1: 2., 5: .5}, {1: 1.}, {5: 4.}, {}, {1: 2., 5: .5}]
        contract = {"fixture": "none"}
        self.plan = {"version": VERSION, "scope": "full-selected", "input_bundle_id": "fixture",
                     "space_id": digest(contract), "configuration": contract, "vocabulary_size": 8,
                     "shard_rows": 2, "counts": {"documents": 5, "micro": 4, "macro": 3}}
        self.config = {"minimum_running_ram_gib": .000001, "minimum_available_ram_gib": .000001,
                       "minimum_free_gib": .000001, "max_batch": 2, "padded_token_budget": 32, "heartbeat_seconds": 15}
        self.encoded = []

    def source(self):
        return iter((r, str(r["input_row"])) for r in self.rows)

    def encode(self, texts, rows):
        self.encoded.extend(r["input_row"] for r in rows)
        return [self.vectors[r["input_row"]] for r in rows]

    def build(self, **kwargs):
        return build_stream(self.root, self.plan, self.source(), self.encode, config=self.config, **kwargs)

    def test_exact_dot_route_before_topk_and_no_resident_matrices(self):
        self.build()
        session = StreamingSparseSession(self.root)
        self.assertFalse(hasattr(session, "matrices"))
        result = session.search({1: 3., 5: 2.}, space_id=self.plan["space_id"], top_k=1)
        self.assertEqual(result["micro"][0]["input_row"], 0)
        self.assertEqual(result["macro"][0]["input_row"], 2)
        self.assertEqual(result["micro"][0]["score"], 7.)
        self.assertEqual(result["macro"][0]["score"], 8.)
        self.assertEqual(session.search({7: 1.}, space_id=self.plan["space_id"]), {"micro": [], "macro": []})
        with self.assertRaisesRegex(ValueError, "Foreign"):
            session.search({}, space_id="foreign")
        self.assertEqual(validate_store(self.root, source=self.source())["empty_rows"], 1)

    def test_resume_does_not_reencode_or_rewrite_committed_shards(self):
        self.build(stop_after=1)
        saved = {p: sha(p) for p in self.root.glob("shards/*/*")}
        self.encoded.clear()
        self.build()
        self.assertEqual(self.encoded, [2, 3, 4])
        self.assertTrue(all(sha(p) == h for p, h in saved.items()))
        self.encoded.clear()
        before = sha(self.root / "manifest.json")
        self.build()
        self.assertFalse(self.encoded)
        self.assertEqual(sha(self.root / "manifest.json"), before)

    def test_partial_attempt_preserved_and_ignored(self):
        def fail(folder):
            raise KeyboardInterrupt("mid-write")
        with self.assertRaises(KeyboardInterrupt):
            self.build(before_commit=fail)
        orphan = next(self.root.glob("shards/*/weights.npz"))
        checksum = sha(orphan)
        self.assertFalse((self.root / "manifest.json").exists())
        self.build()
        self.assertEqual(sha(orphan), checksum)
        self.assertEqual(len(list(self.root.glob("shards/*/complete.json"))), 3)

    def test_corrupt_committed_part_fails_without_reencoding(self):
        self.build(stop_after=1)
        path = next(self.root.glob("shards/*/weights.npz"))
        with path.open("ab") as handle:
            handle.write(b"corrupt")
        self.encoded.clear()
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.encoded)
        self.assertFalse((self.root / "manifest.json").exists())

    def test_changed_plan_mapping_or_missing_rows_fails_closed(self):
        self.build(stop_after=1)
        self.plan["shard_rows"] = 3
        with self.assertRaisesRegex(ValueError, "binding"):
            self.build()
        self.plan["shard_rows"] = 2
        self.rows[0]["chunk_id"] = "other"
        with self.assertRaisesRegex(ValueError, "mapping"):
            self.build()

    def test_source_coverage_and_publication_guard(self):
        self.rows.pop()
        with self.assertRaisesRegex(ValueError, "coverage"):
            self.build()
        self.assertFalse((self.root / "manifest.json").exists())

    def test_model_source_change_blocks_manifest_publication(self):
        with self.assertRaisesRegex(ValueError, "modified"):
            self.build(before_publish=lambda: (_ for _ in ()).throw(ValueError("source modified")))
        self.assertFalse((self.root / "manifest.json").exists())
        self.encoded.clear()
        self.build()
        self.assertFalse(self.encoded)

    def test_streaming_matrix_peak_is_one_shard(self):
        self.build()
        import genshin_corpus.indexing.sparse_full as module
        original, observed = module.matrix_read, []
        def track(folder, receipt, vocab):
            matrix, rows = original(folder, receipt, vocab)
            observed.append(matrix.shape[0])
            return matrix, rows
        with patch.object(module, "matrix_read", track):
            StreamingSparseSession(self.root).search({1: 1.}, space_id=self.plan["space_id"])
        self.assertLessEqual(max(observed), 2)

    def test_ram_disk_failures_do_not_publish(self):
        with patch("genshin_corpus.indexing.sparse_full.ram_guard", side_effect=ValueError("RAM")):
            with self.assertRaisesRegex(ValueError, "RAM"):
                self.build()
        self.assertFalse(self.encoded)
        with patch("genshin_corpus.indexing.sparse_full.disk_guard", side_effect=ValueError("Disk")):
            with self.assertRaisesRegex(ValueError, "Disk"):
                self.build()
        self.assertFalse(self.encoded)

    def test_kernel_lock_releases_and_blocks_second_writer(self):
        with build_lock(self.root):
            with self.assertRaisesRegex(Exception, "another process"):
                with build_lock(self.root):
                    pass
        with build_lock(self.root):
            pass

    def test_cuda_oom_halves_batch_and_singleton_fails(self):
        self.root.mkdir()
        class OOM(Exception):
            pass
        calls = []
        fake = SimpleNamespace(encoder=SimpleNamespace(lengths=lambda texts: [2]*len(texts), model_config={"max_tokens": 8}, model=True),
                               torch=SimpleNamespace(cuda=SimpleNamespace(OutOfMemoryError=OOM, empty_cache=lambda: None)))
        def encode(texts):
            calls.append(len(texts))
            if len(texts) > 1:
                raise OOM("test")
            return [{1: 1.}]
        fake.encode = encode
        self.assertEqual(len(encode_shard(fake, ["a", "b", "c"], self.config, self.root, self.rows[:3])), 3)
        self.assertEqual(calls, [2, 1, 1, 1])
        fake.encode = lambda texts: (_ for _ in ()).throw(OOM("single"))
        with self.assertRaises(OOM):
            encode_shard(fake, ["a"], self.config, self.root, self.rows[:1])

    def test_hard_process_exit_lock_release_and_resume(self):
        # Actual process termination with one commit + one orphan; never a model.
        payload = Path(self.temp.name) / "fixture.json"
        payload.write_text(json.dumps({"plan": self.plan, "rows": self.rows, "vectors": self.vectors, "config": self.config}), encoding="utf-8")
        marker = Path(self.temp.name) / "ready"
        script = """
import json,sys,time
from pathlib import Path
from genshin_corpus.indexing.sparse_full import build_stream
from genshin_corpus.retrieval.local_qwen3_build import build_lock
p=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')); root=Path(sys.argv[2])
def encode(texts, rows): return [{int(k):v for k,v in p['vectors'][r['input_row']].items()} for r in rows]
def stop(folder):
    if folder.name.startswith('part-00001-'):
        Path(sys.argv[3]).write_text('ready')
        time.sleep(30)
with build_lock(root):
    build_stream(root,p['plan'],iter((r,str(r['input_row'])) for r in p['rows']),encode,config=p['config'],before_commit=stop)
"""
        process = subprocess.Popen([sys.executable, "-B", "-c", script, str(payload), str(self.root), str(marker)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 15
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(marker.exists(), "Child did not reach incomplete second shard")
            process.kill()
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stderr.close()
        saved = {p: sha(p) for p in self.root.glob("shards/*/complete.json")}
        with build_lock(self.root):
            self.build()
        self.assertEqual(self.encoded, [2, 3, 4])
        self.assertTrue(all(sha(p) == h for p, h in saved.items()))

    def test_validate_command_handler_uses_independent_source_and_no_encode(self):
        self.build()
        self.plan["source"] = {"input_manifest": "fixture"}
        fake_encoder = SimpleNamespace(space_id=self.plan["space_id"], close=lambda: None)
        fake_reader = SimpleNamespace(__enter__=lambda s: s, __exit__=lambda *a: None)
        class Reader:
            def __enter__(self): return self
            def __exit__(self, *args): pass
        with patch.object(sparse_job, "verify_plan", return_value=(self.plan, {"dense": {"manifest": "fixture"}}, {})), \
             patch.object(sparse_job, "make_encoder", return_value=(fake_encoder, None)), \
             patch.object(sparse_job, "source_stream", side_effect=lambda path: self.source()), \
             patch.object(sparse_job, "source_preview", return_value=[{"text": "fixture source"}]), \
             patch("genshin_corpus.retrieval.clean_based.source.InputReader", return_value=Reader()), \
             patch("genshin_corpus.retrieval.clean_based.trial.select_rows", return_value=([], {"selected": 0})):
            report = sparse_job.validate_job(Path(self.temp.name), self.config, {}, self.root)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["counts"], self.plan["counts"])
        self.assertTrue((self.root / "validation.json").exists())

    def test_run_and_resume_handlers_and_explicit_run_does_not_start_preflight(self):
        self.plan["source"] = {"input_manifest": "fixture", "files": {}}
        self.plan["code_sha256"] = {}
        fake_encoder = SimpleNamespace(space_id=self.plan["space_id"], close=lambda: None,
            encoder=SimpleNamespace(tokenizer=SimpleNamespace(vocab_size=8)))
        with patch.object(sparse_job, "verify_plan", return_value=(self.plan, {}, {})), \
             patch.object(sparse_job, "make_encoder", return_value=(fake_encoder, None)), \
             patch.object(sparse_job, "source_stream", side_effect=lambda path: self.source()), \
             patch.object(sparse_job, "code_files", return_value={}), \
             patch.object(sparse_job, "gpu_guard", return_value={}), \
             patch.object(sparse_job, "encode_shard", side_effect=lambda e,t,c,o,r: self.encode(t,r)):
            self.root.mkdir()
            sparse_job.run_build(Path(self.temp.name), self.config, {}, self.root, "run")
            self.encoded.clear()
            sparse_job.run_build(Path(self.temp.name), self.config, {}, self.root, "resume")
            self.assertFalse(self.encoded)
            with self.assertRaisesRegex(ValueError, "Resume"):
                sparse_job.run_build(Path(self.temp.name), self.config, {}, self.root, "run")

    def test_output_guard_rejects_ancestors_assets_and_historical_reference(self):
        repo = Path(self.temp.name)
        source = repo / "data/retrieval/source/input/manifest.json"
        source.parent.mkdir(parents=True)
        source.write_text(json.dumps({"source_manifests": {"chunk": str(repo / "chunk/manifest.json"),
                                                         "clean": str(repo / "clean/metadata/manifest.json")}}))
        sel = {"dense": {"weights_dir": ".local/model"}}
        manifests = {"dense": (repo / "data/retrieval/selected/dense/manifest.json", {"input_manifest": str(source)}),
                     "bm25f": (repo / "data/retrieval/selected/bm/manifest.json", {})}
        with patch("genshin_corpus.retrieval.clean_based.indexes.selected", return_value=(sel, manifests)):
            for target in (repo / "data/retrieval", source.parent / "child", repo / "data/retrieval/selected",
                           repo / "data/retrieval/old-accepted/child"):
                with self.assertRaisesRegex(ValueError, "output|Output"):
                    sparse_job.output_guard(repo, {}, target, [repo / "data/retrieval/old-accepted"])
            sparse_job.output_guard(repo, {}, repo / "data/retrieval/new")

    def test_plan_pins_model_and_head_for_final_publication_guard(self):
        repo = Path(self.temp.name)
        weight, head = repo / "weight.bin", repo / "sparse_linear.pt"
        weight.write_bytes(b"weights")
        head.write_bytes(b"head")
        encoder = SimpleNamespace(encoder=SimpleNamespace(path=repo, files={"weight.bin": sha(weight)},
            tokenizer=SimpleNamespace(vocab_size=8)), path=head, contract={"head_sha256": sha(head)}, space_id="space")
        reader = SimpleNamespace(bundle_id="fixture", manifest={"counts": self.plan["counts"]})
        with patch.object(sparse_job, "code_files", return_value={}):
            plan = sparse_job.plan_for(repo, self.config | {"shard_rows": 2}, reader, {"files": {}}, encoder)
        self.assertEqual(set(plan["source"]["files"]), {str(weight), str(head)})
        sparse_job.check_files(plan["source"]["files"])
        head.write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "changed"):
            sparse_job.check_files(plan["source"]["files"])

    def test_streams_beyond_trial_cap_with_production_shard_size(self):
        plan = self.plan | {"shard_rows": 256, "counts": {"documents": 513, "micro": 513, "macro": 513}}
        def source():
            for i in range(513):
                yield {"input_row": i, "chunk_id": str(i), "granularities": ["micro", "macro"]}, "fixture"
        sizes = []
        def encode(texts, rows):
            sizes.append(len(texts))
            return [{1: 1.}] * len(texts)
        build_stream(self.root, plan, source(), encode, config=self.config)
        self.assertEqual(sizes, [256, 256, 1])
        self.assertEqual(validate_store(self.root, source=source())["counts"], plan["counts"])


if __name__ == "__main__":
    unittest.main()
