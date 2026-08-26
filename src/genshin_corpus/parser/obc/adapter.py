from __future__ import annotations

import json
import hashlib
from dataclasses import replace
from typing import Any, Iterable, Mapping

from ..classification import classify_obc_component
from ..contracts import ContractMetadata, Diagnostic, ParsedIdentity, RawRef, SourcePosition
from ..dialogue import parse_dialogue_graph
from ..fingerprints import parsed_fingerprint, source_fingerprint
from ..identity import component_identity, content_unit_identity, detail_identity, module_identity
from ..models import ParsedComponent, ParsedContentUnit, ParsedDetail, ParsedModule, ParsedUnknown
from ..rich_text import looks_like_markup, parse_rich_text


class OBCIntegrityError(ValueError):
    """The Raw envelope cannot be parsed without inventing source structure."""


def _pointer_token(value: str | int) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _join_pointer(*parts: str | int) -> str:
    return "/" + "/".join(_pointer_token(part) for part in parts)


def _projection_value(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, tuple):
        return [_projection_value(item) for item in value]
    if isinstance(value, list):
        return [_projection_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _projection_value(item) for key, item in value.items()}
    return value


def _decode_component_data(value: Any) -> tuple[str, Any, Diagnostic | None]:
    if isinstance(value, str):
        try:
            return "json_string", json.loads(value), None
        except json.JSONDecodeError:
            return "plain_string", value, Diagnostic("COMPONENT_DATA_NOT_JSON", "component data is retained as plain text")
    return "json_value", value, None


def _iter_markup(value: Any, path: tuple[str | int, ...] = ()) -> Iterable[tuple[str, tuple[str | int, ...]]]:
    if isinstance(value, str):
        if looks_like_markup(value):
            yield value, path
    elif isinstance(value, Mapping):
        for key, child in value.items():
            yield from _iter_markup(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_markup(child, path + (index,))


def _layout_observations(template_layout: Any) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(template_layout, Mapping):
        return result
    tabs = template_layout.get("tab")
    if not isinstance(tabs, list):
        return result
    for tab_index, tab in enumerate(tabs):
        if not isinstance(tab, Mapping):
            continue
        groups = tab.get("module_group")
        if not isinstance(groups, list):
            continue
        for group_index, group in enumerate(groups):
            if not isinstance(group, Mapping):
                continue
            modules = group.get("module")
            if not isinstance(modules, list):
                continue
            for placement_index, placement in enumerate(modules):
                if not isinstance(placement, Mapping) or placement.get("id") is None:
                    continue
                module_id = str(placement["id"])
                result.setdefault(module_id, []).append({
                    "tab_index": tab_index,
                    "tab_id": tab.get("tab_id"),
                    "group_index": group_index,
                    "group_id": group.get("module_group_id"),
                    "placement_index": placement_index,
                    "layout": group.get("layout"),
                    "position": placement.get("pos"),
                    "group_name": group.get("name"),
                })
    return result


class OBCDetailParser:
    source = "mihoyo_obc"

    def parse(
        self,
        body: bytes,
        *,
        raw_ref: RawRef,
        content_id: str,
        channel_memberships: Iterable[str] = (),
    ) -> ParsedDetail:
        if hashlib.sha256(body).hexdigest().lower() != raw_ref.artifact_sha256.lower():
            raise OBCIntegrityError("detail Raw artifact SHA-256 does not match RawRef")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OBCIntegrityError("detail Raw artifact is not valid UTF-8 JSON") from exc
        if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), Mapping):
            raise OBCIntegrityError("detail response missing object data envelope")
        page = payload["data"].get("page")
        if not isinstance(page, Mapping):
            raise OBCIntegrityError("detail response missing data.page object")
        page_id = page.get("id")
        if page_id is not None and str(page_id) != str(content_id):
            raise OBCIntegrityError(f"page id {page_id!r} conflicts with requested content_id {content_id!r}")
        modules = page.get("modules")
        if not isinstance(modules, list):
            raise OBCIntegrityError("detail page modules is not a list")
        page_ref = replace(raw_ref, content_id=str(content_id), json_pointer=_join_pointer("data", "page"))
        placements = _layout_observations(page.get("template_layout"))
        parsed_modules = []
        unsupported_modules = []
        for index, module in enumerate(modules):
            if isinstance(module, Mapping):
                parsed_modules.append(self._parse_module(
                    module,
                    module_index=index,
                    content_id=str(content_id),
                    page_ref=page_ref,
                    layout_observations=placements.get(str(module.get("id")), []),
                ))
            else:
                unsupported_modules.append(ParsedUnknown.from_value(
                    identity=ParsedIdentity(
                        kind="module",
                        key=f"content:{content_id}:module:unknown:{index}",
                        stability="snapshot_only",
                        components=(("content_id", str(content_id)), ("array_index", str(index))),
                    ),
                    raw_value=module,
                    raw_refs=(replace(page_ref, json_pointer=_join_pointer("data", "page", "modules", index)),),
                    source_position=SourcePosition(_join_pointer("data", "page", "modules", index), array_index=index, ordering=index),
                    reason="module is not an object",
                    context={"module_index": index},
                    diagnostics=(Diagnostic("NON_OBJECT_MODULE", "non-object module retained as unsupported", "error"),),
                ))
        invalid_modules = bool(unsupported_modules)
        diagnostics = (Diagnostic("NON_OBJECT_MODULE", "non-object module retained as unsupported", "error"),) if invalid_modules else ()
        metadata = ContractMetadata(
            raw_refs=(page_ref,),
            source_position=SourcePosition(page_ref.json_pointer),
            source_fingerprint=source_fingerprint(page),
            parsed_fingerprint=parsed_fingerprint({
                "content_id": str(content_id),
                "page_id": None if page_id is None else str(page_id),
                "template_layout": page.get("template_layout"),
                "modules": [self._module_projection(module) for module in parsed_modules],
                "unsupported_modules": [
                    {"identity": module.identity.key, "raw_value": module.raw_value}
                    for module in unsupported_modules
                ],
                "channel_memberships": sorted({str(item) for item in channel_memberships}),
            }),
            parse_status="parsed_with_anomalies" if invalid_modules else "parsed",
            diagnostics=diagnostics,
        )
        source_metadata = {key: value for key, value in page.items() if key not in {"modules", "template_layout"}}
        return ParsedDetail(
            identity=detail_identity(self.source, raw_ref.locale, str(content_id)),
            metadata=metadata,
            source=self.source,
            locale=raw_ref.locale,
            run_id=raw_ref.run_id,
            content_id=str(content_id),
            page_id=None if page_id is None else str(page_id),
            name=page.get("name"),
            page_type=page.get("page_type"),
            source_metadata=source_metadata,
            source_template_layout=page.get("template_layout"),
            modules=tuple(parsed_modules),
            unsupported_modules=tuple(unsupported_modules),
            channel_memberships=tuple(dict.fromkeys(str(item) for item in channel_memberships)),
        )

    @staticmethod
    def _component_projection(component: ParsedComponent) -> dict[str, Any]:
        return {
            "identity": component.identity.key,
            "source_component_id": component.source_component_id,
            "source_data_encoding": component.source_data_encoding,
            "source_layout": component.source_layout,
            "source_style": component.source_style,
            "units": [
                {"identity": unit.identity.key, "value": _projection_value(unit.value)}
                for unit in component.units
            ],
            "unsupported": [
                {"identity": item.identity.key, "raw_value": item.raw_value}
                for item in component.unsupported
            ],
        }

    @classmethod
    def _module_projection(cls, module: ParsedModule) -> dict[str, Any]:
        return {
            "identity": module.identity.key,
            "source_module_id": module.source_module_id,
            "module_index": module.module_index,
            "name": module.name,
            "repeated": module.repeated,
            "is_submodule": module.is_submodule,
            "origin_module_id": module.origin_module_id,
            "components": [cls._component_projection(component) for component in module.components],
            "unsupported": [
                {"identity": item.identity.key, "raw_value": item.raw_value}
                for item in module.unsupported
            ],
            "layout_observations": list(module.layout_observations),
        }

    def _parse_module(
        self,
        module: Mapping[str, Any],
        *,
        module_index: int,
        content_id: str,
        page_ref: RawRef,
        layout_observations: list[dict[str, Any]],
    ) -> ParsedModule:
        module_id = None if module.get("id") is None else str(module["id"])
        module_ref = replace(page_ref, json_pointer=_join_pointer("data", "page", "modules", module_index))
        components = module.get("components")
        component_items = components if isinstance(components, list) else []
        component_ordinals: dict[str, int] = {}
        parsed_components = []
        module_diagnostics: list[Diagnostic] = []
        unsupported_components: list[ParsedUnknown] = []
        if not isinstance(components, list):
            module_diagnostics.append(Diagnostic("MODULE_COMPONENTS_NOT_LIST", "module components is not a list"))
            unsupported_components.append(ParsedUnknown.from_value(
                identity=ParsedIdentity(
                    kind="component_container",
                    key=f"content:{content_id}:module:{module_id or 'missing'}:components",
                    stability="snapshot_only",
                    components=(("content_id", str(content_id)), ("module_id", module_id or "missing")),
                ),
                raw_value=components,
                raw_refs=(replace(module_ref, embedded_json_pointer="/components"),),
                source_position=SourcePosition(module_ref.json_pointer + "/components"),
                reason="module components value is not a list",
                context={"module_index": module_index},
                diagnostics=(Diagnostic("MODULE_COMPONENTS_NOT_LIST", "non-list components value retained as unsupported", "error"),),
            ))
        for component_index, component in enumerate(component_items):
            if not isinstance(component, Mapping):
                module_diagnostics.append(Diagnostic("NON_OBJECT_COMPONENT", "non-object component was not discarded", "error", _join_pointer("components", component_index)))
                unsupported_components.append(ParsedUnknown.from_value(
                    identity=ParsedIdentity(
                        kind="component",
                        key=f"content:{content_id}:module:{module.get('id', 'missing')}:component:unknown:{component_index}",
                        stability="snapshot_only",
                        components=(("content_id", str(content_id)), ("module_index", str(module_index)), ("array_index", str(component_index))),
                    ),
                    raw_value=component,
                    raw_refs=(replace(module_ref, json_pointer=_join_pointer("data", "page", "modules", module_index, "components", component_index)),),
                    source_position=SourcePosition(_join_pointer("data", "page", "modules", module_index, "components", component_index), array_index=component_index, ordering=component_index),
                    reason="component is not an object",
                    context={"module_index": module_index, "component_index": component_index},
                    diagnostics=(Diagnostic("NON_OBJECT_COMPONENT", "non-object component retained as unsupported", "error"),),
                ))
                continue
            component_id = None if component.get("component_id") is None else str(component["component_id"])
            ordinal = component_ordinals.get(component_id or "<missing>", 0)
            component_ordinals[component_id or "<missing>"] = ordinal + 1
            parsed_components.append(self._parse_component(
                component,
                content_id=content_id,
                module_index=module_index,
                component_index=component_index,
                component_id=component_id,
                same_type_ordinal=ordinal,
                component_ref=replace(module_ref, json_pointer=_join_pointer("data", "page", "modules", module_index, "components", component_index)),
            ))
        metadata = ContractMetadata(
            raw_refs=(module_ref,),
            source_position=SourcePosition(module_ref.json_pointer, array_index=module_index, ordering=module_index),
            source_fingerprint=source_fingerprint(module),
            parsed_fingerprint=parsed_fingerprint({
                "module": module_id,
                "components": [self._component_projection(item) for item in parsed_components],
                "unsupported": [
                    {"identity": item.identity.key, "raw_value": item.raw_value}
                    for item in unsupported_components
                ],
                "layout": layout_observations,
            }),
            parse_status="parsed_with_anomalies" if module_diagnostics else "parsed",
            diagnostics=tuple(module_diagnostics),
        )
        return ParsedModule(
            identity=module_identity(content_id, module_id),
            metadata=metadata,
            source_module_id=module_id,
            module_index=module_index,
            name=module.get("name"),
            repeated=module.get("repeated"),
            is_submodule=module.get("is_submodule"),
            origin_module_id=None if module.get("origin_module_id") is None else str(module["origin_module_id"]),
            components=tuple(parsed_components),
            unsupported=tuple(unsupported_components),
            layout_observations=tuple(layout_observations),
        )

    def _parse_component(
        self,
        component: Mapping[str, Any],
        *,
        content_id: str,
        module_index: int,
        component_index: int,
        component_id: str | None,
        same_type_ordinal: int,
        component_ref: RawRef,
    ) -> ParsedComponent:
        encoding, decoded, decode_diagnostic = _decode_component_data(component.get("data"))
        provenance, content_role = classify_obc_component(component_id)
        generic_diagnostics = tuple(item for item in (decode_diagnostic,) if item is not None)
        diagnostics = tuple(item for item in (
            None if component_id == "interactive_dialogue" else Diagnostic("UNSUPPORTED_COMPONENT", "no source-specific component handler has been promoted", "info"),
            *generic_diagnostics,
        ) if item is not None)
        units: list[ParsedContentUnit] = []
        base_key = component_identity(content_id, component_id, same_type_ordinal).key
        generic_value = {"raw": component.get("data"), "decoded": decoded}
        units.append(ParsedContentUnit(
            identity=content_unit_identity(base_key, "data", 0),
            metadata=ContractMetadata(
                raw_refs=(replace(component_ref, embedded_json_pointer=None),),
                source_position=SourcePosition(component_ref.json_pointer, array_index=component_index, ordering=component_index),
                source_fingerprint=source_fingerprint(component.get("data")),
                parsed_fingerprint=parsed_fingerprint(generic_value),
                parse_status="parsed_with_anomalies" if decode_diagnostic else "parsed",
                provenance=provenance,
                content_role=content_role,
                diagnostics=diagnostics,
            ),
            value=generic_value,
        ))
        for ordinal, (markup, path) in enumerate(_iter_markup(decoded), start=1):
            embedded = _join_pointer(*path) if path else ""
            units.append(ParsedContentUnit(
                identity=content_unit_identity(base_key, f"rich_text:{embedded or 'root'}", ordinal),
                metadata=ContractMetadata(
                    raw_refs=(replace(component_ref, embedded_json_pointer=embedded or None),),
                    source_position=SourcePosition(component_ref.json_pointer, array_index=component_index, ordering=ordinal),
                    source_fingerprint=source_fingerprint(markup),
                    parsed_fingerprint=parsed_fingerprint(parse_rich_text(markup)),
                    provenance=provenance,
                    content_role=content_role,
                ),
                value={"kind": "rich_text", **parse_rich_text(markup)},
            ))
        graph = None
        if component_id == "interactive_dialogue":
            graph = parse_dialogue_graph(decoded, component_ref=component_ref)
            units.append(ParsedContentUnit(
                identity=content_unit_identity(base_key, "dialogue_graph", len(units)),
                metadata=ContractMetadata(
                    raw_refs=(component_ref,),
                    source_position=SourcePosition(component_ref.json_pointer, ordering=len(units)),
                    source_fingerprint=source_fingerprint(decoded),
                    parsed_fingerprint=graph.parsed_fingerprint,
                    parse_status="parsed_with_anomalies" if graph.diagnostics else "parsed",
                    provenance=provenance,
                    content_role=content_role,
                    diagnostics=graph.diagnostics,
                ),
                value=graph,
            ))
        component_diagnostics = diagnostics if graph is None else diagnostics + graph.diagnostics
        status = "preserved_unsupported" if graph is None else ("parsed_with_anomalies" if component_diagnostics else "parsed")
        unsupported: tuple[ParsedUnknown, ...] = ()
        return ParsedComponent(
            identity=component_identity(content_id, component_id, same_type_ordinal),
            metadata=ContractMetadata(
                raw_refs=(component_ref,),
                source_position=SourcePosition(component_ref.json_pointer, array_index=component_index, ordering=component_index),
                source_fingerprint=source_fingerprint(component),
                parsed_fingerprint=parsed_fingerprint({
                    "component_id": component_id,
                    "encoding": encoding,
                    "units": [
                        {"identity": unit.identity.key, "value": _projection_value(unit.value)}
                        for unit in units
                    ],
                }),
                parse_status=status,
                provenance=provenance,
                content_role=content_role,
                diagnostics=component_diagnostics,
            ),
            source_component_id=component_id,
            source_data_encoding=encoding,
            source_layout=component.get("layout"),
            source_style=component.get("style"),
            units=tuple(units),
            unsupported=unsupported,
        )


def parse_obc_detail(body: bytes, *, raw_ref: RawRef, content_id: str, channel_memberships: Iterable[str] = ()) -> ParsedDetail:
    return OBCDetailParser().parse(body, raw_ref=raw_ref, content_id=content_id, channel_memberships=channel_memberships)
