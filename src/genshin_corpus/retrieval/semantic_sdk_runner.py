"""Resumable Phase 05 semantic route-pair execution."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence

import httpx

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from .semantic_compiler_u1 import (
    SEMANTIC_OUTPUT_SCHEMA_IDENTITY,
    semantic_input_identity,
    semantic_segment_binding_metadata,
)
from .semantic_live_runner import (
    B_V5_EXPERIMENT_REVISION,
    SemanticResponseValidationError,
    STRICT_SOURCE_BINDING_POLICY,
    _redact,
    _validate_raw_response,
    validate_b_v2_navigation_references,
)
from .semantic_openai_chat_adapter import OpenAIChatCompletionsAdapter
from .semantic_tokenmetro_profile import (
    ROUTE_PROFILES,
    TOKENMETRO_BASE_URL,
    TOKENMETRO_MODEL_IDS,
    route_profile,
    tokenmetro_model_id,
)


BASE_URL = TOKENMETRO_BASE_URL
TRANSIENT_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES_HARD_CAP = 4
V5_GATE_MAX_TOKENS = 300000
V5_GATE_TIMEOUT_SECONDS = 900.0
V5_GATE_SCHEMA_VERSION = "phase05-w2-v5-live-gate-manifest-0.1"
V5_GATE_RESPONSES_SCHEMA_VERSION = "phase05-w2-v5-live-gate-manifest-0.2"


def _chunk_mapping(chunk: Any) -> dict[str, Any]:
    """Convert an SDK stream chunk to a secret-free JSON-compatible mapping."""

    if isinstance(chunk, Mapping):
        return dict(chunk)
    dump = getattr(chunk, "model_dump", None)
    if callable(dump):
        value = dump()
        return dict(value) if isinstance(value, Mapping) else {"value": value}
    return {"value": str(chunk)}


def _nested_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _normalize_usage(value: Any) -> dict[str, Any] | str:
    """Expose common provider token fields while retaining UNKNOWN values."""

    usage = _nested_mapping(value)
    if usage is None:
        return "UNKNOWN"
    details_in = _nested_mapping(usage.get("prompt_tokens_details")) or _nested_mapping(usage.get("input_tokens_details")) or {}
    details_out = _nested_mapping(usage.get("completion_tokens_details")) or _nested_mapping(usage.get("output_tokens_details")) or {}
    result: dict[str, Any] = {}
    for name, candidates in {
        "input_tokens": (usage.get("prompt_tokens"), usage.get("input_tokens")),
        "output_tokens": (usage.get("completion_tokens"), usage.get("output_tokens")),
        "reasoning_tokens": (details_out.get("reasoning_tokens"), usage.get("reasoning_tokens")),
        "cached_tokens": (details_in.get("cached_tokens"), usage.get("cached_tokens")),
    }.items():
        for candidate in candidates:
            if candidate is not None:
                result[name] = candidate
                break
    return result or "UNKNOWN"


def _policy_403_pre_generation(raw: bytes | None, status: int | None, *, chunk_count: int,
                               reasoning_chars: int, visible_chars: int, usage: Any,
                               finish_reason: str | None) -> bool:
    """Return true only for the exact, pre-generation policy rejection allowlist."""

    if status != 403 or chunk_count or reasoning_chars or visible_chars or usage not in (None, "UNKNOWN") or finish_reason is not None or raw is None:
        return False
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(envelope, Mapping):
        return False
    if any(field in envelope for field in ("usage", "choices", "output", "finish_reason", "content")):
        return False
    error = _nested_mapping(envelope.get("error"))
    if error is None:
        return False
    # The frozen TokenMetro 403 uses error.type with a null error.code.
    code = error.get("code")
    kind = error.get("type")
    return bool(code == "content_policy_violation" or
                ("code" in error and code is None and kind == "content_policy_violation"))


def _stream_delta(choice: Mapping[str, Any]) -> tuple[str, str]:
    delta = _nested_mapping(choice.get("delta")) or {}
    visible = delta.get("content")
    reasoning = delta.get("reasoning_content", delta.get("reasoning"))
    return (visible if isinstance(visible, str) else "", reasoning if isinstance(reasoning, str) else "")


def _responses_request_body(chat_body: Mapping[str, Any]) -> dict[str, Any]:
    """Translate the frozen logical prompt into the observed Responses shape."""

    messages = chat_body.get("messages")
    if (not isinstance(messages, list) or len(messages) != 2
            or not all(isinstance(message, Mapping) for message in messages)):
        raise ValueError("Responses route requires one system and one user message")
    system, user = messages
    if system.get("role") != "system" or user.get("role") != "user":
        raise ValueError("Responses route message roles are invalid")
    system_content = system.get("content")
    user_content = user.get("content")
    if not isinstance(system_content, str) or not isinstance(user_content, str):
        raise ValueError("Responses route message content must be strings")
    body: dict[str, Any] = {
        "model": chat_body["model"],
        "instructions": system_content,
        "input": user_content,
        "max_output_tokens": chat_body["max_tokens"],
        "stream": True,
    }
    for key, value in chat_body.items():
        if key not in {"model", "messages", "max_tokens", "stream"}:
            body[key] = value
    return body


def _wire_body_for_surface(chat_body: Mapping[str, Any], api_surface: str) -> dict[str, Any]:
    """Build the one wire body named by an explicit route API surface."""

    if api_surface == "responses":
        return _responses_request_body(chat_body)
    if api_surface == "chat_completions":
        return dict(chat_body)
    raise ValueError("unsupported route API surface")


def _responses_event_parts(data: Mapping[str, Any]) -> tuple[str, str, str | None, Any, str | None, bool]:
    """Extract visible/reasoning deltas and terminal state from a Responses event."""

    event_type = data.get("type")
    response = _nested_mapping(data.get("response")) or {}
    identifier = response.get("id") if isinstance(response.get("id"), str) else data.get("id")
    usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else data.get("usage")
    visible = data.get("delta") if event_type == "response.output_text.delta" else ""
    reasoning = data.get("delta") if event_type in {
        "response.reasoning_summary_text.delta", "response.reasoning_text.delta"
    } else ""
    finish_reason: str | None = None
    terminal = False
    if event_type == "response.completed":
        finish_reason = "stop"
        terminal = True
    elif event_type == "response.incomplete":
        details = _nested_mapping(response.get("incomplete_details")) or _nested_mapping(data.get("incomplete_details")) or {}
        finish_reason = str(details.get("reason")) if details.get("reason") else "length"
        terminal = True
    elif event_type == "response.failed":
        finish_reason = "error"
        terminal = True
    return (
        visible if isinstance(visible, str) else "",
        reasoning if isinstance(reasoning, str) else "",
        finish_reason,
        usage,
        identifier if isinstance(identifier, str) else None,
        terminal,
    )


def _stream_envelope(identifier: str | None, visible: str, reasoning: str,
                     finish_reason: str | None, usage: Any) -> bytes:
    """Build the same local Chat Completions envelope used by non-stream validation."""

    choice: dict[str, Any] = {
        "index": 0,
        "finish_reason": finish_reason,
        "message": {"role": "assistant", "content": visible},
    }
    if reasoning:
        choice["message"]["reasoning_content"] = reasoning
    envelope: dict[str, Any] = {"choices": [choice]}
    if identifier is not None:
        envelope["id"] = identifier
    if isinstance(usage, Mapping):
        envelope["usage"] = dict(usage)
    return canonical_json_bytes(envelope)


class _DoneTrackingStream(httpx.SyncByteStream):
    """Pass SSE bytes through unchanged while observing the terminal marker."""

    def __init__(self, source: Any) -> None:
        self.source = source
        self.saw_done = False
        self.tail = b""

    def __iter__(self):
        for chunk in self.source:
            self.observe(chunk)
            yield chunk

    def observe(self, chunk: bytes) -> None:
        combined = self.tail + chunk
        if b"\ndata: [DONE]\n" in b"\n" + combined or b"\ndata: [DONE]\r\n" in b"\n" + combined:
            self.saw_done = True
        self.tail = combined[-32:]

    def close(self) -> None:
        self.source.close()


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
    if path.name == "manifest.json":
        root = path.parent
    elif path.parent.name.startswith("attempt-") and path.parent.parent.name != "units" and path.parent.parent.parent.name != "units":
        root = path.parent.parent
    else:
        root = path.parents[3]
    return {"path": str(path.relative_to(root)), "sha256": _sha(body), "byte_count": len(body)}


class _ArtifactPersistenceError(OSError):
    pass


class _JsonlArtifactWriter:
    """Durably append one canonical JSON record per observed stream chunk."""

    def __init__(self, path: Path, root: Path) -> None:
        self.path = path
        self.root = root
        self._handle: Any = None
        self._digest = hashlib.sha256()
        self.byte_count = 0
        self.chunk_count = 0

    def append(self, value: Mapping[str, Any]) -> None:
        line = canonical_json_bytes(value) + b"\n"
        try:
            if self._handle is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._handle = self.path.open("xb")
            self._handle.write(line)
            self._handle.flush()
            os.fsync(self._handle.fileno())
        except OSError as exc:
            raise _ArtifactPersistenceError("stream evidence could not be persisted") from exc
        self._digest.update(line)
        self.byte_count += len(line)
        self.chunk_count += 1

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def descriptor(self, *, archive: bool = True) -> dict[str, Any] | None:
        self.close()
        if self.chunk_count == 0:
            return None
        if archive:
            archive_path = self.path.with_suffix(self.path.suffix + ".gz")
            fd, temporary = tempfile.mkstemp(prefix=f".{archive_path.name}.", dir=str(archive_path.parent))
            compressed_digest = hashlib.sha256()
            compressed_bytes = 0
            try:
                with os.fdopen(fd, "wb") as handle:
                    digesting = _DigestingWriter(handle, compressed_digest)
                    with gzip.GzipFile(fileobj=digesting, mode="wb", mtime=0) as compressed:
                        with self.path.open("rb") as source:
                            shutil.copyfileobj(source, compressed, length=1024 * 1024)
                    handle.flush()
                    os.fsync(handle.fileno())
                    compressed_bytes = handle.tell()
                if archive_path.exists():
                    raise _ArtifactPersistenceError("stream archive already exists")
                os.replace(temporary, archive_path)
                self.path.unlink()
            except OSError as exc:
                if os.path.exists(temporary):
                    os.unlink(temporary)
                raise _ArtifactPersistenceError("stream evidence archive could not be persisted") from exc
            return {
                "path": str(archive_path.relative_to(self.root)),
                "sha256": compressed_digest.hexdigest(),
                "byte_count": compressed_bytes,
                "uncompressed_sha256": self._digest.hexdigest(),
                "uncompressed_byte_count": self.byte_count,
                "chunk_count": self.chunk_count,
                "format": "jsonl-gzip-1",
            }
        return {
            "path": str(self.path.relative_to(self.root)),
            "sha256": self._digest.hexdigest(),
            "byte_count": self.byte_count,
            "chunk_count": self.chunk_count,
            "format": "jsonl-1",
        }


class _DigestingWriter:
    """Update a digest while gzip writes compressed bytes to a file."""

    def __init__(self, handle: Any, digest: Any) -> None:
        self.handle = handle
        self.digest = digest

    def write(self, data: bytes) -> int:
        self.digest.update(data)
        return self.handle.write(data)

    def flush(self) -> None:
        self.handle.flush()


def _json_once(path: Path, value: Any) -> dict[str, Any]:
    return _write_once(path, canonical_json_bytes(value))


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _verify_artifacts(root: Path, value: Any) -> None:
    if isinstance(value, Mapping):
        descriptor_keys = set(value)
        if descriptor_keys in ({"path", "sha256", "byte_count"},
                               {"path", "sha256", "byte_count", "chunk_count", "format"},
                               {"path", "sha256", "byte_count", "uncompressed_sha256", "uncompressed_byte_count", "chunk_count", "format"}):
            path = root / str(value["path"])
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("attempt artifact escapes run root")
            body = path.read_bytes()
            if len(body) != value["byte_count"] or _sha(body) != value["sha256"]:
                raise ValueError("attempt artifact integrity mismatch")
            if "chunk_count" in value:
                if value["format"] == "jsonl-gzip-1":
                    try:
                        uncompressed = gzip.decompress(body)
                    except (OSError, EOFError) as exc:
                        raise ValueError("stream artifact compression mismatch") from exc
                    if (len(uncompressed) != value.get("uncompressed_byte_count")
                            or _sha(uncompressed) != value.get("uncompressed_sha256")):
                        raise ValueError("stream artifact content hash mismatch")
                elif value["format"] == "jsonl-1":
                    uncompressed = body
                else:
                    raise ValueError("stream artifact framing mismatch")
                if (type(value["chunk_count"]) is not int or value["chunk_count"] < 0
                        or uncompressed.count(b"\n") != value["chunk_count"]
                        or (uncompressed and not uncompressed.endswith(b"\n"))):
                    raise ValueError("stream artifact framing mismatch")
            else:
                uncompressed = body
            if "chunk_count" in value:
                try:
                    rows = [json.loads(line) for line in uncompressed.splitlines()]
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("stream artifact framing mismatch") from exc
                if len(rows) != value["chunk_count"] or any(not isinstance(row, Mapping) for row in rows):
                    raise ValueError("stream artifact framing mismatch")
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
        attempts.append(_verified_attempt(root, path, index, request_identity, run_identity, unit_id))
    if attempts:
        if attempts[0].get("route") is not None:
            _validate_route_pair_history_shape(root, attempts)
        else:
            _validate_single_history(attempts)
    return attempts


def _verified_attempt(root: Path, path: Path, index: int, request_identity: str,
                      run_identity: str, unit_id: str) -> dict[str, Any]:
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
    if issued.get("route") is not None:
        if (issued.get("route") != terminal.get("route")
                or issued.get("route_role") != terminal.get("route_role")
                or issued.get("route_config_identity") != terminal.get("route_config_identity")
                or issued.get("wire_request_identity") != terminal.get("wire_request_identity")):
            raise ValueError("attempt route identity changed")
        expected = ("tokenmetro", "primary") if index == 1 else ("jizhi", "fallback")
        if (terminal.get("route"), terminal.get("route_role")) != expected:
            raise ValueError("route attempt order is ambiguous")
    artifacts = terminal.get("artifacts", {})
    _verify_artifacts(root, artifacts)
    stream = artifacts.get("stream") if isinstance(artifacts, Mapping) else None
    chunks = artifacts.get("stream_chunks") if isinstance(artifacts, Mapping) else None
    if stream is not None and chunks is not None:
        raise ValueError("ambiguous stream artifact format")
    if stream is not None and (not isinstance(stream, Mapping)
                               or stream.get("chunk_count") != terminal.get("stream_chunk_count")):
        raise ValueError("stream chunk count mismatch")
    if chunks is not None and (not isinstance(chunks, list)
                               or len(chunks) != terminal.get("stream_chunk_count")):
        raise ValueError("stream chunk count mismatch")
    if terminal.get("disposition") == "accepted_for_local_contract" and (
        terminal.get("http_status") != 200 or terminal.get("finish_reason") != "stop"
        or not {"response", "validation", "canonical_output"}.issubset(artifacts)
    ):
        raise ValueError("accepted attempt evidence is incomplete")
    return terminal


def _single_history_state(history: Sequence[Mapping[str, Any]]) -> str:
    if not history:
        return "pending"
    last = history[-1]
    if last.get("disposition") == "accepted_for_local_contract":
        return "accepted"
    if last.get("retry_classification") == "transient":
        return "retryable"
    return "blocked"


def _route_history_state(history: Sequence[Mapping[str, Any]]) -> str:
    if not history:
        return "pending"
    if history[-1].get("disposition") == "accepted_for_local_contract":
        return "accepted"
    if (len(history) == 1
            and history[-1].get("disposition") == "primary_policy_403_pre_generation"):
        return "fallback_pending"
    return "blocked"


def _validate_single_history(history: Sequence[Mapping[str, Any]]) -> None:
    for index, row in enumerate(history):
        if index < len(history) - 1 and row.get("retry_classification") != "transient":
            raise ValueError("single-route attempt history is not resumable")
        if row.get("disposition") == "accepted_for_local_contract" and index < len(history) - 1:
            raise ValueError("single-route attempt follows an accepted attempt")


def _qualifying_policy_attempt(root: Path, row: Mapping[str, Any]) -> bool:
    artifacts = row.get("artifacts")
    raw_descriptor = artifacts.get("response") if isinstance(artifacts, Mapping) else None
    if not isinstance(raw_descriptor, Mapping):
        return False
    raw = (root / str(raw_descriptor["path"])).read_bytes()
    return (
        row.get("route") == "tokenmetro"
        and row.get("route_role") == "primary"
        and row.get("disposition") == "primary_policy_403_pre_generation"
        and row.get("sdk_error_type") == "PermissionDeniedError"
        and row.get("fallback_eligible") is True
        and row.get("fallback_decision") == "eligible"
        and row.get("stream_complete") is False
        and row.get("execution_state") == "not_started"
        and _policy_403_pre_generation(
            raw, row.get("http_status"), chunk_count=row.get("stream_chunk_count", -1),
            reasoning_chars=row.get("reasoning_chars", -1), visible_chars=row.get("visible_chars", -1),
            usage=row.get("usage"), finish_reason=row.get("finish_reason"),
        )
    )


def _validate_route_pair_history_shape(root: Path, history: Sequence[Mapping[str, Any]]) -> None:
    if not history:
        return
    if any(row.get("retry_classification") != "terminal" or row.get("automatic_retry") is not False
           for row in history):
        raise ValueError("route attempt history contains an invalid retry state")
    if len(history) > 2:
        raise ValueError("route attempt history has too many attempts")
    first = history[0]
    if (first.get("attempt_number") != 1
            or first.get("route") != "tokenmetro"
            or first.get("route_role") != "primary"):
        raise ValueError("route attempt order is ambiguous")
    qualifies = _qualifying_policy_attempt(root, first)
    if len(history) == 1:
        if first.get("disposition") == "primary_policy_403_pre_generation" and not qualifies:
            raise ValueError("primary fallback predicate is invalid")
        if first.get("disposition") != "primary_policy_403_pre_generation" and first.get("fallback_eligible") is True:
            raise ValueError("non-policy primary cannot authorize fallback")
        return
    if not qualifies:
        raise ValueError("fallback lacks a qualifying primary policy failure")
    fallback = history[1]
    if (fallback.get("attempt_number") != 2
            or fallback.get("route") != "jizhi"
            or fallback.get("route_role") != "fallback"
            or fallback.get("fallback_eligible") is not False
            or fallback.get("fallback_decision") != "not_eligible"
            or fallback.get("disposition") == "primary_policy_403_pre_generation"):
        raise ValueError("fallback history is not causally valid")


def _verify_manifest_request_binding(root: Path, unit: Mapping[str, Any], artifacts: Any,
                                     *, expected_body: Mapping[str, Any] | None = None,
                                     expected_wire_identity: str | None = None) -> None:
    if not isinstance(artifacts, Mapping):
        raise ValueError("route attempt artifacts are invalid")
    request_descriptor = artifacts.get("request")
    wire_descriptor = artifacts.get("wire_request")
    if not isinstance(request_descriptor, Mapping) or not isinstance(wire_descriptor, Mapping):
        raise ValueError("route attempt lacks immutable request evidence")
    request_body = (root / str(request_descriptor["path"])).read_bytes()
    wire_body = (root / str(wire_descriptor["path"])).read_bytes()
    try:
        request_value = json.loads(request_body)
        wire_value = json.loads(wire_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("route attempt request evidence is not valid JSON") from exc
    if request_value != wire_value:
        raise ValueError("route attempt request differs from wire evidence")
    if expected_body is not None and request_value != expected_body:
        raise ValueError("route attempt request differs from frozen logical request")
    expected_wire_identity = expected_wire_identity or unit.get("wire_request_identity")
    if not isinstance(expected_wire_identity, str) or sha256_json(wire_value) != expected_wire_identity:
        raise ValueError("route attempt wire request identity changed")


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
    primary_route: str | None = None,
    fallback_route: str | None = None,
    stream: bool = False,
    generation_parameters: Mapping[str, Any] | None = None,
    environment: Mapping[str, str] | None = None,
    route_api_keys: Mapping[str, str] | None = None,
    route_transports: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute frozen units in order; retry budget is per invocation.

    Each unit supplies compilation_unit_id, semantic_input_identity, payload,
    and segment_ids. A terminal transient attempt can be resumed later; an
    unresolved issued attempt always requires manual accounting first.
    """
    if primary_route is not None or fallback_route is not None:
        if not stream or max_retries != 0:
            raise ValueError("route-pair execution requires stream=True and max_retries=0")
        keys = dict(route_api_keys or {})
        if api_key is not None:
            keys.setdefault("tokenmetro", api_key)
        return run_sdk_route_pair(
            root, units=units, prompt=prompt, prompt_identity=prompt_identity,
            source_identity=source_identity, schema_identity=schema_identity,
            model=model, primary_route=primary_route or "tokenmetro",
            fallback_route=fallback_route or "jizhi", max_tokens=max_tokens,
            timeout_seconds=timeout_seconds, generation_parameters=generation_parameters,
            environment=environment, api_keys=keys, transports=route_transports or ({"tokenmetro": transport} if transport is not None else None),
        )

    import httpx
    import openai

    if (type(max_retries) is not int or not 0 <= max_retries <= MAX_RETRIES_HARD_CAP
            or max_tokens <= 0 or backoff_cap_seconds < 0):
        raise ValueError("invalid SDK operating point")
    if stream or generation_parameters or environment is not None or route_api_keys or route_transports:
        raise ValueError("stream and route configuration require explicit route-pair mode")
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

    history_cache: dict[str, list[dict[str, Any]]] = {
        candidate["unit_id"]: _attempts(root, candidate["unit_key"], candidate["request_identity"],
                                         identity, candidate["unit_id"])
        for candidate in prepared
    }

    def checkpoint(status: str, issued_now: int, blocked_unit: str | None = None) -> dict[str, Any]:
        states: dict[str, str] = {}
        cumulative = 0
        for candidate in prepared:
            history = history_cache[candidate["unit_id"]]
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
                   "attempt_counts": {candidate["unit_id"]: len(history_cache[candidate["unit_id"]])
                                      for candidate in prepared},
                   "retry_policy_this_invocation": {"max_retries": max_retries,
                                                    "backoff_cap_seconds": backoff_cap_seconds}}
        if blocked_unit is not None:
            summary["blocked_unit"] = blocked_unit
        summary["identity"] = sha256_json(summary)
        atomic_write(root / "checkpoint.json", canonical_json_bytes(summary))
        return summary

    def verify_checkpoint() -> bool:
        checkpoint_path = root / "checkpoint.json"
        if not checkpoint_path.exists():
            return False
        saved = _read(checkpoint_path)
        unsigned = {key: value for key, value in saved.items() if key != "identity"}
        identity_valid = (saved.get("identity") == sha256_json(unsigned)
                          if "identity" in saved else True)
        if not identity_valid or saved.get("run_identity") != identity:
            raise ValueError("checkpoint identity or history mismatch")
        current_counts = {candidate["unit_id"]: len(history_cache[candidate["unit_id"]]) for candidate in prepared}
        saved_counts = saved.get("attempt_counts")
        if saved_counts is None:
            saved_states = saved.get("unit_states")
            if not isinstance(saved_states, Mapping) or set(saved_states) != set(current_counts):
                raise ValueError("checkpoint history shape is invalid")
            reachable_totals = {0}
            for candidate in prepared:
                unit_id = candidate["unit_id"]
                state = saved_states[unit_id]
                options = [index for index in range(current_counts[unit_id] + 1)
                           if _single_history_state(history_cache[unit_id][:index]) == state]
                if not options:
                    raise ValueError("checkpoint history shape is invalid")
                reachable_totals = {total + option for total in reachable_totals for option in options}
            saved_total = saved.get("provider_attempts_total")
            if (type(saved_total) is not int or saved_total not in reachable_totals
                    or saved.get("logical_units") != len(prepared)
                    or saved.get("accepted_units") != sum(value == "accepted" for value in saved_states.values())):
                raise ValueError("checkpoint identity or history mismatch")
            if saved.get("status") == "complete" and (
                    saved_total != sum(current_counts.values())
                    or any(saved_states[candidate["unit_id"]] != _single_history_state(history_cache[candidate["unit_id"]])
                           for candidate in prepared)
                    or any(value != "accepted" for value in saved_states.values())):
                raise ValueError("checkpoint is ahead of verified history")
            return saved_total < sum(current_counts.values())
        if (not isinstance(saved_counts, Mapping)
                or set(saved_counts) != set(current_counts)
                or any(type(value) is not int or value < 0 or value > current_counts[key]
                       for key, value in saved_counts.items())):
            raise ValueError("checkpoint identity or history mismatch")
        expected_states = {
            candidate["unit_id"]: _single_history_state(
                history_cache[candidate["unit_id"]][:saved_counts[candidate["unit_id"]]])
            for candidate in prepared
        }
        expected_total = sum(saved_counts.values())
        if (saved.get("logical_units") != len(prepared)
                or saved.get("unit_states") != expected_states
                or saved.get("accepted_units") != sum(value == "accepted" for value in expected_states.values())
                or saved.get("provider_attempts_total") != expected_total):
            raise ValueError("checkpoint identity or history mismatch")
        if saved.get("status") == "complete" and saved_counts != current_counts:
            raise ValueError("checkpoint is ahead of verified history")
        if saved.get("status") == "complete" and any(value != "accepted" for value in expected_states.values()):
            raise ValueError("complete checkpoint has non-accepted unit history")
        return any(saved_counts[key] < current_counts[key] for key in current_counts)

    checkpoint_behind = verify_checkpoint()
    if checkpoint_behind:
        checkpoint("partial", 0)

    issued_now = 0
    for unit in prepared:
        prior = history_cache[unit["unit_id"]]
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
            verified = _verified_attempt(root, stem, number, unit["request_identity"], identity, unit["unit_id"])
            if verified != terminal:
                raise ValueError("new attempt terminal changed before checkpoint")
            prior.append(verified)
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


def run_sdk_route_pair(
    root: Path,
    *,
    units: Sequence[Mapping[str, Any]],
    prompt: Mapping[str, Any],
    prompt_identity: str,
    source_identity: str | None = None,
    schema_identity: str = SEMANTIC_OUTPUT_SCHEMA_IDENTITY,
    model: str = "deepseek-v4.1-flash",
    primary_route: str = "tokenmetro",
    fallback_route: str = "jizhi",
    max_tokens: int = 500000,
    timeout_seconds: float = 300.0,
    generation_parameters: Mapping[str, Any] | None = None,
    environment: Mapping[str, str] | None = None,
    api_keys: Mapping[str, str] | None = None,
    transports: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run DeepSeek with one conservative TokenMetro -> Jizhi route pair.

    This function intentionally has no retry loop.  A second provider attempt
    is created only for the exact pre-generation TokenMetro policy response.
    """

    import httpx
    import openai

    if primary_route != "tokenmetro" or fallback_route != "jizhi":
        raise ValueError("the production DeepSeek route pair is TokenMetro -> Jizhi")
    if model != ROUTE_PROFILES[primary_route].model_id or model != ROUTE_PROFILES[fallback_route].model_id:
        raise ValueError("route pair requires deepseek-v4.1-flash")
    if (max_tokens <= 0 or timeout_seconds <= 0 or schema_identity != SEMANTIC_OUTPUT_SCHEMA_IDENTITY
            or not isinstance(source_identity, str) or not source_identity or not prompt_identity):
        raise ValueError("invalid route-pair operating point")
    generation = dict(generation_parameters or {})
    if set(generation) & {"model", "messages", "max_tokens", "stream"}:
        raise ValueError("generation parameters cannot override the shared semantic request")
    values = os.environ if environment is None else environment
    profiles = {
        route: route_profile(route, values, require_environment=True)
        for route in (primary_route, fallback_route)
    }
    keys = dict(api_keys or {})
    primary_key = keys.get(primary_route) or values.get(profiles[primary_route].api_key_env)
    if not isinstance(primary_key, str) or not primary_key:
        raise ValueError(f"{profiles[primary_route].api_key_env} is required")
    secret_values = tuple(value for value in (primary_key, keys.get(fallback_route) or values.get(profiles[fallback_route].api_key_env))
                          if isinstance(value, str) and value)
    if any(secret in profile.base_url for secret in secret_values for profile in profiles.values()):
        raise ValueError("route base URL contains a configured credential")

    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    for unit in units:
        unit_id = str(unit["compilation_unit_id"])
        payload = unit["payload"]
        if unit_id in seen or not isinstance(payload, Mapping) or not isinstance(unit.get("segment_ids"), list):
            raise ValueError("invalid or duplicate frozen unit")
        seen.add(unit_id)
        semantic_id = semantic_input_identity(payload)
        if semantic_id != unit["semantic_input_identity"]:
            raise ValueError("semantic input identity mismatch")
        messages = [
            {"role": "system", "content": canonical_json_bytes(prompt).decode("utf-8")},
            {"role": "user", "content": canonical_json_bytes(payload).decode("utf-8")},
        ]
        chat_body = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": True, **generation}
        route_bodies = {
            route: _wire_body_for_surface(chat_body, profiles[route].api_surface)
            for route in profiles
        }
        provider_omitted = bool(unit.get("provider_omitted"))
        if provider_omitted and unit.get("segment_ids"):
            raise ValueError("provider-omitted unit still has provider-visible segments")
        body = route_bodies[primary_route]
        request_identity = sha256_json({
            "source_identity": source_identity,
            "prompt_identity": prompt_identity,
            "schema_identity": schema_identity,
            "compilation_unit_id": unit_id,
            "semantic_input_identity": semantic_id,
            "model": model,
            "messages": messages,
            "payload": payload,
            "stream": True,
            "max_tokens": max_tokens,
            "generation_parameters": generation,
        })
        prepared.append({"unit_id": unit_id, "unit_key": _sha(unit_id.encode("utf-8")),
                         "segment_ids": unit["segment_ids"],
                         "segment_metadata": semantic_segment_binding_metadata(payload), "body": body,
                         "wire_request_identity": sha256_json(body),
                         "route_bodies": route_bodies,
                         "route_wire_request_identities": {route: sha256_json(route_body)
                                                            for route, route_body in route_bodies.items()},
                         "request_identity": request_identity,
                         "semantic_input_identity": semantic_id,
                         "provider_omitted": provider_omitted,
                         "omission_reason": unit.get("omission_reason", "pure_media_map_desc") if provider_omitted else None})

        declared_route_identities = unit.get("route_wire_request_identities")
        if declared_route_identities is not None:
            if not isinstance(declared_route_identities, Mapping):
                raise ValueError("unit route wire identities are invalid")
            for route, route_body in route_bodies.items():
                declared = declared_route_identities.get(route)
                if declared != sha256_json(route_body):
                    raise ValueError("unit route wire identity differs from explicit route API surface")
        elif (unit.get("wire_request_identity") is not None
              and unit.get("wire_request_identity") != sha256_json(route_bodies[primary_route])):
            raise ValueError("unit wire identity differs from primary route API surface")

    contract = {
        "runner": "phase05-semantic-sdk-route-pair-0.1",
        "transport": "official-openai-sdk-route-adapter",
        "sdk_version": openai.__version__,
        "primary_route": profiles[primary_route].safe_dict(),
        "fallback_route": profiles[fallback_route].safe_dict(),
        "primary_route_config_identity": profiles[primary_route].config_identity,
        "fallback_route_config_identity": profiles[fallback_route].config_identity,
        "model": model, "prompt_identity": prompt_identity,
        "source_identity": source_identity, "prompt_sha256": sha256_json(prompt),
        "schema_identity": schema_identity, "stream": True,
        "max_tokens": max_tokens, "generation_parameters": generation,
        "timeout_seconds": timeout_seconds, "automatic_retry": False,
        "validator": "strict_json_schema_source_binding_v2_local_refs_minimality",
        "source_binding_policy": STRICT_SOURCE_BINDING_POLICY,
        "units": [{key: row[key] for key in ("unit_id", "semantic_input_identity", "request_identity", "wire_request_identity", "route_wire_request_identities", "segment_ids", "segment_metadata", "provider_omitted", "omission_reason")}
                  for row in prepared],
    }
    run_identity = sha256_json(contract)
    root = Path(root)
    manifest_path = root / "manifest.json"
    manifest = {"identity": run_identity, "contract": contract}
    if manifest_path.exists():
        if _read(manifest_path) != manifest:
            raise ValueError("resume semantic/request/runtime identity mismatch")
    else:
        if root.exists():
            raise ValueError("new route-pair run root already exists")
        root.mkdir(parents=True)
        _json_once(manifest_path, manifest)

    def validate_route_history(unit: Mapping[str, Any], history: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if unit.get("provider_omitted"):
            if history:
                raise ValueError("provider-omitted unit has provider attempt history")
            return []
        _validate_route_pair_history_shape(root, history)
        for row in history:
            route = row.get("route")
            profile = profiles.get(route)
            if profile is None or row.get("route_config_identity") != profile.config_identity:
                raise ValueError("attempt route configuration identity changed")
            expected_wire_identity = unit.get("route_wire_request_identities", {}).get(route,
                                                                                         unit["wire_request_identity"])
            if row.get("wire_request_identity") != expected_wire_identity:
                raise ValueError("attempt wire request identity changed")
            artifacts = row.get("artifacts", {})
            expected_body = unit.get("route_bodies", {}).get(route, unit["body"])
            _verify_manifest_request_binding(root, unit, artifacts,
                                             expected_body=expected_body,
                                             expected_wire_identity=expected_wire_identity)
            request_descriptor = artifacts["request"]
            wire_descriptor = artifacts["wire_request"]
            request_bytes = (root / str(request_descriptor["path"])).read_bytes()
            wire_bytes = (root / str(wire_descriptor["path"])).read_bytes()
            if (json.loads(request_bytes) != expected_body or json.loads(wire_bytes) != expected_body):
                raise ValueError("route attempt request differs from frozen logical request")
            if row.get("disposition") == "primary_policy_403_pre_generation":
                raw_descriptor = artifacts.get("response")
                if not isinstance(raw_descriptor, Mapping):
                    raise ValueError("policy fallback lacks a persisted primary response")
                raw = (root / str(raw_descriptor["path"])).read_bytes()
                if not _policy_403_pre_generation(
                    raw, row.get("http_status"), chunk_count=row.get("stream_chunk_count", -1),
                    reasoning_chars=row.get("reasoning_chars", -1), visible_chars=row.get("visible_chars", -1),
                    usage=row.get("usage"), finish_reason=row.get("finish_reason"),
                ) or row.get("fallback_eligible") is not True or row.get("stream_complete") is not False:
                    raise ValueError("primary policy fallback evidence is invalid")
        return list(history)

    def history_for(unit: Mapping[str, Any]) -> list[dict[str, Any]]:
        history = _attempts(root, unit["unit_key"], unit["request_identity"], run_identity, unit["unit_id"])
        return validate_route_history(unit, history)

    # Existing attempt trees are verified exactly once at invocation start. All
    # later checkpoint updates use this in-memory ledger and verify only the
    # newly completed attempt.
    history_cache: dict[str, list[dict[str, Any]]] = {
        candidate["unit_id"]: history_for(candidate) for candidate in prepared
    }

    checkpoint_states: dict[str, str] = {
        candidate["unit_id"]: ("omitted" if candidate.get("provider_omitted") else "pending")
        for candidate in prepared
    }
    checkpoint_routes: dict[str, list[str]] = {candidate["unit_id"]: [] for candidate in prepared}
    checkpoint_counters = {
        "tokenmetro_attempts": 0, "tokenmetro_successes": 0,
        "tokenmetro_policy_403": 0, "jizhi_fallback_issued": 0,
        "jizhi_fallback_successes": 0, "jizhi_fallback_failures": 0,
        "stopped_indeterminate": 0, "accepted_primary": 0, "accepted_fallback": 0,
    }
    checkpoint_usage: dict[str, dict[str, Any]] = {
        "tokenmetro": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
                       "unknown_usage_attempts": 0, "reported_credit": 0, "unknown_credit_attempts": 0},
        "jizhi": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
                  "unknown_usage_attempts": 0, "reported_credit": 0, "unknown_credit_attempts": 0},
    }
    checkpoint_total = 0
    checkpoint_accepted = 0

    def account_attempt(unit_id: str, row: Mapping[str, Any]) -> None:
        nonlocal checkpoint_total, checkpoint_accepted
        previous = checkpoint_states[unit_id]
        route = row.get("route")
        checkpoint_total += 1
        checkpoint_routes[unit_id].append(str(route))
        if route in checkpoint_usage:
            normalized = row.get("usage_normalized")
            if isinstance(normalized, Mapping):
                for field in ("input_tokens", "output_tokens", "reasoning_tokens"):
                    value = normalized.get(field)
                    if isinstance(value, int):
                        checkpoint_usage[route][field] += value
            else:
                checkpoint_usage[route]["unknown_usage_attempts"] += 1
            billing = row.get("billing")
            credit = billing.get("reported_credit") if isinstance(billing, Mapping) else None
            if isinstance(credit, (int, float)):
                checkpoint_usage[route]["reported_credit"] += credit
            else:
                checkpoint_usage[route]["unknown_credit_attempts"] += 1
        if route == "tokenmetro":
            checkpoint_counters["tokenmetro_attempts"] += 1
            if row.get("disposition") == "accepted_for_local_contract":
                checkpoint_counters["tokenmetro_successes"] += 1
                checkpoint_counters["accepted_primary"] += 1
            if row.get("disposition") == "primary_policy_403_pre_generation":
                checkpoint_counters["tokenmetro_policy_403"] += 1
        elif route == "jizhi":
            checkpoint_counters["jizhi_fallback_issued"] += 1
            if row.get("disposition") == "accepted_for_local_contract":
                checkpoint_counters["jizhi_fallback_successes"] += 1
                checkpoint_counters["accepted_fallback"] += 1
            else:
                checkpoint_counters["jizhi_fallback_failures"] += 1
        if row.get("execution_state") == "unknown":
            checkpoint_counters["stopped_indeterminate"] += 1
        if row.get("disposition") == "accepted_for_local_contract":
            new_state = "accepted"
        elif row.get("disposition") == "primary_policy_403_pre_generation" and len(checkpoint_routes[unit_id]) == 1:
            new_state = "fallback_pending"
        else:
            new_state = "blocked"
        if previous == "accepted":
            checkpoint_accepted -= 1
        if new_state == "accepted":
            checkpoint_accepted += 1
        checkpoint_states[unit_id] = new_state

    def project_checkpoint(prefix_lengths: Mapping[str, int]) -> tuple[
        dict[str, str], int, int, dict[str, int], dict[str, dict[str, Any]]
    ]:
        """Recompute a saved prefix summary without changing the live ledger."""

        states = {
            candidate["unit_id"]: ("omitted" if candidate.get("provider_omitted") else "pending")
            for candidate in prepared
        }
        routes = {candidate["unit_id"]: [] for candidate in prepared}
        counters = {
            "tokenmetro_attempts": 0, "tokenmetro_successes": 0,
            "tokenmetro_policy_403": 0, "jizhi_fallback_issued": 0,
            "jizhi_fallback_successes": 0, "jizhi_fallback_failures": 0,
            "stopped_indeterminate": 0, "accepted_primary": 0, "accepted_fallback": 0,
        }
        usage: dict[str, dict[str, Any]] = {
            "tokenmetro": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
                           "unknown_usage_attempts": 0, "reported_credit": 0, "unknown_credit_attempts": 0},
            "jizhi": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
                      "unknown_usage_attempts": 0, "reported_credit": 0, "unknown_credit_attempts": 0},
        }
        total = 0
        accepted = 0
        for candidate in prepared:
            unit_id = candidate["unit_id"]
            for row in history_cache[unit_id][:prefix_lengths[unit_id]]:
                total += 1
                route = row.get("route")
                routes[unit_id].append(str(route))
                if route in usage:
                    normalized = row.get("usage_normalized")
                    if isinstance(normalized, Mapping):
                        for field in ("input_tokens", "output_tokens", "reasoning_tokens"):
                            value = normalized.get(field)
                            if isinstance(value, int):
                                usage[route][field] += value
                    else:
                        usage[route]["unknown_usage_attempts"] += 1
                    billing = row.get("billing")
                    credit = billing.get("reported_credit") if isinstance(billing, Mapping) else None
                    if isinstance(credit, (int, float)):
                        usage[route]["reported_credit"] += credit
                    else:
                        usage[route]["unknown_credit_attempts"] += 1
                if route == "tokenmetro":
                    counters["tokenmetro_attempts"] += 1
                    if row.get("disposition") == "accepted_for_local_contract":
                        counters["tokenmetro_successes"] += 1
                        counters["accepted_primary"] += 1
                    if row.get("disposition") == "primary_policy_403_pre_generation":
                        counters["tokenmetro_policy_403"] += 1
                elif route == "jizhi":
                    counters["jizhi_fallback_issued"] += 1
                    if row.get("disposition") == "accepted_for_local_contract":
                        counters["jizhi_fallback_successes"] += 1
                        counters["accepted_fallback"] += 1
                    else:
                        counters["jizhi_fallback_failures"] += 1
                if row.get("execution_state") == "unknown":
                    counters["stopped_indeterminate"] += 1
                previous = states[unit_id]
                if row.get("disposition") == "accepted_for_local_contract":
                    states[unit_id] = "accepted"
                elif row.get("disposition") == "primary_policy_403_pre_generation" and len(routes[unit_id]) == 1:
                    states[unit_id] = "fallback_pending"
                else:
                    states[unit_id] = "blocked"
                accepted += int(previous != "accepted" and states[unit_id] == "accepted")
                accepted -= int(previous == "accepted" and states[unit_id] != "accepted")
        return states, accepted, total, counters, usage

    for candidate in prepared:
        for existing in history_cache[candidate["unit_id"]]:
            account_attempt(candidate["unit_id"], existing)

    def checkpoint(status: str, issued_now: int, blocked_unit: str | None = None) -> dict[str, Any]:
        summary = {
            "status": status, "run_identity": run_identity,
            "logical_units": len(prepared), "accepted_units": checkpoint_accepted,
            "provider_omitted_units": sum(1 for candidate in prepared if candidate.get("provider_omitted")),
            "provider_attempts_total": checkpoint_total, "provider_attempts_this_invocation": issued_now,
            "unit_states": dict(checkpoint_states), "route_history": {key: list(value) for key, value in checkpoint_routes.items()},
            "counters": {key: value for key, value in checkpoint_counters.items()},
            "usage_by_route": {key: dict(value) for key, value in checkpoint_usage.items()},
            "retry_policy": {"automatic_retry": False},
        }
        if blocked_unit is not None:
            summary["blocked_unit"] = blocked_unit
        summary["identity"] = sha256_json(summary)
        atomic_write(root / "checkpoint.json", canonical_json_bytes(summary))
        if status != "partial":
            atomic_write(root / "terminal_summary.json", canonical_json_bytes(summary))
        if _read(root / "checkpoint.json") != summary:
            raise ValueError("route checkpoint persistence failed")
        return summary

    def verify_checkpoint() -> bool:
        checkpoint_path = root / "checkpoint.json"
        if not checkpoint_path.exists():
            return False
        saved = _read(checkpoint_path)
        identity = saved.get("identity")
        unsigned = {key: value for key, value in saved.items() if key != "identity"}
        if identity != sha256_json(unsigned) or saved.get("run_identity") != run_identity:
            raise ValueError("route checkpoint identity or history mismatch")
        saved_routes = saved.get("route_history")
        if (not isinstance(saved_routes, Mapping)
                or set(saved_routes) != set(checkpoint_routes)):
            raise ValueError("route checkpoint history shape is invalid")
        prefix_lengths: dict[str, int] = {}
        for candidate in prepared:
            unit_id = candidate["unit_id"]
            prefix = saved_routes[unit_id]
            full = checkpoint_routes[unit_id]
            if (not isinstance(prefix, list) or len(prefix) > len(full)
                    or prefix != full[:len(prefix)]):
                raise ValueError("route checkpoint history diverged")
            prefix_lengths[unit_id] = len(prefix)
        expected_states, expected_accepted, expected_total, expected_counters, expected_usage = project_checkpoint(prefix_lengths)
        if (saved.get("logical_units") != len(prepared)
                or saved.get("provider_omitted_units") != sum(1 for candidate in prepared if candidate.get("provider_omitted"))
                or saved.get("unit_states") != expected_states
                or saved.get("accepted_units") != expected_accepted
                or saved.get("provider_attempts_total") != expected_total
                or saved.get("counters") != expected_counters
                or saved.get("usage_by_route") != expected_usage):
            raise ValueError("route checkpoint identity or history mismatch")
        behind = any(prefix_lengths[key] < len(checkpoint_routes[key]) for key in checkpoint_routes)
        if saved.get("status") == "complete" and behind:
            raise ValueError("route checkpoint is ahead of verified history")
        if saved.get("status") == "complete" and expected_accepted != len(prepared) - sum(1 for candidate in prepared if candidate.get("provider_omitted")):
            raise ValueError("complete route checkpoint has non-accepted unit history")
        return behind

    def record_attempt(unit: Mapping[str, Any], terminal: Mapping[str, Any]) -> None:
        number = len(history_cache[unit["unit_id"]]) + 1
        path = root / "units" / unit["unit_key"] / f"attempt-{number:03d}"
        verified = _verified_attempt(root, path, number, unit["request_identity"], run_identity, unit["unit_id"])
        if dict(verified) != dict(terminal):
            raise ValueError("new attempt terminal changed before checkpoint")
        history_cache[unit["unit_id"]].append(verified)
        validate_route_history(unit, history_cache[unit["unit_id"]])
        account_attempt(unit["unit_id"], verified)

    def run_attempt(unit: Mapping[str, Any], *, route: str, role: str, number: int) -> dict[str, Any]:
        profile = profiles[route]
        key = keys.get(route) or values.get(profile.api_key_env)
        if not isinstance(key, str) or not key:
            raise ValueError(f"{profile.api_key_env} is required before fallback invocation")
        transport = (transports or {}).get(route)
        route_body = unit.get("route_bodies", {}).get(route, unit["body"])
        route_wire_identity = unit.get("route_wire_request_identities", {}).get(route,
                                                                                   unit["wire_request_identity"])
        body = canonical_json_bytes(route_body)
        stem = root / "units" / unit["unit_key"] / f"attempt-{number:03d}"
        if any(secret.encode("utf-8") in body for secret in secret_values):
            raise ValueError("request body contains credential")
        request_artifact = _write_once(stem / "request.json", body)
        _json_once(stem / "issued.json", {
            "run_identity": run_identity, "unit_id": unit["unit_id"],
            "attempt_number": number, "request_identity": unit["request_identity"],
            "wire_request_identity": route_wire_identity,
            "route": route, "route_role": role, "route_config_identity": profile.config_identity,
            "issued_at": _now(),
        })
        artifacts: dict[str, Any] = {"request": request_artifact}
        stream_writer = _JsonlArtifactWriter(stem / "stream.jsonl", root)
        raw: bytes | None = None
        status: int | None = None
        headers: dict[str, str] = {}
        wire_count = 0
        started = time.monotonic()
        first_chunk_ms: float | None = None
        first_visible_ms: float | None = None
        visible_parts: list[str] = []
        reasoning_parts: list[str] = []
        finish_reason: str | None = None
        usage: Any = None
        provider_id: str | None = None
        stream_complete = False
        response_terminal = False
        done_tracker: _DoneTrackingStream | None = None
        sdk_error_type: str | None = None
        sdk_error_message: str | None = None
        sdk_error_details: dict[str, str] = {}

        def on_request(request: httpx.Request) -> None:
            nonlocal wire_count
            wire_count += 1
            if wire_count != 1 or json.loads(request.content) != route_body:
                raise ValueError("SDK wire request differs from frozen route-pair request")
            if any(secret.encode("utf-8") in request.content for secret in secret_values):
                raise ValueError("SDK wire request contains credential")
            artifacts["wire_request"] = _write_once(stem / "wire_request.bin", request.content)
            artifacts["request_metadata"] = _json_once(stem / "request_metadata.json", {
                "method": request.method, "url": str(request.url),
                "route": route, "headers": {name: request.headers[name] for name in ("accept", "content-type") if name in request.headers},
            })

        def on_response(response: httpx.Response) -> None:
            nonlocal raw, status, headers, done_tracker
            status = response.status_code
            headers = {name: response.headers[name] for name in ("retry-after", "x-request-id", "content-type") if name in response.headers}
            if status == 200:
                done_tracker = _DoneTrackingStream(response.stream)
                if response.is_stream_consumed:
                    done_tracker.observe(response.content)
                response.stream = done_tracker
            else:
                raw = response.read()
                if any(secret.encode("utf-8") in raw for secret in secret_values):
                    raise ValueError("raw response contains credential")
                artifacts["response"] = _write_once(stem / "raw_response.bin", raw)

        try:
            with httpx.Client(transport=transport, event_hooks={"request": [on_request], "response": [on_response]}) as http_client:
                with openai.OpenAI(base_url=profile.base_url, api_key=key, max_retries=0,
                                   timeout=timeout_seconds, http_client=http_client) as client:
                    if profile.api_surface == "responses":
                        stream = client.responses.create(**route_body)
                    else:
                        stream = client.chat.completions.create(**route_body)
                    for chunk in stream:
                        data = _chunk_mapping(chunk)
                        if any(secret.encode("utf-8") in canonical_json_bytes(data) for secret in secret_values):
                            raise ValueError("stream chunk contains credential")
                        stream_writer.append(data)
                        if first_chunk_ms is None:
                            first_chunk_ms = round((time.monotonic() - started) * 1000, 3)
                        identifier = data.get("id")
                        if isinstance(identifier, str):
                            provider_id = identifier
                        if profile.api_surface == "responses" and data.get("type") == "error":
                            error = _nested_mapping(data.get("error")) or data
                            sdk_error_type = "ResponsesStreamError"
                            sdk_error_message = _redact(str(error.get("message", "Responses stream error")), secret_values)
                            for field in ("code", "type", "param"):
                                value = error.get(field)
                                if isinstance(value, (str, int, float, bool)):
                                    sdk_error_details[f"provider_error_{field}"] = _redact(str(value), secret_values)[:1024]
                        chunk_usage = data.get("usage")
                        if isinstance(chunk_usage, Mapping):
                            usage = dict(chunk_usage)
                        if profile.api_surface == "responses":
                            value, reasoning, event_finish, event_usage, event_id, terminal = _responses_event_parts(data)
                            if value:
                                visible_parts.append(value)
                                if first_visible_ms is None:
                                    first_visible_ms = round((time.monotonic() - started) * 1000, 3)
                            if reasoning:
                                reasoning_parts.append(reasoning)
                            if event_finish is not None:
                                finish_reason = event_finish
                            if event_usage is not None:
                                usage = event_usage
                            if event_id is not None:
                                provider_id = event_id
                            if terminal:
                                response_terminal = True
                        else:
                            choices = data.get("choices")
                            if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
                                choice = choices[0]
                                value, reasoning = _stream_delta(choice)
                                if value:
                                    visible_parts.append(value)
                                    if first_visible_ms is None:
                                        first_visible_ms = round((time.monotonic() - started) * 1000, 3)
                                if reasoning:
                                    reasoning_parts.append(reasoning)
                                if isinstance(choice.get("finish_reason"), str):
                                    finish_reason = choice["finish_reason"]
                    # The SDK iterator may end cleanly on EOF even when the
                    # provider never sent a terminal choice.  Treat that as
                    # indeterminate rather than as a completed generation.
                    stream_complete = (finish_reason is not None and
                                       (response_terminal if profile.api_surface == "responses"
                                        else done_tracker is not None and done_tracker.saw_done))
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            sdk_error_type = type(exc).__name__
            sdk_error_message = _redact(str(exc), secret_values)
        except openai.APIStatusError as exc:
            sdk_error_type = type(exc).__name__
            sdk_error_message = _redact(str(exc), secret_values)
            status = exc.status_code
        except openai.APIError as exc:
            sdk_error_type = type(exc).__name__
            sdk_error_message = _redact(str(exc), secret_values)
            if status == 200 and type(exc) is openai.APIError and isinstance(exc.body, Mapping):
                sdk_error_details["origin"] = "sse_error_event"
                for field in ("code", "type", "param"):
                    value = exc.body.get(field)
                    if isinstance(value, (str, int, float, bool)):
                        sdk_error_details[f"provider_error_{field}"] = _redact(str(value), secret_values)[:1024]
        except Exception as exc:
            if wire_count != 1:
                raise
            sdk_error_type = type(exc).__name__
            sdk_error_message = _redact(str(exc), secret_values)

        if wire_count != 1 or "wire_request" not in artifacts:
            raise ValueError("SDK attempt accounting is ambiguous")
        if status is not None and status != 200 and "response" not in artifacts:
            raise ValueError("HTTP error response was not durably persisted")
        visible = "".join(visible_parts)
        reasoning = "".join(reasoning_parts)
        chunk_count = stream_writer.chunk_count
        if status == 200 and stream_complete and sdk_error_type is None:
            raw = _stream_envelope(provider_id, visible, reasoning, finish_reason, usage)
            if any(secret.encode("utf-8") in raw for secret in secret_values):
                raise ValueError("provider response echoed configured secret")
            artifacts["response"] = _write_once(stem / "raw_response.bin", raw)
        elapsed = round((time.monotonic() - started) * 1000, 3)
        disposition = "transport_failure"
        execution_state = "unknown"
        fallback_eligible = False
        if status == 403 and sdk_error_type == "PermissionDeniedError" and _policy_403_pre_generation(raw, status, chunk_count=chunk_count,
                                                        reasoning_chars=len(reasoning), visible_chars=len(visible),
                                                        usage=usage, finish_reason=finish_reason):
            disposition = "primary_policy_403_pre_generation" if role == "primary" else "fallback_terminal_failure"
            execution_state = "not_started"
            fallback_eligible = role == "primary" and route == "tokenmetro"
        elif status == 200 and stream_complete and sdk_error_type is None:
            execution_state = "complete"
            if finish_reason == "length":
                disposition = "output_budget_blocked"
            elif finish_reason != "stop":
                disposition = "stream_incomplete"
                execution_state = "unknown"
            else:
                try:
                    normalized, _content, _parsed, validation = _validate_raw_response(
                        _PARSER, raw or b"", expected_segment_ids=unit["segment_ids"],
                        segment_metadata=unit.get("segment_metadata"))
                    validate_b_v2_navigation_references(normalized)
                    artifacts["canonical_output"] = _json_once(stem / "canonical_output.json", normalized)
                    artifacts["validation"] = _json_once(stem / "validation.json", validation)
                    disposition = "accepted_for_local_contract"
                except SemanticResponseValidationError as exc:
                    artifacts["validation"] = _json_once(stem / "validation.json", exc.diagnostics)
                    disposition = "local_validation_failed"
                except ValueError as exc:
                    artifacts["validation"] = _json_once(stem / "validation.json", {"error": str(exc)})
                    disposition = "local_validation_failed"
        elif status is not None:
            execution_state = "unknown"
            disposition = "transport_failure"
        if sdk_error_type is not None:
            artifacts["sdk_error"] = _json_once(stem / "sdk_error.json", {
                "type": sdk_error_type, "message": sdk_error_message, "http_status": status,
                **sdk_error_details,
            })
        stream_descriptor = stream_writer.descriptor()
        if stream_descriptor is not None:
            artifacts["stream"] = stream_descriptor
        terminal = {
            "run_identity": run_identity, "unit_id": unit["unit_id"], "attempt_number": number,
            "request_identity": unit["request_identity"], "wire_request_identity": unit["wire_request_identity"],
            "route": route, "route_role": role, "route_config_identity": profile.config_identity,
            "model": model, "http_status": status, "sdk_error_type": sdk_error_type,
            "finish_reason": finish_reason, "provider_request_id": provider_id,
            "response_headers": headers, "usage": usage if usage is not None else "UNKNOWN",
            "usage_normalized": _normalize_usage(usage),
            "billing": {
                "reported_credit": usage.get("credit", "UNKNOWN") if isinstance(usage, Mapping) else "UNKNOWN",
                "currency": "UNKNOWN", "billable": "UNKNOWN",
            },
            "latency_ms": elapsed, "first_chunk_latency_ms": first_chunk_ms if first_chunk_ms is not None else "UNKNOWN",
            "first_visible_latency_ms": first_visible_ms if first_visible_ms is not None else "UNKNOWN",
            "stream_complete": stream_complete, "stream_chunk_count": chunk_count,
            "reasoning_chars": len(reasoning), "visible_chars": len(visible),
            "execution_state": execution_state, "fallback_eligible": fallback_eligible,
            "fallback_decision": "eligible" if fallback_eligible else "not_eligible",
            "disposition": disposition, "retry_classification": "terminal",
            "automatic_retry": False, "artifacts": artifacts, "terminal_at": _now(),
        }
        if any(secret.encode("utf-8") in canonical_json_bytes(terminal) for secret in secret_values):
            raise ValueError("terminal metadata contains credential")
        _json_once(stem / "terminal.json", terminal)
        return terminal

    checkpoint_behind = verify_checkpoint()
    if checkpoint_behind or not (root / "checkpoint.json").exists():
        # A terminal may be durable while the previous checkpoint is still a
        # valid prefix. Re-materialize from verified history before any I/O.
        checkpoint("partial", 0)

    issued_now = 0
    for unit in prepared:
        history = history_cache[unit["unit_id"]]
        if unit.get("provider_omitted"):
            continue
        if history and history[-1].get("disposition") == "accepted_for_local_contract":
            continue
        if history and not (len(history) == 1 and history[-1].get("disposition") == "primary_policy_403_pre_generation"):
            return checkpoint("blocked", issued_now, unit["unit_id"])
        route = "jizhi" if history else "tokenmetro"
        role = "fallback" if history else "primary"
        if route == "jizhi" and not (keys.get(route) or values.get(profiles[route].api_key_env)):
            return checkpoint("blocked_missing_fallback_credential", issued_now, unit["unit_id"])
        terminal = run_attempt(unit, route=route, role=role, number=len(history) + 1)
        record_attempt(unit, terminal)
        issued_now += 1
        if terminal["disposition"] == "accepted_for_local_contract":
            checkpoint("partial", issued_now)
            continue
        checkpoint("partial", issued_now, unit["unit_id"])
        if terminal.get("fallback_eligible"):
            if not (keys.get("jizhi") or values.get(profiles["jizhi"].api_key_env)):
                return checkpoint("blocked_missing_fallback_credential", issued_now, unit["unit_id"])
            fallback = run_attempt(unit, route="jizhi", role="fallback", number=2)
            record_attempt(unit, fallback)
            issued_now += 1
            if fallback["disposition"] == "accepted_for_local_contract":
                checkpoint("partial", issued_now)
                continue
            return checkpoint("blocked", issued_now, unit["unit_id"])
        if terminal.get("execution_state") == "unknown":
            return checkpoint("stopped_unknown_execution", issued_now, unit["unit_id"])
        return checkpoint("blocked", issued_now, unit["unit_id"])
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


def frozen_v5_gate_units(manifest_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load the explicit v5 gate manifest and its immutable source payloads."""
    from .semantic_live_runner import b_v5_experiment_contract

    manifest_path = Path(manifest_root)
    manifest = _read(manifest_path)
    contract = manifest.get("contract")
    experiment = b_v5_experiment_contract()
    if (manifest.get("identity") != sha256_json(contract)
            or not isinstance(contract, Mapping)
            or contract.get("schema_version") not in {V5_GATE_SCHEMA_VERSION, V5_GATE_RESPONSES_SCHEMA_VERSION}
            or contract.get("experiment_revision") != experiment.revision
            or contract.get("experiment_identity") != experiment.identity
            or contract.get("prompt_identity") != experiment.prompt_identity
            or contract.get("prompt_sha256") != sha256_json(experiment.prompt_contract)
            or contract.get("request_contract_identity") != experiment.request_contract_identity
            or contract.get("output_schema_identity") != experiment.output_schema_identity
            or contract.get("frozen_preflight_identity") != experiment.frozen_preflight_identity
            or contract.get("frozen_semantic_build_identity") != experiment.frozen_semantic_build_identity
            or contract.get("source_identity") != experiment.frozen_semantic_build_identity
            or contract.get("source_binding_policy") != STRICT_SOURCE_BINDING_POLICY):
        raise ValueError("v5 gate manifest identity or experiment binding mismatch")
    operating = contract.get("operating_point")
    primary_surface = ("responses" if contract.get("schema_version") == V5_GATE_RESPONSES_SCHEMA_VERSION
                        else "chat_completions")
    expected_operating = {
        "model": "deepseek-v4.1-flash", "primary_route": "tokenmetro", "fallback_route": "jizhi",
        "stream": True, "automatic_retry": False, "max_tokens": V5_GATE_MAX_TOKENS,
        "timeout_seconds": V5_GATE_TIMEOUT_SECONDS, "generation_parameters": {},
        "primary_route_config": {**ROUTE_PROFILES["tokenmetro"].safe_dict(), "api_surface": primary_surface},
        "fallback_route_config": ROUTE_PROFILES["jizhi"].safe_dict(),
    }
    if operating != expected_operating:
        raise ValueError("v5 gate operating point is not the frozen route-pair configuration")
    rows = contract.get("units")
    if not isinstance(rows, list) or len(rows) != 4:
        raise ValueError("v5 gate manifest must contain exactly four units")
    units_by_id: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("v5 gate manifest unit is invalid")
        unit_id = row.get("compilation_unit_id")
        if not isinstance(unit_id, str) or unit_id in units_by_id:
            raise ValueError("v5 gate manifest unit identity is invalid")
        units_by_id[unit_id] = row
    source_roots = {str(row.get("source", {}).get("root")) for row in rows if isinstance(row.get("source"), Mapping)}
    if len(source_roots) != 1:
        raise ValueError("v5 gate source roots are not singular")
    source_root = Path(next(iter(source_roots)))
    units_path = source_root / "units.json"
    payloads_path = source_root / "payloads.jsonl.gz"
    source_units_body = json.loads(units_path.read_text(encoding="utf-8"))
    source_units = source_units_body.get("items") if isinstance(source_units_body, Mapping) else None
    if not isinstance(source_units, list):
        raise ValueError("v5 gate source units are invalid")
    with gzip.open(payloads_path, "rt", encoding="utf-8") as handle:
        payloads = [json.loads(line) for line in handle]
    if len(source_units) != len(payloads):
        raise ValueError("v5 gate source unit/payload count mismatch")
    indexed = {row.get("compilation_unit_id"): (row, payloads[index])
               for index, row in enumerate(source_units)
               if isinstance(row, Mapping)}
    result: list[dict[str, Any]] = []
    for row in rows:
        unit_id = str(row["compilation_unit_id"])
        source = row.get("source")
        if not isinstance(source, Mapping) or not isinstance(source.get("payload_index"), int):
            raise ValueError("v5 gate source binding is invalid")
        index = source["payload_index"]
        if index < 0 or index >= len(source_units):
            raise ValueError("v5 gate source payload index is invalid")
        source_row, payload = source_units[index], payloads[index]
        if (source_row.get("compilation_unit_id") != unit_id
                or indexed.get(unit_id) != (source_row, payload)
                or semantic_input_identity(payload) != row.get("semantic_input_identity")
                or _sha(canonical_json_bytes(payload)) != row.get("payload_sha256")
                or source_row.get("semantic_input_identity") != row.get("semantic_input_identity")
                or source_row.get("payload_sha256") != row.get("payload_sha256")
                or source_row.get("segment_ids") != row.get("segment_ids")):
            raise ValueError("v5 gate source payload or segment binding mismatch")
        messages = [
            {"role": "system", "content": canonical_json_bytes(experiment.prompt_contract).decode("utf-8")},
            {"role": "user", "content": canonical_json_bytes(payload).decode("utf-8")},
        ]
        body = {"model": operating["model"], "messages": messages,
                "max_tokens": operating["max_tokens"], "stream": True,
                **operating["generation_parameters"]}
        expected_request = sha256_json({
            "source_identity": experiment.frozen_semantic_build_identity,
            "prompt_identity": experiment.prompt_identity,
            "schema_identity": experiment.output_schema_identity,
            "compilation_unit_id": unit_id,
            "semantic_input_identity": row["semantic_input_identity"],
            "model": operating["model"], "messages": messages, "payload": payload,
            "stream": True, "max_tokens": operating["max_tokens"],
            "generation_parameters": operating["generation_parameters"],
        })
        expected_route_wires = {
            "tokenmetro": sha256_json(_wire_body_for_surface(body, operating["primary_route_config"]["api_surface"])),
            "jizhi": sha256_json(_wire_body_for_surface(body, operating["fallback_route_config"]["api_surface"])),
        }
        if (row.get("request_identity") != expected_request
                or row.get("wire_request_identity") != expected_route_wires["tokenmetro"]
                or (row.get("route_wire_request_identities") is not None
                    and row.get("route_wire_request_identities") != expected_route_wires)):
            raise ValueError("v5 gate request identity mismatch")
        result.append({"compilation_unit_id": unit_id,
                       "semantic_input_identity": row["semantic_input_identity"],
                       "payload": payload, "segment_ids": row["segment_ids"]})
    return dict(manifest), result


def run_sdk_route_canary(
    root: Path,
    *,
    route: str,
    units: Sequence[Mapping[str, Any]],
    prompt: Mapping[str, Any],
    prompt_identity: str,
    source_identity: str | None = None,
    schema_identity: str = SEMANTIC_OUTPUT_SCHEMA_IDENTITY,
    model: str = "deepseek-v4.1-flash",
    max_tokens: int = 500000,
    timeout_seconds: float = 300.0,
    environment: Mapping[str, str] | None = None,
    transport: Any = None,
) -> dict[str, Any]:
    """Issue one explicit-route canary without automatic fallback or retry."""

    if len(units) != 1 or route not in ROUTE_PROFILES:
        raise ValueError("route canary requires one frozen unit and an explicit route")
    import httpx
    import openai

    values = os.environ if environment is None else environment
    profile = route_profile(route, values, require_environment=True)
    key = values.get(profile.api_key_env)
    if not isinstance(key, str) or not key or model != profile.model_id:
        raise ValueError("route canary credential or DeepSeek model is invalid")
    unit = units[0]
    payload = unit["payload"]
    if semantic_input_identity(payload) != unit["semantic_input_identity"]:
        raise ValueError("semantic input identity mismatch")
    if schema_identity != SEMANTIC_OUTPUT_SCHEMA_IDENTITY or max_tokens <= 0 or timeout_seconds <= 0:
        raise ValueError("invalid route canary operating point")
    body = {"model": model, "messages": [
        {"role": "system", "content": canonical_json_bytes(prompt).decode("utf-8")},
        {"role": "user", "content": canonical_json_bytes(payload).decode("utf-8")},
    ], "max_tokens": max_tokens, "stream": True}
    request_identity = sha256_json({
        "source_identity": source_identity, "prompt_identity": prompt_identity,
        "schema_identity": schema_identity, "compilation_unit_id": unit["compilation_unit_id"],
        "semantic_input_identity": unit["semantic_input_identity"], "model": model,
        "messages": body["messages"], "payload": payload,
        "stream": True, "max_tokens": max_tokens, "generation_parameters": {},
    })
    root = Path(root)
    if root.exists():
        raise ValueError("route canary root already exists")
    root.mkdir(parents=True)
    manifest = {
        "runner": "phase05-semantic-sdk-route-canary-0.1", "route": profile.safe_dict(),
        "sdk_version": openai.__version__, "source_identity": source_identity,
        "prompt_identity": prompt_identity, "prompt_sha256": sha256_json(prompt),
        "schema_identity": schema_identity, "unit_id": unit["compilation_unit_id"],
        "semantic_input_identity": unit["semantic_input_identity"],
        "request_identity": request_identity, "wire_request_identity": sha256_json(body),
        "max_tokens": max_tokens, "stream": True, "automatic_retry": False,
    }
    _json_once(root / "manifest.json", manifest)
    stem = root / "attempt-001"
    artifacts: dict[str, Any] = {"request": _json_once(stem / "request.json", body)}
    _json_once(stem / "issued.json", {"request_identity": request_identity, "route": route, "issued_at": _now()})
    chunks: list[dict[str, Any]] = []
    stream_writer = _JsonlArtifactWriter(stem / "stream.jsonl", root)
    provider_id: str | None = None
    raw_error: bytes | None = None
    status: int | None = None
    started = time.monotonic()
    error_type: str | None = None
    wire_count = 0
    done_tracker: _DoneTrackingStream | None = None
    first_chunk_ms: float | None = None
    first_visible_ms: float | None = None

    def on_request(request: httpx.Request) -> None:
        nonlocal wire_count
        wire_count += 1
        if wire_count != 1 or json.loads(request.content) != body or key.encode() in request.content:
            raise ValueError("canary SDK wire request differs from frozen request")
        artifacts["wire_request"] = _write_once(stem / "wire_request.bin", request.content)

    def on_response(response: httpx.Response) -> None:
        nonlocal raw_error, status, done_tracker
        status = response.status_code
        if status == 200:
            done_tracker = _DoneTrackingStream(response.stream)
            if response.is_stream_consumed:
                done_tracker.observe(response.content)
            response.stream = done_tracker
        else:
            raw_error = response.read()
            if key.encode() in raw_error:
                raise ValueError("canary response contains credential")
            artifacts["raw_error"] = _write_once(stem / "raw_error.bin", raw_error)

    try:
        with httpx.Client(transport=transport, event_hooks={"request": [on_request], "response": [on_response]}) as http_client:
            with openai.OpenAI(base_url=profile.base_url, api_key=key, max_retries=0,
                               timeout=timeout_seconds, http_client=http_client) as client:
                for chunk in client.chat.completions.create(**body):
                    data = _chunk_mapping(chunk)
                    if key.encode() in canonical_json_bytes(data):
                        raise ValueError("canary stream chunk contains credential")
                    stream_writer.append(data)
                    chunks.append(data)
                    if isinstance(data.get("id"), str):
                        provider_id = data["id"]
                    if first_chunk_ms is None:
                        first_chunk_ms = round((time.monotonic() - started) * 1000, 3)
                    choices = data.get("choices")
                    if (first_visible_ms is None and isinstance(choices, list) and choices
                            and isinstance(choices[0], Mapping) and _stream_delta(choices[0])[0]):
                        first_visible_ms = round((time.monotonic() - started) * 1000, 3)
    except Exception as exc:
        error_type = type(exc).__name__
    if wire_count != 1 or "wire_request" not in artifacts:
        raise ValueError("canary SDK attempt accounting is ambiguous")
    if status is not None and status != 200 and "raw_error" not in artifacts:
        raise ValueError("canary HTTP error was not durably persisted")
    visible: list[str] = []
    reasoning: list[str] = []
    finish_reason: str | None = None
    usage: Any = None
    for chunk in chunks:
        choices = chunk.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
            text, thought = _stream_delta(choices[0])
            visible.append(text)
            reasoning.append(thought)
            if isinstance(choices[0].get("finish_reason"), str):
                finish_reason = choices[0]["finish_reason"]
        if isinstance(chunk.get("usage"), Mapping):
            usage = chunk["usage"]
    disposition = "transport_or_stream_failure"
    if status == 200 and error_type is None and finish_reason == "stop" and done_tracker is not None and done_tracker.saw_done:
        raw = _stream_envelope(provider_id, "".join(visible), "".join(reasoning), finish_reason, usage)
        if key.encode() in raw:
            raise ValueError("canary response contains credential")
        artifacts["response"] = _write_once(stem / "raw_response.bin", raw)
        try:
            normalized, _content, _parsed, validation = _validate_raw_response(
                _PARSER, raw, expected_segment_ids=unit["segment_ids"],
                segment_metadata=semantic_segment_binding_metadata(payload))
            validate_b_v2_navigation_references(normalized)
            artifacts["validation"] = _json_once(stem / "validation.json", validation)
            artifacts["canonical_output"] = _json_once(stem / "canonical_output.json", normalized)
            disposition = "accepted_for_local_contract"
        except (SemanticResponseValidationError, ValueError) as exc:
            artifacts["validation"] = _json_once(stem / "validation.json", {"error": str(exc)})
            disposition = "local_validation_failed"
    stream_descriptor = stream_writer.descriptor()
    if stream_descriptor is not None:
        artifacts["stream"] = stream_descriptor
    terminal = {
        "route": route, "request_identity": request_identity, "http_status": status,
        "sdk_error_type": error_type, "finish_reason": finish_reason,
        "stream_complete": done_tracker is not None and done_tracker.saw_done and finish_reason is not None,
        "stream_chunk_count": len(chunks), "visible_chars": len("".join(visible)),
        "reasoning_chars": len("".join(reasoning)), "usage": usage if usage is not None else "UNKNOWN",
        "usage_normalized": _normalize_usage(usage),
        "latency_ms": round((time.monotonic() - started) * 1000, 3),
        "first_chunk_latency_ms": first_chunk_ms if first_chunk_ms is not None else "UNKNOWN",
        "first_visible_latency_ms": first_visible_ms if first_visible_ms is not None else "UNKNOWN",
        "disposition": disposition, "artifacts": artifacts, "terminal_at": _now(),
    }
    _json_once(stem / "terminal.json", terminal)
    return terminal


def replay_sdk_route_attempt(root: Path, unit_id: str, attempt_number: int,
                             expected_segment_ids: Sequence[str]) -> dict[str, Any]:
    """Revalidate a preserved route-pair response without loading credentials or network."""

    root = Path(root)
    manifest = _read(root / "manifest.json")
    contract = manifest.get("contract")
    if not isinstance(contract, Mapping) or manifest.get("identity") != sha256_json(contract):
        raise ValueError("route-pair manifest integrity mismatch")
    if contract.get("runner") != "phase05-semantic-sdk-route-pair-0.1":
        raise ValueError("not a route-pair evidence root")
    matching = [row for row in contract.get("units", []) if row.get("unit_id") == unit_id]
    if len(matching) != 1:
        raise ValueError("unit is not in route-pair manifest")
    unit = matching[0]
    if list(expected_segment_ids) != unit.get("segment_ids"):
        raise ValueError("replay segment binding differs from manifest")
    stem = root / "units" / _sha(unit_id.encode("utf-8")) / f"attempt-{attempt_number:03d}"
    issued = _read(stem / "issued.json")
    terminal = _read(stem / "terminal.json")
    if (issued.get("request_identity") != unit["request_identity"]
            or terminal.get("request_identity") != unit["request_identity"]
            or issued.get("route") != terminal.get("route")
            or terminal.get("attempt_number") != attempt_number):
        raise ValueError("route replay attempt identity mismatch")
    artifacts = terminal.get("artifacts", {})
    _verify_artifacts(root, artifacts)
    _verify_manifest_request_binding(root, unit, artifacts)
    if terminal.get("disposition") != "accepted_for_local_contract":
        raise ValueError("route attempt has no accepted output to replay")
    raw = (root / str(artifacts["response"]["path"])).read_bytes()
    normalized, _content, _parsed, _validation = _validate_raw_response(
        _PARSER, raw, expected_segment_ids=expected_segment_ids,
        segment_metadata=(unit.get("segment_metadata")
                          if contract.get("source_binding_policy") == STRICT_SOURCE_BINDING_POLICY else None))
    validate_b_v2_navigation_references(normalized)
    if normalized != _read(root / str(artifacts["canonical_output"]["path"])):
        raise ValueError("provider-free replay differs from accepted output")
    return {
        "status": "PASS", "route": terminal["route"], "unit_id": unit_id,
        "attempt_number": attempt_number, "provider_calls_executed": 0,
        "network_calls_executed": 0,
    }


def audit_sdk_route_integrity(root: Path) -> dict[str, Any]:
    """Run a complete offline integrity/replay audit over a route-pair root.

    Resume uses the incremental ledger above; this function is the explicit
    expensive operation for operators who want every historical artifact
    rehashed and every accepted output replayed.
    """

    root = Path(root)
    manifest = _read(root / "manifest.json")
    contract = manifest.get("contract")
    if (not isinstance(contract, Mapping)
            or manifest.get("identity") != sha256_json(contract)
            or contract.get("runner") != "phase05-semantic-sdk-route-pair-0.1"):
        raise ValueError("route-pair manifest integrity mismatch")
    profiles = {
        route: profile for route in ("tokenmetro", "jizhi")
        if isinstance(profile := contract.get("primary_route" if route == "tokenmetro" else "fallback_route"), Mapping)
    }
    for route, key in (("tokenmetro", "primary_route_config_identity"),
                       ("jizhi", "fallback_route_config_identity")):
        profile = profiles.get(route)
        if profile is None or (key in contract and contract.get(key) != sha256_json(profile)):
            raise ValueError("route configuration identity is missing or changed")
    attempts_total = 0
    accepted_total = 0
    for unit in contract.get("units", []):
        if not isinstance(unit, Mapping):
            raise ValueError("route-pair manifest unit is invalid")
        unit_id = unit.get("unit_id")
        if not isinstance(unit_id, str):
            raise ValueError("route-pair manifest unit is invalid")
        history = _attempts(root, _sha(unit_id.encode("utf-8")), str(unit["request_identity"]),
                            str(manifest["identity"]), unit_id)
        for attempt in history:
            route = attempt.get("route")
            if route not in profiles or attempt.get("route_config_identity") != sha256_json(profiles[route]):
                raise ValueError("attempt route configuration identity changed")
            expected_wire_identity = unit.get("route_wire_request_identities", {}).get(
                route, unit.get("wire_request_identity"))
            _verify_manifest_request_binding(root, unit, attempt.get("artifacts", {}),
                                             expected_wire_identity=expected_wire_identity)
            attempts_total += 1
            if attempt.get("disposition") == "accepted_for_local_contract":
                replay_sdk_route_attempt(root, unit_id, int(attempt["attempt_number"]), unit.get("segment_ids", []))
                accepted_total += 1
    return {
        "status": "PASS", "logical_units": len(contract.get("units", [])),
        "attempts": attempts_total, "accepted_attempts": accepted_total,
        "provider_calls_executed": 0, "network_calls_executed": 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Resumable Phase 05 TokenMetro SDK runner")
    parser.add_argument("--preflight-root", type=Path)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--gate-manifest", type=Path,
                        help="explicit provider-free v5 live-gate manifest; required for route-pair execution")
    parser.add_argument("--model", choices=tuple(TOKENMETRO_MODEL_IDS), required=True)
    parser.add_argument("--experiment-revision", choices=(B_V5_EXPERIMENT_REVISION,))
    parser.add_argument("--ordinal", type=int, action="append")
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--primary-route", choices=("tokenmetro",), default=None)
    parser.add_argument("--fallback-route", choices=("jizhi",), default=None)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--route-canary", choices=("tokenmetro", "jizhi"), default=None)
    args = parser.parse_args(argv)
    if args.route_canary is not None and (args.primary_route is not None or args.fallback_route is not None):
        parser.error("choose either --route-canary or the route pair")
    if args.stream and args.route_canary is None and args.primary_route is None and args.fallback_route is None:
        parser.error("--stream requires --route-canary or explicit route-pair mode")
    if args.gate_manifest is not None:
        if args.experiment_revision != B_V5_EXPERIMENT_REVISION:
            parser.error("--gate-manifest requires explicit v5 experiment selection")
        if args.route_canary is not None or args.model != "deepseek":
            parser.error("v5 gate manifest requires DeepSeek route-pair execution")
        if (args.primary_route, args.fallback_route) != ("tokenmetro", "jizhi"):
            parser.error("v5 gate manifest requires --primary-route tokenmetro and --fallback-route jizhi")
        if not args.stream or args.max_retries != 0:
            parser.error("v5 gate manifest requires stream mode and zero automatic retries")
    elif args.primary_route is not None or args.fallback_route is not None:
        parser.error("automatic paid route-pair execution remains pilot-gated; use one explicit --route-canary")
    elif args.preflight_root is None:
        parser.error("--preflight-root is required without --gate-manifest")
    if args.route_canary is not None:
        if args.model != "deepseek" or not args.stream or args.max_retries != 0 or len(args.ordinal or []) != 1:
            parser.error("route canary requires DeepSeek, stream, zero retries, and one ordinal")
    from .semantic_live_runner import b_v2_experiment_contract, b_v5_experiment_contract
    if args.gate_manifest is not None:
        _manifest, units = frozen_v5_gate_units(args.gate_manifest)
        experiment = b_v5_experiment_contract()
        gate_operating = _manifest["contract"]["operating_point"]
        for route, key in (("tokenmetro", "primary_route_config"), ("jizhi", "fallback_route_config")):
            if route_profile(route, os.environ, require_environment=True).safe_dict() != gate_operating[key]:
                parser.error(f"{route} runtime route configuration differs from the frozen v5 gate")
        model_id = gate_operating["model"]
        max_tokens = gate_operating["max_tokens"] if args.max_tokens is None else args.max_tokens
        timeout_seconds = gate_operating["timeout_seconds"]
        if max_tokens != gate_operating["max_tokens"]:
            parser.error("--max-tokens cannot override the frozen v5 gate operating point")
        source_identity = experiment.frozen_semantic_build_identity
    else:
        experiment = b_v2_experiment_contract()
        units = frozen_preflight_units(args.preflight_root, args.model, args.ordinal)
        model_id = tokenmetro_model_id(args.model)
        max_tokens = 16384 if args.max_tokens is None else args.max_tokens
        timeout_seconds = 300.0
        source_identity = "8601fb2e665fd5f1bc1b7a9ef35a871269e3ea00f0b9a23654656a0bb3838da0"
    common = {
        "model": model_id, "units": units,
        "prompt": experiment.prompt_contract, "prompt_identity": experiment.prompt_identity,
        "source_identity": source_identity, "max_tokens": max_tokens,
    }
    if args.route_canary is not None:
        result = run_sdk_route_canary(args.run_root, route=args.route_canary, **common)
    else:
        result = run_sdk_units(args.run_root, max_retries=args.max_retries,
                               primary_route=args.primary_route, fallback_route=args.fallback_route,
                               stream=args.stream, timeout_seconds=timeout_seconds,
                               generation_parameters={}, **common)
    print(canonical_json_bytes(result).decode("utf-8"))
    if args.route_canary is not None:
        return 0 if result["disposition"] == "accepted_for_local_contract" else 1
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
