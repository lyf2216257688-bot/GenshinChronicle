"""Bounded 70-question Qwen reranker plus Generation validation.

This is an experiment runner.  It reuses persisted Qwen query vectors and the
existing Retrieval, Formal Deferred, and Generation contracts; it does not
alter production defaults or Retrieval policy.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import gzip
import json
from pathlib import Path
from typing import Any

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.generation.generation import (
    BASELINE_QWEN_MODEL_ID,
    BailianControlConfig,
    BailianGenerationProvider,
    BailianOpenAICompatibleTransport,
    project_generation_request,
    workspace_from_bailian_base_url,
    write_generation_result,
)
from .candidate_retrieval import load_batch_candidate_retriever
from .evidence_assembly import (
    assemble_deferred_footprint_charge_packet,
    evidence_packet_json_bytes,
    prepare_evidence_assembly_context,
)
from .qwen_candidate_depth_diagnostic import DEFAULT_RU_MANIFEST, _read_object
from .qwen_m2_query_vectors import load_qwen_m2_query_vectors
from .qwen_rerank import (
    DashScopeQwenRerankConfig,
    DashScopeQwenRerankTransport,
    QWEN_RERANK_MODEL_ID,
    QwenRerankTransportError,
)
from .reranking import RerankCandidate, RerankRequest, project_ranked_candidates


QUESTION_IDS = tuple(f"Q{i:03d}" for i in range(1, 71))
R5_ROOT = Path(".local/p04-qwen-candidate-depth-20260912-r5")
QWEN_COMPARISON_ROOT = Path(".local/p04-rag-bge-qwen-dense-comparison-70q-20260910-172843")
QUERY_ROOT = Path(".local/p04-qwen-m2-query-vectors-beijing-20260910-162427")
SCHEMA_VERSION = "p04-rag-qwen-rerank-70q-0.1"
POOL_DEPTH = 500
TOP_K = 20


class QwenRerank70QError(ValueError):
    pass


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_hash(rows: list[Mapping[str, Any]]) -> str:
    return sha256(canonical_json_bytes(rows)).hexdigest()


def _write(root: Path, relative: str, value: Mapping[str, Any]) -> dict[str, Any]:
    body = canonical_json_bytes(dict(value))
    path = root / relative
    atomic_write(path, body)
    return {"path": str(path), "sha256": sha256(body).hexdigest(), "byte_count": len(body)}


def _persist_rerank_attempt(root: Path, question_id: str, *, status: str, scores: Any = None, metadata: Mapping[str, Any] | None = None, error: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Persist the provider outcome before any downstream status decision."""
    payload: dict[str, Any] = {"question_id": question_id, "status": status}
    if scores is not None:
        payload["scores"] = [
            {"unit_id": row.unit_id, "original_rank": row.original_rank,
             "rerank_rank": row.rerank_rank, "rerank_score": row.rerank_score}
            for row in scores
        ]
    if metadata:
        payload["provider"] = dict(metadata)
    if error:
        payload["error"] = dict(error)
    return _write(root, f"results/{question_id}-rerank-attempt.json", payload)


def _verify_r1_prefix(r1_root: Path, *, expected_manifest_sha256: str | None = None) -> dict[str, Any]:
    manifest_path = r1_root / "metadata/manifest.json"
    manifest_sha256 = _sha256_file(manifest_path)
    if expected_manifest_sha256 is not None and manifest_sha256 != expected_manifest_sha256:
        raise QwenRerank70QError("r1 manifest hash does not match continuation binding")
    manifest = _read_object(manifest_path, "r1 manifest")
    if manifest.get("run_identity") != "2e94eb3d12262cd1bddd824524a503f8127363345bb22c9b38dc85cb9b0c9d59":
        raise QwenRerank70QError("continuation requires the preserved blocked r1 run")
    hashes: dict[str, str] = {}
    for index in range(1, 45):
        qid = f"Q{index:03d}"
        path = r1_root / f"results/{qid}.json"
        if not path.is_file():
            raise QwenRerank70QError(f"r1 prefix artifact is missing: {qid}")
        hashes[qid] = _sha256_file(path)
    return {"root": str(r1_root), "run_identity": manifest["run_identity"], "manifest_sha256": manifest_sha256, "result_hashes": hashes}


def _load_questions() -> tuple[dict[str, str], list[dict[str, Any]], Any]:
    rows, vectors, manifest = load_qwen_m2_query_vectors(QUERY_ROOT)
    if manifest.get("status") != "complete" or len(rows) != 70 or getattr(vectors, "shape", None) != (70, 2048):
        raise QwenRerank70QError("accepted Qwen query-vector artifact is not complete 70x2048")
    questions = {str(row["question_id"]): str(row["question"]) for row in rows}
    if list(questions) != list(QUESTION_IDS):
        raise QwenRerank70QError("query-vector question ordering is not Q001-Q070")
    return questions, rows, vectors


def _load_unit_texts(candidate_ids: set[str], ru_manifest: Mapping[str, Any]) -> dict[str, str]:
    artifact = Path(DEFAULT_RU_MANIFEST).parent.parent / str(ru_manifest["artifacts"]["retrieval_units"]["path"])
    found: dict[str, str] = {}
    with gzip.open(artifact, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if isinstance(row, Mapping) and str(row.get("unit_id")) in candidate_ids:
                text = row.get("retrieval_visible_text")
                if not isinstance(text, str):
                    raise QwenRerank70QError("RU candidate lacks retrieval_visible_text")
                found[str(row["unit_id"])] = text
    if found.keys() != candidate_ids:
        raise QwenRerank70QError("candidate pool is not fully bound to the RU lineage snapshot")
    return found


def _packet_summary(packet: Mapping[str, Any], supplied: Mapping[str, Any] | None = None, candidates: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    summary = supplied or packet.get("packet_summary")
    if not isinstance(summary, Mapping):
        from . import broader_admission_comparison as comparison
        summary = comparison._arm_summary(packet, candidates or [])
    return {
        "packet_sha256": summary.get("packet_sha256"),
        "visible_unit_ids": list(summary.get("visible_unit_ids", [])),
        "visible_unit_count": len(summary.get("visible_unit_ids", [])),
        "used_context_chars": (summary.get("budget") or {}).get("used_context_chars"),
        "remaining_budget": 12000 - int((summary.get("budget") or {}).get("used_context_chars", 0)),
        "used_evidence_blocks": (summary.get("budget") or {}).get("used_evidence_blocks"),
        "omitted_blocks": (summary.get("budget") or {}).get("omitted_blocks"),
    }


def _generation_config(values: Mapping[str, str]) -> BailianControlConfig:
    endpoint = values.get("BAILIAN_BASE_URL")
    if not endpoint:
        raise QwenRerank70QError("BAILIAN_BASE_URL is required for the authorized 70Q run")
    return BailianControlConfig(
        region="cn-beijing", endpoint=endpoint,
        workspace=workspace_from_bailian_base_url(endpoint),
        model_id=BASELINE_QWEN_MODEL_ID, enable_thinking=False,
        max_output_tokens=2048, max_attempts=1,
    )


def run_qwen_rerank_70q(*, output_root: Path | None = None, environment: Mapping[str, str] | None = None, start_index: int = 0, r1_root: Path | None = None, r1_manifest_sha256: str | None = None) -> dict[str, Any]:
    values = environment or __import__("os").environ
    questions, query_rows, query_vectors = _load_questions()
    ru_manifest = _read_object(DEFAULT_RU_MANIFEST, "RU manifest")
    qwen_dense_manifest = Path(".local/p04-qwen-full-batch-beijing-20260909/final/dense/metadata/manifest.json")
    retriever = load_batch_candidate_retriever(
        Path("data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/lexical/metadata/manifest.json"),
        qwen_dense_manifest, accepted_qwen=True,
    )
    assembly_context = prepare_evidence_assembly_context(DEFAULT_RU_MANIFEST)
    generation_config = _generation_config(values)
    workspace = generation_config.workspace
    rerank_config = DashScopeQwenRerankConfig(
        region="cn-beijing",
        endpoint=f"https://{workspace}.cn-beijing.maas.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
        workspace=workspace,
    )
    reranker = DashScopeQwenRerankTransport.from_environment(rerank_config, environment=values)
    generation = BailianGenerationProvider(
        generation_config,
        BailianOpenAICompatibleTransport.from_environment(generation_config, environment=values),
    )
    source_hashes = {
        "runtime_input": _sha256_file(Path(".local/p04-rag-m2/questions.runtime.jsonl")),
        "query_manifest": _sha256_file(QUERY_ROOT / "metadata/manifest.json"),
        "ru_manifest": _sha256_file(DEFAULT_RU_MANIFEST),
        "qwen_dense_manifest": _sha256_file(qwen_dense_manifest),
        "rerank_module": _sha256_file(Path(__file__).with_name("qwen_rerank.py")),
        "runner": _sha256_file(Path(__file__)),
    }
    run_identity = sha256_json({"schema_version": SCHEMA_VERSION, "question_ids": list(QUESTION_IDS), "pool_depth": POOL_DEPTH, "top_k": TOP_K, "rrf_k": 60, "model": QWEN_RERANK_MODEL_ID, "generation_model": BASELINE_QWEN_MODEL_ID, "sources": source_hashes, "rerank_transport": rerank_config.identity_projection(), "generation_config": generation_config.output_affecting_projection()})
    if not 0 <= start_index <= len(QUESTION_IDS):
        raise QwenRerank70QError("invalid continuation start index")
    r1_binding = _verify_r1_prefix(Path(r1_root), expected_manifest_sha256=r1_manifest_sha256) if start_index else None
    root = Path(output_root) if output_root is not None else Path(f".local/p04-qwen-rerank-70q-{run_identity[:16]}")
    if root.exists():
        raise FileExistsError("70Q rerank artifact root exists; runs are immutable")
    root.mkdir(parents=True)
    manifest: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "status": "in_progress", "run_identity": run_identity, "question_ids": list(QUESTION_IDS), "pool_depth": POOL_DEPTH, "top_k": TOP_K, "models": {"reranker": QWEN_RERANK_MODEL_ID, "generation": BASELINE_QWEN_MODEL_ID}, "inputs": source_hashes, "retrieval": {"rrf_k": 60, "bm25": {"k1": 1.2, "b": 0.75}, "qwen_query_vectors": str(QUERY_ROOT), "embedding_calls": 0}, "provider_calls": {"rerank": 0, "generation_control": 0, "generation_challenger": 0}, "generation_config": generation_config.output_affecting_projection()}
    if r1_binding is not None:
        manifest["continuation"] = {"start_question_id": QUESTION_IDS[start_index], "r1": r1_binding}
    _write(root, "metadata/manifest.json", manifest)
    all_candidate_ids: set[str] = set()
    pools: dict[str, list[dict[str, Any]]] = {}
    for row_index, row in enumerate(query_rows):
        qid = str(row["question_id"])
        bundle = retriever.candidates_for_query(questions[qid], query_vectors[row_index], instruction=None, top_k=POOL_DEPTH, rrf_k=60)
        pools[qid] = list(bundle["hybrid"])
        all_candidate_ids.update(str(item["unit_id"]) for item in pools[qid])
    unit_texts = _load_unit_texts(all_candidate_ids, ru_manifest)
    control_comparison = {qid: _read_object(QWEN_COMPARISON_ROOT / "comparisons" / f"{qid}.json", f"accepted Qwen comparison {qid}") for qid in QUESTION_IDS}
    results: list[dict[str, Any]] = []
    try:
        for index, qid in enumerate(QUESTION_IDS[start_index:], start=start_index):
            pool = pools[qid]
            accepted = control_comparison[qid]
            control_candidates = list(accepted["qwen"]["hybrid_candidates"])
            if _candidate_hash(control_candidates) != accepted["qwen"]["hybrid_candidate_sha256"]:
                raise QwenRerank70QError(f"CONTROL Retrieval replay differs: {qid}")
            control_packet = accepted["qwen_packet"]["packet"]
            control_summary = _packet_summary(control_packet, accepted["qwen_packet"]["packet_summary"])
            if control_summary["packet_sha256"] != accepted["qwen_packet"]["packet_summary"]["packet_sha256"]:
                raise QwenRerank70QError(f"CONTROL Packet replay differs: {qid}")
            request = RerankRequest(qid, questions[qid], tuple(RerankCandidate(str(item["unit_id"]), unit_texts[str(item["unit_id"])], int(item["rank"])) for item in pool))
            manifest["provider_calls"]["rerank"] += 1
            try:
                raw_scores = reranker.rerank(request)
                _persist_rerank_attempt(root, qid, status="succeeded", scores=raw_scores, metadata=getattr(reranker, "last_response_metadata", {}))
            except QwenRerankTransportError as exc:
                _persist_rerank_attempt(root, qid, status="failed", metadata=getattr(reranker, "last_response_metadata", {}), error={"code": exc.code, "status_code": exc.status_code, "request_id": exc.request_id, "raw_response_sha256": sha256(exc.raw_response).hexdigest() if exc.raw_response else None})
                raise
            ranked = project_ranked_candidates(pool, raw_scores)
            challenger_candidates = ranked[:TOP_K]
            challenger_packet = assemble_deferred_footprint_charge_packet(DEFAULT_RU_MANIFEST, challenger_candidates, prepared_context=assembly_context, retrieval_audit={"query_id": qid, "query_text": questions[qid], "mode": "hybrid", "reranker": QWEN_RERANK_MODEL_ID})
            challenger_summary = _packet_summary(challenger_packet, candidates=challenger_candidates)
            control_request = project_generation_request(control_packet, question=questions[qid], question_id=qid)
            challenger_request = project_generation_request(challenger_packet, question=questions[qid], question_id=qid)
            control_result = generation.generate(control_request)
            manifest["provider_calls"]["generation_control"] += 1
            control_generation_artifact = write_generation_result(root / qid / "control" / "generation", control_result)
            challenger_result = generation.generate(challenger_request)
            manifest["provider_calls"]["generation_challenger"] += 1
            challenger_generation_artifact = write_generation_result(root / qid / "challenger" / "generation", challenger_result)
            if control_result.execution_status != "succeeded" or challenger_result.execution_status != "succeeded":
                raise QwenRerank70QError(f"Generation did not succeed for {qid}")
            carrier_ids = set()
            if qid in {"Q005", "Q011", "Q045", "Q046", "Q049"}:
                carrier_ids = {str(x["unit_id"]) for x in _read_object(R5_ROOT / "results" / f"{qid}-top-500.json", f"r5 {qid}").get("carrier_outcomes", [])}
            movements = [{"unit_id": item["unit_id"], "original_hybrid_rank": int(item["original_hybrid_rank"]), "rerank_rank": int(item["rerank_rank"]), "rerank_score": item["rerank_score"], "is_known_carrier": item["unit_id"] in carrier_ids, "rerank_top20": int(item["rerank_rank"]) <= TOP_K} for item in ranked if item["unit_id"] in carrier_ids]
            control_visible = set(control_summary["visible_unit_ids"])
            challenger_visible = set(challenger_summary["visible_unit_ids"])
            record = {"question_id": qid, "question_identity": sha256_json({"question_id": qid, "question": questions[qid]}), "input_candidate_hash": _candidate_hash(pool), "control": {"candidate_hash": _candidate_hash(control_candidates), "packet": control_summary, "generation": control_generation_artifact}, "challenger": {"candidate_hash": _candidate_hash(challenger_candidates), "packet": challenger_summary, "generation": challenger_generation_artifact, "rerank_provider": dict(getattr(reranker, "last_response_metadata", {}))}, "carrier_movements": movements, "displaced_control_top20": sorted(control_visible - challenger_visible), "packet_added": sorted(challenger_visible - control_visible), "packet_removed": sorted(control_visible - challenger_visible)}
            _write(root, f"results/{qid}.json", record)
            atomic_write(root / qid / "control" / "packet.json", evidence_packet_json_bytes(control_packet))
            atomic_write(root / qid / "challenger" / "packet.json", evidence_packet_json_bytes(challenger_packet))
            _write(root, f"candidates/{qid}.json", {"question_id": qid, "hybrid_top500": pool, "reranked_top500": ranked})
            results.append(record)
            _write(root, "metadata/manifest.json", manifest)
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        _write(root, "metadata/manifest.json", manifest)
        raise
    manifest["status"] = "complete"
    manifest["result_count"] = len(results)
    _write(root, "metadata/manifest.json", manifest)
    comparison = {"schema_version": SCHEMA_VERSION, "run_identity": run_identity, "questions": [{"question_id": r["question_id"], "control_packet_sha256": r["control"]["packet"]["packet_sha256"], "challenger_packet_sha256": r["challenger"]["packet"]["packet_sha256"], "control_generation_status": "succeeded", "challenger_generation_status": "succeeded", "carrier_movements": r["carrier_movements"], "packet_added": r["packet_added"], "packet_removed": r["packet_removed"]} for r in results]}
    _write(root, "metadata/comparison.json", comparison)
    return manifest


def continue_qwen_rerank_70q(*, output_root: Path, r1_root: Path = Path(".local/p04-qwen-rerank-70q-20260912-r1"), environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Continue only the preserved r1 prefix, beginning with Q045."""
    binding = _verify_r1_prefix(Path(r1_root))
    return run_qwen_rerank_70q(
        output_root=output_root,
        environment=environment,
        start_index=44,
        r1_root=Path(r1_root),
        r1_manifest_sha256=binding["manifest_sha256"],
    )
