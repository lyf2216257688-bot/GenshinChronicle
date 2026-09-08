"""Materialize the frozen 70-question candidate supply for the admission comparison.

This is deliberately Retrieval-only.  It reuses the accepted M2 query-time
path and stops before Evidence Assembly, Provider, or Generation.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.generation.measure import (
    DEFAULT_M2_RUNTIME_INPUT,
    DEFAULT_M2_SOURCE_MANIFEST,
    M1Baseline,
    M1MeasureError,
    AcceptedQuestion,
    _baseline_metadata,
    load_m2_runtime_questions,
)

from .candidate_retrieval import encode_dense_query, load_batch_candidate_retriever, load_dense_query_model
from .retrieval_units import load_retrieval_units
from . import evidence_assembly as assembly_module


SCHEMA_VERSION = "p04-rag-broader-admission-candidate-supply-0.1"
MODES = ("lexical", "dense", "hybrid")
QUESTION_IDS = tuple(f"Q{number:03d}" for number in range(1, 71))
DEFAULT_M2_RUN_MANIFEST = Path(".local/p04-rag-m2/run-001/manifest.json")
DEFAULT_CANDIDATE_HASHES = Path(".local/p04-rag-a1-2-70q-measure/diagnostic.json")
DEFAULT_FULL_ROWS = Path(".local/p04-rag-a1-2-unresolved-attribution/attribution.json")
DEFAULT_OUTPUT_ROOT = Path(".local/p04-rag-broader-admission-70q-candidate-supply")
ACCEPTED_CANDIDATE_HASHES_SHA256 = "30af71560e9b008c01f90e4bfb4721b321baf450fe9a9e14a5febddd934b9961"
ACCEPTED_FULL_ROWS_SHA256 = "8a5c2880b787eba768d0be010239b04d227312db896cf1d17e98eec0c13a4499"
ACCEPTED_RUNTIME_INPUT_SHA256 = "dab333ddfe3061758596cf4196443df274a2f065b8c9c36cbbfe5c52bebe380c"
ACCEPTED_BASELINE = {
    "retrieval_unit_build_identity": "49b48ee746716add0248fed388d10bd522a930efb582a0f5e827f66681ed8998",
    "lexical_build_identity": "1eab8db130daeef16c48f3185de2fd54466584884861af01cf01936472d80d04",
    "dense_build_identity": "3d38d7f72000222c074544ce1835a25e01c42461143de9c4f46aecb259710936",
    "dense_model_revision": "7999e1d3359715c523056ef9478215996d62a620",
    "dense_model_sha256": "354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026",
}


class CandidateSupplyError(ValueError):
    """Raised when the frozen candidate supply cannot be reproduced safely."""


def _read_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateSupplyError(f"{label} is unreadable: {path}") from exc
    if not isinstance(value, Mapping):
        raise CandidateSupplyError(f"{label} must be an object")
    return value


def _sha256_file(path: Path) -> str:
    try:
        return sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise CandidateSupplyError(f"required historical artifact is unreadable: {path}") from exc


def _candidate_sha(rows: Sequence[Mapping[str, Any]]) -> str:
    return sha256(canonical_json_bytes(list(rows))).hexdigest()


def _require_accepted_sha256(actual: str, expected: str, label: str) -> None:
    if actual != expected:
        raise CandidateSupplyError(f"{label} SHA-256 does not match the accepted external identity")


@contextmanager
def _dense_runtime_path(runtime_root: Path):
    original = list(sys.path)
    sys.path.insert(0, str(Path(runtime_root)))
    try:
        yield
    finally:
        sys.path[:] = original


def _historical_hashes(path: Path) -> dict[tuple[str, str], str]:
    artifact = _read_object(path, "70Q historical candidate-hash artifact")
    if artifact.get("schema_version") != "p04-rag-a1-2-70q-measure-0.1" or artifact.get("status") != "pass":
        raise CandidateSupplyError("70Q historical candidate-hash artifact is not the accepted measure")
    rows = artifact.get("rows")
    if not isinstance(rows, list):
        raise CandidateSupplyError("70Q historical candidate-hash artifact lacks rows")
    result: dict[tuple[str, str], str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise CandidateSupplyError("70Q historical candidate-hash row is invalid")
        question_id, mode = row.get("question_id"), row.get("mode")
        candidate = row.get("candidate_input")
        if question_id not in QUESTION_IDS or mode not in MODES or not isinstance(candidate, Mapping):
            raise CandidateSupplyError("70Q historical candidate-hash row has an unsupported binding")
        value = candidate.get("candidate_sha256")
        if candidate.get("candidate_count") != 20 or not isinstance(value, str) or len(value) != 64:
            raise CandidateSupplyError("70Q historical candidate-hash row lacks an exact Top20 hash")
        key = (question_id, mode)
        if key in result:
            raise CandidateSupplyError(f"duplicate historical candidate hash: {question_id} {mode}")
        result[key] = value
    expected = {(question_id, mode) for question_id in QUESTION_IDS for mode in MODES}
    if set(result) != expected:
        raise CandidateSupplyError("historical candidate hashes must cover lexical, Dense, and Hybrid for Q001-Q070")
    return result


def _historical_full_hybrid_rows(path: Path) -> dict[str, list[Mapping[str, Any]]]:
    artifact = _read_object(path, "22Q persisted candidate-row artifact")
    if artifact.get("schema_version") != "p04-rag-a1-2-unresolved-attribution-0.1":
        raise CandidateSupplyError("22Q persisted candidate-row artifact has an unsupported schema")
    questions = artifact.get("questions")
    if not isinstance(questions, list) or len(questions) != 22:
        raise CandidateSupplyError("22Q persisted candidate-row artifact must contain exactly 22 questions")
    result: dict[str, list[Mapping[str, Any]]] = {}
    for question in questions:
        if not isinstance(question, Mapping):
            raise CandidateSupplyError("22Q persisted candidate-row record is invalid")
        question_id, modes = question.get("question_id"), question.get("modes")
        if question_id not in QUESTION_IDS or not isinstance(modes, Mapping) or not isinstance(modes.get("hybrid"), Mapping):
            raise CandidateSupplyError("22Q persisted candidate-row binding is invalid")
        hybrid = modes["hybrid"]
        rows = hybrid.get("candidates_top20")
        if hybrid.get("candidate_count") != 20 or not isinstance(rows, list) or len(rows) != 20:
            raise CandidateSupplyError(f"22Q persisted Hybrid rows are incomplete: {question_id}")
        if not all(isinstance(row, Mapping) for row in rows) or question_id in result:
            raise CandidateSupplyError("22Q persisted Hybrid rows are invalid or duplicated")
        result[question_id] = list(rows)
    return result


def _validate_questions_and_m2_history(
    questions: Sequence[AcceptedQuestion],
    *,
    source_manifest_path: Path,
    m2_run_manifest_path: Path,
) -> dict[str, Any]:
    source = _read_object(source_manifest_path, "M2 source manifest")
    runtime = source.get("runtime_input")
    if source.get("status") != "prepared" or source.get("question_count") != 70 or not isinstance(runtime, Mapping):
        raise CandidateSupplyError("M2 source manifest is not an accepted 70-question runtime binding")
    expected_runtime_sha = runtime.get("sha256")
    if not isinstance(expected_runtime_sha, str) or len(expected_runtime_sha) != 64:
        raise CandidateSupplyError("M2 source manifest lacks the runtime question SHA-256")
    m2 = _read_object(m2_run_manifest_path, "historical M2 run manifest")
    if m2.get("schema_version") != "p04-rag-m2-0.1" or m2.get("status") != "complete" or m2.get("runtime_question_count") != 70:
        raise CandidateSupplyError("historical M2 run manifest is not complete")
    rows = m2.get("questions")
    if not isinstance(rows, list) or len(rows) != 70:
        raise CandidateSupplyError("historical M2 run manifest lacks 70 question identities")
    expected_identities = {row.get("question_id"): row.get("question_identity") for row in rows if isinstance(row, Mapping)}
    if set(expected_identities) != set(QUESTION_IDS):
        raise CandidateSupplyError("historical M2 run manifest question identities are incomplete")
    for question in questions:
        if expected_identities.get(question.question_id) != question.question_identity:
            raise CandidateSupplyError(f"historical M2 question identity mismatch: {question.question_id}")
    return {
        "source_manifest_sha256": _sha256_file(source_manifest_path),
        "m2_run_manifest_sha256": _sha256_file(m2_run_manifest_path),
        "expected_runtime_sha256": expected_runtime_sha,
        "historical_baseline": m2.get("baseline"),
    }


def _full_candidate_record(
    candidate: Mapping[str, Any], input_index: int, units_by_id: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Use the existing A1 public RU projection used by the persisted 22Q rows."""

    unit_id = str(candidate["unit_id"])
    retrieval = dict(candidate.get("retrieval", {}))
    try:
        public = assembly_module._unit_public(units_by_id[unit_id])
    except (KeyError, TypeError) as exc:
        raise CandidateSupplyError(f"candidate unit is absent from the accepted RU snapshot: {unit_id}") from exc
    return {
        "unit_id": unit_id,
        "rank": candidate["rank"],
        "input_index": input_index,
        "score": retrieval.get("score"),
        "retrieval": retrieval,
        "text": public["text"],
        "record_context": public["record_context"],
        "canonical_address": public["canonical_address"],
        "source_order": public["source_order"],
        "content_type": public["content_type"],
        "nested_selector": public["nested_selector"],
        "fragment_selector": public["fragment_selector"],
        "lineage": public["lineage"],
        "provenance": public["provenance"],
    }


def _immutable_write(path: Path, body: bytes) -> None:
    if path.exists():
        if path.read_bytes() != body:
            raise CandidateSupplyError(f"refusing to overwrite different candidate-supply artifact: {path}")
        return
    atomic_write(path, body)


def _write_manifest(path: Path, value: Mapping[str, Any]) -> None:
    """The state manifest is intentionally mutable only during explicit resume."""

    atomic_write(path, canonical_json_bytes(dict(value)))


def _run_identity(
    questions: Sequence[AcceptedQuestion],
    bindings: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> str:
    return sha256_json({
        "schema_version": SCHEMA_VERSION,
        "question_identities": [question.question_identity for question in questions],
        "historical_bindings": bindings,
        "baseline": baseline,
        "top_k": 20,
        "rrf_k": 60,
        "candidate_serialization": "canonical_json_bytes(candidate_windows)",
    })


def preflight_candidate_supply(
    runtime_input: Path = DEFAULT_M2_RUNTIME_INPUT,
    *,
    baseline: M1Baseline = M1Baseline(),
    source_manifest_path: Path = DEFAULT_M2_SOURCE_MANIFEST,
    m2_run_manifest_path: Path = DEFAULT_M2_RUN_MANIFEST,
    historical_hashes_path: Path = DEFAULT_CANDIDATE_HASHES,
    full_rows_path: Path = DEFAULT_FULL_ROWS,
) -> dict[str, Any]:
    """Validate all fixed inputs without creating an output root or running 70Q Retrieval."""

    runtime_input = Path(runtime_input)
    try:
        baseline_metadata = _baseline_metadata(baseline)
    except M1MeasureError as exc:
        raise CandidateSupplyError("Step 0 cannot start until accepted M2 Retrieval artifacts and model binding are ready") from exc
    if not Path(baseline.runtime_root).is_dir():
        raise CandidateSupplyError("Step 0 cannot start until the accepted local Dense runtime is available")
    for key, expected in ACCEPTED_BASELINE.items():
        if baseline_metadata.get(key) != expected:
            raise CandidateSupplyError(f"current baseline does not match the accepted external identity: {key}")
    try:
        actual_runtime_sha = _sha256_file(runtime_input)
        _require_accepted_sha256(actual_runtime_sha, ACCEPTED_RUNTIME_INPUT_SHA256, "runtime question input")
        candidate_hashes_sha256 = _sha256_file(Path(historical_hashes_path))
        _require_accepted_sha256(candidate_hashes_sha256, ACCEPTED_CANDIDATE_HASHES_SHA256, "70Q candidate-hash diagnostic")
        full_rows_sha256 = _sha256_file(Path(full_rows_path))
        _require_accepted_sha256(full_rows_sha256, ACCEPTED_FULL_ROWS_SHA256, "22Q attribution")
        questions = load_m2_runtime_questions(runtime_input)
        history = _validate_questions_and_m2_history(
            questions,
            source_manifest_path=Path(source_manifest_path),
            m2_run_manifest_path=Path(m2_run_manifest_path),
        )
        if actual_runtime_sha != history["expected_runtime_sha256"]:
            raise CandidateSupplyError("runtime question bytes do not match the accepted M2 source manifest")
        if history["expected_runtime_sha256"] != ACCEPTED_RUNTIME_INPUT_SHA256:
            raise CandidateSupplyError("M2 source manifest runtime SHA-256 does not match the accepted external identity")
        hashes = _historical_hashes(Path(historical_hashes_path))
        full_rows = _historical_full_hybrid_rows(Path(full_rows_path))
    except CandidateSupplyError:
        raise
    historical_baseline = history.get("historical_baseline")
    if not isinstance(historical_baseline, Mapping):
        raise CandidateSupplyError("historical M2 run lacks baseline binding")
    for key in ("retrieval_unit_build_identity", "lexical_build_identity", "dense_build_identity", "dense_model_revision", "dense_model_sha256"):
        if baseline_metadata.get(key) != historical_baseline.get(key):
            raise CandidateSupplyError(f"current baseline does not match historical M2 binding: {key}")
    bindings = {
        **history,
        "candidate_hashes_sha256": candidate_hashes_sha256,
        "full_rows_sha256": full_rows_sha256,
        "historical_hash_count": len(hashes),
        "full_hybrid_question_count": len(full_rows),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ready_to_materialize",
        "runtime_input": str(runtime_input),
        "runtime_input_sha256": actual_runtime_sha,
        "runtime_question_count": len(questions),
        "baseline": dict(baseline_metadata),
        "historical_bindings": bindings,
        "run_identity": _run_identity(questions, bindings, baseline_metadata),
        "provider_attempts_issued": 0,
        "generation_calls": 0,
    }


def _existing_completed(
    output_root: Path,
    run_identity: str,
    expected_hashes: Mapping[tuple[str, str], str],
    expected_full_hybrid: Mapping[str, list[Mapping[str, Any]]],
) -> set[str]:
    manifest_path = output_root / "metadata" / "manifest.json"
    existing = _read_object(manifest_path, "existing candidate-supply manifest")
    status = existing.get("status")
    if status == "accepted":
        raise CandidateSupplyError("candidate-supply output is already accepted; refusing to overwrite it")
    if status == "reproduction_failed":
        raise CandidateSupplyError("candidate-supply output has a reproduction failure and cannot be resumed")
    if status not in {"in_progress", "failed", "complete"}:
        raise CandidateSupplyError("existing candidate-supply output has an unsupported state")
    if existing.get("run_identity") != run_identity:
        raise CandidateSupplyError("existing partial output is bound to different inputs or historical evidence")
    completed = existing.get("completed_question_ids")
    if not isinstance(completed, list) or any(question_id not in QUESTION_IDS for question_id in completed):
        raise CandidateSupplyError("existing candidate-supply completion ledger is invalid")
    for question_id in completed:
        path = output_root / "candidates" / f"{question_id}.json"
        record = _read_object(path, "existing candidate-supply question artifact")
        if record.get("question_id") != question_id or record.get("run_identity") != run_identity:
            raise CandidateSupplyError(f"existing candidate-supply question artifact is not bound: {question_id}")
        hashes = record.get("candidate_hashes")
        windows = record.get("candidate_windows")
        if not isinstance(hashes, Mapping) or not isinstance(windows, Mapping):
            raise CandidateSupplyError(f"existing candidate-supply question artifact is incomplete: {question_id}")
        for mode in MODES:
            rows = windows.get(mode)
            if not isinstance(rows, list) or hashes.get(mode) != expected_hashes[(question_id, mode)] or _candidate_sha(rows) != hashes.get(mode):
                raise CandidateSupplyError(f"existing candidate-supply candidates do not match history: {question_id} {mode}")
        if question_id in expected_full_hybrid:
            full_rows = record.get("full_candidate_rows")
            if not isinstance(full_rows, Mapping) or full_rows.get("hybrid") != expected_full_hybrid[question_id]:
                raise CandidateSupplyError(f"existing candidate-supply Hybrid rows do not match history: {question_id}")
    return set(completed)


def materialize_candidate_supply(
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    *,
    runtime_input: Path = DEFAULT_M2_RUNTIME_INPUT,
    baseline: M1Baseline = M1Baseline(),
    source_manifest_path: Path = DEFAULT_M2_SOURCE_MANIFEST,
    m2_run_manifest_path: Path = DEFAULT_M2_RUN_MANIFEST,
    historical_hashes_path: Path = DEFAULT_CANDIDATE_HASHES,
    full_rows_path: Path = DEFAULT_FULL_ROWS,
    resume: bool = False,
) -> dict[str, Any]:
    """Run Retrieval only and write an immutable 70Q candidate supply after exact gates."""

    output_root = Path(output_root)
    preflight = preflight_candidate_supply(
        runtime_input,
        baseline=baseline,
        source_manifest_path=source_manifest_path,
        m2_run_manifest_path=m2_run_manifest_path,
        historical_hashes_path=historical_hashes_path,
        full_rows_path=full_rows_path,
    )
    questions = load_m2_runtime_questions(Path(runtime_input))
    expected_hashes = _historical_hashes(Path(historical_hashes_path))
    expected_full_hybrid = _historical_full_hybrid_rows(Path(full_rows_path))
    if output_root.exists():
        if not resume:
            raise FileExistsError("candidate-supply output root already exists; use --resume only for a verified partial run")
        completed = _existing_completed(
            output_root,
            str(preflight["run_identity"]),
            expected_hashes,
            expected_full_hybrid,
        )
    else:
        completed = set()
        output_root.mkdir(parents=True)

    manifest_path = output_root / "metadata" / "manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "in_progress",
        "run_identity": preflight["run_identity"],
        "runtime_input": preflight["runtime_input"],
        "runtime_input_sha256": preflight["runtime_input_sha256"],
        "baseline": preflight["baseline"],
        "historical_bindings": preflight["historical_bindings"],
        "modes": list(MODES),
        "top_k": 20,
        "bm25": {"k1": 1.2, "b": 0.75},
        "rrf_k": 60,
        "completed_question_ids": [question_id for question_id in QUESTION_IDS if question_id in completed],
        "reproduction": {"lexical": 0, "dense": 0, "hybrid": 0, "total": 0, "full_hybrid_rows": 0},
        "provider_attempts_issued": 0,
        "generation_calls": 0,
        "completed_question_count": len(completed),
    }
    _write_manifest(manifest_path, manifest)

    try:
        with _dense_runtime_path(baseline.runtime_root):
            retriever = load_batch_candidate_retriever(baseline.lexical_manifest, baseline.dense_manifest)
            model = load_dense_query_model(baseline.model_dir)
            ru_manifest, units = load_retrieval_units(baseline.retrieval_unit_manifest)
            if ru_manifest.get("build_identity") != retriever.lexical_manifest.get("retrieval_unit_build_identity"):
                raise CandidateSupplyError("accepted RU snapshot and W2 candidate indexes have different build identities")
            units_by_id = {str(unit["unit_id"]): unit for unit in units}
            instruction = retriever.dense_manifest.get("instruction")
            if not isinstance(instruction, str):
                raise CandidateSupplyError("accepted Dense artifact lacks query instruction")
            for question in questions:
                if question.question_id in completed:
                    continue
                vector = encode_dense_query(baseline.model_dir, question.question, instruction=instruction, model=model)
                windows = retriever.candidates_for_query(
                    question.question,
                    vector,
                    instruction=instruction,
                    top_k=20,
                    k1=1.2,
                    b=0.75,
                    rrf_k=60,
                )
                if set(windows) != set(MODES):
                    raise CandidateSupplyError(f"W2/M2 Retrieval returned unsupported modes for {question.question_id}")
                hashes: dict[str, str] = {}
                for mode in MODES:
                    rows = windows[mode]
                    if len(rows) != 20:
                        raise CandidateSupplyError(f"{question.question_id} {mode} did not return Top20")
                    actual = _candidate_sha(rows)
                    expected = expected_hashes[(question.question_id, mode)]
                    if actual != expected:
                        manifest["status"] = "reproduction_failed"
                        manifest["failure"] = {"question_id": question.question_id, "mode": mode, "expected_sha256": expected, "actual_sha256": actual}
                        _write_manifest(manifest_path, manifest)
                        raise CandidateSupplyError(f"historical candidate hash mismatch: {question.question_id} {mode}")
                    hashes[mode] = actual
                full_rows = {
                    mode: [_full_candidate_record(row, index, units_by_id) for index, row in enumerate(windows[mode])]
                    for mode in MODES
                }
                if question.question_id in expected_full_hybrid and full_rows["hybrid"] != expected_full_hybrid[question.question_id]:
                    manifest["status"] = "reproduction_failed"
                    manifest["failure"] = {"question_id": question.question_id, "mode": "hybrid", "reason": "raw_candidate_array_mismatch"}
                    _write_manifest(manifest_path, manifest)
                    raise CandidateSupplyError(f"historical raw Hybrid candidate array mismatch: {question.question_id}")
                record = {
                    "schema_version": SCHEMA_VERSION,
                    "run_identity": preflight["run_identity"],
                    "question_id": question.question_id,
                    "question_identity": question.question_identity,
                    "candidate_hashes": hashes,
                    "candidate_windows": windows,
                    "full_candidate_rows": full_rows,
                    "historical_full_hybrid_array_matched": question.question_id in expected_full_hybrid,
                }
                _immutable_write(output_root / "candidates" / f"{question.question_id}.json", canonical_json_bytes(record))
                completed.add(question.question_id)
                manifest["completed_question_ids"] = [question_id for question_id in QUESTION_IDS if question_id in completed]
                manifest["completed_question_count"] = len(completed)
                _write_manifest(manifest_path, manifest)
    except CandidateSupplyError as exc:
        if manifest["status"] == "in_progress":
            manifest["status"] = "failed"
            manifest["failure"] = {"reason": type(exc).__name__, "message": str(exc)}
            _write_manifest(manifest_path, manifest)
        raise
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failure"] = {"reason": type(exc).__name__, "message": str(exc)}
        _write_manifest(manifest_path, manifest)
        raise CandidateSupplyError("candidate-supply Retrieval stopped; partial artifacts remain inspectable") from exc

    if len(completed) != 70:
        raise CandidateSupplyError("candidate-supply run ended without all 70 questions")
    manifest["reproduction"] = {"lexical": 70, "dense": 70, "hybrid": 70, "total": 210, "full_hybrid_rows": len(expected_full_hybrid)}
    manifest["status"] = "complete"
    _write_manifest(manifest_path, manifest)
    manifest["status"] = "accepted"
    manifest.pop("failure", None)
    _write_manifest(manifest_path, manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize the frozen broader-admission 70Q candidate supply without Assembly or Generation")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "run"):
        command = commands.add_parser(name)
        command.add_argument("--runtime-input", type=Path, default=DEFAULT_M2_RUNTIME_INPUT)
        command.add_argument("--source-manifest", type=Path, default=DEFAULT_M2_SOURCE_MANIFEST)
        command.add_argument("--m2-run-manifest", type=Path, default=DEFAULT_M2_RUN_MANIFEST)
        command.add_argument("--historical-hashes", type=Path, default=DEFAULT_CANDIDATE_HASHES)
        command.add_argument("--full-rows", type=Path, default=DEFAULT_FULL_ROWS)
        if name == "run":
            command.add_argument("--output-root", required=True, type=Path)
            command.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    kwargs = {
        "runtime_input": args.runtime_input,
        "source_manifest_path": args.source_manifest,
        "m2_run_manifest_path": args.m2_run_manifest,
        "historical_hashes_path": args.historical_hashes,
        "full_rows_path": args.full_rows,
    }
    result = preflight_candidate_supply(**kwargs) if args.command == "preflight" else materialize_candidate_supply(args.output_root, resume=args.resume, **kwargs)
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
