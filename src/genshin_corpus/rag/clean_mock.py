"""Small explicit Mock corpus/transport; no model, GPU, credentials or network."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import time

from ..clean import clean_obc_detail
from ..chunking import ChunkProfile, adapt_record, chunk_parent
from ..evidence_io import digest, read, require, seal, sha, verify, write
from ..generation.clean_chat import MODEL, encoded
from ..indexing import ChunkInputSource, prepare_inputs
from ..retrieval.clean_based.query_v2 import fuse_v2
from ..retrieval.clean_based.rerank_v2 import rank_v2, scoring_input
from .clean_store import put


def fixture_inputs(repo, output):
    """Reuse the checked-in tiny source and actual Clean/Chunk/index readers.
    All generated material is confined to this new ignored G4 root.
    """
    source = Path(repo) / "tests/indexing/fixtures/source.json"
    output = Path(output)
    if (output / "complete.json").exists():
        verify(output)
        require(read(output / "source-binding.json")["sha256"] == sha(source), "Mock fixture source changed")
        return output / "inputs/manifest.json"
    require(not output.exists(), "Partial Mock corpus; preserve it and use a new G4 data root")
    fixture = read(source)
    entries, parents, chunks, graphs = [], [], [], []
    for page in fixture["records"]:
        for module in page["modules"]:
            for component in module["components"]:
                component["data"] = json.dumps(component["data"], ensure_ascii=False)
        body = encoded({"retcode": 0, "data": {"page": page}})
        record = clean_obc_detail(body, artifact_path="synthetic-fixture.json", artifact_sha256=sha256(body).hexdigest(),
                                 locale=fixture["locale"], raw_run_id="g4-synthetic-fixture", content_id=page["id"])
        path = "records/" + record["observation_id"] + ".json"
        write(output / "clean" / path, record)
        entries.append({"content_id": page["id"], "observation_id": record["observation_id"], "record_path": path,
                        "record_sha256": sha(output / "clean" / path), "status": "complete"})
        adapted = adapt_record(record)
        parents.extend(adapted["parents"]); graphs.extend(adapted["graphs"])
        for parent in adapted["parents"]:
            chunks.extend(chunk_parent(parent, len, ChunkProfile("g4-fixture", 60, 100, 180, 250)))
    clean = output / "clean/metadata/manifest.json"
    write(clean, {"schema_version": "gc-clean-1", "status": "complete", "source": fixture["source"],
                  "locale": fixture["locale"], "records": entries})
    chunk_root = output / "chunks"; chunk_root.mkdir()
    for name, values in (("parents.jsonl", parents), ("graphs.jsonl", graphs), ("chunks-balanced.jsonl", chunks)):
        with (chunk_root / name).open("xb") as f:
            for value in values: f.write(encoded(value) + b"\n")
    write(chunk_root / "report.json", {"status": "complete", "record_count": len(entries),
          "policy_version": "gc-story-admission-pilot-4", "parents": len(parents), "graphs": len(graphs),
          "profiles": {"balanced": {"chunks": len(chunks)}}})
    write(chunk_root / "manifest.json", {"status": "complete", "schema_version": "gc-chunking-pilot-2",
          "clean_manifest": str(clean), "clean_manifest_sha256": sha(clean), "sources": entries,
          "selection": {"content_ids": [e["content_id"] for e in entries]},
          "artifacts": {name: sha(chunk_root / name) for name in
                        ("parents.jsonl", "graphs.jsonl", "chunks-balanced.jsonl", "report.json")}})
    prepare_inputs(ChunkInputSource(chunk_root / "manifest.json"), output / "inputs")
    write(output / "source-binding.json", {"mode": "MOCK", "sha256": sha(source), "model_inference": 0})
    seal(output)
    return output / "inputs/manifest.json"


class FixturePipeline:
    """Deterministic fixture hits/scores, actual V2 fusion/structural ordering."""
    def __init__(self, reader, repo):
        self.reader, self.repo = reader, Path(repo)

    def run(self, query):
        docs = {d["chunk"]["chunk_id"]: d for d in
                (self.reader.document(i) for i in range(len(self.reader.offsets)))}
        hits = []
        for granularity in ("micro", "macro"):
            members = [d for d in docs.values() if granularity in d["row"]["granularities"]]
            for n, doc in enumerate(members, 1):
                for channel in ("bm25f", "dense"):
                    hits.append({"question_id": query.turn_id, "query_id": query.query_id, "round": query.round,
                        "kind": query.kind, "channel": channel, "granularity": granularity, "rank": n,
                        "raw_score": 1 / n, "representation_id": "MOCK-" + channel + granularity,
                        "input_row": doc["input_row"], "chunk_id": doc["chunk"]["chunk_id"]})
        profile = {"combination": "S0", "fusion": "f1_max", "depth": 50, "candidate_limit": 100,
                   "rrf_k": 60, "channel_weights": {"bm25f": .5, "dense": .5}, "structural_cleanup": True}
        candidates = fuse_v2(query, hits, docs, profile, input_bundle_id=self.reader.bundle_id, code={"mode": "MOCK"})
        selection = deepcopy(read(self.repo / "config/reranking-selected-v1.json"))
        selection["input_bundle_id"] = self.reader.bundle_id
        selection["score_binding"]["files"] = {"synthetic": "MOCK_NO_MODEL_INFERENCE"}
        score = {"binding": selection["score_binding"], "candidate_set_sha256": digest(scoring_input(candidates, selection)),
                 "scores": {c["chunk_id"]: float(len(candidates["candidates"]) - i) for i, c in enumerate(candidates["candidates"])},
                 "audit": [{"chunk_id": c["chunk_id"], "tokens": 10} for c in candidates["candidates"]], "mode": "MOCK"}
        ranked = rank_v2(candidates, score if candidates["candidates"] else None, selection)
        ranked["mode"] = "MOCK"
        return ranked


class MockResponse(BytesIO):
    status = 200

    def __init__(self, pieces, delay):
        super().__init__()
        self.headers = {"content-type": "text/event-stream"}
        self.pieces, self.delay = iter(pieces), delay

    def read1(self, size):
        if self.delay: time.sleep(self.delay)
        value = next(self.pieces, b"")
        if isinstance(value, BaseException): raise value
        return value


class MockOpener:
    """Feeds real G3 SSE parsing/archival with deterministic synthetic frames."""
    def __init__(self, answer, *, disconnect=False, delay=0):
        self.answer, self.disconnect, self.delay, self.calls = answer, disconnect, delay, 0

    def open(self, request, *, timeout):
        self.calls += 1
        frames = []
        def frame(delta, finish=None, **extra):
            return b"data: " + encoded({"id": "g4-fixture", "model": MODEL, "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}) + b"\n\n"
        frames.append(frame({"role": "assistant", "content": ""}))
        for start in range(0, len(self.answer), 12): frames.append(frame({"content": self.answer[start:start+12]}))
        if self.disconnect:
            frames.append(ConnectionError("Mock stream interrupted"))
        else:
            frames.extend([frame({}, "stop"),
                b"data: " + encoded({"id": "g4-fixture", "model": MODEL, "object": "chat.completion.chunk",
                    "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}) + b"\n\n",
                b"data: [DONE]\n\n"])
        return MockResponse(frames, self.delay)


def mock_answer(packet, scenario):
    prefix = {"normal": "Mock固定链已完成；当前原文仅支持查看所示片段，不证明任何剧情解释。",
              "limited": "Mock有限结论：未找到反证不证明假说成立；保留未解事项。",
              "retract": "Mock撤回先前假说：本夹具演示反证成立后的修正，旧观点不再作为事实。",
              "source_conflict": "Mock冲突提示：用户剧情断言仍待核验；不得覆盖所展示原文。",
              "disconnect": "Mock未完成草稿，不应展示为最终答案。"}[scenario]
    ids = ["[" + e["citation_id"] + "]" for e in packet["evidence"][:2]]
    return prefix + "".join(ids)
