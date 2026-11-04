"""Bounded, transactional original-RU migration. No API transport or default switch.

Receipts commit shards last; OS locks survive stale PID files and release on crash.
The serving manifest is published only after an independent source/row/vector pass.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import gzip
from hashlib import sha256
from io import BytesIO
import json
import math
import os
from pathlib import Path
import shutil
import socket
import sys
import time
import traceback
from typing import Any, Iterator

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from .candidate_retrieval import DENSE_INDEX_SCHEMA_VERSION
from .local_qwen3_embedding import (
    DIMENSION, MAX_LENGTH, MODEL_ID, REVISION, WEIGHT_SHA256,
    LocalQwen3Config, LocalQwen3Embedding, LocalQwen3Error, file_sha256,
)
from .retrieval_units import RETRIEVAL_UNIT_BUILD_SCHEMA_VERSION, RETRIEVAL_UNIT_SCHEMA_VERSION

VERSION = "phase04-local-qwen3-resumable-1"
DEFAULT_RU = Path("data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/ru/metadata/manifest.json")
DEFAULT_MODEL = Path(".local/models/qwen3-embedding-0.6b") / REVISION
DEFAULT_OUTPUT = Path("data/retrieval/p04-local-qwen3-1024-20261102-v1")
LONG_TEXT_POLICY = "exact-full-RU; reject-and-log-above-2048-including-special-tokens; no-truncation"


@contextmanager
def prevent_idle_sleep():
    """Hold an idle-sleep request only while the independent worker is alive."""
    if os.name != "nt":
        yield; return
    import ctypes
    kernel=ctypes.windll.kernel32
    if not kernel.SetThreadExecutionState(0x80000000 | 0x00000001):
        raise LocalQwen3Error("cannot request Windows idle-sleep inhibition")
    try:
        yield
    finally:
        kernel.SetThreadExecutionState(0x80000000)


def guard_root(root: Path) -> None:
    allowed={"build.lock","preflight.json","console.log","stdout.log","stderr.log","launcher.json","launch-logs","length-audit.json"}
    if not (root/"build-plan.json").exists() and any(p.name not in allowed for p in root.iterdir()):
        raise LocalQwen3Error("output root is not a new/recognized build directory")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, canonical_json_bytes(value))


def descriptor(path: Path, root: Path, **extra) -> dict:
    return {"path": path.relative_to(root).as_posix(), "byte_count": path.stat().st_size,
            "sha256": file_sha256(path), **extra}


def check_file(root: Path, desc: dict) -> Path:
    path = (root / desc["path"]).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise LocalQwen3Error("artifact missing or outside its root")
    if path.stat().st_size != desc["byte_count"] or file_sha256(path) != desc["sha256"]:
        raise LocalQwen3Error(f"artifact checksum mismatch: {path}")
    return path


@contextmanager
def build_lock(root: Path):
    """Kernel lock, not PID guessing. Persistent lock file is deliberately retained."""
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / "build.lock").open("a+b")
    if handle.tell() == 0:
        handle.write(b"0"); handle.flush()
    handle.seek(0)
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
    except OSError as exc:
        handle.close()
        raise LocalQwen3Error("another process owns this build; do not remove the lock file") from exc
    try:
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


@contextmanager
def offline_only():
    """Fail even if a dependency attempts a socket connection; restore on return."""
    old = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)
    def deny(*args, **kwargs):
        raise LocalQwen3Error("network forbidden in local embedding build/query")
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = old


def verified_source(path: Path) -> dict:
    manifest = read_json(path)
    if manifest.get("status") != "complete" or manifest.get("schema_version") != RETRIEVAL_UNIT_BUILD_SCHEMA_VERSION:
        raise LocalQwen3Error("source must be a complete supported RU build")
    root = path.parent.parent
    for name in ("retrieval_units", "skip_ledger", "failure_ledger"):
        check_file(root, manifest["artifacts"][name])
    failures = read_json(root / manifest["artifacts"]["failure_ledger"]["path"])
    if failures.get("status") != "clear" or failures.get("failure_count") != 0:
        raise LocalQwen3Error("source failure ledger is not clear")
    skip = manifest["artifacts"]["skip_ledger"]
    with gzip.open(root / skip["path"], "rt", encoding="utf-8") as handle:
        count = sum(1 for line in handle if isinstance(json.loads(line), dict))
    if count != skip["row_count"]:
        raise LocalQwen3Error("skip ledger accounting mismatch")
    count = manifest["artifacts"]["retrieval_units"]["row_count"]
    if type(count) is not int or count <= 0 or count != manifest["accounting"]["retrieval_unit_count"]:
        raise LocalQwen3Error("source row count mismatch")
    return manifest


def iter_units(path: Path, source: dict) -> Iterator[dict]:
    artifact = path.parent.parent / source["artifacts"]["retrieval_units"]["path"]
    count = 0
    with gzip.open(artifact, "rt", encoding="utf-8") as handle:
        for count, line in enumerate(handle, 1):
            unit = json.loads(line)
            if (unit.get("schema_version") != RETRIEVAL_UNIT_SCHEMA_VERSION
                    or not isinstance(unit.get("unit_id"), str) or not unit["unit_id"]
                    or not isinstance(unit.get("retrieval_visible_text"), str) or not unit["retrieval_visible_text"].strip()
                    or not isinstance(unit.get("source"), dict)):
                raise LocalQwen3Error(f"invalid original RU at occurrence {count-1}")
            yield unit
    if count != source["artifacts"]["retrieval_units"]["row_count"]:
        raise LocalQwen3Error("source gzip row accounting mismatch")


def unit_chunks(path: Path, source: dict, size: int, limit: int | None = None, selected_indices: tuple[int, ...] = ()):
    chunk = []
    selected = set(selected_indices)
    kept = 0
    for index, unit in enumerate(iter_units(path, source)):
        if selected and index not in selected:
            continue
        if limit is not None and kept >= limit:
            break
        chunk.append((index, unit)); kept += 1
        if len(chunk) == size:
            yield chunk; chunk = []
    if chunk:
        yield chunk


def token_lengths(encoder, texts: list[str]) -> list[int]:
    return [len(ids) for ids in encoder._tokenizer(texts, truncation=False, add_special_tokens=True)["input_ids"]]


def length_summary(histogram: Counter) -> dict:
    count = sum(histogram.values())
    def percentile(p):
        cutoff = math.ceil(count*p); running = 0
        for length, n in sorted(histogram.items()):
            running += n
            if running >= cutoff: return length
        return None
    return {"rows": count, "tokens": sum(k*v for k,v in histogram.items()),
            "max": max(histogram, default=0), "p50": percentile(.5), "p95": percentile(.95), "p99": percentile(.99),
            "over_limit": sum(v for k,v in histogram.items() if k > MAX_LENGTH),
            "histogram": dict(sorted(histogram.items()))}


def scan_lengths(path: Path, source: dict, encoder, root: Path) -> dict:
    """CPU tokenizer only; full corpus scan, bounded memory, zero model inference."""
    binding = {"source_manifest_sha256": file_sha256(path), "vector_space_identity": encoder.space_identity,
               "long_text_policy": LONG_TEXT_POLICY, "version": VERSION}
    cached = root / "length-audit.json"
    if cached.exists():
        report = read_json(cached)
        if report.get("binding") != binding or report.get("rows") != source["accounting"]["retrieval_unit_count"]:
            raise LocalQwen3Error("length audit binding mismatch")
        if report.get("over_limit"):
            raise LocalQwen3Error("length audit contains oversized RUs; inspect length-audit.json")
        return report
    histogram = Counter(); longest = []; start = time.monotonic()
    for chunk in unit_chunks(path, source, 512):
        lengths = token_lengths(encoder, [u["retrieval_visible_text"] for _,u in chunk])
        histogram.update(lengths)
        longest.extend({"tokens": n, "source_occurrence_index": i, "unit_id": u["unit_id"]} for (i,u),n in zip(chunk,lengths))
        longest = sorted(longest, key=lambda r: (-r["tokens"],r["source_occurrence_index"]))[:16]
        if sum(histogram.values()) % 32768 == 0:
            print(f"TOKEN SCAN {sum(histogram.values()):,}/{source['accounting']['retrieval_unit_count']:,}", flush=True)
    report = {**length_summary(histogram), "binding": binding, "longest": longest, "seconds": time.monotonic()-start}
    write_json(cached, report)
    if report["over_limit"]:
        raise LocalQwen3Error("oversized RUs recorded; no document was truncated or embedded")
    return report


@dataclass(frozen=True)
class BuildOptions:
    shard_rows: int = 1024
    max_batch: int = 8
    padded_token_budget: int = 4096
    limit: int | None = None
    selected_indices: tuple[int, ...] = ()

    def __post_init__(self):
        for name in ("shard_rows", "max_batch", "padded_token_budget"):
            if type(getattr(self,name)) is not int or getattr(self,name) < 1:
                raise LocalQwen3Error(f"invalid {name}")
        if self.shard_rows>4096 or self.max_batch>32 or self.padded_token_budget>16384:
            raise LocalQwen3Error("bounded builder requires shard_rows<=4096, max_batch<=32, padded_token_budget<=16384")
        if self.limit is not None and (type(self.limit) is not int or not 1 <= self.limit <= 4096):
            raise LocalQwen3Error("bounded smoke limit must be 1..4096")
        if self.selected_indices and (len(set(self.selected_indices)) != len(self.selected_indices) or any(type(i) is not int or i<0 for i in self.selected_indices) or self.limit != len(self.selected_indices)):
            raise LocalQwen3Error("explicit smoke indices must be unique and match the hard limit")


def _row(index: int, source_index: int, unit: dict, length: int) -> dict:
    return {"occurrence_index": index, "source_occurrence_index": source_index, "unit_id": unit["unit_id"],
            "text_sha256": sha256(unit["retrieval_visible_text"].encode("utf-8")).hexdigest(),
            "source_ru_row_sha256": sha256_json(unit), "source": unit["source"],
            "record_title": unit["source"].get("record_context",{}).get("record_title"),
            "token_audit": {"untruncated_tokens": length, "encoded_tokens": length, "truncated": False}}


def validate_vectors(vectors, count: int) -> None:
    import numpy as np
    if vectors.shape != (count,DIMENSION) or vectors.dtype != np.dtype("float32"):
        raise LocalQwen3Error("vector shape/dtype mismatch")
    for start in range(0,count,1024):
        part = vectors[start:start+1024]
        if not np.isfinite(part).all() or not np.allclose(np.linalg.norm(part,axis=1),1,atol=1e-5,rtol=0):
            raise LocalQwen3Error("NaN/Inf/zero/non-normalized vectors")


def encode_chunk(encoder, texts, lengths, options, event):
    """Length buckets within one shard; scatter back to unchanged RU order."""
    import numpy as np
    import torch
    result = np.empty((len(texts),DIMENSION),dtype=np.float32)
    order = sorted(range(len(texts)),key=lambda i:(lengths[i],i))
    offset = 0; cap = options.max_batch; groups = []; last_heartbeat=time.monotonic()
    while offset < len(order):
        take = 1
        while take < cap and offset+take < len(order) and lengths[order[offset+take]]*(take+1) <= options.padded_token_budget:
            take += 1
        indices = order[offset:offset+take]
        try:
            batch = encoder.encode_documents([texts[i] for i in indices],batch_size=take)
        except torch.cuda.OutOfMemoryError:
            # Discard only this uncommitted microbatch; no CPU/device fallback.
            import gc
            gc.collect(); torch.cuda.empty_cache()
            event("cuda_oom", batch_size=take, max_tokens=max(lengths[i] for i in indices))
            if take == 1: raise
            cap = max(1,take//2)
            continue
        if batch.space_identity != encoder.space_identity or batch.role != "document" or any(a["truncated"] for a in batch.token_audit):
            raise LocalQwen3Error("encoder identity/truncation violation")
        for position, original in enumerate(indices):
            if batch.text_sha256[position] != sha256(texts[original].encode("utf-8")).hexdigest() or batch.token_audit[position]["encoded_tokens"] != lengths[original]:
                raise LocalQwen3Error("encoded text/order/token mismatch")
        result[indices] = batch.vectors
        groups.append({"indices": indices, "batch_size":take})
        offset += take
        if time.monotonic()-last_heartbeat >= 15:
            event("encoding",uncommitted_rows=offset,shard_rows=len(texts))
            last_heartbeat=time.monotonic()
    validate_vectors(result,len(texts))
    return result, groups


def resources() -> dict:
    import psutil
    import torch
    mem = psutil.Process().memory_info()
    result = {"rss_bytes":mem.rss,"peak_working_set_bytes":getattr(mem,"peak_wset",mem.rss),
              "system_ram_available":psutil.virtual_memory().available}
    if torch.cuda.is_initialized():
        free,total = torch.cuda.mem_get_info()
        result["cuda"] = {"free_bytes":free,"total_bytes":total,"allocated_bytes":torch.cuda.memory_allocated(),
                          "reserved_bytes":torch.cuda.memory_reserved(),"peak_allocated_bytes":torch.cuda.max_memory_allocated(),
                          "peak_reserved_bytes":torch.cuda.max_memory_reserved()}
    return result


def save_shard(root, number, rows, vectors, plan_id, groups, seconds):
    folder = root/"shards"/f"{number:06d}"
    if (folder/"receipt.json").exists():
        raise LocalQwen3Error("committed shard cannot be overwritten")
    buffer = BytesIO()
    import numpy as np
    np.save(buffer,vectors,allow_pickle=False)
    atomic_write(folder/"vectors.npy",buffer.getvalue())
    data = b"".join(canonical_json_bytes(row)+b"\n" for row in rows)
    atomic_write(folder/"rows.jsonl.gz",gzip.compress(data,mtime=0))
    receipt = {"version":VERSION,"plan_identity":plan_id,"number":number,"start":rows[0]["occurrence_index"],
               "rows":len(rows),"row_identity":sha256_json(rows),"encoding_groups":groups,"encoding_seconds":seconds,
               "artifacts":{name:descriptor(folder/file,folder) for name,file in (("vectors","vectors.npy"),("rows","rows.jsonl.gz"))}}
    write_json(folder/"receipt.json",receipt) # sole commit point, after fsynced files
    return receipt


def load_shard(root, number, plan_id, expected_rows=None):
    import numpy as np
    folder = root/"shards"/f"{number:06d}"
    receipt = read_json(folder/"receipt.json")
    if receipt.get("version") != VERSION or receipt.get("plan_identity") != plan_id or receipt.get("number") != number:
        raise LocalQwen3Error("committed shard plan/sequence mismatch")
    vp = check_file(folder,receipt["artifacts"]["vectors"])
    rp = check_file(folder,receipt["artifacts"]["rows"])
    with gzip.open(rp,"rt",encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    if len(rows) != receipt["rows"] or sha256_json(rows) != receipt["row_identity"] or (expected_rows is not None and rows != expected_rows):
        raise LocalQwen3Error("committed shard original-RU alignment mismatch")
    vectors = np.load(vp,allow_pickle=False)
    validate_vectors(vectors,len(rows))
    return receipt, rows, vectors


def receipt_summary(root, number, receipt):
    # Keep only O(shards) tiny summaries in RAM, not all encoding groups/rows.
    return {key:receipt[key] for key in ("number","start","rows","encoding_seconds")} | {
        "receipt_sha256":file_sha256(root/"shards"/f"{number:06d}"/"receipt.json")}


def validate_final(root: Path, plan: dict, source_path: Path, source: dict, *, published=False) -> dict:
    """Independent streaming original-source comparison and bounded matrix checks."""
    import numpy as np
    fields={k:v for k,v in plan.items() if k not in {"identity","candidate_manifest"}}
    if sha256_json(fields) != plan.get("identity") or plan.get("version") != VERSION:
        raise LocalQwen3Error("build plan integrity/version mismatch")
    manifest = read_json(root/"metadata/manifest.json") if published else plan["candidate_manifest"]
    if manifest.get("status") != "complete" or manifest.get("build_plan_identity") != plan["identity"] or manifest.get("vector_space_identity") != sha256_json(plan["encoding_contract"]):
        raise LocalQwen3Error("final manifest binding mismatch")
    for key,value in {"model_name":MODEL_ID,"model_revision":REVISION,"model_sha256":WEIGHT_SHA256,
                      "embedding_dimension":DIMENSION,"dtype":"float32","normalization":"L2",
                      "row_count":plan["target_rows"],"retrieval_unit_build_identity":source["build_identity"],
                      "source_ru_manifest_sha256":file_sha256(source_path),"local_qwen3_contract":plan["encoding_contract"]}.items():
        if manifest.get(key) != value: raise LocalQwen3Error(f"final {key} mismatch")
    vp = check_file(root,manifest["artifacts"]["vectors"])
    rp = check_file(root,manifest["artifacts"]["rows"])
    vectors = np.load(vp,mmap_mode="r",allow_pickle=False)
    try:
        validate_vectors(vectors,plan["target_rows"])
    finally:
        vectors._mmap.close()
    count=0; row_hash=sha256()
    options = BuildOptions(**{**plan["options"],"selected_indices":tuple(plan["options"]["selected_indices"])})
    with gzip.open(rp,"rt",encoding="utf-8") as handle:
        for chunk in unit_chunks(source_path,source,options.shard_rows,options.limit,options.selected_indices):
            for source_index,unit in chunk:
                line = handle.readline()
                if not line: raise LocalQwen3Error("final row mapping is short")
                row = json.loads(line)
                row_hash.update(canonical_json_bytes(row)+b"\n")
                audit = row.get("token_audit",{})
                length = audit.get("untruncated_tokens")
                if type(length) is not int or not 1 <= length <= MAX_LENGTH or row != _row(count,source_index,unit,length):
                    raise LocalQwen3Error(f"final original-RU lineage/order mismatch at {count}")
                count += 1
        if handle.readline() or count != plan["target_rows"]:
            raise LocalQwen3Error("final row coverage mismatch")
    # Recheck source compressed evidence after the pass (read-only source may not drift).
    if file_sha256(source_path) != plan["source_manifest_sha256"]:
        raise LocalQwen3Error("source manifest changed during build")
    check_file(source_path.parent.parent,source["artifacts"]["retrieval_units"])
    expected_arm=sha256_json({"plan":plan["identity"],"artifacts":manifest["artifacts"],"row_identity":row_hash.hexdigest()})
    if manifest.get("arm_build_identity") != expected_arm:
        raise LocalQwen3Error("index build identity does not match artifacts/row order")
    return {"status":"PASS","coverage_rows":count,"dimension":DIMENSION,"source_identity_order":"PASS",
            "nan_inf":"PASS","normalization":"PASS","artifact_hashes":"PASS","long_text_truncations":0}


def finalize(root, plan, source_path, source, receipts):
    import numpy as np
    folder = root/"artifacts"; folder.mkdir(exist_ok=True)
    # Only unadvertised derivative files in this owned build may be replaced.
    vp = folder/"vectors.f32.npy"; temp = folder/"vectors.building.npy"
    vectors = np.lib.format.open_memmap(temp,mode="w+",dtype=np.float32,shape=(plan["target_rows"],DIMENSION))
    rows_temp = folder/"rows.building.gz"
    row_hash = sha256(); offset=0
    try:
        with rows_temp.open("wb") as raw:
            with gzip.GzipFile(filename="",mode="wb",fileobj=raw,mtime=0) as gz:
                for number,expected in enumerate(receipts):
                    receipt,rows,values = load_shard(root,number,plan["identity"])
                    if receipt_summary(root,number,receipt) != expected or receipt["start"] != offset:
                        raise LocalQwen3Error("shard changed during finalization")
                    vectors[offset:offset+len(rows)] = values
                    for row in rows:
                        body = canonical_json_bytes(row)+b"\n"; gz.write(body); row_hash.update(body)
                    offset += len(rows)
            raw.flush(); os.fsync(raw.fileno())
        if offset != plan["target_rows"]: raise LocalQwen3Error("incomplete final coverage")
        vectors.flush()
    finally:
        vectors._mmap.close()
    with temp.open("r+b") as f: os.fsync(f.fileno())
    os.replace(temp,vp); os.replace(rows_temp,folder/"rows.jsonl.gz")
    artifacts = {"vectors":descriptor(vp,root),"rows":descriptor(folder/"rows.jsonl.gz",root,row_count=offset)}
    manifest = {"schema_version":DENSE_INDEX_SCHEMA_VERSION,"status":"complete","arm":"dense",
                "arm_build_identity":sha256_json({"plan":plan["identity"],"artifacts":artifacts,"row_identity":row_hash.hexdigest()}),
                "retrieval_unit_build_identity":source["build_identity"],"source_ru_manifest":str(source_path.resolve()),
                "source_ru_manifest_sha256":plan["source_manifest_sha256"],"model_name":MODEL_ID,"model_revision":REVISION,
                "model_sha256":WEIGHT_SHA256,"embedding_dimension":DIMENSION,"dtype":"float32","normalization":"L2",
                "row_count":offset,"local_qwen3_contract":plan["encoding_contract"],
                "vector_space_identity":sha256_json(plan["encoding_contract"]),"build_plan_identity":plan["identity"],
                "scope":"full-original-RU-candidate" if plan["options"]["limit"] is None else "bounded-smoke-not-production",
                "row_mapping_policy":"source RU order; exact full text; original source occurrence retained",
                "long_text_policy":LONG_TEXT_POLICY,"artifacts":artifacts}
    validation = validate_final(root,{**plan,"candidate_manifest":manifest},source_path,source)
    write_json(root/"validation.json",validation)
    # The public loader cannot see a complete index before this last atomic publication.
    write_json(root/"metadata/manifest.json",manifest)
    return manifest,validation


def run_build(source_path: Path, root: Path, encoder, options: BuildOptions, *, stop_after_shards: int | None = None, tokenize_scan=True):
    """stop_after_shards is an explicit pause/test hook, never successful completion."""
    source_path=Path(source_path).resolve(); root=Path(root).resolve()
    if stop_after_shards is not None and (type(stop_after_shards) is not int or stop_after_shards<1):
        raise LocalQwen3Error("stop_after_shards must be positive")
    if root == source_path.parent.parent or root.is_relative_to(source_path.parent.parent):
        raise LocalQwen3Error("output must not be inside the immutable RU root")
    if root.exists(): guard_root(root)
    with build_lock(root):
        # Refuse unrelated roots before creating build-owned artifacts.
        guard_root(root)
        source=verified_source(source_path)
        target=options.limit or source["accounting"]["retrieval_unit_count"]
        if options.selected_indices and max(options.selected_indices) >= source["accounting"]["retrieval_unit_count"]:
            raise LocalQwen3Error("smoke source index outside corpus")
        if target > source["accounting"]["retrieval_unit_count"]: raise LocalQwen3Error("smoke limit exceeds source")
        identity_fields={"version":VERSION,"source_manifest_sha256":file_sha256(source_path),
                         "source_ru_build_identity":source["build_identity"],"source_artifact":source["artifacts"]["retrieval_units"],
                         "encoding_contract":encoder.contract,"options":asdict(options),"target_rows":target,"long_text_policy":LONG_TEXT_POLICY}
        plan={**identity_fields,"identity":sha256_json(identity_fields)}
        if (root/"build-plan.json").exists():
            if canonical_json_bytes(read_json(root/"build-plan.json")) != canonical_json_bytes(plan):
                raise LocalQwen3Error("resume configuration/source/model mismatch; use original options or a new versioned root")
        else:
            write_json(root/"build-plan.json",plan)
        started=time.monotonic(); previous=read_json(root/"progress.json") if (root/"progress.json").exists() else {}
        elapsed_before=previous.get("active_seconds",0.0)
        def event(name,**extra):
            body={"event":name,"utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),**extra}
            with (root/"events.jsonl").open("a",encoding="utf-8") as f:
                f.write(json.dumps(body,ensure_ascii=False)+"\n"); f.flush(); os.fsync(f.fileno())
            if name=="progress":
                eta=extra.get("eta_seconds")
                print(f"{extra['completed_rows']:,}/{extra['total_rows']:,} ({100*extra['completed_rows']/extra['total_rows']:.1f}%) "
                      f"| {'REUSED' if extra['reused'] else 'SAVED'} | active {extra['active_seconds']/3600:.2f} h "
                      f"| {extra['rows_per_active_second']:.2f} RU/s | ETA {eta/3600:.2f} h",flush=True)
            elif name=="complete":
                report=extra['report']
                print(f"COMPLETE: {report['coverage_rows']:,} rows | validation PASS | active {report['active_seconds']/3600:.2f} h | {report['index_manifest']}",flush=True)
            elif name=="failure":
                print(f"{extra['status'].upper()}: {extra['type']}: {extra['message']} (see last-error.json)",flush=True)
            else:
                print(json.dumps({k:v for k,v in body.items() if k!='report'},ensure_ascii=False),flush=True)
        try:
            event("started",pid=os.getpid(),target_rows=target,plan_identity=plan["identity"])
            if (root/"metadata/manifest.json").exists():
                validation=validate_final(root,plan,source_path,source,published=True)
                if not (root/"report.json").exists():
                    # A crash between manifest publication and the summary is
                    # recoverable without any model inference.
                    write_json(root/"report.json",{"status":"complete","coverage_rows":target,
                        "index_manifest":str(root/"metadata/manifest.json"),"validation":validation,
                        "active_seconds":elapsed_before,"summary_recovered_after_publication":True})
                event("already_complete",validation=validation)
                return read_json(root/"report.json")
            encoder.load()
            if tokenize_scan: scan_lengths(source_path,source,encoder,root)
            receipts=[]; done=0; new_rows=0; encoding_seconds=0.; histogram=Counter()
            for number,chunk in enumerate(unit_chunks(source_path,source,options.shard_rows,options.limit,options.selected_indices)):
                lengths=token_lengths(encoder,[u["retrieval_visible_text"] for _,u in chunk])
                if max(lengths) > MAX_LENGTH:
                    event("oversized_ru",rows=[{"source_occurrence_index":i,"unit_id":u["unit_id"],"tokens":n} for (i,u),n in zip(chunk,lengths) if n>MAX_LENGTH])
                    raise LocalQwen3Error("RU exceeds operating limit; no silent truncation")
                rows=[_row(done+j,i,u,n) for j,((i,u),n) in enumerate(zip(chunk,lengths))]
                if (root/"shards"/f"{number:06d}"/"receipt.json").exists():
                    receipt,_,_=load_shard(root,number,plan["identity"],rows)
                    reused=True
                else:
                    tick=time.monotonic()
                    vectors,groups=encode_chunk(encoder,[u["retrieval_visible_text"] for _,u in chunk],lengths,options,event)
                    seconds=time.monotonic()-tick
                    receipt=save_shard(root,number,rows,vectors,plan["identity"],groups,seconds)
                    del vectors
                    new_rows+=len(rows); encoding_seconds+=seconds; reused=False
                receipts.append(receipt_summary(root,number,receipt)); histogram.update(lengths); done+=len(rows)
                seconds=elapsed_before+time.monotonic()-started
                # Encoding throughput uses measured committed shards, no guessed time guarantee.
                encoded_total=sum(r["encoding_seconds"] for r in receipts)
                speed=done/encoded_total if encoded_total else None
                progress={"status":"building","completed_rows":done,"total_rows":target,"completed_shards":len(receipts),
                          "active_seconds":seconds,"encoding_seconds":encoded_total,"rows_per_encoding_second":speed,
                          "eta_encoding_seconds":(target-done)/speed if speed else None,"resources":resources(),
                          "rows_per_active_second":done/max(seconds,1e-9),"eta_seconds":(target-done)*seconds/done,
                          "new_rows_this_run":new_rows,"new_encoding_seconds_this_run":encoding_seconds}
                write_json(root/"progress.json",progress)
                event("progress",reused=reused,**progress)
                if stop_after_shards is not None and len(receipts)>=stop_after_shards:
                    write_json(root/"progress.json",{**progress,"status":"paused"})
                    event("paused",completed_rows=done)
                    return {**progress,"status":"paused"}
            if done != target: raise LocalQwen3Error("source/target coverage mismatch")
            write_json(root/"progress.json",{**progress,"status":"finalizing"})
            event("finalizing",rows=done)
            manifest,validation=finalize(root,plan,source_path,source,receipts)
            report={"status":"complete","active_seconds":elapsed_before+time.monotonic()-started,
                    "coverage_rows":done,"full_source_rows":source["accounting"]["retrieval_unit_count"],
                    "index_manifest":str(root/"metadata/manifest.json"),"arm_build_identity":manifest["arm_build_identity"],
                    "plan_identity":plan["identity"],"token_distribution":length_summary(histogram),"validation":validation,
                    "encoding_seconds":sum(r["encoding_seconds"] for r in receipts),"resources":resources()}
            write_json(root/"report.json",report)
            write_json(root/"progress.json",{**progress,"status":"complete","active_seconds":report["active_seconds"]})
            event("complete",report=report)
            return report
        except BaseException as exc:
            failure={"status":"interrupted" if isinstance(exc,KeyboardInterrupt) else "failed","type":type(exc).__name__,
                     "message":str(exc),"traceback":traceback.format_exc(),"active_seconds":elapsed_before+time.monotonic()-started}
            write_json(root/"last-error.json",failure)
            if (root/"progress.json").exists():
                write_json(root/"progress.json",{**read_json(root/"progress.json"),"status":failure["status"],"active_seconds":failure["active_seconds"]})
            event("failure",**failure)
            raise


def preflight(source_path: Path, root: Path, model_path: Path) -> dict:
    """Checks real local resources; tokenizer scan only, no document vectors."""
    import psutil
    import torch
    from transformers import AutoTokenizer
    source=verified_source(source_path)
    if root.resolve().is_relative_to(source_path.parent.parent.resolve()) or root.resolve().is_relative_to(model_path.resolve()):
        raise LocalQwen3Error("output overlaps immutable source/model")
    if root.exists(): guard_root(root)
    encoder=LocalQwen3Embedding(LocalQwen3Config(model_path))
    # load() validates all runtime versions, but preflight avoids allocating weights.
    from importlib.metadata import version
    import platform
    from .local_qwen3_embedding import RUNTIME
    actual={"python":platform.python_version(),"torch":torch.__version__}
    actual.update({name:version(name) for name in ("transformers","tokenizers","numpy","safetensors")})
    if actual != RUNTIME: raise LocalQwen3Error(f"runtime mismatch: {actual}")
    if not torch.cuda.is_available(): raise LocalQwen3Error("CUDA unavailable; no CPU fallback")
    count=source["accounting"]["retrieval_unit_count"]
    minimum=count*DIMENSION*4*2+3*1024**3
    with build_lock(root):
        guard_root(root)
        disk=shutil.disk_usage(root)
        # Space for shards plus assembled matrix and metadata; conservative even on resume.
        if disk.free < minimum: raise LocalQwen3Error(f"need at least {minimum} free bytes (conservative headroom)")
        if psutil.virtual_memory().available < 1536*1024**2: raise LocalQwen3Error("less than 1.5 GiB available RAM")
        free,total=torch.cuda.mem_get_info()
        if free < 1536*1024**2: raise LocalQwen3Error("less than 1.5 GiB free VRAM; close other GPU workloads")
        encoder._tokenizer=AutoTokenizer.from_pretrained(str(model_path),local_files_only=True,trust_remote_code=False,padding_side="left")
        audit=scan_lengths(source_path,source,encoder,root)
        receipts=sorted((root/"shards").glob("*/receipt.json")) if (root/"shards").exists() else []
        if receipts:
            plan=read_json(root/"build-plan.json")
            if plan["source_manifest_sha256"] != file_sha256(source_path) or plan["encoding_contract"] != encoder.contract:
                raise LocalQwen3Error("existing progress is bound to a different source/model")
            for number,p in enumerate(receipts):
                if p.parent.name != f"{number:06d}": raise LocalQwen3Error("non-contiguous committed shard sequence")
                load_shard(root,number,plan["identity"])
        report={"status":"PASS","python":sys.executable,"runtime":actual,"gpu":torch.cuda.get_device_name(),
                "gpu_free_bytes":free,"gpu_total_bytes":total,"available_ram_bytes":psutil.virtual_memory().available,
                "disk_free_bytes":disk.free,"disk_required_headroom_bytes":minimum,"source_rows":count,
                "source_manifest_sha256":file_sha256(source_path),"vector_space_identity":encoder.space_identity,
                "output_root":str(root.resolve()),"committed_shards_verified":len(receipts),"token_distribution":audit,
                "complete_manifest_exists":(root/"metadata/manifest.json").exists()}
        write_json(root/"preflight.json",report)
        return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",choices=("preflight","run","resume","validate","smoke"))
    parser.add_argument("--source",type=Path,default=DEFAULT_RU)
    parser.add_argument("--model",type=Path,default=DEFAULT_MODEL)
    parser.add_argument("--output",type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument("--shard-rows",type=int,default=1024)
    parser.add_argument("--max-batch",type=int,default=8)
    parser.add_argument("--padded-token-budget",type=int,default=4096)
    parser.add_argument("--limit",type=int)
    parser.add_argument("--indices",type=Path,help="JSON list of original row indices, smoke only")
    parser.add_argument("--stop-after-shards",type=int)
    parser.add_argument("--operator-start-full",action="store_true",help="explicit manual gate for the 535802-RU job")
    args=parser.parse_args(argv)
    if args.mode in ("run","resume") and (not args.operator_start_full or args.limit or args.indices):
        parser.error("full mode requires --operator-start-full and forbids smoke limits/selections")
    if args.mode=="smoke" and args.limit is None: parser.error("smoke requires explicit --limit <=4096")
    if args.mode!="smoke" and (args.limit is not None or args.indices): parser.error("limits/selections are smoke-only")
    os.environ["HF_HUB_OFFLINE"]="1"; os.environ["TRANSFORMERS_OFFLINE"]="1"; os.environ["TOKENIZERS_PARALLELISM"]="false"
    try:
        with offline_only(), prevent_idle_sleep():
            if args.mode=="preflight": report=preflight(args.source,args.output,args.model)
            elif args.mode=="validate":
                with build_lock(args.output):
                    source=verified_source(args.source); plan=read_json(args.output/"build-plan.json")
                    report=validate_final(args.output,plan,args.source,source,published=True)
                    write_json(args.output/"validation.json",report)
            else:
                if args.mode in ("run","resume"):
                    preflight(args.source,args.output,args.model)
                options=BuildOptions(args.shard_rows,args.max_batch,args.padded_token_budget,args.limit,
                                     tuple(read_json(args.indices)) if args.indices else ())
                encoder=LocalQwen3Embedding(LocalQwen3Config(args.model))
                report=run_build(args.source,args.output,encoder,options,stop_after_shards=args.stop_after_shards)
        display=dict(report)
        if isinstance(display.get("token_distribution"),dict):
            display["token_distribution"]={k:v for k,v in display["token_distribution"].items() if k not in {"histogram","binding","longest"}}
        print(json.dumps(display,ensure_ascii=False),flush=True)
        return 0
    except KeyboardInterrupt:
        print("Interrupted. Committed shards retained; run resume with the same configuration.",file=sys.stderr,flush=True)
        return 130
    except Exception as exc:
        print(f"BUILD FAILED: {type(exc).__name__}: {exc}",file=sys.stderr,flush=True)
        return 1


if __name__=="__main__": raise SystemExit(main())
