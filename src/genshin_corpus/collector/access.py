"""Durable, per-user OBC access restrictions across collector processes."""
from contextlib import contextmanager
from datetime import timezone
from email.utils import parsedate_to_datetime
import math
import os
from pathlib import Path
import time
import uuid

from ..model_calls import locked
from .contract import RawIntegrityError, SOURCE, json_bytes, object_at
from .run_store import create_json
from .storage import atomic_write


class AccessPaused(Exception):
    def __init__(self, restriction):
        self.restriction = restriction
        super().__init__(restriction["reason"])


def _state_root():
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "GenshinChronicle" / "raw-collector"
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "genshinchronicle" / "raw-collector"


def _retry_after(headers):
    return next((value for key, value in headers.items() if key.lower() == "retry-after"), None)


def _delay(value, start, backoff):
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            seconds = deadline.timestamp() - start
        except (TypeError, ValueError, OverflowError, OSError):
            seconds = 0.0
    return max(1.0, backoff, seconds if math.isfinite(seconds) and math.isfinite(start + seconds) else 0.0)


class SourceAccess:
    def __init__(self, root=None, *, clock=time.time):
        self.root = (Path(root) if root is not None else _state_root()) / SOURCE
        self.clock = clock
        self.state_path = self.root / "state.json"
        self.initialized_path = self.root / "initialized.json"

    def _write(self, state):
        atomic_write(self.state_path, json_bytes(state))

    def _read(self):
        if not self.state_path.exists():
            if self.initialized_path.exists():
                raise RawIntegrityError("Collector source access state is missing")
            initial = {"version": 1, "in_flight": None, "incident": None, "cooldown": None}
            self._write(initial)
            create_json(self.initialized_path, {"version": 1})
            return initial
        state = object_at(self.state_path)
        if state.get("version") != 1 or any(field not in state for field in ("in_flight", "incident", "cooldown")):
            raise RawIntegrityError("Invalid collector source access state")
        for field in ("in_flight", "incident", "cooldown"):
            value = state[field]
            if value is None:
                continue
            if (not isinstance(value, dict) or not isinstance(value.get("event_id"), str)
                    or not value["event_id"].isalnum() or not isinstance(value.get("reason"), str)):
                raise RawIntegrityError("Invalid collector source access event")
            if field == "cooldown":
                deadline = value.get("not_before")
                if type(deadline) not in (int, float) or not math.isfinite(deadline):
                    raise RawIntegrityError("Invalid collector source cooldown deadline")
        if not self.initialized_path.exists():
            create_json(self.initialized_path, {"version": 1})
        return state

    @staticmethod
    def _incident(state):
        return state["incident"] or state["in_flight"]

    @contextmanager
    def attempt(self, backoff, request):
        with locked(self.root / "dispatch.lock"):
            state = self._read()
            incident = self._incident(state)
            if incident is not None:
                raise AccessPaused(incident)
            cooldown = state["cooldown"]
            if cooldown is not None and self.clock() < cooldown["not_before"]:
                raise AccessPaused(cooldown)
            event = {
                "event_id": uuid.uuid4().hex, "recorded_at": self.clock(),
                "reason": "previous_dispatch_status_unknown", "request": request,
            }
            state["in_flight"] = event
            self._write(state)
            observed = False

            def observe(status, headers):
                nonlocal observed
                if observed:
                    raise RawIntegrityError("Collector attempted to record HTTP status twice")
                observed = True
                state["in_flight"] = None
                if status == 403:
                    state["incident"] = {**event, "reason": "source_http_403", "status": 403,
                                         "observed_at": self.clock()}
                    self._write(state)
                    return state["incident"]
                if status == 429:
                    current = self.clock()
                    deadline = current + _delay(_retry_after(headers), current, backoff)
                    if cooldown is not None:
                        deadline = max(deadline, cooldown["not_before"])
                    state["cooldown"] = {**event, "reason": "source_http_429", "status": 429, "observed_at": current,
                                         "not_before": deadline}
                    self._write(state)
                    return state["cooldown"]
                self._write(state)
                return None

            yield observe
            if not observed:
                raise RawIntegrityError("Collector dispatch finished without recording its status")

    def acknowledge(self, event_id, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("An access restoration reason is required")
        with locked(self.root / "dispatch.lock"):
            state = self._read()
            incident = self._incident(state)
            if incident is None or incident["event_id"] != event_id:
                raise ValueError("Access event does not identify a blocked 403 or interrupted dispatch")
            receipt = self.root / "acknowledgments" / f"{event_id}.json"
            if receipt.exists():
                acknowledgment = object_at(receipt)
                if acknowledgment.get("event") != incident or acknowledgment.get("reason") != reason.strip():
                    raise RawIntegrityError("Existing access acknowledgment does not match")
            else:
                acknowledgment = {"event": incident, "reason": reason.strip(), "acknowledged_at": self.clock()}
                create_json(receipt, acknowledgment)
            state["incident"] = None
            state["in_flight"] = None
            self._write(state)
            return acknowledgment
