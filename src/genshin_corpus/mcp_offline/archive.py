"""Read only the registered historical ranking and its hash-addressed source ranges."""
from functools import cached_property
from pathlib import Path

from ..evidence_io import digest, inside, read, require, sha
from ..generation.clean_evidence import EvidenceReader, validate_citations
from ..retrieval.clean_based.query_v2 import ResearchQuery, validate_profile_v2
from ..retrieval.clean_based.source import FileBinding, InputReader


class FrozenRows(InputReader):
    """The frozen ranking attests the row copy. Reuse the flat reader's locator checks."""

    def __init__(self, repo, manifest, binding, raw_run):
        self.manifest = manifest
        self.binding = binding
        self.rows = {}
        self.raw_run = raw_run
        for key in ("chunk", "clean"):
            path = inside(repo, manifest["source_manifests"][key])
            binding.bind(path, manifest["binding"][key + "_manifest_sha256"])
            if key == "chunk":
                self.root = path.parent
            else:
                self.clean_root = path.parent.parent
                clean = read(path)
                require(clean["status"] == "complete" and clean["schema_version"] == "gc-clean-1",
                        "Incomplete Clean input")
                self.source_item = {k: clean[k] for k in ("source", "locale")}
                self.records = {e["observation_id"]: e for e in clean["records"]}
                require(len(self.records) == len(clean["records"]), "Duplicate Clean observation")
        binding.check()

    def add(self, ranked):
        for candidate in ranked["candidates"]:
            number, row = candidate["input_row"], candidate["source"]
            require(type(number) is int and 0 <= number < self.manifest["counts"]["documents"]
                    and row["input_snapshot_id"] == self.manifest["input_snapshot_id"]
                    and self.rows.setdefault(number, row) == row, "Frozen input row differs")
            self._validate_row(row)

    def row(self, number):
        return self.rows[number]

    def read_clean(self, row):
        record = super().read_clean(row)
        require(record["input"]["raw_run_id"] == self.raw_run, "Foreign Raw observation")
        return record


class Archive:
    def __init__(self, repo):
        self.repo = Path(repo).resolve()
        self.files = FileBinding()
        path = self.repo / "config/mcp-offline-v1.json"
        catalog_hash = self.files.bind(path)
        self.catalog = read(path)
        require(self.catalog["version"] == "gc-mcp-frozen-catalog-1", "Unknown catalog version")
        self.cases = {c["id"]: c for c in self.catalog["cases"]}
        require(len(self.cases) == len(self.catalog["cases"]), "Duplicate frozen case")
        self.groups = {}
        self.originals = {}
        # Bind the implementation used to read, assemble, expose and replay evidence.
        package = Path(__file__).resolve().parent
        modules = [*package.glob("*.py"), *(package.parent / p for p in (
            "generation/clean_evidence.py", "rag/clean_research_answer.py", "evidence_io.py",
            "clean/models.py", "clean/reader.py", "clean/markup.py",
            "chunking/reader.py", "chunking/dialogue.py",
            "retrieval/clean_based/source.py", "retrieval/clean_based/query_v2.py",
            "retrieval/clean_based/core.py", "indexing/inputs.py"))]
        self.binding = digest([catalog_hash, {p.relative_to(package.parent).as_posix(): self.files.bind(p)
                                              for p in modules}])

    def pinned(self, ref):
        path = inside(self.repo, ref["path"])
        self.files.bind(path, ref["sha256"])
        result = read(path)
        self.files.check()
        return result

    @cached_property
    def reader(self):
        inputs = self.pinned(self.catalog["input"])
        assets = {k: self.pinned(v) for k, v in self.catalog["assets"].items()}
        self.profile = assets["retrieval"]
        validate_profile_v2(self.profile, "S0")
        self.selection = digest(assets["reranking"])
        require(assets["generation"]["version"] == "gc-g5-official-4"
                and assets["reranking"]["default_profile"] == "bge_structural"
                and assets["reranking"]["input_bundle_id"] == inputs["input_bundle_id"]
                and assets["index"]["source"]["input_bundle_id"] == inputs["input_bundle_id"]
                and inputs["status"] == "complete", "Frozen operating point differs")
        for kind in ("dense", "lexical"):
            ref = assets["index"][kind]
            index = self.pinned({"path": ref["manifest"], "sha256": ref["manifest_sha256"]})
            require(index["input_bundle_id"] == inputs["input_bundle_id"]
                    and inside(self.repo, index["input_manifest"]) == inside(self.repo, self.catalog["input"]["path"]),
                    "Index input binding differs")
        return FrozenRows(self.repo, inputs, self.files, self.catalog["raw_run"])

    def group(self, case_id, key):
        identity = case_id, key
        self.files.check()
        if identity not in self.groups:
            case = self.cases[case_id]
            require(key in case["groups"], "Query outside frozen case")
            reader = self.reader
            ranked = self.pinned(case["groups"][key])
            q = ResearchQuery.parse(ranked["query"])
            if key != "original":
                require(q.value() == case["follow_ups"][key], "Frozen follow-up changed")
            run = ranked["reranking_run"]
            require(q.query_id == key and q.original_question == case["question"] and q.combination == "S0"
                    and ranked["query_digest"] == digest(q.value()) and ranked["profile"] == self.profile
                    and ranked["input_bundle_id"] == reader.bundle_id
                    and bool(ranked["candidates"]) == (ranked["status"] == "complete")
                    and (run["status"] == "empty" if not ranked["candidates"] else
                         run["status"] == "complete" and run["selection_id"] == self.selection
                         and run["profile"] == "bge_structural" and run["rerank_text"] == q.rerank_text
                         and run["mmr_applied"] is False)
                    and all(hit["query_id"] == key and hit["question_id"] == q.turn_id
                            for c in ranked["candidates"] for hit in c["hits"]),
                    "Incorrect frozen ranking binding")
            reader.add(ranked)
            evidence = EvidenceReader(reader)
            handles = evidence.register(ranked)
            require(len(handles) == len(ranked["candidates"]), "Duplicate ranked candidate")
            self.groups[identity] = (ranked, handles)
        return self.groups[identity]

    def initial(self, case_id):
        if case_id not in self.originals:
            holder = self.pinned(self.cases[case_id]["initial_sources"])
            packet = holder["packet"]
            validate_citations(packet, packet["citations"])
            require(digest(holder["assembly_context"]) == packet["context_digest"], "Golden context differs")
            self.originals[case_id] = holder["assembly_context"], packet
        return self.originals[case_id]
