"""Offline source survey, bounded input export and input-bundle validation."""
import argparse
from collections import Counter
import json

from ..evidence_io import write
from . import ChunkInputSource, iter_inputs, prepare_inputs


def survey(source):
    counts = Counter()
    memberships = Counter()
    fields = Counter()
    characters = Counter()
    statuses = Counter()
    provenance = Counter()
    contents = set()
    for row in source.iter_documents():
        counts["documents"] += 1
        counts.update(row["granularities"])
        memberships[",".join(row["granularities"])] += 1
        contents.add(row["source_item"]["content_id"])
        provenance[row["source_context"]["source_provenance"]["state"]] += 1
        for field, values in row["lexical_fields"].items():
            fields[field] += bool(values)
            characters[field] += sum(map(len, values))
        statuses.update(condition["status"] for condition in row["conditions"])
        counts["body_refs"] += len(row["clean_refs"])
        counts["alias_refs"] += len(row["alias_clean_refs"])
        counts["context_sources"] += len(row["context_sources"])
        counts["graph_linked_documents"] += row["graph_locator"] is not None
    source.verify_unchanged()
    return {"status": "PASS", "input_snapshot_id": source.input_snapshot_id, "binding": source.binding,
            "projection_code_sha256": source.projection_code_sha256,
            "source_records": len(source.sources), "parents": len(source.parents), "graphs": len(source.graphs),
            "counts": dict(counts), "memberships": dict(memberships), "nonempty_field_documents": dict(fields),
            "field_characters": dict(characters), "part_condition_statuses": dict(statuses),
            "source_provenance": dict(provenance),
            "zero_candidate_content_ids": sorted({entry["content_id"] for entry in source.sources.values()} - contents)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("survey", "prepare"):
        command = commands.add_parser(name)
        command.add_argument("--chunk-manifest", required=True)
        command.add_argument("--clean-manifest", help="Relocated copy of the same hash-bound Clean manifest")
        command.add_argument("--output", required=True)
        if name == "prepare":
            selection = command.add_mutually_exclusive_group(required=True)
            selection.add_argument("--content-id", action="append")
            selection.add_argument("--all", action="store_true")
    validate = commands.add_parser("validate")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--source-readback", action="store_true")
    validate.add_argument("--output")
    args = parser.parse_args()
    if args.command == "validate":
        if args.source_readback:
            from .validation import validate_readback
            from ..evidence_io import read
            manifests = read(args.manifest)["source_manifests"]
            source = ChunkInputSource(manifests["chunk"], clean_manifest=manifests["clean"])
            result = validate_readback(source, args.manifest)
        else:
            result = {"status": "PASS", "documents": sum(1 for _ in iter_inputs(args.manifest))}
        if args.output:
            write(args.output, result)
    else:
        source = ChunkInputSource(args.chunk_manifest, clean_manifest=args.clean_manifest)
        if args.command == "survey":
            result = survey(source)
            write(args.output, result)
        else:
            result = prepare_inputs(source, args.output, content_ids=args.content_id)
    print(json.dumps({key: value for key, value in result.items() if key in
                     {"status", "counts", "documents", "input_snapshot_id", "input_bundle_id"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
