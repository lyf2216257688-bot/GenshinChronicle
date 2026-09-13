from __future__ import annotations

from pathlib import Path
from pathlib import PureWindowsPath
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import shutil
import threading
import uuid

from genshin_corpus.rag.config import (
    DEFAULT_PRODUCTION_RAG_ROOT,
    ProductionRagArtifactPaths,
)
from genshin_corpus.ui.runtime import (
    PreparedStateOwner,
    UiConfigurationError,
    UiQuery,
    build_embedding_transport,
    execute_query,
    production_artifact_paths,
    release_prepared_state,
    resolve_output_root,
    summarize_result,
    validate_prepared_state,
)


class UiRuntimeTests(TestCase):
    def test_production_paths_are_owned_by_rag_config(self) -> None:
        paths = production_artifact_paths()
        self.assertIsInstance(paths, ProductionRagArtifactPaths)
        self.assertEqual(paths.root, DEFAULT_PRODUCTION_RAG_ROOT)
        self.assertNotIn("generation.measure", production_artifact_paths.__module__)
        self.assertEqual(len(paths.resolved_key()), 3)

    def test_closed_state_validation_and_release_delegate_to_backend(self) -> None:
        state = SimpleNamespace(closed=False, close=Mock())
        owner = PreparedStateOwner(state)
        self.assertTrue(validate_prepared_state(owner))
        release_prepared_state(owner)
        self.assertFalse(validate_prepared_state(owner))
        release_prepared_state(owner)
        self.assertEqual(state.close.call_count, 1)

    def test_release_waits_for_active_query_lease(self) -> None:
        state = SimpleNamespace(closed=False, close=Mock())
        owner = PreparedStateOwner(state)
        lease = owner.lease()
        releaser = threading.Thread(target=owner.close)
        releaser.start()
        releaser.join(0.05)
        self.assertTrue(releaser.is_alive())
        state.closed = True
        lease.__exit__(None, None, None)
        releaser.join(1.0)
        self.assertFalse(releaser.is_alive())
        state.close.assert_called_once_with()

    def test_missing_embedding_environment_fails_without_provider_call(self) -> None:
        with self.assertRaisesRegex(UiConfigurationError, "Qwen query embedding"):
            build_embedding_transport({})

    def test_evidence_only_does_not_construct_generation_provider(self) -> None:
        query = UiQuery("问题", "evidence_only", Path("output"))
        with patch("genshin_corpus.ui.runtime.build_embedding_transport", return_value=object()), patch(
            "genshin_corpus.ui.runtime.build_generation_provider"
        ) as build_generation, patch(
            "genshin_corpus.ui.runtime.run_single_question", return_value={"status": "succeeded"}
        ) as run:
            result = execute_query(object(), query, environment={})
        self.assertEqual(result["status"], "succeeded")
        build_generation.assert_not_called()
        self.assertIsNone(run.call_args.kwargs["generation_provider"])

    def test_generate_answer_constructs_generation_provider_only_for_generate_mode(self) -> None:
        query = UiQuery("问题", "generate_answer", Path("output"))
        provider = object()
        with patch("genshin_corpus.ui.runtime.build_embedding_transport", return_value=object()), patch(
            "genshin_corpus.ui.runtime.build_generation_provider", return_value=provider
        ) as build_generation, patch(
            "genshin_corpus.ui.runtime.run_single_question", return_value={"status": "succeeded"}
        ):
            execute_query(object(), query, environment={})
        build_generation.assert_called_once_with({})

    def test_generate_answer_missing_generation_credentials_fails_closed(self) -> None:
        with self.assertRaisesRegex(UiConfigurationError, "BAILIAN_BASE_URL"):
            from genshin_corpus.ui.runtime import build_generation_provider

            build_generation_provider({})

    def test_output_root_handles_existing_directory_and_rejects_file(self) -> None:
        raw = Path(".local") / f"ui-test-{uuid.uuid4().hex}"
        raw.mkdir(parents=True)
        try:
            root = raw / "runs"
            root.mkdir()
            self.assertEqual(resolve_output_root(str(root)), root.resolve())
            windows_path = PureWindowsPath("C:/GenshinChronicle/ui-runs")
            self.assertEqual(resolve_output_root(windows_path), Path(windows_path).resolve(strict=False))
            file_path = raw / "not-a-directory"
            file_path.write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(UiConfigurationError, "not a directory"):
                resolve_output_root(file_path)
        finally:
            shutil.rmtree(raw, ignore_errors=True)

    def test_result_summary_matches_formal_deferred_packet_and_compacts_errors(self) -> None:
        summary = summarize_result({
            "status": "succeeded",
            "execution_mode": "evidence_only",
            "query": {"execution_identity": "run-1"},
            "timing_seconds": {"retrieval": 1.25},
            "citation_validation": {
                "status": "not_applicable",
                "validation_reasons": [{"internal": "hidden"}],
            },
            "evidence_packet": {
                "evidence": [{
                    "evidence_id": "E01",
                    "text": "证据",
                    "char_count": 2,
                    "members": [{"internal": "hidden"}],
                }],
                "budget": {
                    "used_evidence_blocks": 1,
                    "used_context_chars": 2,
                    "total_context_chars": 12000,
                },
            },
            "persistence": {"run_root": "C:/runs/run-1"},
            "error": {
                "stage": "generation",
                "category": "provider_error",
                "code": "InternalError",
                "message": "secret or unrestricted backend detail",
                "provider_payload": {"api_key": "secret"},
            },
        })
        self.assertEqual(summary["evidence_count"], 1)
        self.assertEqual(summary["run_root"], "C:/runs/run-1")
        self.assertEqual(summary["timing_seconds"], {"retrieval": 1.25})
        self.assertEqual(summary["evidence"], [{"evidence_id": "E01", "text": "证据", "char_count": 2}])
        self.assertEqual(summary["error"], {
            "stage": "generation",
            "category": "provider_error",
            "code": "InternalError",
        })
        self.assertEqual(summary["citation_validation"], {"status": "not_applicable"})


if __name__ == "__main__":
    import unittest

    unittest.main()
