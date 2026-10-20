"""Provider-free candidate V session: scheduling, receipts and strict continuity.

Opt-in research only. No transport, active semantic view, Generation integration,
or production selector. Checkpoints are immutable and contain delivered evidence.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.retrieval.clue_state import (
    ClueEntry, ClueEvent, ClueStateError, ContextContinuityState, SourceLedgerEntry,
)
from genshin_corpus.retrieval.navi_exploration import ExplorationError, response_chars
from genshin_corpus.retrieval.navi_reading import HYPOTHESIS, SCHEMA, text

LANES = ("baseline", "related", "independent")


def _validate_catalog(sources, catalog):
    if (catalog.get("schema_version") != SCHEMA
            or catalog.get("catalog_identity") != sha256_json({k: v for k, v in catalog.items()
                                                            if k != "catalog_identity"})
            or catalog.get("source") != sources.tools.canonical["source"]
            or catalog.get("locale") != sources.tools.canonical["locale"]):
        raise ExplorationError("catalog identity mismatch")


@dataclass(frozen=True)
class ReadingBudget:
    max_actions: int = 32
    max_searches: int = 4
    max_pages: int = 8
    max_new_body_chars: int = 30000
    max_batch_body_chars: int = 8000
    max_tool_response_chars: int = 300000
    max_summary_chars: int = 6000

    def __post_init__(self):
        for value in asdict(self).values():
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ExplorationError("reading budgets must be positive integers")


def _once(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(canonical_json_bytes(value))


def _strict_updates(previous, updates, summary, step, known):
    """No invalid-op coercion, guessed citations, or unknown-target fallback."""
    text(summary, "research summary")
    if not isinstance(updates, list):
        raise ExplorationError("updates must be a list")
    events = list(previous.clue_events)
    for index, update in enumerate(updates):
        if not isinstance(update, dict) or set(update) - {"operation", "clue", "target_clue_id", "reason"}:
            raise ExplorationError("invalid update fields")
        if not isinstance(update.get("operation"), str):
            raise ExplorationError("invalid operation type")
        for field in ("target_clue_id", "reason"):
            if update.get(field) is not None:
                text(update[field], field)
        value = update["clue"]
        if not isinstance(value, dict) or not isinstance(value.get("source_unit_ids"), list):
            raise ExplorationError("clue requires explicit source_unit_ids")
        if not value["source_unit_ids"] or any(not isinstance(uid, str) for uid in value["source_unit_ids"]):
            raise ExplorationError("invalid clue source IDs")
        if value.get("status", "active") != "active":
            raise ExplorationError("new clue must have active working status")
        if value.get("uncertainty_note") is not None:
            text(value["uncertainty_note"], "uncertainty note")
        clue = ClueEntry(**{**value, "source_unit_ids": tuple(value["source_unit_ids"])})
        if not set(clue.source_unit_ids) <= known:
            raise ExplorationError("clue references unread or unresolved official RU")
        events.append(ClueEvent(
            event_id=f"v:{step}:{index}", step=step, operation=update["operation"], clue=clue,
            target_clue_id=update.get("target_clue_id"), reason=update.get("reason"),
        ))
    state = replace(previous, step=step, research_summary=summary, clue_events=tuple(events))
    state.validate_references(known)
    return state


class ReadingSession:
    """Single append-only understanding shared by all local research topics."""

    @classmethod
    def create(cls, sources, catalog, output_root: Path, *, baseline_sources: Sequence[str],
               budget: ReadingBudget | None = None,
               decision_mode: str = "injected_recorded_or_fixture_decisions_only"):
        budget = budget or ReadingBudget()
        if not isinstance(decision_mode, str) or not decision_mode.strip():
            raise ExplorationError("explicit non-empty decision_mode required")
        decision_mode = decision_mode.strip()
        _validate_catalog(sources, catalog)
        if len(baseline_sources) != len(set(baseline_sources)):
            raise ExplorationError("duplicate baseline source")
        queue = [r for r in catalog["queue"] if r["source_identity_key"] in baseline_sources]
        if len(queue) != len(baseline_sources):
            raise ExplorationError("baseline source absent from ordered version catalog")
        if any(r["source_identity_key"] not in sources.by_source for r in queue):
            raise ExplorationError("baseline source absent from Canonical snapshot")
        root = Path(output_root)
        root.mkdir(parents=True, exist_ok=False)
        manifest = {"schema_version": SCHEMA, "sources_identity": sources.identity,
                    "catalog_identity": catalog["catalog_identity"], "budget": asdict(budget),
                    "baseline_sources": [r["source_identity_key"] for r in queue],
                    "decision_mode": decision_mode,
                    "interpretation_status": HYPOTHESIS, "semantic_acceptance_status": "not_accepted"}
        manifest["run_identity"] = sha256_json(manifest)
        _once(root / "manifest.json", manifest)
        self = cls(sources, catalog, root, manifest)
        state = {
            "run_identity": manifest["run_identity"], "status": "in_progress", "next_lane": 0,
            "used": {"actions": 0, "searches": 0, "new_body_chars": 0, "reread_body_chars": 0,
                     "tool_response_chars": 0, "reuse_occurrences": 0},
            "queues": {lane: [] for lane in LANES}, "topics": {}, "plans": {}, "coverage": {},
            "pages": [], "receipts": [], "searches": [], "candidates": {}, "rejections": [],
            "pending_questions": [], "dispositions": [],
            "continuity": ContextContinuityState(0, "Research has not started.", (), ()).to_dict(),
        }
        for row in queue:
            key = row["source_identity_key"]
            topic = "topic:" + sha256_json({"source": key, "kind": "version_baseline"})[:20]
            state["topics"][topic] = {"question": row["title"], "origin": {"kind": "version_baseline", "source": key},
                                     "interpretation_status": HYPOTHESIS}
            request = self._request(key, None, topic, "version_baseline", None)
            state["queues"]["baseline"].append(request)
        self._commit(state, "initialize", {"scope": "explicit bounded subset; remaining catalog entries are not marked read"}, charge=False)
        return self

    def __init__(self, sources, catalog, root, manifest):
        self.sources, self.catalog, self.root, self.manifest = sources, catalog, Path(root), manifest
        self.budget = ReadingBudget(**manifest["budget"])
        self.sequence = -1
        self.previous_hash = None
        self.state = {}

    @classmethod
    def resume(cls, sources, catalog, output_root: Path):
        _validate_catalog(sources, catalog)
        root = Path(output_root)
        manifest = json.loads((root / "manifest.json").read_bytes())
        identity_fields = {k: v for k, v in manifest.items() if k != "run_identity"}
        if (manifest["run_identity"] != sha256_json(identity_fields) or manifest["schema_version"] != SCHEMA
                or manifest["sources_identity"] != sources.identity
                or manifest["catalog_identity"] != catalog["catalog_identity"]):
            raise ExplorationError("resume input or manifest identity mismatch")
        self = cls(sources, catalog, root, manifest)
        files = sorted((root / "checkpoints").glob("*.json"))
        if not files:
            raise ExplorationError("missing session checkpoints")
        for index, path in enumerate(files):
            if path.name != f"{index:06d}.json":
                raise ExplorationError("non-contiguous checkpoints")
            payload = json.loads(path.read_bytes())
            digest = payload.pop("checkpoint_identity")
            if sha256_json(payload) != digest or payload["previous_checkpoint"] != self.previous_hash:
                raise ExplorationError("checkpoint tamper or chain mismatch")
            if payload["sequence"] != index or payload["run_identity"] != manifest["run_identity"]:
                raise ExplorationError("checkpoint run mismatch")
            self.sequence, self.previous_hash, self.state = index, digest, payload["state"]
        # Reading a source after resumption must still bind to identical artifacts.
        sources.tools.verify_snapshot()
        for key in self.state["pages"]:
            sources.record(key)
        continuity = ContextContinuityState.from_dict(self.state["continuity"])
        continuity.validate_references(self._read_ids(self.state))
        return self

    def _commit(self, state, kind, details, *, charge=True):
        if charge:
            if self.state["used"]["actions"] >= self.budget.max_actions:
                raise ExplorationError("action budget exhausted; pending state preserved")
            state["used"]["actions"] += 1
            if state["used"]["actions"] == self.budget.max_actions and state["status"] == "in_progress":
                state["status"] = "budget_exhausted"
        payload = {"schema_version": SCHEMA, "run_identity": self.manifest["run_identity"],
                   "sequence": self.sequence + 1, "previous_checkpoint": self.previous_hash,
                   "event": {"kind": kind, "details": details}, "state": state}
        digest = sha256_json(payload)
        _once(self.root / "checkpoints" / f"{self.sequence + 1:06d}.json",
              {**payload, "checkpoint_identity": digest})
        self.sequence += 1
        self.previous_hash, self.state = digest, state
        return deepcopy(details)

    def _state(self):
        if self.state["status"] != "in_progress":
            raise ExplorationError("session stopped; pending work is preserved")
        if self.state["used"]["actions"] >= self.budget.max_actions:
            raise ExplorationError("action budget exhausted; pending work is preserved")
        return deepcopy(self.state)

    @staticmethod
    def _request(source, anchor, topic, origin, reread_reason):
        value = {"source": source, "anchor_section": anchor, "topic_id": topic, "origin": origin,
                 "reread_reason": reread_reason, "completed_batches": []}
        value["request_id"] = sha256_json({k: v for k, v in value.items() if k != "completed_batches"})
        return value

    @staticmethod
    def _read_ids(state):
        return {uid for item in state["coverage"].values() if item["read_status"] == "complete"
                for uid in item["unit_ids"]}

    def search(self, query: str, *, topic_id: str, top_k: int = 5):
        state = self._state()
        if topic_id not in state["topics"]:
            raise ExplorationError("unknown search topic")
        if state["used"]["searches"] >= self.budget.max_searches:
            raise ExplorationError("search budget exhausted")
        result = self.sources.search(query, top_k=top_k)
        charge = response_chars(result)
        if state["used"]["tool_response_chars"] + charge > self.budget.max_tool_response_chars:
            raise ExplorationError("search response budget exhausted")
        search_id = "search:" + str(state["used"]["searches"])
        state["searches"].append({"search_id": search_id, "topic_id": topic_id, "result": result})
        for hit in result["hits"]:
            candidate = state["candidates"].setdefault(hit["unit_id"], {
                "unit_id": hit["unit_id"], "canonical_address": hit["canonical_address"],
                "status": "discovered_unread", "origins": [],
            })
            candidate["origins"].append({"kind": "search_hit", "search_id": search_id,
                                         "query": query, "topic_id": topic_id})
            if hit["unit_id"] in self._read_ids(state):
                candidate["status"] = "delivered"
        state["used"]["searches"] += 1
        state["used"]["tool_response_chars"] += charge
        return self._commit(state, "search", {"search_id": search_id, "result": result})

    def enqueue(self, source: str, *, lane: str, origin: Mapping[str, Any],
                topic_id: str | None = None, question: str | None = None,
                anchor_section: int | None = None, reread_reason: str | None = None):
        state = self._state()
        if lane not in ("related", "independent") or source not in self.sources.by_source:
            raise ExplorationError("invalid frontier lane or source")
        if anchor_section is not None and (not isinstance(anchor_section, int)
                                           or isinstance(anchor_section, bool) or anchor_section < 0):
            raise ExplorationError("invalid anchor section")
        kind = origin.get("kind")
        if kind == "search_hit":
            candidate = state["candidates"].get(origin.get("unit_id"))
            if candidate is None or candidate["canonical_address"]["source_identity_key"] != source:
                raise ExplorationError("frontier source is not a recorded search hit")
            if anchor_section != candidate["canonical_address"]["section_ordinal"]:
                raise ExplorationError("frontier anchor differs from recorded hit")
            candidate["status"] = "queued"
        elif kind == "source_link":
            parent = state["coverage"].get(origin.get("coverage_key"))
            if parent is None or parent["read_status"] != "complete":
                raise ExplorationError("link parent has not been read")
            target = source.rsplit(":", 1)[1]
            if target not in parent.get("linked_content_ids", []):
                raise ExplorationError("link is not present in delivered source")
        elif kind == "independent_seed":
            if lane != "independent":
                raise ExplorationError("independent seed requires independent lane")
            text(origin.get("reason"), "independent seed rationale")
        elif kind == "read_source":
            parent = state["coverage"].get(origin.get("coverage_key"))
            if parent is None or parent["read_status"] != "complete" or parent["source"] != source:
                raise ExplorationError("topic source has not been read")
        else:
            raise ExplorationError("unknown discovery origin")
        if reread_reason is not None:
            text(reread_reason, "reread reason")
        if topic_id is None:
            text(question, "new topic question")
            topic_id = "topic:" + sha256_json({"source": source, "question": question, "origin": origin})[:20]
            state["topics"].setdefault(topic_id, {"question": question, "origin": dict(origin),
                                                  "interpretation_status": HYPOTHESIS})
        elif topic_id not in state["topics"]:
            raise ExplorationError("unknown topic")
        request = self._request(source, anchor_section, topic_id, dict(origin), reread_reason)
        queued = any(request["request_id"] == r["request_id"] for q in state["queues"].values() for r in q)
        if not queued:
            state["queues"][lane].append(request)
        return self._commit(state, "enqueue", {"request": request, "lane": lane, "already_queued": queued})

    def _inventory(self, state, plan, request):
        plan_key = sha256_json({"source": request["source"], "anchor": request["anchor_section"]})
        metadata = {k: v for k, v in plan.items() if k != "batches"}
        metadata["batch_ids"] = [b["batch_id"] for b in plan["batches"]]
        if plan_key in state["plans"] and state["plans"][plan_key] != metadata:
            raise ExplorationError("source reading plan changed")
        state["plans"][plan_key] = metadata
        for batch in plan["batches"]:
            for occurrence in batch["units"]:
                item = state["coverage"].setdefault(occurrence["coverage_key"], {
                    "source": request["source"], "record_id": occurrence["record_id"],
                    "group_id": batch["group_id"], "unit_ids": occurrence["unit_ids"],
                    "section_ordinal": occurrence["section_ordinal"],
                    "canonical_unit_ordinal": occurrence["canonical_unit_ordinal"],
                    "read_status": "unread", "research_status": "pending_reading",
                    "body_chars": len(occurrence["text"]), "text_sha256": occurrence["text_sha256"],
                    "topics": [], "read_count": 0, "receipt_ids": [],
                    "linked_content_ids": sorted({str(r["content_id"]) for r in occurrence["entry_references"]
                                                   if r.get("content_id")}),
                })
                if request["topic_id"] not in item["topics"]:
                    item["topics"].append(request["topic_id"])
        return plan_key

    def read_next(self):
        state = self._state()
        lanes = [LANES[(state["next_lane"] + i) % len(LANES)] for i in range(len(LANES))]
        lane = next((name for name in lanes if state["queues"][name]), None)
        if lane is None:
            return self._commit(state, "idle", {"reason": "queues_empty; discoveries and unresolved questions remain visible"})
        request = state["queues"][lane][0]
        if request["source"] not in state["pages"] and len(state["pages"]) >= self.budget.max_pages:
            state["status"] = "budget_exhausted"
            return self._commit(state, "budget_stop", {"reason": "page_budget", "request": request})
        # Inventory every eligible group of an encountered page, even when the
        # discovery selected just one group. Inventory is not body delivery.
        full_plan = self.sources.plan(request["source"], max_batch_chars=self.budget.max_batch_body_chars)
        plan_key = self._inventory(state, full_plan, {**request, "anchor_section": None})
        plan = (full_plan if request["anchor_section"] is None else
                self.sources.plan(request["source"], max_batch_chars=self.budget.max_batch_body_chars,
                                  anchor_section=request["anchor_section"]))
        batch = next((b for b in plan["batches"] if b["batch_id"] not in request["completed_batches"]), None)
        if batch is None:
            state["queues"][lane].pop(0)
            state["next_lane"] = (LANES.index(lane) + 1) % len(LANES)
            reason = "no_readable_body_or_deferred" if not plan["batches"] else "request_complete"
            state["dispositions"].append({"request": request, "reason": reason, "plan_key": plan_key})
            return self._commit(state, "request_disposition", state["dispositions"][-1])
        remaining = self.budget.max_new_body_chars - state["used"]["new_body_chars"]
        delivered, reused, omitted = [], [], []
        for occurrence in batch["units"]:
            item = state["coverage"][occurrence["coverage_key"]]
            if item["read_status"] == "complete" and not request["reread_reason"]:
                reused.append(occurrence["coverage_key"])
                continue
            size = len(occurrence["text"])
            cost = 0 if item["read_status"] == "complete" else size
            if cost > remaining or omitted:
                omitted.append(occurrence["coverage_key"])
            else:
                remaining -= cost
                delivered.append(occurrence)
        if not delivered and omitted:
            state["status"] = "budget_exhausted"
            return self._commit(state, "budget_stop", {"reason": "new_body_budget", "pending_occurrences": omitted})
        receipt = {
            "receipt_id": f"receipt:{state['used']['actions'] + 1}",
            "batch_id": batch["batch_id"], "lane": lane, "topic_id": request["topic_id"],
            "source": request["source"], "record_title": batch["record_title"],
            "units": delivered, "reused_coverage_keys": reused, "omitted_coverage_keys": omitted,
            "group_context": batch["group_context"],
            "partial_batch": bool(omitted), "reread_reason": request["reread_reason"],
            "source_binding_status": "verified", "semantic_acceptance_status": "not_accepted",
            "research_status": "pending_understanding",
        }
        charge = response_chars(receipt)
        if state["used"]["tool_response_chars"] + charge > self.budget.max_tool_response_chars:
            state["status"] = "budget_exhausted"
            return self._commit(state, "budget_stop", {"reason": "tool_response_budget", "required_chars": charge})
        previous = ContextContinuityState.from_dict(state["continuity"])
        ledger = {e.unit_id: e for e in previous.source_ledger}
        step = state["used"]["actions"] + 1
        for occurrence in delivered:
            item = state["coverage"][occurrence["coverage_key"]]
            counter = "reread_body_chars" if item["read_status"] == "complete" else "new_body_chars"
            state["used"][counter] += len(occurrence["text"])
            item.update(read_status="complete", research_status="pending_understanding")
            item["read_count"] += 1
            item["receipt_ids"].append(receipt["receipt_id"])
            for uid in occurrence["unit_ids"]:
                if uid in ledger:
                    ledger[uid] = replace(ledger[uid], read_count=ledger[uid].read_count + 1, last_step=step)
                else:
                    ledger[uid] = SourceLedgerEntry(uid, request["source"].rsplit(":", 1)[1],
                                                    batch["record_title"], occurrence["section_name"],
                                                    "full_ru", "unclassified", first_step=step, last_step=step)
                if uid in state["candidates"]:
                    state["candidates"][uid]["status"] = "delivered"
        state["continuity"] = replace(previous, step=step, source_ledger=tuple(ledger.values())).to_dict()
        if delivered and request["source"] not in state["pages"]:
            state["pages"].append(request["source"])
        state["used"]["reuse_occurrences"] += len(reused)
        state["used"]["tool_response_chars"] += charge
        state["receipts"].append(receipt)
        if not omitted:
            request["completed_batches"].append(batch["batch_id"])
            if all(b["batch_id"] in request["completed_batches"] for b in plan["batches"]):
                state["queues"][lane].pop(0)
        state["next_lane"] = (LANES.index(lane) + 1) % len(LANES)
        # The checkpoint is the delivery commit. Understanding is a later action.
        return self._commit(state, "read", receipt)

    def understand(self, *, updates: list[dict], summary: str, receipt_ids: list[str],
                   unresolved_questions: list[str] | None = None):
        state = self._state()
        try:
            if not isinstance(receipt_ids, list) or not receipt_ids or len(set(receipt_ids)) != len(receipt_ids):
                raise ExplorationError("explicit unique reading receipt IDs required")
            receipts = [r for r in state["receipts"] if r["receipt_id"] in receipt_ids]
            if len(receipts) != len(receipt_ids) or any(r["research_status"] != "pending_understanding" for r in receipts):
                raise ExplorationError("receipt is absent or already processed")
            if len(text(summary, "summary")) > self.budget.max_summary_chars:
                raise ExplorationError("summary exceeds research operating point")
            questions = unresolved_questions or []
            if not isinstance(questions, list) or any(not isinstance(q, str) or not q.strip() for q in questions):
                raise ExplorationError("invalid unresolved questions")
            previous = ContextContinuityState.from_dict(state["continuity"])
            result = _strict_updates(previous, updates, summary, state["used"]["actions"] + 1, self._read_ids(state))
        except (ValueError, TypeError, KeyError, ClueStateError) as exc:
            rejection = {"receipt_ids": receipt_ids, "updates": updates, "summary": summary,
                         "reason": str(exc), "interpretation_status": HYPOTHESIS}
            state["rejections"].append(rejection)
            return self._commit(state, "understanding_rejected", rejection)
        state["continuity"] = result.to_dict()
        for receipt in receipts:
            receipt["research_status"] = "processed"
            keys = [o["coverage_key"] for o in receipt["units"]] + receipt["reused_coverage_keys"]
            for key in keys:
                state["coverage"][key]["research_status"] = (
                    "pending_research" if questions else "processed")
        for question in questions:
            state["pending_questions"].append({"question": question, "receipt_ids": receipt_ids,
                                               "status": "pending_research", "interpretation_status": HYPOTHESIS})
        return self._commit(state, "understand", {"receipt_ids": receipt_ids, "accepted_updates": len(updates),
                                                 "interpretation_status": HYPOTHESIS})

    def stop(self, reason: str):
        state = self._state()
        state["status"] = "stopped"
        return self._commit(state, "stop", {"reason": text(reason, "stop reason"),
                                           "remaining_work": self.coverage_summary()})

    def decision_context(self):
        """Bounded body delta + current memory; the full ledger stays in audit."""
        state = self.state
        clues = state["continuity"]["active_clues"]
        pending = [r for r in state["receipts"] if r["research_status"] == "pending_understanding"]
        latest = pending[-1:]  # Earlier pending receipts are named, never implicitly processed.
        return {
            "interpretation_status": HYPOTHESIS, "summary": state["continuity"]["research_summary"],
            "active_clues": clues[-12:], "withheld_clue_ids": [c["clue_id"] for c in clues[:-12]],
            "latest_pending_receipts": deepcopy(latest),
            "pending_receipt_ids": [r["receipt_id"] for r in pending],
            "topics": deepcopy(state["topics"]), "queues": deepcopy(state["queues"]),
            "pending_questions": deepcopy(state["pending_questions"]), "used": dict(state["used"]),
        }

    def apply_decision(self, decision: Mapping[str, Any]):
        action = decision["action"]
        arguments = {k: v for k, v in decision.items() if k != "action"}
        method = {"search": self.search, "enqueue": self.enqueue, "read": self.read_next,
                  "understand": self.understand, "stop": self.stop}.get(action)
        if method is None:
            raise ExplorationError("unknown candidate V decision action")
        return method(**arguments)

    def run(self, decision_source: Callable[[Mapping[str, Any]], Mapping[str, Any] | None]):
        while self.state["status"] == "in_progress" and self.state["used"]["actions"] < self.budget.max_actions:
            decision = decision_source(self.decision_context())
            if decision is None:
                break
            self.apply_decision(decision)
        return self.coverage_summary()

    def coverage_summary(self):
        state = self.state
        items = list(state["coverage"].values())
        groups = {}
        for item in items:
            group = groups.setdefault(item["group_id"], {"eligible": 0, "read": 0})
            group["eligible"] += 1
            group["read"] += item["read_status"] == "complete"
        deferred = {d["coverage_key"]: d for p in state["plans"].values() for d in p["deferred"]}
        for item in deferred.values():
            group = groups.setdefault(item["group_id"], {"eligible": 0, "read": 0})
            group["eligible"] += 1
        for group in groups.values():
            group["status"] = ("unread" if not group["read"] else
                               "complete_scoped_body" if group["read"] == group["eligible"] else "partial")
        return {
            "status": state["status"], "interpretation_status": HYPOTHESIS,
            "semantic_acceptance_status": "not_accepted", "used": dict(state["used"]),
            "pages_delivered": len(state["pages"]), "snapshot_records": len(self.sources.by_source),
            "snapshot_records_not_delivered": len(self.sources.by_source) - len(state["pages"]),
            "scoped_eligible_occurrences": len(items) + len(deferred),
            "scoped_read_occurrences": sum(x["read_status"] == "complete" for x in items),
            "scoped_unread_occurrences": sum(x["read_status"] == "unread" for x in items) + len(deferred),
            "unread_occurrences": {key: deepcopy(item) for key, item in state["coverage"].items()
                                   if item["read_status"] == "unread"},
            "deferred_occurrences": list(deferred.values()),
            "pending_understanding_receipts": [r["receipt_id"] for r in state["receipts"]
                                                if r["research_status"] == "pending_understanding"],
            "queues": {k: len(v) for k, v in state["queues"].items()},
            "groups": groups, "topics": len(state["topics"]),
            "unexamined_candidates": [uid for uid, c in state["candidates"].items() if c["status"] == "discovered_unread"],
            "undelivered_candidates": {uid: c["status"] for uid, c in state["candidates"].items()
                                       if c["status"] != "delivered"},
            "pending_metadata": len(self.catalog["pending_metadata"]),
            "deferred_units": len(deferred),
            "layout_issues": sum(len(p["issues"]) for p in state["plans"].values()),
            "pending_questions": len(state["pending_questions"]),
            "rejected_understanding_updates": len(state["rejections"]),
            "baseline_scope": self.manifest["baseline_sources"],
            "catalog_pages_outside_initial_baseline_scope": self.catalog["counts"]["pages"] - len(self.manifest["baseline_sources"]),
            "coverage_caveat": "counts are exact delivery/accounting within scoped bodies; not semantic completeness or corpus recall",
        }
