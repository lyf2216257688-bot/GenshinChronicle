import json
import os
from pathlib import Path
import subprocess
import unittest

from genshin_corpus.chunking.snapshot_build import build_snapshot as chunks
from genshin_corpus.clean.pipeline import build_clean_run
from genshin_corpus.clean.snapshot_build import build_snapshot as clean
from genshin_corpus.evidence_io import read, sha, write
from genshin_corpus.indexing.snapshot_inputs import build_inputs
from genshin_corpus.retrieval.clean_based.query_v2 import ResearchQuery
from tests.clean import test_snapshot_diff as fixtures


@unittest.skipUnless(os.getenv("GC_RUN_SNAPSHOT_GPU_TEST") == "1", "Explicit local GPU and Whoosh acceptance")
class SnapshotReleaseAcceptance(unittest.TestCase):
    def test_three_indexes_query_order_and_versioned_evidence(self):
        from tempfile import TemporaryDirectory
        from tokenizers import Tokenizer
        from tokenizers.models import WordLevel
        from tokenizers.pre_tokenizers import WhitespaceSplit

        workspace = TemporaryDirectory(prefix="gc-snapshot-")
        self.addCleanup(workspace.cleanup)
        root = Path(workspace.name).resolve()
        repo = Path.cwd()
        fixture = fixtures.SnapshotDiffTests()
        fixture.root = root
        first = fixture.collect("first", {10: [1], 20: [2, 3]})
        legacy = build_clean_run(raw_run_root=first, output_root=root / "legacy", clean_run_id="base", workers=1)
        self.assertEqual(legacy["status"], "complete")
        prior_clean = root / "legacy/mihoyo_obc/zh-cn/base/metadata/manifest.json"
        old = fixture.collect("old", {10: [1], 20: [2, 3]})
        current = fixture.collect("current", {10: [5, 1], 20: [2, 7]}, changed_detail=2)
        token = root / "tokenizer.json"
        tokenizer = Tokenizer(WordLevel(vocab={"[UNK]": 0}, unk_token="[UNK]"))
        tokenizer.pre_tokenizer = WhitespaceSplit()
        tokenizer.save(str(token))
        rules = root / "rules.json"
        write(rules, {"profiles": [{"name": "balanced", "micro_target": 240, "micro_hard": 384,
                                   "macro_target": 720, "macro_hard": 1024}], "bge_document_token_cap": 1792})
        config = read(repo / "config/clean-retrieval-v1.json")
        module = "genshin_corpus.retrieval.clean_based.snapshot"

        def invoke(env, command, **options):
            args = [str(repo / config[env + "_python"]), "-m", module, command]
            for key, value in options.items():
                flag = "--" + key.replace("_", "-")
                args.extend([flag] if value is True else [flag, str(value)])
            result = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace", timeout=240,
                                    env={**os.environ, "PYTHONPATH": str(repo / "src"), "PYTHONIOENCODING": "utf-8"})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        def build(name, raw, preceding, previous_clean, previous_arm=None, *, mode):
            arm = root / name
            clean(raw, preceding, previous_clean, arm / "clean", mode=mode)
            before = {"previous": previous_arm / "chunk/manifest.json"} if previous_arm else {}
            clean_path = arm / "clean/manifest.json"
            chunks(clean_path, rules, (token, token), arm / "chunk", mode=mode, **before)
            inputs = arm / "inputs/manifest.json"
            build_inputs(arm / "chunk/manifest.json", inputs.parent)
            for channel, command, environment in (("vectors", "build-vectors", "dense"), ("bm25f", "build-bm25f", "lexical")):
                prior = {"previous": previous_arm / channel / "manifest.json"} if previous_arm else {}
                options = {"sparse": True} if channel == "vectors" else {}
                invoke(environment, command, input=inputs, output=arm / channel, **prior, **options)
            invoke("dense", "package", input=inputs, vectors=arm / "vectors/manifest.json",
                   bm25f=arm / "bm25f/manifest.json", output=arm / "release.json")
            return arm

        initial = build("old", old, first, prior_clean, mode="incremental")
        old_manifest = initial / "clean/manifest.json"
        previous_sha = sha(initial / "release.json")
        inc = build("inc", current, old, old_manifest, initial, mode="incremental")
        full = build("full", current, old, old_manifest, mode="full")
        self.assertEqual(sha(initial / "release.json"), previous_sha)
        vectors = read(inc / "vectors/manifest.json")
        self.assertEqual(vectors["encoded_keys"], {"dense": 0, "sparse": 0})
        self.assertEqual(vectors["reused_keys"], {"dense": 1, "sparse": 1})
        self.assertGreater(read(inc / "bm25f/manifest.json")["reused_buckets"], 0)
        self.assertGreater(read(inc / "bm25f/manifest.json")["built_buckets"], 0)
        query = root / "query.json"
        question = "旧国工匠的故事是什么？"
        write(query, ResearchQuery("s", "t", "q", *([question] * 4), combination="S2").value())
        questions = root / "questions.json"
        write(questions, [question, "同文", "语料里没有的试验词"])
        comparisons = {"left": inc / "release.json", "right": full / "release.json"}
        invoke("dense", "compare", channel="vectors", output=root / "vectors-parity.json", **comparisons)
        invoke("lexical", "compare", channel="bm25f", output=root / "bm25f-parity.json", queries=questions, **comparisons)
        self.assertEqual(read(root / "vectors-parity.json")["result"], {"dense": 1, "sparse": 1})
        self.assertGreater(read(root / "bm25f-parity.json")["result"]["scores"], 0)
        for arm in (initial, inc, full):
            invoke("dense", "query", release=arm / "release.json", output=arm / "query-output", query_json=query, rerank=True)
        for file in ("candidates.json", "first-evidence.json"):
            self.assertEqual(read(inc / "query-output" / file), read(full / "query-output" / file))
        for arm in (inc, full):
            ranked = read(arm / "query-output/ranked.json")
            if arm == inc:
                order = [candidate["chunk_id"] for candidate in ranked["candidates"]]
            else:
                self.assertEqual(order, [candidate["chunk_id"] for candidate in ranked["candidates"]])
        result = read(inc / "query-output/candidates.json")
        self.assertEqual({hit["channel"] for hit in result["raw_hits"]}, {"dense", "bm25f", "sparse"})
        sources = {c["source"]["source_item"]["content_id"]: c["source"]["raw_observations"] for c in result["candidates"]}
        self.assertEqual(set(sources), {"1", "2", "7"})
        self.assertEqual(sources["1"]["current"]["run_id"], "current")
        self.assertEqual(sources["1"]["effective"]["run_id"], "first")
        self.assertEqual(sources["2"]["effective"]["run_id"], "current")
        before = read(initial / "query-output/candidates.json")
        self.assertIn("3", {row["source"]["source_item"]["content_id"] for row in before["candidates"]})
        for arm in (inc, full):
            self.assertEqual(read(arm / "query-output/run.json")["remote_calls"], 0)


if __name__ == "__main__":
    unittest.main()
