"""Opt-in, offline Qwen3 E1 embedding. Never used by production selectors."""
from __future__ import annotations

import gc
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time

MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
DIMENSIONS = 1024
VERSION = "p05-e1-qwen3-local-1"
QUERY_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"
MAX_TOKENS = 512
GPU_BUDGET_MIB = 1792


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def identity_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def prepare_text(text, role):
    if role not in ("query", "document"):
        raise ValueError("role must be query or document")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("embedding text must be a nonblank string")
    # Preserve source codepoints, whitespace and punctuation; no title insertion.
    return f"Instruct: {QUERY_INSTRUCTION}\nQuery: {text}" if role == "query" else text


def verify_snapshot(model_root, lock_path):
    root = Path(model_root).resolve()
    lock = json.loads(Path(lock_path).read_text("utf-8"))
    if lock["model_id"] != MODEL_ID or lock["revision"] != REVISION:
        raise ValueError("wrong model or revision")
    required = {"model.safetensors", "config.json", "tokenizer.json", "tokenizer_config.json",
                "vocab.json", "merges.txt"}
    names = {x["path"] for x in lock["files"]}
    if not required <= names or len(names) != len(lock["files"]):
        raise ValueError("incomplete or duplicate model lock")
    for item in lock["files"]:
        p = (root / item["path"]).resolve()
        if not p.is_relative_to(root) or p.stat().st_size != item["bytes"]:
            raise ValueError("invalid snapshot file")
        if file_sha256(p) != item["sha256"]:
            raise ValueError(f"snapshot hash mismatch: {item['path']}")
    return lock


def last_token_pool(hidden, mask):
    # Correct for both left and right padding (official Qwen example).
    if bool((mask[:, -1].sum() == mask.shape[0]).item()):
        return hidden[:, -1]
    import torch
    lengths = mask.sum(dim=1) - 1
    return hidden[torch.arange(hidden.shape[0], device=hidden.device), lengths]


class LocalQwen3Embedding:
    """Single-text batches, bounded CUDA allocator, explicit whole-call CPU fallback.

    Identity includes numerical backend. Callers must bind an index and its query
    vectors to the same identity. A fallback invalidates the previous identity.
    """
    def __init__(self, model_root, *, lock_path=None, device="auto", gpu_budget_mib=GPU_BUDGET_MIB):
        if device not in ("auto", "cpu", "cuda"):
            raise ValueError("device must be auto, cpu or cuda")
        if not 1400 <= gpu_budget_mib <= GPU_BUDGET_MIB:
            raise ValueError("GPU budget must be between 1400 and 1792 MiB")
        import torch
        from transformers import AutoModel, AutoTokenizer
        self.torch = torch
        self.model_root = Path(model_root)
        self.lock_path = Path(lock_path) if lock_path else Path(__file__).with_name("qwen3_e1_model_lock.json")
        self.lock = verify_snapshot(model_root, self.lock_path)
        self.requested_device = device
        self.gpu_budget_mib = gpu_budget_mib
        self.events = []
        self.audit = []
        self.AutoModel = AutoModel
        torch.set_num_threads(4)
        torch.manual_seed(0)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        self.tokenizer = AutoTokenizer.from_pretrained(model_root, local_files_only=True,
                                                       trust_remote_code=False, padding_side="left")
        use_cuda = device != "cpu" and torch.cuda.is_available()
        if device == "cuda" and not use_cuda:
            raise RuntimeError("CUDA explicitly requested but unavailable")
        if use_cuda:
            free, total = torch.cuda.mem_get_info()
            if free < (gpu_budget_mib + 128) * 1024**2:
                if device == "cuda":
                    raise RuntimeError("insufficient free VRAM for the declared budget")
                use_cuda = False
                self.events.append("CPU_FALLBACK: insufficient free VRAM")
            else:
                torch.cuda.set_per_process_memory_fraction(gpu_budget_mib * 1024**2 / total)
                torch.cuda.reset_peak_memory_stats()
        elif device == "auto":
            self.events.append("CPU_FALLBACK: CUDA unavailable")
        try:
            self._load("cuda" if use_cuda else "cpu")
        except torch.cuda.OutOfMemoryError:
            if device != "auto":
                raise
            self._cpu_fallback("model load OOM")

    def _load(self, device):
        torch = self.torch
        self.device = device
        self.dtype = "float16" if device == "cuda" else "float32"
        self.model = self.AutoModel.from_pretrained(
            self.model_root, local_files_only=True, trust_remote_code=False,
            use_safetensors=True, torch_dtype=getattr(torch, self.dtype),
            attn_implementation="sdpa").eval().to(device)
        if self.model.config.hidden_size != DIMENSIONS:
            raise ValueError("unexpected model output dimension")

    def _cpu_fallback(self, reason):
        self.events.append("CPU_FALLBACK: " + reason)
        self.model = None
        gc.collect()
        self.torch.cuda.empty_cache()
        self._load("cpu")

    @property
    def identity(self):
        return {
            "version": VERSION, "model_id": MODEL_ID, "revision": REVISION,
            "model_lock_sha256": file_sha256(self.lock_path), "dimension": DIMENSIONS,
            "query_template": "Instruct: {instruction}\nQuery: {text}",
            "query_instruction": QUERY_INSTRUCTION, "document_instruction": None,
            "preprocessing": "verbatim Unicode; no strip/normalization/title; nonblank only",
            "max_tokens": MAX_TOKENS, "truncation": "right; audited per input",
            "padding_side": "left", "add_special_tokens": True,
            "pooling": "last attended token", "normalization": "FP32 L2",
            "storage_dtype": "float32", "batch_size": 1, "attention": "sdpa",
            "device": self.device, "compute_dtype": self.dtype,
            "packages": {p: importlib.metadata.version(p) for p in
                         ("torch", "transformers", "numpy", "tokenizers", "safetensors")},
        }

    def encode(self, texts, *, role):
        texts = list(texts)
        if not texts:
            raise ValueError("empty embedding batch")
        prepared = [prepare_text(t, role) for t in texts]
        try:
            return self._encode(texts, prepared, role)
        except self.torch.cuda.OutOfMemoryError:
            if self.requested_device != "auto" or self.device != "cuda":
                raise
            self._cpu_fallback("inference OOM; entire encode call restarted")
            return self._encode(texts, prepared, role)

    def _encode(self, texts, prepared, role):
        import numpy as np
        import psutil
        torch = self.torch
        vectors, audit = [], []
        for original, text in zip(texts, prepared):
            if psutil.Process().memory_info().rss > 6 * 1024**3 or psutil.virtual_memory().available < 2 * 1024**3:
                raise RuntimeError("RAM guard: RSS >6 GiB or available RAM <2 GiB")
            tokens = self.tokenizer(text, add_special_tokens=True, truncation=False)["input_ids"]
            inputs = self.tokenizer([text], padding=True, truncation=True,
                                    max_length=MAX_TOKENS, return_tensors="pt").to(self.device)
            start = time.perf_counter()
            with torch.inference_mode():
                output = self.model(**inputs)
                v = torch.nn.functional.normalize(last_token_pool(output.last_hidden_state,
                                                                  inputs["attention_mask"]).float(), p=2, dim=1)
            v = v.cpu().numpy()[0]
            if v.shape != (DIMENSIONS,) or not np.isfinite(v).all() or abs(float(np.linalg.norm(v))-1) > 1e-5:
                raise RuntimeError("invalid embedding")
            vectors.append(v)
            audit.append({"role": role, "text_sha256": hashlib.sha256(original.encode()).hexdigest(),
                          "prepared_sha256": hashlib.sha256(text.encode()).hexdigest(),
                          "input_tokens": len(tokens), "used_tokens": min(len(tokens), MAX_TOKENS),
                          "truncated": len(tokens) > MAX_TOKENS,
                          "seconds": time.perf_counter()-start})
            del output, inputs
        self.audit.extend(audit)
        return np.stack(vectors).astype("float32")

    def resources(self):
        import psutil
        out = {"rss_bytes": psutil.Process().memory_info().rss, "events": list(self.events),
               "gpu_budget_mib": self.gpu_budget_mib, "device": self.device}
        if self.torch.cuda.is_available():
            out.update(cuda_peak_allocated_bytes=self.torch.cuda.max_memory_allocated(),
                       cuda_peak_reserved_bytes=self.torch.cuda.max_memory_reserved())
        return out
