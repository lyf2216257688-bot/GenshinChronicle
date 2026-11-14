"""G4 fixed-chain Mock dialogue on G1 persistence, G2 evidence and G3 transport."""
from contextlib import closing, contextmanager
import os
import re
import sqlite3
from typing import TypedDict

from ..evidence_io import digest, require, sha
from ..model_calls import Deferred
from ..generation.clean_chat import ChatRequest, encoded
from ..generation.clean_chat_transport import ChatSessions
from ..generation.clean_evidence import EvidenceReader, assemble, message_projection, validate_citations
from ..retrieval.clean_based.query_v2 import ResearchQuery
from . import clean_context, clean_conversation, clean_mock
from .clean_context import organize, assembly_context, SYSTEM
from .clean_mock import MockOpener, mock_answer

VERSION = "gc-clean-conversation-graph-1"


def fixture_settings():
    """Finite OFFLINE FIXTURE values, deliberately no live product defaults."""
    return {"mode": "MOCK", "limits": {"campaign_requests": 64, "campaign_tokens": 4194304,
        "turn_requests": 1, "turn_tokens": 65536},
        "context_tokens": 65536, "output_tokens": 1024, "timeout_seconds": 30,
        "history_bytes": 24000, "recent_turns": 2, "excerpt_chars": 160}


def code_identity():
    return {"graph": sha(__file__), "conversation": sha(clean_conversation.__file__),
            "context": sha(clean_context.__file__), "mock": sha(clean_mock.__file__)}


class State(TypedDict, total=False):
    version: str
    turn_id: str
    code: dict
    context_ref: dict
    sources_ref: dict
    operation_id: str
    result_ref: dict
    dispatch_stop: str


def messages(context, evidence):
    projection = message_projection(context, evidence)
    return [{"role": "system", "content": projection.pop("system")},
            {"role": "user", "content": encoded(projection).decode("utf8")}]


def request_for(context, packet, settings):
    return ChatRequest(messages(context, packet["evidence"]), [], "disabled", True,
        settings["output_tokens"], settings["context_tokens"] - settings["output_tokens"],
        settings["context_tokens"], settings["timeout_seconds"], "g4-mock-fixed-chain-1")


def saved_result(store, tid):
    events = [e for e in store.events(tid) if e["kind"] == "g4_result_ready"]
    return store.load(events[-1]["ref"]) if events else None


def verify_sources(store, ref):
    sources = store.load(ref)
    packet = sources["packet"]
    validate_citations(packet, list(packet["citations"]))
    ranked = store.load(sources["ranked_ref"])
    require(packet["input_group_digests"] == [digest(ranked)], "Packet ranked input differs")
    ctx = store.load(sources["context_ref"])
    turn = store.turn(ctx["turn_id"])
    require(digest(assembly_context(turn, ctx)) == packet["context_digest"], "Packet conversation context differs")
    for rid, read_ref in sources["reads"].items():
        receipt = store.load(read_ref)
        require(receipt["receipt_id"] == rid, "Source receipt identity differs")
    for read_ref in sources["parents"].values(): store.load(read_ref)
    return sources


def citation_view(store, tid, citation_id, *, packet_id=None):
    """Turn + Packet + citation ID is the address, never a global E01 slot."""
    result = saved_result(store, tid)
    require(result is not None and result.get("sources_ref"), "No source Packet for this turn")
    sources = verify_sources(store, result["sources_ref"])
    packet = sources["packet"]
    require(packet_id is None or packet_id == packet["packet_id"], "Citation Packet belongs to another turn")
    validate_citations(packet, [citation_id])
    mapping = packet["citations"][citation_id]
    receipt = store.load(sources["reads"][mapping["receipt_id"]])
    require(receipt["receipt_id"] == mapping["receipt_id"] and receipt["source_validation"] == "PASS", "Unverified citation receipt")
    fragment = next(f for f in receipt["fragments"] if f["fragment_id"] == mapping["fragment_id"])
    parent_ref = sources["parents"].get(receipt["handle"])
    parent = store.load(parent_ref) if parent_ref else None
    if parent:
        require(parent["handle"] == receipt["handle"] and parent["source_snapshot"] == receipt["source_snapshot"], "Parent source differs")
    return {"turn_id": tid, "packet_id": packet["packet_id"], "citation_id": citation_id,
            "fragment": fragment, "receipt": receipt, "parent": parent,
            "parent_status": "available" if parent else "not_read_or_failed",
            "semantic_support": "NOT_EVALUATED"}


def commit_result(store, tid, result):
    """Files precede references. Replay repairs DB/message/checkpoint gaps only."""
    ref = store.artifact(tid, "result", result)
    store.event(tid, "g4-result-ready", {"kind": "g4_result_ready", "ref": ref})
    store.message(tid, "assistant", result["answer_text"] or result["delivery_note"],
                  status=result["status"], mode="MOCK", citations=result["citations"], result_ref=ref,
                  semantic_acceptance="NOT_REVIEWED")
    store.status(tid, result["status"])
    store.event(tid, "turn_complete", {"kind": "g4_complete", "mode": "MOCK", "result_ref": ref})
    return ref


@contextmanager
def conversation_graph(store, tid, pipeline, bridge, *, opener_factory=None, fault=lambda p: None):
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.sqlite import SqliteSaver
    turn = store.turn(tid)
    settings = store.chat(turn["session"])["settings"]
    require(settings["mode"] == "MOCK", "G4_LIVE_DISABLED")

    def progress(stage):
        require(not store.stopping(), "APPLICATION_SHUTDOWN_CONTINUE_REQUIRED")
        store.event(tid, "g4-" + stage, {"kind": "progress", "stage": stage, "mode": "MOCK", "final": False})

    def context_node(state):
        require(state["turn_id"] == tid and state["version"] == VERSION, "Foreign graph state")
        progress("context_started")
        ctx = organize(store, tid, **{k: settings[k] for k in ("history_bytes", "recent_turns", "excerpt_chars")})
        ref = store.artifact(tid, "context", ctx)
        progress("context_organized")
        fault("context_saved_before_checkpoint")
        return {"context_ref": ref}

    def source_node(state):
        progress("retrieval_started")
        published = store.published_artifact(tid, "sources")
        if published:
            sources = verify_sources(store, published)
            require(sources["query"]["turn_id"] == tid and sources["context_ref"] == state["context_ref"],
                    "PUBLISHED_SOURCES_INPUT_CHANGED")
            return {"sources_ref": published}
        ctx = store.load(state["context_ref"])
        context = assembly_context(turn, ctx)
        query = ResearchQuery(turn["session"], tid, "original", *([turn["question"]] * 4))
        ranked = pipeline.run(query)
        ranked_ref = store.artifact(tid, "ranked", ranked)
        progress("ranking_complete")
        er = EvidenceReader(pipeline.reader)
        handles = er.register(ranked)
        receipts, failures, refs, parents = [], [], {}, {}
        for cid, handle in handles.items():
            if store.cancelled(tid): break
            try:
                receipt = er.read(handle)
                refs[receipt["receipt_id"]] = store.artifact(tid, "read-" + receipt["receipt_id"], receipt)
                receipts.append(receipt)
            except (ValueError, KeyError, FileNotFoundError) as exc:
                failures.append({"chunk_id": cid, "type": type(exc).__name__, "scope": "chunk"})
                continue
            try:
                parent = er.read(handle, "natural_parent")
                parents[handle] = store.artifact(tid, "parent-" + parent["receipt_id"], parent)
            except (ValueError, KeyError, FileNotFoundError) as exc:
                failures.append({"chunk_id": cid, "type": type(exc).__name__, "scope": "parent"})
        budget = {"context_tokens": settings["context_tokens"], "output_tokens": settings["output_tokens"], "thinking_tokens": 0}
        # G2 counts the full neutral projection. Also account for G3's actual
        # escaped Chat envelope, reducing only evidence admission when needed.
        envelope_reserve = 0
        for _ in range(len(ranked["candidates"]) + 2):
            packet = assemble([ranked], receipts, source_failures=[f for f in failures if f["scope"] == "chunk"],
                              context=context, budget=budget)
            req = request_for(context, packet, settings)
            size = len(encoded({"model": "deepseek-v4.1-flash-1017", "messages": req.messages, "stream": True,
                                "thinking": {"type": "disabled"}, "max_tokens": req.output_tokens,
                                "stream_options": {"include_usage": True}}))
            excess = size + settings["output_tokens"] - settings["context_tokens"]
            if excess <= 0:
                req.manifest()
                break
            budget["context_tokens"] -= excess
            envelope_reserve += excess
        else: raise ValueError("WHOLE_MESSAGE_BUDGET_EXCEEDED")
        value = {"mode": "MOCK", "query": query.value(), "ranked_ref": ranked_ref,
                 "packet": packet, "reads": refs, "parents": parents, "source_failures": failures,
                 "context_ref": state["context_ref"], "chat_envelope_reserve": envelope_reserve}
        ref = store.artifact(tid, "sources", value)
        progress("packet_ready")
        fault("packet_saved_before_checkpoint")
        return {"sources_ref": ref}

    def prepare_node(state):
        sources = store.load(state["sources_ref"])
        context = assembly_context(turn, store.load(state["context_ref"]))
        req = request_for(context, sources["packet"], settings)
        opid = bridge.prepare_chat(tid, "g4-fixed-answer", req, mock=True)
        progress("request_prepared")
        fault("chat_prepared_before_checkpoint")
        return {"operation_id": opid}

    def answer_node(state):
        progress("generation_started")
        sources = store.load(state["sources_ref"])
        scenario = store.job(tid)["metadata"]["scenario"]
        opener = opener_factory(sources["packet"], scenario) if opener_factory else MockOpener(
            mock_answer(sources["packet"], scenario), disconnect=scenario == "disconnect")
        if store.cancelled(tid):
            return {"dispatch_stop": "user_cancelled"}
        # Reconcile uncertain reservations without invoking a sender. No new
        # operation, root or retry policy is created to bypass UNKNOWN.
        if bridge.reconcile(state["operation_id"])["status"] == "UNKNOWN":
            return {"dispatch_stop": "unknown_requires_reconciliation"}
        try:
            bridge.execute_chat(state["operation_id"], opener=opener)
        except Deferred:
            return {"dispatch_stop": "user_cancelled" if store.cancelled(tid) else "budget_or_admission_deferred"}
        require(not store.stopping(), "APPLICATION_SHUTDOWN_CONTINUE_REQUIRED")
        fault("chat_sealed_before_checkpoint")
        return {}

    def check_node(state):
        progress("checking")
        published = store.published_artifact(tid, "result")
        if published:
            existing = store.load(published)
            require(existing["turn_id"] == tid and existing["operation_id"] == state["operation_id"]
                    and existing["sources_ref"] == state["sources_ref"], "PUBLISHED_RESULT_INPUT_CHANGED")
            verify_sources(store, existing["sources_ref"])
            bridge.reconcile(state["operation_id"])
            return {"result_ref": published}
        sources = store.load(state["sources_ref"])
        status = bridge.reconcile(state["operation_id"])
        result = status.get("result", {})
        answer = result.get("answer_text") if result.get("kind") == "final_text" else None
        citations, check = [], {"status": "NOT_COMPLETED", "semantic_support": "NOT_EVALUATED"}
        if answer:
            ids = list(dict.fromkeys(re.findall(r"\[(E\d+)\]", answer)))
            try: check = validate_citations(sources["packet"], ids)
            except ValueError:
                check = {"status": "FAIL", "error": "CITATION_NOT_DELIVERED", "semantic_support": "NOT_EVALUATED"}
                answer = None
            if answer:
                for cid in ids:
                    mapping = sources["packet"]["citations"][cid]
                    receipt = store.load(sources["reads"][mapping["receipt_id"]])
                    require(receipt["source_validation"] == "PASS", "CITATION_SOURCE_FAILED")
                    citations.append({"turn_id": tid, "packet_id": sources["packet"]["packet_id"], "citation_id": cid})
        cancelled = store.cancelled(tid)
        outcome = "cancelled" if cancelled and not answer else "completed" if answer and citations and not sources["source_failures"] else "partial"
        scenario = store.job(tid)["metadata"]["scenario"]
        if scenario == "limited" and outcome == "completed": outcome = "partial"
        accounting = bridge.batch.accounting()
        # This field is invocation-relative (Batch.window_start), not a durable
        # campaign fact, and changes when a process reopens the same campaign.
        accounting.pop("new_request_reservations", None)
        value = {"version": VERSION, "turn_id": tid, "session_id": turn["session"], "mode": "MOCK",
                 "status": outcome, "answer_text": answer, "citations": citations, "citation_validation": check,
                 "sources_ref": state["sources_ref"], "context_ref": state["context_ref"],
                 "operation_id": state["operation_id"], "call_state": result.get("state", status["status"]),
                 "stop_reason": "user_cancelled" if cancelled else "citation_validation_failed" if check["status"] == "FAIL"
                    else state.get("dispatch_stop", "fixture_limited" if outcome == "partial" else "fixture_complete"),
                 "delivery_note": "Mock部分交付：已保存可核验材料；本轮未形成通过完整检查的最终答案，草稿仅供查看。",
                 "semantic_acceptance": "NOT_REVIEWED", "live_capability": "NOT_VALIDATED",
                 "remote_calls": 0, "budget": accounting}
        ref = store.artifact(tid, "result", value)
        fault("result_saved_before_checkpoint")
        return {"result_ref": ref}

    def finish_node(state):
        result = store.load(state["result_ref"])
        commit_result(store, tid, result)
        return {}

    builder = StateGraph(State)
    nodes = [("context", context_node), ("sources", source_node), ("prepare", prepare_node),
             ("answer", answer_node), ("check", check_node), ("finish", finish_node)]
    previous = START
    for name, node in nodes:
        builder.add_node(name, node, retry_policy=None)
        builder.add_edge(previous, name)
        previous = name
    builder.add_edge(previous, END)
    with closing(sqlite3.connect(store.root / "checkpoints.sqlite", check_same_thread=False)) as db:
        db.execute("PRAGMA synchronous=FULL")
        yield builder.compile(checkpointer=SqliteSaver(db))


def run_turn(store, tid, pipeline, *, continue_turn=False, opener_factory=None, fault=lambda p: None):
    chat = store.chat(store.turn(tid)["session"])
    bridge = ChatSessions(store, chat["campaign"], chat["settings"]["limits"])
    with store.execute(tid):
        store.ensure_submission(tid)
        existing = saved_result(store, tid)
        if existing:
            if existing.get("sources_ref"): verify_sources(store, existing["sources_ref"])
            if existing.get("operation_id"): bridge.reconcile(existing["operation_id"])
            commit_result(store, tid, existing)
            return existing
        if store.cancelled(tid):
            result = {"version": VERSION, "turn_id": tid, "session_id": chat["id"], "mode": "MOCK",
                      "status": "cancelled", "answer_text": None, "citations": [], "sources_ref": None,
                      "delivery_note": "Mock轮次已取消；没有启动新的派发。", "stop_reason": "user_cancelled",
                      "remote_calls": 0, "semantic_acceptance": "NOT_REVIEWED"}
            # An interrupted in-flight graph must first reconcile its original
            # operation, retaining its sources and known/UNKNOWN accounting.
            with conversation_graph(store, tid, pipeline, bridge, opener_factory=opener_factory, fault=fault) as graph:
                snap = graph.get_state({"configurable": {"thread_id": tid}})
                if not snap.values: commit_result(store, tid, result); return result
        with conversation_graph(store, tid, pipeline, bridge, opener_factory=opener_factory, fault=fault) as graph:
            config = {"configurable": {"thread_id": tid}}
            snapshot = graph.get_state(config)
            if snapshot.values:
                require(snapshot.values["code"] == code_identity(), "INCOMPATIBLE_G4_CODE_READ_ONLY")
                require(continue_turn or store.cancelled(tid), "Interrupted turn requires explicit continue")
                data = None
            else:
                data = {"version": VERSION, "turn_id": tid, "code": code_identity()}
            store.status(tid, "running")
            try:
                value = graph.invoke(data, config, durability="sync")
                return store.load(value["result_ref"])
            except BaseException:
                store.status(tid, "interrupted")
                raise
