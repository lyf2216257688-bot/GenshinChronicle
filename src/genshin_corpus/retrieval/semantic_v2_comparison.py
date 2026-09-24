"""Legacy direct-HTTP comparison for frozen B v2 historical evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
from html import escape
import json
import os
from pathlib import Path
import time
from typing import Any

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.collector.storage import atomic_write

from .semantic_compiler_u1 import SEMANTIC_OUTPUT_SCHEMA_IDENTITY, semantic_input_identity, semantic_output_schema
from .semantic_live_runner import (
    ACCEPTED_PREFLIGHT_IDENTITY,
    ChannelConfig,
    SemanticProviderRequest,
    SemanticProviderTransportError,
    SemanticResponseValidationError,
    _validate_raw_response,
    b_v2_experiment_contract,
    validate_b_v2_navigation_references,
)
from .semantic_openai_chat_adapter import OpenAIChatCompletionsAdapter


ORDINALS = (16, 20, 21)
MODELS = {"gemini": ("gemini_b", "Gemini", "Gemini 3.8 Flash", "gemini-3.8-flash", "Medium"),
          "deepseek": ("deepseek", "DeepSeek", "DeepSeek V4.1 Flash", "deepseek-v4.1-flash", "no separate control configured"),
          "glm": ("glm", "GLM", "GLM 5.3 Flash", "glm-5.3-flash", "no separate control configured")}
EXPERIMENT_ID = "50f7219556771d8698bc5f85f464bd58d2bdd84c5bbea748178ad45db7255e98"
PROMPT_ID = "25ebc483c5dfcfe9b3dc8ddd01001f75e98ff4c2b5327aa789a3af84a068003e"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, data: bytes) -> dict[str, Any]:
    if path.exists():
        raise ValueError(f"refusing to overwrite artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, data)
    return {"path": str(path), "sha256": _sha(data), "byte_count": len(data)}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _config(model: str) -> ChannelConfig:
    channel, _provider, alias, transport_model, reasoning = MODELS[model]
    return ChannelConfig(
        channel=channel, provider="JizhiAPI" if model == "deepseek" else ("TokenMetro" if model == "glm" else "Gemini"),
        endpoint="https://tokenmetro.com/v1", adapter_factory="genshin_corpus.retrieval.semantic_openai_chat_adapter:create_adapter",
        auth_mode="bearer", api_key_env="TOKENMETRO_API_KEY", model_alias=alias,
        transport_model=transport_model, reasoning_or_thinking=reasoning,
        structured_output_mode="JSON_OBJECT", generation_parameters={"temperature": 0.0},
        max_output_tokens=8192, timeout_seconds=120.0,
    )


def _frozen(preflight_root: Path) -> tuple[dict[str, Any], dict[int, tuple[dict[str, Any], dict[str, Any]]]]:
    preflight = _json(preflight_root / "preflight.json")
    if preflight.get("preflight_identity") != ACCEPTED_PREFLIGHT_IDENTITY or preflight.get("semantic_output_schema_identity") != SEMANTIC_OUTPUT_SCHEMA_IDENTITY:
        raise ValueError("frozen preflight or schema identity mismatch")
    from genshin_corpus.canonical.fingerprints import sha256_json
    if sha256_json({k: v for k, v in preflight.items() if k not in {"preflight_identity", "artifacts"}}) != ACCEPTED_PREFLIGHT_IDENTITY:
        raise ValueError("frozen preflight self-check failed")
    for name in ("gemini_units.json", "gemini_payloads.jsonl.gz"):
        data = (preflight_root / name).read_bytes()
        if _sha(data) != preflight["artifacts"][name]["sha256"]:
            raise ValueError(f"frozen artifact mismatch: {name}")
    rows = _json(preflight_root / "gemini_units.json")["items"]
    with gzip.open(preflight_root / "gemini_payloads.jsonl.gz", "rt", encoding="utf-8") as handle:
        payloads = [json.loads(line) for line in handle]
    if len(rows) != len(payloads) or len(rows) != 30:
        raise ValueError("frozen rows/payload count mismatch")
    selected = {}
    for row, payload in zip(rows, payloads):
        ordinal = row["gemini_request_ordinal"]
        if ordinal not in ORDINALS:
            continue
        if semantic_input_identity(payload) != row["semantic_input_identity"] or _sha(canonical_json_bytes(payload)) != row["payload_sha256"]:
            raise ValueError(f"frozen payload mismatch: {ordinal}")
        binding = row["ru_binding"]
        if not binding["eligible_for_live_sample"] or binding["ru_unbound_segment_count"] != 0 or binding["ru_bound_segment_count"] != row["segment_count"]:
            raise ValueError(f"unit not fully RU-bound: {ordinal}")
        selected[ordinal] = (row, payload)
    if set(selected) != set(ORDINALS):
        raise ValueError("frozen ordinal set mismatch")
    return preflight, selected


def prepare(preflight_root: Path, root: Path) -> dict[str, Any]:
    if root.exists():
        raise ValueError("comparison root already exists")
    preflight, units = _frozen(preflight_root)
    experiment = b_v2_experiment_contract()
    if experiment.identity != EXPERIMENT_ID or experiment.prompt_identity != PROMPT_ID or experiment.output_schema_identity != SEMANTIC_OUTPUT_SCHEMA_IDENTITY:
        raise ValueError("B v2 contract identity mismatch")
    requests = {}
    for model in MODELS:
        adapter = OpenAIChatCompletionsAdapter(_config(model), "offline-only")
        requests[model] = {}
        for ordinal, (row, payload) in units.items():
            request = SemanticProviderRequest(_config(model).channel, row["compilation_unit_id"], row["semantic_input_identity"], payload, experiment.prompt_contract, semantic_output_schema(), _config(model), ordinal)
            body = adapter.build_request_body(request)
            wire = json.loads(body)
            if wire["messages"][0]["content"] != canonical_json_bytes(experiment.prompt_contract).decode("utf-8") or wire["messages"][1]["content"] != canonical_json_bytes(payload).decode("utf-8"):
                raise ValueError("semantic messages differ from frozen input")
            requests[model][str(ordinal)] = {"request_sha256": _sha(body), "request_bytes": len(body)}
    root.mkdir(parents=True)
    report = {"status": "provider_free_ready", "created_at": _stamp(), "preflight_root": str(preflight_root.resolve()),
              "frozen_preflight_identity": preflight["preflight_identity"], "experiment_identity": experiment.identity,
              "prompt_identity": experiment.prompt_identity, "schema_identity": experiment.output_schema_identity,
              "ordinals": list(ORDINALS), "models": list(MODELS), "maximum_provider_requests": 9,
              "maximum_attempts_per_unit": 1, "automatic_retry": False, "output_budget_tokens": 8192,
              "requests": requests, "provider_requests_issued": 0, "network_calls_executed": 0}
    _write(root / "preflight.json", canonical_json_bytes(report))
    return report


def _ledger(root: Path, model: str) -> list[dict[str, Any]]:
    path = root / model / "attempt_ledger.json"
    return _json(path)["attempts"] if path.exists() else []


def _save_ledger(root: Path, model: str, rows: list[dict[str, Any]]) -> None:
    path = root / model / "attempt_ledger.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, canonical_json_bytes({"attempts": rows}))


def run_one(root: Path, model: str, ordinal: int, *, allow_legacy_live: bool = False) -> dict[str, Any]:
    if allow_legacy_live is not True:
        raise ValueError("legacy direct-HTTP live invocation requires explicit opt-in")
    if model not in MODELS or ordinal not in ORDINALS:
        raise ValueError("model or ordinal outside frozen comparison")
    report = _json(root / "preflight.json")
    if report.get("status") != "provider_free_ready" or report.get("experiment_identity") != EXPERIMENT_ID or report.get("maximum_provider_requests") != 9:
        raise ValueError("comparison preflight mismatch")
    _preflight, units = _frozen(Path(report["preflight_root"]))
    all_rows = {name: _ledger(root, name) for name in MODELS}
    rows = all_rows[model]
    if sum(len(group) for group in all_rows.values()) >= 9 or len(rows) >= 3:
        raise ValueError("request ceiling reached")
    if any(row["ordinal"] == ordinal for row in rows):
        raise ValueError("unit already attempted; retry forbidden")
    if any(row["status"] != "succeeded" for row in rows):
        raise ValueError("model stopped after prior failed or unresolved attempt")
    if ordinal != ORDINALS[len(rows)]:
        raise ValueError("ordinal16 must pass before ordinal20 and ordinal21")
    key = os.environ.get("TOKENMETRO_API_KEY")
    if not key:
        raise ValueError("TOKENMETRO_API_KEY is absent")
    row, payload = units[ordinal]
    experiment = b_v2_experiment_contract()
    config = _config(model)
    adapter = OpenAIChatCompletionsAdapter(config, key, allow_legacy_live=True)
    request = SemanticProviderRequest(config.channel, row["compilation_unit_id"], row["semantic_input_identity"], payload, experiment.prompt_contract, semantic_output_schema(), config, ordinal)
    body = adapter.build_request_body(request)
    if key.encode("utf-8") in body or _sha(body) != report["requests"][model][str(ordinal)]["request_sha256"]:
        raise ValueError("request contains secret or differs from provider-free preflight")
    stem = f"{ordinal:02d}"
    request_artifact = _write(root / model / stem / "request.json", body)
    attempt = {"model": model, "semantic_provider": MODELS[model][1], "transport": "TokenMetro OpenAI-compatible Chat Completions",
               "ordinal": ordinal, "compilation_unit_id": row["compilation_unit_id"], "semantic_input_identity": row["semantic_input_identity"],
               "experiment_identity": EXPERIMENT_ID, "prompt_identity": PROMPT_ID, "schema_identity": SEMANTIC_OUTPUT_SCHEMA_IDENTITY,
               "request_artifact": request_artifact, "issued_at": _stamp(), "status": "issued", "attempt_number": 1}
    rows.append(attempt)
    _save_ledger(root, model, rows)
    started = time.monotonic()
    try:
        response = adapter.invoke(request)
        raw = response.raw_response_bytes
        if key.encode("utf-8") in raw:
            raise ValueError("provider response echoed configured secret")
        attempt.update({"response_artifact": _write(root / model / stem / "raw_response.bin", raw),
                        "transport_status": response.transport_status, "provider_request_id": response.provider_request_id,
                        "finish_reason": response.finish_reason, "usage": response.usage or "UNKNOWN",
                        "response_received_at": _stamp(), "latency_ms": round((time.monotonic() - started) * 1000, 3)})
        envelope = json.loads(raw)
        usage = envelope.get("usage", {}) if isinstance(envelope, dict) else {}
        attempt["billing"] = {"reported_credit": usage.get("credit", "UNKNOWN") if isinstance(usage, dict) else "UNKNOWN",
                              "currency": "UNKNOWN", "billable": "UNKNOWN"}
        _save_ledger(root, model, rows)
        normalized, content, parsed, diagnostics = _validate_raw_response(adapter, raw, expected_segment_ids=row["segment_ids"])
        try:
            validate_b_v2_navigation_references(normalized)
            diagnostics["local_reference_validation"] = {"status": "passed"}
        except Exception as exc:
            diagnostics["local_reference_validation"] = {"status": "rejected", "error": str(exc)}
            raise SemanticResponseValidationError("local_reference_validation", exc, diagnostics, content=content, parsed=parsed) from exc
        attempt["canonical_output_artifact"] = _write(root / model / stem / "canonical_semantic_output.json", canonical_json_bytes(normalized))
        attempt["validation_artifact"] = _write(root / model / stem / "validation.json", canonical_json_bytes(diagnostics))
        attempt.update({"status": "succeeded", "disposition": "accepted_for_local_contract", "semantic_quality": "not_assessed"})
    except SemanticProviderTransportError as exc:
        raw = exc.raw_response_bytes
        if raw is not None and key.encode("utf-8") not in raw:
            attempt["response_artifact"] = _write(root / model / stem / "raw_response.bin", raw)
        attempt.update({"status": "failed", "disposition": "transport_failed", "transport_status": exc.transport_status,
                        "provider_request_id": exc.provider_request_id if exc.provider_request_id and key not in exc.provider_request_id else None,
                        "usage": exc.usage or "UNKNOWN", "billing": {"reported_credit": "UNKNOWN", "currency": "UNKNOWN", "billable": "UNKNOWN"},
                        "error": exc.code})
    except SemanticResponseValidationError as exc:
        attempt["validation_artifact"] = _write(root / model / stem / "validation.json", canonical_json_bytes(exc.diagnostics))
        if exc.parsed is not None:
            attempt["parsed_candidate_artifact"] = _write(root / model / stem / "parsed_candidate.json", canonical_json_bytes(exc.parsed))
        attempt.update({"status": "failed", "disposition": "rejected_fail_closed", "error": {"layer": exc.layer, "type": type(exc).__name__}})
    except Exception as exc:
        attempt.update({"status": "failed", "disposition": "local_or_transport_failure", "error": {"type": type(exc).__name__}})
    attempt["latency_ms"] = attempt.get("latency_ms", round((time.monotonic() - started) * 1000, 3))
    attempt["terminal_at"] = _stamp()
    _save_ledger(root, model, rows)
    return {k: v for k, v in attempt.items() if k not in {"request_artifact", "response_artifact", "canonical_output_artifact", "validation_artifact"}}


def review(root: Path) -> Path:
    report = _json(root / "preflight.json")
    _preflight, units = _frozen(Path(report["preflight_root"]))
    def block(value: Any) -> str:
        return "<pre>" + escape(json.dumps(value, ensure_ascii=False, indent=2) if not isinstance(value, str) else value) + "</pre>"

    def verified(descriptor: dict[str, Any]) -> bytes:
        path = Path(descriptor["path"])
        data = path.read_bytes()
        if _sha(data) != descriptor["sha256"] or len(data) != descriptor["byte_count"]:
            raise ValueError(f"review artifact integrity mismatch: {path}")
        return data

    lines = ["<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>",
             "<title>Phase 05 Prompt v2 comparison</title>",
             "<style>body{margin:0;color:#202322;background:#f4f6f5;font:14px/1.5 system-ui,sans-serif}header{padding:20px 28px;background:#142822;color:white}h1{font-size:22px;margin:0 0 8px}h2{font-size:18px;margin:0 0 8px}h3{font-size:15px;margin:0 0 10px}.meta{font-size:12px;overflow-wrap:anywhere}.unit{padding:22px 28px;border-bottom:1px solid #cbd4cf}.scroll{overflow-x:auto}.grid{display:grid;grid-template-columns:repeat(4,minmax(270px,1fr));gap:12px;min-width:1160px}.cell{background:white;border:1px solid #cad1cc;border-radius:5px;padding:14px;min-width:0}.cell.source{border-top:4px solid #197c68}.cell.gemini{border-top:4px solid #0b72aa}.cell.deepseek{border-top:4px solid #b25136}.cell.glm{border-top:4px solid #78599a}pre{font:12px/1.4 ui-monospace,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:520px;overflow:auto;background:#f5f7f6;padding:10px;border:1px solid #e1e6e3}details{margin:12px 0}summary{cursor:pointer;font-weight:600}p{margin:8px 0}.status{font-weight:700}.pass{color:#13725e}.fail{color:#a33522}.unknown{color:#626d68}code{font-size:12px;overflow-wrap:anywhere}@media(max-width:700px){header,.unit{padding:16px}.grid{min-width:1080px}}</style>",
             "<header><h1>Phase 05 Prompt v2 | 首轮三模型横评</h1>",
             f"<div class='meta'>Experiment {EXPERIMENT_ID}<br>Prompt {PROMPT_ID}<br>Schema {SEMANTIC_OUTPUT_SCHEMA_IDENTITY}</div>",
             "<p>机械通过不代表语义通过。横向滚动比较；每列保留原始响应和验证详情。</p></header>"]
    for ordinal, (unit, payload) in units.items():
        lines += [f"<section class='unit'><h2>Ordinal {ordinal}</h2>",
                  f"<p class='meta'>Unit {unit['compilation_unit_id']}<br>Input {unit['semantic_input_identity']}<br>Record {unit['record_id']} | {escape(str(payload.get('record_key')))}</p>",
                  "<div class='scroll'><div class='grid'><div class='cell source'><h3>Frozen source segments</h3>",
                  block(payload["segments"]), f"<details><summary>Input accounting / provenance</summary>{block({k: v for k, v in payload.items() if k != 'segments'})}{block(unit['ru_binding'])}</details></div>"]
        for model in MODELS:
            attempt = next((r for r in _ledger(root, model) if r["ordinal"] == ordinal), None)
            lines += [f"<div class='cell {model}'><h3>{model.title()}</h3>"]
            if attempt is None:
                lines += ["<p class='status unknown'>未尝试：首样本或前序请求触发停止</p></div>"]
                continue
            for name in ("request_artifact", "response_artifact", "canonical_output_artifact", "validation_artifact", "parsed_candidate_artifact"):
                if name in attempt:
                    verified(attempt[name])
            cls = "pass" if attempt["status"] == "succeeded" else "fail"
            lines += [f"<p class='status {cls}'>{escape(attempt['disposition'])}</p>",
                      f"<p>HTTP {escape(str(attempt.get('transport_status', 'UNKNOWN')))} | {escape(str(attempt.get('latency_ms', 'UNKNOWN')))} ms | finish {escape(str(attempt.get('finish_reason', 'UNKNOWN')))}</p>",
                      "<p>Semantic quality: <strong>not assessed</strong></p>",
                      "<details open><summary>Usage and billing</summary>", block({"usage": attempt.get("usage", "UNKNOWN"), "billing": attempt.get("billing", "UNKNOWN")}), "</details>"]
            if "validation_artifact" in attempt:
                lines += ["<details><summary>Validator / disposition</summary>", block(json.loads(verified(attempt["validation_artifact"]))), "</details>"]
            if "canonical_output_artifact" in attempt:
                lines += ["<h3>Canonical semantic output</h3>", block(json.loads(verified(attempt["canonical_output_artifact"])))]
            if "response_artifact" in attempt:
                raw = verified(attempt["response_artifact"]).decode("utf-8", errors="replace")
                lines += ["<details><summary>Original raw response</summary>", block(raw), "</details>"]
            lines += [f"<p class='meta'>Request: {escape(str(attempt['request_artifact']['path']))}<br>Ledger: {escape(str(root / model / 'attempt_ledger.json'))}</p></div>"]
        lines += ["</div></div></section>"]
    lines += ["</html>"]
    path = root / "review_bundle.html"
    _write(path, "\n".join(lines).encode("utf-8"))
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--preflight-root", type=Path, required=True)
    p.add_argument("--root", type=Path, required=True)
    p = sub.add_parser("run")
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--model", choices=tuple(MODELS), required=True)
    p.add_argument("--ordinal", type=int, choices=ORDINALS, required=True)
    p.add_argument("--legacy-direct-http", action="store_true", required=True)
    p = sub.add_parser("review")
    p.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.preflight_root, args.root) if args.command == "prepare" else (run_one(args.root, args.model, args.ordinal, allow_legacy_live=args.legacy_direct_http) if args.command == "run" else {"review_bundle": str(review(args.root))})
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
