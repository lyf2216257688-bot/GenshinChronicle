"""Phase 02 Parsed-layer contract foundation.

This package currently exposes evidence-preserving models and deterministic
identity/fingerprint helpers. Source-specific component handlers are added in
later Phase 02 batches.
"""

from .contracts import (
    CONTENT_ROLE_UNKNOWN,
    PARSED_SCHEMA_VERSION,
    PARSER_VERSION,
    PROVENANCE_UNKNOWN,
    Classification,
    Diagnostic,
    ParseStatus,
    ParsedIdentity,
    RawRef,
    SourcePosition,
)
from .fingerprints import parsed_fingerprint, source_fingerprint
from .identity import (
    component_identity,
    content_unit_identity,
    detail_identity,
    module_identity,
)
from .models import (
    ParsedComponent,
    ParsedContentUnit,
    ParsedDetail,
    ParsedModule,
    ParsedUnknown,
)
from .storage import ParsedRunStore, blank_manifest

__all__ = [
    "CONTENT_ROLE_UNKNOWN",
    "PARSED_SCHEMA_VERSION",
    "PARSER_VERSION",
    "PROVENANCE_UNKNOWN",
    "Classification",
    "Diagnostic",
    "ParseStatus",
    "ParsedIdentity",
    "RawRef",
    "SourcePosition",
    "parsed_fingerprint",
    "source_fingerprint",
    "component_identity",
    "content_unit_identity",
    "detail_identity",
    "module_identity",
    "ParsedComponent",
    "ParsedContentUnit",
    "ParsedDetail",
    "ParsedModule",
    "ParsedUnknown",
    "ParsedRunStore",
    "blank_manifest",
]
