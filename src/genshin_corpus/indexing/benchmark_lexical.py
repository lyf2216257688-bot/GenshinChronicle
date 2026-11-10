"""Whoosh3 baseline with six logical fields and three separate analyzer channels."""
from collections import Counter
from importlib.metadata import version
from pathlib import Path
import platform
import time

from ..build_runtime import atomic_json
from ..evidence_io import digest, read, require, sha
from .analyzers import ANALYZER_VERSION, analyze_fields
from .benchmark_io import check_artifacts, disk_guard, event, mapping, routes
from .inputs import iter_inputs
from .schema import LEXICAL_FIELDS


def configuration(config, root):
    import jieba
    tokenizer = jieba.Tokenizer()
    dictionary = Path(tokenizer.get_dict_file().name)
    root.mkdir(parents=True, exist_ok=True)
    tokenizer.tmp_dir = str((root / "jieba-cache").resolve())
    Path(tokenizer.tmp_dir).mkdir(exist_ok=True)
    tokenizer.initialize()
    require(config["jieba_mode"] == "precise" and config["hmm"] is False, "Unsupported Jieba contract")

    def segment(text):
        return [term.casefold() for term in tokenizer.cut(text, HMM=False)
                if any(char.isalnum() for char in term) and not any(char.isspace() for char in term)]

    result = {k: config[k] for k in ("field_weights", "field_b", "k1", "analyzer_weights", "jieba_mode", "hmm")}
    require(set(result["field_weights"]) == set(LEXICAL_FIELDS), "Baseline requires all six fields")
    require(set(result["analyzer_weights"]) == {"han_unigram", "han_bigram", "word"}
            and all(value > 0 for value in result["analyzer_weights"].values()), "Invalid analyzer weights")
    result["analyzers"] = {"version": ANALYZER_VERSION, "names": list(result["analyzer_weights"]),
                           "segmenter": {"implementation": "jieba.precise", "version": version("jieba"),
                                         "configuration_sha256": digest({"hmm": False, "mode": "precise"}),
                                         "dictionary_sha256": sha(dictionary)}}
    result.update(python=platform.python_version(), whoosh3=version("Whoosh3"),
                  scoring="Whoosh3 BM25F, field-specific TF saturation, weighted sum of field/channel terms",
                  query="OR of unique analyzed literal terms, no query parser or syntax expansion")
    return result, segment


def schema():
    from whoosh.analysis import RegexTokenizer
    from whoosh.fields import ID, NUMERIC, STORED, TEXT, Schema
    fields = {"chunk_id": ID(stored=True, unique=True), "input_row": NUMERIC(stored=True), "payload": STORED}
    for field in LEXICAL_FIELDS:
        for analyzer in ("han_unigram", "han_bigram", "word"):
            fields[field + "__" + analyzer] = TEXT(analyzer=RegexTokenizer(r"\S+"), phrase=False)
    return Schema(**fields)


def payload(number, row):
    return {**mapping(number, row), "lexical_fields": row["lexical_fields"]}


def _stored(index):
    """Recover from the committed index generation, including commit-before-journal crashes."""
    observed = {}
    with index.reader() as reader:
        for doc in reader.all_stored_fields():
            require(doc["input_row"] not in observed, "Duplicate committed Whoosh input row")
            require(doc["chunk_id"] == doc["payload"]["chunk_id"], "Whoosh stored ID differs")
            observed[doc["input_row"]] = digest(doc["payload"])
    return observed


def build(input_manifest, root, config, *, stop_after=None):
    from whoosh import index
    root = Path(root)
    effective, segment = configuration(config["bm25f"], root)
    bundle = read(input_manifest)
    binding = digest({"configuration": effective, "input": bundle["input_bundle_id"]})
    plan = {"configuration": effective, "binding": binding, "input_bundle_id": bundle["input_bundle_id"]}
    plan_path = root / "plan.json"
    if plan_path.exists():
        require(read(plan_path) == plan, "BM25F resume binding changed")
    else:
        atomic_json(plan_path, plan)
    if (root / "manifest.json").exists():
        return validate(input_manifest, root, config)
    started, additions = time.monotonic(), 0
    for level in ("micro", "macro"):
        folder = root / level
        folder.mkdir(exist_ok=True)
        ix = index.open_dir(folder) if index.exists_in(folder) else index.create_in(folder, schema())
        committed = _stored(ix)
        expected_count = 0
        writer, pending = None, 0
        try:
            for number, row in enumerate(iter_inputs(input_manifest)):
                if level not in row["granularities"]:
                    continue
                expected_count += 1
                stored = payload(number, row)
                if number in committed:
                    require(committed.pop(number) == digest(stored), "Whoosh committed source mapping changed")
                    continue
                if writer is None:
                    disk_guard(root, config)
                    writer = ix.writer(limitmb=config["bm25f"]["writer_mb"], procs=1)
                analyzed = analyze_fields(row["lexical_fields"], effective["analyzers"], segmenter=segment)
                terms = {field + "__" + analyzer: " ".join(tokens)
                         for field, channels in analyzed.items() for analyzer, tokens in channels.items()}
                writer.add_document(chunk_id=row["chunk_id"], input_row=number, payload=stored, **terms)
                pending += 1
                if pending >= config["bm25f"]["commit_rows"]:
                    writer.commit(merge=False)
                    writer, pending = None, 0
                    additions += 1
                    event(root, "bm25f_progress", route=level, processed_rows=expected_count,
                          total=bundle["counts"][level], elapsed_seconds=time.monotonic() - started)
                    if stop_after is not None and additions >= stop_after:
                        return {"status": "INTERRUPTED_FOR_TEST", "route": level}
            require(not committed, "Unexpected committed Whoosh rows")
            if writer is not None:
                writer.commit(merge=False)
                writer = None
        finally:
            if writer is not None:
                writer.cancel()
            ix.close()
        require(expected_count == bundle["counts"][level], "BM25F route count mismatch")
    candidate = {"version": "gc-whoosh3-bm25f-1", "status": "complete", **plan,
                 "input_manifest": str(Path(input_manifest).resolve()), "counts": bundle["counts"],
                 "representations": routes(effective, input_manifest, "bm25f"),
                 "elapsed_seconds": time.monotonic() - started,
                 "files": {p.relative_to(root).as_posix(): sha(p) for level in ("micro", "macro")
                           for p in (root / level).iterdir() if p.is_file() and not p.name.endswith("LOCK")}}
    validate(input_manifest, root, config, candidate=candidate)
    atomic_json(root / "manifest.json", candidate)
    return candidate


def validate(input_manifest, root, config, *, candidate=None):
    from whoosh import index
    root = Path(root)
    manifest = candidate or read(root / "manifest.json")
    effective, _ = configuration(config["bm25f"], root)
    bundle = read(input_manifest)
    require(manifest["version"] == "gc-whoosh3-bm25f-1" and manifest["status"] == "complete", "Incomplete BM25F")
    require(manifest["configuration"] == effective, "BM25F runtime/configuration changed")
    require(manifest["binding"] == digest({"configuration": effective, "input": bundle["input_bundle_id"]})
            and manifest["input_bundle_id"] == bundle["input_bundle_id"], "Foreign BM25F input")
    require(manifest["representations"] == routes(effective, input_manifest, "bm25f"), "BM25F route identity mismatch")
    require(manifest["counts"] == bundle["counts"], "BM25F manifest count mismatch")
    check_artifacts(root, manifest["files"])
    counts = Counter()
    for level in ("micro", "macro"):
        ix = index.open_dir(root / level)
        try:
            stored = _stored(ix)
            for number, row in enumerate(iter_inputs(input_manifest)):
                if level in row["granularities"]:
                    require(stored.pop(number, None) == digest(payload(number, row)), "Whoosh/source coverage mismatch")
                    counts[level] += 1
            require(not stored and counts[level] == bundle["counts"][level], "BM25F route coverage mismatch")
        finally:
            ix.close()
    result = {"status": "PASS", "memberships": dict(counts), "binding": manifest["binding"]}
    atomic_json(root / "validation.json", result)
    return result


def search(root, text, *, granularity, top_k=20, config):
    from whoosh import index, query, scoring
    root = Path(root)
    require(granularity in {"micro", "macro"} and type(top_k) is int and top_k > 0, "Invalid search options")
    manifest = read(root / "manifest.json")
    effective, segment = configuration(config["bm25f"], root)
    require(manifest["status"] == "complete" and manifest["configuration"] == effective, "BM25F search contract differs")
    check_artifacts(root, manifest["files"])
    fields = {field: [text] for field in LEXICAL_FIELDS}
    analyzed = analyze_fields(fields, effective["analyzers"], segmenter=segment)
    clauses = [query.Term(field + "__" + name, term,
                          boost=effective["field_weights"][field] * effective["analyzer_weights"][name])
               for field, channels in analyzed.items() for name, tokens in channels.items() for term in sorted(set(tokens))]
    if not clauses:
        return []
    lengths = {field + "__" + name + "_B": effective["field_b"][field]
               for field in LEXICAL_FIELDS for name in effective["analyzer_weights"]}
    ix = index.open_dir(root / granularity)
    try:
        with ix.searcher(weighting=scoring.BM25F(K1=effective["k1"], **lengths)) as searcher:
            hits = searcher.search(query.Or(clauses), limit=top_k)
            return [{"score": hit.score, **hit["payload"]} for hit in hits]
    finally:
        ix.close()
