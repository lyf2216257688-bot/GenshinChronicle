"""Offline bounded source readback for a prepared input bundle."""
from collections import Counter

from ..clean import CleanRef, iter_blocks, validate_record
from ..clean.models import pointer_get
from ..evidence_io import require
from .inputs import iter_inputs
from .schema import project_chunk


def validate_readback(source, input_manifest):
    """Compare stored projections, source ranges and complete dialogue groups.

    Clean-to-Raw semantic fidelity remains an upstream acceptance responsibility.
    This check uses hash-verified Clean records and their existing locators.
    """
    counts = Counter()
    observations = set()
    parents = set()
    graphs = set()
    current_observation = None

    def check_text(ref, expected, counter):
        reference = CleanRef(**ref)
        require(reference.observation_id == record["observation_id"], "Readback observation mismatch")
        value = pointer_get(blocks[reference.block_id], reference.value_pointer)
        text = value.get("text", value.get("lexeme"))
        require(isinstance(text, str) and reference.char_end <= len(text)
                and text[reference.char_start:reference.char_end] == expected, "Source text readback mismatch")
        counts[counter] += 1

    for row in iter_inputs(input_manifest):
        chunk, parent = source.read_chunk(row), source.read_parent(row)
        expected = project_chunk(chunk, parent)
        require(all(row[key] == value for key, value in expected.items()), "Stored field projection mismatch")
        require(row["dense_text"] == chunk["retrieval_text"], "Dense input differs from retrieval_text")
        if current_observation != row["observation_id"]:
            record = source.read_clean(row)
            validate_record(record)
            blocks = {block["block_id"]: block for block in iter_blocks(record)}
            relations = {relation["relation_id"]: relation for relation in record["relations"]}
            current_observation = row["observation_id"]
            observations.add(current_observation)
        for part in chunk["parts"]:
            check_text(part["clean_ref"], part["text"], "body_refs")
            for ref in part.get("alias_clean_refs", []):
                check_text(ref, part["text"], "alias_refs")
            sources = (part.get("condition_sources", []) + part.get("source_annotations", [])
                       + part.get("upstream_context", []) + part.get("option_context", []))
            if part.get("article_title"):
                sources += [part["article_title"]]
            for context in sources:
                check_text(context["clean_ref"], context["text"], "part_context_refs")
        for context in chunk["context_sources"]:
            check_text(context["clean_ref"], context["text"], "delivered_context_refs")
        for context in parent["context"]["label_sources"]:
            check_text(context["clean_ref"], context["text"], "label_refs")
        for task in parent["context"]["task_context"]:
            for context in task["sources"]:
                check_text(context["clean_ref"], context["text"], "task_refs")
            require(all(relations.get(relation["relation_id"]) == relation for relation in task["source_relations"]),
                    "Task source relation mismatch")
        if row["parent_id"] not in parents:
            natural = source.read_natural_parent(row)
            require(natural is not None, "Natural Parent readback missing")
            parents.add(row["parent_id"])
            graph = source.read_graph(row)
            if graph is not None:
                require(graph["group"] == natural, "Original dialogue graph readback mismatch")
                graphs.add(row["parent_id"])
        counts["documents"] += 1
    source.verify_unchanged()
    return {"status": "PASS", "input_snapshot_id": source.input_snapshot_id,
            "clean_records_read": len(observations), "natural_parents_read": len(parents),
            "original_graphs_checked": len(graphs), "counts": dict(counts)}
