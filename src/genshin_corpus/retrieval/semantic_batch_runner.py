"""Immutable, resumable semantic batch coordination.

The coordinator deliberately keeps provider execution behind a small callable
boundary.  The callable may use the existing SDK route implementation in a
live process, while tests can use a provider-free fake.  The coordinator owns
lineage, unit state, locks, and derived indexes; per-attempt artifacts remain
the authority.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from .semantic_compiler_u1 import semantic_input_identity


PENDING = "pending_never_issued"
OMITTED = "provider_omitted"
IN_FLIGHT = "in_flight"
ACCEPTED = "accepted_for_local_contract"
LOCAL_REJECT = "local_correctness_reject"
TERMINAL_FAILURE = "provider_terminal_failure"
UNKNOWN = "execution_unknown"

SAFE_RERUN_FAILURES = {"pre_generation_reject"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _write_once(path: Path, value: Any) -> None:
    if path.exists():
        raise ValueError(f"immutable artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, canonical_json_bytes(value))


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(canonical_json_bytes(value) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def _unit_key(unit_id: str) -> str:
    import hashlib
    return hashlib.sha256(unit_id.encode("utf-8")).hexdigest()


def _unit_map(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = manifest.get("units")
    if not isinstance(rows, list):
        raise ValueError("batch manifest units are invalid")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("compilation_unit_id"), str):
            raise ValueError("batch manifest unit is invalid")
        unit_id = str(row["compilation_unit_id"])
        if unit_id in result:
            raise ValueError("duplicate batch unit")
        result[unit_id] = row
    return result


@dataclass(frozen=True)
class AttemptOutcome:
    """Provider adapter result consumed by the coordinator."""

    disposition: str
    execution_certainty: str
    failure_class: str | None = None
    http_status: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    artifacts: Mapping[str, Any] | None = None
    metrics: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        valid = {ACCEPTED, LOCAL_REJECT, TERMINAL_FAILURE, UNKNOWN}
        if self.disposition not in valid:
            raise ValueError("invalid batch attempt disposition")
        if self.execution_certainty not in {"complete", "not_started", "unknown"}:
            raise ValueError("invalid execution certainty")
        if self.disposition == TERMINAL_FAILURE and self.failure_class not in SAFE_RERUN_FAILURES | {"terminal"}:
            raise ValueError("terminal failure requires a verified failure class")
        if self.disposition == UNKNOWN and self.execution_certainty != "unknown":
            raise ValueError("unknown execution requires unknown certainty")
        if self.disposition == ACCEPTED and self.execution_certainty != "complete":
            raise ValueError("accepted result requires complete execution")
        if self.disposition == LOCAL_REJECT and self.execution_certainty not in {"complete", "not_started"}:
            raise ValueError("local correctness reject requires known local execution boundary")

    def as_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition,
            "execution_certainty": self.execution_certainty,
            "failure_class": self.failure_class,
            "http_status": self.http_status,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "artifacts": dict(self.artifacts or {}),
            "metrics": dict(self.metrics or {}),
        }


Executor = Callable[[Mapping[str, Any], Path, Mapping[str, Any]], AttemptOutcome | Mapping[str, Any]]


def classify_execution(*, issued: bool, http_status: int | None = None,
                       provider_semantics: str | None = None,
                       stream_complete: bool = False, terminal_persisted: bool = True,
                       local_validation_failed: bool = False) -> tuple[str, str, str | None]:
    """Classify execution without inferring certainty from generic HTTP codes."""

    if not issued:
        return LOCAL_REJECT, "not_started", "local_preflight"
    if not terminal_persisted or not stream_complete:
        return UNKNOWN, "unknown", "missing_or_incomplete_terminal"
    if local_validation_failed:
        return LOCAL_REJECT, "complete", "local_contract"
    if provider_semantics == "pre_generation_reject":
        return TERMINAL_FAILURE, "not_started", "pre_generation_reject"
    if http_status is not None and http_status != 200:
        return UNKNOWN, "unknown", "generic_provider_response"
    return ACCEPTED, "complete", None


def _normalize_outcome(value: AttemptOutcome | Mapping[str, Any]) -> AttemptOutcome:
    if isinstance(value, AttemptOutcome):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("executor returned an invalid outcome")
    return AttemptOutcome(
        disposition=str(value.get("disposition", UNKNOWN)),
        execution_certainty=str(value.get("execution_certainty", "unknown")),
        failure_class=value.get("failure_class"),
        http_status=value.get("http_status"),
        error_type=value.get("error_type"),
        error_message=value.get("error_message"),
        artifacts=value.get("artifacts"),
        metrics=value.get("metrics"),
    )


class _RunLock:
    def __init__(self, root: Path) -> None:
        self.path = root / "run.lock"
        self.handle: Any = None

    def __enter__(self) -> "_RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            if self.path.stat().st_size == 0:
                self.handle.write(b"\0")
                self.handle.flush()
                os.fsync(self.handle.fileno())
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.handle.seek(0)
            self.handle.truncate()
            self.handle.write(canonical_json_bytes({"pid": os.getpid(), "acquired_at": _now()}))
            self.handle.flush()
            os.fsync(self.handle.fileno())
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError(f"batch root is already active: {self.path.parent}") from exc
        return self

    def __exit__(self, *_: Any) -> None:
        if self.handle is not None:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


def prepare_batch(root: Path, *, units: Sequence[Mapping[str, Any]], route: str,
                  contract: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Create a new immutable batch root from frozen compiler units."""
    root = Path(root).resolve()
    if root.exists():
        raise ValueError("batch root already exists")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in units:
        unit = dict(source)
        unit_id = str(unit.get("compilation_unit_id", ""))
        if not unit_id or unit_id in seen or not isinstance(unit.get("payload"), Mapping):
            raise ValueError("invalid or duplicate frozen unit")
        if semantic_input_identity(unit["payload"]) != unit.get("semantic_input_identity"):
            raise ValueError(f"semantic input identity mismatch: {unit_id}")
        omitted = bool(unit.get("provider_omitted"))
        if omitted and unit.get("segment_ids"):
            raise ValueError("provider-omitted unit still has provider-visible segments")
        if omitted and not unit.get("omission_reason"):
            raise ValueError("provider omission requires an explicit omission reason")
        seen.add(unit_id)
        normalized.append(unit)
    normalized_contract = dict(contract or {})
    if normalized_contract.get("prompt") is not None:
        if normalized_contract.get("prompt_identity") != sha256_json(normalized_contract["prompt"]):
            raise ValueError("prompt identity does not match prompt content")
        if normalized_contract.get("prompt_sha256") not in {None, normalized_contract["prompt_identity"]}:
            raise ValueError("prompt sha256 does not match prompt identity")
    if normalized_contract.get("route") is not None and normalized_contract.get("route") != route:
        raise ValueError("batch contract route differs from manifest route")
    manifest = {
        "schema_version": "semantic-batch-1",
        "created_at": _now(),
        "mode": "initial",
        "route": route,
        "contract": normalized_contract,
        "selected_unit_ids": [u["compilation_unit_id"] for u in normalized if not u.get("provider_omitted")],
        "lineage_roots": [str(root)],
        "parent_root": None,
        "units": normalized,
    }
    manifest["identity"] = sha256_json({k: v for k, v in manifest.items() if k != "identity"})
    root.mkdir(parents=True)
    _write_once(root / "manifest.json", manifest)
    return manifest


def _load_manifest(root: Path) -> dict[str, Any]:
    manifest = _read(Path(root) / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("identity") != sha256_json({k: v for k, v in manifest.items() if k != "identity"}):
        raise ValueError("batch manifest identity mismatch")
    _unit_map(manifest)
    contract = manifest.get("contract")
    if not isinstance(contract, Mapping):
        raise ValueError("batch manifest contract is invalid")
    if contract.get("route") is not None and contract.get("route") != manifest.get("route"):
        raise ValueError("batch manifest route contract mismatch")
    if contract.get("prompt") is not None:
        prompt_identity = sha256_json(contract["prompt"])
        if contract.get("prompt_identity") != prompt_identity or contract.get("prompt_sha256", prompt_identity) != prompt_identity:
            raise ValueError("batch manifest prompt contract mismatch")
    return manifest


def _lineage_roots(root: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    """Resolve a single continuation chain and reject ambiguous branches."""
    requested = Path(root).resolve()
    _load_manifest(requested)
    head = requested
    while True:
        children: list[Path] = []
        for candidate in head.parent.iterdir():
            if not candidate.is_dir() or candidate == head:
                continue
            manifest_path = candidate / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                child_manifest = _load_manifest(candidate)
            except OSError:
                continue
            parent = child_manifest.get("parent_root")
            if (child_manifest.get("mode") == "continuation" and isinstance(parent, str)
                    and Path(parent).resolve() == head):
                children.append(candidate.resolve())
        if len(children) > 1:
            raise ValueError("batch lineage has multiple continuation heads; select one explicitly")
        if not children:
            break
        head = children[0]
    head_manifest = _load_manifest(head)
    if head_manifest.get("mode") == "rerun":
        parent_value = head_manifest.get("parent_root")
        if not isinstance(parent_value, str):
            raise ValueError("rerun branch parent is missing")
        parent = Path(parent_value).resolve()
        parent_manifest = _load_manifest(parent)
        declared_sources = head_manifest.get("source_lineage_roots")
        parent_sources = parent_manifest.get("lineage_roots") if parent_manifest.get("mode") != "rerun" else [str(parent)]
        if not isinstance(declared_sources, list) or declared_sources != parent_sources:
            raise ValueError("rerun branch source lineage is inconsistent")
        return [head], [head_manifest]
    raw = head_manifest.get("lineage_roots")
    if not isinstance(raw, list) or not raw:
        raise ValueError("batch lineage is invalid")
    roots = [Path(str(value)).resolve() for value in raw]
    if roots[-1] != head:
        raise ValueError("batch lineage head is inconsistent")
    manifests = [_load_manifest(candidate) for candidate in roots]
    for index, candidate_manifest in enumerate(manifests):
        declared = candidate_manifest.get("lineage_roots")
        expected = [str(value) for value in roots[:index + 1]]
        if declared != expected:
            raise ValueError("batch lineage manifest prefix is inconsistent")
        if index == 0:
            if candidate_manifest.get("parent_root") is not None:
                raise ValueError("initial batch cannot have a parent root")
        elif Path(str(candidate_manifest.get("parent_root"))).resolve() != roots[index - 1]:
            raise ValueError("batch parent root is inconsistent")
    return roots, manifests


def _attempt_dirs(root: Path, unit_id: str) -> list[Path]:
    directory = root / "units" / _unit_key(unit_id)
    if not directory.exists():
        return []
    return sorted(directory.glob("attempt-*"))


def _read_attempt(root: Path, path: Path) -> dict[str, Any]:
    issued_path = path / "issued.json"
    issued = _read(issued_path) if issued_path.exists() else None
    terminal_path = path / "terminal.json"
    if not terminal_path.exists():
        return {"attempt_number": issued.get("attempt_number") if issued else None, "issued": issued, "state": UNKNOWN,
                "execution_certainty": "unknown", "failure_class": "missing_terminal"}
    terminal = _read(terminal_path)
    if issued is not None and terminal.get("attempt_number") != issued.get("attempt_number"):
        raise ValueError("attempt identity mismatch")
    return terminal


def state_for_unit(root: Path, unit: Mapping[str, Any]) -> str:
    if unit.get("provider_omitted"):
        if _attempt_dirs(root, str(unit["compilation_unit_id"])):
            raise ValueError("provider-omitted unit has provider attempts")
        return OMITTED
    attempts = _attempt_dirs(root, str(unit["compilation_unit_id"]))
    if not attempts:
        return PENDING
    terminal = _read_attempt(root, attempts[-1])
    if terminal.get("state") == UNKNOWN and terminal.get("issued") is None and not (attempts[-1] / "issued.json").exists():
        return PENDING
    if terminal.get("disposition") == ACCEPTED:
        return ACCEPTED
    if terminal.get("disposition") == LOCAL_REJECT:
        return LOCAL_REJECT
    if terminal.get("disposition") == TERMINAL_FAILURE:
        return TERMINAL_FAILURE
    return UNKNOWN


def aggregate_status(root: Path) -> dict[str, Any]:
    roots, manifests = _lineage_roots(Path(root))
    units = _unit_map(manifests[0])
    if manifests[0].get("mode") == "rerun":
        selected = {str(value) for value in manifests[0].get("selected_unit_ids", [])}
        units = {unit_id: unit for unit_id, unit in units.items() if unit_id in selected}
    latest: dict[str, tuple[Path, str]] = {unit_id: (roots[0], state_for_unit(roots[0], unit)) for unit_id, unit in units.items()}
    attempted: set[str] = set()
    selected_in_lineage: set[str] = set()
    for candidate_root, manifest in zip(roots, manifests):
        if manifest.get("mode") != "initial":
            selected_in_lineage.update(str(value) for value in manifest.get("selected_unit_ids", []))
        for unit_id, unit in _unit_map(manifest).items():
            attempts = _attempt_dirs(candidate_root, unit_id)
            if attempts and any((attempt / "issued.json").exists() for attempt in attempts):
                attempted.add(unit_id)
                latest[unit_id] = (candidate_root, state_for_unit(candidate_root, unit))
            elif attempts:
                latest[unit_id] = (candidate_root, state_for_unit(candidate_root, unit))
            elif manifest.get("mode") == "continuation" and unit_id in manifest.get("selected_unit_ids", []):
                latest.setdefault(unit_id, (candidate_root, PENDING))
    counts: dict[str, int] = {}
    for _unit_id, (_root, state) in latest.items():
        counts[state] = counts.get(state, 0) + 1
    return {
        "lineage_roots": [str(value) for value in roots],
        "lineage_head": str(roots[-1]),
        "logical_units": len(units),
        "states": counts,
        "units": {unit_id: {"state": state, "root": str(unit_root), "attempted": unit_id in attempted,
                             "selected_in_lineage": unit_id in selected_in_lineage}
                  for unit_id, (unit_root, state) in latest.items()},
    }


def _new_child(parent: Path, *, mode: str, selected: Sequence[str], route: str | None = None) -> Path:
    roots, manifests = _lineage_roots(parent)
    head = roots[-1]
    head_manifest = manifests[-1]
    if Path(parent).resolve() != head.resolve():
        parent = head
    suffix = "cont" if mode == "continuation" else "rerun"
    index = 1
    while True:
        candidate = parent.parent / f"{parent.name}-{suffix}-{index:03d}"
        if not candidate.exists():
            break
        index += 1
    units = list(_unit_map(manifests[0]).values())
    selected_set = set(selected)
    child_contract = dict(head_manifest.get("contract") or {})
    if mode == "rerun" and route is not None and route != head_manifest.get("route"):
        from .semantic_tokenmetro_profile import route_profile
        profile = route_profile(route, require_environment=False)
        child_contract["route"] = route
        child_contract["route_override"] = True
        child_contract["route_config_identity"] = profile.config_identity
        child_contract["api_surface"] = profile.api_surface
    manifest = {
        "schema_version": "semantic-batch-1",
        "created_at": _now(),
        "mode": mode,
        "route": route or head_manifest.get("route"),
        "contract": child_contract,
        "selected_unit_ids": [str(value) for value in selected if str(value) in selected_set],
        "lineage_roots": ([*map(str, roots), str(candidate)] if mode == "continuation" else [str(candidate)]),
        "parent_root": str(parent),
        "source_lineage_roots": [*map(str, roots)],
        "units": units,
    }
    manifest["identity"] = sha256_json({k: v for k, v in manifest.items() if k != "identity"})
    candidate.mkdir(parents=True)
    _write_once(candidate / "manifest.json", manifest)
    return candidate


def create_resume(root: Path) -> Path:
    requested_manifest = _load_manifest(Path(root).resolve())
    if requested_manifest.get("mode") == "rerun":
        raise ValueError("resume cannot start from a rerun branch; use its source batch lineage")
    initial_status = aggregate_status(Path(root))
    head = Path(initial_status["lineage_head"])
    _assert_lineage_idle(Path(root))
    with _RunLock(head):
        status = aggregate_status(Path(root))
        head = Path(status["lineage_head"])
        manifest = _load_manifest(head)
        if manifest.get("mode") == "continuation" and not (head / "progress.jsonl").exists():
            raise ValueError("current continuation has not been executed")
        selected = [unit_id for unit_id, info in status["units"].items()
                    if info["state"] == PENDING and not info["attempted"]]
        if not selected:
            raise ValueError("lineage has no never-issued pending units")
        return _new_child(head, mode="continuation", selected=selected)


def create_rerun(root: Path, *, unit_ids: Sequence[str] | None = None,
                 states: Sequence[str] | None = None, route: str | None = None) -> Path:
    if unit_ids is None and states is None:
        raise ValueError("rerun requires explicit unit-id or state selection")
    if unit_ids is not None and not unit_ids or states is not None and not states:
        raise ValueError("rerun selection cannot be empty")
    initial_status = aggregate_status(Path(root))
    _assert_lineage_idle(Path(root))
    allowed = {LOCAL_REJECT, TERMINAL_FAILURE, UNKNOWN}
    wanted_states = set(states or ())
    if not wanted_states <= allowed:
        raise ValueError("rerun state filter contains a non-failure state")
    selected = set(unit_ids or ())
    with _RunLock(Path(initial_status["lineage_head"])):
        status = aggregate_status(Path(root))
        result: list[str] = []
        for unit_id, info in status["units"].items():
            if (not selected or unit_id in selected) and (not wanted_states or info["state"] in wanted_states):
                if info["state"] not in allowed:
                    continue
                result.append(unit_id)
        if not result:
            raise ValueError("rerun selection is empty")
        return _new_child(Path(status["lineage_head"]), mode="rerun", selected=result, route=route)


def _write_indexes(root: Path, records: Sequence[Mapping[str, Any]]) -> None:
    errors = [record for record in records if record.get("state") in {LOCAL_REJECT, TERMINAL_FAILURE, UNKNOWN}]
    if errors:
        atomic_write(root / "errors.jsonl", b"".join(canonical_json_bytes(row) + b"\n" for row in errors))
        atomic_write(root / "error.log", "".join(
            f"{row.get('unit_id')} state={row.get('state')} certainty={row.get('execution_certainty')} "
            f"failure={row.get('failure_class') or '-'} error={row.get('error_message') or '-'}\n"
            for row in errors).encode("utf-8"))


def _assert_lineage_idle(root: Path) -> None:
    roots, _ = _lineage_roots(root)
    for candidate in roots:
        with _RunLock(candidate):
            pass


def _assert_fresh_execution_root(root: Path) -> None:
    if (root / "progress.jsonl").exists() or (root / "status.json").exists():
        raise ValueError("batch root has already been executed; create a continuation child")
    if (root / "units").exists() and any(path.is_dir() for path in (root / "units").glob("**/attempt-*")):
        raise ValueError("batch root already contains provider attempts; create a continuation child")


def dry_run(root: Path) -> dict[str, Any]:
    """Provider-free preflight for a prepared or existing batch lineage."""
    status = aggregate_status(Path(root))
    head = Path(status["lineage_head"])
    manifest = _load_manifest(head)
    units = _unit_map(manifest)
    contract = manifest.get("contract") or {}
    prompt_contract_valid = True
    if contract.get("prompt") is not None:
        prompt_digest = sha256_json(contract["prompt"])
        prompt_contract_valid = contract.get("prompt_identity") == prompt_digest and contract.get("prompt_sha256", prompt_digest) == prompt_digest
    checks = {
        "manifest_identity": True,
        "semantic_input_identities": all(
            semantic_input_identity(unit["payload"]) == unit.get("semantic_input_identity")
            for unit in units.values() if isinstance(unit.get("payload"), Mapping)
        ),
        "route_declared": isinstance(manifest.get("route"), str) and bool(manifest.get("route")),
        "selected_units_known": all(str(value) in units for value in manifest.get("selected_unit_ids", [])),
        "prompt_contract": prompt_contract_valid,
        "schema_declared": contract.get("schema_identity") is None or bool(contract.get("schema_identity")),
        "source_declared": contract.get("source_identity") is None or bool(contract.get("source_identity")),
        "provider_calls": 0,
    }
    checks["ready"] = all(value is True or value == 0 for value in checks.values())
    return {"status": "READY" if checks["ready"] else "BLOCKED", "checks": checks, **status}


def run_batch(root: Path, *, workers: int = 1, executor: Executor,
              max_units: int | None = None, request_budget: int | None = None,
              wall_clock_seconds: float | None = None, stop_on_unknown: bool = True) -> dict[str, Any]:
    """Run selected units in one immutable root using a bounded worker pool."""
    if workers not in {1, 6, 12}:
        raise ValueError("workers must be one of 1, 6, or 12")
    if max_units is not None and max_units < 0:
        raise ValueError("max_units cannot be negative")
    if request_budget is not None and request_budget < 0:
        raise ValueError("request_budget cannot be negative")
    if wall_clock_seconds is not None and wall_clock_seconds < 0:
        raise ValueError("wall_clock_seconds cannot be negative")
    root = Path(root)
    manifest = _load_manifest(root)
    lineage_roots, _ = _lineage_roots(root)
    if lineage_roots[-1].resolve() != root.resolve():
        raise ValueError("batch root is not the current lineage head; run the continuation child")
    _assert_fresh_execution_root(root)
    selected = [str(value) for value in manifest.get("selected_unit_ids", [])]
    units = _unit_map(manifest)
    selected = [unit_id for unit_id in selected if unit_id in units and state_for_unit(root, units[unit_id]) == PENDING]
    if max_units is not None:
        selected = selected[:max_units]
    started = time.monotonic()
    issued = 0
    issued_lock = threading.Lock()
    records: list[dict[str, Any]] = []
    stop_event = threading.Event()
    interrupted = False

    def invoke(unit_id: str) -> dict[str, Any]:
        nonlocal issued
        unit = units[unit_id]
        number = len(_attempt_dirs(root, unit_id)) + 1
        stem = root / "units" / _unit_key(unit_id) / f"attempt-{number:03d}"
        stem.mkdir(parents=True, exist_ok=False)
        try:
            executor_result = executor(unit, stem, manifest)
            raw = _normalize_outcome(executor_result)
        except Exception as exc:
            issued_boundary = (stem / "issued.json").exists()
            raw = AttemptOutcome(
                UNKNOWN if issued_boundary else LOCAL_REJECT,
                "unknown" if issued_boundary else "not_started",
                failure_class="executor_exception" if issued_boundary else "local_preflight",
                error_type=type(exc).__name__, error_message=str(exc))
        terminal_path = stem / "terminal.json"
        if terminal_path.exists():
            terminal = _read(terminal_path)
            if terminal.get("unit_id") != unit_id or terminal.get("attempt_number") != number:
                raise ValueError("executor terminal identity mismatch")
        else:
            terminal = {"unit_id": unit_id, "attempt_number": number,
                        "run_identity": manifest["identity"],
                        "issued": (stem / "issued.json").exists(),
                        **raw.as_dict(), "terminal_at": _now()}
            _write_once(terminal_path, terminal)
        with issued_lock:
            issued += int((stem / "issued.json").exists())
        return {"unit_id": unit_id, "attempt_number": number, "state": raw.disposition,
                "execution_certainty": terminal.get("execution_certainty", raw.execution_certainty),
                "failure_class": terminal.get("failure_class", raw.failure_class),
                "error_message": terminal.get("error_message", raw.error_message)}

    with _RunLock(root):
        locked_lineage_roots, _ = _lineage_roots(root)
        if locked_lineage_roots[-1].resolve() != root.resolve():
            raise ValueError("batch root is not the current lineage head; run the continuation child")
        _assert_fresh_execution_root(root)
        _append_jsonl(root / "progress.jsonl", {
            "event": "run_started", "at": _now(), "workers": workers,
            "selected_units": len(selected), "request_budget": request_budget,
            "wall_clock_seconds": wall_clock_seconds,
        })
        futures: dict[Future[dict[str, Any]], str] = {}
        next_index = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            def submit_more() -> None:
                nonlocal next_index
                while len(futures) < workers and next_index < len(selected):
                    if stop_event.is_set() or (request_budget is not None and next_index >= request_budget):
                        return
                    if wall_clock_seconds is not None and time.monotonic() - started >= wall_clock_seconds:
                        return
                    unit_id = selected[next_index]
                    next_index += 1
                    futures[pool.submit(invoke, unit_id)] = unit_id

            submit_more()
            try:
                while futures:
                    done, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
                    for future in done:
                        futures.pop(future, None)
                        record = future.result()
                        records.append(record)
                        _append_jsonl(root / "progress.jsonl", {
                            "event": "unit_finished", "at": _now(), **record,
                        })
                        if stop_on_unknown and record["state"] == UNKNOWN:
                            stop_event.set()
                    submit_more()
            except KeyboardInterrupt:
                interrupted = True
                stop_event.set()
                for future in futures:
                    future.cancel()
                # Running workers are allowed to finish their current
                # attempt; no new units are submitted and the root remains
                # immutable after this invocation.
            for future, unit_id in list(futures.items()):
                if future.cancelled():
                    continue
                if future.done():
                    record = future.result()
                    if not any(existing.get("unit_id") == unit_id for existing in records):
                        records.append(record)
                        _append_jsonl(root / "progress.jsonl", {
                            "event": "unit_finished", "at": _now(), **record,
                        })
        _write_indexes(root, records)
        summary = aggregate_status(root)
        if interrupted:
            run_status = "interrupted"
        elif summary["states"].get(UNKNOWN):
            run_status = "stopped_unknown"
        elif summary["states"].get(PENDING):
            run_status = "partial"
        elif summary["states"].get(LOCAL_REJECT) or summary["states"].get(TERMINAL_FAILURE):
            run_status = "blocked"
        else:
            run_status = "complete"
        summary.update({"status": run_status,
                        "provider_attempts_this_invocation": issued,
                        "started_at": datetime.fromtimestamp(time.time() - (time.monotonic() - started), timezone.utc).isoformat()})
        _append_jsonl(root / "progress.jsonl", {
            "event": "run_finished", "at": _now(), "status": summary["status"],
            "provider_attempts_this_invocation": issued,
        })
        atomic_write(root / "status.json", canonical_json_bytes(summary))
        return summary


def offline_audit(root: Path) -> dict[str, Any]:
    status = aggregate_status(Path(root))
    authority_complete = True
    for unit_id, info in status["units"].items():
        candidate = Path(info["root"])
        unit = _unit_map(_load_manifest(candidate)).get(unit_id)
        if unit is not None:
            state_for_unit(candidate, unit)
            for attempt_dir in _attempt_dirs(candidate, unit_id):
                issued_path = attempt_dir / "issued.json"
                terminal_path = attempt_dir / "terminal.json"
                if not issued_path.exists() and not terminal_path.exists():
                    authority_complete = False
                    continue
                if not terminal_path.exists():
                    authority_complete = False
                    continue
                terminal = _read(terminal_path)
                if (terminal.get("run_identity") != _load_manifest(candidate).get("identity")
                        or terminal.get("unit_id") != unit_id
                        or terminal.get("attempt_number") != int(attempt_dir.name.split("-")[-1])):
                    raise ValueError("attempt authority identity mismatch")
                if terminal.get("disposition") == ACCEPTED and not issued_path.exists():
                    authority_complete = False
                artifacts = terminal.get("artifacts")
                if not isinstance(artifacts, Mapping):
                    authority_complete = False
                else:
                    from .semantic_sdk_runner import _verify_artifacts
                    _verify_artifacts(candidate, artifacts)
                    request_descriptor = artifacts.get("request")
                    wire_descriptor = artifacts.get("wire_request")
                    if isinstance(request_descriptor, Mapping) and isinstance(wire_descriptor, Mapping):
                        request_body = json.loads((candidate / str(request_descriptor["path"])).read_bytes())
                        wire_body = json.loads((candidate / str(wire_descriptor["path"])).read_bytes())
                        if request_body != wire_body:
                            raise ValueError("request and wire authority differ")
                        if terminal.get("wire_request_identity") not in {None, sha256_json(wire_body)}:
                            raise ValueError("wire authority identity mismatch")
                    if terminal.get("disposition") == ACCEPTED and not {
                        "response", "validation", "canonical_output"
                    }.issubset(artifacts):
                        authority_complete = False
    return {"status": "PASS" if authority_complete else "INCOMPLETE", "authority_complete": authority_complete, **status}


__all__ = [
    "ACCEPTED", "IN_FLIGHT", "LOCAL_REJECT", "OMITTED", "PENDING", "TERMINAL_FAILURE", "UNKNOWN",
    "AttemptOutcome", "aggregate_status", "classify_execution", "create_rerun", "create_resume",
    "offline_audit", "prepare_batch", "run_batch", "state_for_unit",
    "dry_run",
]
