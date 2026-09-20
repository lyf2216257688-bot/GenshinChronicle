"""Provider-free mechanics for the Phase 04 targeted semantic gate.

This module is deliberately an execution seam, not a semantic evaluator.  It
validates immutable historical bindings, captures one shared Block A round-0
result, adapts the existing injected Bailian transport to the provider-neutral
EvidenceAssessor contract, and persists facts for later human review.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from typing import Any, Mapping, Sequence

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.generation.generation import (
    BailianControlConfig,
    BailianGenerationProvider,
    BailianTransport,
    BailianTransportError,
    BailianTransportResponse,
    CitationValidation,
    REQUESTED_ALIAS_POLICY,
    BASELINE_QWEN_MODEL_ID,
    EXACT_SNAPSHOT_POLICY,
    GenerationProvider,
    GenerationResult,
    GenerationAnswerScope,
    DEFAULT_GENERATION_INSTRUCTION,
    project_generation_request,
    workspace_from_bailian_base_url,
    write_generation_result,
)

from .adaptive import (
    AdaptiveContractError,
    EvidenceAssessmentRequest,
    EvidenceAssessmentResult,
    bind_evidence_packet,
    _project_round,
    FrozenBlockAEvidenceCapability,
    _partial_instruction,
    run_bounded_adaptive_question,
)
from .backend import PreparedRagState, SingleQuestionBackendConfig
from .config import ProductionRagArtifactPaths
from genshin_corpus.retrieval.bge_reranker_v2_m3 import (
    BgeRerankerV2M3,
    MODEL_ID as BGE_MODEL_ID,
    MODEL_REVISION as BGE_MODEL_REVISION,
    MODEL_WEIGHT_SHA256 as BGE_MODEL_WEIGHT_SHA256,
    MAX_LENGTH as BGE_MAX_LENGTH,
    PROJECTION_MAX_CHARS as BGE_PROJECTION_MAX_CHARS,
)
from genshin_corpus.retrieval.candidate_retrieval import (
    ACCEPTED_QWEN_DENSE_ARM_BUILD_IDENTITY,
    ACCEPTED_QWEN_RU_BUILD_IDENTITY,
    _field_aware_lexical_arm_identity,
)
from genshin_corpus.retrieval.qwen_embedding import (
    DashScopeQwenEmbeddingConfig,
    DashScopeQwenEmbeddingTransport,
    QWEN_EMBEDDING_MODEL_ID,
    _dashscope_embedding_endpoint,
)


TARGETED_GATE_SCHEMA_VERSION = "phase04-targeted-semantic-gate-0.1"
TARGETED_CASE_MANIFEST_SCHEMA_VERSION = "phase04-targeted-case-manifest-0.1"
ASSESSOR_PROMPT_SCHEMA_VERSION = "phase04-evidence-assessor-prompt-0.3"
ASSESSOR_PROMPT_ID = "phase04-bounded-evidence-assessor"
ASSESSOR_PROMPT_VERSION = "0.3"
ASSESSOR_MODEL_ID = "qwen3.8-max"
HISTORICAL_QWEN38_EXECUTION_CONFIG_IDENTITY = (
    "88aafe9aefb9b2b8869dbb522b2ccbd0104fca036d3a5c7a18ca094eca6e8a0d"
)
# The immutable first-run parent was created with the pre-contract prompt.
# These identities are accepted only as historical lineage, never as current
# assessor bindings.
HISTORICAL_PARENT_ASSESSOR_PROMPT_IDENTITY = (
    "e1998714f565a93d7f01542c18df197dd2d66e99048e99f91bf426530185bd51"
)
HISTORICAL_PARENT_ASSESSOR_EXECUTION_CONFIG_IDENTITY = (
    "66e4868b4bcfdc4e9676dfda29460fb3a35f132187e1e6f926daeaa678522f77"
)
HISTORICAL_PARENT_ASSESSOR_SCHEMA_VERSION = "phase04-evidence-assessor-prompt-0.1"
ASSESSOR_REGION = "cn-beijing"
ASSESSOR_WORKSPACE = "ws-gdq9z4ufdb87egio"
ASSESSOR_ENDPOINT = (
    "https://ws-gdq9z4ufdb87egio.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)
ASSESSOR_OPERATING_POINT_SCHEMA_VERSION = "phase04-evidence-assessor-operating-point-0.1"
RUNTIME_INPUT_PATH = ".local/p04-rag-m2/questions.runtime.jsonl"
RUNTIME_INPUT_SHA256 = "dab333ddfe3061758596cf4196443df274a2f065b8c9c36cbbfe5c52bebe380c"
RETRIEVAL_UNIT_BUILD_IDENTITY = "49b48ee746716add0248fed388d10bd522a930efb582a0f5e827f66681ed8998"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SemanticGateContractError(ValueError):
    """Raised when a targeted-gate binding or persisted record is invalid."""


class EvidenceAssessorProviderError(RuntimeError):
    """Raised when the assessor transport cannot produce a valid response."""


ASSESSOR_SYSTEM_PROMPT = (
    "You are a strict evidence-condition assessor. Return exactly one raw JSON object "
    "and no Markdown, prose, code fence, or other text. The object must contain exactly "
    "nine keys, each exactly once, with no other keys: "
    "assessment_request_identity (non-empty single-line string; copy the exact supplied request identity), "
    "condition (string; one of sufficient, incomplete, conflicting, unable), "
    "action (string; one of answer_now, supplement_once, stop), "
    "answer_disposition (string; one of full, bounded_partial, none), "
    "supported_scope (array of strings), unresolved_aspects (array of strings), "
    "missing_information (array of strings), conflicts (array of strings), and "
    "supplemental_query (string or null). Every array entry must be a unique, non-empty, "
    "single-line string within its field. Classify only the supplied official evidence "
    "and preserve the original question exactly. Legal condition rules: sufficient "
    "requires missing_information and conflicts to be empty, cannot use supplement_once, "
    "and when using answer_now requires answer_disposition full. incomplete requires "
    "missing_information to be non-empty. conflicting requires conflicts to be non-empty. "
    "unable requires action stop, answer_disposition none, and unresolved_aspects to be "
    "non-empty. Legal action and disposition rules: answer_now requires answer_disposition "
    "full or bounded_partial and supplemental_query null. incomplete or conflicting may "
    "use answer_now only with bounded_partial. supplement_once requires incomplete or "
    "conflicting, answer_disposition none, and one concrete non-empty single-line "
    "supplemental_query different from both the original question and current query. stop "
    "requires answer_disposition none and supplemental_query null. bounded_partial requires "
    "incomplete or conflicting and requires both supported_scope and unresolved_aspects to "
    "be non-empty. supported_scope must be empty unless answer_disposition is bounded_partial. "
    "answer_disposition full requires unresolved_aspects to be empty. supported_scope, "
    "unresolved_aspects, and conflicts must not contain Evidence Packet citation IDs. Never "
    "invent provenance, facts, or authority."
)


def _reject_duplicate_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SemanticGateContractError(f"duplicate assessor JSON key: {key}")
        result[key] = value
    return result


@dataclass(frozen=True)
class ArtifactBinding:
    path: str
    sha256: str
    kind: str

    def __post_init__(self) -> None:
        if not self.path or Path(self.path).is_absolute() or "*" in self.path or "?" in self.path:
            raise SemanticGateContractError("artifact path must be relative and exact")
        if not _SHA256_RE.fullmatch(self.sha256):
            raise SemanticGateContractError("artifact SHA-256 is invalid")
        if not self.kind:
            raise SemanticGateContractError("artifact kind is required")

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256, "kind": self.kind}


@dataclass(frozen=True)
class PacketArtifactBinding:
    arm: str
    path: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"arm": self.arm, "path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class PacketSourceIdentityBinding:
    arm: str
    path: str
    source_identity_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if tuple(sorted(self.source_identity_keys)) != self.source_identity_keys:
            raise SemanticGateContractError("Packet source identities must be sorted")
        if len(set(self.source_identity_keys)) != len(self.source_identity_keys):
            raise SemanticGateContractError("Packet source identities must be unique")
        if any(not isinstance(item, str) or not item for item in self.source_identity_keys):
            raise SemanticGateContractError("Packet source identities must be non-empty text")

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "path": self.path,
            "source_identity_keys": list(self.source_identity_keys),
            "count": len(self.source_identity_keys),
            "sha256": sha256_json(list(self.source_identity_keys)),
        }


@dataclass(frozen=True)
class TargetedCaseBinding:
    question_id: str
    question: str
    question_identity: str
    historical_role: str
    classification: str
    source_artifacts: tuple[ArtifactBinding, ...]
    packet_artifacts: tuple[PacketArtifactBinding, ...]
    packet_source_identities: tuple[PacketSourceIdentityBinding, ...]
    primary_packet_path: str
    retrieval_unit_build_identity: str = RETRIEVAL_UNIT_BUILD_IDENTITY
    source_identity_count: int = 0
    source_identity_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "question_identity": self.question_identity,
            "historical_role": self.historical_role,
            "classification": self.classification,
            "retrieval_unit_build_identity": self.retrieval_unit_build_identity,
            "source_identity": {
                "count": self.source_identity_count,
                "sha256": self.source_identity_sha256,
            },
            "source_artifacts": [item.to_dict() for item in self.source_artifacts],
            "packet_artifacts": [item.to_dict() for item in self.packet_artifacts],
            "packet_source_identities": [item.to_dict() for item in self.packet_source_identities],
            "primary_packet_path": self.primary_packet_path,
        }


@dataclass(frozen=True)
class TargetedCaseManifest:
    cases: tuple[TargetedCaseBinding, ...]
    runtime_input: ArtifactBinding

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TARGETED_CASE_MANIFEST_SCHEMA_VERSION,
            "runtime_input": self.runtime_input.to_dict(),
            "cases": [case.to_dict() for case in self.cases],
        }

    @property
    def manifest_identity(self) -> str:
        return sha256_json(self.to_dict())


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repo_path(repo_root: Path, relative: str) -> Path:
    candidate = (Path(repo_root) / relative).resolve()
    root = Path(repo_root).resolve()
    if candidate != root and root not in candidate.parents:
        raise SemanticGateContractError("artifact path escapes repository root")
    return candidate


def _validate_hash(repo_root: Path, artifact: ArtifactBinding | PacketArtifactBinding) -> Path:
    path = _repo_path(repo_root, artifact.path)
    if not path.is_file():
        raise SemanticGateContractError(f"bound artifact is missing: {artifact.path}")
    actual = _sha256_file(path)
    if actual != artifact.sha256:
        raise SemanticGateContractError(f"bound artifact hash mismatch: {artifact.path}")
    return path


def _packet_source_identity_keys(packet: Mapping[str, Any]) -> tuple[str, ...]:
    keys: set[str] = set()
    evidence_items = packet.get("evidence")
    if not isinstance(evidence_items, list):
        raise SemanticGateContractError("Packet evidence must be a list")
    for evidence in evidence_items:
        if not isinstance(evidence, Mapping) or not isinstance(evidence.get("members"), list):
            raise SemanticGateContractError("Packet evidence members are invalid")
        for member in evidence["members"]:
            if not isinstance(member, Mapping):
                raise SemanticGateContractError("Packet evidence member is invalid")
            address = member.get("canonical_address")
            if not isinstance(address, Mapping) or not isinstance(address.get("source_identity_key"), str):
                raise SemanticGateContractError("Packet member lacks canonical source identity")
            keys.add(address["source_identity_key"])
    return tuple(sorted(keys))


def _load_runtime_questions(repo_root: Path) -> dict[str, dict[str, str]]:
    path = _repo_path(repo_root, RUNTIME_INPUT_PATH)
    if _sha256_file(path) != RUNTIME_INPUT_SHA256:
        raise SemanticGateContractError("runtime question input hash mismatch")
    rows: dict[str, dict[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SemanticGateContractError("runtime question input is not valid JSONL") from exc
        if not isinstance(row, Mapping) or not isinstance(row.get("question_id"), str) or not isinstance(row.get("question"), str):
            raise SemanticGateContractError("runtime question row is invalid")
        question_id = str(row["question_id"])
        if question_id in rows:
            raise SemanticGateContractError(f"duplicate runtime question: {question_id}")
        rows[question_id] = {"question_id": question_id, "question": str(row["question"])}
    return rows


_CASE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "Q011",
        "role": "retrieval/query-recall missing-evidence diagnostic",
        "classification": "advancement_candidate",
        "source": (
            (".local/p04-rag-qwen-q011-formal-deferred-oracle-20260912/q011_oracle.json", "e70e0694ce23b39f9fc64d01ead6edaab478da2f7688ea8792cc55ada5f1b5ad", "oracle"),
            (".local/p04-rag-qwen-q011-formal-deferred-oracle-20260912/q011_prose_correction_sidecar.json", "f1aa42f75ab2ed737ce30157ee123b37f033451c7e48b92c87043f4f5456ac39", "correction_sidecar"),
            (".local/p04-qwen-rerank-70q-20260912-r1/metadata/manifest.json", "967b84357349188bb0aeac72b9cb4766e7c92196ef7b161b0dcf431a7430e205", "historical_manifest"),
        ),
        "packets": (
            ("control", ".local/p04-qwen-rerank-70q-20260912-r1/Q011/control/packet.json", "53552efe2f673a8210fc66562a6220b444d3d5b3203ea8191d92d75c7f65c736"),
            ("challenger", ".local/p04-qwen-rerank-70q-20260912-r1/Q011/challenger/packet.json", "1a598ca26c86404f026aeedf7edd12933ac301b3ad4a2f6050a7f654d0054277"),
        ),
        "primary": ".local/p04-qwen-rerank-70q-20260912-r1/Q011/control/packet.json",
        "source_count": 9,
        "source_hash": "440580ad61ec70f7e3cd72ac18449e2f6578d951e6f79a136ab622f386befb95",
    },
    {
        "id": "Q015", "role": "accepted bounded upstream-regression control", "classification": "control_only",
        "source": ((".local/p04-qwen-rerank-70q-20260912-r1/metadata/manifest.json", "967b84357349188bb0aeac72b9cb4766e7c92196ef7b161b0dcf431a7430e205", "historical_manifest"),),
        "packets": (("control", ".local/p04-qwen-rerank-70q-20260912-r1/Q015/control/packet.json", "2d4901391a887afbd5e2ede5feb7a5e9a8d86bfbaa158bed9876466c1087fe67"), ("challenger", ".local/p04-qwen-rerank-70q-20260912-r1/Q015/challenger/packet.json", "65aeab401f2ccbc18b93e9730b03866e60e5ce3c9a705a1ea24ddcd60dac3803")),
        "primary": ".local/p04-qwen-rerank-70q-20260912-r1/Q015/control/packet.json", "source_count": 15, "source_hash": "cda8c4ded9c24b135fbe2a799ad9bf7352d4afdb77f5917f7cf725617246705d",
    },
    {
        "id": "Q045", "role": "adequate-evidence timeline-interpretation control", "classification": "control_only",
        "source": ((".local/p04-qwen-rerank-70q-20260912-c1/metadata/manifest.json", "effce4ef8a9f626bc161c733427b18230b5a162697bfdcc02cc11fadfeea7c90", "historical_manifest"),),
        "packets": (("challenger", ".local/p04-qwen-rerank-70q-20260912-c1/Q045/challenger/packet.json", "60f7b063b6ab29d840d96a7f29ee6b1301244af22d22c73e7e93ae34f213c7ab"),),
        "primary": ".local/p04-qwen-rerank-70q-20260912-c1/Q045/challenger/packet.json", "source_count": 5, "source_hash": "e58ae0e5962ddcbd4b29a3319ea9a9cf133df7263b0f843e390b4da2b1efafc8",
    },
    {
        "id": "Q049", "role": "positive deep-rescue control", "classification": "control_only",
        "source": ((".local/p04-rerank-fusion-challenger-20260913-repaired-run1/metadata/manifest.json", "8241c5e3194a5964ab7b3bac0ad801f293ffacc8fd8de7197836fae50889368b", "historical_manifest"), (".local/p04-rerank-fusion-answer-validation-live-20260913-164432/metadata/manifest.json", "97ff4e1c94e26c74ed19b525c7bb145a7cf2d204611fb06c864af5d527e8b08f", "generation_manifest"), (".local/p04-rerank-fusion-answer-validation-live-20260913-164432/results/Q049/generation_result.json", "115a6dae9bb4b9e6ac162f70575b38bdc3531995b1fdcaf7e4da600721b33319", "generation")),
        "packets": (("fused", ".local/p04-rerank-fusion-challenger-20260913-repaired-run1/packets/Q049/fused.json", "92b0411683b952622de26259455d387d87d2addaa5320fb7fe44e794d548039b"),),
        "primary": ".local/p04-rerank-fusion-challenger-20260913-repaired-run1/packets/Q049/fused.json", "source_count": 11, "source_hash": "7ed099e7b15916e26f48248b8c39d50ec70314d3c69827abdd777e2af5afca0b",
    },
    {
        "id": "Q050", "role": "mixed candidate/coverage plus synthesis diagnostic", "classification": "advancement_candidate_if_clean_missing_evidence_else_diagnostic",
        "source": ((".local/p04-qwen-rerank-70q-20260912-c1/metadata/manifest.json", "effce4ef8a9f626bc161c733427b18230b5a162697bfdcc02cc11fadfeea7c90", "historical_manifest"),),
        "packets": (("challenger", ".local/p04-qwen-rerank-70q-20260912-c1/Q050/challenger/packet.json", "b349129abab7e3d5c0f769cc1c97e8a2bae69244a5bc01adb1c6ebb69042ce64"),),
        "primary": ".local/p04-qwen-rerank-70q-20260912-c1/Q050/challenger/packet.json", "source_count": 7, "source_hash": "81235f2ec0d68d8b1f0fe17ea2d3efbd40d8d1289d090e91903944e932ee132f",
    },
    {
        "id": "Q052", "role": "time-scope/mixed-evidence Generation diagnostic", "classification": "control_only",
        "source": ((".local/p04-qwen-rerank-70q-20260912-c1/metadata/manifest.json", "effce4ef8a9f626bc161c733427b18230b5a162697bfdcc02cc11fadfeea7c90", "historical_manifest"),),
        "packets": (("challenger", ".local/p04-qwen-rerank-70q-20260912-c1/Q052/challenger/packet.json", "58759f5cc2a7f379354a4fdaf469fe9d8dee225930bb23994ab6579e1e2a415d"),),
        "primary": ".local/p04-qwen-rerank-70q-20260912-c1/Q052/challenger/packet.json", "source_count": 2, "source_hash": "778e48d704c48276d00884fc61910444115008fd72bd2ba5ee7bb46bdc447fa9",
    },
    {
        "id": "Q056", "role": "confounded negative/control", "classification": "control_only_excluded_from_advancement",
        "source": ((".local/p04-rerank-fusion-challenger-20260913-repaired-run1/metadata/manifest.json", "8241c5e3194a5964ab7b3bac0ad801f293ffacc8fd8de7197836fae50889368b", "historical_manifest"), (".local/p04-qwen38-max-instruction-v02-live-b781d095f595446c81fd83f673b473ae/metadata/manifest.json", "25de9d1385fb3db6c43f4abe47ebae9c24835e82fba3187c858b8562d0a5951f", "historical_generation_manifest"), (".local/p04-qwen38-max-instruction-v02-live-b781d095f595446c81fd83f673b473ae/results/Q056/generation_result.json", "430912d97505b7ea7782eaf09709021c5c3b3d6e53ecee3a5cf0237a10840fe7", "generation")),
        "packets": (("fused", ".local/p04-rerank-fusion-challenger-20260913-repaired-run1/packets/Q056/fused.json", "aa771cee883e3f952bb2383e6483025f31d1d0003d24efa30cfc7e9f533b3a0c"),),
        "primary": ".local/p04-rerank-fusion-challenger-20260913-repaired-run1/packets/Q056/fused.json", "source_count": 5, "source_hash": "3442181be7eb1ed8eeff2486ce61c18bcfc3d7994901bc80bbda497786caaaf3",
    },
    {
        "id": "Q058", "role": "adequate-evidence timeline-interpretation control", "classification": "control_only",
        "source": ((".local/p04-qwen-rerank-70q-20260912-c1/metadata/manifest.json", "effce4ef8a9f626bc161c733427b18230b5a162697bfdcc02cc11fadfeea7c90", "historical_manifest"),),
        "packets": (("challenger", ".local/p04-qwen-rerank-70q-20260912-c1/Q058/challenger/packet.json", "4d2aa4fd514a2c4425e866c7ec93ef77cadc9929087ef976b5c67c88ac084c9c"),),
        "primary": ".local/p04-qwen-rerank-70q-20260912-c1/Q058/challenger/packet.json", "source_count": 4, "source_hash": "ab55fae09b5fd8c22f6f0db1b24a3de4b314c3be7b7d4f15ebd9ff8004efc462",
    },
    {
        "id": "Q068", "role": "admission/identity safety diagnostic", "classification": "falsification_only",
        "source": ((".local/p04-rerank-fusion-challenger-20260913-repaired-run1/metadata/manifest.json", "8241c5e3194a5964ab7b3bac0ad801f293ffacc8fd8de7197836fae50889368b", "historical_manifest"), (".local/p04-rerank-fusion-answer-validation-live-20260913-164432/metadata/manifest.json", "97ff4e1c94e26c74ed19b525c7bb145a7cf2d204611fb06c864af5d527e8b08f", "generation_manifest"), (".local/p04-rerank-fusion-answer-validation-live-20260913-164432/results/Q068/generation_result.json", "959f5ee8f2c1adf6ceda035da87ac0c9dd8a9b46c01f778adecb0d588bb51e9b", "generation")),
        "packets": (("fused", ".local/p04-rerank-fusion-challenger-20260913-repaired-run1/packets/Q068/fused.json", "988effc2c0f636601bac86c81c63b88b6f343ca0742d71ec9ca1eed0798c6175"),),
        "primary": ".local/p04-rerank-fusion-challenger-20260913-repaired-run1/packets/Q068/fused.json", "source_count": 2, "source_hash": "f654cc1d71d72adc4b6d8bc15070d9b640f5178d60bcfcb89d72a6cc26eeb097",
    },
)


def build_targeted_case_manifest(repo_root: Path) -> TargetedCaseManifest:
    """Resolve and validate all immutable pre-call case bindings."""

    root = Path(repo_root)
    questions = _load_runtime_questions(root)
    runtime_binding = ArtifactBinding(RUNTIME_INPUT_PATH, RUNTIME_INPUT_SHA256, "runtime_questions")
    cases: list[TargetedCaseBinding] = []
    for spec in _CASE_SPECS:
        question_id = str(spec["id"])
        row = questions.get(question_id)
        if row is None:
            raise SemanticGateContractError(f"targeted question is missing: {question_id}")
        question = row["question"]
        question_identity = sha256_json({"question_id": question_id, "question": question})
        source_artifacts = tuple(ArtifactBinding(*item) for item in spec["source"])
        packet_artifacts = tuple(PacketArtifactBinding(*item) for item in spec["packets"])
        for artifact in source_artifacts:
            _validate_hash(root, artifact)
        packet_source_identities: list[PacketSourceIdentityBinding] = []
        for artifact in packet_artifacts:
            packet_path = _validate_hash(root, artifact)
            packet_value = json.loads(packet_path.read_text(encoding="utf-8"))
            if not isinstance(packet_value, Mapping):
                raise SemanticGateContractError(f"bound Packet is not an object: {artifact.path}")
            packet_binding = bind_evidence_packet(packet_value)
            if packet_binding.static_identity["retrieval_unit_build_identity"] != RETRIEVAL_UNIT_BUILD_IDENTITY:
                raise SemanticGateContractError(f"Retrieval Unit build mismatch for {question_id}: {artifact.path}")
            source_keys = _packet_source_identity_keys(packet_value)
            packet_source_identities.append(PacketSourceIdentityBinding(artifact.arm, artifact.path, source_keys))
        primary = next((item for item in packet_artifacts if item.path == spec["primary"]), None)
        if primary is None:
            raise SemanticGateContractError(f"primary Packet is not bound for {question_id}")
        source_keys = next(item.source_identity_keys for item in packet_source_identities if item.path == primary.path)
        if len(source_keys) != int(spec["source_count"]) or sha256_json(source_keys) != spec["source_hash"]:
            raise SemanticGateContractError(f"source identity binding mismatch for {question_id}")
        cases.append(TargetedCaseBinding(
            question_id=question_id,
            question=question,
            question_identity=question_identity,
            historical_role=str(spec["role"]),
            classification=str(spec["classification"]),
            source_artifacts=source_artifacts,
            packet_artifacts=packet_artifacts,
            packet_source_identities=tuple(packet_source_identities),
            primary_packet_path=str(spec["primary"]),
            source_identity_count=int(spec["source_count"]),
            source_identity_sha256=str(spec["source_hash"]),
        ))
    if {item.question_id for item in cases} != {"Q011", "Q015", "Q045", "Q049", "Q050", "Q052", "Q056", "Q058", "Q068"}:
        raise SemanticGateContractError("targeted case set is incomplete")
    return TargetedCaseManifest(tuple(cases), runtime_binding)


def _assessment_prompt_projection(request: EvidenceAssessmentRequest) -> dict[str, Any]:
    generation_request = project_generation_request(request.packet, question=request.original_question)
    return {
        "schema_version": ASSESSOR_PROMPT_SCHEMA_VERSION,
        "request_identity": request.request_identity,
        "original_question": request.original_question,
        "current_query": request.current_query,
        "round_index": request.round_index,
        "supplements_remaining": request.supplements_remaining,
        "packet_binding": request.packet_binding.to_dict(),
        "evidence": [item.to_dict() for item in generation_request.evidence],
    }


def _strict_assessment_object(value: Any, request: EvidenceAssessmentRequest) -> EvidenceAssessmentResult:
    if not isinstance(value, Mapping):
        raise SemanticGateContractError("assessor response must be a JSON object")
    allowed = {
        "assessment_request_identity", "condition", "action", "answer_disposition",
        "supported_scope", "unresolved_aspects", "missing_information", "conflicts", "supplemental_query",
    }
    if set(value) != allowed:
        raise SemanticGateContractError("assessor response has unknown or missing fields")
    if not isinstance(value.get("assessment_request_identity"), str):
        raise SemanticGateContractError("assessor request identity must be text")
    for field in ("supported_scope", "unresolved_aspects", "missing_information", "conflicts"):
        raw = value.get(field)
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise SemanticGateContractError(f"assessor {field} must be a string list")
    if value.get("supplemental_query") is not None and not isinstance(value.get("supplemental_query"), str):
        raise SemanticGateContractError("assessor supplemental_query must be text or null")
    if value.get("action") == "supplement_once":
        supplemental_query = value.get("supplemental_query")
        if (
            not isinstance(supplemental_query, str)
            or not supplemental_query.strip()
            or "\n" in supplemental_query
            or "\r" in supplemental_query
            or supplemental_query in {request.original_question, request.current_query}
        ):
            raise SemanticGateContractError("assessor supplemental query is invalid")
    try:
        result = EvidenceAssessmentResult(
            assessment_request_identity=value["assessment_request_identity"],
            condition=value["condition"],
            action=value["action"],
            answer_disposition=value["answer_disposition"],
            supported_scope=tuple(value["supported_scope"]),
            unresolved_aspects=tuple(value["unresolved_aspects"]),
            missing_information=tuple(value["missing_information"]),
            conflicts=tuple(value["conflicts"]),
            supplemental_query=value["supplemental_query"],
        )
    except Exception as exc:
        raise SemanticGateContractError("assessor response violates the assessment contract") from exc
    if result.assessment_request_identity != request.request_identity:
        raise SemanticGateContractError("assessor response is bound to another request")
    return result


def parse_assessment_response(text: str, request: EvidenceAssessmentRequest) -> EvidenceAssessmentResult:
    if not isinstance(text, str) or not text.strip() or text.lstrip().startswith("```"):
        raise SemanticGateContractError("assessor response must be raw JSON text")
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_object_pairs)
    except (json.JSONDecodeError, SemanticGateContractError) as exc:
        if isinstance(exc, SemanticGateContractError):
            raise
        raise SemanticGateContractError("assessor response is not valid JSON") from exc
    return _strict_assessment_object(value, request)


@dataclass
class BailianEvidenceAssessor:
    config: BailianControlConfig
    transport: BailianTransport
    response_artifact_root: Path | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            self.config.region != ASSESSOR_REGION
            or self.config.endpoint != ASSESSOR_ENDPOINT
            or self.config.workspace != ASSESSOR_WORKSPACE
            or self.config.model_id != ASSESSOR_MODEL_ID
            or self.config.model_reference_policy != REQUESTED_ALIAS_POLICY
            or not self.config.enable_thinking
            or self.config.thinking_budget != 4096
            or self.config.temperature != 0.0
            or self.config.max_output_tokens != 2048
            or self.config.max_attempts != 1
            or self.config.timeout_seconds != 30.0
        ):
            raise SemanticGateContractError("assessor configuration is not the frozen targeted operating point")
        try:
            if workspace_from_bailian_base_url(self.config.endpoint) != self.config.workspace:
                raise SemanticGateContractError("assessor endpoint/workspace binding mismatch")
        except Exception as exc:
            if isinstance(exc, SemanticGateContractError):
                raise
            raise SemanticGateContractError("assessor endpoint is not a valid Beijing Bailian origin") from exc
        current_identity = self.execution_config_identity
        if not _SHA256_RE.fullmatch(current_identity) or current_identity == HISTORICAL_QWEN38_EXECUTION_CONFIG_IDENTITY:
            raise SemanticGateContractError("current assessor identity is invalid or historical")
        self.provider_network_calls = 0
        self.last_audit: dict[str, Any] = {}
        self.audit_history: list[dict[str, Any]] = []

    def bind_response_artifact_root(self, root: Path) -> None:
        """Bind one no-overwrite case-local root for raw response evidence."""

        path = Path(root)
        if path.exists() and not path.is_dir():
            raise SemanticGateContractError("assessor response artifact root is not a directory")
        path.mkdir(parents=True, exist_ok=True)
        self.response_artifact_root = path

    def _store_audit(self, audit: dict[str, Any]) -> dict[str, Any]:
        record = dict(audit)
        self.last_audit = record
        self.audit_history.append(record)
        return record

    def _response_evidence(
        self,
        request: EvidenceAssessmentRequest,
        response: Any,
        *,
        wall_clock_ms: float,
    ) -> dict[str, Any]:
        raw_answer_text = response.answer_text if isinstance(response, BailianTransportResponse) and isinstance(response.answer_text, str) else None
        usage = response.usage if isinstance(response, BailianTransportResponse) else None
        provider_request_id = response.provider_request_id if isinstance(response, BailianTransportResponse) else None
        finish_reason = response.finish_reason if isinstance(response, BailianTransportResponse) else None
        return {
            "schema_version": "phase04-evidence-assessor-response-0.1",
            "request_identity": request.request_identity,
            "execution_config_identity": self.execution_config_identity,
            "provider_request_id": provider_request_id if isinstance(provider_request_id, str) else None,
            "usage": dict(usage) if isinstance(usage, Mapping) else None,
            "finish_reason": finish_reason if isinstance(finish_reason, str) else None,
            "response_type": type(response).__name__,
            "raw_answer_text": raw_answer_text,
            "raw_answer_text_sha256": sha256(raw_answer_text.encode("utf-8")).hexdigest() if raw_answer_text is not None else None,
            "attempt_count": 1,
            "wall_clock_ms": round(wall_clock_ms * 1000.0, 3),
        }

    def _persist_response_evidence(self, evidence: Mapping[str, Any]) -> dict[str, Any] | None:
        if self.response_artifact_root is None:
            return None
        path = self.response_artifact_root / f"response-{self.provider_network_calls:04d}.json"
        body = canonical_json_bytes({
            "schema_version": "phase04-evidence-assessor-response-artifact-0.1",
            "response_evidence": dict(evidence),
        })
        _write_no_overwrite(path, body)
        return {
            "path": str(path),
            "sha256": sha256(body).hexdigest(),
            "byte_count": len(body),
        }

    @property
    def prompt_identity(self) -> str:
        return sha256_json({"prompt_id": ASSESSOR_PROMPT_ID, "version": ASSESSOR_PROMPT_VERSION, "text": ASSESSOR_SYSTEM_PROMPT})

    @property
    def execution_config_identity(self) -> str:
        return sha256_json({
            "operating_point_schema_version": ASSESSOR_OPERATING_POINT_SCHEMA_VERSION,
            "assessor_config": self.config.audit_projection(),
            "prompt_id": ASSESSOR_PROMPT_ID,
            "prompt_version": ASSESSOR_PROMPT_VERSION,
            "prompt_identity": self.prompt_identity,
            "schema_version": ASSESSOR_PROMPT_SCHEMA_VERSION,
        })

    def invocation_payload(self, request: EvidenceAssessmentRequest) -> dict[str, Any]:
        return {
            "model": self.config.model_id,
            "messages": [
                {"role": "system", "content": ASSESSOR_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(_assessment_prompt_projection(request), ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
            ],
            "generation_parameters": {
                "enable_thinking": True,
                "thinking_budget": 4096,
                "temperature": 0.0,
                "max_output_tokens": 2048,
            },
            "workspace": self.config.workspace,
        }

    def assess(self, request: EvidenceAssessmentRequest) -> EvidenceAssessmentResult:
        payload = self.invocation_payload(request)
        started = time.perf_counter()
        try:
            self.provider_network_calls += 1
            response = self.transport.invoke(payload, timeout_seconds=float(self.config.timeout_seconds))
        except (BailianTransportError, TimeoutError) as exc:
            self._store_audit({
                "status": "provider_error",
                "request_identity": request.request_identity,
                "execution_config_identity": self.execution_config_identity,
                "attempt_count": 1,
                "provider_network_calls": self.provider_network_calls,
                "wall_clock_ms": round((time.perf_counter() - started) * 1000, 3),
                "provider_error": {
                    "category": type(exc).__name__,
                    "code": exc.code if isinstance(exc, BailianTransportError) else type(exc).__name__,
                    "status_code": exc.status_code if isinstance(exc, BailianTransportError) else None,
                    "provider_request_id": exc.provider_request_id if isinstance(exc, BailianTransportError) else None,
                },
                "response_evidence": None,
                "currency_cost": "UNKNOWN",
            })
            raise EvidenceAssessorProviderError("assessor provider call failed") from exc
        response_evidence = self._response_evidence(
            request,
            response,
            wall_clock_ms=time.perf_counter() - started,
        )
        response_artifact = self._persist_response_evidence(response_evidence)
        if response_artifact is not None:
            response_evidence["raw_response_artifact"] = response_artifact
        received_audit = self._store_audit({
            "status": "response_received",
            "request_identity": request.request_identity,
            "execution_config_identity": self.execution_config_identity,
            "attempt_count": 1,
            "provider_network_calls": self.provider_network_calls,
            "response_evidence": response_evidence,
            "currency_cost": "UNKNOWN",
        })
        if not isinstance(response, BailianTransportResponse) or not isinstance(response.answer_text, str):
            received_audit.update({
                "status": "response_invalid",
                "parse_failure": {
                    "category": "response_invalid",
                    "code": "non_text_answer",
                    "reason": "assessor provider response must expose string answer_text",
                },
                "wall_clock_ms": round((time.perf_counter() - started) * 1000, 3),
            })
            raise EvidenceAssessorProviderError("assessor provider response is invalid")
        try:
            result = parse_assessment_response(response.answer_text, request)
        except SemanticGateContractError as exc:
            received_audit.update({
                "status": "response_invalid",
                "parse_failure": {
                    "category": type(exc).__name__,
                    "code": type(exc).__name__,
                    "reason": str(exc),
                },
                "wall_clock_ms": round((time.perf_counter() - started) * 1000, 3),
            })
            raise EvidenceAssessorProviderError("assessor response failed strict validation") from exc
        received_audit.update({
            "status": "succeeded",
            "result_identity": result.result_identity,
            "provider_request_id": response.provider_request_id,
            "usage": dict(response.usage) if isinstance(response.usage, Mapping) else None,
            "wall_clock_ms": round((time.perf_counter() - started) * 1000, 3),
        })
        return result


@dataclass(frozen=True)
class SharedRound0Replay:
    question: str
    adaptive_execution_identity: str
    block_a_execution_identity: str
    backend_result: Mapping[str, Any]
    packet_sha256: str
    packet_binding: Mapping[str, Any]
    retrieval_request_identity: str
    source_backend_result: Mapping[str, Any]
    source_backend_sha256: str

    @classmethod
    def capture(cls, block_a: Any, *, question: str, execution_identity: str) -> "SharedRound0Replay":
        round_identity = f"{execution_identity}.round0"
        result = block_a.run_round(query=question, round_index=0, request_identity=round_identity)
        return cls.from_result(result, question=question, adaptive_execution_identity=execution_identity)

    @classmethod
    def from_result(
        cls, result: Mapping[str, Any], *, question: str, adaptive_execution_identity: str,
        legacy_block_a_execution_identity: str | None = None,
    ) -> "SharedRound0Replay":
        round_identity = f"{adaptive_execution_identity}.round0"
        expected_source_identity = legacy_block_a_execution_identity or round_identity
        if not isinstance(result, Mapping) or result.get("status") != "succeeded":
            raise AdaptiveContractError("round-0 capture failed")
        query = result.get("query")
        if not isinstance(query, Mapping) or query.get("question_text") != question or query.get("execution_identity") != expected_source_identity:
            raise AdaptiveContractError("round-0 capture identity mismatch")
        packet = result.get("evidence_packet")
        binding = bind_evidence_packet(packet) if isinstance(packet, Mapping) else None
        if binding is None:
            raise AdaptiveContractError("round-0 capture lacks a valid Packet")
        audit = result.get("audit")
        embedding = audit.get("embedding") if isinstance(audit, Mapping) else None
        retrieval_request_identity = embedding.get("request_identity") if isinstance(embedding, Mapping) else None
        if not isinstance(retrieval_request_identity, str) or not retrieval_request_identity:
            raise AdaptiveContractError("round-0 capture lacks retrieval request identity")
        source = dict(result)
        projected = dict(source)
        projected["query"] = {**query, "execution_identity": round_identity}
        return cls(question, adaptive_execution_identity, expected_source_identity, projected,
                   binding.packet_sha256, binding.to_dict(), retrieval_request_identity, source,
                   sha256_json(source))

    def capability(self, delegate: Any) -> "ReplayBlockACapability":
        return ReplayBlockACapability(self, delegate)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "execution_identity": self.block_a_execution_identity,
            "adaptive_execution_identity": self.adaptive_execution_identity,
            "round0_request_identity": f"{self.adaptive_execution_identity}.round0",
            "packet_sha256": self.packet_sha256,
            "packet_binding": dict(self.packet_binding),
            "retrieval_request_identity": self.retrieval_request_identity,
            "source_backend_sha256": self.source_backend_sha256,
        }


@dataclass
class ReplayBlockACapability:
    replay: SharedRound0Replay
    delegate: Any

    def run_round(self, *, query: str, round_index: int, request_identity: str) -> Mapping[str, Any]:
        if round_index == 0:
            if query != self.replay.question or request_identity != f"{self.replay.adaptive_execution_identity}.round0":
                raise AdaptiveContractError("shared round-0 replay identity mismatch")
            source = self.replay.source_backend_result
            if (sha256_json(source) != self.replay.source_backend_sha256
                    or source.get("query", {}).get("execution_identity") != self.replay.block_a_execution_identity
                    or source.get("query", {}).get("question_text") != query
                    or bind_evidence_packet(source.get("evidence_packet")).to_dict() != self.replay.packet_binding
                    or bind_evidence_packet(self.replay.backend_result.get("evidence_packet")).to_dict() != self.replay.packet_binding
                    or self.replay.backend_result.get("audit", {}).get("embedding", {}).get("request_identity") != self.replay.retrieval_request_identity):
                raise AdaptiveContractError("shared round-0 source/Packet identity mismatch")
            return self.replay.backend_result
        if round_index != 1 or request_identity != f"{self.replay.adaptive_execution_identity}.round1" or not isinstance(query, str) or not query.strip():
            raise AdaptiveContractError("supplemental round identity mismatch")
        return self.delegate.run_round(query=query, round_index=round_index, request_identity=request_identity)

    def rebuild_packet(self, ranked_candidates: Sequence[Mapping[str, Any]], *, retrieval_audit: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.delegate.rebuild_packet(ranked_candidates, retrieval_audit=retrieval_audit)


@dataclass
class ReusingGenerationProvider:
    delegate: GenerationProvider
    reusable: Mapping[str, GenerationResult]
    reused: list[str] = field(default_factory=list)
    call_audit: list[dict[str, Any]] = field(default_factory=list)

    def generate(self, request: Any) -> GenerationResult:
        identity = request.semantic_request_identity
        started = time.perf_counter()
        cached = self.reusable.get(identity)
        if cached is not None:
            self.reused.append(identity)
            self.call_audit.append({
                "semantic_request_identity": identity,
                "reused": True,
                "provider_network_call": False,
                "attempt_count": 0,
                "wall_clock_ms": round((time.perf_counter() - started) * 1000, 3),
                "token_usage": "reused_baseline_occurrence",
                "currency_cost": "UNKNOWN",
            })
            return cached
        result = self.delegate.generate(request)
        attempts = result.provider_audit.get("attempts") if isinstance(result.provider_audit, Mapping) else None
        self.call_audit.append({
            "semantic_request_identity": identity,
            "reused": False,
            "provider_network_call": True,
            "attempt_count": len(attempts) if isinstance(attempts, list) else "UNKNOWN",
            "wall_clock_ms": round((time.perf_counter() - started) * 1000, 3),
            "token_usage": [item.get("usage") for item in attempts if isinstance(item, Mapping)] if isinstance(attempts, list) else "UNKNOWN",
            "currency_cost": "UNKNOWN",
        })
        return result


def _write_no_overwrite(path: Path, body: bytes) -> None:
    if path.exists():
        if path.read_bytes() != body:
            raise FileExistsError(f"refusing to overwrite existing gate artifact: {path}")
        return
    atomic_write(path, body)


def persist_targeted_case_manifest(manifest: TargetedCaseManifest, output_path: Path) -> dict[str, Any]:
    """Persist one validated pre-call manifest without replacing different bytes."""

    if not isinstance(manifest, TargetedCaseManifest):
        raise SemanticGateContractError("targeted case manifest type is invalid")
    body = canonical_json_bytes(manifest.to_dict())
    path = Path(output_path)
    _write_no_overwrite(path, body)
    return {
        "path": str(path),
        "sha256": sha256(body).hexdigest(),
        "manifest_identity": manifest.manifest_identity,
        "case_count": len(manifest.cases),
    }


def persist_round0_replay(replay: SharedRound0Replay, output_root: Path, question_id: str) -> dict[str, Any]:
    root = Path(output_root) / "round0" / question_id
    root.mkdir(parents=True, exist_ok=True)
    result_body = canonical_json_bytes(dict(replay.source_backend_result))
    packet_body = canonical_json_bytes(replay.source_backend_result["evidence_packet"])
    _write_no_overwrite(root / "block_a_result.json", result_body)
    _write_no_overwrite(root / "evidence_packet.json", packet_body)
    descriptor = {
        "schema_version": "phase04-shared-round0-replay-0.1",
        **replay.to_dict(),
        "artifacts": {
            "block_a_result": {"path": "block_a_result.json", "sha256": sha256(result_body).hexdigest()},
            "evidence_packet": {"path": "evidence_packet.json", "sha256": sha256(packet_body).hexdigest()},
        },
    }
    descriptor_body = canonical_json_bytes(descriptor)
    _write_no_overwrite(root / "round0_replay.json", descriptor_body)
    return descriptor


PARENT_LIVE_ROOT = Path(".local/p04-targeted-semantic-gate-live-20260919-172106")
PARENT_MANIFEST_IDENTITY = "9b070932497c039a6d1933d44979cdb60893e4a079d3f4f5bb25e7704e785f48"


def _read_bound_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_object_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SemanticGateContractError(f"parent artifact is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise SemanticGateContractError(f"parent artifact must be an object: {path}")
    return value


def _require_parent(condition: bool, detail: str) -> None:
    if not condition:
        raise SemanticGateContractError(f"parent targeted-gate binding mismatch: {detail}")


def _parent_artifact(path: Path, descriptor: Mapping[str, Any], expected_name: str) -> dict[str, Any]:
    _require_parent(descriptor.get("path") == expected_name, f"{expected_name} descriptor path")
    _require_parent(path.is_file() and _sha256_file(path) == descriptor.get("sha256"), f"{expected_name} SHA-256")
    if "byte_count" in descriptor:
        _require_parent(path.stat().st_size == descriptor["byte_count"], f"{expected_name} byte count")
    return _read_bound_json(path)


def _generation_from_parent(value: Mapping[str, Any], request: Any, config_identity: str) -> GenerationResult:
    result = value.get("result")
    audit = value.get("audit")
    _require_parent(isinstance(result, Mapping) and isinstance(audit, Mapping), "baseline Generation shape")
    _require_parent(result.get("execution_status") == "succeeded", "baseline Generation status")
    _require_parent(audit.get("semantic_request_identity") == request.semantic_request_identity
                    and audit.get("execution_config_identity") == config_identity
                    and audit.get("request") == request.audit_projection(), "baseline Generation request/config")
    validation = CitationValidation(
        tuple(result["citation_tokens"]), result["citation_integrity"], result["citation_coverage"],
        tuple(result["validation_reasons"]), tuple(result["citation_normalizations"]),
    )
    reconstructed = GenerationResult(
        result["execution_status"], result["answer_text"], validation,
        request.semantic_request_identity, config_identity, request.audit_projection(), audit["provider_execution"],
    )
    _require_parent(reconstructed.to_dict() == value, "baseline Generation occurrence bytes/validation")
    return reconstructed


def load_parent_targeted_gate(
    repo_root: Path, parent_root: Path, frozen_runtime: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Validate all nine immutable parent occurrences without modifying the parent."""

    repo = Path(repo_root).resolve()
    root = Path(parent_root).resolve()
    _require_parent(root == (repo / PARENT_LIVE_ROOT).resolve(), "exact parent root")
    manifest = build_targeted_case_manifest(repo)
    _require_parent(manifest.manifest_identity == PARENT_MANIFEST_IDENTITY, "frozen manifest identity")
    manifest_path = root / "manifest" / "targeted_case_manifest.json"
    manifest_body = canonical_json_bytes(manifest.to_dict())
    _require_parent(manifest_path.is_file() and manifest_path.read_bytes() == manifest_body
                    and sha256(manifest_body).hexdigest() == PARENT_MANIFEST_IDENTITY, "parent manifest bytes/hash")

    def parent_runtime_matches_current(parent_runtime: Any) -> bool:
        """Keep immutable parent assessor identity as history, while rebinding current code."""

        if not isinstance(parent_runtime, Mapping):
            return False
        if parent_runtime.get("block_a") != frozen_runtime.get("block_a"):
            return False
        if parent_runtime.get("generation") != frozen_runtime.get("generation"):
            return False
        historical = parent_runtime.get("assessor")
        if not isinstance(historical, Mapping):
            return False
        required = {
            "provider_type": "BailianEvidenceAssessor",
            "model_id": ASSESSOR_MODEL_ID,
            "model_reference_policy": REQUESTED_ALIAS_POLICY,
            "region": ASSESSOR_REGION,
            "workspace": ASSESSOR_WORKSPACE,
            "endpoint": ASSESSOR_ENDPOINT,
            "operating_point_schema_version": ASSESSOR_OPERATING_POINT_SCHEMA_VERSION,
        }
        if any(historical.get(key) != value for key, value in required.items()):
            return False
        return (
            historical.get("prompt_identity") == HISTORICAL_PARENT_ASSESSOR_PROMPT_IDENTITY
            and historical.get("schema_version") == HISTORICAL_PARENT_ASSESSOR_SCHEMA_VERSION
            and historical.get("execution_config_identity") == HISTORICAL_PARENT_ASSESSOR_EXECUTION_CONFIG_IDENTITY
        )

    loaded: dict[str, dict[str, Any]] = {}
    for case in manifest.cases:
        outer = f"p04-targeted-semantic-gate-live-20260919-{case.question_id.lower()}"
        case_root = root / "cases" / outer
        preflight_path = case_root / "preflight_started.json"
        preflight = _read_bound_json(preflight_path)
        expected_manifest_binding = {
            "schema_version": TARGETED_CASE_MANIFEST_SCHEMA_VERSION,
            "manifest_identity": PARENT_MANIFEST_IDENTITY,
            "path": str(manifest_path),
            "sha256": PARENT_MANIFEST_IDENTITY,
        }
        _require_parent(preflight.get("status") == "STARTED" and preflight.get("run_identity") == outer
                        and preflight.get("case") == {"question_id": case.question_id, "question_identity": case.question_identity}
                        and preflight.get("manifest_binding") == expected_manifest_binding
                        and parent_runtime_matches_current(preflight.get("frozen_runtime")), f"{case.question_id} preflight")
        package_path = case_root / "targeted_gate_review_package.json"
        package = _read_bound_json(package_path)
        _require_parent(package.get("case") == case.to_dict() and package.get("run_identity") == outer
                        and package.get("manifest_binding") == expected_manifest_binding
                        and parent_runtime_matches_current(package.get("frozen_runtime")), f"{case.question_id} case/package binding")
        descriptor = _read_bound_json(case_root / "round0" / case.question_id / "round0_replay.json")
        _require_parent(package.get("round0") == descriptor and descriptor.get("execution_identity") == f"{outer}-adaptive"
                        and descriptor.get("question") == case.question, f"{case.question_id} round0 descriptor")
        round_dir = case_root / "round0" / case.question_id
        source = _parent_artifact(round_dir / "block_a_result.json", descriptor["artifacts"]["block_a_result"], "block_a_result.json")
        packet = _parent_artifact(round_dir / "evidence_packet.json", descriptor["artifacts"]["evidence_packet"], "evidence_packet.json")
        binding = bind_evidence_packet(packet)
        _require_parent(source.get("evidence_packet") == packet
                        and binding.to_dict() == descriptor.get("packet_binding")
                        and binding.packet_sha256 == descriptor.get("packet_sha256"), f"{case.question_id} exact Packet binding")
        projected = _project_round(source, expected_query=case.question, round_index=0,
                                   expected_execution_identity=f"{outer}-adaptive")
        _require_parent(projected.retrieval_request_identity == descriptor.get("retrieval_request_identity")
                        and projected.static_identity["retrieval_unit_build_identity"] == frozen_runtime["block_a"]["retrieval_unit_build_identity"]
                        and projected.static_identity["lexical_build_identity"] == frozen_runtime["block_a"]["lexical_build_identity"]
                        and projected.static_identity["dense_build_identity"] == frozen_runtime["block_a"]["dense_build_identity"]
                        and projected.static_identity["reranker_runtime_identity"] == frozen_runtime["block_a"]["reranker_runtime_identity"]
                        and projected.static_identity["backend_config"] == frozen_runtime["block_a"]["backend_config"],
                        f"{case.question_id} Block A static/retrieval identity")
        request = project_generation_request(packet, question=case.question, question_id=case.question_id)
        baseline = package.get("baseline")
        _require_parent(isinstance(baseline, Mapping) and baseline.get("request") == request.audit_projection()
                        and baseline.get("semantic_request_identity") == request.semantic_request_identity,
                        f"{case.question_id} recomputed baseline request")
        generation_path = case_root / "generation" / "baseline" / "generation_result.json"
        generation_json = _parent_artifact(generation_path, baseline["artifact"], "generation_result.json")
        generation = _generation_from_parent(generation_json, request, frozen_runtime["generation"]["execution_config_identity"])
        _require_parent(baseline.get("generation_result") == generation_json, f"{case.question_id} baseline package occurrence")
        loaded[case.question_id] = {
            "case": case, "source": source, "packet": packet, "baseline": generation,
            "round0_descriptor": descriptor,
            "lineage": {
                "parent_root": str(root), "parent_run_identity": outer,
                "manifest_identity": PARENT_MANIFEST_IDENTITY,
                "preflight": {"path": str(preflight_path), "sha256": _sha256_file(preflight_path)},
                "review_package": {"path": str(package_path), "sha256": _sha256_file(package_path)},
                "round0_result": {"path": str(round_dir / "block_a_result.json"), "sha256": _sha256_file(round_dir / "block_a_result.json")},
                "packet": {"path": str(round_dir / "evidence_packet.json"), "sha256": _sha256_file(round_dir / "evidence_packet.json")},
                "baseline": {"path": str(generation_path), "sha256": _sha256_file(generation_path)},
            },
        }
    _require_parent(len(loaded) == 9, "complete nine-case parent")
    return loaded


def _mechanical_attribution(baseline_request: Any, baseline_result: GenerationResult, adaptive: Mapping[str, Any], replay: SharedRound0Replay) -> dict[str, Any]:
    generation = adaptive.get("generation") if isinstance(adaptive.get("generation"), Mapping) else {}
    adaptive_identity = generation.get("semantic_request_identity")
    adaptive_packet = adaptive.get("final_packet_binding") if isinstance(adaptive.get("final_packet_binding"), Mapping) else {}
    baseline_packet_sha = baseline_request.evidence_packet_sha256
    adaptive_packet_sha = adaptive_packet.get("packet_sha256")
    same_request = adaptive_identity == baseline_request.semantic_request_identity
    admission = adaptive.get("admission") if isinstance(adaptive.get("admission"), Mapping) else {}
    selected = admission.get("selected_supplemental_unit_id")
    visible = admission.get("selected_supplemental_visible") is True
    return {
        "assessment_directive_effect": {
            "eligible": adaptive_packet_sha == baseline_packet_sha and not same_request,
            "baseline_semantic_request_identity": baseline_request.semantic_request_identity,
            "adaptive_semantic_request_identity": adaptive_identity,
        },
        "supplemental_retrieval_effect": {
            "mechanical_chain": {
                "assessment_requested": any(item.get("result", {}).get("action") == "supplement_once" for item in adaptive.get("assessments", []) if isinstance(item, Mapping) and isinstance(item.get("result"), Mapping)),
                "new_occurrence_selected": bool(selected),
                "new_occurrence_packet_visible": visible,
            },
            "semantic_review_required": True,
            "semantic_useful": "UNKNOWN",
            "material_answer_improvement": "UNKNOWN",
        },
        "admission_displacement_effect": {
            "selected_supplemental_unit_id": selected,
            "dropped_round0_tail_unit_id": admission.get("dropped_round0_tail_unit_id"),
            "displacement": "recorded_for_review",
        },
        "final_generation_effect": {
            "eligible": not same_request and adaptive_identity is not None,
            "same_semantic_request": same_request,
            "provider_occurrence_variance_excluded": same_request,
            "semantic_answer_quality": "REVIEW_REQUIRED",
        },
    }


def _directive_only_request(packet: Mapping[str, Any], question: str, question_id: str, adaptive: Mapping[str, Any]) -> Any | None:
    """Rebuild the adaptive directive over shared P0 for attribution only."""

    assessments = adaptive.get("assessments")
    if not isinstance(assessments, list) or not assessments:
        return None
    final = assessments[-1]
    result = final.get("result") if isinstance(final, Mapping) else None
    if not isinstance(result, Mapping):
        return None
    disposition = result.get("answer_disposition")
    if disposition == "bounded_partial":
        try:
            assessment = EvidenceAssessmentResult(
                assessment_request_identity=str(result["assessment_request_identity"]),
                condition=result["condition"],
                action=result["action"],
                answer_disposition=result["answer_disposition"],
                supported_scope=tuple(result["supported_scope"]),
                unresolved_aspects=tuple(result["unresolved_aspects"]),
                missing_information=tuple(result["missing_information"]),
                conflicts=tuple(result["conflicts"]),
                supplemental_query=result.get("supplemental_query"),
            )
            instruction = _partial_instruction(assessment)
            answer_scope = GenerationAnswerScope(
                supported_scope=assessment.supported_scope,
                unresolved_aspects=assessment.unresolved_aspects,
                conflicts=assessment.conflicts,
            )
        except Exception as exc:
            raise SemanticGateContractError("adaptive directive cannot be reconstructed for P0 attribution") from exc
    elif disposition == "full":
        instruction = DEFAULT_GENERATION_INSTRUCTION
        answer_scope = None
    else:
        return None
    return project_generation_request(
        packet,
        question=question,
        question_id=question_id,
        instruction=instruction,
        answer_scope=answer_scope,
    )


def _provider_execution_evidence(result: GenerationResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    provider_audit = dict(result.provider_audit)
    attempts = provider_audit.get("attempts")
    return {
        "execution_status": result.execution_status,
        "semantic_request_identity": result.semantic_request_identity,
        "execution_config_identity": result.execution_config_identity,
        "attempt_count": len(attempts) if isinstance(attempts, list) else "UNKNOWN",
        "provider_network_calls": len(attempts) if isinstance(attempts, list) else "UNKNOWN",
        "generation_occurrence": provider_audit.get("generation_occurrence"),
        "attempts": attempts if isinstance(attempts, list) else [],
        "currency_cost": "UNKNOWN",
    }


def _validate_frozen_block_a_binding(block_a: Any) -> dict[str, Any]:
    """Validate the already-frozen production Block A without invoking it."""

    if not isinstance(block_a, FrozenBlockAEvidenceCapability):
        raise SemanticGateContractError("targeted gate requires FrozenBlockAEvidenceCapability")
    state = block_a.prepared_state
    config = block_a.config
    reranker = block_a.reranker
    embedding_transport = block_a.embedding_transport
    if not isinstance(state, PreparedRagState):
        raise SemanticGateContractError("Block A prepared state lacks the frozen PreparedRagState contract")
    if not isinstance(config, SingleQuestionBackendConfig):
        raise SemanticGateContractError("Block A configuration is not the frozen backend configuration type")
    if not isinstance(embedding_transport, DashScopeQwenEmbeddingTransport):
        raise SemanticGateContractError("Block A embedding capability lacks the frozen DashScope transport identity")
    embedding_config = getattr(embedding_transport, "_config", None)
    if not isinstance(embedding_config, DashScopeQwenEmbeddingConfig):
        raise SemanticGateContractError("Block A embedding transport does not expose a stable configuration")
    if embedding_config.region != ASSESSOR_REGION:
        raise SemanticGateContractError("Block A embedding transport is not the frozen Beijing configuration")
    try:
        embedding_endpoint = _dashscope_embedding_endpoint(
            embedding_config.endpoint,
            workspace=embedding_config.workspace,
            region=embedding_config.region,
        )
    except Exception as exc:
        raise SemanticGateContractError("Block A embedding endpoint is not a valid frozen DashScope binding") from exc
    paths = ProductionRagArtifactPaths()
    expected_paths = {
        "retrieval_unit_manifest_path": paths.retrieval_unit_manifest_path,
        "lexical_manifest_path": paths.lexical_manifest_path,
        "dense_manifest_path": paths.accepted_qwen_dense_manifest_path,
    }
    for name, expected in expected_paths.items():
        actual = getattr(state, name, None)
        if not isinstance(actual, Path) or actual.resolve() != Path(expected).resolve():
            raise SemanticGateContractError(f"Block A {name} is not the frozen production artifact")
    expected_lexical = _field_aware_lexical_arm_identity(ACCEPTED_QWEN_RU_BUILD_IDENTITY)
    if (
        state.retrieval_unit_build_identity != ACCEPTED_QWEN_RU_BUILD_IDENTITY
        or state.lexical_build_identity != expected_lexical
        or state.dense_build_identity != ACCEPTED_QWEN_DENSE_ARM_BUILD_IDENTITY
    ):
        raise SemanticGateContractError("Block A prepared artifact identities are not the accepted production bindings")
    if config.audit_projection() != SingleQuestionBackendConfig().audit_projection():
        raise SemanticGateContractError("Block A configuration is not the frozen production operating point")
    if not config.reranker_enabled or not isinstance(reranker, BgeRerankerV2M3):
        raise SemanticGateContractError("Block A must use the enabled local BGE reranker")
    runtime_identity = reranker.runtime_identity()
    if not isinstance(runtime_identity, Mapping):
        raise SemanticGateContractError("local BGE reranker lacks a stable runtime identity")
    expected_runtime = reranker.config.identity_projection()
    if dict(runtime_identity) != expected_runtime:
        raise SemanticGateContractError("local BGE runtime identity is not bound to its configuration")
    expected_runtime_fields = {
        "model": BGE_MODEL_ID,
        "revision": BGE_MODEL_REVISION,
        "model_weight_sha256": BGE_MODEL_WEIGHT_SHA256,
        "dtype": "float16",
        "device": "cuda",
        "batch_size": 1,
        "max_length": BGE_MAX_LENGTH,
        "projection_max_chars": BGE_PROJECTION_MAX_CHARS,
    }
    if any(runtime_identity.get(key) != value for key, value in expected_runtime_fields.items()):
        raise SemanticGateContractError("local BGE runtime identity is not the accepted pinned identity")
    canonical_json_bytes(runtime_identity)
    return {
        "capability_type": type(block_a).__name__,
        "prepared_state_type": type(state).__name__,
        "retrieval_unit_build_identity": state.retrieval_unit_build_identity,
        "lexical_build_identity": state.lexical_build_identity,
        "dense_build_identity": state.dense_build_identity,
        "backend_config": config.audit_projection(),
        "embedding": {
            "model_id": QWEN_EMBEDDING_MODEL_ID,
            "region": embedding_config.region,
            "workspace": embedding_config.workspace,
            "endpoint": embedding_endpoint,
        },
        "reranker_backend": "local_bge",
        "reranker_runtime_identity": dict(runtime_identity),
        "reranker_runtime_identity_hash": sha256_json(runtime_identity),
        "assembly_config_identity": sha256_json(config.assembly_config.to_dict()),
    }


def _validate_frozen_assessor_binding(assessor: Any) -> dict[str, Any]:
    if not isinstance(assessor, BailianEvidenceAssessor):
        raise SemanticGateContractError("targeted gate requires the frozen BailianEvidenceAssessor")
    identity = assessor.execution_config_identity
    if not _SHA256_RE.fullmatch(identity) or identity == HISTORICAL_QWEN38_EXECUTION_CONFIG_IDENTITY:
        raise SemanticGateContractError("assessor execution identity is not a current frozen identity")
    if assessor.prompt_identity != sha256_json({"prompt_id": ASSESSOR_PROMPT_ID, "version": ASSESSOR_PROMPT_VERSION, "text": ASSESSOR_SYSTEM_PROMPT}):
        raise SemanticGateContractError("assessor prompt identity is not current")
    return {
        "provider_type": type(assessor).__name__,
        "model_id": assessor.config.model_id,
        "model_reference_policy": assessor.config.model_reference_policy,
        "region": assessor.config.region,
        "workspace": assessor.config.workspace,
        "endpoint": assessor.config.endpoint,
        "prompt_identity": assessor.prompt_identity,
        "schema_version": ASSESSOR_PROMPT_SCHEMA_VERSION,
        "operating_point_schema_version": ASSESSOR_OPERATING_POINT_SCHEMA_VERSION,
        "execution_config_identity": identity,
    }


def _validate_frozen_generation_binding(generation_provider: Any) -> dict[str, Any]:
    if not isinstance(generation_provider, BailianGenerationProvider):
        raise SemanticGateContractError("targeted gate requires the frozen BailianGenerationProvider")
    config = getattr(generation_provider, "_config", None)
    if not isinstance(config, BailianControlConfig):
        raise SemanticGateContractError("Generation provider does not expose a stable configuration")
    if (
        config.region != ASSESSOR_REGION
        or config.model_id != BASELINE_QWEN_MODEL_ID
        or config.model_reference_policy != EXACT_SNAPSHOT_POLICY
        or config.enable_thinking is not False
        or config.thinking_budget is not None
        or config.temperature != 0.0
        or config.max_output_tokens != 1024
        or config.max_attempts != 1
    ):
        raise SemanticGateContractError("Generation provider is not the frozen production configuration")
    try:
        if workspace_from_bailian_base_url(config.endpoint) != config.workspace:
            raise SemanticGateContractError("Generation endpoint/workspace binding mismatch")
    except SemanticGateContractError:
        raise
    except Exception as exc:
        raise SemanticGateContractError("Generation endpoint is not a valid Beijing Bailian origin") from exc
    return {
        "provider_type": type(generation_provider).__name__,
        "config": config.audit_projection(),
        "execution_config_identity": config.execution_config_identity,
    }


def _validate_frozen_runtime_bindings(block_a: Any, assessor: Any, generation_provider: Any) -> dict[str, Any]:
    """Return secret-free static identities required before any live call."""

    return {
        "block_a": _validate_frozen_block_a_binding(block_a),
        "assessor": _validate_frozen_assessor_binding(assessor),
        "generation": _validate_frozen_generation_binding(generation_provider),
    }


def run_targeted_gate_case(
    case: TargetedCaseBinding,
    *,
    repo_root: Path,
    manifest_path: Path,
    block_a: Any,
    assessor: Any,
    generation_provider: GenerationProvider,
    execution_identity: str,
    output_root: Path,
    parent_root: Path | None = None,
) -> dict[str, Any]:
    """Execute one future case and persist a review package.

    This function is the only live seam.  It is never called during manifest
    validation or provider-free tests.  It does not assign semantic PASS/FAIL.
    """

    if not execution_identity or not re.fullmatch(r"[A-Za-z0-9._-]+", execution_identity):
        raise SemanticGateContractError("execution identity is invalid")
    if not isinstance(case, TargetedCaseBinding):
        raise SemanticGateContractError("selected case binding is invalid")
    validated_manifest = build_targeted_case_manifest(Path(repo_root))
    manifest_file = Path(manifest_path)
    if not manifest_file.is_file():
        raise SemanticGateContractError("validated targeted case manifest is not persisted")
    manifest_bytes = manifest_file.read_bytes()
    expected_manifest_bytes = canonical_json_bytes(validated_manifest.to_dict())
    if manifest_bytes != expected_manifest_bytes:
        raise SemanticGateContractError("persisted targeted case manifest does not match validated bindings")
    manifest_identity = validated_manifest.manifest_identity
    selected_case = next((item for item in validated_manifest.cases if item.question_id == case.question_id), None)
    if selected_case is None or selected_case.to_dict() != case.to_dict():
        raise SemanticGateContractError("selected case is not bound to the persisted validated manifest")
    frozen_runtime = _validate_frozen_runtime_bindings(block_a, assessor, generation_provider)
    parent = None
    if parent_root is not None:
        parent = load_parent_targeted_gate(Path(repo_root), Path(parent_root), frozen_runtime)[case.question_id]
    run_root = Path(output_root) / execution_identity
    output_base = Path(output_root)
    if output_base.exists() and not output_base.is_dir():
        raise NotADirectoryError(f"targeted gate output root is not a directory: {output_base}")
    output_base.mkdir(parents=True, exist_ok=True)
    if run_root.exists():
        raise FileExistsError(f"refusing to overwrite targeted gate run: {run_root}")
    run_root.mkdir(parents=False, exist_ok=False)
    preflight = {
        "schema_version": "phase04-targeted-gate-preflight-0.1",
        "status": "STARTED",
        "run_identity": execution_identity,
        "case": {
            "question_id": case.question_id,
            "question_identity": case.question_identity,
        },
        "manifest_binding": {
            "schema_version": TARGETED_CASE_MANIFEST_SCHEMA_VERSION,
            "manifest_identity": manifest_identity,
            "path": str(manifest_file),
            "sha256": sha256(manifest_bytes).hexdigest(),
        },
        "frozen_runtime": frozen_runtime,
    }
    if parent is not None:
        preflight["parent_lineage"] = parent["lineage"]
    _write_no_overwrite(run_root / "preflight_started.json", canonical_json_bytes(preflight))
    if isinstance(assessor, BailianEvidenceAssessor):
        assessor.bind_response_artifact_root(run_root / "assessor")
    assessor_calls_before = getattr(assessor, "provider_network_calls", None)
    assessor_audits_before = len(getattr(assessor, "audit_history", ()))
    if parent is None:
        replay = SharedRound0Replay.capture(block_a, question=case.question, execution_identity=f"{execution_identity}-adaptive")
        round0_descriptor = persist_round0_replay(replay, run_root, case.question_id)
        persisted_packet = json.loads((run_root / "round0" / case.question_id / "evidence_packet.json").read_text(encoding="utf-8"))
    else:
        replay = SharedRound0Replay.from_result(
            parent["source"], question=case.question, adaptive_execution_identity=f"{execution_identity}-adaptive",
            legacy_block_a_execution_identity=parent["round0_descriptor"]["execution_identity"],
        )
        round0_descriptor = {
            "schema_version": "phase04-shared-round0-recovery-import-0.1",
            **replay.to_dict(),
            "parent_lineage": parent["lineage"],
            "artifacts": {
                "block_a_result": parent["lineage"]["round0_result"],
                "evidence_packet": parent["lineage"]["packet"],
            },
        }
        _write_no_overwrite(run_root / "round0_import.json", canonical_json_bytes(round0_descriptor))
        persisted_packet = parent["packet"]
    persisted_binding = bind_evidence_packet(persisted_packet)
    if persisted_binding.packet_sha256 != replay.packet_sha256:
        raise SemanticGateContractError("persisted round-0 Packet identity drifted before replay")
    replay = replace(replay, backend_result={**replay.backend_result, "evidence_packet": persisted_packet})
    baseline_request = project_generation_request(dict(persisted_packet), question=case.question, question_id=case.question_id)
    if parent is None:
        baseline_started = time.perf_counter()
        baseline_result = generation_provider.generate(baseline_request)
        baseline_wall_clock_ms: float | str = round((time.perf_counter() - baseline_started) * 1000, 3)
        baseline_artifact = write_generation_result(run_root / "generation" / "baseline", baseline_result)
    else:
        baseline_result = parent["baseline"]
        baseline_wall_clock_ms = "parent_occurrence_reused"
        baseline_artifact = {
            **parent["lineage"]["baseline"],
            "reused_parent_occurrence": True,
        }
    reused: list[str] = []
    adaptive_provider = ReusingGenerationProvider(generation_provider, {baseline_request.semantic_request_identity: baseline_result}, reused)
    adaptive = run_bounded_adaptive_question(
        case.question,
        block_a=replay.capability(block_a),
        assessor=assessor,
        generation_provider=adaptive_provider,
        execution_identity=f"{execution_identity}-adaptive",
    )
    directive_only_request = None
    directive_only_result = None
    directive_only_artifact = None
    adaptive_packet_sha = adaptive.get("final_packet_binding", {}).get("packet_sha256") if isinstance(adaptive.get("final_packet_binding"), Mapping) else None
    adaptive_generation = adaptive.get("generation") if isinstance(adaptive.get("generation"), Mapping) else {}
    if adaptive_packet_sha != replay.packet_sha256 and adaptive_generation.get("status") == "succeeded":
        directive_only_request = _directive_only_request(persisted_packet, case.question, case.question_id, adaptive)
        if directive_only_request is not None and directive_only_request.semantic_request_identity != baseline_request.semantic_request_identity:
            directive_only_result = adaptive_provider.generate(directive_only_request)
            directive_only_artifact = write_generation_result(run_root / "generation" / "directive_only_round0", directive_only_result)
    adaptive_identity = adaptive.get("generation", {}).get("semantic_request_identity") if isinstance(adaptive.get("generation"), Mapping) else None
    same_request = adaptive_identity == baseline_request.semantic_request_identity
    assessor_config = getattr(assessor, "config", None)
    assessor_audits = list(getattr(assessor, "audit_history", []))[assessor_audits_before:]
    assessor_calls_after = getattr(assessor, "provider_network_calls", None)
    if isinstance(assessor_calls_before, int) and isinstance(assessor_calls_after, int):
        assessor_case_calls: int | str = assessor_calls_after - assessor_calls_before
    else:
        assessor_case_calls = "UNKNOWN"
    assessor_binding = {
        "model_id": getattr(assessor_config, "model_id", "UNKNOWN"),
        "model_reference_policy": getattr(assessor_config, "model_reference_policy", "UNKNOWN"),
        "prompt_id": ASSESSOR_PROMPT_ID,
        "prompt_version": ASSESSOR_PROMPT_VERSION,
        "prompt_schema_version": ASSESSOR_PROMPT_SCHEMA_VERSION,
        "operating_point_schema_version": ASSESSOR_OPERATING_POINT_SCHEMA_VERSION,
        "prompt_identity": getattr(assessor, "prompt_identity", "UNKNOWN"),
        "execution_config_identity": getattr(assessor, "execution_config_identity", "UNKNOWN"),
        "max_attempts": getattr(assessor_config, "max_attempts", "UNKNOWN"),
        "retry_policy": "none",
    }
    package = {
        "schema_version": TARGETED_GATE_SCHEMA_VERSION,
        "case": case.to_dict(),
        "run_identity": execution_identity,
        "manifest_binding": {
            "schema_version": TARGETED_CASE_MANIFEST_SCHEMA_VERSION,
            "manifest_identity": manifest_identity,
            "path": str(manifest_file),
            "sha256": sha256(manifest_bytes).hexdigest(),
        },
        "assessor_binding": assessor_binding,
        "frozen_runtime": frozen_runtime,
        "parent_lineage": parent["lineage"] if parent is not None else None,
        "round0": round0_descriptor,
        "baseline": {
            "request": baseline_request.audit_projection(),
            "semantic_request_identity": baseline_request.semantic_request_identity,
            "generation_result": baseline_result.to_dict(),
            "artifact": baseline_artifact,
        },
        "adaptive": adaptive,
        "directive_only_round0": {
            "request": directive_only_request.audit_projection() if directive_only_request is not None else None,
            "semantic_request_identity": directive_only_request.semantic_request_identity if directive_only_request is not None else None,
            "generation_result": directive_only_result.to_dict() if directive_only_result is not None else None,
            "artifact": directive_only_artifact,
            "reused_baseline_occurrence": directive_only_request is not None and directive_only_request.semantic_request_identity == baseline_request.semantic_request_identity,
        },
        "generation_variance": {
            "baseline_semantic_request_identity": baseline_request.semantic_request_identity,
            "adaptive_semantic_request_identity": adaptive_identity,
            "same_semantic_request": same_request,
            "reused_baseline_occurrence": bool(reused),
            "provider_occurrence_variance_excluded": same_request,
            "directive_only_round0_semantic_request_identity": directive_only_request.semantic_request_identity if directive_only_request is not None else None,
        },
        "attribution": _mechanical_attribution(baseline_request, baseline_result, adaptive, replay),
        "semantic_review": {
            "status": "REQUIRED",
            "assessment_condition_action_correctness": "REVIEW_REQUIRED",
            "supplemental_query_appropriateness": "REVIEW_REQUIRED",
            "verified_useful_new_official_occurrence": "UNKNOWN",
            "final_packet_visibility": "REVIEW_REQUIRED",
            "displacement_effect": "REVIEW_REQUIRED",
            "final_answer_correctness": "REVIEW_REQUIRED",
            "constraint_handling": {
                "time": "REVIEW_REQUIRED",
                "relation": "REVIEW_REQUIRED",
                "identity": "REVIEW_REQUIRED",
                "category": "REVIEW_REQUIRED",
                "count": "REVIEW_REQUIRED",
            },
            "bounded_partial_uncertainty_handling": "REVIEW_REQUIRED",
            "semantic_citation_support": "REVIEW_REQUIRED",
            "semantic_useful_evidence": "UNKNOWN",
            "material_answer_improvement": "UNKNOWN",
            "unsupported_answer_regression": "UNKNOWN",
        },
        "provider_calls": {
            "round0_shared": "parent_occurrence_reused" if parent is not None else "persisted_after_execution",
            "assessor": assessor_case_calls,
            "assessor_audit": dict(assessor_audits[-1]) if assessor_audits else {},
            "assessor_attempts": assessor_audits,
            "assessor_response_artifacts": [
                audit["response_evidence"]["raw_response_artifact"]
                for audit in assessor_audits
                if isinstance(audit.get("response_evidence"), Mapping)
                and isinstance(audit["response_evidence"].get("raw_response_artifact"), Mapping)
            ],
            "generation_reused_identities": list(reused),
            "generation_occurrences": {
                "baseline": {
                    **(_provider_execution_evidence(baseline_result) or {}),
                    "wall_clock_ms": baseline_wall_clock_ms,
                    "reused_parent_occurrence": parent is not None,
                    "current_run_provider_network_calls": 0 if parent is not None else _provider_execution_evidence(baseline_result)["provider_network_calls"],
                },
                "adaptive": list(adaptive_provider.call_audit),
                "directive_only_round0": _provider_execution_evidence(directive_only_result),
            },
            "latency_ms": "UNKNOWN",
            "estimated_cost": "UNKNOWN",
        },
    }
    _write_no_overwrite(run_root / "targeted_gate_review_package.json", canonical_json_bytes(package))
    return package


__all__ = [
    "ArtifactBinding",
    "ASSESSOR_ENDPOINT",
    "ASSESSOR_OPERATING_POINT_SCHEMA_VERSION",
    "ASSESSOR_REGION",
    "ASSESSOR_WORKSPACE",
    "BailianEvidenceAssessor",
    "EvidenceAssessorProviderError",
    "HISTORICAL_QWEN38_EXECUTION_CONFIG_IDENTITY",
    "PacketArtifactBinding",
    "PacketSourceIdentityBinding",
    "PARENT_LIVE_ROOT",
    "PARENT_MANIFEST_IDENTITY",
    "ReusingGenerationProvider",
    "SemanticGateContractError",
    "SharedRound0Replay",
    "TargetedCaseBinding",
    "TargetedCaseManifest",
    "build_targeted_case_manifest",
    "load_parent_targeted_gate",
    "parse_assessment_response",
    "persist_targeted_case_manifest",
    "persist_round0_replay",
    "run_targeted_gate_case",
]
