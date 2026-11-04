"""Replay frozen 70Q Assembly inputs and export three retained-evidence ablations."""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.generation.generation import project_generation_request, generation_messages, validate_citations
from genshin_corpus.retrieval import evidence_assembly as assembly
from genshin_corpus.retrieval import evidence_compaction, evidence_projection
from genshin_corpus.retrieval.evidence_compaction import VARIANTS, assemble_retained_evidence_packet
from genshin_corpus.retrieval.local_qwen3_build import offline_only
from genshin_corpus.retrieval.retrieval_units import load_retrieval_units


PROFILES = ("local_qwen3_1024", "online_qwen_2048")
DEFAULT_SOURCE = Path("data/retrieval/p04-embedding-70q-20261103-r3")


def artifact(path: Path) -> dict:
    with path.open("rb") as handle:
        digest = sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(path.resolve()), "sha256": digest.hexdigest()}


def write_json(path: Path, value: object) -> None:
    atomic_write(path, canonical_json_bytes(value) + b"\n")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    atomic_write(path, b"".join(canonical_json_bytes(row) + b"\n" for row in rows))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _case(source: Path, profile: str, qid: str, expected_hash: str) -> dict:
    path = source / profile / qid / "rag_result.json"
    if artifact(path)["sha256"] != expected_hash:
        raise ValueError(f"{profile}/{qid}: frozen result hash mismatch")
    result = json.loads(path.read_bytes())
    control = result["evidence_packet"]
    packet_path = path.parent / "packet/evidence_packet.json"
    if (result["status"] != "succeeded" or result["execution_mode"] != "evidence_only"
            or result["audit"]["embedding_profile"] != profile
            or result["query"]["request_label"] != qid
            or result["telemetry"]["provider_call_counts"]["generation"] != 0
            or control != json.loads(packet_path.read_bytes())):
        raise ValueError(f"{profile}/{qid}: frozen result contract mismatch")
    candidates = [row["candidate"] for row in control["admission_contract"]["candidate_observations"]]
    if candidates != result["rerank_trace"]["after"][:20] or len(candidates) != 20:
        raise ValueError(f"{profile}/{qid}: frozen Top20 mismatch")
    return {"question_id": qid, "profile": profile, "question": result["query"]["question_text"],
            "control": control, "candidates": candidates,
            "inputs": {"result": artifact(path), "packet": artifact(packet_path)}}


def _visible(packet: dict, question: str) -> list[dict]:
    return [row.to_dict() for row in project_generation_request(packet, question=question).evidence]


def verify_challenger(control: dict, packet: dict, question: str, *, prepared_context=None) -> dict:
    """Check the retention floor, local edit replay, budgets and citation targets."""
    contract = packet["admission_contract"]
    old = {m["unit_id"]: m for b in control["evidence"] for m in b["members"]}
    new = {m["unit_id"]: m for b in packet["evidence"] for m in b["members"]}
    aliases = {row["suppressed_unit_id"]: row for row in contract["aliases"]}
    if set(old) - set(new) != set(aliases):
        raise ValueError("independent control evidence was lost")
    for uid, member in new.items():
        if uid in old and {k: v for k, v in member.items() if k not in {"display_projection", "display_text"}} != old[uid]:
            raise ValueError("original RU evidence changed")
        if prepared_context is not None:
            unit = assembly._prepared_state(prepared_context).units_by_id[uid]
            if any(member[key] != value for key, value in assembly._unit_public(unit).items()):
                raise ValueError("RU lineage or source text mismatch")
        projection = member.get("display_projection")
        if projection is not None:
            if sha256(member["text"].encode("utf-8")).hexdigest() != projection["source_sha256"]:
                raise ValueError("projection source hash mismatch")
            if evidence_projection.apply_display_edits(member["text"], projection["edits"]) != member["display_text"]:
                raise ValueError("projection source-range replay mismatch")
            if prepared_context is not None and projection["edits"]:
                state = assembly._prepared_state(prepared_context)
                chain_key = (unit["content_type"], *assembly._base_address(unit),
                             canonical_json_bytes(dict(assembly._nested(unit))))
                scalars = evidence_projection._scalar_ranges(unit, state.fragment_chains[chain_key])
                for scalar, stored in zip(scalars, projection["scalar_contexts"]):
                    if (sha256(scalar["text"].encode("utf-8")).hexdigest() != stored["scalar_sha256"]
                            or scalar["unit_ids"] != stored["unit_ids"]):
                        raise ValueError("scalar context binding mismatch")
                for edit in projection["edits"]:
                    scalar = scalars[edit["scalar_context"]]
                    start, end = edit["scalar_token_range"]
                    if scalar["text"][start:end] != edit["scalar_token"]:
                        raise ValueError("scalar token range mismatch")
    for uid, row in aliases.items():
        representative = new[row["representative_unit_id"]]
        if old[uid]["text"] != representative["text"] or row["representative_evidence_id"] not in {
            b["evidence_id"] for b in packet["evidence"] if representative in b["members"]
        }:
            raise ValueError("alias lacks visible representative")
    counts = [len(b["text"]) for b in packet["evidence"]]
    budget = packet["budget"]
    if (counts != [b["char_count"] for b in packet["evidence"]]
            or sum(counts) != budget["used_context_chars"] or sum(counts) > budget["total_context_chars"]
            or any(count > budget["per_block_chars"] for count in counts)):
        raise ValueError("display budget mismatch")
    request = project_generation_request(packet, question=question)
    validation = validate_citations(" ".join(f"[{b.evidence_id}]" for b in request.evidence), request)
    if validation.integrity != "pass":
        raise ValueError("citation target integrity failure")
    return {"retention": "PASS", "source_range_replay": "PASS", "budget": "PASS",
            "citation_integrity": "PASS", "semantic_faithfulness": "not_evaluated",
            "alias_released_chars": contract["alias_released_chars"],
            "projection_released_chars": contract["projection_released_chars"],
            "added_units": len(set(new) - set(old)), "suppressed_aliases": len(aliases),
            "body_chars": sum(counts),
            "message_chars": sum(len(row["content"]) for row in generation_messages(request)),
            "preserved_uncertain_units": sum(m.get("display_projection", {}).get("status") == "preserved_uncertain"
                                             for m in new.values())}


def run(source: Path, output: Path) -> dict:
    source = source.resolve()
    if output.resolve().is_relative_to(source) or source.is_relative_to(output.resolve()):
        raise ValueError("output must be separate from the frozen source root")
    source_manifest = json.loads((source / "manifest.json").read_bytes())
    if source_manifest["status"] != "complete" or source_manifest["completed"] != dict.fromkeys(PROFILES, 70):
        raise ValueError("source requires a complete paired 70Q run")
    paired = read_jsonl(source / "paired-results.jsonl")
    ids = [f"Q{i:03d}" for i in range(1, 71)]
    if [row["question_id"] for row in paired] != ids:
        raise ValueError("source must contain ordered Q001-Q070")
    cases = [_case(source, profile, row["question_id"], row["results"][profile]["sha256"])
             for profile in PROFILES for row in paired]
    inputs = {"manifest": artifact(source / "manifest.json"), "paired_results": artifact(source / "paired-results.jsonl"),
              "cases": [case["inputs"] for case in cases]}
    ru_input = source_manifest["inputs"]["ru_manifest"]
    ru_manifest = Path(ru_input["path"])
    if artifact(ru_manifest)["sha256"] != ru_input["sha256"]:
        raise ValueError("source RU manifest hash mismatch")
    inputs["ru_manifest"] = artifact(ru_manifest)
    needed = {row["unit_id"] for case in cases for row in case["candidates"]}
    _, candidate_units = load_retrieval_units(ru_manifest, selected_unit_ids=needed)
    if {row["unit_id"] for row in candidate_units} != needed:
        raise ValueError("source RU candidates missing")
    records = {row["source"]["canonical_address"]["record_id"] for row in candidate_units}
    context = assembly.prepare_evidence_assembly_context(ru_manifest, selected_record_ids=records)
    identity = {"schema_version": "phase04-retained-assembly-70q-1", "inputs": inputs,
                "source_run_identity": source_manifest["run_identity"], "variants": list(VARIANTS),
                "pipeline_source": {module.__name__: artifact(Path(module.__file__))
                                    for module in (assembly, evidence_compaction, evidence_projection)},
                "runner": artifact(Path(__file__)), "python_version": sys.version}
    manifest = {**identity, "run_identity": sha256_json(identity), "status": "running",
                "completed_controls": 0, "completed_challengers": 0, "human_review": "pending",
                "provider_calls": 0}
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "manifest.json", manifest)
    for descriptor in [identity["runner"], *identity["pipeline_source"].values()]:
        code = Path(descriptor["path"])
        atomic_write(output / "source" / code.name, code.read_bytes())
    metrics, blinded, keys, decisions = [], [], [], []
    try:
        # Every control must reproduce before the first challenger is built.
        for case in cases:
            control = case["control"]
            cfg = assembly.EvidenceAssemblyConfig(**control["assembly_config"])
            replay = assembly.assemble_deferred_footprint_charge_packet(
                ru_manifest, case["candidates"], config=cfg, prepared_context=context,
                retrieval_audit=control["retrieval_audit"]["retrieval_metadata"],
            )
            if assembly.evidence_packet_json_bytes(replay) != assembly.evidence_packet_json_bytes(control):
                raise ValueError(f"{case['profile']}/{case['question_id']}: control replay mismatch")
            assembly.write_evidence_packet(output / "control" / case["profile"] / case["question_id"], replay)
            manifest["completed_controls"] += 1
        write_json(output / "manifest.json", manifest)
        print("Control replay PASS 140/140", flush=True)
        for case in cases:
            for variant in VARIANTS:
                kwargs = {"variant": variant, "config": assembly.EvidenceAssemblyConfig(**case["control"]["assembly_config"]),
                          "prepared_context": context,
                          "retrieval_audit": case["control"]["retrieval_audit"]["retrieval_metadata"]}
                packet = assemble_retained_evidence_packet(ru_manifest, case["candidates"], **kwargs)
                repeat = assemble_retained_evidence_packet(ru_manifest, case["candidates"], **kwargs)
                if assembly.evidence_packet_json_bytes(packet) != assembly.evidence_packet_json_bytes(repeat):
                    raise ValueError("challenger determinism failure")
                measurement = verify_challenger(case["control"], packet, case["question"], prepared_context=context)
                path = output / variant / case["profile"] / case["question_id"]
                assembly.write_evidence_packet(path, packet)
                case_id = f"{variant}/{case['profile']}/{case['question_id']}"
                metrics.append({"case_id": case_id, "question_id": case["question_id"], "profile": case["profile"],
                                "variant": variant, **measurement, "determinism": "PASS"})
                sides = ("control", variant) if sha256(f"{manifest['run_identity']}:{case_id}".encode()).digest()[0] % 2 else (variant, "control")
                projections = {"control": _visible(case["control"], case["question"]), variant: _visible(packet, case["question"])}
                blinded.append({"case_id": case_id, "question_id": case["question_id"], "question": case["question"],
                                "packet_a": projections[sides[0]], "packet_b": projections[sides[1]]})
                keys.append({"case_id": case_id, "side_a": sides[0], "side_b": sides[1]})
                decisions.append({"case_id": case_id, "run_identity": manifest["run_identity"], "status": "pending",
                                  "verdict": None, "side_a_support": None, "side_b_support": None,
                                  "evidence_ids": [], "rationale": None})
                manifest["completed_challengers"] += 1
            print(f"{case['profile']} {case['question_id']} 3/3", flush=True)
        # Review-only reference answers are read only after all Assembly work.
        reference_path = Path(source_manifest["inputs"]["review_only"]["path"])
        if artifact(reference_path)["sha256"] != source_manifest["inputs"]["review_only"]["sha256"]:
            raise ValueError("review-only source changed")
        references = {row["question_id"]: row for row in read_jsonl(reference_path)}
        for row in blinded:
            reference = references[row["question_id"]]
            if reference["question"] != row["question"]:
                raise ValueError("review reference question mismatch")
            row["reference_answer"] = reference["reference_answer"]
        for case in cases:
            for descriptor in case["inputs"].values():
                if artifact(Path(descriptor["path"])) != descriptor:
                    raise ValueError("frozen input changed during replay")
        for descriptor in [inputs["manifest"], inputs["paired_results"], inputs["ru_manifest"],
                           identity["runner"], *identity["pipeline_source"].values()]:
            if artifact(Path(descriptor["path"])) != descriptor:
                raise ValueError("bound source or implementation changed during replay")
        write_jsonl(output / "metrics.jsonl", metrics)
        write_jsonl(output / "review/blinded-packets.jsonl", blinded)
        write_jsonl(output / "review/unblinding.jsonl", keys)
        write_jsonl(output / "review/decisions-template.jsonl", decisions)
        summary = {}
        for profile in PROFILES:
            summary[profile] = {}
            for variant in VARIANTS:
                rows = [row for row in metrics if row["profile"] == profile and row["variant"] == variant]
                summary[profile][variant] = dict(sum((Counter({k: row[k] for k in (
                    "alias_released_chars", "projection_released_chars", "added_units", "suppressed_aliases",
                    "body_chars", "message_chars", "preserved_uncertain_units")}) for row in rows), Counter()))
        manifest.update(status="complete", summary=summary)
    except BaseException as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        write_json(output / "manifest.json", manifest)
    return manifest


def tally_review(output: Path, decisions_path: Path) -> dict:
    """Bind explicit human/Codex judgments to the actual blinded Packet IDs."""
    manifest = json.loads((output / "manifest.json").read_bytes())
    if manifest["status"] != "complete" or manifest["completed_challengers"] != 420:
        raise ValueError("review requires a complete 420-case Assembly run")
    packets = read_jsonl(output / "review/blinded-packets.jsonl")
    keys = read_jsonl(output / "review/unblinding.jsonl")
    decisions = read_jsonl(decisions_path)
    ids = [row["case_id"] for row in packets]
    if len(set(ids)) != 420 or any([row["case_id"] for row in rows] != ids for rows in (keys, decisions)):
        raise ValueError("review case identity/order mismatch")
    counts = Counter()
    for packet, mapping, decision in zip(packets, keys, decisions):
        if decision["run_identity"] != manifest["run_identity"]:
            raise ValueError("review belongs to a different run")
        if decision["status"] == "pending" and decision["verdict"] is None:
            counts["pending"] += 1
            continue
        if decision["status"] != "reviewed" or not str(decision.get("rationale") or "").strip():
            raise ValueError("reviewed decision requires a rationale")
        if decision["verdict"] not in {"a_better", "b_better", "equivalent", "both_insufficient"}:
            raise ValueError("invalid review verdict")
        if any(decision[f"side_{side}_support"] not in {"sufficient", "partial", "insufficient"} for side in ("a", "b")):
            raise ValueError("reviewed support is missing")
        valid = {f"{side.upper()}:{row['evidence_id']}" for side in ("a", "b") for row in packet[f"packet_{side}"]}
        if not set(decision["evidence_ids"]) <= valid:
            raise ValueError("review evidence ID is outside its Packet")
        verdict = decision["verdict"]
        if verdict in {"a_better", "b_better"}:
            verdict = mapping[f"side_{verdict[0]}"] + "_better"
        counts[verdict] += 1
    return {"run_identity": manifest["run_identity"], "decisions": artifact(decisions_path), "verdict_counts": dict(counts)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tally-review", type=Path)
    args = parser.parse_args()
    if args.tally_review is not None:
        print(json.dumps(tally_review(args.output, args.tally_review), ensure_ascii=False, indent=2))
        return
    with offline_only():
        run(args.source, args.output.resolve())


if __name__ == "__main__":
    main()
