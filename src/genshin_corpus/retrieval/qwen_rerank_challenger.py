"""Five-question, provider-backed Qwen text-rerank challenger diagnostic."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import gzip
from pathlib import Path
import os
from urllib.parse import urlsplit
from typing import Any

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from .evidence_assembly import assemble_deferred_footprint_charge_packet, evidence_packet_json_bytes, prepare_evidence_assembly_context
from .qwen_candidate_depth_diagnostic import DEFAULT_RU_MANIFEST, QUESTION_IDS, _config_projection, _direct_root_ids, _formal_displacements, _read_object
from .qwen_rerank import DashScopeQwenRerankConfig, DashScopeQwenRerankTransport, QWEN_RERANK_MODEL_ID, QwenRerankTransportError
from .reranking import RerankCandidate, RerankRequest, project_ranked_candidates
from .retrieval_units import load_retrieval_units


R5_ROOT = Path(".local/p04-qwen-candidate-depth-20260912-r5")
SCHEMA_VERSION = "p04-qwen-rerank-challenger-0.1"
TOP_K = 20
POOL_DEPTH = 500


class QwenRerankChallengerError(ValueError):
    pass


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(root: Path, relative: str, value: Mapping[str, Any]) -> dict[str, Any]:
    body = canonical_json_bytes(dict(value))
    path = root / relative
    atomic_write(path, body)
    return {"path": str(path), "sha256": sha256(body).hexdigest(), "byte_count": len(body)}


def _load_r5() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = _read_object(R5_ROOT / "metadata/manifest.json", "r5 manifest")
    if manifest.get("status") != "complete" or manifest.get("question_ids") != list(QUESTION_IDS):
        raise QwenRerankChallengerError("r5 artifact is not the accepted complete five-question run")
    rows: dict[str, dict[str, Any]] = {}
    for question_id in QUESTION_IDS:
        row = _read_object(R5_ROOT / "results" / f"{question_id}-top-500.json", "r5 Top500 result")
        if row.get("question_id") != question_id or row.get("depth") != 500:
            raise QwenRerankChallengerError(f"r5 result binding mismatch: {question_id}")
        if not isinstance(row.get("candidate_windows"), Mapping):
            raise QwenRerankChallengerError(f"r5 candidate windows missing: {question_id}")
        rows[question_id] = row
    return manifest, rows


def _candidate_hash(rows: list[Mapping[str, Any]]) -> str:
    from . import broader_admission_comparison as comparison
    return comparison._candidate_sha(rows)


def _packet_summary(packet: Mapping[str, Any], candidates: list[Mapping[str, Any]]) -> dict[str, Any]:
    from . import broader_admission_comparison as comparison
    summary = comparison._arm_summary(packet, candidates)
    summary["direct_root_ids"] = _direct_root_ids(packet)
    summary["formal_displacements"] = _formal_displacements(packet)
    return summary


def _workspace_from_base(base_url: str) -> str:
    host = (urlsplit(base_url).hostname or "").lower()
    suffix = ".cn-beijing.maas.aliyuncs.com"
    if not host.endswith(suffix):
        raise QwenRerankChallengerError("BAILIAN_BASE_URL must be a workspace-owned Beijing endpoint")
    workspace = host[: -len(suffix)]
    if not workspace:
        raise QwenRerankChallengerError("BAILIAN_BASE_URL has no workspace")
    return workspace


def run_qwen_rerank_challenger(*, output_root: Path | None = None, transport: Any | None = None) -> dict[str, Any]:
    r5_manifest, r5_rows = _load_r5()
    ru_manifest = _read_object(DEFAULT_RU_MANIFEST, "RU manifest")
    candidate_ids = {str(row["unit_id"]) for result in r5_rows.values() for row in result["candidate_windows"]["hybrid"]}
    ru_artifact = Path(DEFAULT_RU_MANIFEST).parent.parent / str(ru_manifest["artifacts"]["retrieval_units"]["path"])
    units_by_id: dict[str, Mapping[str, Any]] = {}
    with gzip.open(ru_artifact, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if isinstance(row, Mapping) and str(row.get("unit_id")) in candidate_ids:
                units_by_id[str(row["unit_id"])] = row
    for question_id, result in r5_rows.items():
        for row in result["candidate_windows"]["hybrid"]:
            if str(row["unit_id"]) not in units_by_id:
                raise QwenRerankChallengerError(f"r5 candidate is not bound to RU snapshot: {question_id}")
    base = os.environ.get("BAILIAN_BASE_URL", "")
    workspace = _workspace_from_base(base)
    config = DashScopeQwenRerankConfig(region="cn-beijing", endpoint=f"https://{workspace}.cn-beijing.maas.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank", workspace=workspace)
    provider = transport or DashScopeQwenRerankTransport.from_environment(config)
    runner_source = Path(__file__)
    run_identity = sha256_json({"schema_version": SCHEMA_VERSION, "r5_manifest_sha256": _sha256_file(R5_ROOT / "metadata/manifest.json"), "r5_run_identity": r5_manifest["run_identity"], "question_ids": list(QUESTION_IDS), "pool_depth": POOL_DEPTH, "top_k": TOP_K, "model": QWEN_RERANK_MODEL_ID, "transport": config.identity_projection(), "runner_sha256": _sha256_file(runner_source), "reranking_sha256": _sha256_file(runner_source.parent / "reranking.py"), "ru_build_identity": ru_manifest["build_identity"]})
    root = Path(output_root) if output_root is not None else Path(f".local/p04-qwen-rerank-{run_identity[:16]}")
    if root.exists():
        raise FileExistsError("rerank challenger output root already exists; runs are immutable")
    root.mkdir(parents=True)
    manifest: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "status": "in_progress", "run_identity": run_identity, "r5_run_identity": r5_manifest["run_identity"], "question_ids": list(QUESTION_IDS), "pool_depth": POOL_DEPTH, "top_k": TOP_K, "model": QWEN_RERANK_MODEL_ID, "transport": config.identity_projection(), "input": {"r5_manifest": {"path": str(R5_ROOT / "metadata/manifest.json"), "sha256": _sha256_file(R5_ROOT / "metadata/manifest.json")}, "ru_manifest": {"path": str(DEFAULT_RU_MANIFEST), "sha256": _sha256_file(DEFAULT_RU_MANIFEST)}}, "provider_calls": 0, "generation_calls": 0, "embedding_calls": 0}
    _write(root, "metadata/manifest.json", manifest)
    from .qwen_m2_query_vectors import load_qwen_m2_query_vectors
    qrows, _, _ = load_qwen_m2_query_vectors(Path(".local/p04-qwen-m2-query-vectors-beijing-20260910-162427"))
    queries = {row["question_id"]: str(row["question"]) for row in qrows}
    results: list[dict[str, Any]] = []
    try:
        for question_id in QUESTION_IDS:
            source = r5_rows[question_id]
            r5_control = _read_object(R5_ROOT / "results" / f"{question_id}-top-020.json", "r5 Top20 result")
            pool = list(source["candidate_windows"]["hybrid"])
            control = list(r5_control["candidate_windows"]["hybrid"])
            if _candidate_hash(control) != r5_control["candidate_hashes"]["hybrid"]:
                raise QwenRerankChallengerError(f"CONTROL candidate replay differs: {question_id}")
            # The accepted r5 Top20 Packet is the replay control artifact. Its
            # immutable hash/visibility is validated above and reused verbatim.
            control_packet = _read_object(R5_ROOT / "packets" / question_id / "top-020.json", "r5 control Packet")
            control_summary = dict(r5_control["packet_summary"])
            if control_summary["packet_sha256"] != r5_control["packet_summary"]["packet_sha256"] or control_summary["visible_unit_ids"] != r5_control["packet_summary"]["visible_unit_ids"]:
                raise QwenRerankChallengerError(f"CONTROL Packet replay differs: {question_id}")
            request = RerankRequest(question_id, queries[question_id], tuple(RerankCandidate(str(row["unit_id"]), str(units_by_id[str(row["unit_id"])] ["retrieval_visible_text"]), int(row["rank"])) for row in pool))
            manifest["provider_calls"] += 1
            scores = provider.rerank(request)
            reranked = project_ranked_candidates(pool, scores)
            challenger = reranked[:TOP_K]
            context = prepare_evidence_assembly_context(DEFAULT_RU_MANIFEST)
            challenger_packet = assemble_deferred_footprint_charge_packet(DEFAULT_RU_MANIFEST, challenger, prepared_context=context, retrieval_audit={"query_id": question_id, "mode": "hybrid", "candidate_supply": "r5_top500", "reranker": QWEN_RERANK_MODEL_ID})
            challenger_summary = _packet_summary(challenger_packet, challenger)
            carrier_ids = {str(row["unit_id"]) for row in source.get("carrier_outcomes", [])}
            movements = []
            for row in pool:
                if str(row["unit_id"]) in carrier_ids:
                    new_rank = next((int(item["rerank_rank"]) for item in reranked if item["unit_id"] == row["unit_id"]), None)
                    movements.append({"unit_id": row["unit_id"], "original_hybrid_rank": int(row["rank"]), "rerank_rank": new_rank, "rerank_top20": new_rank is not None and new_rank <= TOP_K, "control_packet_visible": row["unit_id"] in control_summary["visible_unit_ids"], "challenger_packet_visible": row["unit_id"] in challenger_summary["visible_unit_ids"]})
            record = {"schema_version": SCHEMA_VERSION, "run_identity": run_identity, "question_id": question_id, "input_candidate_count": len(pool), "input_candidate_hash": _candidate_hash(pool), "control": {"candidate_hash": _candidate_hash(control), "packet": control_summary}, "challenger": {"candidate_hash": _candidate_hash(challenger), "packet": challenger_summary, "rerank_scores": [{"unit_id": row["unit_id"], "original_hybrid_rank": row["original_hybrid_rank"], "rerank_rank": row["rerank_rank"], "rerank_score": row["rerank_score"]} for row in reranked]}, "carrier_movements": movements, "displaced_control_top20": [row["unit_id"] for row in control if row["unit_id"] not in challenger_summary["visible_unit_ids"]], "provider": getattr(provider, "last_response_metadata", {"model": QWEN_RERANK_MODEL_ID})}
            _write(root, f"results/{question_id}.json", record)
            atomic_write(root / f"packets/{question_id}/control.json", evidence_packet_json_bytes(control_packet))
            atomic_write(root / f"packets/{question_id}/challenger.json", evidence_packet_json_bytes(challenger_packet))
            results.append(record)
            _write(root, "metadata/manifest.json", manifest)
    except QwenRerankTransportError as exc:
        manifest["status"] = "failed"
        manifest["failure"] = {"code": exc.code, "status_code": exc.status_code, "request_id": exc.request_id, "raw_response_sha256": sha256(exc.raw_response).hexdigest() if exc.raw_response else None}
        if exc.raw_response:
            atomic_write(root / "metadata/provider_error_response.bin", exc.raw_response)
        _write(root, "metadata/manifest.json", manifest)
        raise
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failure"] = {"code": type(exc).__name__, "message": str(exc)}
        _write(root, "metadata/manifest.json", manifest)
        raise
    manifest["status"] = "complete"
    manifest["result_count"] = len(results)
    _write(root, "metadata/manifest.json", manifest)
    comparison = {"schema_version": SCHEMA_VERSION, "run_identity": run_identity, "questions": [{"question_id": row["question_id"], "carrier_movements": row["carrier_movements"], "control_packet_sha256": row["control"]["packet"]["packet_sha256"], "challenger_packet_sha256": row["challenger"]["packet"]["packet_sha256"], "control_visible_unit_ids": row["control"]["packet"]["visible_unit_ids"], "challenger_visible_unit_ids": row["challenger"]["packet"]["visible_unit_ids"], "displaced_control_top20": row["displaced_control_top20"]} for row in results]}
    _write(root, "metadata/comparison.json", comparison)
    return manifest
