"""Small offline tests of source identity, fusion and coverage protection."""
from copy import deepcopy
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from genshin_corpus.evidence_io import read, write
from genshin_corpus.indexing import ChunkInputSource, prepare_inputs
from genshin_corpus.retrieval.clean_based.core import QuerySpec, covered_by, fuse_candidates
from genshin_corpus.retrieval.clean_based.source import FileBinding, InputReader
from genshin_corpus.retrieval.clean_based.runtime import PreparedRetriever, validate_output
from tests.indexing.test_indexing import build_source


def profile(**changes):
    return {"combination": "S0", "fusion": "f1_max", "depth": 50, "candidate_limit": 100,
            "rrf_k": 60, "channel_weights": {"bm25f": .5, "dense": .5},
            "structural_cleanup": True, **changes}


def document(cid, number, start=0, end=10, **part_changes):
    ref = {"observation_id": "obs", "block_id": "block", "value_pointer": "/value",
           "char_start": start, "char_end": end}
    part = {"clean_ref": ref, "text": "原文"[0:1] * (end - start), **part_changes}
    chunk = {"chunk_id": cid, "parent_id": "parent", "observation_id": "obs", "context": {},
             "clean_refs": [ref], "parts": [part], "context_sources": [], "alias_clean_refs": []}
    return {"input_row": number, "chunk": chunk, "row": {"granularities": ["micro", "macro"]}}


def hit(cid, number, channel="dense", granularity="micro", rank=1, **changes):
    return {"chunk_id": cid, "input_row": number, "channel": channel, "granularity": granularity,
            "rank": rank, "raw_score": .8, "query_id": "original", "question_id": "operator",
            "representation_id": channel + granularity, **changes}


class FusionTests(unittest.TestCase):
    def fuse(self, hits, docs, **changes):
        return fuse_candidates(QuerySpec("问题"), hits, docs, profile(**changes), input_bundle_id="bundle")

    def test_query_seam_rejects_unimplemented_enhancement(self):
        self.assertEqual(QuerySpec("问题").text, "问题")
        for changes in ({"kind": "step_back"}, {"round": 1}, {"retrieval_text": "改写"}, {"parent_query_ids": ("p",)}):
            with self.assertRaises(ValueError):
                QuerySpec("问题", **changes)

    def test_shared_chunk_has_one_output_and_no_second_granularity_bonus(self):
        docs = {"a": document("a", 0)}
        one = self.fuse([hit("a", 0)], docs)
        two = self.fuse([hit("a", 0), hit("a", 0, granularity="macro")], docs)
        self.assertEqual(two["counts"]["selected"], 1)
        self.assertEqual(one["candidates"][0]["fusion_score"], two["candidates"][0]["fusion_score"])
        self.assertEqual(len(two["candidates"][0]["hits"]), 2)

    def test_replayed_identical_hit_is_idempotent_but_conflict_fails(self):
        docs, h = {"a": document("a", 0)}, hit("a", 0)
        result = self.fuse([h, deepcopy(h)], docs)
        self.assertEqual(result["counts"]["distinct_hits"], 1)
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.fuse([h, {**h, "raw_score": .7}], docs)

    def test_channel_consensus_and_tie_order_are_deterministic(self):
        docs = {"a": document("a", 1), "b": document("b", 0, 10, 20)}
        hits = [hit("a", 1, rank=1), hit("b", 0, rank=2), hit("b", 0, channel="bm25f")]
        result = self.fuse(hits, docs)
        self.assertEqual(result["candidates"][0]["chunk_id"], "b")
        tied = self.fuse([hit("a", 1), hit("b", 0, granularity="macro")], docs)
        self.assertEqual([r["chunk_id"] for r in tied["candidates"]], ["b", "a"])

    def test_switchable_cross_fusion_and_no_intermediate_truncation(self):
        docs = {"a": document("a", 0), "b": document("b", 1, 10, 20)}
        hits = [hit("a", 0), hit("b", 1, rank=2), hit("a", 0, granularity="macro")]
        for fusion in ("f0", "f1_max", "f1_rrf"):
            result = self.fuse(hits, docs, fusion=fusion, candidate_limit=1)
            self.assertEqual(result["counts"]["unique_chunks"], 2)
            self.assertEqual(len(result["dispositions"]), 2)
            self.assertEqual(result["dispositions"][1]["disposition"], "outside_candidate_limit")

    def test_s2_budget_is_explicit_and_sparse_does_not_replace_dense(self):
        docs = {"a": document("a", 0), "b": document("b", 1, 10, 20)}
        hits = [hit("a", 0), hit("b", 1, channel="bm25f"), hit("b", 1, channel="sparse")]
        result = self.fuse(hits, docs, combination="S2", channel_weights={"dense": .5, "bm25f": .25, "sparse": .25})
        self.assertEqual(result["candidates"][0]["chunk_id"], "a")  # Equal mass; input-row tie break.

    def test_foreign_query_route_rank_and_nonfinite_score_fail(self):
        docs = {"a": document("a", 0)}
        for changes in ({"query_id": "other"}, {"input_row": 3}, {"granularity": "parent"},
                        {"rank": 2}, {"raw_score": float("nan")}):
            with self.assertRaises(ValueError):
                self.fuse([hit("a", 0, **changes)], docs)

    def test_containment_defers_and_refills_without_deleting_source(self):
        docs = {"a": document("a", 0, 0, 20), "b": document("b", 1, 3, 8), "c": document("c", 2, 21, 25)}
        hits = [hit(cid, i, rank=i + 1) for i, cid in enumerate(docs)]
        result = self.fuse(hits, docs, candidate_limit=2)
        self.assertEqual([r["chunk_id"] for r in result["candidates"]], ["a", "c"])
        self.assertEqual(result["dispositions"][-1]["deferred_by"], "a")
        refilled = self.fuse(hits, docs)
        self.assertEqual(refilled["candidates"][-1]["disposition"], "selected_deferred_refill")

    def test_partial_overlap_same_long_document_and_other_occurrence_survive(self):
        a, b, c = document("a", 0, 0, 10), document("b", 1, 8, 18), document("c", 2, 0, 10)
        c["chunk"]["parts"][0]["clean_ref"]["block_id"] = "other-block"
        result = self.fuse([hit(cid, i, rank=i + 1) for i, cid in enumerate(("a", "b", "c"))], {"a": a, "b": b, "c": c})
        self.assertEqual(result["counts"]["deferred"], 0)
        self.assertEqual(result["candidates"][1]["overlaps"][0]["body_codepoints"], 2)

    def test_conditions_branches_context_aliases_and_unknown_metadata_protect(self):
        a = document("a", 0, 0, 20)["chunk"]
        for changes in ({"condition_status": "unresolved"}, {"branch_context": ["choice-A"]},
                        {"condition_sources": [{"text": "任务完成前"}]}, {"unknown_source_marker": "x"}):
            b = document("b", 1, 3, 8, **changes)["chunk"]
            self.assertFalse(covered_by(b, a))
        b = document("b", 1, 3, 8)["chunk"]
        b["context_sources"] = [{"text": "不可省略的提问"}]
        self.assertFalse(covered_by(b, a))
        b["context_sources"] = []
        b["alias_clean_refs"] = [b["clean_refs"][0]]
        self.assertFalse(covered_by(b, a))


class InputReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.network = patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)
        source = ChunkInputSource(build_source(self.root, long_story=True))
        self.path = self.root / "input/manifest.json"
        prepare_inputs(source, self.path.parent)

    def test_verified_random_access_and_all_readback_kinds(self):
        with InputReader(self.path) as reader:
            for number in range(len(reader.offsets)):
                doc = reader.document(number)
                row = doc["row"]
                self.assertEqual(reader.read_parent(row)["parent_id"], row["parent_id"])
                self.assertEqual(reader.read_clean(row)["observation_id"], row["observation_id"])
                self.assertIsNotNone(reader.read_natural_parent(row))
                if row["graph_locator"]:
                    self.assertIsNotNone(reader.read_graph(row))

    def test_modification_after_preparation_invalidates_session(self):
        with InputReader(self.path) as reader:
            path = self.path.parent / "inputs.jsonl"
            with path.open("ab") as handle:
                handle.write(b" ")
            with self.assertRaisesRegex(ValueError, "changed"):
                reader.row(0)

    def test_unbound_hit_or_locator_is_rejected(self):
        with InputReader(self.path) as reader:
            with self.assertRaises(ValueError):
                reader.document(0, "other-chunk")
            row = reader.row(0)
            row["chunk_locator"]["offset"] += 1
            with self.assertRaises(ValueError):
                reader.read_chunk(row)

    def test_corrupt_artifact_is_rejected_before_ready(self):
        with (self.path.parent / "macro.jsonl").open("ab") as handle:
            handle.write(b" ")
        with self.assertRaisesRegex(ValueError, "checksum"):
            InputReader(self.path)


class RuntimeBoundaryTests(unittest.TestCase):
    def prepared(self, *, foreign=False, fail=False):
        obj = PreparedRetriever.__new__(PreparedRetriever)
        obj.config, obj.binding = profile(), FileBinding()
        doc = document("a", 0)
        obj.reader = SimpleNamespace(bundle_id="bundle", binding=FileBinding(), document=lambda n, cid: doc)
        obj.workers = {}
        for channel in ("dense", "bm25f"):
            row = hit("a", 0, channel=channel, index_mapping={"granularities": ["micro", "macro"]})
            if foreign and channel == "dense":
                row["representation_id"] = "wrong-space"
            def search(query, depth, verify_reference=False, row=row, channel=channel):
                if fail and channel == "dense":
                    raise ValueError("explicit channel failure")
                return {"hits": [row], "elapsed_seconds": 0., "resources": {}, "reference_check": "not_requested"}
            obj.workers[channel] = SimpleNamespace(search=search,
                ready={"representations": {"micro": channel + "micro", "macro": channel + "macro"}})
        return obj

    def test_worker_representation_must_match_prepared_binding(self):
        result = self.prepared().search(QuerySpec("问题"))
        self.assertEqual(result["counts"]["selected"], 1)
        with self.assertRaisesRegex(ValueError, "foreign route"):
            self.prepared(foreign=True).search(QuerySpec("问题"))

    def test_channel_failure_does_not_implicitly_select_another_combination(self):
        with self.assertRaisesRegex(ValueError, "channel failure"):
            self.prepared(fail=True).search(QuerySpec("问题"))

    def test_outputs_cannot_be_written_into_selected_assets(self):
        with TemporaryDirectory() as name:
            repo = Path(name)
            path = repo / "indexes/input/manifest.json"
            write(path, {"source_manifests": {"chunk": str(repo / "chunks/manifest.json"),
                                             "clean": str(repo / "clean/metadata/manifest.json")}})
            indexes = {"dense": (repo / "indexes/dense/manifest.json", {"input_manifest": str(path)}),
                       "bm25f": (repo / "indexes/bm25f/manifest.json", {"input_manifest": str(path)})}
            selection = {"dense": {"weights_dir": "models/bge"}}
            with patch("genshin_corpus.retrieval.clean_based.runtime.selected", return_value=(selection, indexes)):
                for protected in ("indexes/new-output", "chunks/results", "clean/results", "models/bge/results"):
                    with self.assertRaisesRegex(ValueError, "outside selected"):
                        validate_output(repo, {}, repo / protected)
                    self.assertFalse((repo / protected).exists())
                validate_output(repo, {}, repo / "review/safe-output")


if __name__ == "__main__":
    unittest.main()
