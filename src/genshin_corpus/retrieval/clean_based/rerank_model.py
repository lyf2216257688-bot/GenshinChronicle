"""New CPython 3.14 offline adapter; frozen Phase04 adapters are unchanged."""
from importlib.metadata import version
import os
from pathlib import Path
import platform
import shutil
import struct
import sysconfig
import time

from ...evidence_io import digest, read, require, sha, write
from ..bge_reranker_v2_m3 import MODEL_WEIGHT_SHA256, MODEL_REVISION
from ..gte_multilingual_reranker import PINNED_MODEL_FILE_SHA256, PINNED_CUSTOM_CODE_SHA256
from ...indexing.benchmark_io import resources


def identity():
    require(platform.python_version() == "3.14.5", "Use CPython 3.14.5")
    require(platform.python_implementation() == "CPython" and platform.system() == "Windows"
            and platform.machine().upper() == "AMD64" and struct.calcsize("P") == 8
            and not sysconfig.get_config_var("Py_GIL_DISABLED"), "Use Windows AMD64 regular-GIL CPython")
    expected = {"torch": "2.11.0+cu128", "transformers": "4.57.3", "tokenizers": "0.22.2",
                "numpy": "2.5.3", "safetensors": "0.8.0"}
    actual = {k: version(k) for k in expected}
    require(actual == expected, "Unvalidated reranker dependencies: " + str(actual))
    return {"python": platform.python_version(), "platform": "windows-amd64", "gil_disabled": False,
            "packages": actual}


def verify_assets(repo, model):
    require(model in {"bge", "gte"}, "Unknown reranker")
    root = Path(repo) / ".local/models" / ("bge-reranker-v2-m3/" + MODEL_REVISION
                                           if model == "bge" else "gte-multilingual-reranker-base")
    if model == "gte":
        expected = {k: v for k, v in PINNED_MODEL_FILE_SHA256.items() if k != "README.md"}
        expected.update(PINNED_CUSTOM_CODE_SHA256)
    else:
        expected = {p.name: sha(p) for p in root.iterdir() if p.is_file() and p.suffix in {".json", ".model"}}
        expected["model.safetensors"] = MODEL_WEIGHT_SHA256
    require({"config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors"} <= set(expected),
            "Missing tokenizer/model assets")
    require(all(sha(root / name) == checksum for name, checksum in expected.items()), "Model snapshot changed")
    return root, expected


def stage_gte(root, output, files):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for name in files:
        if name == "config.json":
            config = read(root / name)
            config["auto_map"] = {k: v.split("--", 1)[-1] for k, v in config["auto_map"].items()}
            write(output / name, config)
        elif name == "model.safetensors":
            try:
                os.link(root / name, output / name)
            except OSError:
                # Restricted Windows filesystems may disallow hard links.
                shutil.copyfile(root / name, output / name)
            require(sha(output / name) == files[name], "Staged weight bytes changed")
        else:
            shutil.copyfile(root / name, output / name)
    return output


class LocalReranker:
    def __init__(self, repo, model, scratch):
        self.runtime = identity()
        self.key = model
        self.root, self.files = verify_assets(repo, model)
        self.path = stage_gte(self.root, Path(scratch) / "gte_stage", self.files) if model == "gte" else self.root
        from transformers import AutoTokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(str(self.path), local_files_only=True)
        self.tokenizer.padding_side = "right"
        config = read(self.path / "config.json")
        self.cap = config["max_position_embeddings"] - (2 if model == "bge" else 0)
        self.model = None
        self.load_seconds = 0.
        self.load_resources = None
        self.binding = {"model": model, "files": self.files, "runtime": self.runtime,
                        "dtype": "float16", "device": "cuda", "attention": "eager", "batch_size": 1,
                        "pairing": "query_full_retrieval_text", "truncation": False}

    def count(self, question, text):
        return len(self.tokenizer([[question, text]], padding=False, truncation=False)["input_ids"][0])

    def load(self):
        if self.model is not None:
            return
        import torch
        from transformers import AutoModelForSequenceClassification
        require(torch.cuda.is_available(), "CUDA unavailable; no CPU fallback")
        torch.cuda.reset_peak_memory_stats()
        tick = time.perf_counter()
        self.model = AutoModelForSequenceClassification.from_pretrained(
            str(self.path), local_files_only=True, trust_remote_code=self.key == "gte",
            torch_dtype=torch.float16, attn_implementation="eager").to("cuda").eval()
        require(all(str(p.device).startswith("cuda") and p.dtype == torch.float16
                    for p in self.model.parameters()), "Model must be fully FP16 CUDA")
        torch.cuda.synchronize()
        self.load_seconds = time.perf_counter() - tick
        self.load_resources = resources()

    def score(self, bundle, budget, limits=None):
        import math
        import torch
        require(0 < budget <= self.cap, "Pair budget exceeds model capability")
        cold_model = self.model is None
        self.load()
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        scores, audits = {}, []
        for candidate in bundle["candidates"]:
            before = resources()
            floor = (limits or {}).get("minimum_running_ram_bytes")
            require(floor is None or before.get("available_ram_bytes", 0) >= floor, "Configured running RAM protection")
            tick = time.perf_counter()
            text = candidate["chunk"]["retrieval_text"]
            count = self.count(bundle["query"]["question"], text)
            require(count <= budget, "Overlength pair; no truncation or candidate omission")
            tokens = self.tokenizer([[bundle["query"]["question"], text]], padding=True,
                                    truncation=False, return_tensors="pt")
            require(int(tokens["attention_mask"].sum()) == count, "Token count changed")
            token_seconds = time.perf_counter() - tick
            tokens = {k: v.to("cuda") for k, v in tokens.items()}
            torch.cuda.synchronize()
            tick = time.perf_counter()
            with torch.inference_mode():
                logits = self.model(**tokens, return_dict=True).logits
            torch.cuda.synchronize()
            forward_seconds = time.perf_counter() - tick
            require(logits.numel() == 1, "Expected one scalar logit")
            value = float(logits.reshape(-1)[0].float().cpu())
            require(math.isfinite(value), "Nonfinite reranker score")
            cid = candidate["chunk_id"]
            require(cid not in scores, "Duplicate candidate")
            scores[cid] = value
            audits.append({"chunk_id": cid, "tokens": count, "tokenization_seconds": token_seconds,
                           "forward_seconds": forward_seconds})
        observation = resources()
        import psutil
        info = psutil.Process().memory_info()
        observation["process_peak_working_set_bytes"] = getattr(info, "peak_wset", None)
        return {"scores": scores, "audit": audits, "binding": self.binding,
                "candidate_set_sha256": digest(bundle), "load_seconds": self.load_seconds if cold_model else 0.,
                "load_resources": self.load_resources if cold_model else None,
                "cold_model": cold_model,
                "elapsed_seconds": time.perf_counter() - started, "resources": observation}
