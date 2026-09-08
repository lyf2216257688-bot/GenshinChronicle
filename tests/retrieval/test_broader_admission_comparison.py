from __future__ import annotations

import hashlib
import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval import broader_admission_comparison as comparison
from genshin_corpus.retrieval.evidence_assembly import (
    EVIDENCE_PACKET_SCHEMA_VERSION,
    EvidenceAssemblyConfig,
    V2_DIRECT_FIRST_CONTEXT_CAP,
    prepare_evidence_assembly_context,
)
from genshin_corpus.retrieval.retrieval_units import (
    RetrievalUnitBuildConfig,
    build_retrieval_units,
    load_retrieval_units,
)


class BroaderAdmissionComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("data/retrieval/.broader-admission-comparison-test")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        self.config = EvidenceAssemblyConfig()

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def _candidates(self) -> list[dict]:
        return [
            {
                "unit_id": f"u{rank}", "rank": rank,
                "retrieval": {
                    "mode": "hybrid", "score": 1.0 / rank,
                    "arm_build_identities": {
                        "lexical": comparison.ACCEPTED_LEXICAL_BUILD_IDENTITY,
                        "dense": comparison.ACCEPTED_DENSE_BUILD_IDENTITY,
                    },
                    "fusion": {"method": "rrf"},
                },
            }
            for rank in range(1, 21)
        ]

    def _packet(self, members: list[str], *, challenger: bool) -> dict:
        packet = {
            "schema_version": EVIDENCE_PACKET_SCHEMA_VERSION,
            "assembly_version": comparison.DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY if challenger else "phase04-rag-w1-deterministic-assembly-0.1",
            "retrieval_unit_build": {"build_identity": comparison.ACCEPTED_RU_BUILD_IDENTITY},
            "assembly_config": comparison._v2_operating_config(self.config),
            "assembly_config_identity": "fixture-config",
            "selection_policy": V2_DIRECT_FIRST_CONTEXT_CAP.to_dict(),
            "selection_policy_identity": "fixture-policy",
            "evidence": [{"evidence_id": "E01", "char_count": len(members), "text": "fixture", "source_order": [1], "members": [{"unit_id": item} for item in members]}],
            "budget": {
                "per_block_chars": 3000, "total_context_chars": 12000,
                "used_context_chars": len(members), "used_evidence_blocks": 1,
                "used_direct_containing_blocks": 1, "used_context_only_blocks": 0,
                "omitted_blocks": [],
            },
        }
        if challenger:
            packet["shadow_contract"] = {
                "identity": comparison.DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
                "control_selection_policy": V2_DIRECT_FIRST_CONTEXT_CAP.to_dict(),
                "direct_footprints": [],
            }
        return packet

    def test_same_frozen_object_is_passed_to_both_existing_policy_paths(self) -> None:
        candidates = self._candidates()
        frozen = {
            "record_path": self.root / "supply" / "Q001.json",
            "record": {"question_id": "Q001", "question_identity": "identity", "run_identity": comparison.ACCEPTED_SUPPLY_RUN_IDENTITY},
            "hybrid_rows": candidates,
        }
        frozen["hybrid_sha256"] = comparison._candidate_sha(candidates)
        frozen["accepted_hybrid_sha256"] = frozen["hybrid_sha256"]
        seen = []

        def control(manifest, supplied, **kwargs):
            seen.append(("control", id(supplied), comparison._candidate_sha(supplied)))
            kwargs["diagnostics"].selection_trace = {"admission_order": []}
            return self._packet(["u1"], challenger=False)

        def challenger(manifest, supplied, **kwargs):
            seen.append(("challenger", id(supplied), comparison._candidate_sha(supplied)))
            kwargs["diagnostics"].selection_trace = {"admission_order": []}
            return self._packet(["u1", "u2"], challenger=True)

        with patch.object(comparison, "assemble_evidence_packet", side_effect=control), patch.object(comparison, "assemble_deferred_footprint_charge_shadow_packet", side_effect=challenger):
            row = comparison.compare_frozen_question(
                frozen,
                retrieval_unit_manifest_path=self.root / "ru.json",
                prepared_context=object(),
                config=self.config,
            )

        self.assertEqual([item[0] for item in seen], ["control", "challenger"])
        self.assertEqual({item[1] for item in seen}, {id(candidates)})
        self.assertEqual({item[2] for item in seen}, {frozen["hybrid_sha256"]})
        self.assertTrue(row["frozen_input"]["same_object_passed_to_both_arms"])
        self.assertEqual(row["control_invisible_to_challenger_visible_roots"], ["u2"])
        # Generation receives text and display context, not Packet member audit
        # metadata, so the direct-root audit delta remains mechanically recorded
        # while the evidence-visible identity is unchanged.
        self.assertTrue(row["evidence_identical"])
        self.assertEqual(row["mechanical_class"], "identical")

    def test_frozen_hybrid_sha_mismatch_fails_before_any_policy_path(self) -> None:
        rows = self._candidates()
        record = {
            "schema_version": "p04-rag-broader-admission-candidate-supply-0.1",
            "question_id": "Q001", "question_identity": "identity", "run_identity": comparison.ACCEPTED_SUPPLY_RUN_IDENTITY,
            "candidate_windows": {"hybrid": rows}, "candidate_hashes": {"hybrid": "0" * 64},
        }
        supply_manifest = {"schema_version": "p04-rag-broader-admission-candidate-supply-0.1", "run_identity": comparison.ACCEPTED_SUPPLY_RUN_IDENTITY, "baseline": {"lexical_build_identity": comparison.ACCEPTED_LEXICAL_BUILD_IDENTITY, "dense_build_identity": comparison.ACCEPTED_DENSE_BUILD_IDENTITY}}
        path = self.root / "supply" / "candidates" / "Q001.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(canonical_json_bytes(record))
        with self.assertRaisesRegex(comparison.BroaderAdmissionComparisonError, "candidate SHA mismatch"), patch.object(comparison, "assemble_evidence_packet") as control:
            comparison._load_frozen_question(self.root / "supply", "Q001", supply_manifest)
        control.assert_not_called()

    def test_co_tampered_rows_and_embedded_hash_are_rejected_by_external_hybrid_oracle(self) -> None:
        original = self._candidates()
        tampered = self._candidates()
        tampered[0]["retrieval"]["score"] = 99.0
        expected_sha = comparison._candidate_sha(original)
        actual_sha = comparison._candidate_sha(tampered)
        self.assertNotEqual(actual_sha, expected_sha)
        record = {
            "schema_version": "p04-rag-broader-admission-candidate-supply-0.1",
            "question_id": "Q001",
            "question_identity": "identity",
            "run_identity": comparison.ACCEPTED_SUPPLY_RUN_IDENTITY,
            "candidate_windows": {"hybrid": tampered},
            "candidate_hashes": {"hybrid": actual_sha},
        }
        path = self.root / "supply" / "candidates" / "Q001.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(canonical_json_bytes(record))
        supply_manifest = {
            "schema_version": "p04-rag-broader-admission-candidate-supply-0.1",
            "run_identity": comparison.ACCEPTED_SUPPLY_RUN_IDENTITY,
            "baseline": {
                "lexical_build_identity": comparison.ACCEPTED_LEXICAL_BUILD_IDENTITY,
                "dense_build_identity": comparison.ACCEPTED_DENSE_BUILD_IDENTITY,
            },
            "_accepted_hybrid_hashes": {"Q001": expected_sha},
        }
        with self.assertRaisesRegex(comparison.BroaderAdmissionComparisonError, "accepted historical oracle"), patch.object(comparison, "assemble_evidence_packet") as control, patch.object(comparison, "assemble_deferred_footprint_charge_shadow_packet") as challenger:
            comparison._load_frozen_question(self.root / "supply", "Q001", supply_manifest)
        control.assert_not_called()
        challenger.assert_not_called()

    def test_byte_different_but_structurally_valid_historical_diagnostic_is_rejected(self) -> None:
        copied = self.root / "historical-diagnostic.json"
        copied.write_bytes(comparison.DEFAULT_CANDIDATE_HASHES.read_bytes() + b"\n")
        with patch.object(comparison, "DEFAULT_CANDIDATE_HASHES", copied):
            with self.assertRaisesRegex(comparison.BroaderAdmissionComparisonError, "external SHA-256"):
                comparison._accepted_hybrid_hashes()

    def test_evidence_identity_ignores_shadow_and_policy_audit_metadata(self) -> None:
        control_packet = self._packet(["u1"], challenger=False)
        challenger_packet = self._packet(["u1"], challenger=True)
        control = comparison._arm_summary(control_packet, self._candidates())
        challenger = comparison._arm_summary(challenger_packet, self._candidates())
        self.assertNotEqual(control["packet_sha256"], challenger["packet_sha256"])
        self.assertEqual(
            control["generation_visible_projection_sha256"],
            challenger["generation_visible_projection_sha256"],
        )
        self.assertEqual(comparison._mechanical_class(control, challenger), "identical")
        row = {
            "evidence_identical": True,
            "mechanical_class": "identical",
            "control": control,
            "challenger": challenger,
            "control_invisible_to_challenger_visible_roots": [],
            "control_visible_to_challenger_invisible_roots": [],
        }
        self.assertEqual(comparison._aggregate([row])["evidence_non_identical_count"], 0)
        self.assertEqual(comparison._non_identical_index([row]), b"")

    def _preflight(self) -> dict:
        return {
            "run_identity": "comparison-run", "assembly_config": comparison._v2_operating_config(self.config),
            "control_selection_policy": V2_DIRECT_FIRST_CONTEXT_CAP.to_dict(),
        }

    def _row(self, frozen: dict) -> dict:
        question_id = frozen["record"]["question_id"]
        control_packet = self._packet(["u1"], challenger=False)
        challenger_packet = self._packet(["u1"], challenger=True)
        control = comparison._arm_summary(control_packet, frozen["hybrid_rows"])
        challenger = comparison._arm_summary(challenger_packet, frozen["hybrid_rows"])
        return {
            "schema_version": comparison.SCHEMA_VERSION,
            "question_id": question_id,
            "question_identity": frozen["record"]["question_identity"],
            "frozen_input": {
                "step0_run_identity": comparison.ACCEPTED_SUPPLY_RUN_IDENTITY,
                "source_candidate_path": str(frozen["record_path"]),
                "hybrid_candidate_sha256": frozen["hybrid_sha256"],
                "accepted_historical_hybrid_sha256": frozen["accepted_hybrid_sha256"],
                "candidate_count": 20,
                "same_object_passed_to_both_arms": True,
                "candidate_sha256_before": frozen["hybrid_sha256"],
                "candidate_sha256_after_control": frozen["hybrid_sha256"],
                "candidate_sha256_after_challenger": frozen["hybrid_sha256"],
            },
            "policy_bindings": {
                "retrieval_unit_build_identity": comparison.ACCEPTED_RU_BUILD_IDENTITY,
                "control_selection_policy": V2_DIRECT_FIRST_CONTEXT_CAP.to_dict(),
                "challenger_policy": comparison.DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
                "assembly_config": comparison._v2_operating_config(self.config),
            },
            "control": control,
            "challenger": challenger,
            "evidence_identical": True,
            "mechanical_class": "identical",
            "control_invisible_to_challenger_visible_roots": [],
            "control_visible_to_challenger_invisible_roots": [],
            "shared_visible_roots": ["u1"],
            "control_selection_trace": {"admission_order": []},
            "challenger_selection_trace": {"admission_order": []},
            "control_packet": control_packet,
            "challenger_packet": challenger_packet,
        }

    def test_two_question_synthetic_resume_revalidates_partial_rows_and_refuses_complete(self) -> None:
        output = self.root / "comparison"
        question_ids = ("Q001", "Q002")
        supply = {"run_identity": comparison.ACCEPTED_SUPPLY_RUN_IDENTITY}

        def frozen(_, question_id, __):
            rows = self._candidates()
            digest = comparison._candidate_sha(rows)
            return {
                "record_path": self.root / "supply" / "candidates" / f"{question_id}.json",
                "hybrid_rows": rows,
                "hybrid_sha256": digest,
                "accepted_hybrid_sha256": digest,
                "record": {
                    "question_id": question_id,
                    "question_identity": f"identity-{question_id}",
                    "run_identity": comparison.ACCEPTED_SUPPLY_RUN_IDENTITY,
                },
            }
        calls = []

        def compare(frozen_input, **kwargs):
            question_id = frozen_input["record"]["question_id"]
            calls.append(question_id)
            if question_id == "Q002" and calls.count("Q002") == 1:
                raise RuntimeError("fixture interruption")
            return self._row(frozen_input)

        patches = (
            patch.object(comparison, "QUESTION_IDS", question_ids),
            patch.object(comparison, "preflight_broader_admission_comparison", return_value=self._preflight()),
            patch.object(comparison, "_validate_supply_manifest", return_value=supply),
            patch.object(comparison, "_expected_config", return_value=self.config),
            patch.object(comparison, "prepare_evidence_assembly_context", return_value=type("Context", (), {"retrieval_unit_build_identity": comparison.ACCEPTED_RU_BUILD_IDENTITY})()),
            patch.object(comparison, "_load_frozen_question", side_effect=frozen),
            patch.object(comparison, "compare_frozen_question", side_effect=compare),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            with self.assertRaisesRegex(comparison.BroaderAdmissionComparisonError, "partial artifacts remain inspectable"):
                comparison.run_broader_admission_comparison(output, supply_root=self.root / "supply", retrieval_unit_manifest_path=self.root / "ru.json")
        partial = json_load(output / "metadata" / "manifest.json")
        self.assertEqual(partial["status"], "failed")
        self.assertEqual(partial["completed_question_ids"], ["Q001"])
        duplicate_ledger = dict(partial)
        duplicate_ledger["completed_question_ids"] = ["Q001", "Q001"]
        duplicate_ledger["completed_question_count"] = 2
        (output / "metadata" / "manifest.json").write_bytes(canonical_json_bytes(duplicate_ledger))
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            with self.assertRaisesRegex(comparison.BroaderAdmissionComparisonError, "recovery ledger"):
                comparison.run_broader_admission_comparison(output, supply_root=self.root / "supply", retrieval_unit_manifest_path=self.root / "ru.json", resume=True)
        (output / "metadata" / "manifest.json").write_bytes(canonical_json_bytes(partial))
        q001_path = output / "comparisons" / "Q001.json"
        original_body = q001_path.read_bytes()
        tampered = json_load(q001_path)
        tampered["control"]["packet_sha256"] = "0" * 64
        tampered_body = canonical_json_bytes(tampered)
        q001_path.write_bytes(tampered_body)
        partial["completed_artifacts"]["Q001"] = comparison._descriptor(q001_path, tampered_body)
        (output / "metadata" / "manifest.json").write_bytes(canonical_json_bytes(partial))
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            with self.assertRaisesRegex(comparison.BroaderAdmissionComparisonError, "Packet audit/projection"):
                comparison.run_broader_admission_comparison(output, supply_root=self.root / "supply", retrieval_unit_manifest_path=self.root / "ru.json", resume=True)
        q001_path.write_bytes(original_body)
        partial["completed_artifacts"]["Q001"] = comparison._descriptor(q001_path, original_body)
        (output / "metadata" / "manifest.json").write_bytes(canonical_json_bytes(partial))
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            complete = comparison.run_broader_admission_comparison(output, supply_root=self.root / "supply", retrieval_unit_manifest_path=self.root / "ru.json", resume=True)
        self.assertEqual(complete["status"], "complete")
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            with self.assertRaisesRegex(comparison.BroaderAdmissionComparisonError, "complete"):
                comparison.run_broader_admission_comparison(output, supply_root=self.root / "supply", retrieval_unit_manifest_path=self.root / "ru.json", resume=True)

    def test_compare_frozen_question_exercises_both_real_policy_paths_on_tiny_ru(self) -> None:
        fixture = Path(__file__).parents[1] / "fixtures" / "retrieval" / "canonical-rag-w1-record.json"
        record = json.loads(fixture.read_text(encoding="utf-8"))
        record_path = self.root / "canonical-record.json"
        record_body = canonical_json_bytes(record)
        record_path.write_bytes(record_body)
        canonical_manifest = self.root / "canonical-manifest.json"
        canonical_manifest.write_bytes(canonical_json_bytes({
            "status": "complete",
            "canonical_run_id": "broader-admission-fixture",
            "source": "mihoyo_obc",
            "locale": "zh-cn",
            "input_record_count": 1,
            "accounted_record_count": 1,
            "input_integrity_failure_count": 0,
            "dependencies": {"canonical_versions": {
                "schema_version": "phase03-draft-0.1",
                "transform_version": "obc-modules-as-sections-0.1",
                "structural_normalization_version": "none-0.1",
                "classification_rule_versions": {},
            }},
            "records": [{
                "record_id": record["record_id"],
                "canonical_record_path": str(record_path),
                "canonical_record_sha256": hashlib.sha256(record_body).hexdigest(),
                "canonical_status": "canonical",
            }],
        }))
        ru_root = self.root / "tiny-ru"
        build = build_retrieval_units(
            canonical_manifest,
            ru_root,
            config=RetrievalUnitBuildConfig(text_fragment_chars=4, structured_fragment_chars=10),
        )
        ru_manifest = ru_root / "metadata" / "manifest.json"
        _, units = load_retrieval_units(ru_manifest)
        rows = [
            {"unit_id": unit["unit_id"], "rank": index + 1, "retrieval": {"mode": "hybrid"}}
            for index, unit in enumerate(units[:20])
        ]
        # The tiny fixture has fewer than 20 units; direct Assembly coverage does
        # not run the frozen-supply loader's Top20 gate.
        digest = comparison._candidate_sha(rows)
        frozen = {
            "record_path": self.root / "supply" / "candidates" / "Q001.json",
            "hybrid_rows": rows,
            "hybrid_sha256": digest,
            "accepted_hybrid_sha256": digest,
            "record": {
                "question_id": "Q001",
                "question_identity": "tiny-fixture-question",
                "run_identity": comparison.ACCEPTED_SUPPLY_RUN_IDENTITY,
            },
        }
        config = EvidenceAssemblyConfig()
        context = prepare_evidence_assembly_context(ru_manifest)
        with patch.object(comparison, "ACCEPTED_RU_BUILD_IDENTITY", build["build_identity"]):
            row = comparison.compare_frozen_question(
                frozen,
                retrieval_unit_manifest_path=ru_manifest,
                prepared_context=context,
                config=config,
            )
        self.assertTrue(row["frozen_input"]["same_object_passed_to_both_arms"])
        self.assertEqual(row["policy_bindings"]["assembly_config"], comparison._v2_operating_config(config))
        self.assertEqual(row["control_packet"]["retrieval_audit"]["retrieval_metadata"]["candidate_supply"], "frozen_step0")
        self.assertEqual(row["challenger_packet"]["retrieval_audit"]["retrieval_metadata"]["candidate_supply"], "frozen_step0")
        self.assertEqual(row["control_packet"]["selection_policy"]["identity"], comparison.V2_DIRECT_FIRST_CONTEXT_CAP_POLICY)
        self.assertEqual(
            row["challenger_packet"]["shadow_contract"]["identity"],
            comparison.DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
        )


def json_load(path: Path) -> dict:
    import json
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
