"""Pinned S0 query wiring over the installed flat and versioned worker entry points."""

from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import ExitStack
from functools import cached_property
from pathlib import Path
import time

from ..evidence_io import digest, inside, read, require, strict_json, write
from ..indexing import snapshot_lexical, snapshot_vectors
from ..indexing.benchmark_io import attempt, disk_guard, ram_guard, resources
from ..model_calls import locked
from ..retrieval.clean_based.indexes import selected
from ..retrieval.clean_based.query_v2 import WEIGHTS, fuse_v2, validate_ancestry, validate_profile_v2
from ..retrieval.clean_based.rerank_handoff import validate_selection
from ..retrieval.clean_based.rerank_v2 import rank_v2, scoring_input
from ..retrieval.clean_based.runtime_v2 import JsonWorker, code_identity
from ..retrieval.clean_based.snapshot import VERSION as RELEASE_VERSION, open_release
from ..retrieval.clean_based.snapshot_source import SnapshotReader
from ..retrieval.clean_based.source import FileBinding, InputReader


class CancellableWorker(JsonWorker):
    def __init__(self, *args, check_cancel, **kwargs):
        self.check_cancel = check_cancel
        super().__init__(*args, **kwargs)

    def receive(self):
        incoming = self.pool.submit(self.process.stdout.readline)
        deadline = time.monotonic() + self.timeout
        while True:
            self.check_cancel()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Local worker deadline elapsed")
            try:
                line = incoming.result(timeout=min(remaining, 0.25))
                break
            except FutureTimeout:
                continue
        require(bool(line), "Worker exited unexpectedly; inspect stderr")
        value = strict_json(line)
        require(value.get("status") != "failed", "Worker failed: " + str(value))
        return value


CATALOG = "config/mcp-live-v1.json"


def production_release(repo):
    releases = Releases(Path(repo).resolve())
    return releases.selected(releases.active)


class Releases:
    def __init__(self, repo):
        self.repo = repo
        config = read(repo / CATALOG)
        require(config["version"] == "gc-mcp-live-1", "Unknown MCP release catalog")
        self.active = config["active"]
        self.points = {key: Release(repo, key, spec, config, self.use) for key, spec in config["releases"].items()}
        require(self.active in self.points and not self.points[self.active].flat, "Invalid production release")

    def use(self, key):
        for name, release in self.points.items():
            if name != key:
                release.close()

    def selected(self, key):
        require(key in self.points, "Unknown or archived corpus release")
        return self.points[key]

    def resolve(self, key, binding):
        release = self.selected(key)
        require(release.binding == binding, "Study is archived or bound to a different implementation")
        return release

    def catalog(self):
        return [{"release": key, "raw_run": release.spec["raw_run"], "kind": release.spec["kind"]}
                for key, release in self.points.items()]

    def close(self):
        for release in self.points.values():
            release.close()


class Release:
    def __init__(self, repo, key, spec, catalog, use):
        self.repo, self.key, self.spec = repo, key, spec
        self.catalog = catalog
        self.use = use
        self.files = FileBinding()
        self.flat = spec["kind"] == "flat"
        require(spec["kind"] in {"flat", "release"}, "Unsupported release kind")
        package = Path(__file__).resolve().parent
        deps = [
            "evidence_io.py", "model_calls.py", "generation/clean_evidence.py", "rag/clean_research_answer.py",
            "rag/clean_research_catalog.py",
            "mcp_offline/study.py", "mcp_offline/protocol.py", "clean/models.py", "clean/reader.py",
            "clean/markup.py", "chunking/reader.py", "chunking/dialogue.py", "indexing/benchmark_io.py",
            "indexing/inputs.py", "indexing/benchmark_dense.py", "indexing/benchmark_lexical.py",
            "indexing/snapshot_inputs.py", "indexing/snapshot_vectors.py", "indexing/snapshot_encoder.py",
            "indexing/snapshot_lexical.py", "retrieval/clean_based/snapshot.py",
            "retrieval/clean_based/snapshot_source.py",
        ]
        upstream = code_identity()
        for name, expected in upstream.items():
            self.files.bind(package.parent / "retrieval/clean_based" / name, expected)
        code = {**upstream, **{path.relative_to(package.parent).as_posix(): self.files.bind(path)
                                     for path in [*package.glob("*.py"), *(package.parent / p for p in deps)]}}
        self.code = code
        self.binding = digest([catalog["version"], key, spec, catalog["retrieval"], catalog["reranking"], code])

    def _pin(self, ref):
        path = inside(self.repo, ref["path"])
        self.files.bind(path, ref["sha256"])
        return path

    @cached_property
    def settings(self):
        self.config_path = self._pin(self.catalog["retrieval"])
        config = read(self.config_path)
        validate_profile_v2(config, "S0")
        self.selection_path = self._pin(self.catalog["reranking"])
        official_selection = read(self.selection_path)
        self.artifact = self._pin(self.spec["artifact"])
        if self.flat:
            require(self.artifact == inside(self.repo, config["selection"]), "Foreign flat index selector")
            _, self.manifests = selected(self.repo, config)
            index = read(self.artifact)
            for name, ref in (("dense", index["dense"]), ("bm25f", index["lexical"])):
                self.files.bind(self.manifests[name][0], ref["manifest_sha256"])
            self.input_manifest = Path(self.manifests["dense"][1]["input_manifest"])
            self.files.bind(self.input_manifest, self.spec["input_sha256"])
            self.bundle_id = official_selection["input_bundle_id"]
            require(self.bundle_id == index["source"]["input_bundle_id"], "Foreign flat index bundle")
        else:
            self._pin({"path": config["selection"], "sha256": config["selection_sha256"]})
            self.descriptor = read(self.artifact)
            require(self.descriptor["version"] == RELEASE_VERSION and self.descriptor["status"] == "complete",
                    "Unsupported release descriptor")
            self.bundle_id = self.descriptor["input_bundle_id"]
            require(self.bundle_id == self.spec["input_bundle_id"], "Unexpected release input identity")
            self.stores = {}
            for key in ("input", "vectors", "bm25f"):
                ref = self.descriptor[key]
                self.files.bind(ref["path"], ref["sha256"])
                if key != "input":
                    self.stores[key] = read(ref["path"])
        self.selection = validate_selection({**official_selection, "input_bundle_id": self.bundle_id})
        self.files.check()
        return config

    @cached_property
    def prepared(self):
        started = time.perf_counter()
        self.use(self.key)
        self.settings
        if self.flat:
            reader = InputReader(self.input_manifest)
        else:
            descriptor, source, stores, binding = open_release(self.artifact)
            require(descriptor == self.descriptor and stores == self.stores, "Foreign prepared release")
            self.files.files.update(binding.files)
            require(source.current_raw["run_id"] == self.spec["raw_run"], "Foreign Raw observation")
            reader = SnapshotReader(source)
        try:
            require(self.bundle_id == reader.bundle_id, "Foreign Reader selection")
            self.files.check()
            self.prepare_seconds = time.perf_counter() - started
            return reader
        except BaseException:
            if self.flat:
                reader.close()
            raise

    @property
    def reader(self):
        self.files.check()
        reader = self.prepared
        reader.binding.check()
        return reader

    def summary(self):
        return {"binding": self.binding, "release": self.key, "raw_run": self.spec["raw_run"]}

    def close(self):
        reader = self.__dict__.pop("prepared", None)
        if self.flat and reader is not None:
            reader.close()

    def _worker(self, python, module, args, output, check):
        return CancellableWorker(self.repo, python, module, args, output,
                                 timeout=self.settings["worker_timeout_seconds"], check_cancel=check)

    def _recall(self, channel, query, output, check):
        started = time.perf_counter()
        python = self.settings["lexical_python" if channel == "bm25f" else "dense_python"]
        args = ["--repo", str(self.repo)]
        if self.flat:
            module = "genshin_corpus.retrieval.clean_based.worker_v2"
            expected = self.manifests[channel][1]["representations"]
            args.extend(["--config", str(self.config_path)])
            request = {"query": query.value(), "verify_reference": False}
        else:
            lexical = channel == "bm25f"
            module = ("genshin_corpus.mcp_live.lexical_worker" if lexical
                      else "genshin_corpus.retrieval.clean_based.snapshot")
            expected = (snapshot_lexical.representations(self.stores["bm25f"]) if channel == "bm25f"
                        else snapshot_vectors.representations(self.stores["vectors"], channel))
            if not lexical:
                args.append("worker")
            args.extend(["--release", str(self.artifact)])
            request = {"text": query.search_text}
        if self.flat or channel != "bm25f":
            args.extend(["--channel", channel])
        args.extend(["--scratch", str(output / (channel + "-scratch"))])
        with self._worker(python, module, args,
                          output / (channel + ".stderr.log"), check) as worker:
            opened = time.perf_counter()
            require(worker.ready["representations"] == expected, "Foreign worker representations")
            if self.flat:
                require(worker.ready["input_bundle_id"] == self.bundle_id, "Foreign worker input bundle")
            else:
                require(worker.ready["release_sha256"] == self.spec["artifact"]["sha256"], "Foreign worker release")
            stage = worker.request({**request, "depth": self.settings["depth"]})
            finished = time.perf_counter()
        usage = {"prepare_seconds": opened - started, "search_seconds": finished - opened,
                 "exit_seconds": time.perf_counter() - finished, "resources": stage["resources"], "exit_confirmed": True}
        if self.flat:
            require(stage["query"] == query.value() and all(h["channel"] == channel
                    and h["representation_id"] == expected[h["granularity"]] for h in stage["hits"]), "Foreign worker hit")
            return stage["hits"], usage
        require(stage["routes"].keys() == expected.keys(), "Missing worker routes")
        hits = [{"question_id": query.turn_id, "query_id": query.query_id, "round": query.round, "kind": query.kind,
                 "channel": channel, "granularity": route, "rank": rank, "raw_score": entry["score"],
                 "representation_id": expected[route], "input_row": entry["input_row"]}
                for route, rows in stage["routes"].items() for rank, entry in enumerate(rows, 1)]
        return hits, usage

    def _rank(self, candidates, output, check):
        if not candidates["candidates"]:
            return rank_v2(candidates, None, self.selection), {}
        path = output / "scoring-input.json"
        write(path, scoring_input(candidates, self.selection))
        started = time.perf_counter()
        with self._worker(self.selection["python"], "genshin_corpus.retrieval.clean_based.rerank_worker_v2",
                          ["--repo", str(self.repo), "--scratch", str(output / "reranking-scratch")],
                          output / "reranking.stderr.log", check) as worker:
            opened = time.perf_counter()
            require(worker.ready["binding"] == self.selection["score_binding"], "Foreign local reranker")
            score = worker.request({"action": "score", "input": str(path), "budget": 2048})
            finished = time.perf_counter()
        usage = {"prepare_seconds": opened - started, "score_seconds": finished - opened,
                 "exit_seconds": time.perf_counter() - finished, "resources": score["resources"], "exit_confirmed": True}
        write(output / "scores.json", score)
        return rank_v2(candidates, score, self.selection), usage

    def _reader_fits(self):
        return resources()["available_ram_bytes"] >= self.settings["minimum_available_ram_gib"] * 1024 ** 3

    def search(self, query, parents, evidence_refs, root, check):
        return self.run(query, attempt(root, "query"), parents=parents, evidence_refs=evidence_refs, check=check)

    def run(self, query, output, *, parents=(), evidence_refs=(), check=lambda: None):
        validate_ancestry(query, parents, evidence_refs)
        started = time.perf_counter()
        warm = "prepared" in self.__dict__
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        usage, hits = {}, []
        try:
            self.use(self.key)
            config = {**self.settings, "combination": query.combination, "channel_weights": WEIGHTS[query.combination]}
            require(not self.flat or query.combination == "S0", "Archived flat release only supports S0")
            if warm and not self._reader_fits():
                self.close()
                warm = False
            usage["before"] = ram_guard(config)
            disk_guard(output, config)
            check()
            with ExitStack() as stack:
                try:
                    stack.enter_context(locked(self.repo / ".local/clean-generation-gpu.lock"))
                except OSError as exc:
                    raise ValueError("GC local compute is busy") from exc
                for channel in WEIGHTS[query.combination]:
                    check()
                    if warm and not self._reader_fits():
                        self.close()
                        warm = False
                    ram_guard(config)
                    rows, usage[channel] = self._recall(channel, query, output, check)
                    hits.extend(rows)
                reader = self.reader
                check()
                tick = time.perf_counter()
                if not self.flat:
                    # Unified input rows are grouped by source. Use that order for
                    # readback so the bounded source cache can serve adjacent hits.
                    for hit in hits:
                        number = hit["input_row"]
                        require(type(number) is int and 0 <= number < len(reader.source.rows), "Invalid versioned hit row")
                        hit["chunk_id"] = reader.source.rows[number]["chunk_id"]
                    read_order = sorted(hits, key=lambda hit: hit["input_row"])
                else:
                    read_order = hits
                documents = {}
                for hit in read_order:
                    check()
                    cid = hit["chunk_id"]
                    if cid not in documents:
                        documents[cid] = reader.document(hit["input_row"], cid)
                    document = documents[cid]
                    require(document["input_row"] == hit["input_row"] and all(
                        document["row"][k] == v for k, v in hit.get("index_mapping", {}).items()), "Index source mismatch")
                usage["document_read_seconds"] = time.perf_counter() - tick
                tick = time.perf_counter()
                candidates = fuse_v2(query, hits, documents, config, input_bundle_id=reader.bundle_id, code=self.code)
                if self.flat:
                    candidates["raw_hits"] = hits
                usage["fusion_seconds"] = time.perf_counter() - tick
                write(output / "candidates.json", candidates)
                check()
                reader.binding.check()
                if not self._reader_fits():
                    reader = None
                    self.close()
                    usage["reader_released_before_rerank"] = True
                ram_guard(config)
                ranked, usage["bge"] = self._rank(candidates, output, check)
            self.files.check()
            if reader is not None:
                reader.binding.check()
            check()
            write(output / "ranked.json", ranked)
            write(output / "run.json", {"status": "complete", **self.summary(), "query": query.value(),
                                      "ranked_digest": digest(ranked), "reader_reused": warm,
                                      "reader_prepare_seconds": 0 if warm else self.prepare_seconds, "usage": usage,
                                      "seconds": time.perf_counter() - started, "resources": resources(), "remote_calls": 0})
            return ranked
        except Exception as exc:
            write(output / "failure.json", {"status": "failed", **self.summary(), "query": query.value(),
                                            "usage": usage, "resources_at_failure": resources(),
                                            "error_type": type(exc).__name__, "detail": str(exc)[:512]})
            raise
