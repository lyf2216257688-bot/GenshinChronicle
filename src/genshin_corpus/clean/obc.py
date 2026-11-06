"""Direct Raw-to-Clean OBC adapter. Source values have one primary owner."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from ..evidence_io import digest
from ..parser.contracts import RawRef
from ..parser.dialogue import parse_dialogue_graph
from ..parser.obc.adapter import OBCIntegrityError, _layout_observations
from .context import RecordContext
from .field_rules import FIELD_TYPES
from .markup import MarkupProjector, content_target
from .models import SCHEMA_VERSION, SourceNumber, dependencies, observation_id, pointer_token, source_json

KNOWN_COMPONENTS = frozenset("""collapse_panel map_desc material_base_info rich_base_info
interactive_dialogue good_desc npc_base_info card_group_info cave_base_info base_info strategy
multi_table card_base_info card_skill equipment_growth_info timeline_base_info monster_base_info
business_card artifact_list_v2 equipment_base_info role_base_info role_talent recommend
role_ascension role_voice filter_table role_relation dress_base_info spiral_abyss_target copy_base_info""".split())

# These paths are observed resource slots. A URL in arbitrary prose remains prose.
_IMAGE_PATHS = frozenset("""/avatar_m /avatar_pc /card_img /common_img /gold_img /icon_url
/image /img /long_img /feedback/*/img /list/*/cover /list/*/icon /list/*/image /list/*/img
/list/*/materials/*/img /list/*/first_half/list/*/entry_pages/*/img
/list/*/second_half/list/*/entry_pages/*/img""".split())
_CONTROL_PATHS = frozenset("""/baseInfoSwitchDisabled /is_show_switch /layout_ /repeated
/switch /without_border /list/*/table/*/isUploading""".split())
_LINK_PATHS = frozenset("""/link /more_link /feedback/*/link /list/*/link
/list/*/materials/*/link /list/*/first_half/list/*/entry_pages/*/link
/list/*/second_half/list/*/entry_pages/*/link""".split())


def _pattern(path: str) -> str:
    return re.sub(r"/[0-9]+(?=/|$)", "/*", path)


def record_fingerprint(detail: dict[str, Any], discovery: dict[str, Any]) -> str:
    def descriptor(artifact):
        return {key: artifact[key] for key in ("kind", "path", "sha256")}
    return digest({"detail": descriptor(detail), "discovery": {
        **{key: value for key, value in discovery.items() if key != "artifacts"},
        "artifacts": {name: descriptor(value) for name, value in discovery.get("artifacts", {}).items()},
    }, "versions": dependencies()})


class OBCRecordBuilder:
    def __init__(self, *, source: str, locale: str, raw_run_id: str, content_id: str):
        if source != "mihoyo_obc" or not locale or not raw_run_id or not content_id:
            raise ValueError("Clean OBC adapter requires explicit source, locale, run and content identity")
        self.source_item = {"source": source, "locale": locale, "content_id": content_id}
        self.raw_run_id = raw_run_id
        self.ctx = RecordContext()
        self.component_id = ""
        self._source_artifacts: dict[str, Any] = {}
        self._artifact_names: dict[str, str] = {}
        self._tags: list[dict[str, Any]] = []
        self._channel_names: set[str] = set()

    def add_artifact(self, name: str, artifact: dict[str, Any]) -> str:
        body = artifact["body"]
        actual = hashlib.sha256(body).hexdigest()
        if actual != artifact["sha256"]:
            raise OBCIntegrityError("RAW_ARTIFACT_SHA256_MISMATCH")
        if name in self._artifact_names:
            raise ValueError("Duplicate supporting artifact name")
        artifact_id = self.ctx.artifact({"kind": artifact["kind"], "path": artifact["path"], "sha256": actual})
        self._artifact_names[name] = artifact_id
        self._source_artifacts[name] = source_json(body.decode("utf-8"))
        return artifact_id

    def source_origin(self, name: str, pointer: str) -> str:
        return self.ctx.origin(self._artifact_names[name], [{"op": "pointer", "path": pointer}])

    def value(self, value: Any, origin: str, path: str = "") -> dict[str, Any]:
        if isinstance(value, SourceNumber):
            self.ctx.claim(value, path)
            return self.ctx.block("scalar", origin, value={"source_type": value.source_type,
                                                          "lexeme": str(value), "origin_id": origin})
        if isinstance(value, str):
            return self._string(value, origin, path)
        if isinstance(value, dict):
            if self.component_id == "interactive_dialogue" and path == "":
                return self._dialogue(value, origin)
            if isinstance(value.get("header"), list) and isinstance(value.get("row"), list):
                if all(isinstance(row, list) for row in value["row"]):
                    return self._table(value, origin, path)
                self.ctx.diagnostic("NON_MATRIX_TABLE_ROWS", origin)
            if "key" in value and "value" in value:
                label_origin = self.ctx.child(origin, "key")
                label = self.value(value["key"], label_origin, path + "/key")
                body = self.value(value["value"], self.ctx.child(origin, "value"), path + "/value")
                return self.ctx.block("field", origin, field_key=self.component_id + ":" + _pattern(path),
                                      label=label, value=body,
                                      children=self._members(value, origin, path, {"key", "value"}))
            title_key = next((key for key in ("tab_name", "name", "title", "nickname")
                              if self.component_id in KNOWN_COMPONENTS and isinstance(value.get(key), str)
                              and not isinstance(value[key], SourceNumber)), None)
            title = self.value(value[title_key], self.ctx.child(origin, title_key), path + "/" + title_key) if title_key else None
            children = self._members(value, origin, path, {title_key} if title_key else None)
            if not value:
                self.ctx.claim(value, path)
            return self.ctx.block("group" if self.component_id in KNOWN_COMPONENTS else "generic", origin,
                                  source_type="object", title=title, title_source_key=title_key, children=children)
        if isinstance(value, list):
            if not value:
                self.ctx.claim(value, path)
            return self.ctx.block("group", origin, source_type="array", children=[
                self.value(child, self.ctx.child(origin, index), path + "/" + str(index))
                for index, child in enumerate(value)
            ])
        self.ctx.claim(value, path)
        source_type = "null" if value is None else "boolean"
        return self.ctx.block("scalar", origin, value={"source_type": source_type, "value": value,
                                                       "lexeme": "null" if value is None else str(value).lower(),
                                                       "origin_id": origin})

    def _string(self, value: str, origin: str, path: str) -> dict[str, Any]:
        normalized = _pattern(path)
        if value == "":
            self.ctx.claim(value, path)
            return self.ctx.block("paragraph", origin, value=self.ctx.text(value, origin))
        if not self.component_id and value and (
            path in {"/ext", "/ext/fe_ext"} or re.fullmatch(r"/ext(?:/fe_ext)?/c_[0-9]+/filter/text", path)
        ):
            try:
                decoded = source_json(value)
            except ValueError:
                self.ctx.diagnostic("UNRECOGNIZED_METADATA_JSON", origin)
            else:
                return self.value(decoded, self.ctx.derive(origin, {"op": "json_decode"}), path)
        if (self.component_id in KNOWN_COMPONENTS and normalized in _IMAGE_PATHS) or (
            not self.component_id and normalized in {"/icon_url", "/header_img_url", "/icon", "/avatar_url"}
        ) or (self.component_id == "interactive_dialogue" and re.fullmatch(r"(?:/list/[0-9]+)?/contents/[^/]+/icon", path)):
            self.ctx.claim(value, path, "resource")
            media_id = self.ctx.resource("image", value, origin, owner_path=path)
            return self.ctx.block("media", origin, media_id=media_id)
        if self.component_id in KNOWN_COMPONENTS and normalized in _LINK_PATHS:
            self.ctx.claim(value, path, "reference")
            relation_id = self.ctx.relation(origin, kind="hyperlink", href=value,
                                            target_content_id=content_target(value), owner_path=path)
            return self.ctx.block("group", origin, source_type="reference", relation_id=relation_id)
        if self.component_id == "role_voice" and normalized == "/list/*/table/*/audio_url":
            self.ctx.claim(value, path, "resource")
            media_id = self.ctx.resource("audio", value, origin, owner_path=path.rsplit("/", 1)[0])
            return self.ctx.block("media", origin, media_id=media_id)
        self.ctx.claim(value, path)
        if "<" in value and ">" in value:
            projector = MarkupProjector(self.ctx, origin, value)
            return self.ctx.block("group", origin, source_type="html", children=projector.project())
        return self.ctx.block("paragraph", origin, value=self.ctx.text(value, origin))

    def _members(self, value: dict[str, Any], origin: str, path: str,
                 excluded: set[str] | None = None) -> list[dict[str, Any]]:
        children = []
        for key, child in value.items():
            if excluded and key in excluded:
                continue
            child_path = path + "/" + pointer_token(key)
            child_origin = self.ctx.child(origin, key)
            if self.component_id in KNOWN_COMPONENTS and _pattern(child_path) in _CONTROL_PATHS:
                self.ctx.claim(child, child_path, "metadata")
                self.ctx.extensions.append({"kind": "component_metadata", "name": key,
                                            "origin_id": child_origin, "value": self._plain(child)})
                continue
            label_origin = self.ctx.derive(origin, {"op": "json_key", "key": key})
            block = self.ctx.block("field", child_origin,
                                   field_key=self.component_id + ":" + _pattern(child_path),
                                   label=self.ctx.text(key, label_origin),
                                   value=self.value(child, child_origin, child_path))
            children.append(block)
        if "ep_id" in value:
            target = str(value["ep_id"])
            self.ctx.relation(origin, kind="entry_reference", target_content_id=target,
                              owner_path=path, target_origin_id=self.ctx.child(origin, "ep_id"),
                              link_origin_id=self.ctx.child(origin, "link") if "link" in value else None)
            href_target = content_target(str(value.get("link", "")))
            if href_target and href_target != target:
                self.ctx.diagnostic("ENTRY_TARGET_CONFLICT", origin)
        return children

    @staticmethod
    def _plain(value: Any) -> Any:
        if isinstance(value, SourceNumber):
            return {"source_type": value.source_type, "lexeme": str(value)}
        if isinstance(value, dict):
            return [{"key": key, "value": OBCRecordBuilder._plain(child)} for key, child in value.items()]
        if isinstance(value, list):
            return [OBCRecordBuilder._plain(child) for child in value]
        return value

    def _table(self, value: dict[str, Any], origin: str, path: str) -> dict[str, Any]:
        header_origin = self.ctx.child(origin, "header")
        row_origin = self.ctx.child(origin, "row")
        if not value["header"]:
            self.ctx.claim([], path + "/header")
        if not value["row"]:
            self.ctx.claim([], path + "/row")
        header = [self.value(cell, self.ctx.child(header_origin, index), path + f"/header/{index}")
                  for index, cell in enumerate(value["header"])]
        rows = []
        for row_index, row in enumerate(value["row"]):
            current_origin = self.ctx.child(row_origin, row_index)
            if not row:
                self.ctx.claim([], path + f"/row/{row_index}")
            cells = [{"column_index": column, "origin_id": self.ctx.child(current_origin, column),
                      "blocks": [self.value(cell, self.ctx.child(current_origin, column),
                                            path + f"/row/{row_index}/{column}")]}
                     for column, cell in enumerate(row)]
            rows.append({"row_index": row_index, "origin_id": current_origin, "cells": cells})
        return self.ctx.block("table", origin, table_format="json", header=header, rows=rows,
                              children=self._members(value, origin, path, {"header", "row"}))

    def _dialogue(self, value: dict[str, Any], origin: str) -> dict[str, Any]:
        groups = []
        excluded: set[str] = set()
        top = any(value.get(key) for key in ("contents", "child_ids", "root_id"))
        if top:
            groups.append(self._dialogue_group(value, origin, "", len(groups)))
            excluded.update({"contents", "child_ids", "root_id"})
        listed = value.get("list")
        if isinstance(listed, list):
            excluded.add("list")
            if not listed:
                self.ctx.claim(listed, "/list", "structure")
            list_origin = self.ctx.child(origin, "list")
            for index, group in enumerate(listed):
                group_origin = self.ctx.child(list_origin, index)
                if isinstance(group, dict):
                    groups.append(self._dialogue_group(group, group_origin, f"/list/{index}", len(groups)))
                else:
                    groups.append(self.value(group, group_origin, f"/list/{index}"))
                    self.ctx.diagnostic("DIALOGUE_GROUP_NOT_OBJECT", group_origin)
        children = self._members(value, origin, "", excluded)
        if not value:
            self.ctx.claim(value, "", "structure")
        if not groups:
            self.ctx.diagnostic("DIALOGUE_GROUPS_NOT_FOUND", origin)
        return self.ctx.block("dialogue", origin, groups=groups, children=children)

    def _dialogue_group(self, value: dict[str, Any], origin: str, path: str, ordering: int) -> dict[str, Any]:
        # Reuse the existing node/edge diagnostics per direct group. Keep group
        # discovery here so top-level and listed payloads are independently owned.
        artifact = self.ctx.artifact_for_origin(origin)
        raw_ref = RawRef(source=self.source_item["source"], locale=self.source_item["locale"],
                         run_id=self.raw_run_id, artifact_kind="details", artifact_path=artifact["path"],
                         artifact_sha256=artifact["sha256"], content_id=self.source_item["content_id"])
        direct = {key: child for key, child in value.items() if key != "list"}
        for diagnostic in parse_dialogue_graph(direct, component_ref=raw_ref).diagnostics:
            self.ctx.diagnostic(diagnostic.code, origin, source_path=diagnostic.path)
        nodes = []
        contents = value.get("contents")
        if isinstance(contents, dict):
            contents_origin = self.ctx.child(origin, "contents")
            if not contents:
                self.ctx.claim(contents, path + "/contents", "structure")
            for index, (node_id, payload) in enumerate(contents.items()):
                node_origin = self.ctx.child(contents_origin, node_id)
                node_path = path + "/contents/" + pointer_token(node_id)
                if isinstance(payload, dict):
                    members = self._members(payload, node_origin, node_path)
                    if not payload:
                        self.ctx.claim(payload, node_path, "structure")
                    nodes.append({"source_id": node_id, "ordering": index, "speaker": None,
                                  "origin_id": node_origin, "fields": members})
                else:
                    nodes.append({"source_id": node_id, "ordering": index,
                                  "value": self.value(payload, node_origin, node_path)})
            self.ctx.counts["dialogue_nodes"] += len(contents)
        edges = []
        child_ids = value.get("child_ids")
        if isinstance(child_ids, dict):
            edge_origin = self.ctx.child(origin, "child_ids")
            if not child_ids:
                self.ctx.claim(child_ids, path + "/child_ids", "structure")
            for parent_id, children in child_ids.items():
                parent_origin = self.ctx.child(edge_origin, parent_id)
                child_path = path + "/child_ids/" + pointer_token(parent_id)
                if isinstance(children, list):
                    self.ctx.claim(children, child_path, "structure")
                    for index, child in enumerate(children):
                        edges.append({"parent_id": parent_id, "child_id": str(child), "ordering": index,
                                      "origin_id": self.ctx.child(parent_origin, index)})
                else:
                    self.ctx.extensions.append({"kind": "dialogue_children", "value": self.value(children, parent_origin, child_path)})
            self.ctx.counts["dialogue_edges"] += len(edges)
        root = value.get("root_id")
        if "root_id" in value:
            self.ctx.claim(root, path + "/root_id", "structure")
        excluded = {key for key in ("contents", "child_ids") if isinstance(value.get(key), dict)} | {"root_id"}
        # Envelope fields outside these keys are owned by the surrounding block.
        extras = []
        for key in ("contents", "child_ids"):
            if key in value and key not in excluded:
                extras.append(self.value(value[key], self.ctx.child(origin, key), path + "/" + key))
        if path:
            extras.extend(self._members(value, origin, path, {"contents", "child_ids", "root_id"}))
        self.ctx.counts["dialogue_groups"] += 1
        return {"group_id": f"g{ordering}", "ordering": ordering, "origin_id": origin,
                "root_id": self._plain(root), "root_present": "root_id" in value,
                "nodes": nodes, "edges": edges, "children": extras}

    def _tag(self, namespace: str, key: str, value: str, origin: str, **details) -> None:
        for tag in self._tags:
            if (tag["namespace"], tag["key"], tag["value"], tag.get("channel_id")) == (namespace, key, value, details.get("channel_id")):
                if origin not in tag["evidence"]:
                    tag["evidence"].append(origin)
                return
        self._tags.append({"namespace": namespace, "key": key, "value": value,
                           "scope": "document", "evidence": [origin], **details})

    def _filters(self, extension: Any, origin: str, *, detail: bool) -> None:
        try:
            if detail:
                if not isinstance(extension, dict) or not extension.get("fe_ext"):
                    return
                extension = extension["fe_ext"]
                origin = self.ctx.child(origin, "fe_ext")
            if isinstance(extension, str):
                extension = source_json(extension)
                origin = self.ctx.derive(origin, {"op": "json_decode"})
            if not isinstance(extension, dict):
                raise ValueError("Filter extension is not an object")
            for channel_key, channel in extension.items():
                if not isinstance(channel, dict) or not isinstance(channel.get("filter"), dict):
                    continue
                text = channel["filter"].get("text")
                text_origin = self.ctx.child(self.ctx.child(self.ctx.child(origin, channel_key), "filter"), "text")
                if isinstance(text, str):
                    text = source_json(text)
                    text_origin = self.ctx.derive(text_origin, {"op": "json_decode"})
                if not isinstance(text, list):
                    raise ValueError("Assigned filter text is not an array")
                for index, raw in enumerate(text):
                    assigned_origin = self.ctx.child(text_origin, index)
                    if not isinstance(raw, str) or isinstance(raw, SourceNumber):
                        raise ValueError("Assigned filter value is not a string")
                    key, separator, val = raw.partition("/")
                    self._tag("source_filter", key if separator else "", val if separator else raw,
                              assigned_origin, observed=raw, channel_id=channel_key.removeprefix("c_"))
        except (ValueError, TypeError, KeyError) as error:
            self.ctx.diagnostic("FILTER_STRUCTURE_UNRECOGNIZED", origin, detail=str(error))

    def build(self, detail: dict[str, Any], *, discovery: dict[str, Any] | None = None) -> dict[str, Any]:
        self.add_artifact("detail", detail)
        envelope = self._source_artifacts["detail"]
        if not isinstance(envelope, dict) or ("retcode" in envelope and str(envelope["retcode"]) != "0"):
            raise OBCIntegrityError("RAW_ENVELOPE_INVALID")
        if not isinstance(envelope.get("data"), dict):
            raise OBCIntegrityError("RAW_DATA_NOT_OBJECT")
        page = envelope["data"].get("page")
        if not isinstance(page, dict) or str(page.get("id")) != self.source_item["content_id"]:
            raise OBCIntegrityError("RAW_PAGE_IDENTITY_MISMATCH")
        if not isinstance(page.get("modules"), list):
            raise OBCIntegrityError("RAW_MODULES_NOT_ARRAY")
        page_origin = self.source_origin("detail", "/data/page")
        memberships, listings = self._discovery(discovery or {})
        document = {
            "title": self.ctx.text(str(page.get("name", "")), self.ctx.child(page_origin, "name")) if "name" in page else None,
            "description": self.value(page["desc"], self.ctx.child(page_origin, "desc")) if "desc" in page else None,
            "source_fields": self._members(page, page_origin, "", {"name", "desc", "modules"}),
            "memberships": memberships, "listing_observations": listings,
            "tags": self._tags, "provenance": {"state": "unknown", "labels": []},
        }
        if "ext" in page:
            self._filters(page["ext"], self.ctx.child(page_origin, "ext"), detail=True)
        for tag in list(self._tags):
            if tag["namespace"] == "source_filter" and tag.get("channel_id") == "43" and tag["key"] == "任务类型" and "任务" in self._channel_names:
                self._tag("content_role", "task_kind", tag["value"], tag["evidence"][0], rule_id="obc-task-filter-1")
        by_key: dict[tuple[str, str], set[str]] = {}
        for tag in self._tags:
            if tag["namespace"] == "source_filter":
                by_key.setdefault((tag.get("channel_id", ""), tag["key"]), set()).add(tag["value"])
        for (channel_id, key), values in by_key.items():
            if len(values) > 1:
                self.ctx.diagnostic("FILTER_MULTIPLE_VALUES", page_origin, channel_id=channel_id, key=key)
        sections = []
        placements = _layout_observations(page.get("template_layout"))
        for index, module in enumerate(page["modules"]):
            module_origin = self.ctx.child(self.ctx.child(page_origin, "modules"), index)
            if not isinstance(module, dict):
                sections.append({"section_id": f"s{index}", "origin_id": module_origin,
                                 "blocks": [self.value(module, module_origin)], "tags": []})
                self.ctx.diagnostic("MODULE_NOT_OBJECT", module_origin)
                continue
            name = str(module.get("name", ""))
            tags = [{"namespace": "source_section", "key": "name", "value": name, "scope": f"s{index}",
                     "evidence": [self.ctx.child(module_origin, "name")]}] if "name" in module else []
            if "角色" in self._channel_names and re.fullmatch(r"角色故事[1-5]", name):
                tags.append({"namespace": "content_role", "key": "role", "value": "role_story", "scope": f"s{index}",
                             "rule_id": "obc-role-story-title-1", "evidence": [self.ctx.child(module_origin, "name")]})
            components = []
            source_components = module.get("components")
            if not isinstance(source_components, list):
                self.ctx.diagnostic("COMPONENTS_NOT_ARRAY", module_origin)
                source_components = []
            for component_index, component in enumerate(source_components):
                component_origin = self.ctx.child(self.ctx.child(module_origin, "components"), component_index)
                components.append(self._component(component, component_origin))
            sections.append({"section_id": f"s{index}", "ordering": index, "origin_id": module_origin,
                             "title": self.ctx.text(name, self.ctx.child(module_origin, "name")) if "name" in module else None,
                             "source_module_id": str(module.get("id", "")), "tags": tags,
                             "layout_observations": placements.get(str(module.get("id", "")), []),
                             "source_fields": self._members(module, module_origin, "", {"name", "components"} if isinstance(module.get("components"), list) else {"name"}),
                             "components": components})
        self.component_id = ""
        envelope_origin = self.source_origin("detail", "")
        self.ctx.extensions.extend(self._members(envelope, envelope_origin, "", {"retcode", "message", "data"}))
        data_origin = self.source_origin("detail", "/data")
        self.ctx.extensions.extend(self._members(envelope["data"], data_origin, "", {"page"}))
        if set(envelope) - {"retcode", "message", "data"} or set(envelope["data"]) - {"page"}:
            self.ctx.diagnostic("UNKNOWN_ENVELOPE_FIELDS", envelope_origin)
        fingerprint = record_fingerprint(detail, discovery or {})
        unknown = any(d["code"].startswith(("UNKNOWN_", "UNRECOGNIZED_", "COMPONENT_DATA_"))
                      or d["code"] in {"MODULE_NOT_OBJECT", "COMPONENTS_NOT_ARRAY", "FILTER_STRUCTURE_UNRECOGNIZED"}
                      for d in self.ctx.diagnostics)
        return {
            "schema_version": SCHEMA_VERSION,
            "observation_id": observation_id(self.source_item, self.raw_run_id, fingerprint),
            "source_item": self.source_item,
            "input": {"raw_run_id": self.raw_run_id, "dependency_fingerprint": fingerprint, **dependencies()},
            "artifacts": self.ctx.artifacts, "origins": self.ctx.origins, "document": document,
            "sections": sections, "relations": self.ctx.relations, "media": self.ctx.media,
            "extensions": self.ctx.extensions, "diagnostics": self.ctx.diagnostics,
            "coverage": {"status": "preserved_unknown" if unknown else "complete",
                         **dict(self.ctx.counts)},
        }

    def _component(self, component: Any, origin: str) -> dict[str, Any]:
        self.ctx.counts["components"] += 1
        if not isinstance(component, dict) or "data" not in component or "component_id" not in component:
            self.ctx.diagnostic("UNKNOWN_COMPONENT_CONTAINER", origin)
            return {"component_id": None, "origin_id": origin, "blocks": [self.value(component, origin)]}
        self.component_id = str(component.get("component_id", ""))
        if self.component_id not in KNOWN_COMPONENTS:
            self.ctx.diagnostic("UNKNOWN_COMPONENT_TYPE", origin, component_id=self.component_id)
        data_origin = self.ctx.child(origin, "data")
        raw = component.get("data")
        decoded = raw
        malformed = False
        if isinstance(raw, str):
            try:
                decoded = source_json(raw)
                data_origin = self.ctx.derive(data_origin, {"op": "json_decode"})
            except ValueError:
                malformed = True
                self.ctx.diagnostic("SOURCE_EMPTY_COMPONENT" if raw == "" else "COMPONENT_DATA_NOT_JSON", data_origin)
        self.ctx.begin_component()
        if malformed:
            self.ctx.claim(raw, "", "opaque")
            block = self.ctx.block("opaque", data_origin, value=raw, reason="source_empty" if raw == "" else "not_json")
        else:
            self._check_fields(decoded, data_origin)
            block = self.value(decoded, data_origin)
        self.ctx.end_component(decoded)
        tags = [{"namespace": "source_component", "key": "component_id", "value": self.component_id,
                 "scope": block["block_id"], "evidence": [self.ctx.child(origin, "component_id")]}]
        if self.component_id == "interactive_dialogue":
            tags.append({"namespace": "content_role", "key": "role", "value": "dialogue", "scope": block["block_id"],
                         "rule_id": "obc-dialogue-component-1", "evidence": [self.ctx.child(origin, "component_id")]})
        fields = self._members(component, origin, "", {"data", "component_id"})
        result = {"component_id": self.component_id, "origin_id": origin, "tags": tags,
                  "blocks": [block], "source_fields": fields}
        self.component_id = ""
        return result

    def _check_fields(self, value: Any, origin: str, path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = path + "/" + pointer_token(key)
                child_origin = self.ctx.child(origin, key)
                normalized = re.sub(r"(/(?:contents|child_ids))/[^/]+", r"\1/*", _pattern(child_path))
                source_type = (child.source_type if isinstance(child, SourceNumber) else
                               {str: "string", list: "array", dict: "object", bool: "boolean", type(None): "null"}[type(child)])
                if source_type not in FIELD_TYPES.get(normalized, ()):
                    self.ctx.diagnostic("UNKNOWN_COMPONENT_FIELD_SHAPE", child_origin,
                                        field_path=child_path, source_type=source_type)
                self._check_fields(child, child_origin, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self._check_fields(child, self.ctx.child(origin, index), path + "/" + str(index))

    def _discovery(self, discovery: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        from .models import pointer_get

        for name, artifact in discovery.get("artifacts", {}).items():
            self.add_artifact(name, artifact)
        memberships = []
        channels = discovery.get("channel_nodes", {})
        for membership in discovery.get("memberships", []):
            channel_id = str(membership["channel_id"])
            origin = self.source_origin(membership["artifact_name"], membership["pointer"])
            if str(pointer_get(self._source_artifacts[membership["artifact_name"]], membership["pointer"])) != channel_id:
                raise OBCIntegrityError("MEMBERSHIP_EVIDENCE_MISMATCH")
            nodes = []
            current = channel_id
            seen = set()
            while current in channels and current not in seen:
                seen.add(current)
                node = channels[current]
                node_origin = self.source_origin(node["artifact_name"], node["pointer"])
                actual = pointer_get(self._source_artifacts[node["artifact_name"]], node["pointer"])
                if (str(actual["id"]), actual["name"], str(actual["parent_id"])) != (current, node["name"], str(node["parent_id"])):
                    raise OBCIntegrityError("CHANNEL_EVIDENCE_MISMATCH")
                name_origin = self.ctx.child(node_origin, "name")
                name = str(node["name"])
                self._channel_names.add(name)
                nodes.insert(0, {"channel_id": current, "name": self.ctx.text(name, name_origin)})
                self._tag("source_channel", "channel", name, name_origin, channel_id=current)
                current = str(node.get("parent_id", ""))
            if current in seen:
                self.ctx.diagnostic("CHANNEL_TREE_CYCLE", origin)
            memberships.append({"channel_id": channel_id, "origin_id": origin, "path": nodes})
        listings = []
        for listing in discovery.get("listings", []):
            name, pointer = listing["artifact_name"], listing["pointer"]
            row = pointer_get(self._source_artifacts[name], pointer)
            origin = self.source_origin(name, pointer)
            if str(row.get("content_id")) != self.source_item["content_id"]:
                raise OBCIntegrityError("LISTING_CONTENT_IDENTITY_MISMATCH")
            listings.append({"channel_id": listing["channel_id"], "requested_channel_id": listing.get("requested_channel_id"),
                             "origin_id": origin, "fields": self._members(row, origin, "")})
            if "ext" in row:
                self._filters(row["ext"], self.ctx.child(origin, "ext"), detail=False)
        return memberships, listings


def clean_obc_detail(body: bytes, *, artifact_path: str, artifact_sha256: str,
                     locale: str, raw_run_id: str, content_id: str,
                     source: str = "mihoyo_obc", discovery: dict[str, Any] | None = None) -> dict[str, Any]:
    builder = OBCRecordBuilder(source=source, locale=locale, raw_run_id=raw_run_id, content_id=content_id)
    return builder.build({"body": body, "path": artifact_path, "sha256": artifact_sha256, "kind": "details"},
                         discovery=discovery)
