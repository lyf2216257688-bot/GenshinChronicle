from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from genshin_corpus.canonical.fingerprints import sha256_json
from genshin_corpus.retrieval import broader_admission_comparison as comparison
from genshin_corpus.retrieval import deferred_api_parity as parity
from genshin_corpus.retrieval.evidence_assembly import (
    DEFERRED_FOOTPRINT_CHARGE,
    DEFERRED_FOOTPRINT_CHARGE_POLICY,
    DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
    EVIDENCE_PACKET_SCHEMA_VERSION,
    EvidenceAssemblyConfig,
    V2_DIRECT_FIRST_CONTEXT_CAP,
)


class DeferredApiParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("data/retrieval/.deferred-api-parity-test")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        self.config = EvidenceAssemblyConfig()

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def _packet(self, *, shadow: bool, text: str = "evidence") -> dict:
        version = DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY if shadow else DEFERRED_FOOTPRINT_CHARGE_POLICY
        selection = V2_DIRECT_FIRST_CONTEXT_CAP if shadow else DEFERRED_FOOTPRINT_CHARGE
        policy_binding = (
            {"control_selection_policy": V2_DIRECT_FIRST_CONTEXT_CAP.to_dict()}
            if shadow
            else {"selection_policy": DEFERRED_FOOTPRINT_CHARGE.to_dict()}
        )
        assembly_config = parity._operating_config(self.config)
        packet = {
            "schema_version": EVIDENCE_PACKET_SCHEMA_VERSION,
            "assembly_version": version,
            "retrieval_unit_build": {"build_identity": comparison.ACCEPTED_RU_BUILD_IDENTITY},
            "assembly_config": assembly_config,
            "assembly_config_identity": parity._expected_identity(
                {"assembly_config": assembly_config},
                assembly_version=version,
                policy_binding=policy_binding,
            ),
            "selection_policy": selection.to_dict(),
            "selection_policy_identity": parity._expected_identity(
                {"assembly_config": assembly_config},
                assembly_version=version,
                policy_binding={**policy_binding, "assembly_config": assembly_config},
                config_key="assembly_config",
            ),
            "retrieval_audit": {"input_candidate_count": 1, "retrieval_metadata": {"mode": "synthetic"}},
            "evidence": [{
                "evidence_id": "E01",
                "text": text,
                "char_count": len(text),
                "source_order": [1],
                "members": [{
                    "unit_id": "u1",
                    ("shadow_anchor_memberships" if shadow else "admission_anchor_memberships"): [],
                    ("shadow_rendering" if shadow else "admission_rendering"): {"phase": "direct_root"},
                }],
            }],
            "budget": {
                "per_block_chars": 3000,
                "total_context_chars": 12000,
                "context_only_block_cap": 8,
                "used_direct_containing_blocks": 1,
                "used_context_only_blocks": 0,
                "used_evidence_blocks": 1,
                "used_context_chars": len(text),
                "omitted_blocks": [],
            },
        }
        contract_key = "shadow_contract" if shadow else "admission_contract"
        packet[contract_key] = {
            "identity": version,
            **({"control_selection_policy": V2_DIRECT_FIRST_CONTEXT_CAP.to_dict()} if shadow else {}),
            "candidate_observations": [],
            "direct_footprints": [],
            "context_occurrences": [],
        }
        return packet

    def _frozen(self) -> dict:
        candidates = [{"unit_id": "u1", "rank": 1, "retrieval": {"mode": "hybrid"}}]
        digest = comparison._candidate_sha(candidates)
        return {
            "record": {
                "question_id": "Q001",
                "question_identity": "question-identity",
                "run_identity": comparison.ACCEPTED_SUPPLY_RUN_IDENTITY,
            },
            "record_path": self.root / "Q001.json",
            "hybrid_rows": candidates,
            "accepted_hybrid_sha256": digest,
        }

    def _trace(self, *, shadow: bool) -> dict:
        return {
            ("shadow_contract" if shadow else "admission_contract"): (
                DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY if shadow else DEFERRED_FOOTPRINT_CHARGE_POLICY
            ),
            "candidate_observations": [],
            "direct_footprints": [],
            "context_occurrences": [],
            "admission_order": [],
        }

    def test_formal_matches_historical_shadow_and_only_gate_a_metadata_is_ignored(self) -> None:
        formal = self._packet(shadow=False)
        shadow = self._packet(shadow=True)
        projection, projection_sha = comparison._evidence_visible_projection(shadow)
        historical = {
            "challenger_packet": shadow,
            "challenger_selection_trace": self._trace(shadow=True),
            "challenger": {
                "generation_visible_projection": projection,
                "generation_visible_projection_sha256": projection_sha,
            },
        }
        def formal_assembler(*args, **kwargs):
            kwargs["diagnostics"].selection_trace = self._trace(shadow=False)
            return formal

        with patch.object(parity, "assemble_deferred_footprint_charge_packet", side_effect=formal_assembler):
            result = parity._compare_question(
                self._frozen(),
                historical,
                retrieval_unit_manifest_path=self.root / "ru.json",
                prepared_context=SimpleNamespace(retrieval_unit_build_identity=comparison.ACCEPTED_RU_BUILD_IDENTITY),
                config=self.config,
            )
        self.assertEqual(result["parity"], {"generation_visible": True, "mechanical_accounting": True, "selection_trace": True})
        self.assertEqual(result["formal"]["generation_visible_projection_sha256"], projection_sha)

        changed = self._packet(shadow=True)
        changed["budget"]["used_context_chars"] = 999
        with self.assertRaisesRegex(parity.DeferredApiParityError, "mechanical/accounting parity"):
            with patch.object(parity, "assemble_deferred_footprint_charge_packet", side_effect=formal_assembler):
                parity._compare_question(
                    self._frozen(),
                    {**historical, "challenger_packet": changed},
                    retrieval_unit_manifest_path=self.root / "ru.json",
                    prepared_context=SimpleNamespace(retrieval_unit_build_identity=comparison.ACCEPTED_RU_BUILD_IDENTITY),
                    config=self.config,
                )

    def test_evidence_text_difference_is_not_normalized(self) -> None:
        formal = self._packet(shadow=False)
        shadow = self._packet(shadow=True, text="different")
        projection, projection_sha = comparison._evidence_visible_projection(shadow)
        historical = {
            "challenger_packet": shadow,
            "challenger_selection_trace": self._trace(shadow=True),
            "challenger": {
                "generation_visible_projection": projection,
                "generation_visible_projection_sha256": projection_sha,
            },
        }
        with self.assertRaisesRegex(parity.DeferredApiParityError, "Generation-visible parity"):
            with patch.object(parity, "assemble_deferred_footprint_charge_packet", return_value=formal):
                parity._compare_question(
                    self._frozen(),
                    historical,
                    retrieval_unit_manifest_path=self.root / "ru.json",
                    prepared_context=SimpleNamespace(retrieval_unit_build_identity=comparison.ACCEPTED_RU_BUILD_IDENTITY),
                    config=self.config,
                )

    def test_runner_keeps_exact_order_zero_counters_and_resume_binding(self) -> None:
        output = self.root / "output"
        question_ids = ("Q001", "Q002")
        bindings = {"source_commit": parity.ACCEPTED_SOURCE_COMMIT, "input": "fixture"}
        supply_manifest = {"run_identity": comparison.ACCEPTED_SUPPLY_RUN_IDENTITY}
        config = self.config
        frozen = self._frozen()
        calls: list[str] = []

        def compare(frozen_input, historical, **kwargs):
            question_id = frozen_input["record"]["question_id"]
            calls.append(question_id)
            return {"question_id": question_id, "historical": historical["value"]}

        def frozen_question(_, question_id, __):
            value = dict(frozen)
            value["record"] = dict(frozen["record"], question_id=question_id)
            return value

        preflight = patch.object(parity, "QUESTION_IDS", question_ids), patch.object(
            parity, "_preflight", return_value=(bindings, supply_manifest, {item: {"value": item} for item in question_ids}, config)
        ), patch.object(
            parity, "prepare_evidence_assembly_context", return_value=SimpleNamespace(retrieval_unit_build_identity=comparison.ACCEPTED_RU_BUILD_IDENTITY)
        ), patch.object(parity.comparison, "_load_frozen_question", side_effect=frozen_question), patch.object(parity, "_compare_question", side_effect=compare)
        with preflight[0], preflight[1], preflight[2], preflight[3], preflight[4]:
            manifest = parity.run_deferred_api_parity(output)
        self.assertEqual(calls, ["Q001", "Q002"])
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["counters"], {
            "retrieval_calls": 0,
            "dense_query_encodings": 0,
            "rrf_recomputations": 0,
            "provider_calls": 0,
            "generation_calls": 0,
        })
        with preflight[0], preflight[1], preflight[2], preflight[3], preflight[4]:
            with self.assertRaisesRegex(parity.DeferredApiParityError, "complete"):
                parity.run_deferred_api_parity(output, resume=True)

    def test_formal_identity_uses_the_existing_assembly_contract_shape(self) -> None:
        packet = self._packet(shadow=False)
        self.assertEqual(
            packet["assembly_config_identity"],
            sha256_json({
                "assembly_version": DEFERRED_FOOTPRINT_CHARGE_POLICY,
                "config": packet["assembly_config"],
                "selection_policy": DEFERRED_FOOTPRINT_CHARGE.to_dict(),
            }),
        )

    def test_later_head_is_allowed_when_gate_a_sources_are_unchanged(self) -> None:
        # The source binding must not invoke Git HEAD.  A later checkpoint can
        # therefore contain this runner without invalidating Gate-A semantics.
        with patch.object(subprocess, "run", side_effect=AssertionError("HEAD must not be required")):
            bindings = parity._source_bindings()
        self.assertEqual(bindings["gate_a_formal_source_baseline"], parity.ACCEPTED_SOURCE_COMMIT)
        self.assertEqual(
            bindings["gate_a_formal_source_sha256"],
            parity.GATE_A_FORMAL_SOURCE_SHA256,
        )
        self.assertEqual(len(bindings["parity_runner_source_sha256"]), 64)

    def test_changed_gate_a_helper_source_fails_closed(self) -> None:
        original = parity._file_sha256

        def changed_one(path: Path, label: str) -> str:
            if Path(path).as_posix().endswith("evidence_assembly.py"):
                return "0" * 64
            return original(path, label)

        with patch.object(parity, "_file_sha256", side_effect=changed_one):
            with self.assertRaisesRegex(parity.DeferredApiParityError, "byte-equivalent"):
                parity._source_bindings()

    def test_changed_runner_source_identity_makes_partial_run_non_resumable(self) -> None:
        output = self.root / "resume-output"
        question_ids = ("Q001", "Q002")
        source_bindings = {
            "gate_a_formal_source_baseline": parity.ACCEPTED_SOURCE_COMMIT,
            "gate_a_formal_source_sha256": dict(parity.GATE_A_FORMAL_SOURCE_SHA256),
            "parity_runner_source_path": "src/genshin_corpus/retrieval/deferred_api_parity.py",
            "parity_runner_source_sha256": "a" * 64,
        }
        changed_source_bindings = {**source_bindings, "parity_runner_source_sha256": "b" * 64}
        supply_manifest = {"run_identity": comparison.ACCEPTED_SUPPLY_RUN_IDENTITY}
        preflight_result = (
            source_bindings,
            supply_manifest,
            {question_id: {"value": question_id} for question_id in question_ids},
            self.config,
        )

        def stop_after_preflight(*args, **kwargs):
            raise parity.DeferredApiParityError("synthetic interruption")

        patches = (
            patch.object(parity, "QUESTION_IDS", question_ids),
            patch.object(parity, "_preflight", return_value=preflight_result),
            patch.object(
                parity,
                "prepare_evidence_assembly_context",
                return_value=SimpleNamespace(retrieval_unit_build_identity=comparison.ACCEPTED_RU_BUILD_IDENTITY),
            ),
            patch.object(parity.comparison, "_load_frozen_question", return_value=self._frozen()),
            patch.object(parity, "_compare_question", side_effect=stop_after_preflight),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaisesRegex(parity.DeferredApiParityError, "synthetic interruption"):
                parity.run_deferred_api_parity(output)

        changed_preflight = patch.object(
            parity,
            "_preflight",
            return_value=(
                changed_source_bindings,
                supply_manifest,
                {question_id: {"value": question_id} for question_id in question_ids},
                self.config,
            ),
        )
        with patches[0], changed_preflight:
            with self.assertRaisesRegex(parity.DeferredApiParityError, "exact source and inputs"):
                parity.run_deferred_api_parity(output, resume=True)


if __name__ == "__main__":
    unittest.main()
