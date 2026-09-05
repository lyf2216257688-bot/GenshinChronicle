import gzip
import hashlib
import json
import shutil
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval.candidate_retrieval import (
    CandidateBundle,
    CandidateRetrievalError,
    build_dense_index,
    build_lexical_index,
    dense_candidates,
    dense_candidates_local,
    load_batch_candidate_retriever,
    _document_frequencies,
    hybrid_candidates,
    lexical_candidates,
    retrieve_candidates,
)
from genshin_corpus.retrieval.evidence_assembly import assemble_evidence_packet, EvidenceAssemblyConfig


class RagW2Tests(unittest.TestCase):
    def setUp(self):
        self.root = Path("data/retrieval/.rag-w2-test")
        if self.root.exists(): shutil.rmtree(self.root)
        (self.root / "ru/artifacts").mkdir(parents=True)
        units = []
        for i, text in enumerate(("阿贝多炼金术", "璃月港历史", "阿贝多画家")):
            units.append({
                "schema_version": "phase04-retrieval-unit-0.1", "unit_id": f"u{i}",
                "retrieval_visible_text": text, "source_order": [0, 0, 0, i],
                "source": {"canonical_address": {"source_identity_key":"s", "record_id":"r", "section_ordinal":0, "component_observation_key":"c", "component_ordinal":0, "canonical_unit_ordinal":i, "canonical_unit_kind":"rich_text", "parsed_json_pointer":f"/x/{i}"}, "lineage": {"raw_refs": []}, "record_context": {}, "provenance": {}, "content_role": {}},
                "content_type": "rich_text", "nested_selector": {}, "fragment_selector": {"kind":"whole"}, "structure": {}
            })
        body = gzip.compress(b"".join(canonical_json_bytes(u)+b"\n" for u in units), mtime=0)
        skip = gzip.compress(b"", mtime=0)
        failure = canonical_json_bytes({"status":"clear","failure_count":0})
        for rel, data in (("retrieval_units.jsonl.gz", body),("skip_ledger.jsonl.gz", skip)):
            (self.root / "ru/artifacts" / rel).write_bytes(data)
        (self.root / "ru/metadata").mkdir()
        (self.root / "ru/metadata/failure_ledger.json").write_bytes(failure)
        manifest = {"schema_version":"phase04-retrieval-unit-build-0.1","status":"complete","build_identity":"ru-build-1","canonical_input":{"canonical_run_id":"fixture","canonical_input_identity":"fixture","canonical_schema_version":"phase03-draft-0.1"},"artifacts":{
            "retrieval_units":{"path":"artifacts/retrieval_units.jsonl.gz","sha256":hashlib.sha256(body).hexdigest(),"byte_count":len(body),"row_count":3},
            "skip_ledger":{"path":"artifacts/skip_ledger.jsonl.gz","sha256":hashlib.sha256(skip).hexdigest(),"byte_count":len(skip),"row_count":0},
            "failure_ledger":{"path":"metadata/failure_ledger.json","sha256":hashlib.sha256(failure).hexdigest(),"byte_count":len(failure)} }}
        (self.root / "ru/metadata/manifest.json").write_bytes(canonical_json_bytes(manifest))
        self.ru_manifest = self.root / "ru/metadata/manifest.json"

    def tearDown(self):
        if self.root.exists(): shutil.rmtree(self.root)

    def test_lexical_and_dense_identity_isolated_and_deterministic(self):
        lm = build_lexical_index(self.ru_manifest, self.root/"lex")
        lm2 = build_lexical_index(self.ru_manifest, self.root/"lex2", analyzer_version="other")
        self.assertNotEqual(lm["arm_build_identity"], lm2["arm_build_identity"])
        dm = build_dense_index(self.ru_manifest, self.root/"dense", model_dir=self.root, vectors=np.array([[1,0],[0,1],[1,0]], dtype=np.float32))
        dm2 = build_dense_index(self.ru_manifest, self.root/"dense2", model_dir=self.root, vectors=np.array([[1,0],[0,1],[1,0]], dtype=np.float32), instruction="different")
        self.assertEqual(dm["arm_build_identity"], dm2["arm_build_identity"])
        self.assertEqual(lm["retrieval_unit_build_identity"], dm["retrieval_unit_build_identity"])

    def test_lexical_dense_rrf_and_assembly_handoff(self):
        lm = build_lexical_index(self.ru_manifest, self.root/"lex")
        lexical = lexical_candidates(self.root/"lex/metadata/manifest.json", "阿贝多", top_k=3)
        self.assertEqual(lexical[0]["unit_id"], "u2")
        dm = build_dense_index(self.ru_manifest, self.root/"dense", model_dir=self.root, vectors=np.array([[1,0],[0,1],[1,0]], dtype=np.float32))
        dense = dense_candidates(self.root/"dense/metadata/manifest.json", np.array([1,0], dtype=np.float32), top_k=3)
        self.assertEqual([x["unit_id"] for x in dense[:2]], ["u0", "u2"])
        hybrid = hybrid_candidates(lexical, dense, lexical_build_identity=lm["arm_build_identity"], dense_build_identity=dm["arm_build_identity"], top_k=3)
        self.assertEqual(hybrid[0]["retrieval"]["mode"], "hybrid")
        packet = assemble_evidence_packet(self.ru_manifest, hybrid, config=EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, per_block_chars=100, total_context_chars=1000))
        self.assertEqual(packet["evidence"][0]["members"][0]["unit_id"], hybrid[0]["unit_id"])
        self.assertEqual(packet["evidence"][0]["members"][0]["canonical_address"]["record_id"], "r")

    def test_candidate_bundle_preserves_current_window_rows_and_provenance(self):
        lm = build_lexical_index(self.ru_manifest, self.root / "lex")
        dm = build_dense_index(
            self.ru_manifest,
            self.root / "dense",
            model_dir=self.root,
            vectors=np.array([[1, 0], [0, 1], [1, 0]], dtype=np.float32),
        )
        lexical = lexical_candidates(self.root / "lex/metadata/manifest.json", "阿贝多", top_k=3)
        dense = dense_candidates(self.root / "dense/metadata/manifest.json", np.array([1, 0], dtype=np.float32), top_k=3)
        hybrid = hybrid_candidates(
            lexical,
            dense,
            lexical_build_identity=lm["arm_build_identity"],
            dense_build_identity=dm["arm_build_identity"],
            top_k=3,
        )
        bundle = CandidateBundle.from_current_windows({"lexical": lexical, "dense": dense, "hybrid": hybrid})
        original = bundle.audit_projection()
        lexical[0]["retrieval"]["score"] = -1.0
        self.assertEqual(bundle.audit_projection(), original)
        self.assertEqual(len(bundle.candidates_for("lexical")), len(lexical))
        self.assertEqual(bundle.candidates_for("hybrid")[0]["retrieval"]["components"], hybrid[0]["retrieval"]["components"])
        self.assertIn("arm_build_identities", bundle.candidates_for("hybrid")[0]["retrieval"])

    def test_batch_retriever_loads_each_arm_once_and_matches_public_candidates(self):
        lm = build_lexical_index(self.ru_manifest, self.root / "lex")
        dm = build_dense_index(
            self.ru_manifest,
            self.root / "dense",
            model_dir=self.root,
            vectors=np.array([[1, 0], [0, 1], [1, 0]], dtype=np.float32),
        )
        lexical_path = self.root / "lex/metadata/manifest.json"
        dense_path = self.root / "dense/metadata/manifest.json"
        query_vector = np.array([1, 0], dtype=np.float32)
        expected_lexical = lexical_candidates(lexical_path, "阿贝多", top_k=3)
        expected_dense = dense_candidates(dense_path, query_vector, top_k=3, query_instruction="指令")
        expected_hybrid = hybrid_candidates(
            expected_lexical,
            expected_dense,
            lexical_build_identity=lm["arm_build_identity"],
            dense_build_identity=dm["arm_build_identity"],
            top_k=3,
        )
        from genshin_corpus.retrieval import candidate_retrieval as module

        with patch.object(module, "_load_lexical", wraps=module._load_lexical) as load_lexical, patch.object(module, "_load_dense", wraps=module._load_dense) as load_dense:
            batch = load_batch_candidate_retriever(lexical_path, dense_path)
            with patch.object(module, "hybrid_candidates", wraps=module.hybrid_candidates) as fusion:
                actual = batch.candidates_for_query("阿贝多", query_vector, instruction="指令", top_k=3)

        self.assertEqual(load_lexical.call_count, 1)
        self.assertEqual(load_dense.call_count, 1)
        self.assertEqual(actual["lexical"], expected_lexical)
        self.assertEqual(actual["dense"], expected_dense)
        self.assertEqual(actual["hybrid"], expected_hybrid)
        self.assertIs(fusion.call_args.args[0], actual["lexical"])
        self.assertIs(fusion.call_args.args[1], actual["dense"])

    def test_batch_retriever_fails_closed_on_dense_artifact_integrity(self):
        build_lexical_index(self.ru_manifest, self.root / "lex")
        build_dense_index(
            self.ru_manifest,
            self.root / "dense",
            model_dir=self.root,
            vectors=np.array([[1, 0], [0, 1], [1, 0]], dtype=np.float32),
        )
        vector_path = self.root / "dense/artifacts/vectors.f32.npy"
        vector_path.write_bytes(vector_path.read_bytes() + b"changed")
        with self.assertRaisesRegex(CandidateRetrievalError, "SHA-256 mismatch"):
            load_batch_candidate_retriever(
                self.root / "lex/metadata/manifest.json",
                self.root / "dense/metadata/manifest.json",
            )

    def test_batch_retriever_validates_once_and_computes_each_arm_once_for_70_queries(self):
        build_lexical_index(self.ru_manifest, self.root / "lex")
        build_dense_index(
            self.ru_manifest,
            self.root / "dense",
            model_dir=self.root,
            vectors=np.array([[1, 0], [0, 1], [1, 0]], dtype=np.float32),
        )
        lexical_path = self.root / "lex/metadata/manifest.json"
        dense_path = self.root / "dense/metadata/manifest.json"
        from genshin_corpus.retrieval import candidate_retrieval as module

        with patch.object(module, "_load_lexical", wraps=module._load_lexical) as load_lexical, patch.object(module, "_load_dense", wraps=module._load_dense) as load_dense, patch.object(module, "_lexical_candidates_from_loaded", wraps=module._lexical_candidates_from_loaded) as compute_lexical, patch.object(module, "_dense_candidates_from_loaded", wraps=module._dense_candidates_from_loaded) as compute_dense:
            batch = load_batch_candidate_retriever(lexical_path, dense_path)
            for index in range(70):
                batch.candidates_for_query(
                    f"阿贝多 {index}",
                    np.array([1, 0], dtype=np.float32),
                    instruction="指令",
                )

        self.assertEqual(load_lexical.call_count, 1)
        self.assertEqual(load_dense.call_count, 1)
        self.assertEqual(compute_lexical.call_count, 70)
        self.assertEqual(compute_dense.call_count, 70)

    def test_dense_rejects_wrong_query_dimension(self):
        build_dense_index(self.ru_manifest, self.root/"dense", model_dir=self.root, vectors=np.array([[1,0],[0,1],[1,1]], dtype=np.float32))
        with self.assertRaises(CandidateRetrievalError):
            dense_candidates(self.root/"dense/metadata/manifest.json", np.array([1, 0, 0], dtype=np.float32))

    def test_bm25_df_counts_distinct_retrieval_units(self):
        rows = [{"tf": {"x": 1, "y": 1}}, {"tf": {"x": 2}}, {"tf": {"z": 1}}]
        self.assertEqual(_document_frequencies(rows, ["x", "y", "z"]), {"x": 2, "y": 1, "z": 1})

    def test_dense_local_binding_rejects_model_mismatch(self):
        (self.root / "model.safetensors").write_bytes(b"weights-a")
        build_dense_index(self.ru_manifest, self.root/"dense", model_dir=self.root, vectors=np.array([[1,0],[0,1],[1,0]], dtype=np.float32))
        (self.root / "model.safetensors").write_bytes(b"weights-b")
        with self.assertRaisesRegex(CandidateRetrievalError, "SHA-256 does not match"):
            dense_candidates_local(self.root/"dense/metadata/manifest.json", self.root, "阿贝多")

    def test_precomputed_vector_cannot_enter_local_model_path(self):
        (self.root / "model.safetensors").write_bytes(b"weights-a")
        build_dense_index(self.ru_manifest, self.root/"dense", model_dir=self.root, vectors=np.array([[1,0],[0,1],[1,0]], dtype=np.float32))
        build_lexical_index(self.ru_manifest, self.root/"lex")
        with self.assertRaisesRegex(CandidateRetrievalError, "cannot be combined"):
            retrieve_candidates(
                "dense",
                dense_manifest_path=self.root/"dense/metadata/manifest.json",
                model_dir=self.root,
                query_vector=np.array([1, 0], dtype=np.float32),
            )
        with self.assertRaisesRegex(CandidateRetrievalError, "cannot be combined"):
            retrieve_candidates(
                "hybrid",
                lexical_manifest_path=self.root/"lex/metadata/manifest.json",
                dense_manifest_path=self.root/"dense/metadata/manifest.json",
                model_dir=self.root,
                query_vector=np.array([1, 0], dtype=np.float32),
            )

    def test_dense_query_instruction_changes_query_identity_only(self):
        (self.root / "model.safetensors").write_bytes(b"weights-a")
        dm = build_dense_index(self.ru_manifest, self.root/"dense", model_dir=self.root, vectors=np.array([[1,0],[0,1],[1,0]], dtype=np.float32), instruction="corpus-placeholder")
        with patch("genshin_corpus.retrieval.candidate_retrieval.encode_dense_query", return_value=np.array([1, 0], dtype=np.float32)):
            first = dense_candidates_local(self.root/"dense/metadata/manifest.json", self.root, "阿贝多", instruction="指令一")
            second = dense_candidates_local(self.root/"dense/metadata/manifest.json", self.root, "阿贝多", instruction="指令二")
        self.assertEqual(dm["arm_build_identity"], json.loads((self.root/"dense/metadata/manifest.json").read_text(encoding="utf-8"))["arm_build_identity"])
        self.assertNotEqual(first[0]["retrieval"]["query_config_identity"], second[0]["retrieval"]["query_config_identity"])

    def test_dense_loader_validates_artifact_contract(self):
        build_dense_index(self.ru_manifest, self.root/"dense", model_dir=self.root, vectors=np.array([[1,0],[0,1],[1,1]], dtype=np.float32))
        manifest_path = self.root/"dense/metadata/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        vector_path = self.root/"dense/artifacts/vectors.f32.npy"
        values = np.load(vector_path, allow_pickle=False).astype(np.float32)
        values[0, 0] = np.nan
        import io
        out = io.BytesIO(); np.save(out, values, allow_pickle=False); vector_path.write_bytes(out.getvalue())
        manifest["artifacts"]["vectors"]["sha256"] = hashlib.sha256(vector_path.read_bytes()).hexdigest()
        manifest["artifacts"]["vectors"]["byte_count"] = vector_path.stat().st_size
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        with self.assertRaisesRegex(CandidateRetrievalError, "non-finite"):
            dense_candidates(self.root/"dense/metadata/manifest.json", np.array([1,0], dtype=np.float32))

    def test_candidate_serialization_is_deterministic(self):
        lm = build_lexical_index(self.ru_manifest, self.root/"lex")
        rows = lexical_candidates(self.root/"lex/metadata/manifest.json", "阿贝多", top_k=3)
        self.assertEqual(canonical_json_bytes(rows), canonical_json_bytes(json.loads(canonical_json_bytes(rows).decode())))

    def test_switchable_local_dense_and_hybrid_paths_require_binding(self):
        (self.root / "model.safetensors").write_bytes(b"weights-a")
        lm = build_lexical_index(self.ru_manifest, self.root/"lex")
        dm = build_dense_index(self.ru_manifest, self.root/"dense", model_dir=self.root, vectors=np.array([[1,0],[0,1],[1,0]], dtype=np.float32))
        with patch("genshin_corpus.retrieval.candidate_retrieval.encode_dense_query", return_value=np.array([1, 0], dtype=np.float32)):
            dense = retrieve_candidates("dense", dense_manifest_path=self.root/"dense/metadata/manifest.json", model_dir=self.root, query="阿贝多", top_k=2)
            hybrid = retrieve_candidates("hybrid", lexical_manifest_path=self.root/"lex/metadata/manifest.json", dense_manifest_path=self.root/"dense/metadata/manifest.json", model_dir=self.root, query="阿贝多", top_k=2)
        self.assertEqual(dense[0]["retrieval"]["mode"], "dense")
        self.assertEqual(hybrid[0]["retrieval"]["mode"], "hybrid")
        self.assertEqual(hybrid[0]["retrieval"]["arm_build_identities"]["dense"], dm["arm_build_identity"])

    def test_dense_loader_rejects_non_deterministic_row_mapping(self):
        build_dense_index(self.ru_manifest, self.root/"dense", model_dir=self.root, vectors=np.array([[1,0],[0,1],[1,1]], dtype=np.float32))
        rows_path = self.root/"dense/artifacts/rows.jsonl.gz"
        rows = [{"occurrence_index": 1, "unit_id": "u0"}, {"occurrence_index": 1, "unit_id": "u1"}, {"occurrence_index": 2, "unit_id": "u2"}]
        body = gzip.compress(b"".join(canonical_json_bytes(row)+b"\n" for row in rows), mtime=0)
        rows_path.write_bytes(body)
        manifest_path = self.root/"dense/metadata/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"]["rows"].update({"sha256": hashlib.sha256(body).hexdigest(), "byte_count": len(body)})
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        with self.assertRaisesRegex(CandidateRetrievalError, "row mapping"):
            dense_candidates(self.root/"dense/metadata/manifest.json", np.array([1,0], dtype=np.float32))


if __name__ == "__main__": unittest.main()
