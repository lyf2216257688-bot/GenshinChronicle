"""Resumable Phase 05 TokenMetro Chat Completions execution."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Sequence

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from .semantic_compiler_u1 import SEMANTIC_OUTPUT_SCHEMA_IDENTITY, semantic_input_identity
from .semantic_live_runner import (
    SemanticResponseValidationError,
    _redact,
    _validate_raw_response,
    validate_b_v2_navigation_references,
)
from .semantic_openai_chat_adapter import OpenAIChatCompletionsAdapter
from .semantic_tokenmetro_profile import TOKENMETRO_BASE_URL, TOKENMETRO_MODEL_IDS, tokenmetro_model_id


BASE_URL = TOKENMETRO_BASE_URL
TRANSIENT_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES_HARD_CAP = 4
class _Parser:
    extract_content = OpenAIChatCompletionsAdapter.extract_content
    parse_content = OpenAIChatCompletionsAdapter.parse_content


_PARSER = _Parser()


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_once(path: Path, body: bytes) -> dict[str, Any]:
    if path.exists():
        raise ValueError(f"immutable artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, body)
    root = path.parent if path.name == "manifest.json" else path.parents[3]
    return {"path": str(path.relative_to(root)), "sha256": _sha(body), "byte_count": len(body)}


def _json_once(path: Path, value: Any) -> dict[str, Any]:
    return _write_once(path, canonical_json_bytes(value))


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _verify_artifacts(root: Path, value: Any) -> None:
    if isinstance(value, Mapping):
        if set(value) == {"path", "sha256", "byte_count"}:
            path = root / str(value["path"])
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("attempt artifact escapes run root")
            body = path.read_bytes()
            if len(body) != value["byte_count"] or _sha(body) != value["sha256"]:
                raise ValueError("attempt artifact integrity mismatch")
        else:
            for child in value.values():
                _verify_artifacts(root, child)
    elif isinstance(value, list):
        for child in value:
            _verify_artifacts(root, child)


def _retry_after(headers: Mapping[str, str], cap: float) -> float | None:
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return min(cap, max(0.0, seconds))


def _attempts(root: Path, unit_key: str, request_identity: str, run_identity: str, unit_id: str) -> list[dict[str, Any]]:
    directory = root / "units" / unit_key
    if not directory.exists():
        return []
    attempts: list[dict[str, Any]] = []
    for index, path in enumerate(sorted(directory.glob("attempt-*")), 1):
        if path.name != f"attempt-{index:03d}" or not path.is_dir():
            raise ValueError("attempt numbering is ambiguous")
        issued = _read(path / "issued.json")
        if (issued.get("request_identity") != request_identity or issued.get("run_identity") != run_identity
                or issued.get("unit_id") != unit_id or issued.get("attempt_number") != index):
            raise ValueError("attempt request identity changed")
        terminal_path = path / "terminal.json"
        if not terminal_path.exists():
            raise ValueError("unresolved issued attempt; provider accounting is ambiguous")
        terminal = _read(terminal_path)
        if (terminal.get("attempt_number") != index or terminal.get("request_identity") != request_identity
                or terminal.get("run_identity") != run_identity or terminal.get("unit_id") != unit_id):
            raise ValueError("attempt terminal identity changed")
        _verify_artifacts(root, terminal.get("artifacts", {}))
        if terminal.get("disposition") == "accepted_for_local_contract" and (
            terminal.get("http_status") != 200 or terminal.get("finish_reason") != "stop"
            or not {"response", "validation", "canonical_output"}.issubset(terminal.get("artifacts", {}))
        ):
            raise ValueError("accepted attempt evidence is incomplete")
        attempts.append(terminal)
    return attempts


def run_sdk_units(
    root: Path,
    *,
    model: str,
    units: Sequence[Mapping[str, Any]],
    prompt: Mapping[str, Any],
    prompt_identity: str,
    source_identity: str | None = None,
    schema_identity: str = SEMANTIC_OUTPUT_SCHEMA_IDENTITY,
    max_tokens: int = 16384,
    max_retries: int = 2,
    timeout_seconds: float = 300.0,
    backoff_cap_seconds: float = 30.0,
    api_key: str | None = None,
    transport: Any = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Execute frozen units in order; retry budget is per invocation.

    Each unit supplies compilation_unit_id, semantic_input_identity, payload,
    and segment_ids. A terminal transient attempt can be resumed later; an
    unresolved issued attempt always requires manual accounting first.
    """
    import httpx
    import openai

    if (type(max_retries) is not int or not 0 <= max_retries <= MAX_RETRIES_HARD_CAP
            or max_tokens <= 0 or backoff_cap_seconds < 0):
        raise ValueError("invalid SDK operating point")
    if schema_identity != SEMANTIC_OUTPUT_SCHEMA_IDENTITY:
        raise ValueError("authoritative schema identity changed")
    if not api_key:
        api_key = os.environ.get("TOKENMETRO_API_KEY")
    if not api_key:
        raise ValueError("TOKENMETRO_API_KEY is required")
    root = Path(root)
    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    for unit in units:
        unit_id = str(unit["compilation_unit_id"])
        payload = unit["payload"]
        if unit_id in seen or not isinstance(payload, Mapping) or not isinstance(unit.get("segment_ids"), list):
            raise ValueError("invalid or duplicate frozen unit")
        seen.add(unit_id)
        if semantic_input_identity(payload) != unit["semantic_input_identity"]:
            raise ValueError("semantic input identity mismatch")
        body = {"model": model, "messages": [
            {"role": "system", "content": canonical_json_bytes(prompt).decode("utf-8")},
            {"role": "user", "content": canonical_json_bytes(payload).decode("utf-8")},
        ], "max_tokens": max_tokens}
        prepared.append({"unit_id": unit_id, "unit_key": _sha(unit_id.encode("utf-8")),
                         "segment_ids": unit["segment_ids"], "body": body,
                         "request_identity": sha256_json(body),
                         "semantic_input_identity": unit["semantic_input_identity"]})
    contract = {"transport": "official-openai-sdk-chat-completions", "sdk_version": openai.__version__,
                "base_url": BASE_URL, "model": model, "prompt_identity": prompt_identity,
                "source_identity": source_identity,
                "prompt_sha256": sha256_json(prompt), "schema_identity": schema_identity,
                "max_tokens": max_tokens, "timeout_seconds": timeout_seconds,
                "validator": "strict_json_schema_source_binding_v2_local_refs",
                "units": [{key: row[key] for key in ("unit_id", "semantic_input_identity", "request_identity", "segment_ids")}
                          for row in prepared]}
    identity = sha256_json(contract)
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        if _read(manifest_path) != {"identity": identity, "contract": contract}:
            raise ValueError("resume semantic/request/runtime identity mismatch")
    else:
        if root.exists():
            raise ValueError("new SDK run root already exists")
        root.mkdir(parents=True)
        _json_once(manifest_path, {"identity": identity, "contract": contract})

    def checkpoint(status: str, issued_now: int, blocked_unit: str | None = None) -> dict[str, Any]:
        states: dict[str, str] = {}
        cumulative = 0
        for candidate in prepared:
            history = _attempts(root, candidate["unit_key"], candidate["request_identity"], identity, candidate["unit_id"])
            cumulative += len(history)
            states[candidate["unit_id"]] = (
                "pending" if not history else
                "accepted" if history[-1]["disposition"] == "accepted_for_local_contract" else
                "retryable" if history[-1]["retry_classification"] == "transient" else "blocked"
            )
        summary = {"status": status, "run_identity": identity, "logical_units": len(prepared),
                   "accepted_units": sum(value == "accepted" for value in states.values()),
                   "provider_attempts_total": cumulative,
                   "provider_attempts_this_invocation": issued_now, "unit_states": states,
                   "retry_policy_this_invocation": {"max_retries": max_retries,
                                                    "backoff_cap_seconds": backoff_cap_seconds}}
        if blocked_unit is not None:
            summary["blocked_unit"] = blocked_unit
        atomic_write(root / "checkpoint.json", canonical_json_bytes(summary))
        return summary

    issued_now = 0
    for unit in prepared:
        prior = _attempts(root, unit["unit_key"], unit["request_identity"], identity, unit["unit_id"])
        if prior and prior[-1]["disposition"] == "accepted_for_local_contract":
            continue
        if prior and prior[-1]["retry_classification"] != "transient":
            return checkpoint("blocked", issued_now, unit["unit_id"])
        for retry_index in range(max_retries + 1):
            number = len(prior) + 1
            stem = root / "units" / unit["unit_key"] / f"attempt-{number:03d}"
            request_body = canonical_json_bytes(unit["body"])
            if api_key.encode("utf-8") in request_body:
                raise ValueError("request body contains credential")
            request_artifact = _write_once(stem / "request.json", request_body)
            _json_once(stem / "issued.json", {"run_identity": identity, "unit_id": unit["unit_id"],
                                            "request_identity": unit["request_identity"],
                                            "attempt_number": number, "issued_at": _now()})
            artifacts = {"request": request_artifact}
            raw: bytes | None = None
            status: int | None = None
            headers: dict[str, str] = {}
            wire_count = 0

            def on_request(request: httpx.Request) -> None:
                nonlocal wire_count
                wire_count += 1
                if wire_count != 1 or json.loads(request.content) != unit["body"]:
                    raise ValueError("SDK wire request differs from frozen request")
                if api_key.encode("utf-8") in request.content:
                    raise ValueError("SDK wire request contains credential")
                artifacts["wire_request"] = _write_once(stem / "wire_request.bin", request.content)
                artifacts["request_metadata"] = _json_once(stem / "request_metadata.json", {
                    "method": request.method, "url": str(request.url),
                    "headers": {name: request.headers[name] for name in ("accept", "content-type")
                                if name in request.headers},
                })

            def on_response(response: httpx.Response) -> None:
                nonlocal raw, status, headers
                raw = response.read()
                status = response.status_code
                headers = {name: response.headers[name] for name in ("retry-after", "x-request-id", "content-type")
                           if name in response.headers}
                if api_key.encode("utf-8") in raw:
                    raise ValueError("raw response contains credential")
                artifacts["response"] = _write_once(stem / "raw_response.bin", raw)

            started = time.monotonic()
            error_type: str | None = None
            error_message: str | None = None
            try:
                with httpx.Client(transport=transport, event_hooks={"request": [on_request], "response": [on_response]}) as http_client:
                    with openai.OpenAI(base_url=BASE_URL, api_key=api_key, max_retries=0,
                                       timeout=timeout_seconds, http_client=http_client) as client:
                        client.chat.completions.create(**unit["body"])
            except (openai.APITimeoutError, openai.APIConnectionError) as exc:
                error_type = type(exc).__name__
                error_message = _redact(str(exc), (api_key,))
            except openai.APIStatusError as exc:
                error_type = type(exc).__name__
                error_message = _redact(str(exc), (api_key,))
                status = exc.status_code
            except Exception as exc:
                if raw is None or "response" not in artifacts or wire_count != 1:
                    # Instrumentation or persistence errors leave issued unresolved.
                    raise
                error_type = type(exc).__name__
                error_message = _redact(str(exc), (api_key,))
            if wire_count != 1:
                raise ValueError("SDK attempt accounting is ambiguous")
            issued_now += 1
            envelope: dict[str, Any] = {}
            if raw is not None:
                try:
                    value = json.loads(raw)
                    if isinstance(value, dict):
                        envelope = value
                except (UnicodeError, json.JSONDecodeError):
                    pass
            usage = envelope.get("usage", "UNKNOWN")
            choices = envelope.get("choices")
            finish_reason = choices[0].get("finish_reason") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
            disposition = "transport_failure"
            classification = "transient" if status in TRANSIENT_STATUS or error_type in {"APITimeoutError", "APIConnectionError"} else "terminal"
            validation: dict[str, Any] | None = None
            if status == 200 and raw is not None:
                classification = "terminal"
                if finish_reason == "length":
                    disposition = "output_budget_blocked"
                else:
                    try:
                        normalized, _content, _parsed, validation = _validate_raw_response(
                            _PARSER, raw, expected_segment_ids=unit["segment_ids"])
                        validate_b_v2_navigation_references(normalized)
                        if finish_reason != "stop":
                            raise ValueError("finish_reason is not stop")
                        artifacts["canonical_output"] = _json_once(stem / "canonical_output.json", normalized)
                        disposition = "accepted_for_local_contract"
                    except SemanticResponseValidationError as exc:
                        validation = exc.diagnostics
                        disposition = "local_validation_failed"
                    except ValueError as exc:
                        validation = {"local_reference_or_finish_reason": str(exc)}
                        disposition = "local_validation_failed"
            if validation is not None:
                artifacts["validation"] = _json_once(stem / "validation.json", validation)
            if error_type is not None:
                artifacts["sdk_error"] = _json_once(stem / "sdk_error.json", {
                    "type": error_type, "message": error_message, "http_status": status})
            provider_request_id = headers.get("x-request-id") or envelope.get("id")
            if not isinstance(provider_request_id, str) or api_key in provider_request_id:
                provider_request_id = None
            terminal = {"run_identity": identity, "unit_id": unit["unit_id"], "attempt_number": number,
                        "request_identity": unit["request_identity"], "http_status": status,
                        "sdk_error_type": error_type, "finish_reason": finish_reason,
                        "provider_request_id": provider_request_id,
                        "response_headers": headers, "usage": usage, "latency_ms": round((time.monotonic() - started) * 1000, 3),
                        "disposition": disposition, "retry_classification": classification,
                        "retry_policy": {"max_retries": max_retries,
                                         "backoff_cap_seconds": backoff_cap_seconds},
                        "artifacts": artifacts, "terminal_at": _now()}
            if api_key.encode("utf-8") in canonical_json_bytes(terminal):
                raise ValueError("terminal metadata contains credential")
            _json_once(stem / "terminal.json", terminal)
            prior.append(terminal)
            checkpoint("partial", issued_now)
            if disposition == "accepted_for_local_contract":
                break
            if classification != "transient":
                return checkpoint("blocked", issued_now, unit["unit_id"])
            if retry_index == max_retries:
                return checkpoint("paused_transient_outage", issued_now, unit["unit_id"])
            delay = _retry_after(headers, backoff_cap_seconds)
            sleep(delay if delay is not None else min(backoff_cap_seconds, 2 ** retry_index))
    return checkpoint("complete", issued_now)


def frozen_preflight_units(preflight_root: Path, model: str, ordinals: Sequence[int] | None = None) -> list[dict[str, Any]]:
    """Load the accepted U1 sample with its source and payload integrity checks."""
    from .semantic_live_runner import (
        ACCEPTED_PREFLIGHT_IDENTITY, _payload_rows, _read_json,
        _unit_rows, _verify_preflight_artifact,
    )

    if model not in {"gemini", "deepseek", "glm"}:
        raise ValueError("unsupported TokenMetro model")
    preflight_root = Path(preflight_root)
    preflight = _read_json(preflight_root / "preflight.json")
    if preflight.get("preflight_identity") != ACCEPTED_PREFLIGHT_IDENTITY or preflight.get("semantic_output_schema_identity") != SEMANTIC_OUTPUT_SCHEMA_IDENTITY:
        raise ValueError("frozen preflight identity mismatch")
    if sha256_json({key: value for key, value in preflight.items() if key not in {"preflight_identity", "artifacts"}}) != ACCEPTED_PREFLIGHT_IDENTITY:
        raise ValueError("frozen preflight self-check failed")
    # Explicit benchmark ordinals identify the shared 30-unit Gemini source;
    # the complete DeepSeek build uses its independently numbered 18-unit subset.
    channel = "deepseek" if model == "deepseek" and ordinals is None else "gemini_b"
    prefix = "deepseek" if channel == "deepseek" else "gemini"
    _verify_preflight_artifact(preflight_root, preflight, f"{prefix}_units.json")
    _verify_preflight_artifact(preflight_root, preflight, f"{prefix}_payloads.jsonl.gz")
    rows, payloads = _unit_rows(preflight_root, channel), _payload_rows(preflight_root, channel)
    if len(rows) != len(payloads):
        raise ValueError("frozen unit/payload count mismatch")
    ordinal_key = f"{prefix}_request_ordinal"
    selected = set(ordinals) if ordinals is not None else None
    result = []
    for row, payload in zip(rows, payloads):
        ordinal = int(row[ordinal_key])
        if semantic_input_identity(payload) != row["semantic_input_identity"] or _sha(canonical_json_bytes(payload)) != row["payload_sha256"]:
            raise ValueError("frozen semantic input mismatch")
        if selected is None or ordinal in selected:
            result.append({"compilation_unit_id": row["compilation_unit_id"],
                           "semantic_input_identity": row["semantic_input_identity"],
                           "payload": payload, "segment_ids": row["segment_ids"]})
    if selected is not None and len(result) != len(selected):
        raise ValueError("selected ordinal missing from frozen sample")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Resumable Phase 05 TokenMetro SDK runner")
    parser.add_argument("--preflight-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--model", choices=tuple(TOKENMETRO_MODEL_IDS), required=True)
    parser.add_argument("--ordinal", type=int, action="append")
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--max-retries", type=int, default=2)
    args = parser.parse_args(argv)
    from .semantic_live_runner import b_v2_experiment_contract
    experiment = b_v2_experiment_contract()
    units = frozen_preflight_units(args.preflight_root, args.model, args.ordinal)
    result = run_sdk_units(args.run_root, model=tokenmetro_model_id(args.model), units=units,
                           prompt=experiment.prompt_contract, prompt_identity=experiment.prompt_identity,
                           source_identity="8601fb2e665fd5f1bc1b7a9ef35a871269e3ea00f0b9a23654656a0bb3838da0",
                           max_tokens=args.max_tokens, max_retries=args.max_retries)
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
