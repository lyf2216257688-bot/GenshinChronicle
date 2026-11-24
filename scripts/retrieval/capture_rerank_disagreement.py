"""Register the independent evidence set and capture one local S0/BGE run per question."""

import argparse
from pathlib import Path

from evaluate_g5_evidence import CLEAN_ROOT, G7_ROOT, assets, checked, g7_index, new_output
from rerank_disagreement_policy import ARMS, REGISTRATION, RANK_GAP, ROUTE_LIMIT, SLOT

from genshin_corpus.clean import iter_blocks, iter_text_values
from genshin_corpus.evidence_io import read, require, seal, sha, write
from genshin_corpus.indexing.benchmark_io import offline_only
from genshin_corpus.retrieval.clean_based.query_v2 import ResearchQuery
from genshin_corpus.retrieval.clean_based.runtime_v2 import LocalPipeline, code_identity


SCRIPTS = Path(__file__).resolve().parent
INPUTS = ("rerank_disagreement_cases.json", "rerank_disagreement_policy.py", Path(__file__).name)


def bind_cases(repo):
    cases = read(SCRIPTS / INPUTS[0])
    require(len(cases) == 20 and len(set(v["question"] for v in cases.values())) == 20, "Independent inventory differs")
    known = {v["question"] for v in g7_index(repo).values()}
    root = repo / CLEAN_ROOT
    manifest = read(root / "metadata/manifest.json")
    records = {row["content_id"]: row for row in manifest["records"]}
    cache = {}
    for case in cases.values():
        require(case["question"] not in known, "Independent question overlaps G7")
        for claim in case["claims"]:
            anchors = []
            for content_id, block_id, quote in claim["all"]:
                entry = records[content_id]
                if content_id not in cache:
                    record = checked(root, {"path": entry["record_path"], "sha256": entry["record_sha256"]})
                    require(record["observation_id"] == entry["observation_id"], "Clean observation changed")
                    cache[content_id] = {row["block_id"]: row for row in iter_blocks(record)}
                block = cache[content_id][block_id]
                values = [value for value in iter_text_values(block) if quote in value["text"]]
                require(len(values) == 1 and values[0]["text"].count(quote) == 1, "Ambiguous/missing Clean anchor")
                value = values[0]
                start = value["text"].index(quote)
                anchors.append({"content_id": content_id, "observation_id": entry["observation_id"],
                                "record_sha256": entry["record_sha256"], "record_path": entry["record_path"],
                                "block_id": block_id, "value_pointer": value["value_pointer"],
                                "char_start": start, "char_end": start + len(quote), "quote": quote,
                                "origin_id": value["origin_id"]})
            claim["all"] = anchors
    return cases


def binding(repo):
    return {"code": {name: sha(SCRIPTS / name) for name in INPUTS}, "upstream": code_identity(),
            "assets": assets(repo), "g7_index": sha(repo / G7_ROOT / "index.jsonl")}


def register(repo, out):
    cases = bind_cases(repo)
    new_output(repo, out)
    write(out / "registration.json", {"version": REGISTRATION, "binding": binding(repo), "cases": cases,
          "policies": list(ARMS), "slot": SLOT, "route_limit": ROUTE_LIMIT, "rank_gap": RANK_GAP,
          "generation_profile": "gc-g5-official-4", "remote_calls_authorized": 0})
    print("Registered 20 evidence cases before local ranking", flush=True)


def registered(repo, out):
    value = read(out / "registration.json")
    require(value["version"] == REGISTRATION and value["binding"] == binding(repo)
            and value["cases"] == bind_cases(repo), "Registration or frozen inputs changed")
    return value


def capture(repo, out, resume):
    plan = registered(repo, out)
    capture_root = out / "independent"
    capture_root.mkdir(exist_ok=resume)
    completed = {}
    for case_id, case in plan["cases"].items():
        query = ResearchQuery("rerank-disagreement-independent", case_id, "original", *([case["question"]] * 4))
        case_root = capture_root / case_id
        if case_root.exists():
            require(resume and (case_root / "complete.json").is_file(), "Partial capture preserved at " + str(case_root))
            from genshin_corpus.evidence_io import verify
            require(verify(case_root)["query"] == query.value(), "Captured query changed")
        else:
            with LocalPipeline(repo, repo / "config/clean-retrieval-v1.json", repo / "config/reranking-selected-v1.json",
                               case_root) as pipeline:
                group = pipeline.run(query)
            require(group["query"] == query.value(), "Local query changed")
            seal(case_root, {"query": query.value(), "registration_sha256": sha(out / "registration.json")})
            print(case_id + " captured", flush=True)
        completed[case_id] = sha(case_root / "complete.json")
    write(out / "independent-complete.json", {"cases": completed, "local_only": True})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("register", "capture"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    repo = Path.cwd().resolve()
    with offline_only():
        if args.action == "register":
            register(repo, args.out.resolve())
        else:
            capture(repo, args.out.resolve(), args.resume)


if __name__ == "__main__":
    main()
