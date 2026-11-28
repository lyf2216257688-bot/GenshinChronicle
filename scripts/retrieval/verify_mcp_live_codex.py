"""Exercise live evidence through the installed Codex app-server without a model turn."""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

from genshin_corpus.evidence_io import digest, read

from verify_mcp_codex import connect, require


@contextmanager
def client(repo, root, release):
    command = shutil.which("codex")
    require(command, "Installed Codex CLI is required")
    scratch = repo / ".local/tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=scratch) as tmp:
        home = Path(tmp)
        allowed = {"PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "COMSPEC", "PROCESSOR_ARCHITECTURE",
                   "PROCESSOR_ARCHITEW6432", "NUMBER_OF_PROCESSORS", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "USERNAME"}
        env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        env.update(CODEX_HOME=tmp, CODEX_SQLITE_HOME=tmp, TEMP=tmp, TMP=tmp, TMPDIR=tmp,
                   HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9", ALL_PROXY="http://127.0.0.1:9",
                   NO_PROXY="localhost,127.0.0.1", DO_NOT_TRACK="1", OTEL_SDK_DISABLED="true")
        args = ["-X", "utf8", "-B", "-m", "genshin_corpus.mcp_live", "--repo", str(repo),
                "--audit-name", root.name, "--release", release]
        settings = [
            'model = "offline-fixture"', 'model_provider = "offline_fixture"', "[model_providers.offline_fixture]",
            'name = "offline fixture"', 'base_url = "http://127.0.0.1:9/v1"', 'wire_api = "responses"',
            "[analytics]", "enabled = false", "[mcp_servers.gc_live]", "command = " + json.dumps(sys.executable),
            "args = " + json.dumps(args), "cwd = " + json.dumps(str(repo)), "required = true",
            'env_vars = ["PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432"]',
            "startup_timeout_sec = 20", "tool_timeout_sec = 600", "[mcp_servers.gc_live.env]",
            "PYTHONPATH = " + json.dumps(str(repo / "src")),
        ]
        (home / "config.toml").write_text("\n".join(settings), encoding="utf8")
        log_path = root / f"codex-{release}-{time.time_ns()}.log"
        with log_path.open("xb") as log, connect(command, home, env, log, timeout=615) as rpc:
            rpc("initialize", {"clientInfo": {"name": "gc_live_probe", "version": "1"},
                               "capabilities": {"experimentalApi": True}})
            rpc("initialized", {}, notification=True)
            thread = rpc("thread/start", {"cwd": tmp, "model": "offline-fixture", "modelProvider": "offline_fixture",
                                         "ephemeral": True, "approvalPolicy": "never", "sandbox": "read-only"})["thread"]["id"]
            status = rpc("mcpServerStatus/list", {"threadId": thread, "serverName": "gc_live"})
            server, = [item for item in status["data"] if item["name"] == "gc_live"]

            def call(name, args, *, expect_error=False):
                result = rpc("mcpServer/tool/call", {"threadId": thread, "server": "gc_live", "tool": name, "arguments": args})
                require(bool(result.get("isError")) == expect_error,
                        "Unexpected tool status: " + name + " " + str(result.get("content", []))[:230])
                if expect_error:
                    require("structuredContent" not in result, "Error supplied structured evidence")
                    return
                value = json.loads(result["content"][0]["text"])
                require(value == result["structuredContent"], "Codex text projection differs")
                return value

            version = subprocess.check_output([command, "--version"], env=env, cwd=home).decode().strip()
            yield call, version, sorted(server["tools"])


def collect(first, next_page):
    projection = first["projection"]
    if "body" in projection:
        return projection["body"], 1
    checksum = projection["projection_sha256"]
    total = projection["total_chars"]
    count, parts = 1, [projection["partial_json_text"]]
    while (offset := projection["next_offset"]) is not None:
        projection = next_page(offset)["projection"]
        require(projection["offset"] == offset and projection["projection_sha256"] == checksum, "Foreign page")
        parts.append(projection["partial_json_text"])
        count += 1
    combined = "".join(parts)
    require(len(combined) == total, "Incomplete tool projection")
    body = json.loads(combined)
    require(digest(body) == checksum, "Corrupted tool projection")
    return body, count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-name", required=True)
    flat = parser.add_mutually_exclusive_group()
    flat.add_argument("--old-study", help="Verify a historical flat study in this audit root")
    flat.add_argument("--seed-flat", action="store_true", help="Open a flat study through Codex before selecting the release")
    args = parser.parse_args()
    require(re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", args.audit_name), "Invalid audit name")
    repo = Path.cwd()
    catalog = read(repo / "config/mcp-live-v1.json")
    active = catalog["active"]
    old = {"study": args.old_study or "codex-flat-study"} if args.old_study or args.seed_flat else None
    require(old is None or "20260824" in catalog["releases"], "Flat study execution has been archived")
    reconnect_release = "20260824" if old else active
    root = repo / "data/generation/mcp-live-v1" / args.audit_name
    root.mkdir(parents=True, exist_ok=bool(args.old_study))
    study = {"study": "codex-release-study"}
    open_args = {**study, "request_id": "open", "question": "《幽潭心》中住在清泉镇的纯水精灵叫什么？她和迪奥娜的调酒祝福有何关系？"}
    flat_seconds = None
    if args.seed_flat:
        with client(repo, root, "20260824") as (call, _, _):
            start = time.perf_counter()
            flat_first = call("open_study", {**old, "request_id": "open",
                              "question": "须弥教令院有哪些学派？研究历史、社会与知识来源的学者对应什么学派？"})
            flat_seconds = time.perf_counter() - start
            flat_packet = {**old, "packet_ref": flat_first["packet_ref"]}
            body, _ = collect(flat_first, lambda offset: call("get_packet", {**flat_packet, "offset": offset}))
            require(bool(body) and call("check_answer", {**flat_packet, "answer": "[E01]"})["status"] == "PASS",
                    "Flat study evidence not recoverable in Codex")
    with client(repo, root, active) as (call, version, tools):
        scope = call("get_scope", {})
        require(scope["new_studies"]["release"] == active, "Wrong release")
        previous = call("get_study", old) if old else None
        start = time.perf_counter()
        first = call("open_study", open_args)
        seconds = time.perf_counter() - start
        packet = {**study, "packet_ref": first["packet_ref"]}
        body, packet_pages = collect(first, lambda offset: call("get_packet", {**packet, "offset": offset}))
        require(bool(body), "No live evidence")
        checked = call("check_answer", {**packet, "answer": "[E01]"})
        require(checked["status"] == "PASS", "Citation did not bind")
        call("check_answer", {**packet, "answer": "[E999]"}, expect_error=True)
        browsed = call("browse_candidates", {**study, "query": "original"})
        handle = browsed["rows"][0]["handle"]
        expanded = call("read_source", {**study, "request_id": "parent", "handle": handle, "scope": "parent"})
        source, source_pages = collect(expanded, lambda offset: call("get_read", {
            **study, "read_ref": expanded["read_ref"], "offset": offset}))
        require(bool(source) and expanded["raw_byte_revalidation"] == "current_and_effective", "No verified parent")
        if old:
            call("get_packet", {**study, "packet_ref": previous["current_packet_ref"]}, expect_error=True)
            call("read_source", {**old, "request_id": "foreign", "handle": handle, "scope": "chunk"}, expect_error=True)
    with client(repo, root, reconnect_release) as (call, _, _):
        require(call("get_scope", {})["new_studies"]["release"] == reconnect_release, "Reconnect release differs")
        require(call("get_study", study)["release"] == active, "Study was rebound after reconnect")
        recovered = call("request_status", {**study, "request_id": "open"})
        require(recovered == {"status": "committed", "result": first}
                and call("open_study", open_args) == first, "Committed readback after reconnect changed")
        packet_view = call("get_packet", packet)
        require(packet_view["projection"] == first["projection"], "Historical Packet changed")
        reread = call("read_source", {**study, "request_id": "reread-parent", "handle": handle, "scope": "parent"})
        require(reread["receipt_id"] == expanded["receipt_id"], "Historical source receipt changed")
        if old:
            require(call("get_study", old) == previous, "Flat study changed")
            older = call("get_packet", {**old, "packet_ref": previous["current_packet_ref"]})
            require(bool(older["citation_ids"]), "Flat study lost its citations")
            old_rows = call("browse_candidates", {**old, "query": "original"})
            older_read = call("read_source", {**old, "request_id": "rollback-read", "scope": "chunk",
                                              "handle": old_rows["rows"][0]["handle"]})
            require(older_read["effective_raw_run"] == "p01eb_full_20260824", "Old Raw was rebound")
    report = {"host": version, "tools": tools, "generation_calls": 0, "tool_seconds": seconds,
              "flat_tool_seconds": flat_seconds,
              "packet": first["packet_id"], "binding": first["binding"], "packet_pages": packet_pages,
              "packet_chars": first["projection"]["total_chars"], "parent_pages": source_pages,
              "parent_chars": expanded["projection"]["total_chars"], "parent_receipt": expanded["receipt_id"],
              "citations": checked["sources"], "unknown_citation_rejected": True,
              "reconnect_release": reconnect_release, "exact_recovery": True,
              "both_releases_reread": bool(old), "cross_release_rejected": bool(old)}
    with (root / "codex-client.json").open("x", encoding="utf8") as out:
        json.dump(report, out, ensure_ascii=False, indent=2)
    print(root / "codex-client.json")


if __name__ == "__main__":
    main()
