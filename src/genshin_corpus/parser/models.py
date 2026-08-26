from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import (
    CONTENT_ROLE_UNKNOWN,
    ContractMetadata,
    Diagnostic,
    ParsedIdentity,
    RawRef,
    SourcePosition,
)
from .fingerprints import parsed_fingerprint, source_fingerprint


def _json_value(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class ParsedContentUnit:
    identity: ParsedIdentity
    metadata: ContractMetadata
    value: Any

    def to_dict(self) -> dict[str, Any]:
        return {"identity": self.identity.to_dict(), "metadata": self.metadata.to_dict(), "value": _json_value(self.value)}


@dataclass(frozen=True)
class ParsedUnknown:
    identity: ParsedIdentity
    metadata: ContractMetadata
    reason: str
    raw_value: Any
    context: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(
        cls,
        *,
        identity: ParsedIdentity,
        raw_value: Any,
        raw_refs: tuple[RawRef, ...],
        source_position: SourcePosition,
        reason: str,
        context: Mapping[str, Any] | None = None,
        diagnostics: tuple[Diagnostic, ...] = (),
    ) -> "ParsedUnknown":
        if not reason:
            raise ValueError("reason is required for unknown content")
        source_hash = source_fingerprint(raw_value)
        metadata = ContractMetadata(
            parse_status="preserved_unsupported",
            raw_refs=raw_refs,
            source_position=source_position,
            source_fingerprint=source_hash,
            parsed_fingerprint=parsed_fingerprint({"raw_value": raw_value}),
            diagnostics=diagnostics,
        )
        return cls(identity, metadata, reason, raw_value, dict(context or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "metadata": self.metadata.to_dict(),
            "reason": self.reason,
            "raw_value": _json_value(self.raw_value),
            "context": _json_value(self.context),
        }


@dataclass(frozen=True)
class ParsedComponent:
    identity: ParsedIdentity
    metadata: ContractMetadata
    source_component_id: str | None
    source_data_encoding: str
    source_layout: Any = None
    source_style: Any = None
    units: tuple[ParsedContentUnit, ...] = ()
    unsupported: tuple[ParsedUnknown, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "units", tuple(self.units))
        object.__setattr__(self, "unsupported", tuple(self.unsupported))

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "metadata": self.metadata.to_dict(),
            "source_component_id": self.source_component_id,
            "source_data_encoding": self.source_data_encoding,
            "source_layout": _json_value(self.source_layout),
            "source_style": _json_value(self.source_style),
            "units": [unit.to_dict() for unit in self.units],
            "unsupported": [item.to_dict() for item in self.unsupported],
        }


@dataclass(frozen=True)
class ParsedModule:
    identity: ParsedIdentity
    metadata: ContractMetadata
    source_module_id: str | None
    module_index: int
    name: str | None
    repeated: bool | None
    is_submodule: bool | None
    origin_module_id: str | None
    components: tuple[ParsedComponent, ...] = ()
    layout_observations: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.module_index < 0:
            raise ValueError("module_index must be non-negative")
        object.__setattr__(self, "components", tuple(self.components))
        object.__setattr__(self, "layout_observations", tuple(self.layout_observations))

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "metadata": self.metadata.to_dict(),
            "source_module_id": self.source_module_id,
            "module_index": self.module_index,
            "name": self.name,
            "repeated": self.repeated,
            "is_submodule": self.is_submodule,
            "origin_module_id": self.origin_module_id,
            "components": [component.to_dict() for component in self.components],
            "layout_observations": [_json_value(item) for item in self.layout_observations],
        }


@dataclass(frozen=True)
class ParsedDetail:
    identity: ParsedIdentity
    metadata: ContractMetadata
    source: str
    locale: str
    run_id: str
    content_id: str
    page_id: str | None
    name: str | None
    page_type: str | None
    source_metadata: Mapping[str, Any] = field(default_factory=dict)
    source_template_layout: Any = None
    modules: tuple[ParsedModule, ...] = ()
    channel_memberships: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "modules", tuple(self.modules))
        object.__setattr__(self, "channel_memberships", tuple(self.channel_memberships))

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "metadata": self.metadata.to_dict(),
            "source": self.source,
            "locale": self.locale,
            "run_id": self.run_id,
            "content_id": self.content_id,
            "page_id": self.page_id,
            "name": self.name,
            "page_type": self.page_type,
            "source_metadata": _json_value(self.source_metadata),
            "source_template_layout": _json_value(self.source_template_layout),
            "modules": [module.to_dict() for module in self.modules],
            "channel_memberships": list(self.channel_memberships),
        }
