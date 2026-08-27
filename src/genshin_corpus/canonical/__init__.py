"""Phase 03 Batch 1 Canonical contract foundation."""

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
]
