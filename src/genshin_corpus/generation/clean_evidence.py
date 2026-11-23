"""Hash-verified Clean reads and deterministic provider-neutral evidence Packets."""
from copy import deepcopy
from itertools import groupby
import json
from pathlib import Path

from ..clean import CleanRef, iter_blocks, iter_text_values, resolve_clean_ref
from ..evidence_io import digest, require
from ..retrieval.clean_based.core import covered_by

READ_VERSION = "gc-clean-read-receipt-1"
PACKET_VERSION = "gc-clean-evidence-packet-1"


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values(): yield from _walk(child)
    elif isinstance(value, list):
        for child in value: yield from _walk(child)


def _lineage(record, origins):
    pool, artifacts = {}, {}
    for item in origins:
        current = item.get("origin_id")
        seen = set()
        while current is not None:
            require(current not in seen and current in record["origins"], "Invalid Raw origin chain")
            seen.add(current); node = record["origins"][current]; pool[current] = deepcopy(node)
            if "artifact_id" in node:
                aid = node["artifact_id"]
                artifact = next(a for a in record["artifacts"] if a["artifact_id"] == aid)
                artifacts[aid] = deepcopy(artifact)
            current = node.get("parent_origin_id")
    return {"origins": pool, "artifacts": list(artifacts.values()),
            "raw_run_id": record["input"]["raw_run_id"], "raw_byte_revalidation": "not_performed"}


class EvidenceReader:
    """Only registered candidates authorize reads; no guessed paths/content IDs."""
    def __init__(self, reader, *, verified_parent_ranges=False):
        self.reader, self.handles, self.receipts = reader, {}, {}
        self.verified_parent_ranges = verified_parent_ranges

    def register(self, ranked):
        require(ranked["version"] in {"gc-clean-ranked-candidates-1", "gc-clean-ranked-candidates-2"}
                and ranked["status"] in {"complete", "empty"} and ranked["input_bundle_id"] == self.reader.bundle_id,
                "Foreign/incomplete ranked set")
        handles = {}
        for rank, candidate in enumerate(ranked["candidates"], 1):
            require(candidate["reranking"]["final_rank"] == rank, "Ranked group ordering changed")
            cid = candidate["chunk_id"]
            verified = self.reader.document(candidate["input_row"], cid)
            require(verified["row"] == candidate["source"] and verified["chunk"] == candidate["chunk"],
                    "Candidate source changed")
            handle = digest(["gc-source-handle-1", ranked["input_bundle_id"], cid, digest(verified["row"])])
            self.handles[handle] = deepcopy(candidate)
            handles[cid] = handle
        return handles

    def read(self, handle, scope="chunk"):
        require(handle in self.handles, "Unknown source handle; candidate discovery required")
        require(scope in {"chunk", "parent", "natural_parent", "graph"}, "Unsupported read scope")
        candidate = self.handles[handle]; row = candidate["source"]
        # Revalidate source/clean bytes at every actual read, not just registration.
        verified = self.reader.document(candidate["input_row"], candidate["chunk_id"])
        require(verified["row"] == row and verified["chunk"] == candidate["chunk"], "Source changed")
        record = self.reader.read_clean(row); chunk = verified["chunk"]
        parent = self.reader.read_parent(row)
        resolved = []
        blocks, locations, structure_positions = None, {}, []
        if self.verified_parent_ranges and scope != "chunk":
            blocks = {}
            for block in iter_blocks(record):
                block_id = block["block_id"]
                require(block_id not in blocks, "Duplicate Clean block identity")
                blocks[block_id] = block

        def resolve(ref, expected):
            clean_ref = CleanRef(**ref)
            if clean_ref in locations:
                position = locations[clean_ref]
                require(resolved[position]["text"] == expected, "Conflicting text at a Clean location")
                return position
            value = resolve_clean_ref(clean_ref, record=record, blocks=blocks)
            require(value["text"] == expected, "Clean original text differs")
            item = {"clean_ref": deepcopy(ref), **value}
            item["raw_lineage"] = _lineage(record, value["origins"])
            position = len(resolved)
            resolved.append(item)
            if blocks is not None:
                locations[clean_ref] = position
            return position

        for part in chunk["parts"]:
            resolve(part["clean_ref"], part["text"])
            for alias in part.get("alias_clean_refs", []): resolve(alias, part["text"])
        for item in _walk([chunk, parent["context"]]):
            if "clean_ref" in item and "text" in item: resolve(item["clean_ref"], item["text"])
        graph = None
        if scope == "chunk":
            structure = chunk
        elif scope == "parent":
            structure = parent
            if self.verified_parent_ranges:
                for unit in parent["units"]:
                    for part in unit["parts"]:
                        structure_positions.append(resolve(part["clean_ref"], part["text"]))
                        for alias in part.get("alias_clean_refs", []):
                            resolve(alias, part["text"])
                for item in _walk(parent["units"]):
                    if "clean_ref" in item and "text" in item:
                        resolve(item["clean_ref"], item["text"])
        else:
            natural = self.reader.read_natural_parent(row)
            graph = self.reader.read_graph(row)
            if graph is not None:
                require(graph["group"] == natural, "Dialogue graph differs from natural Parent")
            require(scope != "graph" or graph is not None, "No graph for this source handle")
            structure = graph if scope == "graph" else natural
            # Use original origin identities, never text matching, to map the
            # complete natural container back to exact block/value positions.
            allowed = {v["origin_id"] for v in _walk(natural) if "origin_id" in v}
            for block in blocks.values() if blocks is not None else iter_blocks(record):
                for value in iter_text_values(block):
                    if value["origin_id"] in allowed:
                        ref = {"observation_id": record["observation_id"], "block_id": block["block_id"],
                               "value_pointer": value["value_pointer"], "char_start": 0, "char_end": len(value["text"])}
                        position = resolve(ref, value["text"])
                        if self.verified_parent_ranges:
                            structure_positions.append(position)
        # Whole source blocks/containers are structural admission boundaries.
        # Long containers can be read in full; G2 never slices away qualifiers.
        fragment = {"scope": scope, "structure": deepcopy(structure), "source_context": deepcopy(row["source_context"]),
                    "parent_context": deepcopy(parent["context"]), "conditions": deepcopy(row["conditions"]),
                    "source_item": deepcopy(row["source_item"]), "observation_id": row["observation_id"],
                    "parent_id": row["parent_id"], "texts": resolved,
                    "order_semantics": "display_only", "authority": "preserved_source_status_not_verified_fact"}
        if self.verified_parent_ranges and scope != "chunk":
            fragment["structure_text_positions"] = structure_positions
        fragment["fragment_id"] = digest([handle, fragment])
        receipt = {"version": READ_VERSION, "handle": handle, "chunk_id": candidate["chunk_id"],
                   "input_bundle_id": self.reader.bundle_id, "scope": scope,
                   "source_snapshot": deepcopy(row), "fragments": [fragment],
                   "read_extent": "retrieval_chunk" if scope == "chunk" else "complete_" + scope,
                   "unread": "other_source_ranges_not_read" if scope == "chunk" else "other_natural_parents_not_read",
                   "source_validation": "PASS", "semantic_acceptance": "NOT_EVALUATED"}
        receipt["receipt_id"] = digest(receipt)
        self.receipts[receipt["receipt_id"]] = deepcopy(receipt)
        return receipt


def estimate_tokens(value):
    """Conservative UTF-8 byte estimate; not the provider's exact tokenizer."""
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8"))


def _source_view(value):
    """Omit locator machinery from model-visible data, retain the receipt intact."""
    if isinstance(value, dict):
        hidden = {"clean_ref", "alias_clean_refs", "origin_id", "segments", "block_id", "observation_id",
                  "parent_id", "chunk_id", "source_range", "clean_range", "value_pointer"}
        return {k: _source_view(v) for k, v in value.items() if k not in hidden}
    if isinstance(value, list): return [_source_view(v) for v in value]
    return value


def _share_equal_fields(parts, keys):
    first = parts[0]
    shared = {key: first[key] for key in keys if key in first
              and all(key in part and part[key] == first[key] for part in parts[1:])}
    return shared, [{k: v for k, v in part.items() if k not in shared} for part in parts]


def _compact_chunk_view(body):
    parts = body["parts"]
    if len(parts) < 2:
        return body
    # Keep literal body, speaker, branch membership and applicable conditions
    # inline. Repeated option/ancestor context can share an exact node range.
    common, projected = _share_equal_fields(parts, ("source_kind", "field_keys", "field_labels", "admission_reason",
        "source_component_id", "node_has_heading", "reachability", "node_id", "node_source_order", "node_display_order",
        "incoming_node_ids", "outgoing_node_ids", "choice_point", "source_fork"))
    factored = {**body, "parts": projected}
    if common:
        factored["shared_fields_for_all_parts"] = common
    ranges = []
    offset = 0
    for node_id, run in groupby(parts, key=lambda part: part.get("node_id")):
        count = len(list(run))
        end = offset + count
        if node_id and count > 1:
            shared, rows = _share_equal_fields(projected[offset:end], ("field_keys", "field_labels", "node_id",
                "incoming_node_ids", "outgoing_node_ids", "upstream_context", "option_context", "incoming_condition_sources"))
            if shared:
                ranges.append({"first_part": offset + 1, "last_part": end, "fields": shared})
                projected[offset:end] = rows
        offset = end
    if ranges:
        factored["shared_fields_by_part_range"] = ranges
    return factored if estimate_tokens(factored) < estimate_tokens(body) else body


def fragment_content(fragment, *, compact=False):
    structure = fragment["structure"]
    if fragment["scope"] == "chunk":
        body = {k: structure[k] for k in ("retrieval_text", "parts", "context_sources")}
    else:
        body = structure
    view = {"source_text_and_structure": _source_view(body), "source_context": _source_view(fragment["source_context"]),
            "parent_context": _source_view(fragment["parent_context"]), "conditions": _source_view(fragment["conditions"]),
            "order_semantics": "display_only", "authority": fragment["authority"]}
    if not compact:
        return view
    smaller = {**view, "source_item": _source_view(fragment["source_item"])}
    if fragment["parent_context"] == fragment["source_context"]:
        del smaller["parent_context"]
        smaller["parent_context_same_as_source_context"] = True
    if fragment["scope"] == "chunk":
        smaller["source_text_and_structure"] = _compact_chunk_view(view["source_text_and_structure"])
    return smaller if estimate_tokens(smaller) <= estimate_tokens(view) else view


def message_projection(context, evidence):
    return {**context, "evidence": [{"citation_id": e["citation_id"], "content": e["content"]} for e in evidence]}


EVIDENCE_QUALITY_ADMISSION = "evidence-quality-1"


def _priority_admission(groups, fragments, by_chunk, selected, preserved, priority, failed, context, budget, compact_content):
    """Choose what fits before publishing it in the original query/rank order."""
    candidates = {}
    for group in groups:
        for candidate in group["candidates"]:
            cid = candidate["chunk_id"]
            if cid in failed:
                continue
            for fid in by_chunk.get(cid, []):
                fragment, receipt = fragments[fid]
                require(receipt["input_bundle_id"] == group["input_bundle_id"]
                        and receipt["source_snapshot"] == candidate["source"], "Foreign receipt/candidate source")
                require(receipt["scope"] != "chunk" or fragment["structure"] == candidate["chunk"],
                        "Receipt Chunk changed")
                if fid in selected or receipt["scope"] == "chunk":
                    candidates.setdefault(fid, candidate)
    tiers = {"selected": [], "initial_admission_preserved": [], "requested_read": [], "ranked_read": []}
    for fid in candidates:
        tier = ("selected" if fid in selected else "initial_admission_preserved" if fid in preserved
                else "requested_read" if fid in priority else "ranked_read")
        tiers[tier].append(fid)
    decisions, admitted, admitted_chunks = {}, {}, []
    reserve = budget["output_tokens"] + budget["thinking_tokens"]
    used = estimate_tokens(message_projection(context, [])) + reserve
    for tier, ids in tiers.items():
        for fid in ids:
            fragment, receipt = fragments[fid]
            candidate = candidates[fid]
            decision = {"admission_tier": tier}
            decisions[fid] = decision
            if receipt["scope"] == "chunk":
                covering = next((other for other in admitted_chunks if covered_by(candidate["chunk"], other["chunk"])), None)
                if covering is not None:
                    decision.update(disposition="covered_duplicate", covered_by=covering["chunk_id"])
                    continue
            content = fragment_content(fragment, compact=compact_content)
            evidence = {"citation_id": f"E{len(admitted) + 1:02d}", "content": content}
            # JSON list brackets are already counted in the empty projection.
            added = estimate_tokens(evidence) + int(bool(admitted))
            if used + added > budget["context_tokens"]:
                decision["disposition"] = "budget_excluded"
                continue
            used += added
            admitted[fid] = content
            if receipt["scope"] == "chunk":
                admitted_chunks.append(candidate)
                decision["disposition"] = "included"
            else:
                decision["disposition"] = "context_expansion"
    return decisions, admitted


def assemble(groups, receipts, *, selection_ids=None, source_failures=(), context, budget,
             admission=None, priority_ids=(), exposed_ids=(), compact_content=False, preserve_ids=()):
    """Preserve group/rank order, verify selections, and account for every input."""
    require(set(budget) == {"context_tokens", "output_tokens", "thinking_tokens"}
            and all(type(v) is int and v >= 0 for v in budget.values()) and budget["context_tokens"] > 0,
            "Explicit finite whole-message budget required")
    require(set(context) == {"system", "question", "conversation", "tools", "protocol_history"},
            "Complete non-evidence message context required")
    fragments, by_chunk = {}, {}
    for receipt in receipts:
        require(receipt["version"] == READ_VERSION and receipt["source_validation"] == "PASS"
                and receipt["receipt_id"] == digest({k: v for k, v in receipt.items() if k != "receipt_id"}),
                "Read receipt changed/unverified")
        for fragment in receipt["fragments"]:
            fid = fragment["fragment_id"]
            require(fid == digest([receipt["handle"], {k: v for k, v in fragment.items() if k != "fragment_id"}]),
                    "Fragment changed")
            require(fid not in fragments or fragments[fid][0] == fragment, "Conflicting fragment identity")
            fragments[fid] = (fragment, receipt)
            by_chunk.setdefault(receipt["chunk_id"], []).append(fid)
    require(admission in {None, EVIDENCE_QUALITY_ADMISSION}, "Unknown evidence admission policy")
    quality = admission == EVIDENCE_QUALITY_ADMISSION
    selected = (set() if quality else set(fragments)) if selection_ids is None else set(selection_ids)
    failed = {entry["chunk_id"]: entry for entry in source_failures}
    require(len(selected) == len(selection_ids) if selection_ids is not None else True, "Repeated selection ID")
    require(selected <= set(fragments), "Selection must use delivered fragment IDs")
    visible, audit, included, seen = [], [], [], set()
    reserve = budget["output_tokens"] + budget["thinking_tokens"]
    require(estimate_tokens(message_projection(context, [])) + reserve <= budget["context_tokens"], "Non-evidence messages exceed budget")
    priority, exposed, preserved = set(priority_ids), set(exposed_ids), set(preserve_ids)
    require(not quality or (priority | preserved) <= set(fragments), "Priority must use verified fragment IDs")
    require(not preserved or (quality and compact_content), "Preservation requires compact quality admission")
    decisions, projected = _priority_admission(groups, fragments, by_chunk, selected, preserved, priority, failed,
                                              context, budget, compact_content) if quality else ({}, {})
    for group_index, ranked in enumerate(groups):
        require(ranked["version"] in {"gc-clean-ranked-candidates-1", "gc-clean-ranked-candidates-2"}
                and ranked["status"] in {"complete", "empty"}, "Incomplete ranked group")
        for rank, candidate in enumerate(ranked["candidates"], 1):
            require(candidate["reranking"]["final_rank"] == rank, "Ranked group ordering changed")
            cid = candidate["chunk_id"]
            record = {"group": group_index, "chunk_id": cid, "rank": candidate["reranking"]["final_rank"], "fragments": []}
            earlier = next((c for c in included if covered_by(candidate["chunk"], c["chunk"])), None)
            ids = [fid for fid in by_chunk.get(cid, []) if quality or fid in selected]
            for fid in ids:
                f, r = fragments[fid]
                require(r["input_bundle_id"] == ranked["input_bundle_id"] and r["source_snapshot"] == candidate["source"],
                        "Foreign receipt/candidate source")
                require(r["scope"] != "chunk" or f["structure"] == candidate["chunk"], "Receipt Chunk changed")
            if quality and ids and cid not in failed:
                for fid in ids:
                    detail = {"fragment_id": fid, "body_exposure": "shown" if fid in exposed else "not_shown"}
                    if fid in seen:
                        detail["disposition"] = "covered_duplicate"
                    else:
                        detail.update(decisions.get(fid, {"disposition": "not_selected"}))
                    if detail["disposition"] in {"included", "context_expansion"}:
                        _, receipt = fragments[fid]
                        visible.append({"citation_id": f"E{len(visible) + 1:02d}", "fragment_id": fid,
                                        "receipt_id": receipt["receipt_id"], "content": projected[fid]})
                        seen.add(fid)
                    record["fragments"].append(detail)
                dispositions = {v["disposition"] for v in record["fragments"]}
                record["disposition"] = next(value for value in
                    ("included", "budget_excluded", "not_selected", "covered_duplicate")
                    if value in dispositions or (value == "included" and "context_expansion" in dispositions))
            elif not ids or quality:
                record["disposition"] = "invalid_source" if cid in failed else "not_selected"
                record["reason"] = failed[cid] if cid in failed else ("not_selected" if cid in by_chunk else "not_read")
            elif earlier and all(fragments[fid][1]["scope"] == "chunk" for fid in ids):
                record.update(disposition="covered_duplicate", covered_by=earlier["chunk_id"])
            else:
                for fid in ids:
                    fragment, receipt = fragments[fid]
                    if fid in seen:
                        record["fragments"].append({"fragment_id": fid, "disposition": "covered_duplicate"})
                        continue
                    evidence = {"citation_id": "E" + str(len(visible) + 1).zfill(2), "fragment_id": fid,
                                "receipt_id": receipt["receipt_id"], "content": fragment_content(fragment, compact=compact_content)}
                    estimate = estimate_tokens(message_projection(context, visible + [evidence])) + reserve
                    if estimate <= budget["context_tokens"]:
                        visible.append(evidence); seen.add(fid)
                        disposition = "included" if receipt["scope"] == "chunk" else "context_expansion"
                    else: disposition = "budget_excluded"
                    record["fragments"].append({"fragment_id": fid, "disposition": disposition})
                admitted = any(v["disposition"] in {"included", "context_expansion"} for v in record["fragments"])
                record["disposition"] = "included" if admitted else (
                    "covered_duplicate" if all(v["disposition"] == "covered_duplicate" for v in record["fragments"]) else "budget_excluded")
                # Only a delivered complete Chunk can prove coverage of later chunks.
                if any(v["disposition"] == "included" for v in record["fragments"]): included.append(candidate)
            audit.append(record)
    require(selected <= {fid for row in audit for fid in by_chunk.get(row["chunk_id"], [])}, "Selection outside candidate groups")
    result = {"version": PACKET_VERSION, "status": "complete" if visible else "empty",
              "input_group_digests": [digest(g) for g in groups], "read_receipt_ids": [r["receipt_id"] for r in receipts],
              "selection_ids": sorted(selected), "policy": {"budget": budget, "estimator": "utf8_bytes_conservative_not_vendor_tokens",
              "structure_boundary": "whole_chunk_or_natural_container", "cross_group_order": "supplied_research_order"},
              "context_digest": digest(context), "evidence": visible, "dispositions": audit,
              "estimated_message_tokens": estimate_tokens(message_projection(context, visible)), "reserved_tokens": reserve,
              "semantic_acceptance": "NOT_EVALUATED"}
    if quality:
        result["policy"].update(admission=admission, priority_ids=sorted(priority))
    if compact_content:
        result["policy"]["content_projection"] = "shared_identical_context_and_chunk_fields_v1"
    if preserved:
        result["policy"]["preserved_initial_fragment_ids"] = sorted(preserved)
    result["packet_id"] = digest(result)
    result["citations"] = {e["citation_id"]: {"packet_id": result["packet_id"], "fragment_id": e["fragment_id"],
                                               "receipt_id": e["receipt_id"]} for e in visible}
    return result


def validate_citations(packet, citation_ids):
    require(packet["packet_id"] == digest({k: v for k, v in packet.items() if k not in {"packet_id", "citations"}}),
            "Packet changed")
    expected = {e["citation_id"]: {"packet_id": packet["packet_id"], "fragment_id": e["fragment_id"],
                                  "receipt_id": e["receipt_id"]} for e in packet["evidence"]}
    require(packet["citations"] == expected, "Citation mapping changed")
    require(set(citation_ids) <= set(packet["citations"]), "Citation not delivered in this Packet")
    return {"status": "PASS", "packet_id": packet["packet_id"], "semantic_support": "NOT_EVALUATED"}
