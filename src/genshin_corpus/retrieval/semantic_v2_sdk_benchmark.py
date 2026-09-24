"""One-shot SDK benchmark for the three frozen Phase 05 Prompt v2 units."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

import httpx
import openai
from openai import OpenAI

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from .semantic_compiler_u1 import SEMANTIC_OUTPUT_SCHEMA_IDENTITY, SEMANTIC_OUTPUT_SCHEMA_VERSION
from .semantic_live_runner import (
    SemanticResponseValidationError,
    _validate_raw_response,
    b_v2_experiment_contract,
    validate_b_v2_navigation_references,
)
from .semantic_openai_chat_adapter import OpenAIChatCompletionsAdapter
from .semantic_v2_comparison import MODELS, ORDINALS, _frozen


BASE_URL = "https://tokenmetro.com/v1"
MAX_TOKENS = 16384
TIMEOUT_SECONDS = 300.0
PROMPT_ID = "25ebc483c5dfcfe9b3dc8ddd01001f75e98ff4c2b5327aa789a3af84a068003e"
SEMANTIC_EXPERIMENT_ID = "50f7219556771d8698bc5f85f464bd58d2bdd84c5bbea748178ad45db7255e98"


class _LocalParser:
    extract_content = OpenAIChatCompletionsAdapter.extract_content
    parse_content = OpenAIChatCompletionsAdapter.parse_content


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _new(path: Path, data: bytes) -> dict[str, Any]:
    if path.exists():
        raise ValueError(f"artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, data)
    return {"path": str(path.resolve()), "sha256": _sha(data), "byte_count": len(data)}


def _new_json(path: Path, value: Any) -> dict[str, Any]:
    return _new(path, canonical_json_bytes(value))


def _checked(descriptor: Mapping[str, Any], secret: str | None = None) -> bytes:
    data = Path(str(descriptor["path"])).read_bytes()
    if secret and secret.encode("utf-8") in data:
        raise ValueError("persisted artifact contains configured secret")
    if _sha(data) != descriptor["sha256"] or len(data) != descriptor["byte_count"]:
        raise ValueError("artifact integrity mismatch")
    return data


def _checkpoint(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise ValueError("aborted r1 checkpoint missing")
    expected = {"gemini": [(16, "succeeded"), (20, "failed")], "deepseek": [(16, "failed")], "glm": [(16, "failed")]}
    ledgers: dict[str, str] = {}
    for model, wanted in expected.items():
        path = root / model / "attempt_ledger.json"
        data = path.read_bytes()
        attempts = json.loads(data)["attempts"]
        if [(a["ordinal"], a["status"]) for a in attempts] != wanted:
            raise ValueError("aborted r1 attempt accounting changed")
        for attempt in attempts:
            for key, descriptor in attempt.items():
                if key.endswith("_artifact"):
                    _checked(descriptor, os.environ.get("TOKENMETRO_API_KEY"))
        ledgers[model] = _sha(data)
    return {"root": str(root.resolve()), "preflight_sha256": _sha((root / "preflight.json").read_bytes()),
            "attempt_count": 4, "ledger_sha256": ledgers, "status": "aborted_immutable_checkpoint"}


def _messages(prompt: Mapping[str, Any], payload: Mapping[str, Any]) -> list[dict[str, str]]:
    return [{"role": "system", "content": canonical_json_bytes(prompt).decode("utf-8")},
            {"role": "user", "content": canonical_json_bytes(payload).decode("utf-8")}]


def _model_id(model: str) -> str:
    return MODELS[model][3]


def _mock_request(model: str, messages: list[dict[str, str]]) -> bytes:
    captured: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) != f"{BASE_URL}/chat/completions" or request.method != "POST":
            raise ValueError("SDK endpoint differs from frozen TokenMetro route")
        captured.append(request.content)
        return httpx.Response(200, json={"id": "offline", "object": "chat.completion", "created": 0,
                                     "model": _model_id(model), "choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant", "content": "{}"}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        with OpenAI(base_url=BASE_URL, api_key="offline-placeholder", max_retries=0,
                    timeout=TIMEOUT_SECONDS, http_client=transport) as client:
            client.chat.completions.create(model=_model_id(model), messages=messages, max_tokens=MAX_TOKENS)
    if len(captured) != 1:
        raise ValueError("SDK mock issued an unexpected request count")
    body = json.loads(captured[0])
    if set(body) != {"model", "messages", "max_tokens"} or body["messages"] != messages or body["max_tokens"] != MAX_TOKENS:
        raise ValueError("SDK request differs from minimal common operating point")
    return captured[0]


def prepare(frozen_root: Path, aborted_root: Path, root: Path) -> dict[str, Any]:
    if root.exists():
        raise ValueError("fresh benchmark root already exists")
    frozen_root, aborted_root = frozen_root.resolve(), aborted_root.resolve()
    preflight, units = _frozen(frozen_root)
    checkpoint = _checkpoint(aborted_root)
    experiment = b_v2_experiment_contract()
    if (experiment.identity, experiment.prompt_identity, experiment.output_schema_identity) != (
        SEMANTIC_EXPERIMENT_ID, PROMPT_ID, SEMANTIC_OUTPUT_SCHEMA_IDENTITY
    ):
        raise ValueError("frozen semantic contract identity mismatch")
    requests: dict[str, dict[str, Any]] = {model: {} for model in MODELS}
    parser = _LocalParser()
    for ordinal, (unit, payload) in units.items():
        messages = _messages(experiment.prompt_contract, payload)
        covered = {"schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION, "items": [],
                   "segment_coverage": [{"segment_id": sid, "disposition": "covered", "reason": None}
                                        for sid in unit["segment_ids"]]}
        fake = canonical_json_bytes({"choices": [{"message": {"content": canonical_json_bytes(covered).decode("utf-8")}}]})
        normalized, _, _, diagnostics = _validate_raw_response(parser, fake, expected_segment_ids=unit["segment_ids"])
        validate_b_v2_navigation_references(normalized)
        if diagnostics["terminal_disposition"] != "accepted_for_local_contract":
            raise ValueError("offline validator did not accept exact source binding")
        try:
            _validate_raw_response(parser, fake, expected_segment_ids=["not-a-supplied-segment"])
        except SemanticResponseValidationError as exc:
            if exc.layer != "source_binding_validation":
                raise
        else:
            raise ValueError("offline validator accepted wrong source binding")
        for model in MODELS:
            body = _mock_request(model, messages)
            requests[model][str(ordinal)] = {"request_sha256": _sha(body), "request_bytes": len(body),
                                              "semantic_input_identity": unit["semantic_input_identity"],
                                              "compilation_unit_id": unit["compilation_unit_id"]}
    identity_body = {"semantic_experiment_identity": SEMANTIC_EXPERIMENT_ID,
                     "prompt_identity": PROMPT_ID, "schema_identity": SEMANTIC_OUTPUT_SCHEMA_IDENTITY,
                     "frozen_preflight_identity": preflight["preflight_identity"],
                     "semantic_inputs": [units[n][0]["semantic_input_identity"] for n in ORDINALS],
                     "ordinals": list(ORDINALS), "models": {name: _model_id(name) for name in MODELS},
                     "transport": "openai-python-sdk-chat-completions", "sdk_version": openai.__version__,
                     "base_url": BASE_URL, "body_fields": ["model", "messages", "max_tokens"],
                     "max_tokens": MAX_TOKENS, "timeout_seconds": TIMEOUT_SECONDS,
                     "max_retries": 0, "validator": "local_strict_json_schema_source_binding_and_v2_local_refs"}
    benchmark_identity = sha256_json(identity_body)
    created_at = _now()
    report = {"status": "provider_free_ready", "created_at": created_at,
              "benchmark_identity": benchmark_identity,
              "run_identity": sha256_json({"benchmark_identity": benchmark_identity, "root": str(root.resolve()), "created_at": created_at}),
              "identity_contract": identity_body, "prior_checkpoint": checkpoint,
              "frozen_root": str(frozen_root), "requests": requests,
              "maximum_provider_requests": 9, "one_unit_one_request": True, "automatic_retry": False,
              "provider_requests_issued": 0, "network_calls_executed": 0,
              "offline_validator_positive_and_negative_passed": True,
              "same_messages_across_models": True}
    root.mkdir(parents=True)
    _new_json(root / "preflight.json", report)
    return {k: report[k] for k in ("status", "benchmark_identity", "run_identity", "maximum_provider_requests",
                                   "provider_requests_issued", "network_calls_executed", "same_messages_across_models")}


def _ledger(root: Path, model: str) -> list[dict[str, Any]]:
    path = root / model / "attempt_ledger.json"
    return _read(path)["attempts"] if path.exists() else []


def _save_ledger(root: Path, model: str, attempts: list[dict[str, Any]]) -> None:
    path = root / model / "attempt_ledger.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, canonical_json_bytes({"attempts": attempts}))


def gate(root: Path, ordinal: int) -> dict[str, Any]:
    if ordinal not in (16, 20):
        raise ValueError("only ordinal16 and ordinal20 have progression gates")
    report = _read(root / "preflight.json")
    if report["status"] != "provider_free_ready":
        raise ValueError("benchmark preflight is not ready")
    if ordinal == 20 and not (root / "gate_16.json").exists():
        raise ValueError("ordinal16 review gate missing")
    rows = {model: _ledger(root, model) for model in MODELS}
    if ordinal == 16 and not all(any(a["ordinal"] == 16 and a["status"] != "issued" for a in attempts)
                                 for attempts in rows.values()):
        raise ValueError("all three ordinal16 terminals are required before review")
    if ordinal == 20 and any(attempts and attempts[-1]["status"] == "issued" for attempts in rows.values()):
        raise ValueError("unresolved attempt prevents ordinal20 review")
    eligible = [model for model, attempts in rows.items()
                if any(a["ordinal"] == ordinal and a["status"] == "succeeded" for a in attempts)]
    result = {"reviewed_at": _now(), "benchmark_identity": report["benchmark_identity"],
              "completed_ordinal": ordinal, "eligible_next_models": eligible,
              "mechanical_results": {model: [{"ordinal": a["ordinal"], "status": a["status"],
                                               "disposition": a["disposition"]} for a in attempts]
                                     for model, attempts in rows.items()},
              "ledger_sha256": {model: _sha((root / model / "attempt_ledger.json").read_bytes())
                                if (root / model / "attempt_ledger.json").exists() else None for model in MODELS},
              "semantic_quality": "not_assessed"}
    _new_json(root / f"gate_{ordinal}.json", result)
    return result


def run_one(root: Path, model: str, ordinal: int) -> dict[str, Any]:
    if model not in MODELS or ordinal not in ORDINALS:
        raise ValueError("model or ordinal outside frozen benchmark")
    report = _read(root / "preflight.json")
    identity = report["identity_contract"]
    if (report["status"] != "provider_free_ready" or report["benchmark_identity"] != sha256_json(identity)
            or identity["max_tokens"] != MAX_TOKENS or identity["body_fields"] != ["model", "messages", "max_tokens"]
            or identity["sdk_version"] != openai.__version__):
        raise ValueError("benchmark identity or SDK runtime mismatch")
    _checkpoint(Path(report["prior_checkpoint"]["root"]))
    _, units = _frozen(Path(report["frozen_root"]))
    rows_by_model = {name: _ledger(root, name) for name in MODELS}
    attempts = rows_by_model[model]
    if sum(len(value) for value in rows_by_model.values()) >= 9 or len(attempts) >= 3:
        raise ValueError("nine-request ceiling reached")
    if any(a["ordinal"] == ordinal for a in attempts) or any(a["status"] != "succeeded" for a in attempts):
        raise ValueError("attempt already consumed or model stopped")
    if ordinal != ORDINALS[len(attempts)]:
        raise ValueError("model unit order differs from frozen ordinal order")
    if ordinal != 16:
        previous = ORDINALS[ORDINALS.index(ordinal) - 1]
        review_gate = _read(root / f"gate_{previous}.json")
        if review_gate["benchmark_identity"] != report["benchmark_identity"] or model not in review_gate["eligible_next_models"]:
            raise ValueError("prior mechanical review gate did not admit model")
    key = os.environ.get("TOKENMETRO_API_KEY")
    if not key:
        raise ValueError("TOKENMETRO_API_KEY is absent")
    unit, payload = units[ordinal]
    experiment = b_v2_experiment_contract()
    if experiment.identity != SEMANTIC_EXPERIMENT_ID or experiment.prompt_identity != PROMPT_ID:
        raise ValueError("frozen Prompt v2 identity changed")
    messages = _messages(experiment.prompt_contract, payload)
    stem = root / model / f"{ordinal:02d}"
    attempt: dict[str, Any] = {"benchmark_identity": report["benchmark_identity"],
                              "run_identity": report["run_identity"], "model": model, "transport_model": _model_id(model),
                              "ordinal": ordinal, "compilation_unit_id": unit["compilation_unit_id"],
                              "semantic_input_identity": unit["semantic_input_identity"],
                              "prompt_identity": PROMPT_ID, "schema_identity": SEMANTIC_OUTPUT_SCHEMA_IDENTITY,
                              "status": "issued", "disposition": "not_started", "issued_at": _now(),
                              "provider_requests_issued": 0, "automatic_retry": False}
    attempts.append(attempt)
    _save_ledger(root, model, attempts)
    started = time.monotonic()
    raw: bytes | None = None

    def on_request(request: httpx.Request) -> None:
        if attempt["provider_requests_issued"]:
            raise ValueError("SDK tried to issue more than one request")
        body = request.content
        if key.encode("utf-8") in body:
            raise ValueError("request body contains configured secret")
        if _sha(body) != report["requests"][model][str(ordinal)]["request_sha256"]:
            raise ValueError("SDK wire body differs from zero-network preflight")
        attempt["request_artifact"] = _new(stem / "request.json", body)
        attempt["request_metadata_artifact"] = _new_json(stem / "request_metadata.json", {
            "method": request.method, "url": str(request.url), "authorization_scheme": "Bearer",
            "headers": {k: v for k, v in request.headers.items()
                        if k.lower() in {"accept", "content-type", "user-agent"} or k.lower().startswith("x-stainless-")},
        })
        attempt["provider_requests_issued"] = 1
        _save_ledger(root, model, attempts)

    def on_response(response: httpx.Response) -> None:
        nonlocal raw
        raw = response.read()
        if key.encode("utf-8") in raw:
            raise ValueError("response echoed configured secret")
        attempt.update({"raw_response_artifact": _new(stem / "raw_response.bin", raw),
                        "http_status": response.status_code, "response_received_at": _now(),
                        "response_headers": {k: v for k, v in response.headers.items()
                                             if k.lower() in {"content-type", "x-request-id"}}})
        _save_ledger(root, model, attempts)

    try:
        with httpx.Client(event_hooks={"request": [on_request], "response": [on_response]}) as transport:
            with OpenAI(base_url=BASE_URL, api_key=key, max_retries=0, timeout=TIMEOUT_SECONDS,
                        http_client=transport) as client:
                sdk_response = client.chat.completions.create(model=_model_id(model), messages=messages,
                                                              max_tokens=MAX_TOKENS)
        if raw is None or attempt.get("http_status") != 200 or attempt["provider_requests_issued"] != 1:
            raise ValueError("SDK result has incomplete response accounting")
        attempt["sdk_status"] = "success"
        envelope = json.loads(raw)
        choice = envelope.get("choices", [{}])[0]
        attempt["finish_reason"] = choice.get("finish_reason", "UNKNOWN")
        usage = envelope.get("usage")
        attempt["usage"] = usage if isinstance(usage, dict) else "UNKNOWN"
        attempt["billing"] = {"reported_credit": usage.get("credit", "UNKNOWN") if isinstance(usage, dict) else "UNKNOWN",
                              "currency": "UNKNOWN", "billable": "UNKNOWN"}
        _save_ledger(root, model, attempts)
        try:
            normalized, content, parsed, diagnostics = _validate_raw_response(
                _LocalParser(), raw, expected_segment_ids=unit["segment_ids"]
            )
            try:
                validate_b_v2_navigation_references(normalized)
                diagnostics["local_reference_validation"] = {"status": "passed"}
            except Exception as exc:
                diagnostics["local_reference_validation"] = {"status": "rejected", "error": str(exc)}
                raise SemanticResponseValidationError("local_reference_validation", exc, diagnostics,
                                                      content=content, parsed=parsed) from exc
            if attempt["finish_reason"] != "stop":
                diagnostics["terminal_disposition"] = "output_incomplete"
                raise SemanticResponseValidationError("finish_reason", ValueError("finish reason is not stop"), diagnostics,
                                                      content=content, parsed=parsed)
            attempt["canonical_output_artifact"] = _new_json(stem / "canonical_semantic_output.json", normalized)
            attempt["validation_artifact"] = _new_json(stem / "validation.json", diagnostics)
            attempt.update({"status": "succeeded", "disposition": "accepted_for_local_contract",
                            "semantic_quality": "not_assessed"})
        except SemanticResponseValidationError as exc:
            diagnostics = dict(exc.diagnostics)
            diagnostics["terminal_disposition"] = "rejected_fail_closed"
            diagnostics["finish_reason"] = attempt["finish_reason"]
            attempt["validation_artifact"] = _new_json(stem / "validation.json", diagnostics)
            if exc.parsed is not None:
                attempt["parsed_candidate_artifact"] = _new_json(stem / "parsed_candidate.json", exc.parsed)
            attempt.update({"status": "failed", "disposition": "output_budget_failure" if attempt["finish_reason"] == "length" else "rejected_fail_closed",
                            "failure_layer": exc.layer, "semantic_quality": "not_assessed"})
    except Exception as exc:
        if attempt["status"] == "issued":
            attempt.update({"status": "failed", "disposition": "http_or_sdk_failure" if attempt.get("http_status") != 200 else "sdk_response_failure",
                            "sdk_status": type(exc).__name__, "semantic_quality": "not_assessed"})
            if raw is not None:
                try:
                    envelope = json.loads(raw)
                    if isinstance(envelope, dict):
                        attempt["usage"] = envelope.get("usage", "UNKNOWN")
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
    attempt["latency_ms"] = round((time.monotonic() - started) * 1000, 3)
    attempt["terminal_at"] = _now()
    _save_ledger(root, model, attempts)
    return {k: v for k, v in attempt.items() if not k.endswith("_artifact")}


def review(root: Path) -> Path:
    report = _read(root / "preflight.json")
    _, units = _frozen(Path(report["frozen_root"]))
    lines = ["<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
             "<title>Phase 05 Prompt v2 SDK comparison</title>",
             "<style>body{margin:0;background:#f3f6f4;color:#202723;font:14px/1.45 system-ui,sans-serif}header,section{padding:18px 24px}header{background:#18352e;color:#fff}h1{font-size:22px;margin:0 0 8px}h2{font-size:18px;margin:0 0 10px}h3{font-size:15px;margin:0 0 8px}.meta{font-size:12px;overflow-wrap:anywhere}.scroll{overflow-x:auto}.grid{display:grid;grid-template-columns:repeat(4,minmax(275px,1fr));gap:10px;min-width:1160px}.cell{background:#fff;border:1px solid #ced8d2;border-top:4px solid #17866e;border-radius:4px;padding:12px;min-width:0}.gemini{border-top-color:#1875af}.deepseek{border-top-color:#b75c3b}.glm{border-top-color:#755ba4}section{border-bottom:1px solid #ced8d2}pre{background:#f6f8f7;padding:9px;border:1px solid #e2e8e4;white-space:pre-wrap;overflow-wrap:anywhere;max-height:480px;overflow:auto;font:12px/1.4 Consolas,monospace}details{margin:10px 0}summary{cursor:pointer;font-weight:600}.ok{color:#166f57}.bad{color:#a33e2e}</style>",
             f"<header><h1>Phase 05 Prompt v2 | SDK 三模型审阅</h1><div class='meta'>Benchmark {report['benchmark_identity']}<br>Prompt {PROMPT_ID}<br>Schema {SEMANTIC_OUTPUT_SCHEMA_IDENTITY}<br>max_tokens {MAX_TOKENS}</div><p>机械通过不代表语义通过；横向滚动查看四列。</p></header>"]

    def pre(value: Any) -> str:
        return "<pre>" + escape(json.dumps(value, ensure_ascii=False, indent=2) if not isinstance(value, str) else value) + "</pre>"

    for ordinal, (unit, payload) in units.items():
        lines += [f"<section><h2>Ordinal {ordinal}</h2><p class='meta'>Unit {unit['compilation_unit_id']} | Input {unit['semantic_input_identity']} | Record {unit['record_id']}</p>",
                  "<div class='scroll'><div class='grid'><div class='cell'><h3>Frozen source segments</h3>", pre(payload["segments"]),
                  f"<details><summary>Input and RU provenance</summary>{pre({k:v for k,v in payload.items() if k != 'segments'})}{pre(unit['ru_binding'])}</details></div>"]
        for model in MODELS:
            attempt = next((a for a in _ledger(root, model) if a["ordinal"] == ordinal), None)
            lines += [f"<div class='cell {model}'><h3>{model.title()}</h3>"]
            if attempt is None:
                lines += ["<p>Not attempted under the mechanical stop gate.</p></div>"]
                continue
            for name, descriptor in attempt.items():
                if name.endswith("_artifact"):
                    _checked(descriptor, os.environ.get("TOKENMETRO_API_KEY"))
            css = "ok" if attempt["status"] == "succeeded" else "bad"
            lines += [f"<p class='{css}'><strong>{escape(attempt['disposition'])}</strong></p>",
                      f"<p>HTTP {escape(str(attempt.get('http_status','UNKNOWN')))} | SDK {escape(str(attempt.get('sdk_status','UNKNOWN')))} | finish {escape(str(attempt.get('finish_reason','UNKNOWN')))} | {escape(str(attempt.get('latency_ms','UNKNOWN')))} ms</p>",
                      "<p>Semantic quality: not assessed</p>",
                      f"<details open><summary>Usage / billing</summary>{pre({'usage':attempt.get('usage','UNKNOWN'),'billing':attempt.get('billing','UNKNOWN')})}</details>"]
            if "canonical_output_artifact" in attempt:
                lines += ["<h3>Canonical semantic output</h3>", pre(json.loads(_checked(attempt["canonical_output_artifact"])))]
            if "validation_artifact" in attempt:
                lines += [f"<details><summary>Local validator</summary>{pre(json.loads(_checked(attempt['validation_artifact'])))}</details>"]
            if "raw_response_artifact" in attempt:
                lines += [f"<details><summary>Original raw response</summary>{pre(_checked(attempt['raw_response_artifact']).decode('utf-8',errors='replace'))}</details>"]
            lines += [f"<p class='meta'>Request: {escape(str(attempt.get('request_artifact',{}).get('path','UNKNOWN')))}<br>Ledger: {escape(str((root/model/'attempt_ledger.json').resolve()))}</p></div>"]
        lines += ["</div></div></section>"]
    lines += ["</html>"]
    path = root / "review_bundle.html"
    _new(path, "\n".join(lines).encode("utf-8"))
    return path


def finalize(root: Path) -> dict[str, Any]:
    report = _read(root / "preflight.json")
    checkpoint = _checkpoint(Path(report["prior_checkpoint"]["root"]))
    if checkpoint != report["prior_checkpoint"]:
        raise ValueError("aborted checkpoint changed during SDK benchmark")
    rows = {model: _ledger(root, model) for model in MODELS}
    if any(attempt["status"] == "issued" for attempts in rows.values() for attempt in attempts):
        raise ValueError("unresolved issued attempt prevents final accounting")
    attempts = []
    for model, model_rows in rows.items():
        for attempt in model_rows:
            for key, descriptor in attempt.items():
                if key.endswith("_artifact"):
                    _checked(descriptor, os.environ.get("TOKENMETRO_API_KEY"))
            request = json.loads(_checked(attempt["request_artifact"]))
            if set(request) != {"model", "messages", "max_tokens"} or request["max_tokens"] != MAX_TOKENS:
                raise ValueError("terminal request shape changed")
            if attempt["request_artifact"]["sha256"] != report["requests"][model][str(attempt["ordinal"])]["request_sha256"]:
                raise ValueError("terminal request no longer matches offline preflight")
            attempts.append({"model": model, "ordinal": attempt["ordinal"],
                             "status": attempt["status"], "disposition": attempt["disposition"],
                             "http_status": attempt.get("http_status", "UNKNOWN"),
                             "sdk_status": attempt.get("sdk_status", "UNKNOWN"),
                             "finish_reason": attempt.get("finish_reason", "UNKNOWN"),
                             "latency_ms": attempt.get("latency_ms", "UNKNOWN"),
                             "usage": attempt.get("usage", "UNKNOWN"),
                             "billing": attempt.get("billing", {"reported_credit": "UNKNOWN", "currency": "UNKNOWN", "billable": "UNKNOWN"}),
                             "local_validation": "not_started_http_failure" if attempt.get("http_status") != 200 else attempt["disposition"],
                             "canonical_output": attempt.get("canonical_output_artifact", "UNAVAILABLE"),
                             "raw_response": attempt.get("raw_response_artifact", "UNAVAILABLE"),
                             "provider_requests_issued": attempt["provider_requests_issued"]})
    if sum(a["provider_requests_issued"] for a in attempts) != len(attempts) or len(attempts) > 9:
        raise ValueError("final attempt accounting is incomplete")
    summary = {"benchmark_identity": report["benchmark_identity"], "run_identity": report["run_identity"],
               "status": "stopped_under_per_model_gates", "attempted_requests": len(attempts),
               "maximum_provider_requests": 9, "automatic_retry": False,
               "attempts": attempts, "semantic_quality": "not_assessed", "winning_model": "not_selected",
               "review_bundle": str((root / "review_bundle.html").resolve())}
    _new_json(root / "terminal_summary.json", summary)
    return {key: summary[key] for key in ("status", "attempted_requests", "maximum_provider_requests", "automatic_retry")}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--frozen-root", type=Path, required=True)
    p.add_argument("--aborted-root", type=Path, required=True)
    p.add_argument("--root", type=Path, required=True)
    p = sub.add_parser("run")
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--model", choices=tuple(MODELS), required=True)
    p.add_argument("--ordinal", type=int, choices=ORDINALS, required=True)
    p = sub.add_parser("gate")
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--ordinal", type=int, choices=(16, 20), required=True)
    p = sub.add_parser("review")
    p.add_argument("--root", type=Path, required=True)
    p = sub.add_parser("finalize")
    p.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare(args.frozen_root, args.aborted_root, args.root)
    elif args.action == "run":
        result = run_one(args.root, args.model, args.ordinal)
    elif args.action == "gate":
        result = gate(args.root, args.ordinal)
    elif args.action == "finalize":
        result = finalize(args.root)
    else:
        result = {"review_bundle": str(review(args.root))}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
