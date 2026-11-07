"""Reward grammar and one-off recovery checks using bounded local fixtures."""
import json
from pathlib import Path
import random
import re
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from genshin_corpus.chunking.core import ChunkProfile
from genshin_corpus.chunking.pilot import _write_exports
from genshin_corpus.chunking.recover import (_check_admission, _checked_source_receipt,
                                            validate_exports)
from genshin_corpus.chunking.structure import _complete_reward_quantities
from genshin_corpus.evidence_io import read, sha
from tests.chunking.test_chunking import clean
from tests.chunking.test_v2 import candidates
from tests import test_build_runtime


class RewardQuantityTests(unittest.TestCase):
    def test_original_grammar_boundaries(self):
        for text in ("原石*60", " 原石×６０ 摩拉*20000\n", "原石\n*60", "甲*1.5",
                     "甲" * 40 + "*1", "甲*1乙*2", "甲*1.2.乙*3", "甲²*1"):
            with self.subTest(text=text):
                self.assertTrue(_complete_reward_quantities(text))
        for text in ("", "甲" * 41 + "*1", "配方：纯洁之水*1", "甲*1.", "甲*²",
                     "原石*60，见证古国往事。", "原石*1\n奇特的零件"):
            with self.subTest(text=text):
                self.assertFalse(_complete_reward_quantities(text))

    def test_differential_against_original_on_bounded_strings(self):
        original = re.compile(r"(?:\s*[^\d*×\n。！？；：:]{1,40}\s*[*×]\s*\d+(?:\.\d+)?\s*)+")
        randomizer = random.Random(7)
        for _ in range(5000):
            text = "".join(randomizer.choice("甲乙  \n\t\r\u00a0*×12.，：²３")
                           for _ in range(randomizer.randrange(24)))
            self.assertEqual(_complete_reward_quantities(text), bool(original.fullmatch(text)), repr(text))

    def test_two_actual_reward_blocks_finish_and_remain_admitted(self):
        rows = read(Path(__file__).parent / "fixtures/reward-quantity-regressions.json")
        for row in rows:
            self.assertFalse(_complete_reward_quantities(row["text"].strip()))
            adapted, chunks = candidates(clean({"rich_text": row["text"]}, title=row["section_title"]))
            self.assertFalse(any(d["reason"] == "explicit_reward_quantity" for d in adapted["decisions"]))
            self.assertIn("*", "".join(c["body_text"] for c in chunks))

    def test_scope_prefix_and_mixed_prose(self):
        for title, text, excluded in (("奖励", "原石*60", True), ("商店", "原石×60 摩拉*20", True),
                                     ("故事", "达成奖励： 原石*60", True),
                                     ("故事", "原石*60", False),
                                     ("奖励", "先王赠予原石*60，见证往事。", False)):
            with self.subTest(title=title, text=text):
                adapted, _ = candidates(clean({"rich_text": "<p>" + text + "</p>"}, title=title))
                self.assertEqual(any(d["reason"] == "explicit_reward_quantity" for d in adapted["decisions"]), excluded)

    def test_long_rejections_have_a_process_timeout(self):
        script = """
from genshin_corpus.chunking.structure import _complete_reward_quantities
assert not _complete_reward_quantities(('甲 *1\\n ' * 20000) + '缺数量')
assert not _complete_reward_quantities(' ' * 100000 + '缺数量')
"""
        subprocess.run([sys.executable, "-B", "-c", script], check=True, timeout=5)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.builder = test_build_runtime.ChunkWorkerTests()
        self.builder.setUp()
        self.root = self.builder.root

    def tearDown(self):
        self.builder.tearDown()

    def test_shared_merge_and_independent_export_validation(self):
        self.builder.run_build("baseline")
        baseline = self.root / "baseline"
        manifest, state = read(baseline / "manifest.json"), read(baseline / "build-state.json")
        selection = manifest["selection"]
        completed, shards, origins = {}, {}, []
        for index, entry in enumerate(manifest["sources"]):
            source_entry = {key: value for key, value in entry.items() if key not in {"before_sha256", "after_sha256"}}
            path, receipt = _checked_source_receipt(baseline, source_entry, state["binding"])
            cid = entry["content_id"]
            completed[cid], shards[cid] = receipt["summary"], path.parent
            origins.append({"content_id": cid, "observation_id": entry["observation_id"],
                            "disposition": "reused" if index == 0 else "rebuilt", "files": receipt["files"]})
        output = self.root / "recovered"
        output.mkdir()
        _, names = _write_exports(output, selection, completed, shards, [ChunkProfile()], time.monotonic())
        for name in names:
            if name != "report.json":
                self.assertEqual(sha(output / name), sha(baseline / name))
        (output / "record-origins.jsonl").write_text("".join(json.dumps(origin) + "\n" for origin in origins), encoding="utf-8")
        names.append("record-origins.jsonl")
        final = {**manifest, "recovery": {"record_origins": "record-origins.jsonl", "reused_records": 1, "rebuilt_records": 1},
                 "artifacts": {name: sha(output / name) for name in names}}
        (output / "manifest.json").write_text(json.dumps(final), encoding="utf-8")
        # Historical shards are unavailable. Local exports still validate fully.
        baseline.rename(self.root / "unavailable-history")
        self.assertEqual(validate_exports(output)["status"], "PASS")
        self.assertEqual(validate_exports(output, full=True)["status"], "PASS")
        chunk_path = output / "chunks-balanced.jsonl"
        original_bytes = chunk_path.read_bytes()
        changed = original_bytes.replace(b'"body_text":', b'"altered_field":', 1)
        chunk_path.write_bytes(changed)
        final["artifacts"]["chunks-balanced.jsonl"] = sha(chunk_path)
        (output / "manifest.json").write_text(json.dumps(final), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Merged shard bytes/order mismatch"):
            validate_exports(output)
        chunk_path.write_bytes(original_bytes)
        final["artifacts"]["chunks-balanced.jsonl"] = sha(chunk_path)
        (output / "manifest.json").write_text(json.dumps(final), encoding="utf-8")
        (output / "chunks-balanced.jsonl").write_bytes(b"broken")
        with self.assertRaisesRegex(ValueError, "artifact mismatch"):
            validate_exports(output)

    def test_receipt_binding_identity_and_file_set_are_strict(self):
        self.builder.run_build("baseline")
        root = self.root / "baseline"
        manifest = read(root / "manifest.json")
        entry = {key: value for key, value in manifest["sources"][0].items()
                 if key not in {"before_sha256", "after_sha256"}}
        with self.assertRaisesRegex(ValueError, "different inputs"):
            _checked_source_receipt(root, entry, "different")
        with self.assertRaisesRegex(ValueError, "source mismatch"):
            _checked_source_receipt(root, {**entry, "content_id": "wrong"}, manifest["build_binding"])

    def test_record_failure_names_source_and_preserves_cause(self):
        from genshin_corpus.chunking import pilot
        output = self.root / "failure"
        entry = {"content_id": "one", "observation_id": "a" * 64}
        with patch.object(pilot, "_chunk_worker", (None, output, None, None, None, None)), \
             patch.object(pilot, "_build_chunk_record", side_effect=ValueError("specific failure")):
            with self.assertRaisesRegex(RuntimeError, "Chunk record one") as error:
                pilot._chunk_record_job(entry)
        self.assertIsInstance(error.exception.__cause__, ValueError)

    def test_admission_comparison_rejects_a_changed_decision(self):
        from genshin_corpus.chunking import adapt_record
        record = clean({"rich_text": "<p>达成奖励： 原石*60</p>"})
        adapted = adapt_record(record)
        shard = self.root / "admission"
        shard.mkdir()
        path = shard / "admission.jsonl"
        path.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in adapted["decisions"]), encoding="utf-8")
        self.assertEqual(_check_admission(record, shard), 1)
        decision = next(d for d in adapted["decisions"] if d["reason"] == "explicit_reward_quantity")
        decision.update(decision="include", reason="conservative_default")
        path.write_text(json.dumps(decision, ensure_ascii=False) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Reward admission changed"):
            _check_admission(record, shard)


if __name__ == "__main__":
    unittest.main()
