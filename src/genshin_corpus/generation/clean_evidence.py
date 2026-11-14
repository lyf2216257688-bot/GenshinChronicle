"""Hash-verified Clean reads and deterministic provider-neutral evidence Packets."""
from copy import deepcopy
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
    def __init__(self, reader):
        self.reader, self.handles, self.receipts = reader, {}, {}

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

        def resolve(ref, expected):
            value = resolve_clean_ref(CleanRef(**ref), record=record)
            require(value["text"] == expected, "Clean original text differs")
            item = {"clean_ref": deepcopy(ref), **value}
            item["raw_lineage"] = _lineage(record, value["origins"])
            resolved.append(item)
            return item

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
            for block in iter_blocks(record):
                for value in iter_text_values(block):
                    if value["origin_id"] in allowed:
                        ref = {"observation_id": record["observation_id"], "block_id": block["block_id"],
                               "value_pointer": value["value_pointer"], "char_start": 0, "char_end": len(value["text"])}
                        resolve(ref, value["text"])
        # Whole source blocks/containers are structural admission boundaries.
        # Long containers can be read in full; G2 never slices away qualifiers.
        fragment = {"scope": scope, "structure": deepcopy(structure), "source_context": deepcopy(row["source_context"]),
                    "parent_context": deepcopy(parent["context"]), "conditions": deepcopy(row["conditions"]),
                    "source_item": deepcopy(row["source_item"]), "observation_id": row["observation_id"],
                    "parent_id": row["parent_id"], "texts": resolved,
                    "order_semantics": "display_only", "authority": "preserved_source_status_not_verified_fact"}
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


def fragment_content(fragment):
    structure = fragment["structure"]
    if fragment["scope"] == "chunk":
        body = {k: structure[k] for k in ("retrieval_text", "parts", "context_sources")}
    else:
        body = structure
    return {"source_text_and_structure": _source_view(body), "source_context": _source_view(fragment["source_context"]),
            "parent_context": _source_view(fragment["parent_context"]), "conditions": _source_view(fragment["conditions"]),
            "order_semantics": "display_only", "authority": fragment["authority"]}


def message_projection(context, evidence):
    return {**context, "evidence": [{"citation_id": e["citation_id"], "content": e["content"]} for e in evidence]}


def assemble(groups, receipts, *, selection_ids=None, source_failures=(), context, budget):
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
    selected = set(fragments) if selection_ids is None else set(selection_ids)
    failed = {entry["chunk_id"]: entry for entry in source_failures}
    require(len(selected) == len(selection_ids) if selection_ids is not None else True, "Repeated selection ID")
    require(selected <= set(fragments), "Selection must use delivered fragment IDs")
    visible, audit, included, seen = [], [], [], set()
    reserve = budget["output_tokens"] + budget["thinking_tokens"]
    require(estimate_tokens(message_projection(context, [])) + reserve <= budget["context_tokens"], "Non-evidence messages exceed budget")
    for group_index, ranked in enumerate(groups):
        require(ranked["version"] in {"gc-clean-ranked-candidates-1", "gc-clean-ranked-candidates-2"}
                and ranked["status"] in {"complete", "empty"}, "Incomplete ranked group")
        for rank, candidate in enumerate(ranked["candidates"], 1):
            require(candidate["reranking"]["final_rank"] == rank, "Ranked group ordering changed")
            cid = candidate["chunk_id"]
            record = {"group": group_index, "chunk_id": cid, "rank": candidate["reranking"]["final_rank"], "fragments": []}
            earlier = next((c for c in included if covered_by(candidate["chunk"], c["chunk"])), None)
            ids = [fid for fid in by_chunk.get(cid, []) if fid in selected]
            for fid in ids:
                f, r = fragments[fid]
                require(r["input_bundle_id"] == ranked["input_bundle_id"] and r["source_snapshot"] == candidate["source"],
                        "Foreign receipt/candidate source")
                require(r["scope"] != "chunk" or f["structure"] == candidate["chunk"], "Receipt Chunk changed")
            if not ids:
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
                                "receipt_id": receipt["receipt_id"], "content": fragment_content(fragment)}
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
