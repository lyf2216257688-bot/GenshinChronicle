"""Finite network-forbidden recovery and searchable-index regressions."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from genshin_corpus.build_runtime import atomic_json
from genshin_corpus.evidence_io import digest, read, sha
from genshin_corpus.indexing import ChunkInputSource, iter_inputs, prepare_inputs
from genshin_corpus.indexing import benchmark_dense as dense
from genshin_corpus.indexing import benchmark_lexical as lexical
from genshin_corpus.indexing.benchmark import load_config
from genshin_corpus.indexing.benchmark import execute
from genshin_corpus.indexing.benchmark_io import (attempt, build_lock, completed_input, disk_guard, child_process,
                                               dump_lines, event, lines, mapping)
from tests.indexing.test_indexing import build_source

REPO = Path(__file__).resolve().parents[2]


class FakeOOM(RuntimeError):
    pass


class FakeEncoder:
    def __init__(self, *, max_tokens=10000, oom_above=None):
        self.contract = {"model": "offline-fixture", "revision": "fixed", "tokenizer_sha256": "0" * 64,
                         "dimensions": 8, "pooling": "fixture", "normalization": "L2", "document_prefix": "",
                         "query_prefix": "", "max_tokens": max_tokens}
        self.space_id = digest(self.contract)
        self.oom_above = oom_above
        self.calls = []
        self.torch = SimpleNamespace(cuda=SimpleNamespace(OutOfMemoryError=FakeOOM, empty_cache=lambda: None))

    def lengths(self, texts, role="document"):
        return [len(text) + 2 for text in texts]

    def encode(self, texts, *, role="document"):
        import numpy as np
        self.calls.append((role, list(texts)))
        if self.oom_above is not None and len(texts) > self.oom_above:
            raise FakeOOM("injected OOM")
        vectors = np.array([list(sha256(text.encode()).digest()[:8]) for text in texts], dtype=np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


@unittest.skipUnless(importlib.util.find_spec("torch"), "Torch runtime required for pooling checks")
class EncoderContractTests(unittest.TestCase):
    def test_prefixes_are_role_specific_and_preserve_document_text(self):
        config = load_config(REPO / "config/index-benchmark-v1.json")
        for model in config["models"]:
            encoder = dense.Encoder.__new__(dense.Encoder)
            encoder.model_config = model
            self.assertEqual(encoder.project(["原文😀"], "document"), [model["document_prefix"] + "原文😀"])
            self.assertEqual(encoder.project(["问题"], "query"), [model["query_prefix"] + "问题"])
        self.assertEqual(config["models"][2]["document_prefix"], "Document: ")

    def test_actual_pooling_handles_padding_and_cls(self):
        import numpy as np
        import torch
        hidden = torch.tensor([[[1., 0.], [0., 1.], [1., 1.]], [[2., 0.], [0., 2.], [3., 4.]]])
        class Tokens(dict):
            def to(self, device):
                return self
        for pooling, mask, expected in [
            ("last_nonpadding_token", [[0, 1, 1], [1, 1, 1]], [[2 ** -0.5, 2 ** -0.5], [.6, .8]]),
            ("last_nonpadding_token", [[1, 1, 0], [1, 1, 1]], [[0, 1], [.6, .8]]),
            ("cls_token", [[1, 1, 0], [1, 1, 1]], [[1, 0], [1, 0]])]:
            encoder = dense.Encoder.__new__(dense.Encoder)
            encoder.model_config = {"pooling": pooling, "max_tokens": 4, "document_prefix": "", "dimensions": 2}
            encoder.contract = {"dimensions": 2}
            encoder.torch = torch
            encoder.model = lambda **kwargs: SimpleNamespace(last_hidden_state=hidden)
            encoder.lengths = lambda texts, role="document": [2, 3]
            encoder.tokenizer = lambda *args, **kwargs: Tokens(attention_mask=torch.tensor(mask))
            np.testing.assert_allclose(encoder.encode(["甲", "乙"]), expected, atol=1e-6)


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = load_config(REPO / "config/index-benchmark-v1.json")
        self.config.update(shard_rows=3, minimum_free_gib=0.001, minimum_available_ram_gib=0.001,
                           padded_token_budget=100000)
        self.config["bm25f"]["commit_rows"] = 2
        source = ChunkInputSource(build_source(self.root, long_story=True))
        self.manifest = self.root / "inputs/manifest.json"
        prepare_inputs(source, self.manifest.parent)
        self.rows = list(iter_inputs(self.manifest))
        blocked = patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden"))
        blocked.start()
        self.addCleanup(blocked.stop)

    def test_disk_guard_and_unique_attempts(self):
        with patch("shutil.disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(ValueError, "Disk guard"):
                disk_guard(self.root, self.config)
        one, two = attempt(self.root, "partial"), attempt(self.root, "partial")
        self.assertNotEqual(one, two)
        self.assertTrue(one.exists())

    def test_kernel_lock_rejects_concurrent_owner(self):
        with build_lock(self.root):
            with self.assertRaises(Exception):
                with build_lock(self.root):
                    self.fail("second lock acquired")
        with build_lock(self.root):
            pass

    def test_startup_and_resident_ram_guards_have_separate_budgets(self):
        from genshin_corpus.indexing.benchmark_io import ram_guard, GIB
        config = deepcopy(self.config)
        config["minimum_available_ram_gib"] = 1.5
        config["minimum_running_ram_gib"] = 0.25
        with patch("genshin_corpus.indexing.benchmark_io.resources", return_value={"available_ram_bytes": GIB}):
            with self.assertRaisesRegex(ValueError, "Insufficient"):
                ram_guard(config)
            self.assertEqual(ram_guard(config, model_loaded=True)["available_ram_bytes"], GIB)

    def test_child_tree_is_owned_and_stopped_on_context_exit(self):
        target = self.root / "child-ready.txt"
        command = [sys.executable, "-c", "import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('ready'); time.sleep(120)", str(target)]
        with child_process(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
            deadline = time.monotonic() + 15
            while not target.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(target.exists())
            self.assertIsNone(process.poll())
        self.assertIsNotNone(process.poll())

    @unittest.skipUnless(os.name == "nt" and importlib.util.find_spec("psutil"), "Windows job lifetime check")
    def test_hard_supervisor_exit_stops_child_interpreter(self):
        import psutil
        target = self.root / "descendant-pid.txt"
        child_code = "import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(120)"
        supervisor_code = (
            "import os,pathlib,subprocess,sys,time\n"
            "from genshin_corpus.indexing.benchmark_io import child_process\n"
            "with child_process([sys.executable,'-c',sys.argv[2],sys.argv[1]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL):\n"
            "    deadline=time.monotonic()+10\n"
            "    while not pathlib.Path(sys.argv[1]).exists() and time.monotonic()<deadline: time.sleep(.02)\n"
            "    os._exit(0)\n")
        result = subprocess.run([sys.executable, "-c", supervisor_code, str(target), child_code],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertTrue(target.exists())
        pid = int(target.read_text())
        deadline = time.monotonic() + 5
        while psutil.pid_exists(pid) and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertFalse(psutil.pid_exists(pid))


class SupervisorTests(FixtureTests):
    def args(self, mode):
        config = deepcopy(self.config)
        config["chunk_manifest"] = str(self.root / "chunks/manifest.json")
        path = self.root / "config.json"
        if not path.exists():
            atomic_json(path, config)
        return SimpleNamespace(repo=str(REPO), output=str(self.root / "experiment"), config=str(path),
                               mode=mode, input_manifest=str(self.manifest), allow_subset=False,
                               query=None, questions=None, top_k=3)

    def fake_child(self, python, action, **kwargs):
        folder = Path(kwargs["root"])
        folder.mkdir(parents=True, exist_ok=True)
        if action.endswith("build"):
            atomic_json(folder / "manifest.json", {"test": action})
        return {"action": action, "model": kwargs.get("model"), "exit_code": 0, "elapsed_seconds": 0}

    def test_four_modes_and_serial_auto_preflight(self):
        with patch("genshin_corpus.indexing.benchmark.code_binding", return_value={}), \
             patch("genshin_corpus.indexing.benchmark.run_child", side_effect=self.fake_child) as child:
            self.assertEqual(execute(self.args("preflight")), 0)
            self.assertEqual(len(child.call_args_list), 4)
            child.reset_mock()
            self.assertEqual(execute(self.args("run")), 0)
            actions = [call.args[1] for call in child.call_args_list]
            self.assertEqual(actions[:4], ["dense-preflight"] * 3 + ["bm25f-preflight"])
            self.assertEqual(actions[4:8], ["dense-build"] * 3 + ["bm25f-build"])
            self.assertEqual(actions[8:12], ["dense-validate"] * 3 + ["bm25f-validate"])
            self.assertEqual(actions[12:], ["dense-query"] * 3 + ["bm25f-query"])
            self.assertTrue((self.root / "experiment/complete.json").exists())
            with self.assertRaisesRegex(ValueError, "Use Resume"):
                execute(self.args("run"))
            self.assertEqual(execute(self.args("resume")), 0)
            self.assertEqual(execute(self.args("validate")), 0)

    def test_changed_config_rejected_and_failed_preflight_does_not_build_that_model(self):
        def fail_one(python, action, **kwargs):
            result = self.fake_child(python, action, **kwargs)
            if action == "dense-preflight" and kwargs["model"] == self.config["models"][0]["key"]:
                result["exit_code"] = 1
            return result
        with patch("genshin_corpus.indexing.benchmark.code_binding", return_value={}), \
             patch("genshin_corpus.indexing.benchmark.run_child", side_effect=fail_one) as child:
            self.assertEqual(execute(self.args("run")), 1)
            builds = [c.kwargs["model"] for c in child.call_args_list if c.args[1] == "dense-build"]
            self.assertNotIn(self.config["models"][0]["key"], builds)
            self.assertFalse((self.root / "experiment/complete.json").exists())
            args = self.args("resume")
            config = read(args.config)
            config["max_batch"] = 1
            atomic_json(Path(args.config), config)
            with self.assertRaisesRegex(ValueError, "changed"):
                execute(args)

    def test_unfinished_input_export_is_retained_and_restarted(self):
        from genshin_corpus.indexing.benchmark import prepare
        root = self.root / "experiment"
        incomplete = root / "input-attempts/orphan/bundle"
        incomplete.mkdir(parents=True)
        (incomplete / "inputs.jsonl").write_bytes(b"partial")
        config = deepcopy(self.config)
        config["chunk_manifest"] = str(self.root / "chunks/manifest.json")
        result = prepare(root, REPO, config)
        self.assertEqual(completed_input(result)["documents"], len(self.rows))
        self.assertEqual((incomplete / "inputs.jsonl").read_bytes(), b"partial")

    def test_input_export_reports_final_counts(self):
        source = ChunkInputSource(self.root / "chunks/manifest.json")
        calls = []
        result = prepare_inputs(source, self.root / "second-export", progress=calls.append)
        self.assertEqual(calls[-1], result["counts"])


@unittest.skipUnless(importlib.util.find_spec("numpy"), "Dense validation runtime requires numpy")
class DenseBenchmarkTests(FixtureTests):
    def prepare_dense(self, encoder=None):
        encoder = encoder or FakeEncoder()
        root = self.root / "dense"
        dense.preflight(encoder, self.manifest, root, self.config)
        return root, encoder

    def test_interrupt_resume_reuses_commits_and_shared_route_vectors(self):
        root, encoder = self.prepare_dense()
        result = dense.build(encoder, self.manifest, root, self.config, stop_after=1)
        self.assertEqual(result["status"], "INTERRUPTED_FOR_TEST")
        self.assertFalse((root / "manifest.json").exists())
        committed = next((root / "shards").glob("*/complete.json"))
        before = sha(committed)
        orphan = attempt(root / "shards", "partial")
        (orphan / "vectors.npy").write_bytes(b"partial")
        replacement = FakeEncoder()
        dense.build(replacement, self.manifest, root, self.config)
        self.assertEqual(sha(committed), before)
        self.assertTrue(orphan.exists())
        docs = sum(len(texts) for role, texts in replacement.calls if role == "document")
        self.assertEqual(docs, len(self.rows) - self.config["shard_rows"])
        self.assertEqual(dense.validate(self.manifest, root)["rows"], len(self.rows))
        first = self.rows[0]
        vector = replacement.encode([first["dense_text"]], role="query")[0]
        for level in first["granularities"]:
            hits = dense.search(root, vector, space_id=replacement.space_id, granularity=level, top_k=len(self.rows))
            self.assertIn(first["chunk_id"], [hit["chunk_id"] for hit in hits])
            self.assertTrue(all(level in hit["granularities"] for hit in hits))

    def test_overlength_is_recorded_and_blocks_before_encoding(self):
        encoder, root = FakeEncoder(max_tokens=5), self.root / "blocked"
        with self.assertRaisesRegex(ValueError, "overlength"):
            dense.preflight(encoder, self.manifest, root, self.config)
        self.assertFalse(encoder.calls)
        folder = root / read(root / "lengths.json")["folder"]
        self.assertEqual(read(folder / "complete.json")["exceeded"], len(self.rows))
        self.assertEqual(len(list(lines(folder / "overlength.jsonl"))), len(self.rows))
        self.assertFalse((root / "manifest.json").exists())

    def test_oom_halves_batch_without_truncation(self):
        encoder = FakeEncoder(oom_above=1)
        rows = ["完整文本甲", "完整文本乙"]
        result = dense.encode_bounded(encoder, rows, encoder.lengths(rows), self.config, self.root)
        self.assertEqual(result.shape, (2, 8))
        self.assertEqual([len(texts) for _, texts in encoder.calls], [2, 1, 1])
        self.assertIn('"event": "cuda_oom"', (self.root / "events.jsonl").read_text(encoding="utf-8"))

    def test_single_oom_remains_incomplete(self):
        encoder = FakeEncoder(oom_above=0)
        with self.assertRaises(FakeOOM):
            dense.encode_bounded(encoder, ["完整文本"], [6], self.config, self.root)

    def test_corrupt_shard_and_foreign_query_rejected(self):
        root, encoder = self.prepare_dense()
        dense.build(encoder, self.manifest, root, self.config)
        vector = encoder.encode(["查询"], role="query")[0]
        with self.assertRaisesRegex(ValueError, "Foreign query"):
            dense.search(root, vector, space_id="foreign", granularity="micro")
        path = next((root / "shards").glob("*/vectors.npy"))
        with path.open("ab") as handle:
            handle.write(b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum"):
            dense.validate(self.manifest, root)
        with self.assertRaisesRegex(ValueError, "checksum"):
            dense.build(encoder, self.manifest, root, self.config)

    def test_token_audit_corruption_is_rejected(self):
        root, encoder = self.prepare_dense()
        folder = root / read(root / "lengths.json")["folder"]
        (folder / "lengths.jsonl").write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checksum"):
            dense.preflight(encoder, self.manifest, root, self.config)


@unittest.skipUnless(importlib.util.find_spec("whoosh") and importlib.util.find_spec("jieba"), "Whoosh3 runtime required")
class LexicalBenchmarkTests(FixtureTests):
    def test_commit_before_journal_resume_and_independent_routes(self):
        root = self.root / "lexical"
        lexical.build(self.manifest, root, self.config, stop_after=1)
        self.assertFalse((root / "manifest.json").exists())
        lexical.build(self.manifest, root, self.config)
        result = lexical.validate(self.manifest, root, self.config)
        self.assertEqual(result["memberships"], {level: sum(level in r["granularities"] for r in self.rows)
                                                 for level in ("micro", "macro")})
        for level in ("micro", "macro"):
            hits = lexical.search(root, "长句", granularity=level, config=self.config, top_k=50)
            self.assertTrue(hits)
            self.assertTrue(all(level in hit["granularities"] for hit in hits))
            self.assertEqual(set(hits[0]["lexical_fields"]), set(lexical.LEXICAL_FIELDS))

    def test_dictionary_and_weights_are_bound(self):
        root = self.root / "lexical"
        lexical.build(self.manifest, root, self.config, stop_after=1)
        config = deepcopy(self.config)
        config["bm25f"]["field_weights"]["title"] = 2
        with self.assertRaisesRegex(ValueError, "binding changed"):
            lexical.build(self.manifest, root, config)

    def test_analyzers_do_not_form_cross_value_bigrams(self):
        effective, segment = lexical.configuration(self.config["bm25f"], self.root / "lexical")
        fields = {field: [] for field in lexical.LEXICAL_FIELDS}
        fields["body"] = ["坎瑞", "亚"]
        result = lexical.analyze_fields(fields, effective["analyzers"], segmenter=segment)
        self.assertEqual(result["body"]["han_bigram"], ["坎瑞"])
        self.assertTrue(effective["analyzers"]["segmenter"]["dictionary_sha256"])

    def test_corrupt_index_file_rejected(self):
        root = self.root / "lexical"
        lexical.build(self.manifest, root, self.config)
        name = next(iter(read(root / "manifest.json")["files"]))
        with (root / name).open("ab") as handle:
            handle.write(b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum"):
            lexical.validate(self.manifest, root, self.config)
