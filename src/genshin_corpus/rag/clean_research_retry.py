"""One explicitly authorized, origin-bound retry using the original G5 budget."""
from dataclasses import asdict
import os
from pathlib import Path
import re
import time
from urllib.request import build_opener, ProxyHandler

from .. import model_calls as calls
from ..evidence_io import digest, inventory, read, require, seal, sha, verify
from ..generation.clean_chat_transport import attempt, code_identity as adapter_code
from ..generation.generation import _RejectRedirectHandler
from ..generation.clean_evidence import validate_citations
from ..model_call_sessions import SessionCalls
from .clean_store import put

VERSION = "gc-g5-explicit-origin-retry-1"


def code_identity():
    from . import clean_research_live
    return {"retry": sha(__file__), "adapter": adapter_code(), "reconcile": sha(clean_research_live.__file__)}


def origin_bridge(store):
    from .clean_research_live import ResearchSessions
    p = read(store.root / "live/permit.json")
    return ResearchSessions(store, p["campaign"], p["settings"]["limits"], permit=p)


def prepare(store, opid, authorization, *, seconds=600):
    require(isinstance(authorization, str) and authorization.strip()
            and type(seconds) is int and 0 < seconds <= 600, "EXPLICIT_ONE_RETRY_AUTHORIZATION_REQUIRED")
    bridge = origin_bridge(store); plan = bridge.operation(opid)
    origin = bridge.root / "calls" / opid; verify(origin)
    require(read(origin / "result.json")["state"] == "UNKNOWN", "SEALED_UNKNOWN_ORIGIN_REQUIRED")
    require((store.root / "live/closed.json").is_file(), "ORIGINAL_CAMPAIGN_MUST_STAY_CLOSED")
    require(not list((bridge.root / "ledger" / plan["fingerprint"] / "recoveries").glob("*/reservation.json")),
            "RECOVERY_ALREADY_EXISTS_RESUME_IT")
    settings = bridge.permit["settings"]
    previews = [store.load(ref) for n in range(settings["limits"]["turn_requests"])
                if (ref := store.published_artifact(plan["turn_id"], "g5-request-" + str(n)))]
    envelope = next(v for v in previews if v["manifest"] == plan["body"]["manifest"])
    from .clean_conversation_graph import verify_sources
    verify_sources(store, envelope["sources_ref"])
    used = bridge.batch.accounting()["request_reservations"]
    require(used < min(10, bridge.limits["campaign_requests"]), "ORIGINAL_REQUEST_BUDGET_EXHAUSTED")
    target = bridge.root / "explicit_retry" / opid
    grant = {"version": VERSION, "operation_id": opid, "fingerprint": plan["fingerprint"],
        "authorization": authorization, "expires_at": time.time() + seconds,
        "original_root": str(origin), "original_seal": sha(origin / "complete.json"),
        "original_artifacts": inventory(origin), "budget_root": str(bridge.batch.root),
        "campaign_root": str(bridge.root), "data_root": str(store.root), "turn_id": plan["turn_id"],
        "prior_requests": used, "request_ceiling": used + 1, "new_requests": 1,
        "source_preview": envelope, "body_digest": digest(plan["body"]["manifest"]["wire"]),
        "code": code_identity(),
        "policy": asdict(calls.Policy(max_attempts=2, unknown_retries=1, format_retries=0,
            cost_risk_authorization=authorization, initial_delay=0, delay_cap=0)),
        "fee_control": "original_UNKNOWN_retained_one_additional_request_no_limit_increase"}
    put(target / "grant.json", grant)
    return grant


def reconcile_origin(bridge, opid):
    """Recognize shared origin-bound recoveries without changing G1 or old facts."""
    plan = bridge.operation(opid); origin = bridge.root / "calls" / opid
    verify(origin)
    require(read(origin / "plan.json") == plan, "RECOVERY_ORIGIN_PLAN_CHANGED")
    claim = bridge.root / "ledger" / plan["fingerprint"]
    require(read(claim / "reservation.json")["root"] == str(origin.resolve()), "RECOVERY_LEDGER_CHANGED")
    legal = {str((origin / "attempts").resolve())}
    recoveries = []
    for path in (claim / "recoveries").glob("*/reservation.json"):
        entry = read(path); root = Path(entry["root"])
        require(entry["origin_artifacts"] == inventory(origin), "RECOVERY_ORIGIN_CHANGED")
        grant = read(bridge.root / "explicit_retry" / opid / "grant.json")
        require(root == bridge.root / "recovered_calls" / opid and grant["operation_id"] == opid
                and grant["original_artifacts"] == entry["origin_artifacts"], "UNREGISTERED_G5_RECOVERY")
        if (root / "call.json").exists():
            identity = read(root / "call.json")
            require(identity["plan"] == plan and identity["policy"] == grant["policy"]
                    and identity["legacy_origin"] == str(origin.resolve()), "RECOVERY_IDENTITY_CHANGED")
        legal.add(str((root / "attempts").resolve()))
        if (root / "complete.json").exists():
            verify(root)
            require(read(root / "plan.json") == plan, "RECOVERY_PLAN_CHANGED")
            result = read(root / "result.json")
            put(path.parent / "outcome.json", {"root_complete_sha256": sha(root / "complete.json"), "state": result["state"]})
            recoveries.append({"root": str(root), "status": "sealed", "result": result})
        else: recoveries.append({"root": str(root), "status": "incomplete_not_resent"})
    for path in (bridge.batch.root / "requests").glob("*/reservation.json"):
        row = read(path)
        if row["fingerprint"] != plan["fingerprint"]: continue
        folder = Path(row["attempt"])
        require(str(folder.parent.resolve()) in legal, "FOREIGN_RECOVERY_ATTEMPT")
        if (folder / "complete.json").exists():
            verify(folder); bridge.finish(path.parent, read(folder / "result.json"), folder)
            bridge.batch._outcome_cache.pop(path, None); bridge.batch._outcome(path)
    put(claim / "outcome.json", {"root_complete_sha256": sha(origin / "complete.json"),
                                "state": read(origin / "result.json")["state"]})
    return {"status": "sealed", "result": read(origin / "result.json"), "explicit_recoveries": recoveries}


def run(store, opid, *, opener=None, api_key=None):
    bridge = origin_bridge(store); plan = bridge.operation(opid)
    target = bridge.root / "explicit_retry" / opid
    grant = read(target / "grant.json")
    origin = bridge.root / "calls" / opid; root = bridge.root / "recovered_calls" / opid
    require(grant["original_root"] == str(origin) and grant["fingerprint"] == plan["fingerprint"]
            and grant["budget_root"] == str(bridge.batch.root) and grant["campaign_root"] == str(bridge.root)
            and grant["data_root"] == str(store.root),
            "RETRY_SCOPE_OR_CODE_CHANGED")
    require(inventory(origin) == grant["original_artifacts"] and sha(origin / "complete.json") == grant["original_seal"],
            "ORIGINAL_UNKNOWN_MUST_REMAIN_IMMUTABLE")
    policy = calls.Policy(**grant["policy"])
    manifest = plan["body"]["manifest"]
    require(digest(manifest["wire"]) == grant["body_digest"], "RETRY_REQUEST_BODY_CHANGED")
    mock = plan["body"]["mode"] == "MOCK"

    def admit():
        require(time.time() < grant["expires_at"] and not store.cancelled(plan["turn_id"]), "RETRY_EXPIRED_OR_CANCELLED")
        from .clean_conversation_graph import verify_sources
        verify_sources(store, grant["source_preview"]["sources_ref"])
        require(bridge.batch.accounting()["request_reservations"] <= grant["request_ceiling"], "RETRY_CAP_EXCEEDED")
        require(mock or isinstance(api_key, str) and api_key.strip(), "TOKENMETRO_API_KEY_REQUIRED")

    class Budget:
        # Same Batch, fingerprint, turn and mapping. No replacement operation or
        # budget-root switch. Shared caller alone decides the authorized retry.
        root = bridge.batch.root
        def reserve(self, fingerprint, folder, allowance):
            admit()
            require(fingerprint == plan["fingerprint"] and Path(folder).parent == root / "attempts"
                    and allowance == plan["allowance"], "RETRY_RESERVATION_CHANGED")
            with calls.locked(target / "admission.lock"):
                require(bridge.batch.accounting()["request_reservations"] < grant["request_ceiling"], "ONE_RETRY_ONLY")
                return SessionCalls.reserve(bridge, fingerprint, folder, allowance)
        def finish(self, *args): return bridge.finish(*args)

    def send(folder, headers):
        require(not headers, "IDEMPOTENCY_NOT_VERIFIED")
        admit()
        sequence = 0
        def emit(event):
            nonlocal sequence
            sequence += 1
            store.event(plan["turn_id"], f"retry-{opid}-{sequence:06d}",
                        {**event, "operation_id": opid, "explicit_retry": True})
        return attempt(folder, manifest, key="fixture" if mock else api_key,
            opener=opener if mock else build_opener(ProxyHandler({}), _RejectRedirectHandler()),
            cancelled=lambda: store.cancelled(plan["turn_id"]),
            emit=emit)

    if not (root / "complete.json").exists():
        require(grant["code"] == code_identity(), "RETRY_SCOPE_OR_CODE_CHANGED")
        admit(); require(mock and opener is not None or not mock and opener is None, "RETRY_MOCK_LIVE_SEPARATION")
    result = calls.run(root, plan, bridge.root / "ledger", send, channel=plan["channel"], policy=policy,
                       batch=Budget(), token_allowance=plan["allowance"], legacy_origin=origin)
    reconcile_origin(bridge, opid)
    report_path = target / "report.json"
    if report_path.exists(): return read(report_path)
    attempts = sorted((root / "attempts").glob("*"))
    require(len(attempts) <= 1, "EXPLICIT_RETRY_OVERDISPATCH")
    receipt = read(attempts[-1] / "result.json") if attempts else result
    check = {"status": "NOT_APPLICABLE", "semantic_support": "NOT_EVALUATED"}
    if receipt.get("kind") == "final_text":
        sources = store.load(grant["source_preview"]["sources_ref"])
        check = validate_citations(sources["packet"], re.findall(r"\[(E\d+)\]", receipt["answer_text"]))
    accounting = bridge.batch.accounting(); accounting.pop("new_request_reservations", None)
    report = {"version": VERSION, "operation_id": opid, "retry_call": str(root), "retry_result": receipt,
        "original_result": read(origin / "result.json"), "combined_accounting": accounting,
        "additional_requests": len(attempts), "citation_validation": check,
        "original_history_preserved": True, "research_continuation": "not_automatically_dispatched"}
    put(report_path, report)
    # Grant/report remain separate from immutable shared call facts. Reopening
    # replays this same recovery and cannot dispatch another retry.
    return report
