"""Phase 03 Canonical contracts, projection, and local run processing."""

from .contracts import (
    CANONICAL_SCHEMA_VERSION,
    CANONICAL_TRANSFORM_VERSION,
    STRUCTURAL_NORMALIZATION_VERSION,
    CanonicalObservation,
    CanonicalRecord,
    CanonicalRecordStatus,
    CanonicalSection,
    CanonicalUnit,
    CanonicalVersions,
    ComponentContext,
    LineageEvidenceScope,
    LineageLink,
    parsed_identity_from_input,
    source_identity_from_parsed_input,
)
from .fingerprints import (
    canonical_content_fingerprint,
    canonical_dependency_fingerprint,
    canonical_json_bytes,
    canonical_record_id,
)
from .serialization import serialize_canonical_record
from .projector import PROJECTOR_POLICY_VERSION, project_parsed_detail, project_parsed_input
from .pipeline import CANONICAL_PIPELINE_VERSION, CanonicalRunPipeline
from .storage import CanonicalRunStore

__all__ = [
    "CANONICAL_SCHEMA_VERSION",
    "CANONICAL_TRANSFORM_VERSION",
    "STRUCTURAL_NORMALIZATION_VERSION",
    "CanonicalObservation",
    "CanonicalRecord",
    "CanonicalRecordStatus",
    "CanonicalSection",
    "CanonicalUnit",
    "CanonicalVersions",
    "ComponentContext",
    "LineageEvidenceScope",
    "LineageLink",
    "parsed_identity_from_input",
    "source_identity_from_parsed_input",
    "canonical_content_fingerprint",
    "canonical_dependency_fingerprint",
    "canonical_json_bytes",
    "canonical_record_id",
    "serialize_canonical_record",
    "PROJECTOR_POLICY_VERSION",
    "project_parsed_detail",
    "project_parsed_input",
    "CANONICAL_PIPELINE_VERSION",
    "CanonicalRunPipeline",
    "CanonicalRunStore",
]
