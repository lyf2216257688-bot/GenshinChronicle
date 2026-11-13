"""P4 diagnostics, never automatic relevance labels or a serving decision."""
from collections import Counter
from itertools import combinations
import json

from ...evidence_io import digest, require
from .core import CHANNELS, QuerySpec, covered_by, fuse_candidates, validate_profile

CHANNEL_ORDER = ("dense", "sparse", "bm25f")


def profiles(config):
    result = {}
    for combination in CHANNELS:
        for fusion, cleanup in (("f1_max", True), ("f0", True), ("f1_rrf", True), ("f1_max", False)):
            key = combination + "/" + fusion + ("" if cleanup else "/no_cleanup")
            profile = {"combination": combination, "fusion": fusion, "structural_cleanup": cleanup,
                       **{k: config[k] for k in ("depth", "candidate_limit", "rrf_k")},
                       "channel_weights": config["channel_weights"][combination]}
            validate_profile(profile)
            result[key] = profile
    return result


def ids(rows):
    return {r["chunk_id"] for r in rows}


def span_union(documents, selected_ids):
    """Primary body only; source character overlap is NOT semantic equivalence.

    No cross-observation merging, context expansion or alias equivalence.
    """
    groups = {}
    for cid in selected_ids:
        for ref in documents[cid]["chunk"]["clean_refs"]:
            key = tuple(ref[k] for k in ("observation_id", "block_id", "value_pointer"))
            groups.setdefault(key, []).append((ref["char_start"], ref["char_end"]))
    total = 0
    for intervals in groups.values():
        end = -1
        for start, stop in sorted(intervals):
            total += max(0, stop - max(start, end))
            end = max(end, stop)
    return total


def coverage_diagnostics(documents, selected_ids):
    parents = Counter((documents[c]["row"]["observation_id"], documents[c]["row"]["parent_id"])
                      for c in selected_ids)
    raw_size = sum(span_union(documents, {c}) for c in selected_ids)
    union = span_union(documents, selected_ids)
    return {"chunks": len(selected_ids), "parents": len(parents),
            "observations": len({documents[c]["row"]["observation_id"] for c in selected_ids}),
            "max_chunks_one_parent": max(parents.values(), default=0),
            "body_union_codepoints": union, "body_repeated_codepoints": raw_size - union}


def difference(left, right, documents):
    """Keep IDs and directional source differences, including safe containment proof."""
    only = sorted(left - right, key=lambda c: documents[c]["input_row"])
    contained = [c for c in only if any(covered_by(documents[c]["chunk"], documents[r]["chunk"]) for r in right)]
    return {"only": only, "only_count": len(only), "safely_contained_by_right": contained,
            "not_proven_contained_by_right": [c for c in only if c not in set(contained)],
            "body_codepoints_outside_right": span_union(documents, left | right) - span_union(documents, right)}


def pair(left, right, documents):
    common = left & right
    return {"common": len(common), "jaccard": len(common) / len(left | right) if left | right else None,
            "left_minus_right": difference(left, right, documents),
            "right_minus_left": difference(right, left, documents)}


def analyze(question, stages, documents, config, bundle_id):
    query = QuerySpec(**question)
    require(set(stages) == set(CHANNEL_ORDER), "Missing P4 channel")
    all_hits = [hit for ch in CHANNEL_ORDER for hit in stages[ch]["hits"]]
    require(ids(all_hits) == set(documents), "P4 document coverage differs")
    results, diagnostics = {}, {"channels": {}, "channel_pairs": {}, "profiles": {}, "comparisons": {}, "channel_ranks": {}}
    sets = {ch: ids(stages[ch]["hits"]) for ch in CHANNEL_ORDER}
    for ch in CHANNEL_ORDER:
        diagnostics["channel_ranks"][ch] = {cid: min(h["rank"] for h in stages[ch]["hits"] if h["chunk_id"] == cid) for cid in sets[ch]}
        diagnostics["channels"][ch] = {**coverage_diagnostics(documents, sets[ch]),
            "route_counts": {g: len([h for h in stages[ch]["hits"] if h["granularity"] == g]) for g in ("micro", "macro")},
            "elapsed_seconds": stages[ch]["elapsed_seconds"]}
    for a, b in combinations(CHANNEL_ORDER, 2):
        diagnostics["channel_pairs"][a + "/" + b] = {
            "union": pair(sets[a], sets[b], documents),
            **{g: pair(ids([h for h in stages[a]["hits"] if h["granularity"] == g]),
                        ids([h for h in stages[b]["hits"] if h["granularity"] == g]), documents)
               for g in ("micro", "macro")}}
    for lexical, other in (("sparse", "bm25f"), ("bm25f", "sparse")):
        # Residual against BOTH channels, not just lexical overlap.
        unique = sets[lexical] - sets[other]
        diagnostics[lexical + "_unique_vs_other_lexical"] = {
            "ids": sorted(unique), "also_dense": sorted(unique & sets["dense"]),
            "residual_vs_dense": difference(unique, sets["dense"], documents)}
    import time
    for key, profile in profiles(config).items():
        started = time.perf_counter()
        hits = [h for h in all_hits if h["channel"] in CHANNELS[profile["combination"]]]
        result = fuse_candidates(query, hits, documents, profile, input_bundle_id=bundle_id)
        elapsed = time.perf_counter() - started
        # Store original source once per question, not twelve copies.
        result["candidates"] = [{k: v for k, v in r.items() if k not in {"source", "chunk"}} for r in result["candidates"]]
        result.pop("raw_hits")
        results[key] = result
        diagnostics["profiles"][key] = {
            "counts": result["counts"], "fusion_seconds": elapsed,
            "serial_channel_seconds": sum(stages[c]["elapsed_seconds"] for c in profile["channel_weights"]),
            "cutoffs": {str(k): coverage_diagnostics(documents, ids(result["candidates"][:k])) for k in config["cutoffs"]}}
    for a, b in (("S0/f1_max", "S1/f1_max"), ("S0/f1_max", "S2/f1_max"), ("S1/f1_max", "S2/f1_max")):
        diagnostics["comparisons"][a + " -> " + b] = {
            str(k): pair(ids(results[a]["candidates"][:k]), ids(results[b]["candidates"][:k]), documents) for k in config["cutoffs"]}
    for combo in CHANNELS:
        a = combo + "/f1_max"
        for suffix in ("f0", "f1_rrf", "f1_max/no_cleanup"):
            b = combo + "/" + suffix
            diagnostics["comparisons"][a + " -> " + b] = {
                str(k): pair(ids(results[a]["candidates"][:k]), ids(results[b]["candidates"][:k]), documents) for k in config["cutoffs"]}
    return {"version": "gc-recall-comparison-1", "question": question, "input_bundle_id": bundle_id,
            "profiles": results, "diagnostics": diagnostics}


def stable_comparison(result):
    """Exclude measured timings ONLY; replay must reproduce every ranking/disposition/diagnostic."""
    value = json.loads(json.dumps(result))
    for row in value["diagnostics"]["profiles"].values():
        row.pop("fusion_seconds")
    return value


def case_ids(result, documents, config):
    selected, reasons = [], {}
    def add(cid, reason):
        if cid not in reasons:
            if len(selected) >= config["max_case_chunks"]:
                return
            selected.append(cid)
            reasons[cid] = []
        if reason not in reasons[cid]:
            reasons[cid].append(reason)
    n = config["case_candidates"]
    for combo in CHANNELS:
        for row in result["profiles"][combo + "/f1_max"]["candidates"][:n]:
            add(row["chunk_id"], combo + " 前排")
    for ch in ("sparse", "bm25f"):
        values = result["diagnostics"][ch + "_unique_vs_other_lexical"]["residual_vs_dense"]["not_proven_contained_by_right"]
        values = sorted(values, key=lambda c: (result["diagnostics"]["channel_ranks"][ch][c], documents[c]["input_row"]))
        for cid in values[:n]:
            add(cid, ch + " 独有且未证实被 Dense 包含；待判断价值")
    cutoff = str(config["cutoffs"][0])
    focus = ("S0/f1_max -> S1/f1_max", "S0/f1_max -> S2/f1_max", "S2/f1_max -> S2/f0",
             "S2/f1_max -> S2/f1_rrf", "S2/f1_max -> S2/f1_max/no_cleanup")
    pending = []
    for name in focus:
        changes = result["diagnostics"]["comparisons"][name]
        choices = []
        for direction in ("left_minus_right", "right_minus_left"):
            side = name.split(" -> ")[0 if direction == "left_minus_right" else 1]
            ranks = {r["chunk_id"]: r["organized_rank"] for r in result["profiles"][side]["candidates"]}
            cids = sorted(changes[cutoff][direction]["not_proven_contained_by_right"], key=lambda c: ranks[c])
            choices.extend((cid, name + " " + direction + "@" + cutoff) for cid in cids[:n])
        if choices:
            add(*choices[0])
            pending.extend(choices[1:])
    for args in pending:
        add(*args)
    return [(cid, reasons[cid]) for cid in selected]


def render_cases(result, documents, config):
    q = result["question"]
    text = [f"# {q['question_id']} 原文差异", "", q["question"], "",
            "候选不等于有效证据。独有、未被包含、覆盖字符、父层多样性均不是相关性标签。", "",
            f"展示至多{config['max_case_chunks']}个完整原文示例，差异优先按通道/候选名次取例；没有截断单条原文。", "",
            "范围、条件和全部原始候选的完整来源见同目录 documents.json。", ""]
    for cid, reasons in case_ids(result, documents, config):
        doc, row = documents[cid], documents[cid]["row"]
        chunk = doc["chunk"]
        body = chunk["retrieval_text"]
        fence = "~" * max(4, max((len(line) + 1 for line in body.splitlines() if set(line) == {"~"}), default=0))
        text += [f"## {cid}", "", "; ".join(reasons), "",
                 f"input_row={doc['input_row']}；source_item={row['source_item']}；parent={row['parent_id']}", "",
                 "条件状态: " + ", ".join(sorted({p.get("condition_status", "not_established") for p in chunk["parts"]})), "",
                 "通道最佳原始名次: " + json.dumps({ch: ranks.get(cid) for ch, ranks in result["diagnostics"]["channel_ranks"].items()}, ensure_ascii=False), "",
                 fence + "text", body, fence, "",
                 "CleanRefs: " + json.dumps(row["clean_refs"], ensure_ascii=False), "",
                 "来源性质/标题/语境: " + json.dumps(row["source_context"], ensure_ascii=False), ""]
    return "\n".join(text)
