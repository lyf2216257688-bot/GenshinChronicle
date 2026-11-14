"""G1 Mock-only durable graph; does not implement a research/chat controller."""
from contextlib import contextmanager, closing
import os
from pathlib import Path
import sqlite3
from typing import TypedDict

from ..evidence_io import digest, require, seal, sha, write
from ..model_call_sessions import SessionCalls


class ResearchState(TypedDict, total=False):
    version: str
    turn_id: str
    operation_id: str
    result_ref: str
    stop_reason: str
    graph_code: str


def mock_attempt(attempt, headers):
    result = {"state": "CONTENT_VERIFIED", "mode": "MOCK", "text": "离线夹具，不是模型回答。",
              "calls": 0, "request_count": 0, "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
              "actual_fee": 0}
    write(attempt / "request.json", {"mode": "MOCK", "headers": headers})
    write(attempt / "result.json", result); seal(attempt)
    return result


@contextmanager
def graph(bridge, tid, *, send_once=mock_attempt, fault=lambda point: None):
    # Import only inside the isolated orchestration runtime.
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.sqlite import SqliteSaver
    require(isinstance(bridge, SessionCalls), "Session bridge required")
    turn = bridge.store.turn(tid)

    def prepare(state):
        require(state["turn_id"] == tid and state["version"] == "gc-clean-research-state-1", "Foreign graph state")
        opid = bridge.prepare(tid, "g1-mock", {"question": turn["question"], "mode": "MOCK"}, allowance=10)
        fault("prepared_before_checkpoint")
        return {"operation_id": opid}

    def dispatch(state):
        # prepare's checkpoint is durable before this effect node begins.
        fault("checkpoint_before_dispatch")
        result = bridge.execute_mock(state["operation_id"], send_once)
        fault("call_sealed_before_checkpoint")
        bridge.store.event(tid, "mock_result", {"kind": "mock_result", "operation_id": state["operation_id"],
                                             "result_digest": digest(result)})
        return {"result_ref": str(bridge.root / "calls" / state["operation_id"] / "result.json"),
                "stop_reason": "sufficient_fixture_only"}

    builder = StateGraph(ResearchState)
    builder.add_node("prepare", prepare, retry_policy=None)
    builder.add_node("dispatch", dispatch, retry_policy=None)
    builder.add_edge(START, "prepare"); builder.add_edge("prepare", "dispatch"); builder.add_edge("dispatch", END)
    with closing(sqlite3.connect(bridge.store.root / "checkpoints.sqlite", check_same_thread=False)) as db:
        db.execute("PRAGMA synchronous=FULL")
        yield builder.compile(checkpointer=SqliteSaver(db))


def run_mock(bridge, tid, *, continue_turn=False, send_once=mock_attempt, fault=lambda point: None):
    with bridge.store.execute(tid):
        with graph(bridge, tid, send_once=send_once, fault=fault) as compiled:
            config = {"configurable": {"thread_id": tid}}
            snapshot = compiled.get_state(config)
            if snapshot.values:
                require(snapshot.values.get("graph_code") == sha(__file__),
                        "Incompatible graph code; read-only display, no automatic migration")
                if not snapshot.next:
                    opid = snapshot.values["operation_id"]
                    bridge.reconcile(opid)
                    bridge.store.event(tid, "turn_complete", {"kind": "mock_complete", "mode": "MOCK"})
                    return snapshot.values
                require(continue_turn, "Interrupted turn requires explicit continue")
                data = None
            else:
                data = {"version": "gc-clean-research-state-1", "turn_id": tid, "graph_code": sha(__file__)}
            value = compiled.invoke(data, config, durability="sync")
            bridge.store.event(tid, "turn_complete", {"kind": "mock_complete", "mode": "MOCK"})
            return value
