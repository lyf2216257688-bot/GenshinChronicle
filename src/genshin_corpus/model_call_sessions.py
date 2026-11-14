"""Session/turn budget bridge to model_calls; G1 deliberately permits Mock only."""
from dataclasses import asdict
from pathlib import Path
import time

from . import model_calls as calls
from .evidence_io import digest, read, require, seal, sha, verify, write
from .rag.clean_store import put
from .rag import clean_store
from .retrieval.local_qwen3_build import offline_only

VERSION = "gc-model-call-session-1"


class SessionCalls:
    def __init__(self, store, campaign, limits, *, policy=None):
        require(isinstance(campaign, str) and bool(campaign.strip()), "Campaign identity required")
        self.store = store
        self.root = store.root / "campaigns" / digest(campaign)
        self.campaign = campaign
        require(set(limits) == {"campaign_requests", "campaign_tokens", "turn_requests", "turn_tokens"},
                "Explicit finite campaign/turn limits required")
        require(all(type(v) is int and v > 0 for v in limits.values()), "Finite positive limits required")
        self.policy = policy or calls.Policy(max_attempts=1, unknown_retries=0, format_retries=0)
        with calls.locked(self.root / "campaign.lock"):
            put(self.root / "scope.json", {"version": VERSION, "campaign": campaign,
                "data_root": str(store.root), "limits": limits, "policy": asdict(self.policy)})
        self.limits = limits
        self.batch = calls.Batch(self.root / "batch", max_requests=limits["campaign_requests"],
                                 max_tokens=limits["campaign_tokens"])

    def prepare(self, tid, key, body, *, allowance, channel="MOCK", schema="fixture-1"):
        require(isinstance(key, str) and bool(key.strip()), "Logical operation key required")
        turn = self.store.turn(tid)
        require(turn["campaign"] == self.campaign, "Turn cannot change campaign/budget root")
        require(type(allowance) is int and allowance > 0, "Positive allowance required")
        opid = digest([VERSION, turn["session"], tid, key])
        value = {"version": VERSION, "session_id": turn["session"], "turn_id": tid,
                 "operation_id": opid, "campaign_root": str(self.root), "body": body,
                 "body_fingerprint": digest(body), "allowance": allowance,
                 "channel": channel, "schema": schema, "policy": asdict(self.policy),
                 "shared_code": calls.code_identity(), "bridge_code": sha(__file__),
                 "state_code": sha(clean_store.__file__)}
        value["fingerprint"] = digest(value)
        with calls.locked(self.root / "locks" / opid):
            put(self.root / "operations" / opid / "prepared.json", value)
            put(self.root / "mappings" / (value["fingerprint"] + ".json"), value)
        return opid

    def operation(self, opid):
        require(isinstance(opid, str) and len(opid) == 64 and set(opid) <= set("0123456789abcdef"), "Invalid operation")
        plan = read(self.root / "operations" / opid / "prepared.json")
        require(plan["campaign_root"] == str(self.root) and plan["policy"] == asdict(self.policy)
                and plan["shared_code"] == calls.code_identity() and plan["bridge_code"] == sha(__file__)
                and plan["state_code"] == sha(clean_store.__file__),
                "Incompatible operation: read-only display, no dispatch")
        require(plan["operation_id"] == opid and plan["body_fingerprint"] == digest(plan["body"])
                and plan["fingerprint"] == digest({k: v for k, v in plan.items() if k != "fingerprint"}),
                "Prepared operation changed")
        require(self.store.turn(plan["turn_id"])["campaign"] == self.campaign, "Foreign turn/campaign")
        require(read(self.root / "mappings" / (plan["fingerprint"] + ".json")) == plan, "Operation mapping changed")
        return plan

    def reserve(self, fingerprint, attempt, allowance):
        # run owns fingerprint lock. Mapping precedes ANY Batch reservation.
        plan = read(self.root / "mappings" / (fingerprint + ".json"))
        self.operation(plan["operation_id"])
        require(allowance == plan["allowance"], "Allowance changed")
        with calls.locked(self.root / "campaign.lock"):
            with calls.locked(self.store.root / "locks/admission.lock"):
                if self.store.cancelled(plan["turn_id"]):
                    raise calls.Deferred("CANCELLED: no new dispatch admission")
                rows = []
                for p in (self.batch.root / "requests").glob("*/reservation.json"):
                    reservation = read(p)
                    mapping = read(self.root / "mappings" / (reservation["fingerprint"] + ".json"))
                    if mapping["turn_id"] == plan["turn_id"]:
                        outcome = self.batch._outcome(p)
                        tokens = outcome.get("accounted_tokens", outcome["observed_tokens"])
                        rows.append(reservation["token_allowance"] if tokens is None else tokens)
                if len(rows) >= self.limits["turn_requests"] or sum(rows) + allowance > self.limits["turn_tokens"]:
                    raise calls.Deferred("TURN_BUDGET_DEFERRED")
                slot = self.batch.reserve(fingerprint, attempt, allowance)
                self.store.event(plan["turn_id"], "admission-" + slot.name, {
                    "kind": "dispatch_admitted", "operation_id": plan["operation_id"],
                    "fingerprint": fingerprint, "batch_slot": str(slot)})
                return slot

    def finish(self, slot, result, attempt):
        expected = {"observed_tokens": calls.token_count(result),
            "attempt_complete_sha256": sha(Path(attempt) / "complete.json"),
            "request_observations": None if result.get("request_count") == "UNKNOWN" else result.get("calls"),
            "external_invocations": result.get("external_invocations", 0),
            "actual_fee": result.get("actual_fee", "UNKNOWN")}
        put(Path(slot) / "outcome.json", expected)

    def reconcile(self, opid):
        """Repair derived receipts only; pending_retry never performs network work."""
        plan = self.operation(opid)
        root = self.root / "calls" / opid
        with calls.locked(self.root / "ledger/.locks" / plan["fingerprint"]):
            if (root / "call.json").exists():
                identity = read(root / "call.json")
                require(identity["plan"] == plan and identity["shared_code"] == calls.code_identity()
                        and identity["policy"] == asdict(self.policy), "Call identity changed")
                # Crash between the two create-only writes: the prepared FILE
                # and call record already bind these exact plan bytes/values.
                if not (root / "plan.json").exists() and not (root / "complete.json").exists():
                    put(root / "plan.json", plan)
            for dispatch in (root / "dispatches").glob("*.json"):
                value = read(dispatch)
                slot = Path(value["batch_slot"])
                require(slot.parent == (self.batch.root / "requests").resolve(), "Foreign Batch slot")
                reservation = read(slot / "reservation.json")
                require(reservation["fingerprint"] == plan["fingerprint"]
                        and Path(reservation["attempt"]) == (root / "attempts" / dispatch.stem).resolve(),
                        "Dispatch reservation missing/changed")
            for p in (self.batch.root / "requests").glob("*/reservation.json"):
                r = read(p)
                if r["fingerprint"] != plan["fingerprint"]:
                    continue
                attempt = Path(r["attempt"])
                require(attempt.parent == (root / "attempts").resolve(), "Foreign reservation attempt")
                if (attempt / "complete.json").exists():
                    verify(attempt); self.finish(p.parent, read(attempt / "result.json"), attempt)
                    self.batch._outcome_cache.pop(p, None)
                    self.batch._outcome(p)
            if (root / "complete.json").exists():
                verify(root)
                require(read(root / "plan.json") == plan, "Sealed call plan changed")
                claim = self.root / "ledger" / plan["fingerprint"]
                reservation = read(claim / "reservation.json")
                require(reservation["root"] == str(root.resolve()), "Call ledger root changed")
                put(claim / "outcome.json", {"root_complete_sha256": sha(root / "complete.json"),
                                           "state": read(root / "result.json")["state"]})
                return {"status": "sealed", "result": read(root / "result.json")}
            attempts = sorted((root / "attempts").glob("*"))
            reserved = [p for p in (self.batch.root / "requests").glob("*/reservation.json")
                        if read(p)["fingerprint"] == plan["fingerprint"]]
            if any(not (Path(read(p)["attempt"]) / "complete.json").exists() for p in reserved):
                return {"status": "UNKNOWN"}
            if attempts:
                last = attempts[-1]; result = read(last / "result.json")
                trace = read(last / "trace.json").get("events", []) if (last / "trace.json").exists() else []
                cs = [calls.classify(read(p / "result.json"), read(p / "trace.json").get("events", [])
                      if (p / "trace.json").exists() else []) for p in attempts]
                decision = calls.retry_decision(calls.classify(result, trace), self.policy, len(attempts),
                    sum(c["execution"] == "UNKNOWN" for c in cs[:-1]),
                    sum(c["kind"] == "NON_JSON" for c in cs[:-1]), False)
                return {"status": "pending_retry" if decision["retry"] else "pending_finalize"}
            return {"status": "prepared"}

    def execute_mock(self, opid, send_once):
        plan = self.operation(opid)
        require(plan["channel"] == "MOCK", "G1 bridge has no live sender; G3-Live authorization required")
        root = self.root / "calls" / opid
        self.reconcile(opid)

        def send(attempt, headers):
            # Cancellation after reserve is in-flight. Do not release uncertain budget.
            with calls.locked(self.store.root / "locks/admission.lock"):
                cancelled = self.store.cancelled(plan["turn_id"])
            if cancelled:
                result = {"state": "CANCELLED_BEFORE_SEND", "calls": 0, "request_count": 0,
                          "usage": "UNKNOWN", "content_validation": {"status": "FAIL"},
                          "error_codes": ["CANCELLED_BEFORE_SEND"]}
                write(attempt / "result.json", result); seal(attempt)
                return result
            return send_once(attempt, headers)

        def sleep(seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                if self.store.cancelled(plan["turn_id"]):
                    raise calls.Deferred("CANCELLED during retry wait")
                time.sleep(min(.05, max(0, end - time.monotonic())))

        # Batch-compatible root is intentionally the original fixed Batch root.
        with offline_only():
            result = calls.run(root, plan, self.root / "ledger", send, channel="MOCK",
                               policy=self.policy, batch=TurnBatch(self), token_allowance=plan["allowance"], sleep=sleep)
        self.reconcile(opid)
        return result

class TurnBatch:
    """Expose only the Batch surface consumed by model_calls.run."""
    def __init__(self, bridge):
        self.bridge, self.root = bridge, bridge.batch.root

    def reserve(self, *args):
        return self.bridge.reserve(*args)

    def finish(self, *args):
        return self.bridge.finish(*args)
