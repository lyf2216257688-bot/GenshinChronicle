"""Compare fixed-path DSH MCP runs and verify evidence parity before timing."""

import argparse
import math
from pathlib import Path
from statistics import median

from genshin_corpus.evidence_io import read, require, write


STAGES = ("cold_open", "warm_open", "warm_follow")


def query_rows(root):
    runs = {}
    for path in (root / "queries").glob("*/run.json"):
        run = read(path)
        query = run["query"]
        require(run["status"] == "complete" and run["remote_calls"] == 0, "Invalid timing run")
        key = (query["session_id"], query["query_id"])
        require(key not in runs, "Duplicate timing run")
        runs[key] = (run, read(path.with_name("ranked.json")))
    return runs


def provenance(row):
    return {cid: {key: value for key, value in source.items() if key not in {"handle", "read_ref"}}
            for cid, source in row["citations"]["sources"].items()}


def compare(left, right):
    host = [read(p / "dsh-client.json") for p in (left, right)]
    require(all(v["generation_calls"] == 0 and v["release"] == "20261124" for v in host), "Foreign DSH run")
    require(host[0]["bridge_sha256"] == host[1]["bridge_sha256"], "DSH bridge changed")
    base, revised = query_rows(left), query_rows(right)
    require(base.keys() == revised.keys() and len(base) == 3, "Different processing path")
    for key, (run, ranked) in base.items():
        other, new_ranked = revised[key]
        require(run["query"] == other["query"] and ranked["candidates"] == new_ranked["candidates"],
                "Ordered candidate, source or score regression")
    results = {}
    for stage in STAGES:
        a, b = (read(dir / (stage + ".json")) for dir in (left, right))
        require(all(a[k] == b[k] for k in ("stage", "tool", "args", "projection")) and provenance(a) == provenance(b),
                "Fixed path evidence or citation source differs")
        key = a["args"]["study"], "original" if a["tool"] == "open_study" else a["args"]["request_id"]
        results[stage] = {"base_seconds": a["seconds"], "revised_seconds": b["seconds"],
                          "base_retrieval": base[key][0], "revised_retrieval": revised[key][0],
                          "verified_candidates": len(base[key][1]["candidates"]),
                          "verified_citations": len(a["citations"]["citations"])}
    return results


def quantiles(values):
    return {"median_seconds": median(values), "p90_observed_seconds": sorted(values)[math.ceil(.9 * len(values)) - 1]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", type=Path, nargs=2, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd().resolve() / "data/generation/mcp-live-v1"
    out = args.output.resolve()
    require(out.is_relative_to(root) and not out.exists(), "Use a new MCP audit path")
    pairs = [compare(*(root / p for p in pair)) for pair in args.pair]
    summary = {}
    for stage in STAGES:
        old, new = (quantiles([p[stage][arm + "_seconds"] for p in pairs]) for arm in ("base", "revised"))
        summary[stage] = {"base": old, "revised": new,
                          "median_reduction": 1 - new["median_seconds"] / old["median_seconds"]}
    write(out, {"same_rank_body_and_source": True, "sample_pairs": len(pairs),
                "comparisons": [[str(p) for p in pair] for pair in args.pair],
                "summary": summary, "samples": pairs})


if __name__ == "__main__":
    main()
