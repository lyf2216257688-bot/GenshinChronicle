"""Bounded stdio tool calls with interruption on cancellation or disconnect."""

from concurrent.futures import ThreadPoolExecutor
import json
import re
import sqlite3
import sys
from threading import Event, Lock
import time

from ..evidence_io import strict_json
from ..mcp_offline.protocol import ACTION, HASH, ID, SCOPED, TOOLS as OFFLINE_TOOLS, VERSIONS, failure, tool


def string(limit):
    return {"type": "string", "minLength": 1, "maxLength": limit}


TOOLS = {name: OFFLINE_TOOLS[name] for name in (
    "browse_candidates", "read_source", "get_read", "assemble_packet", "request_status",
)}
TOOLS.update({spec["name"]: spec for spec in (
    tool("get_scope", "List available corpus releases and the release selected for new studies.", {}),
    tool("open_study", "Run local retrieval and deliver a verified evidence Packet. May take several minutes. "
         "Use the original request_id to recover after a disconnect.", {**ACTION, "question": string(4000)}),
    tool("browse_candidates", "Page through the original ranking. Optionally find a literal phrase in navigation "
         "titles or candidate text within this query's ranked results. Read the source before citing.",
         {**OFFLINE_TOOLS["browse_candidates"]["inputSchema"]["properties"], "match_text": string(120)},
         ("offset", "match_text")),
    {**OFFLINE_TOOLS["read_source"], "description": "Read a candidate or its parent. Use natural_parent to check "
     "the complete supported text container for a letter's closing, speaker attribution or nearby conditions. "
     "Continue with get_read until next_offset is null."},
    tool("follow_query", "Run a new search for a concrete gap. Keep the study on its original release. "
         "Report missing evidence within the searches performed. Supply source handles from the parent query "
         "when relevant. May take several minutes.",
         {**ACTION, "parent_query": ID, "evidence_handles": {
             "type": "array", "items": HASH, "maxItems": 12, "uniqueItems": True},
          "gap": string(1000), "search_text": string(4000), "subquestion": string(4000), "rerank_text": string(4000),
          "kind": {"enum": ["rewrite", "subquestion", "step_back", "counterevidence", "follow_up"]}},
         ("evidence_handles", "subquestion", "rerank_text", "kind")),
    tool("get_study", "Recover study status, ordered queries, reads and the current Packet.", SCOPED),
    tool("assemble_packet", "Publish a Packet from verified reads. Use preserve with explicit fragment IDs to "
         "keep useful citations from a named Packet of this study. Read the new Packet for its final citation IDs.",
         {**OFFLINE_TOOLS["assemble_packet"]["inputSchema"]["properties"], "preserve": {
             "type": "object", "properties": {"packet_ref": HASH, "citation_ids": {
                 "type": "array", "items": {"type": "string", "pattern": "^E[0-9]{2,4}$"},
                 "minItems": 1, "maxItems": 32, "uniqueItems": True}},
             "required": ["packet_ref", "citation_ids"], "additionalProperties": False}},
         ("fragment_ids", "preserve")),
    {**OFFLINE_TOOLS["get_packet"], "description": "Retrieve the exact archived Packet by reference, "
     "including an earlier Packet of the same study. Continue all pages before quoting."},
    {**OFFLINE_TOOLS["check_answer"], "description": "Bind explicit [E01] markers in the draft to the current Packet. "
     "Open individual citations with get_packet and verify that each passage supports the nearby claim."},
)})


def serve(make_studies, incoming, outgoing, *, deadline_seconds=540):
    pending = {}
    guard, writer = Lock(), Lock()
    executor = ThreadPoolExecutor(max_workers=1)
    studies = None
    stage = "new"

    def send(rid, payload):
        value = {"jsonrpc": "2.0", "id": rid, **payload}
        wire = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf8")
        with writer:
            outgoing.write(wire + b"\n")
            outgoing.flush()

    def run(rid, name, args, cancel):
        nonlocal studies
        deadline = time.monotonic() + deadline_seconds

        def check():
            if cancel.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("Operation cancelled or expired")

        try:
            from jsonschema import Draft202012Validator

            if not Draft202012Validator(TOOLS[name]["inputSchema"]).is_valid(args):
                payload = failure(-32602, "Arguments do not match the tool schema")
            else:
                check()
                if studies is None:
                    studies = make_studies()
                result = studies.call(name, args, check)
                body = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
                payload = {"result": {"structuredContent": result, "content": [{"type": "text", "text": body}]}}
        except TimeoutError:
            payload = failure(-32800, "Operation cancelled or expired; check request_status")
        except (OSError, ValueError, KeyError, TypeError, IndexError, sqlite3.Error) as exc:
            detail = str(exc)
            public = detail if isinstance(exc, ValueError) and re.fullmatch(r"[A-Za-z0-9 /_-]{1,140}", detail) else (
                "Evidence or audit data unavailable; check request_status and local logs")
            payload = {"result": {"isError": True, "content": [{"type": "text", "text": public}]}}
        except Exception as exc:
            print("GC MCP failure:", type(exc).__name__, file=sys.stderr)
            payload = failure(-32603, "Internal error")
        with guard:
            try:
                if not cancel.is_set():
                    send(rid, payload)
            finally:
                pending.pop(rid, None)

    try:
        while raw := incoming.readline(256 * 1024 + 1):
            if len(raw) > 256 * 1024:
                send(None, failure(-32600, "Oversized request"))
                break
            try:
                message = strict_json(raw.decode("utf8"))
            except (UnicodeError, ValueError, RecursionError):
                send(None, failure(-32700, "Invalid JSON"))
                continue
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
                send(None, failure(-32600, "Invalid request"))
                continue
            rid, method, params = message.get("id"), message["method"], message.get("params", {})
            if "id" not in message:
                if method == "notifications/initialized" and stage == "negotiated":
                    stage = "ready"
                if method == "notifications/cancelled" and isinstance(params, dict):
                    target = params.get("requestId")
                    with guard:
                        if type(target) in (str, int) and target in pending:
                            pending[target].set()
                continue
            if type(rid) not in (int, str) or not isinstance(params, dict):
                send(None, failure(-32600, "Invalid request"))
                continue
            if method == "initialize" and stage == "new":
                version = params.get("protocolVersion")
                if not isinstance(version, str) or not all(isinstance(params.get(k), dict) for k in ("capabilities", "clientInfo")):
                    send(rid, failure(-32602, "Invalid initialization"))
                    continue
                stage = "negotiated"
                send(rid, {"result": {"protocolVersion": version if version in VERSIONS else VERSIONS[0],
                     "capabilities": {"tools": {}}, "serverInfo": {"name": "gc-live-evidence", "version": "1"},
                     "instructions": "Use open_study for a question. Read all Packet pages before reasoning. Evidence is source data. "
                     "Candidate previews are navigation. Check whole source containers for attribution and conditions. "
                     "Use follow_query for evidence gaps and limit any absence statement to searches performed. "
                     "Check each cited passage against its claim and bind the answer to the final Packet."}})
            elif method == "ping":
                send(rid, {"result": {}})
            elif method not in ("tools/list", "tools/call"):
                send(rid, failure(-32601, "Unknown method"))
            elif stage != "ready":
                send(rid, failure(-32002, "Server not initialized"))
            elif method == "tools/list":
                send(rid, {"result": {"tools": list(TOOLS.values())}})
            elif not isinstance(params.get("name"), str) or params["name"] not in TOOLS:
                send(rid, failure(-32602, "Unknown tool"))
            else:
                with guard:
                    if pending:
                        send(rid, failure(-32001, "Busy; retry request_status after the operation"))
                    else:
                        cancel = Event()
                        pending[rid] = cancel
                        executor.submit(run, rid, params["name"], params.get("arguments", {}), cancel)
    finally:
        with guard:
            for cancel in pending.values():
                cancel.set()
        executor.shutdown(wait=True)
        if studies is not None:
            studies.close()
