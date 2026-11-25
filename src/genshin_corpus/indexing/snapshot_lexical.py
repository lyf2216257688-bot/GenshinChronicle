"""Sealed BM25F buckets with one global Whoosh score space per route."""

from collections import Counter, defaultdict
from contextlib import ExitStack
import heapq
import os
from pathlib import Path
import tempfile
import time

from ..collector.run_store import create_json
from ..evidence_io import digest, inside, read, require, sha
from .analyzers import analyze_fields
from .benchmark_lexical import configuration, schema
from .schema import GRANULARITIES, LEXICAL_FIELDS


VERSION = "gc-index-snapshot-bm25f-1"
BUCKETS = 64


def source_bucket(item):
    return int(digest([item[k] for k in ("source", "locale", "content_id")])[:8], 16) >> (32 - (BUCKETS.bit_length() - 1))


def assignments(source):
    occurrences, groups = Counter(), defaultdict(dict)
    for row in source.rows:
        item = row["source_item"]
        source_id = digest(item)
        bucket = source_bucket(item)
        for route in row["granularities"]:
            fields = row["lexical_fields_sha256"]
            identity = (source_id, route, fields)
            key = digest([*identity, occurrences[identity]])
            occurrences[identity] += 1
            groups[route, bucket][key] = row["input_row"]
    return groups


def identity(source, effective):
    return {"input_bundle_id": source.manifest["input_bundle_id"], "counts": source.manifest["counts"],
            "configuration": effective, "buckets_per_route": BUCKETS}


def representations(manifest):
    return {route: digest([VERSION, manifest["input_bundle_id"], route, manifest["configuration"], BUCKETS])
            for route in GRANULARITIES}


def _terms(text, config, segment):
    from whoosh import query

    analyzed = analyze_fields({field: [text] for field in LEXICAL_FIELDS}, config["analyzers"], segmenter=segment)
    clauses = [query.Term(f"{field}__{name}", term,
                          boost=config["field_weights"][field] * config["analyzer_weights"][name])
               for field, channels in analyzed.items() for name, terms in channels.items() for term in sorted(set(terms))]
    return query.Or(clauses)


class LexicalIndex:
    def __init__(self, manifest_path, source, effective, segment):
        self.manifest = read(manifest_path)
        require(self.manifest["version"] == VERSION and self.manifest["status"] == "complete"
                and all(self.manifest[k] == v for k, v in identity(source, effective).items()), "Foreign BM25F index")
        self.segment, self.effective = segment, effective
        self.groups = assignments(source)
        self.refs = {}
        for ref in self.manifest["buckets"]:
            key = (ref["route"], ref["bucket"])
            path = Path(ref["path"])
            require(key in self.groups and key not in self.refs and path.is_absolute()
                    and path.resolve() == path and sha(path) == ref["sha256"], "Invalid BM25F bucket reference")
            meta = read(path)
            require(meta["key"] == list(key) and meta["membership"] == digest(sorted(self.groups[key]))
                    and meta["configuration_sha256"] == digest(effective), "BM25F bucket membership changed")
            require(meta["count"] == len(self.groups[key]) and bool(meta["files"]), "Incomplete BM25F bucket")
            folder = path.parent / "whoosh"
            require({item.name for item in folder.iterdir() if not item.name.endswith("LOCK")} == meta["files"].keys(),
                    "Unexpected BM25F index files")
            for name, checksum in meta["files"].items():
                require(sha(inside(folder, name)) == checksum, "BM25F file binding differs")
            self.refs[key] = ref
        require(self.refs.keys() == self.groups.keys(), "Incomplete BM25F route coverage")
        self.cleanup = ExitStack()
        self.searchers = {}

    def _open_route(self, route):
        from whoosh import index, scoring
        from whoosh.reading import MultiReader
        from whoosh.searching import Searcher

        ids, readers = {}, []
        for key, ref in sorted(self.refs.items()):
            if key[0] != route:
                continue
            ix = index.open_dir(Path(ref["path"]).parent / "whoosh")
            self.cleanup.callback(ix.close)
            reader = ix.reader()
            self.cleanup.callback(reader.close)
            require(reader.is_atomic() and reader.doc_count_all() == reader.doc_count(), "Unsealed BM25F segment")
            stored = [row["chunk_id"] for row in reader.all_stored_fields()]
            require(len(stored) == len(self.groups[key]) and set(stored) == self.groups[key].keys(),
                    "Stored BM25F keys differ")
            readers.append(reader)
            ids.update(self.groups[key])
        if not readers:
            return None, ids
        weights = {f"{field}__{name}_B": self.effective["field_b"][field]
                   for field in LEXICAL_FIELDS for name in self.effective["analyzer_weights"]}
        reader = MultiReader(readers) if len(readers) > 1 else readers[0]
        searcher = Searcher(reader, weighting=scoring.BM25F(K1=self.effective["k1"], **weights), closereader=False)
        self.cleanup.enter_context(searcher)
        return searcher, ids

    def search(self, text, top_k):
        require(type(top_k) is int and top_k > 0, "Invalid BM25F query depth")
        clauses = _terms(text, self.effective, self.segment)
        result = {}
        for route in GRANULARITIES:
            searcher, ids = self.route(route)
            # Collect the full score boundary before applying deterministic ties.
            hits = searcher.search(clauses, limit=None, optimize=False) if searcher is not None else ()
            result[route] = heapq.nsmallest(top_k, ({"input_row": ids[hit["chunk_id"]], "score": hit.score} for hit in hits),
                                            key=lambda row: (-row["score"], row["input_row"]))
        return result

    def route(self, route):
        if route not in self.searchers:
            self.searchers[route] = self._open_route(route)
        return self.searchers[route]

    def close(self):
        self.cleanup.close()


def _write_bucket(folder, key, entries, fields_for, effective, segment):
    from whoosh import index

    with tempfile.TemporaryDirectory(prefix="bucket-", dir=folder.parent) as temporary:
        staged = Path(temporary) / "bucket"
        data = staged / "whoosh"
        data.mkdir(parents=True)
        ix = index.create_in(data, schema())
        try:
            writer = ix.writer(limitmb=64, procs=1)
            try:
                for stable, row in entries.items():
                    analyzed = analyze_fields(fields_for(row), effective["analyzers"], segmenter=segment)
                    columns = {f"{field}__{name}": " ".join(tokens)
                               for field, channels in analyzed.items() for name, tokens in channels.items()}
                    writer.add_document(chunk_id=stable, **columns)
            except BaseException:
                writer.cancel()
                raise
            writer.commit(merge=False)
        finally:
            ix.close()
        create_json(staged / "complete.json", {"key": list(key), "count": len(entries), "membership": digest(sorted(entries)),
                    "configuration_sha256": digest(effective),
                    "files": {file.name: sha(file) for file in data.iterdir() if not file.name.endswith("LOCK")}})
        # Windows can retain a short-lived sharing lock after Whoosh closes
        # its segment files. Keep the already verified bucket and retry its
        # create-only directory promotion under the existing build lock.
        for delay in (.1, .25, .5, 1, 2, None):
            require(not folder.exists(), "BM25F bucket already published")
            try:
                staged.rename(folder)
                break
            except PermissionError as exc:
                if os.name != "nt" or exc.winerror not in {5, 32, 33} or delay is None:
                    raise
                time.sleep(delay)


def build_lexical(source, output, effective, segment, *, previous=None, disk_guard=lambda: None):
    output = Path(output).resolve()
    require(not output.is_relative_to(source.path.parent) and not source.path.is_relative_to(output),
            "Use a separate BM25F root")
    groups = assignments(source)
    inherited = {}
    if previous is not None:
        root = Path(previous[0]).resolve().parent
        require(not output.is_relative_to(root) and not root.is_relative_to(output), "Keep the previous BM25F index immutable")
        if read(previous[0])["configuration"] == effective:
            old = LexicalIndex(*previous, effective, segment)
            inherited = {key: ref for key, ref in old.refs.items()
                         if key in groups and groups[key].keys() == old.groups[key].keys()}
    plan = {**identity(source, effective), "input_manifest": str(source.path), "input_sha256": sha(source.path),
            "prior_buckets_sha256": digest(list(inherited.values()))}
    output.mkdir(parents=True, exist_ok=True)
    plan_path = output / "plan.json"
    if not plan_path.exists():
        create_json(plan_path, plan)
    require(read(plan_path) == plan, "BM25F resume plan differs")
    target = output / "manifest.json"
    if target.exists():
        return LexicalIndex(target, source, effective, segment).manifest
    refs = []
    for key, entries in sorted(groups.items()):
        if key in inherited:
            refs.append(inherited[key])
            continue
        route, bucket = key
        folder = output / f"{route}-{bucket:02d}"
        if not folder.exists():
            disk_guard()
            _write_bucket(folder, key, entries, lambda row: source.document(row)["projected"]["lexical_fields"], effective, segment)
        path = folder / "complete.json"
        refs.append({"route": route, "bucket": bucket, "path": str(path), "sha256": sha(path)})
    require(sha(source.path) == plan["input_sha256"], "BM25F input manifest changed")
    value = {"version": VERSION, "status": "complete", **plan, "buckets": refs,
             "reused_buckets": len(inherited), "built_buckets": len(groups) - len(inherited)}
    with tempfile.TemporaryDirectory(prefix="validate-", dir=output) as temporary:
        draft = Path(temporary) / "manifest.json"
        create_json(draft, value)
        with ExitStack() as cleanup:
            index = LexicalIndex(draft, source, effective, segment)
            cleanup.callback(index.close)
            for route in GRANULARITIES:
                index._open_route(route)
    create_json(target, value)
    return value


def lexical_runtime(repo, scratch):
    return configuration(read(Path(repo) / "config/index-benchmark-v1.json")["bm25f"], Path(scratch))
