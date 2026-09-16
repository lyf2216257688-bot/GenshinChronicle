"""Explicit, bounded local Qwen3 reranker smoke/equivalence runner.

The runner never downloads a model.  It requires a pre-materialized manifest
and uses ``local_files_only=True`` through the adapter.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from typing import Any

from .qwen3_local_reranker import (
    MODEL_ID,
    Qwen3LocalReranker,
    Qwen3LocalRerankerConfig,
    Qwen3LocalRerankerError,
    token_budget_ids,
    verify_local_model_manifest,
)


LADDER = (512, 1024, 2048, 4096, 8192)


def _nvidia_memory() -> dict[str, Any]:
    try:
        raw = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.free,memory.total", "--format=csv,noheader,nounits"],
            text=True,
            stderr=subprocess.STDOUT,
        ).strip().splitlines()[0]
        used, free, total = (int(item.strip()) for item in raw.split(","))
        return {"used_mib": used, "free_mib": free, "total_mib": total}
    except Exception as exc:
        return {"status": "unavailable", "error_type": type(exc).__name__}


def _runtime_identity() -> dict[str, Any]:
    try:
        import torch
        import transformers
        import huggingface_hub
        import safetensors

        return {
            "python": sys.executable,
            "python_version": sys.version,
            "torch": torch.__version__,
            "torch_cuda_build": torch.version.cuda,
            "cuda_available": torch.cuda.is_available(),
            "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "capability": list(torch.cuda.get_device_capability(0)) if torch.cuda.is_available() else None,
            "total_vram_bytes": torch.cuda.get_device_properties(0).total_memory if torch.cuda.is_available() else None,
            "bf16_supported": torch.cuda.is_bf16_supported() if torch.cuda.is_available() else None,
            "sdpa_available": hasattr(torch.nn.functional, "scaled_dot_product_attention"),
            "transformers": transformers.__version__,
            "huggingface_hub": huggingface_hub.__version__,
            "safetensors": safetensors.__version__,
        }
    except Exception as exc:
        return {"status": "unavailable", "error_type": type(exc).__name__, "message": str(exc)}


def _memory_snapshot() -> dict[str, Any]:
    try:
        import torch

        return {
            "cuda_allocated_bytes": torch.cuda.memory_allocated(),
            "cuda_reserved_bytes": torch.cuda.memory_reserved(),
            "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
            "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved(),
            "nvidia_smi": _nvidia_memory(),
        }
    except Exception as exc:
        return {"status": "unavailable", "error_type": type(exc).__name__}


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def build_model_manifest(*, model_root: Path, revision: str, output_path: Path | None = None) -> dict[str, Any]:
    """Create the deterministic file inventory after an authorized snapshot download."""

    root = Path(model_root).resolve()
    if len(revision) != 40 or any(char not in "0123456789abcdefABCDEF" for char in revision):
        raise Qwen3LocalRerankerError("model revision must be a 40-character commit SHA")
    if not root.is_dir():
        raise Qwen3LocalRerankerError("model root is missing")
    files: list[dict[str, Any]] = []
    total_bytes = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name != "manifest.json"):
        relative = path.relative_to(root).as_posix()
        digest_builder = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest_builder.update(chunk)
        digest = digest_builder.hexdigest()
        size = path.stat().st_size
        total_bytes += size
        files.append({"path": relative, "sha256": digest, "byte_count": size})
    config = {}
    config_path = root / "config.json"
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise Qwen3LocalRerankerError("model config is not valid JSON") from exc
    manifest: dict[str, Any] = {
        "schema_version": "phase04-qwen3-local-model-manifest-v1",
        "model": MODEL_ID,
        "revision": revision.lower(),
        "checkpoint_dtype": config.get("torch_dtype"),
        "files": files,
        "total_bytes": total_bytes,
        "identity": hashlib.sha256(json.dumps({"model": MODEL_ID, "revision": revision.lower(), "files": files}, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }
    if output_path is not None:
        _write_json(Path(output_path), manifest)
    return manifest


def run_smoke(*, model_path: Path, manifest_path: Path, revision: str, output_root: Path) -> dict[str, Any]:
    before = _nvidia_memory()
    manifest = verify_local_model_manifest(manifest_path)
    config = Qwen3LocalRerankerConfig(model_path=Path(model_path), revision=revision)
    adapter = Qwen3LocalReranker.from_local_manifest(config, manifest_path)
    adapter._lazy_load()
    runtime = _runtime_identity()
    if runtime.get("cuda_available") is not True:
        raise Qwen3LocalRerankerError("CUDA is unavailable")
    if runtime.get("bf16_supported") is not True and config.dtype == "bfloat16":
        raise Qwen3LocalRerankerError("configured BF16 is not supported")

    pairs = [("谁带来了火之国的消息？", "旅行者在纳塔遇见了新的伙伴。"), ("谁带来了火之国的消息？", "这是关于璃月烹饪大赛的公告。")]
    equivalence_rows: list[dict[str, Any]] = []
    for query, document in pairs:
        input_ids, audit = token_budget_ids(adapter._tokenizer, query, document, max_length=512)  # type: ignore[arg-type]
        reference = adapter.score_input_ids([input_ids], logits_to_keep=False)[0]
        optimized = adapter.score_input_ids([input_ids], logits_to_keep=True)[0]
        equivalence_rows.append({"query": query, "document": document, "audit": audit, "reference": reference, "optimized": optimized, "absolute_difference": abs(reference - optimized)})
    reference_order = sorted(range(len(equivalence_rows)), key=lambda index: -equivalence_rows[index]["reference"])
    optimized_order = sorted(range(len(equivalence_rows)), key=lambda index: -equivalence_rows[index]["optimized"])
    equivalence = {
        "status": "pass" if reference_order == optimized_order and all(row["absolute_difference"] <= 5e-3 for row in equivalence_rows) else "failed",
        "tolerance": 5e-3,
        "ordering_reference": reference_order,
        "ordering_optimized": optimized_order,
        "rows": equivalence_rows,
    }

    ladder: list[dict[str, Any]] = []
    for max_length in LADDER:
        if max_length > config.max_length:
            break
        try:
            input_ids, audit = token_budget_ids(adapter._tokenizer, pairs[0][0], pairs[0][1] * 3000, max_length=max_length)  # type: ignore[arg-type]
            if hasattr(__import__("torch"), "cuda"):
                import torch

                torch.cuda.reset_peak_memory_stats()
            started = perf_counter()
            score = adapter.score_input_ids([input_ids], logits_to_keep=True)[0]
            elapsed = perf_counter() - started
            ladder.append({"requested_max_length": max_length, "actual_tokenized_length": audit["actual_token_count"], "truncated": audit["truncated"], "wall_seconds": elapsed, "score": score, "status": "succeeded", "memory": _memory_snapshot()})
        except Exception as exc:
            ladder.append({"requested_max_length": max_length, "status": "failed", "error_type": type(exc).__name__})
            break

    representative = "中文" * 3000
    _, representative_audit = token_budget_ids(adapter._tokenizer, "代表性问题", representative, max_length=config.max_length)  # type: ignore[arg-type]
    result = {
        "schema_version": "phase04-qwen3-local-smoke-v1",
        "model": MODEL_ID,
        "revision": revision,
        "runtime_identity": adapter.runtime_identity(),
        "manifest": manifest,
        "runtime": runtime,
        "gpu_memory_before_load": before,
        "gpu_memory_after_load": _nvidia_memory(),
        "equivalence": equivalence,
        "ladder": ladder,
        "representative_6000_character_projection": {"projected_character_count": 6000, "token_audit": representative_audit},
        "provider_api_calls": 0,
        "network_calls_after_materialization": 0,
    }
    _write_json(Path(output_root) / "smoke.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an explicit local Qwen3 reranker smoke")
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run_smoke(model_path=args.model_path, manifest_path=args.manifest, revision=args.revision, output_root=args.output_root)
    except Exception as exc:
        print(f"BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": result["equivalence"]["status"], "output_root": str(args.output_root)}, ensure_ascii=False))
    return 0 if result["equivalence"]["status"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
