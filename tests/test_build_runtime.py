"""Small source-backed fixtures for parallel builds and recovery boundaries."""
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from genshin_corpus.build_runtime import record_jobs
from genshin_corpus.clean.pipeline import build_clean_run
from genshin_corpus.evidence_io import read, sha
from tests.clean import test_clean


def square(value):
    return value * value


def abrupt_exit(value):
    os._exit(17)


class BuildRuntimeTests(unittest.TestCase):
    def test_worker_death_surfaces_instead_of_waiting_for_a_lost_task(self):
        from concurrent.futures.process import BrokenProcessPool
        with self.assertRaises(BrokenProcessPool):
            list(record_jobs(abrupt_exit, [1], workers=2))

    def test_spawn_dynamic_jobs_and_invalid_worker_counts(self):
        self.assertEqual(sorted(record_jobs(square, range(15), workers=12)), [x*x for x in range(15)])
        for workers in (0, -1, True):
            with self.assertRaises(ValueError):
                list(record_jobs(square, [], workers=workers))


class CleanWorkerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_clean.CleanTests()
        self.fixture.setUp()
        self.raw = self.fixture.raw_run()

    def tearDown(self):
        self.fixture.tearDown()

    def test_serial_and_twelve_worker_records_are_byte_identical(self):
        first = self.fixture.build(self.raw, "serial", workers=1)
        parallel = self.fixture.build(self.raw, "parallel", workers=12)
        self.assertEqual(first["records"], parallel["records"])
        for entry in first["records"]:
            a = self.fixture.manifest_path("serial").parent.parent / entry["record_path"]
            b = self.fixture.manifest_path("parallel").parent.parent / entry["record_path"]
            self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_receipts_recover_unlisted_completed_records_without_recleaning(self):
        first = self.fixture.build(self.raw)
        path = self.fixture.manifest_path()
        manifest = {**first, "status": "partial", "records": [], "accounted_detail_count": 0}
        path.write_text(json.dumps(manifest), encoding="utf8")
        record_paths = [path.parent.parent / e["record_path"] for e in first["records"]]
        before = [p.stat().st_mtime_ns for p in record_paths]
        with patch("genshin_corpus.clean.pipeline.clean_obc_detail", side_effect=AssertionError("Already complete")), \
             patch("genshin_corpus.clean.pipeline.validate_record", side_effect=AssertionError("Already validated")):
            resumed = self.fixture.build(self.raw)
        self.assertEqual(resumed["status"], "complete")
        self.assertTrue(all(e["reused"] for e in resumed["records"]))
        self.assertEqual(before, [p.stat().st_mtime_ns for p in record_paths])

    def test_completed_receipt_input_mismatch_fails_closed(self):
        self.fixture.build(self.raw)
        path = next((self.fixture.manifest_path().parent / "completed").glob("*.json"))
        receipt = read(path)
        receipt["binding"] = "changed"
        path.write_text(json.dumps(receipt), encoding="utf8")
        with self.assertRaises(ValueError):
            self.fixture.build(self.raw)

    def test_legacy_completed_run_is_read_only_without_receipts(self):
        self.fixture.build(self.raw)
        receipts = self.fixture.manifest_path().parent / "completed"
        for path in receipts.glob("*.json"):
            path.unlink()
        before = self.fixture.manifest_path().read_bytes()
        self.fixture.build(self.raw)
        self.assertEqual(before, self.fixture.manifest_path().read_bytes())
        self.assertEqual(list(receipts.glob("*.json")), [])

    def test_only_orphan_diagnostic_serialization_order_is_stabilized(self):
        from copy import deepcopy
        from genshin_corpus.clean.pipeline import _record_bytes
        group = {"root_id": "r", "contents": {n: {"option": "", "dialogue": n, "icon": ""}
                                               for n in ("r", "a", "b", "c")}, "child_ids": {}}
        record = self.fixture.record(group, "interactive_dialogue")
        changed = deepcopy(record)
        positions = [i for i, d in enumerate(changed["diagnostics"]) if d["code"] == "DIALOGUE_ORPHAN_NODE"]
        for index, value in zip(positions, reversed([changed["diagnostics"][i] for i in positions])):
            changed["diagnostics"][index] = value
        self.assertEqual(_record_bytes(record), _record_bytes(changed))
        self.assertEqual(record["sections"], changed["sections"])
        changed["diagnostics"][positions[0]]["source_path"] = "changed"
        self.assertNotEqual(_record_bytes(record), _record_bytes(changed))


@unittest.skipUnless(importlib.util.find_spec("tokenizers"), "Uses the existing tokenizer runtime")
class ChunkWorkerTests(unittest.TestCase):
    def setUp(self):
        from tokenizers import Tokenizer, models, pre_tokenizers, processors
        self.fixture = test_clean.CleanTests()
        self.fixture.setUp()
        self.root = self.fixture.root
        self.fixture.build(self.fixture.raw_run())
        tokenizer = Tokenizer(models.WordLevel({"<unk>": 0, "<s>": 1, "</s>": 2}, unk_token="<unk>"))
        tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
        tokenizer.post_processor = processors.RobertaProcessing(("</s>", 2), ("<s>", 1))
        self.tokenizer_path = self.root / "tokenizer.json"
        tokenizer.save(str(self.tokenizer_path))
        self.selection = self.root / "selection.json"
        self.selection.write_text(json.dumps({"content_ids": ["one", "two"],
            "profiles": [{"name": "balanced", "micro_target": 240, "micro_hard": 384,
                          "macro_target": 720, "macro_hard": 1024}],
            "review_content_ids": ["one"], "bge_document_token_cap": 1792}), encoding="utf8")

    def tearDown(self):
        self.fixture.tearDown()

    def run_build(self, name, *, workers=1, resume=False):
        from genshin_corpus.chunking.pilot import run_pilot
        return run_pilot(self.fixture.manifest_path(), self.selection,
                         self.tokenizer_path, self.tokenizer_path, self.root / name,
                         workers=workers, resume=resume)

    def test_serial_and_twelve_worker_exports_and_pair_counts_identical(self):
        from tokenizers import Tokenizer
        from genshin_corpus.chunking.pilot import PROBE_QUERY
        first = self.run_build("serial")
        second = self.run_build("parallel", workers=12)
        self.assertEqual({k:v for k,v in first.items() if k != "elapsed_seconds"},
                         {k:v for k,v in second.items() if k != "elapsed_seconds"})
        for name in ("parents.jsonl", "graphs.jsonl", "admission.jsonl", "chunks-balanced.jsonl", "review.md"):
            self.assertEqual(sha(self.root / "serial" / name), sha(self.root / "parallel" / name))
        tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
        for line in (self.root / "parallel/chunks-balanced.jsonl").read_text(encoding="utf8").splitlines():
            chunk = json.loads(line)
            self.assertEqual(len(tokenizer.encode(PROBE_QUERY, chunk["retrieval_text"]).ids), chunk["bge_probe_pair_tokens"])

    def test_interrupted_record_build_skips_completed_receipt_on_resume(self):
        from genshin_corpus.chunking import pilot
        original_jobs = pilot.record_jobs

        def interrupted(*args, **kwargs):
            jobs = original_jobs(*args, **kwargs)
            yield next(jobs)
            raise KeyboardInterrupt("Fixture interruption")

        with patch.object(pilot, "record_jobs", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.run_build("resume")
        receipt = next((self.root / "resume/_records").glob("*/receipt.json"))
        first_mtime = receipt.stat().st_mtime_ns
        self.assertEqual(read(self.root / "resume/build-state.json")["status"], "partial")
        with patch.object(pilot, "_chunk_record_job", wraps=pilot._chunk_record_job) as process:
            self.run_build("resume", resume=True)
        self.assertEqual(process.call_count, 1)
        self.assertEqual(first_mtime, receipt.stat().st_mtime_ns)

    def test_interrupted_merge_resumes_without_tokenizer_or_record_work(self):
        from genshin_corpus.chunking import pilot
        original = pilot._atomic_text

        def fail_merge(path):
            if Path(path) == self.root / "resume/parents.jsonl":
                raise OSError("Fixture merge interruption")
            return original(path)

        with patch.object(pilot, "_atomic_text", side_effect=fail_merge):
            with self.assertRaises(OSError):
                self.run_build("resume")
        with patch.object(pilot, "_init_chunk_worker", side_effect=AssertionError("No pending records")):
            self.run_build("resume", workers=12, resume=True)
        self.assertEqual(read(self.root / "resume/manifest.json")["status"], "complete")

    def test_resume_detects_changed_selection_source_and_artifacts(self):
        self.run_build("complete")
        original = self.selection.read_bytes()
        selection = read(self.selection)
        selection["content_ids"].reverse()
        self.selection.write_text(json.dumps(selection), encoding="utf8")
        with self.assertRaises(ValueError):
            self.run_build("complete", resume=True)
        self.selection.write_bytes(original)
        path = self.root / "complete/chunks-balanced.jsonl"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            self.run_build("complete", resume=True)
        self.run_build("source")
        manifest = read(self.fixture.manifest_path())
        source = self.fixture.manifest_path().parent.parent / manifest["records"][0]["record_path"]
        source.write_bytes(source.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            self.run_build("source", resume=True)

    def test_run_lock_and_existing_directory_are_rejected(self):
        from genshin_corpus.model_calls import locked
        self.run_build("complete")
        with self.assertRaises(FileExistsError):
            self.run_build("complete")
        with locked(self.root / "complete/build.lock"):
            with self.assertRaises(OSError):
                self.run_build("complete", resume=True)

    def test_initializer_failure_surfaces_and_completed_manifest_repairs_state(self):
        self.run_build("complete")
        state_path = self.root / "complete/build-state.json"
        state = read(state_path)
        state.update(status="partial", completed_records=0)
        state_path.write_text(json.dumps(state), encoding="utf8")
        self.run_build("complete", resume=True)
        self.assertEqual(read(state_path)["status"], "complete")
        self.assertEqual(read(state_path)["completed_records"], 2)
        self.tokenizer_path.write_text("{}", encoding="utf8")
        with self.assertRaisesRegex(RuntimeError, "Tokenizer initialization failed"):
            self.run_build("bad-tokenizer", workers=12)
