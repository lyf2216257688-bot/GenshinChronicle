"""Provider-free targeted GTE structural probe over persisted current-C Hybrid500."""

from __future__ import annotations

import argparse
from contextlib import AbstractContextManager
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
from time import perf_counter
from typing import Any, Mapping, Sequence

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.rag.backend import _reranker_fields_from_verified_ru
from genshin_corpus.retrieval.evidence_assembly import (
    EvidenceAssemblyConfig,
    assemble_deferred_footprint_charge_packet,
    evidence_packet_json_bytes,
    prepare_evidence_assembly_context,
)
from genshin_corpus.retrieval.gte_multilingual_reranker import (
    CUSTOM_CODE_REVISION,
    MODEL_ID,
    MODEL_REVISION,
    GteMultilingualReranker,
    GteMultilingualRerankerConfig,
    prepare_pinned_model_stage,
)
from genshin_corpus.retrieval.rerank_fusion_challenger import (
    Q056_COMPETING,
    Q056_DECISIVE,
    Q068_COMPETING,
    Q068_DECISIVE,
)
from genshin_corpus.retrieval.reranking import (
    RankFusionConfig,
    RerankCandidate,
    RerankRequest,
    fuse_ranked_candidates,
    project_ranked_candidates,
    project_reranker_text,
)


SCHEMA_VERSION = "phase04-gte-reranker-targeted-structural-probe-v1"
QUESTION_IDS = ("Q011", "Q042", "Q049", "Q052", "Q058", "Q060", "Q068", "Q005", "Q045", "Q046", "Q056")
CLEAN_QUESTION_IDS = tuple(qid for qid in QUESTION_IDS if qid != "Q056")
DIAGNOSTIC_ONLY = ("Q056",)
CURRENT_C_RUN_IDENTITY = "4d09456a30470dc679b7d24f1403f687742568a916a5837e6b97e8953f523c40"
CURRENT_C_MANIFEST_SHA256 = "a9e67f58570e1f359c15b1e2f08b7d83961061ab23f8e3bf353066609c638359"
RU_MANIFEST_SHA256 = "dc6bbd30cc6fbc83fa38132085fb8550f20322082467fe24aadcb4239ca78673"
R7_MANIFEST_SHA256 = "f08c8cbfb3319cf15452f124f84e789d86bafe8cfeb0889b8f6db9ddbdebb8a5"
R7_RESULT_SHA256 = {
    "Q005": "dabc127c53c2ad50064aabc30d00acb0f81b4c9d2ab396ebfbbd6a83742b4857",
    "Q011": "17061a01be6322def275838e324536e83bff8dc168dcf6fc53edd9f6f0609740",
    "Q045": "fb20e6d1f9658e93c1e4da7fec48fb405ec0a629f8478f0724c9891a837c8156",
    "Q046": "bb9972f500b56b4e123bce9355e4fe00993042f63754b31b8f119e3d6e104828",
    "Q049": "4eb3b13ca877fbe22adb847975731144a0dda8dffddcedb4d4a81b75b44dd5bb",
}
EXPECTED_R7_VISIBLE_CARRIERS = {
    "Q005": ("1b478e74c37ad55548b1527adf2f2035410d4070c08bc62ecc4cf34daf853c24", "b5cc672f533d01860d88ec0b7d298965762a4951e4fa20f440d6d2f550872469"),
    "Q011": ("8d9c66927fba247be9df8bc9f97e99c1127c6f5402e8ab645713f688d2b3ce27", "e9be68b0d686a3a38875e3ce5bddd1cd927b9c1b81e162f31f1a313b7c4b9c4c"),
    "Q045": ("a7921260b262ff541c5a94fac0573992fde2499986fee9cc0aebe4a833cdf1f3", "f0e5d0892ceca89394143c5d16e567b0adf6afaa945fa20f0a55206b2d15e35b", "e943d3b8a8c3ee3a222ab8b46c7dadd3be70e688e2015684ddf52bc7ee18debb"),
    "Q046": ("b7d7ff8044e3599df322ed61ad4ce8d2b0d9fa911a46f2c9da3465e3a462c168", "fc89c303dc8aec74fbd0db11240db7c8ebbab838f5315d96a2596252dd55739b"),
    "Q049": ("51146bf44f9dab34092a4d9c44d609cae0a20b3b39ca1c30d61b855ed7c4f28d",),
}
FUSION = RankFusionConfig(hybrid_weight=0.35, rerank_weight=0.65, denominator=60)


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _descriptor(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "byte_count": path.stat().st_size, "sha256": _sha256_file(path)}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return value


def _write_json(path: Path, value: Any) -> dict[str, Any]:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, body)
    return {"path": str(path.resolve()), "byte_count": len(body), "sha256": sha256(body).hexdigest()}


def _write_packet(path: Path, packet: Mapping[str, Any]) -> dict[str, Any]:
    body = evidence_packet_json_bytes(packet)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, body)
    return {"path": str(path.resolve()), "byte_count": len(body), "sha256": sha256(body).hexdigest()}


def _git_snapshot(repo: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout
    return {
        "head": run("rev-parse", "HEAD").strip(),
        "log_one_line": run("log", "-1", "--oneline").strip(),
        "status_short_untracked_all": run("status", "--short", "--untracked-files=all"),
    }


class _NetworkGuard(AbstractContextManager["_NetworkGuard"]):
    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._originals: tuple[Any, ...] | None = None

    def __enter__(self) -> "_NetworkGuard":
        self._originals = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)
        def blocked(_socket: Any, address: Any, *_args: Any, **_kwargs: Any) -> Any:
            self.attempts.append(repr(address))
            raise RuntimeError(f"network attempt blocked: {address!r}")
        def blocked_create(address: Any, *_args: Any, **_kwargs: Any) -> Any:
            self.attempts.append(repr(address))
            raise RuntimeError(f"network attempt blocked: {address!r}")
        socket.socket.connect = blocked
        socket.socket.connect_ex = blocked
        socket.create_connection = blocked_create
        return self

    def __exit__(self, *_args: Any) -> None:
        assert self._originals is not None
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = self._originals


def _visible_unit_ids(packet: Mapping[str, Any]) -> list[str]:
    return list(dict.fromkeys(
        str(member["unit_id"])
        for block in packet.get("evidence", [])
        if isinstance(block, Mapping)
        for member in block.get("members", [])
        if isinstance(member, Mapping) and isinstance(member.get("unit_id"), str)
    ))


def _rank_map(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    return {str(row["unit_id"]): int(row[key]) for row in rows}


def _load_carrier_bindings(repo: Path) -> tuple[dict[str, list[dict[str, str]]], dict[str, Any]]:
    r7 = repo / ".local/p04-qwen-rerank-20260912-r7"
    manifest = r7 / "metadata/manifest.json"
    if _sha256_file(manifest) != R7_MANIFEST_SHA256:
        raise ValueError("accepted R7 carrier manifest hash mismatch")
    bindings: dict[str, list[dict[str, str]]] = {}
    sources: dict[str, Any] = {"manifest": _descriptor(manifest), "results": {}}
    for qid, expected_hash in R7_RESULT_SHA256.items():
        path = r7 / f"results/{qid}.json"
        if _sha256_file(path) != expected_hash:
            raise ValueError(f"accepted R7 carrier result hash mismatch: {qid}")
        result = _read_json(path)
        rows = result.get("carrier_movements")
        if not isinstance(rows, list):
            raise ValueError(f"accepted R7 carrier rows missing: {qid}")
        ids = tuple(str(row["unit_id"]) for row in rows if isinstance(row, Mapping) and row.get("challenger_packet_visible") is True)
        if ids != EXPECTED_R7_VISIBLE_CARRIERS[qid]:
            raise ValueError(f"accepted R7 visible carrier binding changed: {qid}")
        bindings[qid] = [{"unit_id": unit_id, "binding_role": "accepted_carrier", "carrier_label": "UNKNOWN"} for unit_id in ids]
        sources["results"][qid] = _descriptor(path)
    bindings["Q056"] = [
        {"unit_id": Q056_DECISIVE, "binding_role": "decisive", "carrier_label": "UNKNOWN"},
        {"unit_id": Q056_COMPETING, "binding_role": "competing", "carrier_label": "UNKNOWN"},
    ]
    bindings["Q068"] = [
        {"unit_id": Q068_DECISIVE, "binding_role": "decisive", "carrier_label": "UNKNOWN"},
        {"unit_id": Q068_COMPETING, "binding_role": "competing", "carrier_label": "UNKNOWN"},
    ]
    sources["q056_q068_binding_source"] = _descriptor(repo / "src/genshin_corpus/retrieval/rerank_fusion_challenger.py")
    return bindings, sources


def _carrier_delta(
    bindings: Sequence[Mapping[str, str]] | None,
    hybrid: Sequence[Mapping[str, Any]],
    online_reranked: Sequence[Mapping[str, Any]],
    online_fused: Sequence[Mapping[str, Any]],
    gte_reranked: Sequence[Mapping[str, Any]],
    gte_fused: Sequence[Mapping[str, Any]],
    online_visible: set[str],
    gte_visible: set[str],
) -> tuple[str, list[dict[str, Any]]]:
    if not bindings:
        return "UNKNOWN", []
    maps = {
        "hybrid": _rank_map(hybrid, "rank"),
        "online_rerank": _rank_map(online_reranked, "rerank_rank"),
        "online_fusion": _rank_map(online_fused, "fusion_rank"),
        "gte_rerank": _rank_map(gte_reranked, "rerank_rank"),
        "gte_fusion": _rank_map(gte_fused, "fusion_rank"),
    }
    rows: list[dict[str, Any]] = []
    for binding in bindings:
        unit_id = str(binding["unit_id"])
        rows.append({
            **dict(binding),
            "hybrid_rank": maps["hybrid"].get(unit_id),
            "online_rerank_rank": maps["online_rerank"].get(unit_id),
            "online_fusion_rank": maps["online_fusion"].get(unit_id),
            "online_final20": maps["online_fusion"].get(unit_id, 10**9) <= 20,
            "online_packet_present": unit_id in online_visible,
            "gte_rerank_rank": maps["gte_rerank"].get(unit_id),
            "gte_fusion_rank": maps["gte_fusion"].get(unit_id),
            "gte_final20": maps["gte_fusion"].get(unit_id, 10**9) <= 20,
            "gte_packet_present": unit_id in gte_visible,
        })
    unknown = any(row["hybrid_rank"] is None for row in rows)
    lost = any(row["online_packet_present"] and not row["gte_packet_present"] for row in rows)
    improved = any(not row["online_packet_present"] and row["gte_packet_present"] for row in rows)
    if lost:
        judgment = "lost"
    elif unknown:
        judgment = "UNKNOWN"
    elif improved:
        judgment = "improved"
    else:
        judgment = "preserved"
    return judgment, rows


def _validate_current_c(repo: Path) -> tuple[dict[str, Any], Path, Path]:
    root = repo / ".local/p04-block-a-paid-c-only-20260915-6583536f"
    manifest_path = root / "metadata/manifest.json"
    if _sha256_file(manifest_path) != CURRENT_C_MANIFEST_SHA256:
        raise ValueError("accepted current-C manifest hash mismatch")
    manifest = _read_json(manifest_path)
    if manifest.get("status") != "complete" or manifest.get("run_identity") != CURRENT_C_RUN_IDENTITY:
        raise ValueError("accepted current-C terminal identity mismatch")
    ru_manifest = repo / "data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/ru/metadata/manifest.json"
    if _sha256_file(ru_manifest) != RU_MANIFEST_SHA256:
        raise ValueError("accepted RU manifest hash mismatch")
    return manifest, root, ru_manifest


def _load_baseline(root: Path, qid: str) -> tuple[dict[str, Any], Path, Path]:
    result_path = root / f"results/{qid}/vnext/{qid}/rag_result.json"
    packet_path = root / f"results/{qid}/vnext/{qid}/packet/evidence_packet.json"
    result = _read_json(result_path)
    trace = result.get("rerank_trace", {})
    config = result.get("audit", {}).get("config", {})
    if result.get("status") != "succeeded" or len(trace.get("before", [])) != 500 or len(trace.get("reranked", [])) != 500 or len(trace.get("after", [])) != 500:
        raise ValueError(f"accepted current-C ranking evidence incomplete: {qid}")
    if trace.get("provider", {}).get("model") != "qwen3.7-text-rerank":
        raise ValueError(f"accepted current-C online reranker mismatch: {qid}")
    expected_config = {"candidate_supply_depth": 500, "rerank_depth": 500, "final_top_n": 20, "reranker_projection_max_chars": 6000, "rrf_k": 60}
    if any(config.get(key) != value for key, value in expected_config.items()) or config.get("fusion", {}).get("identity") != FUSION.identity:
        raise ValueError(f"accepted current-C operating point mismatch: {qid}")
    if [int(row["rank"]) for row in trace["before"]] != list(range(1, 501)):
        raise ValueError(f"accepted current-C Hybrid500 ranks invalid: {qid}")
    if _sha256_file(packet_path) != sha256(evidence_packet_json_bytes(result["evidence_packet"])).hexdigest():
        raise ValueError(f"accepted current-C Packet/result mismatch: {qid}")
    return result, result_path, packet_path


def _prepare_run_root(root: Path) -> None:
    if not root.is_dir():
        raise FileNotFoundError("target run root must contain the pre-modification Git snapshot")
    allowed = {"pre_modification_git_snapshot.txt"}
    existing = {path.name for path in root.iterdir()}
    if existing != allowed:
        raise FileExistsError(f"refusing completed or ambiguous target run root: {root}")


def run_probe(repo: Path, output_root: Path) -> dict[str, Any]:
    repo = repo.resolve()
    output_root = output_root.resolve()
    _prepare_run_root(output_root)
    manifest_path = output_root / "manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "in_progress",
        "question_ids": list(QUESTION_IDS),
        "clean_question_ids": list(CLEAN_QUESTION_IDS),
        "diagnostic_only_question_ids": list(DIAGNOSTIC_ONLY),
        "provider_api_calls": 0,
        "network_calls": 0,
        "new_generation_calls": 0,
        "upstream_retrieval_calls": 0,
        "production_behavior_changes": 0,
        "git_before_source_edits": (output_root / "pre_modification_git_snapshot.txt").read_text(encoding="utf-8"),
        "git_at_execution": _git_snapshot(repo),
    }
    _write_json(manifest_path, manifest)
    guard = _NetworkGuard()
    try:
        current_manifest, current_root, ru_manifest = _validate_current_c(repo)
        carrier_bindings, carrier_sources = _load_carrier_bindings(repo)
        baselines: dict[str, tuple[dict[str, Any], Path, Path]] = {}
        input_bindings: dict[str, Any] = {}
        for qid in QUESTION_IDS:
            result, result_path, packet_path = _load_baseline(current_root, qid)
            baselines[qid] = (result, result_path, packet_path)
            input_bindings[qid] = {"accepted_online_result": _descriptor(result_path), "accepted_online_packet": _descriptor(packet_path)}
        model_source = repo / f".local/models/gte-multilingual-reranker-base/{MODEL_REVISION}"
        custom_source = repo / f".local/models/gte-multilingual-reranker-base/new-impl/{CUSTOM_CODE_REVISION}"
        os.environ["HF_HOME"] = str(output_root / "runtime/hf-home")
        os.environ["HF_MODULES_CACHE"] = str(output_root / "runtime/hf-modules")
        with guard:
            stage = prepare_pinned_model_stage(model_source, custom_source, output_root / "runtime/staging-model")
            config = GteMultilingualRerankerConfig(output_root / "runtime/staging-model", custom_source, max_length=8192, batch_size=1)
            reranker = GteMultilingualReranker(config)
            import torch
            import transformers
            runtime = {
                "python": platform.python_version(),
                "torch": torch.__version__,
                "transformers": transformers.__version__,
                "cuda_available": torch.cuda.is_available(),
                "cuda_build": torch.version.cuda,
                "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            }
            if runtime["python"] != "3.12.10" or runtime["torch"] != "2.11.0+cu128" or runtime["transformers"] != "4.39.1" or not runtime["cuda_available"]:
                raise RuntimeError(f"isolated GTE runtime identity mismatch: {runtime}")
            load_started = perf_counter()
            reranker._lazy_load()
            torch.cuda.synchronize()
            runtime["model_load_seconds"] = perf_counter() - load_started
            runtime["model_devices"] = sorted({str(parameter.device) for parameter in reranker._model.parameters()})
            context = prepare_evidence_assembly_context(ru_manifest)
            question_summaries: list[dict[str, Any]] = []
            output_artifacts: dict[str, Any] = {}
            for index, qid in enumerate(QUESTION_IDS, 1):
                result, result_path, packet_path = baselines[qid]
                trace = result["rerank_trace"]
                hybrid = list(trace["before"])
                query = str(result["query"]["question_text"])
                ids = [str(row["unit_id"]) for row in hybrid]
                fields = _reranker_fields_from_verified_ru(context, ids)
                candidates: list[RerankCandidate] = []
                projection_audit: list[dict[str, Any]] = []
                for row in hybrid:
                    unit_id = str(row["unit_id"])
                    text, audit = project_reranker_text(fields[unit_id], max_chars=6000)
                    candidates.append(RerankCandidate(unit_id, text, int(row["rank"])))
                    projection_audit.append({"unit_id": unit_id, **audit})
                print(f"[{index:02d}/{len(QUESTION_IDS)}] {qid} GTE rerank500", flush=True)
                torch.cuda.synchronize()
                torch.cuda.reset_peak_memory_stats()
                started = perf_counter()
                scores = reranker.rerank(RerankRequest(qid, query, tuple(candidates)))
                torch.cuda.synchronize()
                wall_seconds = perf_counter() - started
                gte_reranked = project_ranked_candidates(hybrid, scores)
                gte_fused = fuse_ranked_candidates(hybrid, gte_reranked, config=FUSION)
                final20 = gte_fused[:20]
                assembly_config = EvidenceAssemblyConfig(**result["audit"]["config"]["assembly_config"])
                packet = assemble_deferred_footprint_charge_packet(
                    ru_manifest,
                    final20,
                    config=assembly_config,
                    prepared_context=context,
                    retrieval_audit={
                        "query_text": query,
                        "mode": "hybrid_rerank_fusion",
                        "candidate_depth": 500,
                        "candidate_supply_depth": 500,
                        "reranker_stage": "gte_targeted_provider_free",
                        "rerank_depth": 500,
                        "final_top_n": 20,
                        "rerank_output_k": 20,
                        "fusion_config_identity": FUSION.identity,
                    },
                )
                online_reranked = list(trace["reranked"])
                online_fused = list(trace["after"])
                online_packet = result["evidence_packet"]
                online_visible = set(_visible_unit_ids(online_packet))
                gte_visible = set(_visible_unit_ids(packet))
                judgment, carriers = _carrier_delta(
                    carrier_bindings.get(qid), hybrid, online_reranked, online_fused,
                    gte_reranked, gte_fused, online_visible, gte_visible,
                )
                qroot = output_root / "questions" / qid
                token_audit = list(reranker.last_request_metadata["candidate_token_audit"])
                combined_audit = [
                    {**projection, **token}
                    for projection, token in zip(projection_audit, token_audit, strict=True)
                ]
                artifacts = {
                    "hybrid_500": _write_json(qroot / "hybrid_500.json", hybrid),
                    "gte_reranked_500": _write_json(qroot / "gte_reranked_500.json", gte_reranked),
                    "gte_fused_500": _write_json(qroot / "gte_fused_500.json", gte_fused),
                    "gte_final20": _write_json(qroot / "gte_final20.json", final20),
                    "gte_candidate_audit": _write_json(qroot / "gte_candidate_audit.json", combined_audit),
                    "gte_packet": _write_packet(qroot / "gte_packet.json", packet),
                    "online_reranked_500": _write_json(qroot / "online_reranked_500.json", online_reranked),
                    "online_fused_500": _write_json(qroot / "online_fused_500.json", online_fused),
                    "online_final20": _write_json(qroot / "online_final20.json", online_fused[:20]),
                    "online_packet": _write_packet(qroot / "online_packet.json", online_packet),
                }
                online_top20 = [str(row["unit_id"]) for row in online_fused[:20]]
                gte_top20 = [str(row["unit_id"]) for row in final20]
                summary = {
                    "question_id": qid,
                    "diagnostic_only": qid in DIAGNOSTIC_ONLY,
                    "question": query,
                    "important_evidence_judgment": judgment,
                    "carrier_binding_status": "BOUND" if qid in carrier_bindings else "UNKNOWN",
                    "carriers": carriers,
                    "timing": {
                        "rerank_wall_seconds": wall_seconds,
                        "candidates_per_second": 500.0 / wall_seconds,
                        "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                        "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved(),
                    },
                    "truncation": {
                        "projection_truncated_count": sum(1 for row in projection_audit if row["truncated"]),
                        "token_truncated_count": int(reranker.last_request_metadata["truncated_count"]),
                        "min_actual_tokens": min(int(row["actual_token_count"]) for row in token_audit),
                        "max_actual_tokens": max(int(row["actual_token_count"]) for row in token_audit),
                        "total_input_tokens": int(reranker.last_request_metadata["total_input_tokens"]),
                    },
                    "fusion_delta": {
                        "top20_overlap": len(set(online_top20) & set(gte_top20)),
                        "online_only_final20": [unit_id for unit_id in online_top20 if unit_id not in set(gte_top20)],
                        "gte_only_final20": [unit_id for unit_id in gte_top20 if unit_id not in set(online_top20)],
                    },
                    "packet_delta": {
                        "online_visible_count": len(online_visible),
                        "gte_visible_count": len(gte_visible),
                        "online_only_visible_unit_ids": sorted(online_visible - gte_visible),
                        "gte_only_visible_unit_ids": sorted(gte_visible - online_visible),
                        "online_used_context_chars": int(online_packet["budget"]["used_context_chars"]),
                        "gte_used_context_chars": int(packet["budget"]["used_context_chars"]),
                    },
                    "input_binding": input_bindings[qid],
                    "artifacts": artifacts,
                }
                artifacts["summary"] = _write_json(qroot / "summary.json", summary)
                output_artifacts[qid] = artifacts
                question_summaries.append(summary)
        if guard.attempts:
            raise RuntimeError(f"network attempts were blocked: {guard.attempts}")
        clean = [row for row in question_summaries if not row["diagnostic_only"]]
        counts = {key: sum(row["important_evidence_judgment"] == key for row in clean) for key in ("preserved", "improved", "lost", "UNKNOWN")}
        aggregate = {
            "schema_version": SCHEMA_VERSION,
            "status": "PASS",
            "question_count": len(question_summaries),
            "clean_question_count": len(clean),
            "diagnostic_only": list(DIAGNOSTIC_ONLY),
            "clean_judgment_counts": counts,
            "material_carrier_losses": [row["question_id"] for row in clean if row["important_evidence_judgment"] == "lost"],
            "total_token_truncated_candidates": sum(row["truncation"]["token_truncated_count"] for row in question_summaries),
            "total_projection_truncated_candidates": sum(row["truncation"]["projection_truncated_count"] for row in question_summaries),
            "questions": question_summaries,
        }
        aggregate_descriptor = _write_json(output_root / "aggregate.json", aggregate)
        lines = [
            "# GTE Targeted Structural Probe",
            "",
            "Provider/API calls: 0; network calls: 0; Generation calls: 0; upstream retrieval reruns: 0.",
            "",
            "| Question | Scope | Important evidence | Carrier / Packet note | Token truncation |",
            "|---|---|---|---|---:|",
        ]
        for row in question_summaries:
            scope = "diagnostic-only" if row["diagnostic_only"] else "clean"
            note = "carrier binding UNKNOWN" if row["carrier_binding_status"] == "UNKNOWN" else f"{len(row['carriers'])} bound; online-only Packet carriers={sum(item['online_packet_present'] and not item['gte_packet_present'] for item in row['carriers'])}"
            lines.append(f"| {row['question_id']} | {scope} | {row['important_evidence_judgment']} | {note} | {row['truncation']['token_truncated_count']} |")
        lines.extend(["", "UNKNOWN means the repository has no explicit accepted carrier binding for that question, or a bound carrier is outside the persisted current-C Hybrid500. Structural churn alone is not treated as semantic quality."])
        summary_path = output_root / "summary.md"
        atomic_write(summary_path, ("\n".join(lines) + "\n").encode("utf-8"))
        manifest.update({
            "status": "PASS",
            "terminal_status": "complete",
            "model": config.identity_projection(),
            "runtime": runtime,
            "model_materialization": stage,
            "accepted_current_c": {
                "root": str(current_root.resolve()),
                "run_identity": current_manifest["run_identity"],
                "manifest": _descriptor(current_root / "metadata/manifest.json"),
            },
            "ru_manifest": _descriptor(ru_manifest),
            "carrier_binding_sources": carrier_sources,
            "input_bindings": input_bindings,
            "fusion": FUSION.to_dict(),
            "reranker_projection": {"version": "phase04-rag-reranker-projection-0.1", "max_chars": 6000, "changed": False},
            "deterministic_ordering": "rerank score desc, original Hybrid rank asc, unit_id asc; fusion score desc, original Hybrid rank asc, unit_id asc",
            "output_artifacts": output_artifacts,
            "aggregate": aggregate_descriptor,
            "summary": _descriptor(summary_path),
            "provider_api_calls": 0,
            "network_calls": len(guard.attempts),
            "network_attempts": list(guard.attempts),
            "new_generation_calls": 0,
            "upstream_retrieval_calls": 0,
            "production_behavior_changes": 0,
            "git_after_execution": _git_snapshot(repo),
        })
        _write_json(manifest_path, manifest)
        return manifest
    except BaseException as exc:
        manifest.update({
            "status": "FAIL",
            "terminal_status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "network_calls": len(guard.attempts),
            "network_attempts": list(guard.attempts),
            "provider_api_calls": 0,
            "new_generation_calls": 0,
            "upstream_retrieval_calls": 0,
            "production_behavior_changes": 0,
        })
        _write_json(manifest_path, manifest)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = run_probe(args.repo, args.output_root)
    print(json.dumps({"status": result["status"], "root": str(args.output_root.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
