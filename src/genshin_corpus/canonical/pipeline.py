from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from genshin_corpus.parser.contracts import Classification, ContractMetadata, Diagnostic, ParsedIdentity, RawRef, SourcePosition
from genshin_corpus.parser.dialogue import DialogueEdge, DialogueGraph, DialogueGroup, DialogueNode
from genshin_corpus.parser.models import ParsedComponent, ParsedContentUnit, ParsedDetail, ParsedModule, ParsedUnknown

from .contracts import CanonicalObservation, CanonicalVersions
from .fingerprints import canonical_dependency_fingerprint
from .projector import PROJECTOR_POLICY_VERSION, project_parsed_detail, project_parsed_input
from .serialization import serialize_canonical_record
from .storage import CanonicalRunStore, blank_manifest


CANONICAL_PIPELINE_VERSION = "canonical-run-0.1"
_RECORD_STATUSES = ("canonical", "canonical_with_anomalies", "blocked_integrity")


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"serialized Parsed {name} must be an object")
    return value


def _identity(value: Any) -> ParsedIdentity:
    value = _mapping(value, "identity")
    components = _mapping(value.get("components", {}), "identity.components")
    return ParsedIdentity(
        kind=value.get("kind"),
        key=value.get("key"),
        stability=value.get("stability"),
        components=tuple(components.items()),
    )


def _raw_ref(value: Any) -> RawRef:
    return RawRef(**dict(_mapping(value, "RawRef")))


def _position(value: Any) -> SourcePosition:
    value = _mapping(value, "source_position")
    return SourcePosition(
        json_pointer=value.get("json_pointer", ""),
        array_index=value.get("array_index"),
        layout_path=tuple(value.get("layout_path", ())),
        ordering=value.get("ordering"),
    )


def _classification(value: Any) -> Classification:
    return Classification(**dict(_mapping(value, "classification")))


def _diagnostic(value: Any) -> Diagnostic:
    return Diagnostic(**dict(_mapping(value, "diagnostic")))


def _metadata(value: Any) -> ContractMetadata:
    value = _mapping(value, "metadata")
    refs = value.get("raw_refs", ())
    diagnostics = value.get("diagnostics", ())
    if not isinstance(refs, list) or not isinstance(diagnostics, list):
        raise ValueError("serialized Parsed metadata raw_refs and diagnostics must be lists")
    return ContractMetadata(
        schema_version=value.get("schema_version"),
        parser_version=value.get("parser_version"),
        parse_status=value.get("parse_status"),
        raw_refs=tuple(_raw_ref(item) for item in refs),
        source_position=_position(value.get("source_position", {})),
        source_fingerprint=value.get("source_fingerprint", ""),
        parsed_fingerprint=value.get("parsed_fingerprint", ""),
        provenance=_classification(value.get("provenance", {})),
        content_role=_classification(value.get("content_role", {})),
        diagnostics=tuple(_diagnostic(item) for item in diagnostics),
    )


def _optional_raw_ref(value: Any) -> RawRef | None:
    return None if value is None else _raw_ref(value)


def _dialogue_graph(value: Mapping[str, Any]) -> DialogueGraph:
    groups = value.get("groups", ())
    diagnostics = value.get("diagnostics", ())
    if not isinstance(groups, list) or not isinstance(diagnostics, list):
        raise ValueError("serialized dialogue graph groups and diagnostics must be lists")
    parsed_groups: list[DialogueGroup] = []
    for item in groups:
        group = _mapping(item, "dialogue group")
        nodes = group.get("nodes", ())
        edges = group.get("edges", ())
        group_diagnostics = group.get("diagnostics", ())
        if not isinstance(nodes, list) or not isinstance(edges, list) or not isinstance(group_diagnostics, list):
            raise ValueError("serialized dialogue group collections must be lists")
        parsed_nodes = tuple(
            DialogueNode(
                source_id=node.get("source_id"),
                ordering=node.get("ordering"),
                option=node.get("option"),
                dialogue=node.get("dialogue"),
                icon=node.get("icon"),
                speaker=node.get("speaker"),
                raw_fields=_mapping(node.get("raw_fields", {}), "dialogue node raw_fields"),
                raw_ref=_optional_raw_ref(node.get("raw_ref")),
                source_position=_position(node.get("source_position", {})),
                option_rich_text=node.get("option_rich_text"),
                dialogue_rich_text=node.get("dialogue_rich_text"),
            )
            for raw_node in nodes
            for node in (_mapping(raw_node, "dialogue node"),)
        )
        parsed_edges = tuple(
            DialogueEdge(
                parent_id=edge.get("parent_id"),
                child_id=edge.get("child_id"),
                ordering=edge.get("ordering"),
                raw_ref=_raw_ref(edge.get("raw_ref")),
                source_position=_position(edge.get("source_position", {})),
            )
            for raw_edge in edges
            for edge in (_mapping(raw_edge, "dialogue edge"),)
        )
        parsed_groups.append(DialogueGroup(
            ordering=group.get("ordering"),
            source_path=group.get("source_path"),
            root_id=group.get("root_id"),
            nodes=parsed_nodes,
            edges=parsed_edges,
            raw_fields=_mapping(group.get("raw_fields", {}), "dialogue group raw_fields"),
            raw_ref=_optional_raw_ref(group.get("raw_ref")),
            diagnostics=tuple(_diagnostic(item) for item in group_diagnostics),
        ))
    return DialogueGraph(
        groups=tuple(parsed_groups),
        raw_fields=_mapping(value.get("raw_fields", {}), "dialogue graph raw_fields"),
        diagnostics=tuple(_diagnostic(item) for item in diagnostics),
    )


def _unit(value: Any, *, source_component_id: str | None) -> ParsedContentUnit:
    value = _mapping(value, "content unit")
    unit_value = value.get("value")
    if source_component_id == "interactive_dialogue" and isinstance(unit_value, Mapping) and unit_value.get("kind") == "dialogue_graph":
        unit_value = _dialogue_graph(unit_value)
    return ParsedContentUnit(identity=_identity(value.get("identity")), metadata=_metadata(value.get("metadata")), value=unit_value)


def _unknown(value: Any) -> ParsedUnknown:
    value = _mapping(value, "unknown value")
    return ParsedUnknown(
        identity=_identity(value.get("identity")),
        metadata=_metadata(value.get("metadata")),
        reason=value.get("reason"),
        raw_value=value.get("raw_value"),
        context=_mapping(value.get("context", {}), "unknown context"),
    )


def _component(value: Any) -> ParsedComponent:
    value = _mapping(value, "component")
    component_id = value.get("source_component_id")
    units = value.get("units", ())
    unsupported = value.get("unsupported", ())
    if not isinstance(units, list) or not isinstance(unsupported, list):
        raise ValueError("serialized Parsed component units and unsupported must be lists")
    return ParsedComponent(
        identity=_identity(value.get("identity")),
        metadata=_metadata(value.get("metadata")),
        source_component_id=component_id,
        source_data_encoding=value.get("source_data_encoding"),
        source_layout=value.get("source_layout"),
        source_style=value.get("source_style"),
        units=tuple(_unit(item, source_component_id=component_id) for item in units),
        unsupported=tuple(_unknown(item) for item in unsupported),
    )


def _module(value: Any) -> ParsedModule:
    value = _mapping(value, "module")
    components = value.get("components", ())
    unsupported = value.get("unsupported", ())
    layouts = value.get("layout_observations", ())
    if not isinstance(components, list) or not isinstance(unsupported, list) or not isinstance(layouts, list):
        raise ValueError("serialized Parsed module collections must be lists")
    return ParsedModule(
        identity=_identity(value.get("identity")),
        metadata=_metadata(value.get("metadata")),
        source_module_id=value.get("source_module_id"),
        module_index=value.get("module_index"),
        name=value.get("name"),
        repeated=value.get("repeated"),
        is_submodule=value.get("is_submodule"),
        origin_module_id=value.get("origin_module_id"),
        components=tuple(_component(item) for item in components),
        unsupported=tuple(_unknown(item) for item in unsupported),
        layout_observations=tuple(_mapping(item, "module layout_observation") for item in layouts),
    )


def decode_parsed_detail(value: Mapping[str, Any]) -> ParsedDetail:
    """Strictly rebuild a normal Phase 02 serialized detail for the existing projector."""

    modules = value.get("modules")
    unsupported_modules = value.get("unsupported_modules")
    channels = value.get("channel_memberships")
    if not isinstance(modules, list) or not isinstance(unsupported_modules, list) or not isinstance(channels, list):
        raise ValueError("serialized ParsedDetail modules, unsupported_modules, and channel_memberships must be lists")
    return ParsedDetail(
        identity=_identity(value.get("identity")),
        metadata=_metadata(value.get("metadata")),
        source=value.get("source"),
        locale=value.get("locale"),
        run_id=value.get("run_id"),
        content_id=value.get("content_id"),
        page_id=value.get("page_id"),
        name=value.get("name"),
        page_type=value.get("page_type"),
        source_metadata=_mapping(value.get("source_metadata", {}), "detail source_metadata"),
        source_template_layout=value.get("source_template_layout"),
        modules=tuple(_module(item) for item in modules),
        unsupported_modules=tuple(_unknown(item) for item in unsupported_modules),
        channel_memberships=tuple(str(item) for item in channels),
    )


def _diagnostic_codes(value: Any) -> tuple[str, ...]:
    codes: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for diagnostic in item.get("diagnostics", ()) if isinstance(item.get("diagnostics"), list) else ():
                if isinstance(diagnostic, Mapping) and isinstance(diagnostic.get("code"), str):
                    codes.add(diagnostic["code"])
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return tuple(sorted(codes))


@dataclass(frozen=True)
class _ParsedInput:
    index: int
    manifest_record: Mapping[str, Any]

    @property
    def locator(self) -> str:
        return str(self.manifest_record.get("record_identity_key", self.index))


class CanonicalRunPipeline:
    """Project one complete Parsed run into immutable, auditable Canonical records."""

    def __init__(
        self,
        *,
        parsed_manifest_path: Path,
        output_root: Path = Path("data/canonical"),
        canonical_run_id: str,
        reuse_manifest_path: Path | None = None,
        versions: CanonicalVersions | None = None,
    ) -> None:
        self.parsed_manifest_path = parsed_manifest_path
        self.output_root = output_root
        self.canonical_run_id = canonical_run_id
        self.reuse_manifest_path = reuse_manifest_path
        self.versions = versions or CanonicalVersions(transform_version=PROJECTOR_POLICY_VERSION)

    def run(self) -> dict[str, Any]:
        manifest_body = self.parsed_manifest_path.read_bytes()
        parsed_manifest = json.loads(manifest_body.decode("utf-8"))
        source = parsed_manifest.get("source")
        locale = parsed_manifest.get("locale")
        parsed_run_id = parsed_manifest.get("parsed_run_id")
        if source != "mihoyo_obc" or not isinstance(locale, str) or not isinstance(parsed_run_id, str):
            raise ValueError("Parsed manifest is not a recognized mihoyo_obc run")
        if parsed_manifest.get("status") != "complete":
            raise ValueError("Canonical runner requires a complete Parsed manifest")
        records = parsed_manifest.get("records")
        if not isinstance(records, list):
            raise ValueError("Parsed manifest records must be a list")
        if (
            parsed_manifest.get("input_detail_count") != len(records)
            or parsed_manifest.get("accounted_detail_count") != len(records)
        ):
            raise ValueError("complete Parsed manifest accounting does not match its records")
        parsed_counts = parsed_manifest.get("counts")
        if not isinstance(parsed_counts, Mapping) or sum(
            value for value in parsed_counts.values() if isinstance(value, int)
        ) != len(records):
            raise ValueError("complete Parsed manifest status counts do not match its records")

        parsed_manifest_sha256 = _sha256(manifest_body)
        dependencies = self._dependencies(parsed_manifest)
        store = CanonicalRunStore(self.output_root, source, locale, self.canonical_run_id)
        inputs = tuple(_ParsedInput(index, _mapping(record, "manifest record")) for index, record in enumerate(records))
        prior_manifest = store.read_manifest()
        existing = self._completed_manifest_if_current(
            store,
            inputs,
            parsed_manifest,
            parsed_manifest_sha256,
            dependencies,
        )
        if existing is not None:
            return existing
        if isinstance(prior_manifest, Mapping) and prior_manifest.get("status") == "complete":
            raise FileExistsError(
                "Canonical completed run is not reusable; use a new canonical_run_id for changed or corrupt dependencies"
            )

        manifest = blank_manifest(
            source=source,
            locale=locale,
            canonical_run_id=self.canonical_run_id,
            parsed_run_id=parsed_run_id,
            parsed_manifest_path=str(self.parsed_manifest_path),
            parsed_manifest_sha256=parsed_manifest_sha256,
            dependencies=dependencies,
        )
        manifest["input_record_count"] = len(inputs)
        reusable = self._load_reusable_records(source, locale, parsed_run_id, parsed_manifest_sha256, dependencies)
        status_counts: Counter[str] = Counter()
        all_diagnostics: Counter[str] = Counter()
        output_records: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        reused = 0
        reprojected = 0

        for item in inputs:
            result, did_reuse, failure = self._process_item(
                item=item,
                parsed_manifest_sha256=parsed_manifest_sha256,
                parsed_manifest=parsed_manifest,
                store=store,
                reusable=reusable,
            )
            if failure is not None:
                failures.append(failure)
                continue
            assert result is not None
            output_records.append(result)
            status_counts[result["canonical_status"]] += 1
            all_diagnostics.update(result["diagnostic_codes"])
            if did_reuse:
                reused += 1
            else:
                reprojected += 1

        manifest["records"] = output_records
        manifest["accounted_record_count"] = len(output_records)
        manifest["input_integrity_failure_count"] = len(failures)
        manifest["input_integrity_failures"] = failures
        manifest["reuse_count"] = reused
        manifest["reproject_count"] = reprojected
        manifest["counts"] = {status: status_counts[status] for status in _RECORD_STATUSES}
        manifest["diagnostics"] = [{"code": code, "count": all_diagnostics[code]} for code in sorted(all_diagnostics)]
        manifest["status"] = "complete" if not failures else "incomplete"
        store.write_manifest(manifest)
        return manifest

    def _dependencies(self, parsed_manifest: Mapping[str, Any]) -> dict[str, Any]:
        parsed_dependencies = parsed_manifest.get("dependencies", {})
        if not isinstance(parsed_dependencies, Mapping):
            raise ValueError("Parsed manifest dependencies must be an object")
        pipeline_version = parsed_dependencies.get("pipeline_version")
        if not isinstance(pipeline_version, str) or not pipeline_version:
            raise ValueError("Parsed manifest dependencies.pipeline_version is required")
        return {
            "canonical_pipeline_version": CANONICAL_PIPELINE_VERSION,
            "canonical_versions": self.versions.to_dict(),
            "parsed_schema_version": parsed_manifest.get("schema_version"),
            "parsed_parser_version": parsed_manifest.get("parser_version"),
            "parsed_pipeline_version": pipeline_version,
            "parsed_rule_versions": dict(parsed_dependencies.get("rule_versions", {})),
        }

    def _completed_manifest_if_current(
        self,
        store: CanonicalRunStore,
        inputs: tuple[_ParsedInput, ...],
        parsed_manifest: Mapping[str, Any],
        parsed_manifest_sha256: str,
        dependencies: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        existing = store.read_manifest()
        if not isinstance(existing, Mapping) or existing.get("status") != "complete":
            return None
        if (
            existing.get("parsed_manifest_sha256") != parsed_manifest_sha256
            or existing.get("dependencies") != dict(dependencies)
            or existing.get("input_record_count") != len(inputs)
            or existing.get("accounted_record_count") != len(inputs)
        ):
            return None
        indexed = {str(record.get("parsed_identity_key")): record for record in existing.get("records", ()) if isinstance(record, Mapping)}
        if len(indexed) != len(inputs):
            return None
        for item in inputs:
            parsed_value, parsed_sha, error = self._validated_parsed_record(item)
            if error is not None or parsed_value is None or parsed_sha is None:
                return None
            identity = parsed_value.get("identity") if isinstance(parsed_value, Mapping) else None
            key = identity.get("key") if isinstance(identity, Mapping) else None
            prior = indexed.get(str(key))
            if not isinstance(prior, Mapping) or prior.get("parsed_record_sha256") != parsed_sha:
                return None
            observation, observation_error = self._observation(
                parsed_value,
                parsed_sha,
                item.manifest_record,
                parsed_manifest,
                parsed_manifest_sha256,
            )
            if observation_error is not None or observation is None:
                return None
            if not self._stored_record_is_current(prior, observation, dependencies):
                return None
        return dict(existing)

    def _load_reusable_records(
        self,
        source: str,
        locale: str,
        parsed_run_id: str,
        parsed_manifest_sha256: str,
        dependencies: Mapping[str, Any],
    ) -> dict[str, Mapping[str, Any]]:
        if self.reuse_manifest_path is None:
            return {}
        prior = _read_json(self.reuse_manifest_path)
        if prior.get("status") != "complete":
            raise ValueError("reuse manifest must be a complete Canonical manifest")
        if (
            prior.get("source") != source
            or prior.get("locale") != locale
            or prior.get("parsed_run_id") != parsed_run_id
            or prior.get("parsed_manifest_sha256") != parsed_manifest_sha256
            or prior.get("dependencies") != dict(dependencies)
        ):
            return {}
        records = prior.get("records")
        if not isinstance(records, list):
            raise ValueError("reuse manifest records must be a list")
        return {
            str(record["parsed_identity_key"]): record
            for record in records
            if isinstance(record, Mapping) and isinstance(record.get("parsed_identity_key"), str)
        }

    def _process_item(
        self,
        *,
        item: _ParsedInput,
        parsed_manifest_sha256: str,
        parsed_manifest: Mapping[str, Any],
        store: CanonicalRunStore,
        reusable: Mapping[str, Mapping[str, Any]],
    ) -> tuple[dict[str, Any] | None, bool, dict[str, Any] | None]:
        parsed_value, parsed_sha, error = self._validated_parsed_record(item)
        if error is not None or parsed_value is None or parsed_sha is None:
            return None, False, {"input_index": item.index, "record_locator": item.locator, "code": error or "PARSED_RECORD_INVALID"}
        identity = parsed_value.get("identity") if isinstance(parsed_value, Mapping) else None
        identity_key = identity.get("key") if isinstance(identity, Mapping) else None
        if not isinstance(identity_key, str) or not identity_key:
            return None, False, {"input_index": item.index, "record_locator": item.locator, "code": "PARSED_RECORD_IDENTITY_INVALID"}
        observation, observation_error = self._observation(
            parsed_value,
            parsed_sha,
            item.manifest_record,
            parsed_manifest,
            parsed_manifest_sha256,
        )
        if observation_error is not None or observation is None:
            return None, False, {"input_index": item.index, "record_locator": item.locator, "code": observation_error or "PARSED_RECORD_CONTRACT_INVALID"}
        prior = reusable.get(identity_key)
        if prior is not None and self._stored_record_is_current(prior, observation, self._dependencies(parsed_manifest)):
            try:
                body = Path(str(prior["canonical_record_path"])).read_bytes()
                written = store.write_record(str(prior["record_id"]), body)
            except (OSError, FileExistsError):
                return None, False, {"input_index": item.index, "record_locator": item.locator, "code": "CANONICAL_RECORD_IMMUTABLE_CONFLICT"}
            reused = dict(prior)
            reused.update({"canonical_record_path": written["path"], "canonical_record_sha256": written["sha256"], "reuse": True})
            return reused, True, None
        try:
            if observation.parsed_status == "blocked_integrity":
                record = project_parsed_input(parsed_value, observation=observation, versions=self.versions)
            else:
                record = project_parsed_detail(decode_parsed_detail(parsed_value), observation=observation, versions=self.versions)
        except (TypeError, ValueError, KeyError) as exc:
            return None, False, {"input_index": item.index, "record_locator": item.locator, "code": "PARSED_RECORD_CONTRACT_INVALID", "detail": str(exc)}
        body = serialize_canonical_record(record)
        try:
            written = store.write_record(record.record_id, body)
        except FileExistsError:
            return None, False, {"input_index": item.index, "record_locator": item.locator, "code": "CANONICAL_RECORD_IMMUTABLE_CONFLICT"}
        serialized = record.to_dict()
        return {
            "parsed_identity_key": record.parsed_identity.key,
            "parsed_record_sha256": parsed_sha,
            "record_id": record.record_id,
            "canonical_status": record.status,
            "dependency_fingerprint": record.dependency_fingerprint,
            "content_fingerprint": record.content_fingerprint,
            "canonical_record_path": written["path"],
            "canonical_record_sha256": written["sha256"],
            "diagnostic_codes": list(_diagnostic_codes(serialized)),
            "reuse": False,
        }, False, None

    def _validated_parsed_record(self, item: _ParsedInput) -> tuple[Mapping[str, Any] | None, str | None, str | None]:
        path_value = item.manifest_record.get("record_path")
        expected_sha = item.manifest_record.get("record_sha256")
        if not isinstance(path_value, str) or not isinstance(expected_sha, str):
            return None, None, "PARSED_RECORD_PATH_INVALID"
        path = Path(path_value)
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            return None, None, "PARSED_RECORD_MISSING"
        except OSError:
            return None, None, "PARSED_RECORD_READ_ERROR"
        actual_sha = _sha256(body)
        if actual_sha != expected_sha:
            return None, None, "PARSED_RECORD_SHA256_MISMATCH"
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, None, "PARSED_RECORD_JSON_INVALID"
        if not isinstance(value, Mapping):
            return None, None, "PARSED_RECORD_NOT_OBJECT"
        return value, actual_sha, None

    def _observation(
        self,
        parsed_value: Mapping[str, Any],
        parsed_sha: str,
        manifest_record: Mapping[str, Any],
        parsed_manifest: Mapping[str, Any],
        parsed_manifest_sha256: str,
    ) -> tuple[CanonicalObservation | None, str | None]:
        metadata = parsed_value.get("metadata")
        dependencies = parsed_manifest.get("dependencies")
        if not isinstance(metadata, Mapping) or not isinstance(dependencies, Mapping):
            return None, "PARSED_RECORD_METADATA_INVALID"
        status = metadata.get("parse_status")
        fingerprint = metadata.get("parsed_fingerprint")
        record_path = manifest_record.get("record_path")
        if not isinstance(status, str) or not isinstance(fingerprint, str):
            return None, "PARSED_RECORD_METADATA_INVALID"
        try:
            return CanonicalObservation(
                parsed_run_id=parsed_manifest.get("parsed_run_id"),
                parsed_manifest_path=str(self.parsed_manifest_path),
                parsed_manifest_sha256=parsed_manifest_sha256,
                parsed_record_path=record_path,
                parsed_record_sha256=parsed_sha,
                parsed_schema_version=parsed_manifest.get("schema_version"),
                parsed_parser_version=parsed_manifest.get("parser_version"),
                parsed_pipeline_version=dependencies.get("pipeline_version"),
                parsed_status=status,
                parsed_semantic_fingerprint=fingerprint,
                parsed_rule_versions=tuple(sorted((str(key), str(value)) for key, value in _mapping(dependencies.get("rule_versions", {}), "parsed rule_versions").items())),
            ), None
        except (TypeError, ValueError):
            return None, "PARSED_RECORD_OBSERVATION_INVALID"

    def _stored_record_is_current(
        self,
        record: Mapping[str, Any],
        observation: CanonicalObservation,
        dependencies: Mapping[str, Any],
    ) -> bool:
        path_value = record.get("canonical_record_path")
        expected_sha = record.get("canonical_record_sha256")
        if not isinstance(path_value, str) or not isinstance(expected_sha, str):
            return False
        path = Path(path_value)
        try:
            body = path.read_bytes()
            if _sha256(body) != expected_sha:
                return False
            value = json.loads(body.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not isinstance(value, Mapping):
            return False
        stored_observation = value.get("observation")
        versions = value.get("versions")
        stored_identity = value.get("parsed_identity")
        if (
            not isinstance(stored_observation, Mapping)
            or not isinstance(versions, Mapping)
            or not isinstance(stored_identity, Mapping)
        ):
            return False
        expected_dependency_fingerprint = canonical_dependency_fingerprint(
            observation.dependency_projection(),
            dict(dependencies["canonical_versions"]),
        )
        return (
            value.get("record_id") == record.get("record_id")
            and stored_identity.get("key") == record.get("parsed_identity_key")
            and stored_observation == observation.to_dict()
            and value.get("dependency_fingerprint") == expected_dependency_fingerprint
            and record.get("dependency_fingerprint") == expected_dependency_fingerprint
            and value.get("content_fingerprint") == record.get("content_fingerprint")
            and versions == dependencies.get("canonical_versions")
        )
