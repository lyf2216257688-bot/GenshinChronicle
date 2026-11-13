from copy import deepcopy
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from genshin_corpus.evidence_io import digest, read, seal, sha, write
from genshin_corpus.runtime_migration import capture, compare, baseline_index_check, original_batches, replay, encode_document_jobs
from genshin_corpus.retrieval.clean_based.rerank_model import stage_gte


class WitnessComparisonTests(unittest.TestCase):
    def run_case(self, change=None, should_pass=True):
        with TemporaryDirectory() as temp:
            repo = Path(temp)
            config = {"baseline": "data/retrieval/old", "candidate": "data/retrieval/new",
                      "certificate": "data/retrieval/cert", "retrieval_config": "config/retrieval.json",
                      "sparse_root": "data/retrieval/sparse", "rtol": 1e-5, "atol": 1e-6}
            old = {"python": "3.12.10", "model": "fixed", "packages": {"torch": "same"}}
            source = {"dense": {"configuration": old, "space_id": digest(old)},
                      "sparse": {"configuration": {"backbone": old, "head": "fixed"},
                                 "space_id": digest({"backbone": old, "head": "fixed"})}}
            write(repo / config["retrieval_config"], {})
            write(repo / config["sparse_root"] / "manifest.json", source["sparse"])
            shape = (34, 1024)
            base_vectors = np.ones(shape, dtype=np.float32)
            for key in ("baseline", "candidate"):
                root = repo / config[key]
                root.mkdir(parents=True)
                contract = old if key == "baseline" else {**old, "python": "3.14.5"}
                witness = {"configuration": config, "samples": [{"text": str(n), "input_row": n,
                            "chunk_id": str(n)} for n in range(34)],
                    "source_witness": {"source": {k: digest(v) for k, v in source.items()}},
                    "code": {}, "contracts": {"dense": contract, "sparse": {"backbone": contract, "head": "fixed"}},
                    "tokens": [[1, 2]] * 34, "rankings": {"dense": ["a"], "sparse": ["b"]},
                    "official_sparse_kernel": True}
                vectors, weights = base_vectors.copy(), [{"2": 1.}] * 34
                if key == "candidate" and change:
                    change(witness, vectors, weights)
                np.save(root / "dense.npy", vectors, allow_pickle=False)
                write(root / "sparse.json", weights)
                write(root / "witness.json", witness)
                if key == "baseline":
                    baseline_index_check(root, witness["samples"], vectors,
                                         {str(n): vectors[n] for n in range(34)}, config)
                seal(root)
            with patch("genshin_corpus.runtime_migration.compatibility_code", return_value={}), \
                 patch("genshin_corpus.runtime_compatibility.compatibility_code", return_value={}), \
                 patch("genshin_corpus.retrieval.clean_based.indexes.selected", return_value=(None, {"dense": (None, source["dense"])})):
                if should_pass:
                    self.assertEqual(compare(repo, config)["status"], "PASS")
                else:
                    with self.assertRaises(ValueError): compare(repo, config)
                    report = repo / config["certificate"] / "report.json"
                    if report.exists(): self.assertEqual(read(report)["status"], "FAIL")

    def test_equivalent_runtime_is_admitted_without_index_rewrite(self):
        self.run_case()

    def test_numeric_drift_token_changes_sparse_support_and_ranks_fail(self):
        changes = [lambda w, v, s: v.__setitem__((0, 0), 2.),
                   lambda w, v, s: w["tokens"].__setitem__(0, [1, 3]),
                   lambda w, v, s: s.__setitem__(0, {"3": 1.}),
                   lambda w, v, s: w["rankings"]["dense"].append("different")]
        for change in changes:
            with self.subTest(change=change): self.run_case(change, False)

    def test_unapproved_dependency_change_is_rejected(self):
        self.run_case(lambda w, v, s: w["contracts"]["dense"].update(model="different"), False)

    def test_sparse_rule_change_is_rejected(self):
        self.run_case(lambda w, v, s: w["contracts"]["sparse"].update(head="different"), False)


class BaselineDiagnosticsTests(unittest.TestCase):
    def test_incomplete_baseline_blocks_candidate_before_creating_output(self):
        with TemporaryDirectory() as temp:
            repo = Path(temp)
            config = {"retrieval_config": "config/base.json", "sparse_root": "data/sparse",
                      "baseline": "data/retrieval/old", "candidate": "data/retrieval/new"}
            write(repo / config["retrieval_config"], {})
            write(repo / config["sparse_root"] / "manifest.json", {})
            with patch("genshin_corpus.runtime_migration.platform.python_version", return_value="3.14.5"), \
                 patch("genshin_corpus.retrieval.clean_based.indexes.selected", return_value=(None,
                     {"dense": (repo / "data/index/manifest.json", {})})):
                with self.assertRaisesRegex(ValueError, "Baseline is incomplete or failed"):
                    capture(repo, config)
            self.assertFalse((repo / config["candidate"]).exists())

    def test_failed_capture_keeps_encoded_values_without_completion_or_queries(self):
        with TemporaryDirectory() as temp:
            repo = Path(temp)
            config = {"retrieval_config": "config/base.json", "sparse_root": "data/sparse",
                      "baseline": "data/retrieval/old", "sample_rows": 1, "queries": ["q"],
                      "rtol": 1e-5, "atol": 1e-6}
            write(repo / config["retrieval_config"], {"sparse_reference": {"wheel": "unused", "sha256": "fixed"},
                                                     "sparse_head_sha256": "fixed"})
            write(repo / config["sparse_root"] / "manifest.json", {"space_id": "old-sparse"})
            manifest = {"space_id": "old-dense", "input_manifest": "unused"}
            encoder = SimpleNamespace(space_id="old-dense", contract={"python": "3.12.10"},
                tokenizer=lambda *a, **k: {"input_ids": [[1, 2]]}, project=lambda texts, role: texts,
                lengths=lambda texts: [2] * len(texts),
                encode=lambda *a, **k: np.asarray([[.1, .2]], dtype=np.float32))
            sparse = SimpleNamespace(space_id="old-sparse", contract={"backbone": encoder.contract},
                reference_checks=[{"max_abs_error": 0., "status": "PASS"}], encode=lambda *a, **k: [{4: .3}])
            with (
                patch("genshin_corpus.runtime_migration.platform.python_version", return_value="3.12.10"),
                patch("genshin_corpus.runtime_migration.compatibility_code", return_value={}),
                patch("genshin_corpus.runtime_migration.source_batch_plan", return_value={"batches": []}),
                patch("genshin_corpus.runtime_migration.document_jobs", return_value=[
                    ([{"input_row": 0, "tokens": 2}], ["source"])]),
                patch("genshin_corpus.indexing.benchmark_dense.Encoder", return_value=encoder),
                patch("genshin_corpus.indexing.sparse_trial.SparseEncoder", return_value=sparse),
                patch("genshin_corpus.indexing.sparse_trial.official_kernel", return_value=(None, {})),
                patch("genshin_corpus.retrieval.clean_based.indexes.selected", return_value=(None,
                    {"dense": (repo / "data/index/manifest.json", manifest)})),
                patch("genshin_corpus.retrieval.clean_based.indexes.model_spec", return_value={}),
                patch("genshin_corpus.retrieval.clean_based.source.InputReader", return_value=nullcontext(None)),
                patch("genshin_corpus.retrieval.clean_based.trial.select_rows", return_value=([
                    {"input_row": 0, "row": {"chunk_id": "a", "dense_text": "source"}}], {})),
                patch("genshin_corpus.retrieval.clean_based.rerank_core.candidate_vectors",
                      return_value={"a": np.asarray([.1, .201], dtype=np.float32)}),
                patch("genshin_corpus.retrieval.clean_based.indexes.DenseSession",
                      side_effect=AssertionError("Failed baseline must not search indexes")),
            ):
                with self.assertRaisesRegex(ValueError, "diagnostics retained"):
                    capture(repo, config, baseline=True)
            root = repo / config["baseline"]
            self.assertEqual(np.load(root / "dense.npy").shape, (2, 2))
            self.assertEqual(len(read(root / "sparse.json")), 2)
            self.assertEqual(read(root / "encoded.json")["tokens"], [[1, 2], [1, 2]])
            self.assertEqual(read(root / "baseline-index-check.json")["status"], "FAIL")
            self.assertFalse((root / "complete.json").exists())
            self.assertFalse((root / "witness.json").exists())

    def test_failed_comparison_preserves_numbers_and_keeps_fixed_tolerance(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            samples = [{"input_row": 7, "chunk_id": "fixed"}]
            actual = np.asarray([[.1, .2]], dtype=np.float32)
            stored = {"fixed": np.asarray([.1, .201], dtype=np.float32)}
            with self.assertRaisesRegex(ValueError, "diagnostics retained"):
                baseline_index_check(root, samples, actual, stored, {"rtol": 1e-5, "atol": 1e-6})
            report = read(root / "baseline-index-check.json")
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["failed_rows"], 1)
            self.assertEqual(report["rows"][0]["failed_components"], 1)
            self.assertGreater(report["rows"][0]["max_tolerance_ratio"], 1.)
            np.testing.assert_array_equal(np.load(root / "stored-dense.npy"), [stored["fixed"]])
            self.assertFalse((root / "complete.json").exists())
            self.assertEqual(report["atol"], 1e-6)

    def test_matching_old_baseline_is_recorded_as_pass(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            vector = np.asarray([[.1, .2]], dtype=np.float32)
            result = baseline_index_check(root, [{"input_row": 0, "chunk_id": "a"}], vector,
                                          {"a": vector[0]}, {"rtol": 1e-5, "atol": 1e-6})
            self.assertEqual(result["status"], "PASS")
            self.assertTrue(result["rows"][0]["bitwise_equal"])


class OriginalBatchReplayTests(unittest.TestCase):
    def test_document_encoding_keeps_original_companion_and_extracts_only_anchors(self):
        calls = []
        def encode(texts):
            calls.append(texts)
            return np.asarray([[float(text), 0.] for text in texts], dtype=np.float32)
        encoder = SimpleNamespace(lengths=lambda texts: [2] * len(texts), encode=encode)
        jobs = [([{"input_row": 4, "tokens": 2}, {"input_row": 7, "tokens": 2}], ["4", "7"])]
        result = encode_document_jobs(encoder, jobs, [{"input_row": 4}])
        self.assertEqual(calls, [["4", "7"]])
        self.assertEqual(set(result), {4})
        with self.assertRaisesRegex(ValueError, "Missing document anchor"):
            encode_document_jobs(encoder, [], [{"input_row": 4}])

    def test_plan_matches_frozen_encoding_batch_order_and_budget(self):
        from genshin_corpus.indexing.benchmark_dense import encode_bounded
        rows = [{"input_row": n, "tokens": length} for n, length in enumerate([4, 2, 2, 7])]
        config = {"max_batch": 2, "padded_token_budget": 8}
        planned = [[r["input_row"] for r in batch] for batch in original_batches(rows, config)]
        actual = []
        def encode(texts):
            actual.append(texts)
            return np.asarray([[n, n] for n in texts], dtype=np.float32)
        encoder = SimpleNamespace(contract={"dimensions": 2}, encode=encode)
        with TemporaryDirectory() as temp:
            encode_bounded(encoder, list(range(4)), [r["tokens"] for r in rows], config, Path(temp))
        self.assertEqual(planned, [[1, 2], [0], [3]])
        self.assertEqual(planned, actual)
        self.assertEqual([r["input_row"] for r in rows], list(range(4)))
        with self.assertRaises(ValueError): original_batches(rows, {**config, "max_batch": 3})

    def test_replay_pass_and_failure_are_only_diagnostics_and_preserve_source(self):
        for drift in (False, True):
            with self.subTest(drift=drift), TemporaryDirectory() as temp:
                repo = Path(temp)
                source = repo / "data/retrieval/old"
                source.mkdir(parents=True)
                config = {"retrieval_config": "config/base.json", "baseline": "data/retrieval/old",
                          "replay_output": "data/retrieval/replay", "rtol": 1e-5, "atol": 1e-6}
                write(repo / config["retrieval_config"], {})
                expected = np.zeros((32, 1024), dtype=np.float32)
                expected[:, 0] = 1.
                captured = np.zeros((34, 1024), dtype=np.float32)
                captured[:, 0] = 1.
                captured[:26, 1] = .001
                np.save(source / "dense.npy", captured, allow_pickle=False)
                np.save(source / "stored-dense.npy", expected, allow_pickle=False)
                hashes = {name: sha(source / name) for name in ("dense.npy", "stored-dense.npy")}
                samples = [{"input_row": n, "chunk_id": str(n), "text": str(n)} for n in range(32)]
                make_row = lambda n: {"input_row": n, "chunk_id": str(n), "dense_text_sha256": str(n), "tokens": 1}
                groups = [[make_row(0), make_row(1)], [make_row(2), make_row(3)]]
                groups += [[make_row(n), make_row(100 + n)] for n in range(4, 26)]
                groups += [[make_row(n)] for n in range(26, 32)]
                plan = {"scope": "original-batch-diagnostic-only", "source": str(source), "source_files": hashes,
                        "index_manifest": "unused", "batches": groups, "new_forward_documents": 48}
                encoded = {"samples": samples, "contracts": {"dense": {}}}
                reader = SimpleNamespace(row=lambda n: {"chunk_id": str(n), "dense_text_sha256": str(n), "dense_text": str(n)})
                def encode(texts):
                    result = np.tile(expected[0], (len(texts), 1))
                    if drift and "0" in texts: result[texts.index("0"), 1] = .1
                    return result
                encoder = SimpleNamespace(contract={}, lengths=lambda texts: [1] * len(texts), encode=encode)
                with (
                    patch("genshin_corpus.runtime_migration.platform.python_version", return_value="3.12.10"),
                    patch("genshin_corpus.runtime_migration.replay_plan", return_value=(encoded, {"input_manifest": "unused"}, plan)),
                    patch("genshin_corpus.retrieval.clean_based.source.InputReader", return_value=nullcontext(reader)),
                    patch("genshin_corpus.retrieval.clean_based.rerank_core.candidate_vectors",
                          return_value={str(n): expected[n] for n in range(32)}),
                    patch("genshin_corpus.retrieval.clean_based.indexes.selected", return_value=(None, {})),
                    patch("genshin_corpus.retrieval.clean_based.indexes.model_spec", return_value={}),
                    patch("genshin_corpus.indexing.benchmark_dense.Encoder", return_value=encoder),
                ):
                    if drift:
                        with self.assertRaisesRegex(ValueError, "diagnostics retained"): replay(repo, config)
                    else:
                        self.assertFalse(replay(repo, config)["migration_admitted"])
                output = repo / config["replay_output"]
                summary = read(output / "replay-summary.json")
                self.assertEqual(summary["status"], "FAIL" if drift else "PASS")
                self.assertEqual(summary["new_forward_batches"], 24)
                self.assertEqual(summary["reused_single_item_rows"], list(range(26, 32)))
                self.assertFalse(summary["migration_admitted"])
                self.assertFalse((output / "witness.json").exists())
                self.assertFalse((source / "complete.json").exists())
                self.assertTrue(all(sha(source / name) == value for name, value in hashes.items()))


class StageTests(unittest.TestCase):
    def test_offline_stage_copy_fallback_preserves_original_bytes(self):
        from genshin_corpus.evidence_io import sha
        with TemporaryDirectory() as temp:
            root, output = Path(temp) / "source", Path(temp) / "stage"
            write(root / "config.json", {"auto_map": {"AutoConfig": "remote--configuration.NewConfig"}})
            (root / "model.safetensors").write_bytes(b"fixed test weight")
            (root / "configuration.py").write_text("FIXED = 1", encoding="utf-8")
            files = {p.name: sha(p) for p in root.iterdir()}
            with patch("genshin_corpus.retrieval.clean_based.rerank_model.os.link", side_effect=PermissionError("sandbox")):
                stage_gte(root, output, files)
            self.assertEqual(read(output / "config.json")["auto_map"]["AutoConfig"], "configuration.NewConfig")
            self.assertEqual(read(root / "config.json")["auto_map"]["AutoConfig"], "remote--configuration.NewConfig")
            self.assertEqual(sha(output / "model.safetensors"), files["model.safetensors"])
            with self.assertRaises(FileExistsError): stage_gte(root, output, files)


if __name__ == "__main__": unittest.main()
