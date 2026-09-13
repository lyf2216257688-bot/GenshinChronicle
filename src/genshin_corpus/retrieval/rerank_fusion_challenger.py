"""Provider-free replay of the fixed Rerank Regression Guard challenger.

This module consumes only the accepted persisted 70-question rerank evidence.
It does not retrieve, embed, call a reranker, call Generation, or change the
production backend.  The challenger preserves both rank lineages and sends
only its fused Top20 to the unchanged Formal Deferred assembler.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from .evidence_assembly import (
    EvidenceAssemblyConfig,
    assemble_deferred_footprint_charge_packet,
    evidence_packet_json_bytes,
    prepare_evidence_assembly_context,
)


QUESTION_IDS = tuple(f"Q{i:03d}" for i in range(1, 71))
CHALLENGER_SCHEMA_VERSION = "p04-rerank-fusion-challenger-0.1"
POOL_DEPTH = 500
OUTPUT_DEPTH = 20
RU_BUILD_IDENTITY = "49b48ee746716add0248fed388d10bd522a930efb582a0f5e827f66681ed8998"
DENSE_ARM_BUILD_IDENTITY = "be3efd531bcf514148e9f2b3162dbaed99fe1ac0b5257121864dff6416525922"
LEXICAL_ARM_BUILD_IDENTITY = "1eab8db130daeef16c48f3185de2fd54466584884861af01cf01936472d80d04"
RU_MANIFEST = Path(
    "data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/"
    "ru/metadata/manifest.json"
)
COMPARISON_ROOT = Path(".local/p04-rag-bge-qwen-dense-comparison-70q-20260910-172843")
R1_ROOT = Path(".local/p04-qwen-rerank-70q-20260912-r1")
C1_ROOT = Path(".local/p04-qwen-rerank-70q-20260912-c1")
R7_ROOT = Path(".local/p04-qwen-rerank-20260912-r7")
R1_RUN_IDENTITY = "2e94eb3d12262cd1bddd824524a503f8127363345bb22c9b38dc85cb9b0c9d59"
C1_RUN_IDENTITY = "663cd51f0fbfb4cb6268e947bc321b7c50fb08a51a29e0184280931bf641d505"
R1_MANIFEST_SHA256 = "967b84357349188bb0aeac72b9cb4766e7c92196ef7b161b0dcf431a7430e205"
C1_MANIFEST_SHA256 = "effce4ef8a9f626bc161c733427b18230b5a162697bfdcc02cc11fadfeea7c90"
R7_MANIFEST_SHA256 = "f08c8cbfb3319cf15452f124f84e789d86bafe8cfeb0889b8f6db9ddbdebb8a5"
R7_RUN_IDENTITY = "ce9d7156d7907c2faad26dce30ab09b0c6e2f814b006192ae8c7a11a3b78f55c"
R7_QUESTION_IDS = ("Q005", "Q011", "Q045", "Q046", "Q049")

Q068_DECISIVE = "fe1311f2b2ed8559a529b0ef8a45a2ad960ef787fa1493b765a8ed105bc2c18a"
Q068_COMPETING = "de4b1922721a6156f8202fa880f3a4ce85ebbdd752f8c80bd52d7cde9269458c"
Q056_DECISIVE = "41a662c18d06d6dbe1268db3690f012f96027ba21caa61fc563f43982a124d05"
Q056_COMPETING = "03925ee506bd34f8d502fdfcfff51757c0c7cdf81eb3be18675fff11b76e865d"
Q049_RESCUE = "51146bf44f9dab34092a4d9c44d609cae0a20b3b39ca1c30d61b855ed7c4f28d"


class RerankFusionError(ValueError):
    """Raised when persisted evidence cannot be bound without guessing."""


@dataclass(frozen=True)
class FusionConfig:
    """Fixed experimental operating point; none of these values is a contract."""

    hybrid_weight: float = 0.35
    rerank_weight: float = 0.65
    hybrid_denominator: int = 60
    rerank_denominator: int = 60
    pool_depth: int = POOL_DEPTH
    output_depth: int = OUTPUT_DEPTH
    tie_break: str = "fused_score_desc,original_hybrid_rank_asc,unit_id_asc"
    version: str = "phase04-rerank-regression-guard-rank-fusion-0.1"

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.hybrid_weight)) or not math.isfinite(float(self.rerank_weight)):
            raise ValueError("fusion weights must be finite")
        if self.hybrid_weight < 0 or self.rerank_weight < 0:
            raise ValueError("fusion weights must be non-negative")
        if self.hybrid_weight + self.rerank_weight <= 0:
            raise ValueError("at least one fusion weight must be positive")
        for name in ("hybrid_denominator", "rerank_denominator", "pool_depth", "output_depth"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.pool_depth < self.output_depth:
            raise ValueError("pool_depth must be at least output_depth")
        if not self.tie_break or not self.version:
            raise ValueError("fusion identity fields must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "hybrid_weight": self.hybrid_weight,
            "rerank_weight": self.rerank_weight,
            "hybrid_denominator": self.hybrid_denominator,
            "rerank_denominator": self.rerank_denominator,
            "pool_depth": self.pool_depth,
            "output_depth": self.output_depth,
            "tie_break": self.tie_break,
            "version": self.version,
        }

    @property
    def identity(self) -> str:
        return sha256_json(self.to_dict())


FIXED_FUSION_CONFIG = FusionConfig()


def implementation_binding() -> dict[str, str]:
    """Return the physical identity of this untracked challenger source."""

    path = Path(__file__).resolve()
    return {"path": str(path), "sha256": _sha256_file(path)}


def compute_run_identity(
    replay_input_binding: Mapping[str, Any],
    *,
    config: FusionConfig = FIXED_FUSION_CONFIG,
    implementation: Mapping[str, Any] | None = None,
) -> str:
    """Derive a replay identity only from effective inputs and implementation."""

    implementation_value = implementation if implementation is not None else implementation_binding()
    return sha256_json(
        {
            "schema_version": CHALLENGER_SCHEMA_VERSION,
            "fusion_config_identity": config.identity,
            "fusion_config": config.to_dict(),
            "implementation_binding": dict(implementation_value),
            "replay_input_binding": dict(replay_input_binding),
        }
    )


def _as_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RerankFusionError(f"{label} must be an object")
    return value


def _finite_rank(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RerankFusionError(f"{label} must be a positive integer")
    return value


def candidate_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    return sha256(canonical_json_bytes([dict(row) for row in rows])).hexdigest()


def bind_rankings(
    hybrid_rows: Sequence[Mapping[str, Any]],
    reranked_rows: Sequence[Mapping[str, Any]],
    *,
    pool_depth: int = POOL_DEPTH,
) -> dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """Bind the two persisted rank lists by identity, failing closed."""

    if len(hybrid_rows) != pool_depth or len(reranked_rows) != pool_depth:
        raise RerankFusionError("historical ranking lists do not have the required pool depth")
    hybrid_by_id: dict[str, Mapping[str, Any]] = {}
    expected_ranks = set(range(1, pool_depth + 1))
    for index, row in enumerate(hybrid_rows):
        item = _as_mapping(row, f"hybrid row {index}")
        unit_id = item.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id:
            raise RerankFusionError("hybrid row has invalid unit_id")
        rank = _finite_rank(item.get("rank"), f"hybrid rank for {unit_id}")
        if rank in {int(r.get("rank")) for r in hybrid_rows[:index]} or unit_id in hybrid_by_id:
            raise RerankFusionError("hybrid ranking contains duplicate identity or rank")
        hybrid_by_id[unit_id] = item
    if {int(row["rank"]) for row in hybrid_by_id.values()} != expected_ranks:
        raise RerankFusionError("hybrid ranks are not exactly 1..pool_depth")

    rerank_by_id: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(reranked_rows):
        item = _as_mapping(row, f"reranked row {index}")
        unit_id = item.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id or unit_id in rerank_by_id:
            raise RerankFusionError("reranked ranking contains duplicate or invalid identity")
        rerank_rank = _finite_rank(item.get("rerank_rank", item.get("rank")), f"rerank rank for {unit_id}")
        row_rank = _finite_rank(item.get("rank"), f"reranked row rank for {unit_id}")
        original_rank = _finite_rank(item.get("original_hybrid_rank"), f"original rank for {unit_id}")
        if row_rank != rerank_rank or rerank_rank > pool_depth:
            raise RerankFusionError("rerank rank fields are inconsistent")
        if "rerank_score" in item:
            try:
                score = float(item["rerank_score"])
            except (TypeError, ValueError) as exc:
                raise RerankFusionError("rerank score is not numeric") from exc
            if score != score or score in (float("inf"), float("-inf")):
                raise RerankFusionError("rerank score is non-finite")
        if unit_id not in hybrid_by_id or int(hybrid_by_id[unit_id]["rank"]) != original_rank:
            raise RerankFusionError("reranked candidate has unknown or mismatched Hybrid identity")
        rerank_by_id[unit_id] = item
    if set(rerank_by_id) != set(hybrid_by_id):
        raise RerankFusionError("reranked ranking has missing or unknown candidate identity")
    if {int(row.get("rerank_rank", row["rank"])) for row in rerank_by_id.values()} != expected_ranks:
        raise RerankFusionError("rerank ranks are not exactly 1..pool_depth")
    return {unit_id: (hybrid_by_id[unit_id], rerank_by_id[unit_id]) for unit_id in hybrid_by_id}


def _validate_candidate_provenance(rows: Sequence[Mapping[str, Any]], label: str) -> None:
    for index, row in enumerate(rows):
        retrieval = _as_mapping(row.get("retrieval"), f"{label} retrieval {index}")
        arms = _as_mapping(retrieval.get("arm_build_identities"), f"{label} arm identities {index}")
        if arms.get("dense") != DENSE_ARM_BUILD_IDENTITY or arms.get("lexical") != LEXICAL_ARM_BUILD_IDENTITY:
            raise RerankFusionError(f"{label} Retrieval arm provenance binding failed")
        if retrieval.get("mode") != "hybrid":
            raise RerankFusionError(f"{label} candidate mode binding failed")


def fuse_rankings(
    hybrid_rows: Sequence[Mapping[str, Any]],
    reranked_rows: Sequence[Mapping[str, Any]],
    *,
    config: FusionConfig = FIXED_FUSION_CONFIG,
) -> list[dict[str, Any]]:
    """Compute fixed weighted reciprocal-rank fusion and return fused Top20."""

    bound = bind_rankings(hybrid_rows, reranked_rows, pool_depth=config.pool_depth)
    scored: list[tuple[float, int, str, Mapping[str, Any], Mapping[str, Any]]] = []
    for unit_id, (hybrid, reranked) in bound.items():
        hybrid_rank = int(hybrid["rank"])
        rerank_rank = int(reranked.get("rerank_rank", reranked["rank"]))
        score = (
            config.hybrid_weight / (config.hybrid_denominator + hybrid_rank)
            + config.rerank_weight / (config.rerank_denominator + rerank_rank)
        )
        if not isinstance(score, float) or score != score or score in (float("inf"), float("-inf")):
            raise RerankFusionError("fusion produced a non-finite score")
        scored.append((score, hybrid_rank, unit_id, hybrid, reranked))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    output: list[dict[str, Any]] = []
    for fused_rank, (score, hybrid_rank, unit_id, hybrid, reranked) in enumerate(scored[: config.output_depth], 1):
        row = dict(hybrid)
        retrieval = dict(_as_mapping(row.get("retrieval", {}), f"retrieval for {unit_id}"))
        rerank_retrieval = reranked.get("retrieval")
        if isinstance(rerank_retrieval, Mapping):
            for key in ("original_hybrid_rank", "rerank_rank", "rerank_score"):
                if key in rerank_retrieval:
                    retrieval[key] = rerank_retrieval[key]
        row["rank"] = fused_rank
        row["original_hybrid_rank"] = hybrid_rank
        row["rerank_rank"] = int(reranked.get("rerank_rank", reranked["rank"]))
        if "rerank_score" in reranked:
            row["rerank_score"] = reranked["rerank_score"]
        row["fusion_score"] = score
        row["fusion_rank"] = fused_rank
        row["retrieval"] = retrieval
        row["fusion_stage"] = {
            "config_identity": config.identity,
            "config": config.to_dict(),
            "method": "weighted_reciprocal_rank",
            "version": config.version,
        }
        output.append(row)
    return output


def _full_fused_rank_map(
    hybrid_rows: Sequence[Mapping[str, Any]],
    reranked_rows: Sequence[Mapping[str, Any]],
    *,
    config: FusionConfig,
) -> dict[str, int]:
    """Return complete fused ranks for diagnostics without changing Top20 input."""

    bound = bind_rankings(hybrid_rows, reranked_rows, pool_depth=config.pool_depth)
    scored = []
    for unit_id, (hybrid, reranked) in bound.items():
        hybrid_rank = int(hybrid["rank"])
        rerank_rank = int(reranked.get("rerank_rank", reranked["rank"]))
        score = config.hybrid_weight / (config.hybrid_denominator + hybrid_rank) + config.rerank_weight / (config.rerank_denominator + rerank_rank)
        scored.append((score, hybrid_rank, unit_id))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return {unit_id: index for index, (_, _, unit_id) in enumerate(scored, 1)}


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RerankFusionError(f"could not read {label}") from exc
    if not isinstance(value, dict):
        raise RerankFusionError(f"{label} must be an object")
    return value


def _write_json(root: Path, relative: str, value: Mapping[str, Any]) -> str:
    body = canonical_json_bytes(dict(value))
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, body)
    return sha256(body).hexdigest()


def _packet_hash(path: Path) -> str:
    return _sha256_file(path)


def _question_root(question_id: str) -> Path:
    return R1_ROOT if int(question_id[1:]) <= 44 else C1_ROOT


def _validate_manifests() -> dict[str, Any]:
    r1_manifest_path = R1_ROOT / "metadata/manifest.json"
    c1_manifest_path = C1_ROOT / "metadata/manifest.json"
    if _sha256_file(r1_manifest_path) != R1_MANIFEST_SHA256 or _sha256_file(c1_manifest_path) != C1_MANIFEST_SHA256:
        raise RerankFusionError("accepted r1/c1 manifest hash binding failed")
    r1 = _read_json(r1_manifest_path, "r1 manifest")
    c1 = _read_json(c1_manifest_path, "c1 manifest")
    if r1.get("run_identity") != R1_RUN_IDENTITY or c1.get("run_identity") != C1_RUN_IDENTITY:
        raise RerankFusionError("accepted r1/c1 run identity binding failed")
    if r1.get("status") != "failed" or c1.get("status") != "complete":
        raise RerankFusionError("accepted r1/c1 status binding failed")
    if r1.get("question_ids") != list(QUESTION_IDS) or c1.get("question_ids") != list(QUESTION_IDS):
        raise RerankFusionError("accepted r1/c1 question ordering is not Q001-Q070")
    continuation = _as_mapping(c1.get("continuation"), "c1 continuation")
    r1_binding = _as_mapping(continuation.get("r1"), "c1 r1 continuation")
    if r1_binding.get("manifest_sha256") != R1_MANIFEST_SHA256 or continuation.get("start_question_id") != "Q045":
        raise RerankFusionError("r1 prefix/c1 continuation binding failed")
    expected_result_hashes = r1_binding.get("result_hashes")
    if not isinstance(expected_result_hashes, Mapping):
        raise RerankFusionError("c1 continuation lacks r1 result hashes")
    for qid in QUESTION_IDS[:44]:
        path = R1_ROOT / f"results/{qid}.json"
        if _sha256_file(path) != expected_result_hashes.get(qid):
            raise RerankFusionError(f"r1 result hash binding failed: {qid}")
    if c1.get("result_count") != 26:
        raise RerankFusionError("c1 result count binding failed")
    ru_hash = _sha256_file(RU_MANIFEST)
    if ru_hash != "dc6bbd30cc6fbc83fa38132085fb8550f20322082467fe24aadcb4239ca78673":
        raise RerankFusionError("RU manifest hash binding failed")
    r7_manifest_path = R7_ROOT / "metadata/manifest.json"
    if _sha256_file(r7_manifest_path) != R7_MANIFEST_SHA256:
        raise RerankFusionError("accepted r7 manifest hash binding failed")
    r7_manifest = _read_json(r7_manifest_path, "r7 manifest")
    if r7_manifest.get("run_identity") != R7_RUN_IDENTITY or r7_manifest.get("question_ids") != list(R7_QUESTION_IDS):
        raise RerankFusionError("accepted r7 corroboration binding failed")
    return {
        "r1_manifest_sha256": R1_MANIFEST_SHA256,
        "c1_manifest_sha256": C1_MANIFEST_SHA256,
        "ru_manifest_sha256": ru_hash,
        "r7_manifest_sha256": R7_MANIFEST_SHA256,
        "r1_run_identity": R1_RUN_IDENTITY,
        "c1_run_identity": C1_RUN_IDENTITY,
        "r7_run_identity": R7_RUN_IDENTITY,
    }


def _validate_packet_contract(packet: Mapping[str, Any], label: str) -> None:
    if packet.get("schema_version") != "phase04-evidence-packet-0.1":
        raise RerankFusionError(f"{label} Packet schema binding failed")
    if packet.get("assembly_version") != "phase04-rag-a1-3-deferred-footprint-charge-0.1":
        raise RerankFusionError(f"{label} Formal Deferred identity binding failed")
    build = _as_mapping(packet.get("retrieval_unit_build"), f"{label} RU build")
    if build.get("build_identity") != RU_BUILD_IDENTITY:
        raise RerankFusionError(f"{label} RU build identity binding failed")
    config = _as_mapping(packet.get("assembly_config"), f"{label} Assembly config")
    if config.get("total_context_chars") != 12000 or config.get("per_block_chars") != 3000:
        raise RerankFusionError(f"{label} B0/per-block binding failed")
    policy = _as_mapping(packet.get("selection_policy"), f"{label} selection policy")
    if policy.get("identity") != "phase04-rag-a1-3-deferred-footprint-charge-0.1":
        raise RerankFusionError(f"{label} selection policy binding failed")
    if not isinstance(packet.get("evidence"), list) or not isinstance(packet.get("budget"), Mapping):
        raise RerankFusionError(f"{label} Packet structure is incomplete")


def _packet_metadata(packet: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    audit = _as_mapping(packet.get("retrieval_audit"), f"{label} retrieval audit")
    return _as_mapping(audit.get("retrieval_metadata"), f"{label} retrieval metadata")


def _load_carriers() -> tuple[dict[str, list[str]], dict[str, str]]:
    carriers: dict[str, list[str]] = {}
    result_hashes: dict[str, str] = {}
    for qid in R7_QUESTION_IDS:
        result_path = R7_ROOT / f"results/{qid}.json"
        result_hashes[qid] = _sha256_file(result_path)
        result = _read_json(result_path, f"r7 {qid} result")
        if result.get("run_identity") != R7_RUN_IDENTITY:
            raise RerankFusionError(f"r7 result identity binding failed: {qid}")
        rows = result.get("carrier_movements")
        if not isinstance(rows, list):
            raise RerankFusionError(f"r7 carrier binding missing: {qid}")
        visible = [str(row["unit_id"]) for row in rows if isinstance(row, Mapping) and row.get("challenger_packet_visible") is True]
        if not visible:
            raise RerankFusionError(f"r7 has no accepted pure-rerank carrier: {qid}")
        carriers[qid] = visible
    if Q049_RESCUE not in carriers["Q049"]:
        raise RerankFusionError("Q049 rescue identity is not in accepted r7 carriers")
    return carriers, result_hashes


def _artifact_hashes(paths: Mapping[str, Path], label: str) -> dict[str, dict[str, str]]:
    """Hash exactly the files consumed by the replay and fail closed if absent."""

    bound: dict[str, dict[str, str]] = {}
    for key, path in paths.items():
        if not path.is_file():
            raise RerankFusionError(f"{label} artifact is missing: {path}")
        bound[key] = {"path": str(path), "sha256": _sha256_file(path)}
    return bound


def _build_replay_input_binding() -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Validate and bind every persisted input that can affect this replay."""

    binding = _validate_manifests()
    carriers, r7_result_hashes = _load_carriers()
    comparison_paths = {qid: COMPARISON_ROOT / "comparisons" / f"{qid}.json" for qid in QUESTION_IDS}
    comparison_artifacts = _artifact_hashes(comparison_paths, "comparison")
    candidate_artifacts: dict[str, dict[str, str]] = {}
    historical_result_artifacts: dict[str, dict[str, str]] = {}
    control_packet_artifacts: dict[str, dict[str, str]] = {}
    pure_packet_artifacts: dict[str, dict[str, str]] = {}
    for qid in QUESTION_IDS:
        source_root = _question_root(qid)
        paths = _artifact_hashes(
            {
                "candidate": source_root / f"candidates/{qid}.json",
                "historical_result": source_root / f"results/{qid}.json",
                "control_packet": source_root / f"{qid}/control/packet.json",
                "pure_rerank_packet": source_root / f"{qid}/challenger/packet.json",
            },
            f"{qid}",
        )
        candidate_artifacts[qid] = paths["candidate"]
        historical_result_artifacts[qid] = paths["historical_result"]
        control_packet_artifacts[qid] = paths["control_packet"]
        pure_packet_artifacts[qid] = paths["pure_rerank_packet"]
    replay_binding = {
        **binding,
        "comparison_root": str(COMPARISON_ROOT),
        "comparison_artifacts": comparison_artifacts,
        "candidate_artifacts": candidate_artifacts,
        "historical_result_artifacts": historical_result_artifacts,
        "control_packet_artifacts": control_packet_artifacts,
        "pure_rerank_packet_artifacts": pure_packet_artifacts,
        "r7_result_artifacts": {
            qid: {"path": str(R7_ROOT / f"results/{qid}.json"), "sha256": digest}
            for qid, digest in r7_result_hashes.items()
        },
    }
    return replay_binding, carriers


def _omission_reason(packet: Mapping[str, Any], unit_id: str) -> str | None:
    budget = packet.get("budget")
    if not isinstance(budget, Mapping):
        return None
    omitted = budget.get("omitted_blocks", [])
    if not isinstance(omitted, list):
        return None
    for row in omitted:
        if isinstance(row, Mapping) and unit_id in [str(value) for value in row.get("unit_ids", [])]:
            return str(row.get("reason"))
    return None


def _summary(packet: Mapping[str, Any], path: Path) -> dict[str, Any]:
    visible = packet.get("evidence", [])
    visible_ids = [str(member["unit_id"]) for item in visible if isinstance(item, Mapping) for member in item.get("members", []) if isinstance(member, Mapping) and "unit_id" in member]
    return {
        "packet_sha256": _packet_hash(path),
        "visible_unit_ids": list(dict.fromkeys(visible_ids)),
        "visible_unit_count": len(list(dict.fromkeys(visible_ids))),
        "used_context_chars": int(_as_mapping(packet.get("budget"), "Packet budget").get("used_context_chars", 0)),
        "used_evidence_blocks": len(visible),
        "omitted_blocks": list(_as_mapping(packet.get("budget"), "Packet budget").get("omitted_blocks", [])),
    }


def _gate_row(unit_id: str, fused: Sequence[Mapping[str, Any]], full_ranks: Mapping[str, int]) -> dict[str, Any]:
    fused_rank = full_ranks.get(unit_id)
    return {"unit_id": unit_id, "fused_rank": fused_rank, "top20": fused_rank is not None and fused_rank <= OUTPUT_DEPTH}


def run_provider_free_replay(*, output_root: Path | None = None) -> dict[str, Any]:
    """Run exactly one complete provider-free Q001-Q070 replay."""

    replay_input_binding, carriers = _build_replay_input_binding()
    config = FIXED_FUSION_CONFIG
    implementation = implementation_binding()
    run_identity = compute_run_identity(replay_input_binding, config=config, implementation=implementation)
    root = Path(output_root) if output_root is not None else Path(f".local/p04-rerank-fusion-challenger-20260913-{run_identity[:16]}")
    if root.exists():
        raise FileExistsError(f"refusing to overwrite existing challenger root: {root}")
    root.mkdir(parents=True)
    manifest: dict[str, Any] = {
        "schema_version": CHALLENGER_SCHEMA_VERSION,
        "status": "in_progress",
        "run_identity": run_identity,
        "question_ids": list(QUESTION_IDS),
        "fusion_config": config.to_dict(),
        "fusion_config_identity": config.identity,
        "implementation_binding": implementation,
        "replay_input_binding": replay_input_binding,
        "provider_calls": {"rerank": 0, "embedding": 0, "network": 0, "generation": 0},
        "experimental_operating_point": True,
    }
    _write_json(root, "metadata/manifest.json", manifest)
    try:
        context = prepare_evidence_assembly_context(RU_MANIFEST)
        if context.retrieval_unit_build_identity != RU_BUILD_IDENTITY:
            raise RerankFusionError("prepared RU context build identity mismatch")
        results: list[dict[str, Any]] = []
        for qid in QUESTION_IDS:
            source_root = _question_root(qid)
            candidate_path = source_root / f"candidates/{qid}.json"
            result_path = source_root / f"results/{qid}.json"
            control_packet_path = source_root / f"{qid}/control/packet.json"
            pure_packet_path = source_root / f"{qid}/challenger/packet.json"
            candidate_artifact = _read_json(candidate_path, f"candidate artifact {qid}")
            historical_result = _read_json(result_path, f"result artifact {qid}")
            hybrid = candidate_artifact.get("hybrid_top500")
            reranked = candidate_artifact.get("reranked_top500")
            if candidate_artifact.get("question_id") != qid or not isinstance(hybrid, list) or not isinstance(reranked, list):
                raise RerankFusionError(f"candidate artifact identity failed: {qid}")
            _validate_candidate_provenance(hybrid, f"hybrid {qid}")
            _validate_candidate_provenance(reranked, f"reranked {qid}")
            bind_rankings(hybrid, reranked)
            if historical_result.get("input_candidate_hash") != candidate_hash(hybrid):
                raise RerankFusionError(f"candidate hash binding failed: {qid}")
            pure_top20 = reranked[:OUTPUT_DEPTH]
            if historical_result.get("challenger", {}).get("candidate_hash") != candidate_hash(pure_top20):
                raise RerankFusionError(f"pure-rerank Top20 hash binding failed: {qid}")
            control_accepted = _read_json(control_packet_path, f"control Packet {qid}")
            pure_accepted = _read_json(pure_packet_path, f"pure-rerank Packet {qid}")
            _validate_packet_contract(control_accepted, f"control {qid}")
            _validate_packet_contract(pure_accepted, f"pure-rerank {qid}")
            control_comparison = _read_json(COMPARISON_ROOT / "comparisons" / f"{qid}.json", f"accepted comparison {qid}")
            qwen = _as_mapping(control_comparison.get("qwen"), f"accepted Qwen comparison {qid}")
            control_candidates = qwen.get("hybrid_candidates")
            if not isinstance(control_candidates, list) or len(control_candidates) != OUTPUT_DEPTH:
                raise RerankFusionError(f"accepted control candidate set is incomplete: {qid}")
            if historical_result.get("control", {}).get("candidate_hash") != candidate_hash(control_candidates):
                raise RerankFusionError(f"control candidate hash binding failed: {qid}")
            pool_ids = {str(row["unit_id"]) for row in hybrid}
            if not {str(row.get("unit_id")) for row in control_candidates}.issubset(pool_ids):
                raise RerankFusionError(f"accepted control candidates are outside the historical pool: {qid}")
            control_replay = assemble_deferred_footprint_charge_packet(
                RU_MANIFEST, control_candidates, prepared_context=context, retrieval_audit=_packet_metadata(control_accepted, f"control {qid}")
            )
            pure_replay = assemble_deferred_footprint_charge_packet(
                RU_MANIFEST, pure_top20, prepared_context=context, retrieval_audit=_packet_metadata(pure_accepted, f"pure-rerank {qid}")
            )
            control_replay_hash = sha256(evidence_packet_json_bytes(control_replay)).hexdigest()
            pure_replay_hash = sha256(evidence_packet_json_bytes(pure_replay)).hexdigest()
            if control_replay_hash != _packet_hash(control_packet_path):
                raise RerankFusionError(f"control Packet parity failed: {qid}")
            if pure_replay_hash != _packet_hash(pure_packet_path):
                raise RerankFusionError(f"pure-rerank Packet parity failed: {qid}")

            fused = fuse_rankings(hybrid, reranked, config=config)
            full_fused_ranks = _full_fused_rank_map(hybrid, reranked, config=config)
            fused_audit = dict(_packet_metadata(pure_accepted, f"pure-rerank {qid}"))
            fused_audit["fusion_challenger"] = {"config_identity": config.identity, "method": "weighted_reciprocal_rank"}
            fused_packet = assemble_deferred_footprint_charge_packet(RU_MANIFEST, fused, prepared_context=context, retrieval_audit=fused_audit)
            fused_packet_path = root / f"packets/{qid}/fused.json"
            fused_packet_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(fused_packet_path, evidence_packet_json_bytes(fused_packet))
            control_out_path = root / f"packets/{qid}/control.json"
            pure_out_path = root / f"packets/{qid}/pure_rerank.json"
            atomic_write(control_out_path, evidence_packet_json_bytes(control_accepted))
            atomic_write(pure_out_path, evidence_packet_json_bytes(pure_accepted))
            fused_summary = _summary(fused_packet, fused_packet_path)
            pure_summary = _summary(pure_accepted, pure_packet_path)
            control_summary = _summary(control_accepted, control_packet_path)
            fused_by_id = {str(row["unit_id"]): row for row in fused}
            pure_rank_by_id = {str(row["unit_id"]): int(row.get("rerank_rank", row["rank"])) for row in reranked}
            positive_rows = []
            for unit_id in carriers.get(qid, []):
                row = fused_by_id.get(unit_id)
                positive_rows.append({"unit_id": unit_id, "fused_rank": int(row["rank"]) if row else None, "fused_top20": row is not None, "packet_visible": unit_id in fused_summary["visible_unit_ids"], "pure_packet_visible": unit_id in pure_summary["visible_unit_ids"]})
            if any(not row["fused_top20"] or not row["packet_visible"] for row in positive_rows):
                raise RerankFusionError(f"positive-retention gate failed: {qid}")
            q068_gate = None
            if qid == "Q068":
                q068_gate = {"decisive": _gate_row(Q068_DECISIVE, fused, full_fused_ranks), "competing": _gate_row(Q068_COMPETING, fused, full_fused_ranks)}
                q068_gate["decisive"]["packet_visible"] = Q068_DECISIVE in fused_summary["visible_unit_ids"]
                q068_gate["competing"]["packet_visible"] = Q068_COMPETING in fused_summary["visible_unit_ids"]
                q068_gate["pass"] = bool(q068_gate["decisive"]["top20"] and q068_gate["decisive"]["packet_visible"] and q068_gate["competing"]["fused_rank"] is not None and q068_gate["decisive"]["fused_rank"] < q068_gate["competing"]["fused_rank"])
                if not q068_gate["pass"]:
                    raise RerankFusionError("Q068 regression-guard gate failed")
            q056_diagnostic = None
            if qid == "Q056":
                decisive_fused_rank = full_fused_ranks.get(Q056_DECISIVE)
                competing_fused_rank = full_fused_ranks.get(Q056_COMPETING)
                decisive_pure = pure_rank_by_id.get(Q056_DECISIVE)
                competing_pure = pure_rank_by_id.get(Q056_COMPETING)
                pure_gap = competing_pure - decisive_pure if decisive_pure is not None and competing_pure is not None else None
                fused_gap = competing_fused_rank - decisive_fused_rank if decisive_fused_rank is not None and competing_fused_rank is not None else None
                q056_diagnostic = {"decisive": {"unit_id": Q056_DECISIVE, "pure_rerank": decisive_pure, "fused_rank": decisive_fused_rank, "packet_visible": Q056_DECISIVE in fused_summary["visible_unit_ids"], "omission_reason": _omission_reason(fused_packet, Q056_DECISIVE)}, "competing": {"unit_id": Q056_COMPETING, "pure_rerank": competing_pure, "fused_rank": competing_fused_rank, "packet_visible": Q056_COMPETING in fused_summary["visible_unit_ids"], "omission_reason": _omission_reason(fused_packet, Q056_COMPETING)}, "pure_rank_gap": pure_gap, "fused_rank_gap": fused_gap, "rank_gap_change": fused_gap - pure_gap if pure_gap is not None and fused_gap is not None else None, "semantic_claim": "not_evaluated"}
                if Q056_DECISIVE in pure_summary["visible_unit_ids"] and Q056_DECISIVE not in fused_summary["visible_unit_ids"]:
                    raise RerankFusionError("Q056 decisive evidence was lost from Packet")
            fused_top_ids = [str(row["unit_id"]) for row in fused]
            pure_top_ids = [str(row["unit_id"]) for row in reranked[:OUTPUT_DEPTH]]
            hybrid_top_ids = [str(row["unit_id"]) for row in hybrid[:OUTPUT_DEPTH]]
            record = {
                "question_id": qid,
                "input_artifacts": {
                    "candidate": replay_input_binding["candidate_artifacts"][qid],
                    "historical_result": replay_input_binding["historical_result_artifacts"][qid],
                    "comparison": replay_input_binding["comparison_artifacts"][qid],
                    "control_packet": replay_input_binding["control_packet_artifacts"][qid],
                    "pure_rerank_packet": replay_input_binding["pure_rerank_packet_artifacts"][qid],
                },
                "control": {"packet": control_summary, "parity": True},
                "pure_rerank": {"packet": pure_summary, "parity": True},
                "fused": {"config_identity": config.identity, "packet": fused_summary, "top20": [{"unit_id": row["unit_id"], "fused_rank": row["rank"], "fused_score": row["fusion_score"], "original_hybrid_rank": row["original_hybrid_rank"], "rerank_rank": row["rerank_rank"]} for row in fused], "packet_visible_unit_ids": fused_summary["visible_unit_ids"], "omitted_blocks": fused_summary["omitted_blocks"]},
                "positive_retention": positive_rows,
                "q068_regression_guard": q068_gate,
                "q056_diagnostic": q056_diagnostic,
                "failure_family": "failure_family_out_of_fusion_verdict" if qid in {"Q050", "Q058"} else None,
                "packet_displacement": {"from_control": sorted(set(control_summary["visible_unit_ids"]) - set(fused_summary["visible_unit_ids"])), "from_pure_rerank": sorted(set(pure_summary["visible_unit_ids"]) - set(fused_summary["visible_unit_ids"]))},
                "mechanical_churn": {
                    "fused_vs_pure_top20_overlap": len(set(fused_top_ids) & set(pure_top_ids)),
                    "fused_vs_hybrid_top20_overlap": len(set(fused_top_ids) & set(hybrid_top_ids)),
                    "used_context_chars_delta_vs_pure": fused_summary["used_context_chars"] - pure_summary["used_context_chars"],
                    "used_context_chars_delta_vs_control": fused_summary["used_context_chars"] - control_summary["used_context_chars"],
                    "fused_packet_differs_from_pure": fused_summary["packet_sha256"] != pure_summary["packet_sha256"],
                    "fused_packet_differs_from_control": fused_summary["packet_sha256"] != control_summary["packet_sha256"],
                },
            }
            _write_json(root, f"results/{qid}.json", record)
            results.append(record)
        overlaps_pure = [row["mechanical_churn"]["fused_vs_pure_top20_overlap"] for row in results]
        overlaps_hybrid = [row["mechanical_churn"]["fused_vs_hybrid_top20_overlap"] for row in results]
        displacement_pure = [len(row["packet_displacement"]["from_pure_rerank"]) for row in results]
        displacement_control = [len(row["packet_displacement"]["from_control"]) for row in results]
        context_delta_pure = [row["mechanical_churn"]["used_context_chars_delta_vs_pure"] for row in results]
        context_delta_control = [row["mechanical_churn"]["used_context_chars_delta_vs_control"] for row in results]
        def stats(values: Sequence[int]) -> dict[str, float | int]:
            return {"min": min(values), "median": median(values), "mean": mean(values), "max": max(values)}

        aggregate = {
            "schema_version": manifest["schema_version"],
            "run_identity": run_identity,
            "fusion_config_identity": config.identity,
            "question_count": len(results),
            "fused_vs_pure_top20_overlap": stats(overlaps_pure),
            "fused_vs_hybrid_top20_overlap": stats(overlaps_hybrid),
            "packet_visible_displacement_count_vs_pure": stats(displacement_pure),
            "packet_visible_displacement_count_vs_control": stats(displacement_control),
            "fused_visible_counts": stats([row["fused"]["packet"]["visible_unit_count"] for row in results]),
            "pure_visible_counts": stats([row["pure_rerank"]["packet"]["visible_unit_count"] for row in results]),
            "control_visible_counts": stats([row["control"]["packet"]["visible_unit_count"] for row in results]),
            "used_context_chars_delta_vs_pure": stats(context_delta_pure),
            "used_context_chars_delta_vs_control": stats(context_delta_control),
            "fused_packet_differs_from_pure_count": sum(row["mechanical_churn"]["fused_packet_differs_from_pure"] for row in results),
            "fused_packet_differs_from_control_count": sum(row["mechanical_churn"]["fused_packet_differs_from_control"] for row in results),
            "semantic_claim": "not_evaluated",
        }
        aggregate_sha = _write_json(root, "metadata/aggregate_summary.json", aggregate)
        comparison = {"schema_version": manifest["schema_version"], "run_identity": run_identity, "fusion_config": config.to_dict(), "questions": [{"question_id": row["question_id"], "control_packet_sha256": row["control"]["packet"]["packet_sha256"], "pure_rerank_packet_sha256": row["pure_rerank"]["packet"]["packet_sha256"], "fused_packet_sha256": row["fused"]["packet"]["packet_sha256"], "fused_visible_unit_count": row["fused"]["packet"]["visible_unit_count"], "positive_retention": row["positive_retention"], "q068_regression_guard": row["q068_regression_guard"], "q056_diagnostic": row["q056_diagnostic"], "mechanical_churn": row["mechanical_churn"]} for row in results], "aggregate_summary": {"path": "metadata/aggregate_summary.json", "sha256": aggregate_sha}}
        comparison_sha = _write_json(root, "metadata/comparison.json", comparison)
        manifest["status"] = "complete"
        manifest["result_count"] = len(results)
        manifest["comparison_sha256"] = comparison_sha
        manifest["aggregate_summary_sha256"] = aggregate_sha
        _write_json(root, "metadata/manifest.json", manifest)
        return manifest
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        _write_json(root, "metadata/manifest.json", manifest)
        raise


if __name__ == "__main__":
    print(json.dumps(run_provider_free_replay(), ensure_ascii=False, indent=2))
