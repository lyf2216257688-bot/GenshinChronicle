"""MCP tools over newline-delimited JSON-RPC, using the negotiated 2025 tool surface."""
import json
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..evidence_io import strict_json


VERSIONS = ("2025-11-25", "2025-06-18")
ID = {"type": "string", "minLength": 1, "maxLength": 96, "pattern": "^[A-Za-z0-9_-]+$"}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
OFFSET = {"type": "integer", "minimum": 0, "maximum": 50_000_000}
SCOPED = {"study": ID}
ACTION = {**SCOPED, "request_id": ID}


def tool(name, description, properties, optional=()):
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": properties,
        "required": [k for k in properties if k not in optional], "additionalProperties": False}}


TOOLS = {t["name"]: t for t in [
    tool("list_cases", "List the registered offline questions and captured query IDs.", {}),
    tool("open_study", "Open a unique study on one frozen case. Return the verified first evidence Packet.",
         {**ACTION, "case": ID}),
    tool("browse_candidates", "Page through the original ranking. Titles are navigation only.",
         {**SCOPED, "query": ID, "offset": OFFSET}, ("offset",)),
    tool("read_source", "Read a registered candidate or its parent. Use get_read to continue a long archived body.",
         {**ACTION, "handle": HASH, "scope": {"enum": ["chunk", "parent", "natural_parent", "graph"]}}),
    tool("get_read", "Page a previously verified complete source receipt using next_offset. This uses no new read allowance.",
         {**SCOPED, "read_ref": HASH, "offset": OFFSET}, ("offset",)),
    tool("follow_query", "Replay one offered frozen follow-up, with its parent query, evidence handles and a concrete gap.",
         {**ACTION, "query": ID, "parent_query": ID, "evidence_handles": {
             "type": "array", "items": HASH, "maxItems": 32, "uniqueItems": True},
          "gap": {"type": "string", "minLength": 1, "maxLength": 1000}}),
    tool("assemble_packet", "Publish a new Packet from verified reads in research order. Optional selection uses fragment IDs.",
         {**ACTION, "fragment_ids": {"type": "array", "items": HASH, "maxItems": 32, "uniqueItems": True}}, ("fragment_ids",)),
    tool("get_packet", "Read the current Packet or continue its exact JSON text at next_offset.",
         {**SCOPED, "packet_ref": HASH, "offset": OFFSET, "citation": {"type": "string", "pattern": "^E[0-9]{2,4}$"}},
         ("offset", "citation")),
    tool("check_answer", "Bind explicit [E01] markers in host-supplied answer text to the named current Packet.",
         {**SCOPED, "packet_ref": HASH, "answer": {"type": "string", "minLength": 1, "maxLength": 24000}}),
    tool("request_status", "Recover the exact committed result after a disconnect using the original operation ID.",
         {**SCOPED, "request_id": ID}),
]}


def failure(code, message):
    return {"error": {"code": code, "message": message}}


def serve(make_studies, incoming, outgoing):
    """Keep discovery light; serialize evidence actions while the reader handles cancellation."""
    pending = {}
    guard = threading.RLock()
    writer = threading.Lock()
    executor = ThreadPoolExecutor(max_workers=1)
    studies = None
    stage = "new"

    def send(rid, payload):
        wire = json.dumps({"jsonrpc": "2.0", "id": rid, **payload}, ensure_ascii=False, separators=(",", ":"))
        with writer:
            outgoing.write(wire.encode("utf8") + b"\n")
            outgoing.flush()

    def run(rid, name, args, cancel, deadline):
        nonlocal studies
        def check():
            if cancel.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("Operation cancelled or expired")
        try:
            from jsonschema import Draft202012Validator
            check()
            if not Draft202012Validator(TOOLS[name]["inputSchema"]).is_valid(args):
                payload = failure(-32602, "Arguments do not match the tool schema")
            else:
                if studies is None:
                    studies = make_studies()
                result = studies.call(name, args, check)
                text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
                payload = {"result": {"structuredContent": result, "content": [{"type": "text", "text": text}]}}
        except TimeoutError:
            payload = failure(-32800, "Operation cancelled or expired; check request_status")
        except (OSError, ValueError, KeyError, TypeError, IndexError, sqlite3.Error) as exc:
            # Paths and archive data stay out of host error messages.
            msg = str(exc)
            public = msg if isinstance(exc, ValueError) and re.fullmatch(r"[A-Za-z0-9 /_-]{1,110}", msg) else "Evidence or audit data unavailable"
            payload = {"result": {"isError": True, "content": [{"type": "text", "text": public}]}}
        except Exception as exc:
            print("Offline MCP failure:", type(exc).__name__, file=sys.stderr)
            payload = failure(-32603, "Internal error")
        with guard:
            pending.pop(rid, None)
            if not cancel.is_set():
                send(rid, payload)

    try:
        while True:
            raw = incoming.readline(256 * 1024 + 1)
            if not raw:
                break
            if len(raw) > 256 * 1024:
                send(None, failure(-32600, "Oversized request"))
                break
            try:
                msg = strict_json(raw.decode("utf8"))
            except (UnicodeError, ValueError, RecursionError):
                send(None, failure(-32700, "Invalid JSON"))
                continue
            if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
                send(None, failure(-32600, "Invalid request"))
                continue
            rid, method, params = msg.get("id"), msg["method"], msg.get("params", {})
            if "id" not in msg:
                if method == "notifications/initialized" and stage == "negotiated":
                    stage = "ready"
                if method == "notifications/cancelled" and isinstance(params, dict):
                    target = params.get("requestId")
                    with guard:
                        if type(target) in (str, int) and target in pending:
                            pending[target].set()
                continue
            if type(rid) not in (str, int) or not isinstance(params, dict):
                send(None, failure(-32600, "Invalid request"))
                continue
            if method == "initialize" and stage == "new":
                requested = params.get("protocolVersion")
                if not isinstance(requested, str) or not isinstance(params.get("clientInfo"), dict) or not isinstance(params.get("capabilities"), dict):
                    send(rid, failure(-32602, "Invalid initialization"))
                    continue
                stage = "negotiated"
                send(rid, {"result": {"protocolVersion": requested if requested in VERSIONS else VERSIONS[0],
                     "capabilities": {"tools": {}}, "serverInfo": {"name": "gc-offline-evidence", "version": "1"},
                     "instructions": "Use list_cases then open_study. Use unique study and operation IDs. Only complete Packet evidence supports citations. Continue partial pages before interpreting them."}})
            elif method == "ping":
                send(rid, {"result": {}})
            elif method not in {"tools/list", "tools/call"}:
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
                        send(rid, failure(-32001, "Busy; check request_status before retrying"))
                    else:
                        cancel = threading.Event()
                        pending[rid] = cancel
                        executor.submit(run, rid, params["name"], params.get("arguments", {}), cancel, time.monotonic() + 60)
    finally:
        with guard:
            for cancel in pending.values():
                cancel.set()
        executor.shutdown(wait=True)
