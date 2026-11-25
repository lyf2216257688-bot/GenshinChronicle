"""Compare bounded Whoosh bucket builds with independent monolithic references."""

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack, contextmanager
import heapq
import math
from pathlib import Path
from random import Random
from statistics import median
import time

from genshin_corpus.evidence_io import digest, read, require, seal, sha, verify, write
from genshin_corpus.indexing.analyzers import LEXICAL_FIELDS, analyze_fields
from genshin_corpus.indexing.benchmark_io import disk_guard, lines, resources
from genshin_corpus.indexing.benchmark_lexical import configuration, schema
from genshin_corpus.retrieval.clean_based.indexes import selected


BUCKETS = (32, 64, 128, 256)
ROUTES = ("micro", "macro")
QUESTIONS = ("前任大贤者阿扎尔出身于哪个学派？", "白夜国的历史和禁忌知识有什么关系？",
             "原石和摩拉有哪些获取途径？", "枫丹的预言说了什么？", "钟离和若陀龙王是什么关系？",
             "纳塔的部族有哪些传统？", "蒙德的冒险家协会有哪些任务？", "离线改动标记", "离线同分样例", "角色")


def sample(bundle):
    source = read(bundle)
    clean = Path(source["source_manifests"]["clean"])
    require(sha(clean) == source["binding"]["clean_manifest_sha256"], "Clean manifest changed")
    catalog = read(clean)
    keys = sorted(row["content_id"] for row in catalog["records"])
    chosen = set(Random(20261124).sample(keys, 384))
    path = bundle.parent / "inputs.jsonl"
    require(sha(path) == source["artifacts"][path.name], "Input data changed")
    rows = []
    for row in lines(path):
        if row["source_item"]["content_id"] not in chosen:
            continue
        rows.append({"id": row["chunk_id"], "source": row["source_item"], "routes": row["granularities"],
                     "fields": row["lexical_fields"]})
        require(len(rows) <= 6000, "Bounded lexical study exceeds 6000 rows")
    require(len(rows) > 50, "Lexical sample too small")
    return rows


def scenarios(rows, memberships):
    keys = sorted({row["source"]["content_id"] for row in rows})
    grouped = defaultdict(list)
    for key in keys:
        for channel in memberships[key]:
            grouped[channel].append(key)
    biggest = max(grouped.values(), key=len)
    for name, chosen in (("uniform", Random(20261125).sample(keys, 25)),
                         ("same_channel", Random(20261126).sample(biggest, min(25, len(biggest))))):
        actions = {key: i % 3 for i, key in enumerate(chosen)}
        changed = []
        for row in rows:
            action = actions.get(row["source"]["content_id"])
            if action is None:
                changed.append(row)
            elif action == 1:
                changed.append({**row, "fields": {**row["fields"], "body": [*row["fields"]["body"], "离线改动标记"]}})
            elif action == 2:
                changed.append({**row, "routes": [route for route in ROUTES if route not in row["routes"]] or ["micro"]})
        # Exercise deletion, update, membership change and a genuinely new logical key.
        seed = rows[0]
        for i in range(24):
            changed.append({"id": f"study-new-{i:02}", "source": {**seed["source"], "content_id": f"study-{i:02}"},
                            "routes": list(ROUTES), "fields": {field: ["离线同分样例"] for field in LEXICAL_FIELDS}})
        yield name, changed, len(chosen)


def bucket(row, count):
    key = [row["source"][field] for field in ("source", "locale", "content_id")]
    return int(digest(key)[:8], 16) >> (32 - (count.bit_length() - 1))


def group(rows, count):
    groups = defaultdict(list)
    for row in sorted(rows, key=lambda value: value["id"]):
        for route in row["routes"]:
            groups[route, bucket(row, count)].append(row)
    return groups


def build(path, rows, effective, segment):
    from whoosh import index

    path.mkdir(parents=True)
    ix = index.create_in(path, schema())
    try:
        writer = ix.writer(limitmb=64, procs=1)
        try:
            for row in rows:
                terms = analyze_fields(row["fields"], effective["analyzers"], segmenter=segment)
                columns = {f"{field}__{channel}": " ".join(tokens)
                           for field, channels in terms.items() for channel, tokens in channels.items()}
                writer.add_document(chunk_id=row["id"], **columns)
        except BaseException:
            writer.cancel()
            raise
        writer.commit(merge=False)
    finally:
        ix.close()
    return sum(file.stat().st_size for file in path.iterdir() if file.is_file())


@contextmanager
def open_search(paths, effective):
    from whoosh import index, scoring
    from whoosh.reading import MultiReader
    from whoosh.searching import Searcher

    with ExitStack() as cleanup:
        readers = []
        for path in paths:
            ix = index.open_dir(path)
            cleanup.callback(ix.close)
            reader = ix.reader()
            cleanup.callback(reader.close)
            require(reader.is_atomic(), "Expected a single sealed bucket segment")
            require(reader.doc_count_all() == reader.doc_count(), "Bucket contains deleted documents")
            readers.append(reader)
        require(readers, "Empty route in lexical benchmark")
        lengths = {f"{field}__{channel}_B": effective["field_b"][field] for field in LEXICAL_FIELDS
                   for channel in effective["analyzer_weights"]}
        reader = readers[0] if len(readers) == 1 else MultiReader(readers)
        with Searcher(reader, weighting=scoring.BM25F(K1=effective["k1"], **lengths), closereader=False) as searcher:
            yield searcher, readers


def term_query(text, effective, segment):
    from whoosh import query

    analyzed = analyze_fields({field: [text] for field in LEXICAL_FIELDS}, effective["analyzers"], segmenter=segment)
    clauses = [query.Term(f"{field}__{channel}", term,
                          boost=effective["field_weights"][field] * effective["analyzer_weights"][channel])
               for field, channels in analyzed.items() for channel, terms in channels.items() for term in sorted(set(terms))]
    return query.Or(clauses)


def stats(searcher, readers):
    reader = searcher.reader()
    fields = {field: reader.field_length(field) for field in reader.schema.names() if "__" in field}
    frequencies = Counter()
    for leaf in readers:
        frequencies.update({(field, term): leaf.doc_frequency(field, term)
                            for field, term in leaf.all_terms() if field in fields})
    # Length and DF are computed independently of query rank, using live postings only.
    df = digest([[field, term.hex(), value] for (field, term), value in sorted(frequencies.items())])
    return {"documents": reader.doc_count(), "fields": fields, "df_sha256": df,
            "term_count": len(frequencies), "ids_sha256": digest(sorted(row["chunk_id"] for row in reader.all_stored_fields()))}


def evaluate(paths, effective, queries):
    import psutil

    process = psutil.Process()
    base = process.num_handles() if hasattr(process, "num_handles") else process.num_fds()
    start = time.perf_counter()
    with open_search(paths, effective) as (searcher, readers):
        opening = time.perf_counter() - start
        handles = process.num_handles() if hasattr(process, "num_handles") else process.num_fds()
        scores = []
        elapsed = []
        for query in queries:
            tick = time.perf_counter()
            all_hits = searcher.search(query, limit=None, optimize=False)
            result = heapq.nsmallest(20, ((hit["chunk_id"], hit.score) for hit in all_hits),
                                     key=lambda hit: (-hit[1], hit[0]))
            elapsed.append(time.perf_counter() - tick)
            scores.append(result)
        return {"stats": stats(searcher, readers), "scores": scores, "open_seconds": opening,
                "query_median_seconds": median(elapsed), "query_max_seconds": max(elapsed),
                "added_handles": handles - base, "resources": resources()}


def equivalent(reference, actual):
    stats_pass = reference["stats"] == actual["stats"]
    ranks_pass, scores_pass, maximum = True, True, 0.0
    for left, right in zip(reference["scores"], actual["scores"], strict=True):
        ranks_pass &= [k for k, _ in left] == [k for k, _ in right]
        if ranks_pass:
            for (_, a), (_, b) in zip(left, right, strict=True):
                maximum = max(maximum, abs(a - b))
                scores_pass &= math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12)
    return {"statistics": stats_pass, "ranking": ranks_pass, "scores": ranks_pass and scores_pass,
            "max_absolute_score_error_aligned": maximum}


def run(repo, output):
    config = read(repo / "config/index-benchmark-v1.json")
    _, manifests = selected(repo, read(repo / "config/clean-retrieval-v1.json"))
    bundle = Path(manifests["bm25f"][1]["input_manifest"])
    rows = sample(bundle)
    raw = read(repo / "data/raw/mihoyo_obc/zh-cn/p01eb_full_20260824/metadata/inventory.json")
    memberships = {str(row["content_id"]): row["channels"] for row in raw}
    effective, segment = configuration(config["bm25f"], output)
    queries = [term_query(text, effective, segment) for text in QUESTIONS]
    changed = list(scenarios(rows, memberships))
    write(output / "sample.json", {"ids": [row["id"] for row in rows], "questions": QUESTIONS,
                                   "input_manifest_sha256": sha(bundle), "scenario_members": {name: n for name, _, n in changed}})
    reference = {}
    for name, target, _ in changed:
        reference[name] = {}
        for route in ROUTES:
            group_rows = [row for row in target if route in row["routes"]]
            path = output / "reference" / name / route
            tick = time.perf_counter()
            size = build(path, sorted(group_rows, key=lambda row: row["id"]), effective, segment)
            build_seconds = time.perf_counter() - tick
            reference[name][route] = {**evaluate([path], effective, queries), "bytes": size, "build_seconds": build_seconds}
        print("reference", name, flush=True)

    arms = {}
    for count in BUCKETS:
        disk_guard(output, config)
        path = output / str(count)
        base = group(rows, count)
        base_paths = {key: path / "base" / key[0] / str(key[1]) for key in base}
        started = time.perf_counter()
        base_bytes = sum(build(base_paths[key], members, effective, segment) for key, members in sorted(base.items()))
        base_elapsed = time.perf_counter() - started
        results = {}
        for name, target, _ in changed:
            current = group(target, count)
            paths, reprocessed, size, dirty = {}, 0, 0, 0
            tick = time.perf_counter()
            for key, members in sorted(current.items()):
                if members == base.get(key):
                    paths[key] = base_paths[key]
                    continue
                location = path / name / key[0] / str(key[1])
                size += build(location, members, effective, segment)
                reprocessed += len(members)
                dirty += 1
                paths[key] = location
            duration = time.perf_counter() - tick
            evaluations = {route: evaluate([paths[key] for key in sorted(paths) if key[0] == route], effective, queries)
                           for route in ROUTES}
            results[name] = {"seconds": duration, "bytes": size, "dirty_buckets": dirty,
                             "deleted_buckets": len(base.keys() - current.keys()), "rows_reprocessed": reprocessed,
                             "total_members": sum(map(len, current.values())), "routes": evaluations,
                             "equivalence": {route: equivalent(reference[name][route], value) for route, value in evaluations.items()}}
        arms[str(count)] = {"base_seconds": base_elapsed, "base_bytes": base_bytes, "scenarios": results}
        write(output / f"bucket-{count}.json", arms[str(count)])
        print("buckets", count, [(name, r["equivalence"]) for name, r in results.items()], flush=True)
    write(output / "report.json", {"scope": "bounded source sample", "rows": len(rows), "reference": reference,
                                   "arms": arms, "configuration": effective, "sample_sha256": sha(output / "sample.json")})
    seal(output)


def audit_existing(repo, archived, output):
    """Read the sealed indexes and compare all matched scores plus hot timings."""
    verify(archived)
    report = read(archived / "report.json")
    config = read(repo / "config/index-benchmark-v1.json")
    _, manifests = selected(repo, read(repo / "config/clean-retrieval-v1.json"))
    bundle = Path(manifests["bm25f"][1]["input_manifest"])
    rows = sample(bundle)
    archived_sample = read(archived / "sample.json")
    require(archived_sample["input_manifest_sha256"] == sha(bundle)
            and archived_sample["ids"] == [row["id"] for row in rows], "Archived lexical input changed")
    raw = read(repo / "data/raw/mihoyo_obc/zh-cn/p01eb_full_20260824/metadata/inventory.json")
    memberships = {str(row["content_id"]): row["channels"] for row in raw}
    effective, segment = configuration(config["bm25f"], output)
    require(effective == report["configuration"], "Archived lexical scoring contract changed")
    queries = [term_query(text, effective, segment) for text in QUESTIONS]

    def score(searcher, query):
        hits = searcher.search(query, limit=None, optimize=False)
        return sorted((hit["chunk_id"], hit.score) for hit in hits)

    results = {}
    for name, target, _ in scenarios(rows, memberships):
        for route in ROUTES:
            with open_search([archived / "reference" / name / route], effective) as (reference, _):
                expected = [score(reference, query) for query in queries]
            for count in BUCKETS:
                base, current = group(rows, count), group(target, count)
                paths = [archived / str(count) / ("base" if current[key] == base.get(key) else name) / route / str(key[1])
                         for key in sorted(current) if key[0] == route]
                tick = time.perf_counter()
                with open_search(paths, effective) as (reader, _):
                    elapsed_open = time.perf_counter() - tick
                    actual = [score(reader, query) for query in queries]
                    require(actual == expected, f"Full score mismatch: {name}, {route}, {count}")
                    hot = []
                    for _ in range(3):
                        for query in queries:
                            tick = time.perf_counter()
                            hits = reader.search(query, limit=None, optimize=False)
                            heapq.nsmallest(20, ((hit["chunk_id"], hit.score) for hit in hits), key=lambda hit: (-hit[1], hit[0]))
                            hot.append(time.perf_counter() - tick)
                results[f"{name}/{route}/{count}"] = {"status": "bitwise score equality", "matched_scores": sum(map(len, actual)),
                                                      "open_seconds": elapsed_open, "hot_median_seconds": median(hot),
                                                      "hot_p95_seconds": sorted(hot)[math.ceil(len(hot)*.95)-1]}
                print(name, route, count, sum(map(len, actual)), flush=True)
    write(output / "report.json", {"archived_seal_sha256": sha(archived / "complete.json"),
                                   "runner_sha256": sha(__file__), "results": results})
    seal(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    repo = Path(__file__).resolve().parents[2]
    require(output.parent == repo / "data/retrieval" and output.name.startswith("incremental-study-bm25f-"), "Use a fresh named study root")
    output.mkdir()
    if args.audit is None:
        run(repo, output)
    else:
        audit_existing(repo, args.audit.resolve(), output)


if __name__ == "__main__":
    main()
