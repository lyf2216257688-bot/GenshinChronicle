"""Registered old/new recall and initial Packet review for the sealed corpus run."""

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from genshin_corpus.collector.run_store import create_json
from genshin_corpus.evidence_io import digest, inside, read, require, sha, strict_json
from genshin_corpus.generation.clean_evidence import EvidenceReader, validate_citations
from genshin_corpus.indexing.benchmark_io import offline_only
from genshin_corpus.rag.clean_research import packet_for
from genshin_corpus.retrieval.clean_based.query_v2 import ResearchQuery
from genshin_corpus.retrieval.clean_based.runtime_v2 import JsonWorker, LocalPipeline
from genshin_corpus.retrieval.clean_based.snapshot import open_release, run_query
from genshin_corpus.retrieval.clean_based.snapshot_source import SnapshotReader


G7 = "data/generation/g7-70q-baseline-20261117"
G7_INDEX_SHA = "2034d57f874794a45bf8954c90aebd1ee68c917c905d2e61e272ceae9719e593"
QUESTION_SET = "data/retrieval/index-benchmark-v1-70q-raw-20261110/question-set.jsonl"
QUESTION_SHA = "a66e270058bb3b56f1a59252634a0e36188e669ed9d12311b284a4c7c3455df2"
CHANGE_CASES = {"509698": "added", "1190": "modified", "2030": "modified", "506076": "modified", "1048": "unchanged"}


def _rows(path):
    with path.open(encoding="utf8") as handle:
        return [strict_json(line) for line in handle]


def _binding(path, repo):
    return {"path": path.relative_to(repo).as_posix(), "sha256": sha(path)}


def register_cases(repo, diff_path):
    index = repo / G7 / "index.jsonl"
    questions = repo / QUESTION_SET
    require(sha(index) == G7_INDEX_SHA and sha(questions) == QUESTION_SHA, "Historical set changed")
    baseline, wanted = _rows(index), _rows(questions)
    require(len(baseline) == len(wanted) == 70, "Historical set coverage differs")
    result = []
    for row, question in zip(baseline, wanted, strict=True):
        require(all(row[k] == question[k] for k in ("question_id", "question")), "Historical question changed")
        store = inside(repo / G7, row["store"])
        request = store / "chat_artifacts" / row["turn_id"] / (digest("g5-request-0") + ".json")
        settings = store / "live/permit.json"
        require(read(settings)["settings"]["generation_profile"] == "gc-g5-official-4", "Unexpected historical policy")
        result.append({"id": row["question_id"], "question": row["question"], "kind": "historical",
                       "store": store.relative_to(repo).as_posix(), "request": _binding(request, repo),
                       "settings": _binding(settings, repo)})
    diff = read(diff_path)
    changes = {row["source_item"]["content_id"]: row for row in diff["records"]}
    selected = {**CHANGE_CASES, **{cid: "removed" for cid, row in changes.items() if row["status"] == "removed"}}
    for cid, expected in sorted(selected.items()):
        row = changes[cid]
        require(row["status"] == expected, "Registered change case changed")
        current = row["current_observation"] or row["previous_observation"]
        raw = repo / "data/raw/mihoyo_obc/zh-cn" / current["run_id"]
        loc = current["locators"]["detail"]
        path = inside(raw, loc["path"])
        require(sha(path) == loc["sha256"], "Change case Raw title source differs")
        title = read(path)["data"]["page"]["name"]
        require(isinstance(title, str) and bool(title.strip()), "Change case has no query title")
        result.append({"id": "change-" + cid, "kind": "change", "question": title, "content_id": cid,
                       "raw_disposition": expected, "changed_parts": row["changed_parts"], "title_source": _binding(path, repo),
                       "template": result[0]["id"]})
    return result


class Saved:
    def __init__(self, root):
        self.root = root

    def load(self, ref):
        path = inside(self.root, ref["path"])
        require(sha(path) == ref["sha256"], "Archived artifact changed: " + str(path))
        return read(path)


class PacketStore:
    def __init__(self):
        self.values = {}

    def put(self, value):
        key = digest(value)
        self.values[key] = value
        return key

    def load(self, ref):
        return self.values[ref]


def first_packet(reader, group, context, settings):
    require(settings["initial_reads"] == 6, "Historical initial read limit changed")
    evidence = EvidenceReader(reader, verified_parent_ranges=False)
    handles = evidence.register(group)
    reads = [evidence.read(handle) for handle in list(handles.values())[:6]]
    store = PacketStore()
    query = group["query"]
    state = {"groups": [store.put(group)], "queries": [query], "tool_count": 0, "failures": [], "stop": "researching",
             "reads": {item["receipt_id"]: store.put(item) for item in reads}}
    _, packet = packet_for(store, {"question": query["original_question"]}, store.put(context), state, settings)
    check = validate_citations(packet, list(packet["citations"]))
    require(packet["estimated_message_tokens"] + packet["reserved_tokens"] <= packet["policy"]["budget"]["context_tokens"],
            "Initial Packet exceeds its frozen budget")
    return packet, reads, check


def _registered(repo, spec):
    archive = Saved(inside(repo, spec["store"]))
    request = Saved(repo).load(spec["request"])
    state = archive.load(request["data_ref"])
    group, = [archive.load(ref) for ref in state["groups"]]
    packet = archive.load(request["sources_ref"])["packet"]
    receipts = [archive.load(ref) for ref in state["reads"].values()]
    context = archive.load(request["context_ref"])
    settings = Saved(repo).load(spec["settings"])["settings"]
    require(group["query"]["original_question"] == spec["question"] and settings["generation_profile"] == "gc-g5-official-4",
            "Historical archive/query mismatch")
    return group, packet, receipts, context, settings


def candidate_key(s):
    return [s["source_item"], s["dense_text_sha256"]]


def recall_view(group, input_rows=None):
    routes = defaultdict(list)
    for hit in group["raw_hits"]:
        row = hit["index_mapping"] if input_rows is None else input_rows[hit["input_row"]]
        require(input_rows is None or row["chunk_id"] == hit["chunk_id"], "New raw hit row differs")
        routes[(hit["channel"], hit["granularity"])].append({"key": candidate_key(row),
                       "rank": hit["rank"], "score": hit["raw_score"], "chunk_id": hit["chunk_id"]})
    return {"/".join(route): sorted(rows, key=lambda item: item["rank"]) for route, rows in routes.items()}


def membership(old, new):
    a, b = (Counter(digest(row["key"]) for row in rows) for rows in (old, new))
    return {"old": len(old), "new": len(new), "retained": sum((a & b).values()),
            "exited": sum((a - b).values()), "entered": sum((b - a).values())}


def rank_view(group):
    return [{"key": candidate_key(row["source"]), "rank": row["reranking"]["final_rank"], "chunk_id": row["chunk_id"]}
            for row in group["candidates"]]


def packet_view(packet, receipts):
    validate_citations(packet, list(packet["citations"]))
    by_receipt = {item["receipt_id"]: item for item in receipts}
    result = []
    for item in packet["evidence"]:
        receipt = by_receipt[item["receipt_id"]]
        require(receipt["receipt_id"] == digest({k: v for k, v in receipt.items() if k != "receipt_id"}),
                "Initial Packet receipt checksum differs")
        fragment = next(f for f in receipt["fragments"] if f["fragment_id"] == item["fragment_id"])
        texts = [part["text"] for part in fragment["texts"]]
        result.append({"key": [fragment["source_item"], digest(texts)], "citation_id": item["citation_id"],
                       "scope": fragment["scope"], "chunk_id": receipt["chunk_id"], "quotes": texts,
                       "source_observations": receipt["source_snapshot"].get("raw_observations"),
                       "effective_clean_observation": fragment["observation_id"]})
    return {"packet_id": packet["packet_id"], "budget": packet["policy"]["budget"],
            "estimated_message_tokens": packet["estimated_message_tokens"],
            "evidence": result, "dispositions": Counter(item["disposition"] for item in packet["dispositions"])}


def _case(repo, release, specs, target, output):
    spec = specs[target]
    output.mkdir(parents=True, exist_ok=False)
    historical = spec["kind"] == "historical"
    original, old_packet, old_reads, context, settings = _registered(repo, spec if historical else specs[spec["template"]])
    if historical:
        query = ResearchQuery.parse(original["query"])
    else:
        text = spec["question"]
        title_evidence = Saved(repo).load(spec["title_source"])
        require(title_evidence["data"]["page"]["name"] == text, "Frozen change title differs")
        query = ResearchQuery("snapshot-change", target, "original", *([text] * 4))
        context = {**context, "turn_id": target, "session_id": query.session_id,
                   "research_state": {**context["research_state"], "current_question": text}}
        context["context_id"] = digest({k: v for k, v in context.items() if k != "context_id"})
        execution = Path(__file__).resolve().parents[2]

        def worker(project, python, module, args, log, *, timeout):
            return JsonWorker(execution, Path(project) / python, module, args, log, timeout=timeout)

        with LocalPipeline(repo, repo / "config/clean-retrieval-v1.json", repo / "config/reranking-selected-v1.json",
                           output / "old-search", worker_factory=worker) as pipeline:
            original = pipeline.run(query)
            old_packet, old_reads, _ = first_packet(pipeline.reader, original, context, settings)
        del pipeline
    require(query.combination == "S0", "Historical acceptance must retain S0")
    run_query(repo, release, query, output / "new-search", rerank=True)
    _, source, _, binding = open_release(release)
    if not historical:
        require((spec["content_id"] in source.clean) == (spec["raw_disposition"] != "removed"), "Change case inventory differs")
    new = read(output / "new-search/ranked.json")
    new_packet, new_reads, citations = first_packet(SnapshotReader(source), new, context, settings)
    a, b = recall_view(original), recall_view(new, source.rows)
    ar, br = rank_view(original), rank_view(new)
    ap, bp = packet_view(old_packet, old_reads), packet_view(new_packet, new_reads)
    result = {"status": "PASS", "case": spec, "recall": {k: {"membership": membership(a.get(k, []), b.get(k, [])),
                "old": a.get(k, []), "new": b.get(k, [])} for k in sorted(a.keys() | b.keys())},
              "reranked": {"old": ar, "new": br, "top6": membership(ar[:6], br[:6])},
              "packet": {"old": ap, "new": bp, "membership": membership(ap["evidence"], bp["evidence"]),
                         "new_citation_validation": citations}, "old_source": "sealed g7 first turn" if historical else "local selected baseline",
              "semantic_acceptance": "NOT_REVIEWED", "remote_calls": 0}
    if not historical:
        cid = spec["content_id"]
        result["witness"] = {"old_ranked": [x["rank"] for x in ar if x["key"][0]["content_id"] == cid],
                             "new_ranked": [x["rank"] for x in br if x["key"][0]["content_id"] == cid],
                             "old_packet": [x["citation_id"] for x in ap["evidence"] if x["key"][0]["content_id"] == cid],
                             "new_packet": [x["citation_id"] for x in bp["evidence"] if x["key"][0]["content_id"] == cid]}
        if spec["raw_disposition"] == "removed":
            require(not result["witness"]["new_ranked"] and not result["witness"]["new_packet"]
                    and not any(x["key"][0]["content_id"] == cid for rows in b.values() for x in rows),
                    "Removed content leaked into new query")
    create_json(output / "packets.json", {"old": old_packet, "new": new_packet, "new_receipts": new_reads})
    result["packets_sha256"] = sha(output / "packets.json")
    binding.check()
    create_json(output / "case.json", result)


def summarize(paths):
    records = []
    totals = defaultdict(Counter)
    for path in paths:
        case = read(path)
        require(sha(path.with_name("packets.json")) == case["packets_sha256"], "Extended Packet artifacts changed")
        records.append({"id": case["case"]["id"], "kind": case["case"]["kind"],
                        "path": str(path), "sha256": sha(path), "mechanical": case["status"],
                        "semantic_acceptance": case["semantic_acceptance"]})
        if case["case"]["kind"] == "historical":
            for route, value in case["recall"].items():
                totals[route].update(value["membership"])
            totals["reranked_top6"].update(case["reranked"]["top6"])
            totals["first_packet"].update(case["packet"]["membership"])
        else:
            records[-1]["witness"] = case["witness"]
    require(len({item["id"] for item in records}) == len(records)
            and sum(item["kind"] == "historical" for item in records) == 70, "Extended cases are incomplete")
    return {"status": "MECHANICAL_PASS", "semantic_acceptance": "NOT_REVIEWED", "cases": records,
            "historical_membership_totals": dict(totals), "remote_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("repo", "release", "cases", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--case", required=True)
    args = parser.parse_args()
    specs = {case["id"]: case for case in read(args.cases)["extended"]}
    with offline_only():
        _case(args.repo.resolve(), args.release.resolve(), specs, args.case, args.output.resolve())
