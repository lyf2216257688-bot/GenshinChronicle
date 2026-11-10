"""Versioned text projections and independent representation identities."""
from copy import deepcopy
from hashlib import sha256

from ..clean import CleanRef
from ..evidence_io import digest, require

INPUT_VERSION = "gc-index-input-1"
FIELD_VERSION = "gc-index-fields-1"
LEXICAL_FIELDS = ("title", "headings", "field_labels", "body", "context", "conditions")
GRANULARITIES = ("micro", "macro")


def _unique_text(values):
    return list(dict.fromkeys(value for value in values if value))


def project_chunk(chunk, parent):
    """Project existing roles without parsing display text or changing evidence."""
    require(chunk["schema_version"] == "gc-chunking-pilot-2", "Unsupported Chunk schema")
    for key in ("parent_id", "observation_id", "content_id", "section_id", "component_id"):
        require(chunk[key] == parent[key], "Chunk/Parent identity mismatch: " + key)
    levels = chunk["granularities"]
    require(bool(levels) and len(set(levels)) == len(levels)
            and set(levels) <= set(GRANULARITIES), "Invalid granularity membership")
    require(set(chunk["display_order_by_granularity"]) == set(levels), "Missing per-route order")
    require(chunk["order_semantics"] == "display_only", "Unsupported order semantics")
    require(all(type(order) is int and order >= 0
                for order in chunk["display_order_by_granularity"].values()), "Invalid display order")
    parts = chunk["parts"]
    require(bool(parts), "Chunk has no source parts")
    require(chunk["body_text"] == "\n".join(part["text"] for part in parts), "Body/parts mismatch")
    require(chunk["clean_refs"] == [part["clean_ref"] for part in parts], "Body reference mismatch")
    require(chunk["alias_clean_refs"] == [ref for part in parts for ref in part.get("alias_clean_refs", [])],
            "Alias reference mismatch")
    require(isinstance(chunk["retrieval_text"], str) and bool(chunk["retrieval_text"]), "Empty Dense input")
    require(chunk["context"] == parent["context"], "Chunk/Parent context mismatch")
    conditions = []
    for index, part in enumerate(parts):
        if "condition_status" in part:
            require(part["condition_status"] in {"source_marked", "not_established", "unresolved"},
                    "Unsupported condition status")
            conditions.append({"part_index": index, "status": part["condition_status"],
                               "sources": deepcopy(part["condition_sources"])})
        sources = (part.get("condition_sources", []) + part.get("source_annotations", [])
                   + part.get("upstream_context", []) + part.get("option_context", []))
        refs = [part["clean_ref"], *part.get("alias_clean_refs", [])]
        refs += [source["clean_ref"] for source in sources]
        if part.get("article_title"):
            refs.append(part["article_title"]["clean_ref"])
        for ref in refs:
            clean_ref = CleanRef(**ref)
            require(clean_ref.observation_id == chunk["observation_id"], "Cross-observation CleanRef")
    context_sources = chunk["context_sources"]
    for source in context_sources:
        require(source["role"] in {"来源语境", "文章标题", "来源条件"}, "Unsupported context role")
        require(CleanRef(**source["clean_ref"]).observation_id == chunk["observation_id"],
                "Cross-observation context")
    title = chunk["context"]["label_evidence"]["document_title"]
    condition_sources = {}
    for condition in conditions:
        for source in condition["sources"]:
            condition_sources.setdefault(digest(source["clean_ref"]), source["text"])
    fields = {
        "title": [title["text"]] if title and title["text"] else [],
        "headings": _unique_text(parent["labels"][1:] + [part["article_title"]["text"]
                                 for part in parts if part.get("article_title")]),
        "field_labels": _unique_text(label for part in parts for label in part["field_labels"]),
        "body": [chunk["body_text"]],
        "context": [source["text"] for source in context_sources if source["role"] == "来源语境"],
        "conditions": list(condition_sources.values()),
    }
    return {
        "schema_version": INPUT_VERSION, "field_version": FIELD_VERSION,
        "chunk_id": chunk["chunk_id"], "parent_id": chunk["parent_id"],
        "observation_id": chunk["observation_id"], "granularities": list(levels),
        "display_order_by_granularity": dict(chunk["display_order_by_granularity"]),
        "lexical_fields": fields, "dense_text": chunk["retrieval_text"],
        "dense_text_sha256": sha256(chunk["retrieval_text"].encode("utf-8")).hexdigest(),
        "clean_refs": deepcopy(chunk["clean_refs"]), "alias_clean_refs": deepcopy(chunk["alias_clean_refs"]),
        "context_sources": deepcopy(context_sources), "conditions": conditions,
        "source_context": deepcopy(chunk["context"]),
        "structure": {key: deepcopy(chunk[key]) for key in
                      ("section_id", "component_id", "article_scope_ids", "local_scope_ids",
                       "overlapping_chunk_ids", "order_semantics")},
    }


def representation_id(input_bundle_id, granularity, kind, configuration):
    """Bind explicit operating choices. Execution must verify them against artifacts."""
    require(granularity in GRANULARITIES, "Unsupported index granularity")
    required = {
        "dense": {"model", "revision", "tokenizer_sha256", "dimensions", "pooling", "normalization",
                  "document_prefix", "query_prefix", "max_tokens"},
        "bm25f": {"field_weights", "field_b", "k1", "analyzers"},
    }
    require(kind in required, "Unsupported representation kind")
    require(required[kind] <= configuration.keys(), "Incomplete representation configuration")
    if kind == "dense":
        require(type(configuration["dimensions"]) is int and configuration["dimensions"] > 0,
                "Invalid Dense dimensions")
        require(type(configuration["max_tokens"]) is int and configuration["max_tokens"] > 0,
                "Invalid Dense token cap")
        require(all(isinstance(configuration[key], str) and configuration[key]
                    for key in ("model", "revision", "tokenizer_sha256", "pooling", "normalization")),
                "Dense vector-space binding is incomplete")
    else:
        weights, lengths = configuration["field_weights"], configuration["field_b"]
        require(bool(weights) and set(weights) <= set(LEXICAL_FIELDS) and set(weights) == set(lengths),
                "Invalid BM25F fields")
        require(all(isinstance(value, (int, float)) and value > 0 for value in weights.values())
                and all(isinstance(value, (int, float)) and 0 <= value <= 1 for value in lengths.values())
                and configuration["k1"] > 0, "Invalid BM25F parameters")
        from .analyzers import validate_analyzers
        validate_analyzers(configuration["analyzers"])
    require(isinstance(input_bundle_id, str) and bool(input_bundle_id), "Missing input bundle identity")
    return digest({"schema_version": INPUT_VERSION, "input_bundle_id": input_bundle_id,
                   "granularity": granularity, "kind": kind, "configuration": configuration})
