"""One-pass, read-only observations over an accepted Canonical run.

The profiler deliberately reports observations of one immutable Canonical run;
it does not create retrieval documents, alter Canonical data, or infer a
content-role taxonomy.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


CORPUS_PROFILE_VERSION = "phase04-corpus-profile-0.1"


class CanonicalProfileError(ValueError):
    """Raised when a Canonical manifest or record cannot support a trusted profile."""


def _read_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CanonicalProfileError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, Mapping):
        raise CanonicalProfileError(f"{label} must be a JSON object: {path}")
    return value


def _count_lengths(counter: Counter[int], value: str) -> None:
    counter[len(value)] += 1


def _summarize_lengths(counter: Counter[int]) -> dict[str, Any]:
    total = sum(counter.values())
    if not total:
        return {"count": 0, "minimum": None, "maximum": None, "mean": None, "percentiles": {}}
    ordered = sorted(counter.items())

    def percentile(fraction: float) -> int:
        target = max(1, int((total * fraction) + 0.999999999))
        seen = 0
        for length, count in ordered:
            seen += count
            if seen >= target:
                return length
        return ordered[-1][0]

    return {
        "count": total,
        "minimum": ordered[0][0],
        "maximum": ordered[-1][0],
        "mean": round(sum(length * count for length, count in ordered) / total, 3),
        "percentiles": {"p50": percentile(0.50), "p90": percentile(0.90), "p99": percentile(0.99)},
    }


def _top(counter: Counter[str] | Counter[int], limit: int) -> list[dict[str, Any]]:
    return [
        {"value": value, "count": count}
        for value, count in sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))[:limit]
    ]


def _lineage_stats(item: Mapping[str, Any], counters: Counter[str]) -> None:
    lineage = item.get("lineage")
    if not isinstance(lineage, Mapping):
        counters["missing_lineage"] += 1
        return
    pointer = lineage.get("parsed_json_pointer")
    counters["parsed_pointer_present" if isinstance(pointer, str) and pointer else "parsed_pointer_empty"] += 1
    scope = lineage.get("evidence_scope")
    counters[f"evidence_scope:{scope}" if isinstance(scope, str) else "evidence_scope_missing"] += 1
    raw_refs = lineage.get("raw_refs")
    if isinstance(raw_refs, list):
        counters["raw_ref_available" if raw_refs else "raw_ref_empty"] += 1
        counters["raw_ref_total"] += len(raw_refs)
    else:
        counters["raw_ref_missing"] += 1


def _iter_dialogue_groups(value: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    groups = value.get("groups")
    if not isinstance(groups, list):
        return ()
    return (group for group in groups if isinstance(group, Mapping))


def _template_layout_labels(value: Any) -> Iterable[tuple[str, str]]:
    """Yield observed OBC template labels without interpreting them as roles."""

    if not isinstance(value, Mapping):
        return ()
    labels: list[tuple[str, str]] = []
    tabs = value.get("tab")
    if not isinstance(tabs, list):
        return ()
    for tab in tabs:
        if not isinstance(tab, Mapping):
            continue
        name = tab.get("tab_name")
        if isinstance(name, str):
            labels.append(("template_tab_name", name))
        groups = tab.get("module_group")
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, Mapping):
                continue
            name = group.get("name")
            if isinstance(name, str):
                labels.append(("template_module_group_name", name))
            layout = group.get("layout")
            if isinstance(layout, str):
                labels.append(("template_module_group_layout", layout))
    return labels


def _validate_manifest(manifest: Mapping[str, Any], path: Path) -> list[Mapping[str, Any]]:
    if manifest.get("status") != "complete":
        raise CanonicalProfileError("Canonical manifest must be complete")
    records = manifest.get("records")
    if not isinstance(records, list):
        raise CanonicalProfileError("Canonical manifest records must be a list")
    expected = manifest.get("input_record_count")
    accounted = manifest.get("accounted_record_count")
    if expected != len(records) or accounted != len(records):
        raise CanonicalProfileError("Canonical manifest accounting does not match record entries")
    if manifest.get("input_integrity_failure_count") != 0:
        raise CanonicalProfileError("Canonical manifest has input-integrity failures")
    return records


def profile_canonical_run(manifest_path: Path, *, top_n: int = 30) -> dict[str, Any]:
    """Profile an accepted Canonical run in one manifest-ordered pass.

    Each manifest record is opened once, SHA-256 checked, decoded once, and
    then discarded. The returned object contains aggregates only.
    """

    if top_n < 1:
        raise ValueError("top_n must be positive")
    manifest_path = Path(manifest_path)
    manifest = _read_object(manifest_path, "Canonical manifest")
    entries = _validate_manifest(manifest, manifest_path)

    counts: Counter[str] = Counter({
        "records": 0,
        "verified_record_sha256": 0,
        "sections": 0,
        "component_contexts": 0,
        "units": 0,
        "zero_context_sections": 0,
        "zero_unit_contexts": 0,
        "unsupported_units": 0,
        "structured_decoded_mapping": 0,
        "structured_decoded_list": 0,
        "structured_decoded_null": 0,
        "structured_decoded_scalar": 0,
        "dialogue_graphs": 0,
        "dialogue_groups": 0,
        "dialogue_groups_with_diagnostics": 0,
        "dialogue_nodes": 0,
        "dialogue_nodes_with_option": 0,
        "dialogue_nodes_with_dialogue": 0,
        "dialogue_edges": 0,
    })
    unit_kinds: Counter[str] = Counter()
    component_ids: Counter[str] = Counter()
    section_names: Counter[str] = Counter()
    template_tabs: Counter[str] = Counter()
    template_module_groups: Counter[str] = Counter()
    template_module_group_layouts: Counter[str] = Counter()
    channel_memberships: Counter[str] = Counter()
    diagnostic_codes: Counter[str] = Counter()
    context_diagnostic_codes: Counter[str] = Counter()
    lineage: Counter[str] = Counter()
    record_statuses: Counter[str] = Counter()
    section_context_counts: Counter[int] = Counter()
    section_unit_counts: Counter[int] = Counter()
    context_unit_counts: Counter[int] = Counter()
    rich_lengths: Counter[int] = Counter()
    rich_text_values: Counter[str] = Counter()
    dialogue_text_values: Counter[str] = Counter()
    component_section_names: dict[str, set[str]] = defaultdict(set)
    component_unit_kinds: dict[str, set[str]] = defaultdict(set)
    component_record_ids: dict[str, set[str]] = defaultdict(set)

    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise CanonicalProfileError(f"manifest record {index} is not an object")
        path_value = entry.get("canonical_record_path")
        expected_sha = entry.get("canonical_record_sha256")
        if not isinstance(path_value, str) or not isinstance(expected_sha, str):
            raise CanonicalProfileError(f"manifest record {index} lacks path or SHA-256")
        record_path = Path(path_value)
        try:
            body = record_path.read_bytes()
        except OSError as exc:
            raise CanonicalProfileError(f"cannot read Canonical record {index}: {record_path}") from exc
        actual_sha = hashlib.sha256(body).hexdigest()
        if actual_sha != expected_sha:
            raise CanonicalProfileError(f"Canonical record SHA-256 mismatch: {record_path}")
        try:
            record = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CanonicalProfileError(f"Canonical record is not valid UTF-8 JSON: {record_path}") from exc
        if not isinstance(record, Mapping) or record.get("record_id") != entry.get("record_id"):
            raise CanonicalProfileError(f"Canonical record identity mismatch: {record_path}")

        counts["records"] += 1
        counts["verified_record_sha256"] += 1
        status = record.get("status")
        record_statuses[str(status)] += 1
        _lineage_stats(record, lineage)
        metadata = record.get("record_metadata")
        if isinstance(metadata, Mapping):
            memberships = metadata.get("channel_memberships")
            if isinstance(memberships, list):
                channel_memberships.update(str(value) for value in memberships)
            for label_kind, label in _template_layout_labels(metadata.get("source_template_layout")):
                if label_kind == "template_tab_name":
                    template_tabs[label] += 1
                elif label_kind == "template_module_group_name":
                    template_module_groups[label] += 1
                else:
                    template_module_group_layouts[label] += 1

        sections = record.get("sections")
        if not isinstance(sections, list):
            raise CanonicalProfileError(f"Canonical record sections must be a list: {record_path}")
        for section in sections:
            if not isinstance(section, Mapping):
                raise CanonicalProfileError(f"Canonical section must be an object: {record_path}")
            counts["sections"] += 1
            _lineage_stats(section, lineage)
            source_metadata = section.get("source_metadata")
            section_name = ""
            if isinstance(source_metadata, Mapping) and isinstance(source_metadata.get("name"), str):
                section_name = source_metadata["name"]
            section_names[section_name] += 1

            contexts = section.get("component_contexts")
            units = section.get("units")
            if not isinstance(contexts, list) or not isinstance(units, list):
                raise CanonicalProfileError(f"Canonical section members must be lists: {record_path}")
            section_context_counts[len(contexts)] += 1
            section_unit_counts[len(units)] += 1
            contexts_by_key: dict[Any, Mapping[str, Any]] = {}
            if not contexts:
                counts["zero_context_sections"] += 1
            for context in contexts:
                if not isinstance(context, Mapping):
                    raise CanonicalProfileError(f"Canonical context must be an object: {record_path}")
                counts["component_contexts"] += 1
                _lineage_stats(context, lineage)
                contexts_by_key[context.get("observation_key")] = context
                component_id = str(context.get("source_component_id"))
                component_ids[component_id] += 1
                component_section_names[component_id].add(section_name)
                component_record_ids[component_id].add(str(record.get("record_id")))
                unit_count = context.get("unit_count")
                if not isinstance(unit_count, int) or unit_count < 0:
                    raise CanonicalProfileError(f"Canonical context has invalid unit_count: {record_path}")
                context_unit_counts[unit_count] += 1
                if unit_count == 0:
                    counts["zero_unit_contexts"] += 1
                diagnostics = context.get("diagnostics")
                if isinstance(diagnostics, list):
                    for diagnostic in diagnostics:
                        if isinstance(diagnostic, Mapping) and isinstance(diagnostic.get("code"), str):
                            context_diagnostic_codes[diagnostic["code"]] += 1

            for unit in units:
                if not isinstance(unit, Mapping):
                    raise CanonicalProfileError(f"Canonical unit must be an object: {record_path}")
                counts["units"] += 1
                _lineage_stats(unit, lineage)
                kind = str(unit.get("kind"))
                unit_kinds[kind] += 1
                if kind == "unsupported":
                    counts["unsupported_units"] += 1
                parent_key = unit.get("parent_component_key")
                matching_context = contexts_by_key.get(parent_key)
                if isinstance(matching_context, Mapping):
                    component_id = str(matching_context.get("source_component_id"))
                    component_unit_kinds[component_id].add(kind)
                diagnostics = unit.get("diagnostics")
                if isinstance(diagnostics, list):
                    for diagnostic in diagnostics:
                        if isinstance(diagnostic, Mapping) and isinstance(diagnostic.get("code"), str):
                            diagnostic_codes[diagnostic["code"]] += 1
                value = unit.get("value")
                if kind == "rich_text" and isinstance(value, Mapping):
                    text = value.get("normalized_text")
                    if isinstance(text, str):
                        _count_lengths(rich_lengths, text)
                        if text:
                            rich_text_values[text] += 1
                elif kind == "structured_observation" and isinstance(value, Mapping):
                    decoded = value.get("decoded")
                    if isinstance(decoded, Mapping):
                        counts["structured_decoded_mapping"] += 1
                    elif isinstance(decoded, list):
                        counts["structured_decoded_list"] += 1
                    elif decoded is None:
                        counts["structured_decoded_null"] += 1
                    else:
                        counts["structured_decoded_scalar"] += 1
                elif kind == "dialogue_graph" and isinstance(value, Mapping):
                    counts["dialogue_graphs"] += 1
                    for group in _iter_dialogue_groups(value):
                        counts["dialogue_groups"] += 1
                        group_diagnostics = group.get("diagnostics")
                        if isinstance(group_diagnostics, list):
                            counts["dialogue_groups_with_diagnostics"] += bool(group_diagnostics)
                        nodes = group.get("nodes")
                        if isinstance(nodes, list):
                            for node in nodes:
                                if not isinstance(node, Mapping):
                                    continue
                                counts["dialogue_nodes"] += 1
                                option = node.get("option")
                                dialogue = node.get("dialogue")
                                counts["dialogue_nodes_with_option"] += int(isinstance(option, str) and bool(option.strip()))
                                counts["dialogue_nodes_with_dialogue"] += int(isinstance(dialogue, str) and bool(dialogue.strip()))
                                if isinstance(dialogue, str) and dialogue.strip():
                                    dialogue_text_values[dialogue] += 1
                        edges = group.get("edges")
                        if isinstance(edges, list):
                            counts["dialogue_edges"] += len(edges)

    duplicate_rich_groups = [count for count in rich_text_values.values() if count > 1]
    duplicate_dialogue_groups = [count for count in dialogue_text_values.values() if count > 1]
    component_variation = [
        {
            "component_id": component_id,
            "record_count": len(component_record_ids[component_id]),
            "distinct_section_name_count": len(component_section_names[component_id]),
            "distinct_unit_kind_count": len(component_unit_kinds[component_id]),
            "unit_kinds": sorted(component_unit_kinds[component_id]),
        }
        for component_id in component_ids
    ]
    component_variation.sort(key=lambda item: (-item["distinct_section_name_count"], -item["record_count"], item["component_id"]))

    if counts["records"] != manifest.get("accounted_record_count"):
        raise CanonicalProfileError("profiled record count does not reconcile with Canonical manifest")
    return {
        "profile_version": CORPUS_PROFILE_VERSION,
        "observation_scope": {
            "canonical_manifest_path": str(manifest_path),
            "canonical_run_id": manifest.get("canonical_run_id"),
            "source": manifest.get("source"),
            "locale": manifest.get("locale"),
            "manifest_status": manifest.get("status"),
            "manifest_input_record_count": manifest.get("input_record_count"),
            "manifest_accounted_record_count": manifest.get("accounted_record_count"),
            "note": "All statistics are observations of this Canonical run, not permanent schema or semantic contracts.",
        },
        "accounting": dict(sorted(counts.items())),
        "record_statuses": dict(sorted(record_statuses.items())),
        "unit_kinds": dict(sorted(unit_kinds.items())),
        "structural_cardinality": {
            "section_context_count_distribution": _top(section_context_counts, top_n),
            "section_unit_count_distribution": _top(section_unit_counts, top_n),
            "context_unit_count_distribution": _top(context_unit_counts, top_n),
        },
        "searchable_views": {
            "rich_text_normalized_length": _summarize_lengths(rich_lengths),
            "rich_text_exact_repeat": {
                "distinct_nonempty_text_count": len(rich_text_values),
                "repeated_text_group_count": len(duplicate_rich_groups),
                "repeated_occurrence_count": sum(duplicate_rich_groups),
                "note": "Text equality is counted only as an occurrence statistic and never defines evidence identity.",
            },
            "dialogue_exact_repeat": {
                "distinct_nonempty_dialogue_count": len(dialogue_text_values),
                "repeated_text_group_count": len(duplicate_dialogue_groups),
                "repeated_occurrence_count": sum(duplicate_dialogue_groups),
            },
        },
        "lineage": dict(sorted(lineage.items())),
        "diagnostics": {
            "unit": _top(diagnostic_codes, top_n),
            "component_context": _top(context_diagnostic_codes, top_n),
        },
        "top_observed_structure": {
            "component_ids": {"distinct_count": len(component_ids), "top": _top(component_ids, top_n)},
            "section_names": {"distinct_count": len(section_names), "top": _top(section_names, top_n)},
            "channel_memberships": {"distinct_count": len(channel_memberships), "top": _top(channel_memberships, top_n)},
            "template_tab_names": {"distinct_count": len(template_tabs), "top": _top(template_tabs, top_n)},
            "template_module_group_names": {"distinct_count": len(template_module_groups), "top": _top(template_module_groups, top_n)},
            "template_module_group_layouts": {"distinct_count": len(template_module_group_layouts), "top": _top(template_module_group_layouts, top_n)},
            "component_variation": component_variation[:top_n],
        },
    }


def write_profile(profile: Mapping[str, Any], output_path: Path) -> None:
    """Write an explicitly requested aggregate report; never touch Canonical data."""

    Path(output_path).write_text(
        json.dumps(profile, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
