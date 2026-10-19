"""Lightweight context continuity and clue evolution tracking for Phase 05.

Maintains durable research summaries, source ledger, and an append-only clue
stream where current active clues are derived deterministically.
Respects multi-perspective lore: folklore, ancient chronicles, weapon inscriptions,
and scholarly treatises co-exist without forced invalidation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from typing import Any, Iterable, Mapping, Sequence

from genshin_corpus.canonical.fingerprints import sha256_json


CLUE_STATE_SCHEMA_VERSION = "phase05-clue-state-0.1"

VALID_OPERATIONS = frozenset({"add", "supplement", "revise", "contrast", "supersede", "invalidate"})
VALID_CLUE_STATUSES = frozenset({"active", "supplemented", "revised", "contrasted", "superseded", "invalidated"})
INACTIVE_CLUE_STATUSES = frozenset({"superseded", "invalidated", "revised"})

VALID_GENRES = frozenset({
    "official_duty_dialogue",       # NPC实务工作对话 (如绘真馆藏管理)
    "ancient_chronicle_forbidden",  # 馆藏禁书编年史 (如《日月前事》)
    "folklore_adapted_fiction",     # 民间故事改编小说 (如《常世国龙蛇传》)
    "weapon_lore_divine_oath",      # 武器铭文/神明第一人称死誓 (如《证誓之明瞳》)
    "scholarly_conjecture",         # 学者考据专著/民间信仰辨析 (如《珊瑚宫民间信仰初勘》)
    "community_chronology",         # 社区时间线/百科整理 (如《白夜国》)
    "character_lore",               # 官方角色定性资料 (如《珊瑚宫心海》)
})

VALID_EPISTEMIC = frozenset({
    "duty_testimony",               # 职务证言
    "ancient_official_record",      # 古代官方实录
    "folklore_oral_tradition",      # 口头传说母题
    "primary_divine_statement",     # 神明第一人称自述
    "scholarly_conjecture",         # 学术假说与推测
    "community_synthesis",          # 社区综合整理
    "official_character_definition" # 官方角色设定
})

VALID_SCOPE_LEVELS = frozenset({"slice", "full_ru", "full_section"})


class ClueStateError(ValueError):
    """Raised when clue state or transition violates contract."""


def _check_str(val: Any, name: str) -> str:
    if not isinstance(val, str) or not val.strip():
        raise ClueStateError(f"{name} must be a non-empty string")
    return val.strip()


@dataclass(frozen=True)
class SourceLedgerEntry:
    """Represents an entry in the read source ledger."""

    unit_id: str
    content_id: str
    record_title: str
    section_name: str
    scope_level: str
    source_genre: str
    read_count: int = 1
    first_step: int = 1
    last_step: int = 1

    def __post_init__(self) -> None:
        _check_str(self.unit_id, "unit_id")
        _check_str(self.content_id, "content_id")
        _check_str(self.record_title, "record_title")
        if self.scope_level not in VALID_SCOPE_LEVELS:
            raise ClueStateError(f"scope_level {self.scope_level} not in {VALID_SCOPE_LEVELS}")
        if self.source_genre not in VALID_GENRES:
            raise ClueStateError(f"source_genre {self.source_genre} not in {VALID_GENRES}")
        if self.read_count <= 0 or self.first_step <= 0 or self.last_step < self.first_step:
            raise ClueStateError("invalid step or count values in SourceLedgerEntry")

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "content_id": self.content_id,
            "record_title": self.record_title,
            "section_name": self.section_name,
            "scope_level": self.scope_level,
            "source_genre": self.source_genre,
            "read_count": self.read_count,
            "first_step": self.first_step,
            "last_step": self.last_step,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceLedgerEntry:
        return cls(
            unit_id=str(data["unit_id"]),
            content_id=str(data["content_id"]),
            record_title=str(data["record_title"]),
            section_name=str(data.get("section_name", "")),
            scope_level=str(data["scope_level"]),
            source_genre=str(data["source_genre"]),
            read_count=int(data.get("read_count", 1)),
            first_step=int(data.get("first_step", 1)),
            last_step=int(data.get("last_step", 1)),
        )


@dataclass(frozen=True)
class ClueEntry:
    """Represents an immutable clue assertion with source evidence and epistemic nuance."""

    clue_id: str
    text: str
    source_unit_ids: tuple[str, ...]
    source_genre: str
    epistemic_status: str
    narrative_significance: str
    uncertainty_note: str | None = None
    status: str = "active"

    def __post_init__(self) -> None:
        _check_str(self.clue_id, "clue_id")
        _check_str(self.text, "text")
        if not self.source_unit_ids or any(not isinstance(u, str) or not u for u in self.source_unit_ids):
            raise ClueStateError("source_unit_ids must contain non-empty string IDs")
        if len(self.source_unit_ids) != len(set(self.source_unit_ids)):
            raise ClueStateError("source_unit_ids must be unique")
        if self.source_genre not in VALID_GENRES:
            raise ClueStateError(f"source_genre {self.source_genre} not in {VALID_GENRES}")
        if self.epistemic_status not in VALID_EPISTEMIC:
            raise ClueStateError(f"epistemic_status {self.epistemic_status} not in {VALID_EPISTEMIC}")
        _check_str(self.narrative_significance, "narrative_significance")
        if self.status not in VALID_CLUE_STATUSES:
            raise ClueStateError(f"status {self.status} not in {VALID_CLUE_STATUSES}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "clue_id": self.clue_id,
            "text": self.text,
            "source_unit_ids": list(self.source_unit_ids),
            "source_genre": self.source_genre,
            "epistemic_status": self.epistemic_status,
            "narrative_significance": self.narrative_significance,
            "uncertainty_note": self.uncertainty_note,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ClueEntry:
        return cls(
            clue_id=str(data["clue_id"]),
            text=str(data["text"]),
            source_unit_ids=tuple(data["source_unit_ids"]),
            source_genre=str(data["source_genre"]),
            epistemic_status=str(data["epistemic_status"]),
            narrative_significance=str(data["narrative_significance"]),
            uncertainty_note=data.get("uncertainty_note"),
            status=str(data.get("status", "active")),
        )


@dataclass(frozen=True)
class ClueEvent:
    """An append-only modification or introduction of clue evidence."""

    event_id: str
    step: int
    operation: str
    clue: ClueEntry
    target_clue_id: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _check_str(self.event_id, "event_id")
        if self.step <= 0:
            raise ClueStateError("step must be a positive integer")
        if self.operation not in VALID_OPERATIONS:
            raise ClueStateError(f"operation {self.operation} not in {VALID_OPERATIONS}")
        if self.operation in {"supplement", "revise", "contrast", "supersede", "invalidate"}:
            if not self.target_clue_id:
                raise ClueStateError(f"operation {self.operation} requires target_clue_id")
            if not self.reason:
                raise ClueStateError(f"operation {self.operation} requires a reason")
        if self.operation == "add" and self.target_clue_id is not None:
            raise ClueStateError("operation add must not specify target_clue_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "step": self.step,
            "operation": self.operation,
            "clue": self.clue.to_dict(),
            "target_clue_id": self.target_clue_id,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ClueEvent:
        return cls(
            event_id=str(data["event_id"]),
            step=int(data["step"]),
            operation=str(data["operation"]),
            clue=ClueEntry.from_dict(data["clue"]),
            target_clue_id=data.get("target_clue_id"),
            reason=data.get("reason"),
        )


@dataclass(frozen=True)
class ContextContinuityState:
    """Represents a lightweight, auditable state of understanding at a given step.

    History is immutable (append-only ledger and events).
    The current active clues are derived deterministically from the event stream.
    """

    step: int
    research_summary: str
    source_ledger: tuple[SourceLedgerEntry, ...]
    clue_events: tuple[ClueEvent, ...]

    def __post_init__(self) -> None:
        if self.step < 0:
            raise ClueStateError("step must be non-negative")
        _check_str(self.research_summary, "research_summary")

    def derive_active_clues(self) -> tuple[ClueEntry, ...]:
        """Derive currently active clues from the append-only event stream."""
        clues_by_id: dict[str, ClueEntry] = {}
        for ev in self.clue_events:
            cid = ev.clue.clue_id
            if ev.operation == "add":
                clues_by_id[cid] = replace(ev.clue, status="active")
            elif ev.operation == "supplement":
                # Supplementary evidence enriches target without removing target
                clues_by_id[cid] = replace(ev.clue, status="active")
                if ev.target_clue_id in clues_by_id:
                    target = clues_by_id[ev.target_clue_id]
                    if target.status in INACTIVE_CLUE_STATUSES:
                        raise ClueStateError(f"cannot supplement inactive clue {ev.target_clue_id} (status: {target.status})")
                    if target.status == "active":
                        clues_by_id[ev.target_clue_id] = replace(target, status="supplemented")
            elif ev.operation == "contrast":
                # Narrative perspectives contrast and co-exist; both remain active!
                clues_by_id[cid] = replace(ev.clue, status="active")
                if ev.target_clue_id in clues_by_id:
                    target = clues_by_id[ev.target_clue_id]
                    if target.status in INACTIVE_CLUE_STATUSES:
                        raise ClueStateError(f"cannot contrast against inactive clue {ev.target_clue_id} (status: {target.status})")
                    if target.status in {"active", "supplemented"}:
                        clues_by_id[ev.target_clue_id] = replace(target, status="contrasted")
            elif ev.operation == "revise":
                # Revise updates detail; target marked revised (inactive) and replaced by new clue
                clues_by_id[cid] = replace(ev.clue, status="active")
                if ev.target_clue_id in clues_by_id:
                    target = clues_by_id[ev.target_clue_id]
                    if target.status in INACTIVE_CLUE_STATUSES:
                        raise ClueStateError(f"cannot revise inactive clue {ev.target_clue_id} (status: {target.status})")
                    clues_by_id[ev.target_clue_id] = replace(target, status="revised")
            elif ev.operation == "supersede":
                # Explicit supersession makes target inactive
                clues_by_id[cid] = replace(ev.clue, status="active")
                if ev.target_clue_id in clues_by_id:
                    target = clues_by_id[ev.target_clue_id]
                    if target.status in INACTIVE_CLUE_STATUSES:
                        raise ClueStateError(f"cannot supersede already inactive clue {ev.target_clue_id} (status: {target.status})")
                    clues_by_id[ev.target_clue_id] = replace(target, status="superseded")
            elif ev.operation == "invalidate":
                # Explicit invalidation makes both event clue and target inactive
                clues_by_id[cid] = replace(ev.clue, status="invalidated")
                if ev.target_clue_id in clues_by_id:
                    target = clues_by_id[ev.target_clue_id]
                    clues_by_id[ev.target_clue_id] = replace(target, status="invalidated")

        # Active clues: status is 'active', 'supplemented', or 'contrasted'
        # 'superseded', 'invalidated', and 'revised' are excluded from the active set
        active = [c for c in clues_by_id.values() if c.status not in INACTIVE_CLUE_STATUSES]
        active.sort(key=lambda c: c.clue_id)
        return tuple(active)

    def validate_references(self, known_unit_ids: Iterable[str] | None = None) -> None:
        """Validate integrity of all clue events and ledger entries."""
        known_units = set(known_unit_ids) if known_unit_ids is not None else None
        seen_clue_ids: set[str] = set()
        seen_event_ids: set[str] = set()

        for ev in self.clue_events:
            if ev.event_id in seen_event_ids:
                raise ClueStateError(f"duplicate event_id {ev.event_id}")
            seen_event_ids.add(ev.event_id)

            cid = ev.clue.clue_id
            if cid in seen_clue_ids:
                raise ClueStateError(f"duplicate clue_id {cid}")
            seen_clue_ids.add(cid)

            if ev.target_clue_id is not None:
                if ev.target_clue_id == cid:
                    raise ClueStateError(f"clue {cid} cannot target itself")
                if ev.target_clue_id not in seen_clue_ids:
                    raise ClueStateError(f"target_clue_id {ev.target_clue_id} not previously introduced")

            if known_units is not None:
                for uid in ev.clue.source_unit_ids:
                    if uid not in known_units:
                        raise ClueStateError(f"unknown unit_id {uid} in clue {cid}")

        if known_units is not None:
            seen_ledger_units: set[str] = set()
            for entry in self.source_ledger:
                if entry.unit_id in seen_ledger_units:
                    raise ClueStateError(f"duplicate unit_id {entry.unit_id} in source ledger")
                seen_ledger_units.add(entry.unit_id)
                if entry.unit_id not in known_units:
                    raise ClueStateError(f"unknown unit_id {entry.unit_id} in source ledger")

        # Verify lifecycle state transitions succeed without errors
        self.derive_active_clues()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CLUE_STATE_SCHEMA_VERSION,
            "step": self.step,
            "research_summary": self.research_summary,
            "source_ledger": [entry.to_dict() for entry in self.source_ledger],
            "clue_events": [ev.to_dict() for ev in self.clue_events],
            "active_clues": [c.to_dict() for c in self.derive_active_clues()],
            "state_fingerprint": sha256_json({
                "step": self.step,
                "research_summary": self.research_summary,
                "source_ledger": [entry.to_dict() for entry in self.source_ledger],
                "clue_events": [ev.to_dict() for ev in self.clue_events],
            }),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ContextContinuityState:
        return cls(
            step=int(data["step"]),
            research_summary=str(data["research_summary"]),
            source_ledger=tuple(SourceLedgerEntry.from_dict(d) for d in data.get("source_ledger", ())),
            clue_events=tuple(ClueEvent.from_dict(d) for d in data.get("clue_events", ())),
        )
