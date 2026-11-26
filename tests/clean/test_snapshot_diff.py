import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from importlib.util import find_spec

from genshin_corpus.clean.models import source_json
from genshin_corpus.clean.pipeline import build_clean_run
from genshin_corpus.clean.snapshot_build import build_snapshot, validate_snapshot
from genshin_corpus.clean.snapshot_diff import compare_snapshots, main, ordered_value
from genshin_corpus.chunking import snapshot_build as chunk_snapshot
from genshin_corpus.chunking.pilot import run_pilot
from genshin_corpus.chunking.snapshot_ranges import register_flat
from genshin_corpus.collector import Collector, CollectorConfig
from genshin_corpus.collector.access import SourceAccess
from genshin_corpus.collector.transport import Response
from genshin_corpus.evidence_io import write
from genshin_corpus.indexing.snapshot_inputs import SnapshotInput, build_inputs
from tests.collector.test_collector import channel, discovery


class FixtureTransport:
    def __init__(self, listings, *, name="角色", include_third=True, changed_detail=None, dialogue=False):
        self.listings = listings
        self.map = [channel(10, name="任务"), channel(20, name=name, parent_id=10)]
        if include_third:
            self.map.append(channel(30, name="武器"))
        self.changed_detail = changed_detail
        self.dialogue = dialogue

    def get(self, url, *, params=(), headers=()):
        if "channel_id" in params:
            key = int(params["channel_id"])
            rows = [{"content_id": item, "title": "同文"} for item in self.listings.get(key, ())]
            body = discovery(channel(key, rows=rows))
        elif "entry_page_id" in params:
            key = int(params["entry_page_id"])
            prose = {"rich_text": "<p>旧国工匠的故事。</p>"}
            modules = [{"name": "正文", "components": [{"component_id": "collapse_panel", "data": json.dumps(prose)}]}] if key != 5 else []
            if self.dialogue and key == 1:
                dialogue = {"root_id": "root", "contents": {node: {"dialogue": node} for node in ("root", "b", "a")},
                            "child_ids": {"root": []}}
                modules[0]["components"].append({"component_id": "interactive_dialogue", "data": json.dumps(dialogue)})
            body = json.dumps({"retcode": 0, "data": {"page": {"id": key, "name": "同文", "modules": modules,
                              "menus": ["别的结构"] if key == self.changed_detail else []}}}).encode("utf-8")
        else:
            body = discovery(*self.map)
        return Response(200, body, {}, url)


class SnapshotDiffTests(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)

    def collect(self, run_id, listings, *, detail_limit=None, locale="zh-cn", **options):
        collector = Collector(CollectorConfig(output_root=self.root / "raw", run_id=run_id, locale=locale, max_retries=0),
                              FixtureTransport(listings, **options), access=SourceAccess(self.root / "access"))
        collector.run(detail_limit=detail_limit)
        return collector.store.root

    def test_distinguishes_locations_content_structure_and_membership(self):
        old = self.collect("old", {10: [1, 5], 20: [2, 3], 30: [3, 4]})
        new = self.collect("new", {10: [7, 1], 20: [2, 3], 30: [4]}, changed_detail=2)
        plan = compare_snapshots(old, new)
        rows = {row["source_item"]["content_id"]: row for row in plan["records"]}
        self.assertEqual(plan["status"], "complete")
        self.assertEqual(plan["counts"], {"unchanged": 2, "modified": 2, "removed": 1, "added": 1})
        self.assertEqual(rows["1"]["status"], "unchanged")
        self.assertTrue(rows["1"]["locator_changed"])
        self.assertNotEqual(rows["1"]["previous_observation"]["observation_id"], rows["1"]["current_observation"]["observation_id"])
        self.assertEqual(rows["1"]["previous_observation"]["consumed_source_sha256"], rows["1"]["current_observation"]["consumed_source_sha256"])
        self.assertEqual(rows["2"]["changed_parts"], ["detail"])
        self.assertEqual(rows["3"]["changed_parts"], ["memberships", "listings", "channel_structure"])
        self.assertEqual(rows["5"]["status"], "removed")
        self.assertIsNone(rows["5"]["current_observation"])
        self.assertEqual(rows["7"]["status"], "added")

    def test_channel_rename_recomputes_affected_sources_and_topology_loss_blocks_removal(self):
        old = self.collect("old", {10: [1], 20: [2], 30: [3]})
        new = self.collect("new", {10: [1], 20: [2]}, name="任务角色", include_third=False)
        plan = compare_snapshots(old, new)
        self.assertEqual(plan["status"], "coverage_review_required")
        self.assertEqual(plan["changed_channels"], ["30"])
        self.assertEqual([row["status"] for row in plan["records"]], ["unchanged", "modified", "unresolved"])
        self.assertEqual(plan["records"][1]["changed_parts"], ["channel_structure"])

    def test_scoped_mismatched_and_corrupt_archives_do_not_yield_a_diff(self):
        old = self.collect("old", {10: [1], 20: [2]})
        scoped = self.collect("scoped", {10: [1], 20: [2]}, detail_limit=1)
        foreign = self.collect("foreign", {10: [1], 20: [2]}, locale="en-us")
        for root in (scoped, foreign):
            with self.subTest(root=root), self.assertRaises(ValueError):
                compare_snapshots(old, root)
        new = self.collect("new", {10: [1], 20: [2]})
        manifest = json.loads((new / "metadata/manifest.json").read_bytes())
        (new / manifest["paths"]["details:2"]["raw"]).write_bytes(b"bad body")
        output = self.root / "change.json"
        with self.assertRaisesRegex(ValueError, "RAW_CHECKSUM_MISMATCH"):
            main(["--previous", str(old), "--current", str(new), "--output", str(output)])
        self.assertFalse(output.exists())

    def test_discovery_equivalence_preserves_source_value_order_and_type(self):
        self.assertNotEqual(ordered_value(source_json('{"a":1.0,"b":true}')),
                            ordered_value(source_json('{"b":true,"a":1.0}')))
        self.assertNotEqual(ordered_value(source_json('[1.0]')), ordered_value(source_json('[1.00]')))
        self.assertNotEqual(ordered_value(source_json('[2]')), ordered_value(source_json('["2"]')))

    def test_incremental_clean_and_independent_full_use_the_same_effective_sources(self):
        old = self.collect("old", {10: [1], 20: [2, 3]})
        old_clean = self.root / "old-clean"
        build_clean_run(raw_run_root=old, output_root=old_clean, clean_run_id="baseline", workers=1)
        old_manifest = old_clean / "mihoyo_obc/zh-cn/baseline/metadata/manifest.json"
        new = self.collect("new", {10: [4, 1], 20: [2]}, changed_detail=2)
        incremental_root, full_root = self.root / "incremental", self.root / "full"
        inc = build_snapshot(new, old, old_manifest, incremental_root, mode="incremental")
        full = build_snapshot(new, old, old_manifest, full_root, mode="full")
        self.assertEqual(inc["counts"], {"reused": 1, "built": 2})
        self.assertEqual(full["counts"], {"built": 3})
        self.assertEqual(inc["logical_id"], full["logical_id"])
        self.assertEqual(validate_snapshot(incremental_root / "manifest.json"), inc)
        self.assertEqual(validate_snapshot(full_root / "manifest.json"), full)
        self.assertEqual(inc["records"][0]["effective_observation"]["run_id"], "old")
        self.assertEqual(inc["records"][0]["current_observation"]["run_id"], "new")
        self.assertEqual(inc["records"][1]["effective_observation"]["run_id"], "new")
        self.assertEqual(inc, build_snapshot(new, old, old_manifest, incremental_root, mode="incremental"))

        third = self.collect("third", {10: [1, 4, 5], 20: [2]}, changed_detail=2)
        third_root = self.root / "third-clean"
        result = build_snapshot(third, new, incremental_root / "manifest.json", third_root, mode="incremental")
        self.assertEqual(result["counts"], {"reused": 3, "built": 1})
        self.assertEqual(result["records"][0]["record"], inc["records"][0]["record"])
        self.assertEqual(result["records"][0]["effective_observation"]["run_id"], "old")
        self.assertEqual(validate_snapshot(third_root / "manifest.json"), result)

    @unittest.skipUnless(find_spec("tokenizers"), "Requires the existing local Chunk tokenizer runtime")
    def test_chunk_snapshot_whole_source_reuse_and_independent_full_equivalence(self):
        from tokenizers import Tokenizer
        from tokenizers.models import WordLevel
        from tokenizers.pre_tokenizers import WhitespaceSplit

        encoder = Tokenizer(WordLevel(vocab={"[UNK]": 0}, unk_token="[UNK]"))
        encoder.pre_tokenizer = WhitespaceSplit()
        token = self.root / "tokenizer.json"
        encoder.save(str(token))
        rules = self.root / "selection.json"
        write(rules, {"profiles": [{"name": "balanced", "micro_target": 240, "micro_hard": 384,
                                   "macro_target": 720, "macro_hard": 1024}], "bge_document_token_cap": 1792,
                      "content_ids": ["1", "2"], "review_content_ids": []})
        old = self.collect("old", {10: [1], 20: [2]})
        old_clean = self.root / "old-clean"
        build_clean_run(raw_run_root=old, output_root=old_clean, clean_run_id="baseline", workers=1)
        old_manifest = old_clean / "mihoyo_obc/zh-cn/baseline/metadata/manifest.json"
        old_chunk = self.root / "old-chunks"
        run_pilot(old_manifest, rules, token, token, old_chunk, workers=1)
        baseline = self.root / "baseline.json"
        write(baseline, register_flat(old_chunk / "manifest.json"))
        new = self.collect("new", {10: [1, 5], 20: [2]}, changed_detail=2)
        clean = self.root / "new-clean"
        build_snapshot(new, old, old_manifest, clean, mode="incremental")
        inc_root, full_root = self.root / "new-chunks", self.root / "all-chunks"
        inc = chunk_snapshot.build_snapshot(clean / "manifest.json", rules, (token, token), inc_root,
                                            previous=baseline, mode="incremental")
        full = chunk_snapshot.build_snapshot(clean / "manifest.json", rules, (token, token), full_root, mode="full")
        self.assertEqual(inc["counts"], {"reused": 1, "built": 2})
        self.assertEqual(inc["logical_id"], full["logical_id"])
        self.assertEqual(inc, chunk_snapshot.validate_snapshot_manifest(inc_root / "manifest.json"))
        self.assertEqual(full, chunk_snapshot.validate_snapshot_manifest(full_root / "manifest.json"))
        self.assertIsNone(inc["records"]["5"]["ranges"]["chunks-balanced.jsonl"])
        self.assertEqual(Path(inc["records"]["1"]["ranges"]["chunks-balanced.jsonl"]["path"]).parent, old_chunk)

        inc_input, full_input = self.root / "inc-input", self.root / "full-input"
        inc_bundle = build_inputs(inc_root / "manifest.json", inc_input)
        full_bundle = build_inputs(full_root / "manifest.json", full_input)
        self.assertEqual(inc_bundle["input_bundle_id"], full_bundle["input_bundle_id"])
        self.assertEqual(inc_bundle["counts"], {"documents": 2, "micro": 2, "macro": 2})
        self.assertEqual(inc_bundle["sources"], 3)
        loaded = list(SnapshotInput(inc_input / "manifest.json").iter_documents())
        self.assertEqual(loaded, list(SnapshotInput(full_input / "manifest.json").iter_documents()))
        self.assertEqual(loaded[0]["current_raw"]["run_id"], "new")
        self.assertEqual(loaded[0]["effective_raw"]["run_id"], "old")
        self.assertEqual(loaded[0]["projected"]["dense_text"], loaded[1]["projected"]["dense_text"])
        self.assertNotEqual(loaded[0]["row"]["source_item"], loaded[1]["row"]["source_item"])


if __name__ == "__main__":
    unittest.main()
