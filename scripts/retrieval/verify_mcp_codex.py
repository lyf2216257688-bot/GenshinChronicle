"""Probe an installed Codex app-server's MCP client in an isolated ephemeral thread."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
from queue import Queue
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


@contextmanager
def connect(command, home, env, log, *, timeout=60):
    with subprocess.Popen([command, "app-server", "--stdio"], cwd=home, env=env, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=log,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
        incoming = Queue()

        def receive():
            for line in process.stdout:
                incoming.put(line)
            incoming.put(b"")

        reader = Thread(target=receive, daemon=True)
        reader.start()
        serial = 0

        def rpc(method, params, notification=False):
            nonlocal serial
            serial += 1
            message = {"method": method, "params": params}
            if not notification:
                message["id"] = serial
            process.stdin.write((json.dumps(message) + "\n").encode())
            process.stdin.flush()
            if not notification:
                while True:
                    line = incoming.get(timeout=timeout)
                    require(line, "Codex app-server closed before replying")
                    reply = json.loads(line)
                    if reply.get("id") == serial:
                        require("result" in reply, f"Codex {method} failed: {reply.get('error')}")
                        return reply["result"]
        try:
            yield rpc
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
            reader.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-name", required=True)
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,48}", args.audit_name), "Invalid audit name")
    repo = Path.cwd()
    cmd = shutil.which("codex")
    require(cmd, "Installed Codex CLI is required")
    root = repo / "data/generation/mcp-offline-v1" / args.audit_name
    root.mkdir(parents=True, exist_ok=False)
    scratch = repo / ".local/tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=scratch) as tmp:
        home = Path(tmp)
        allowed = {"PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "COMSPEC", "PROCESSOR_ARCHITECTURE",
                   "NUMBER_OF_PROCESSORS", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "USERNAME"}
        env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        env.update(CODEX_HOME=tmp, CODEX_SQLITE_HOME=tmp, TEMP=tmp, TMP=tmp, TMPDIR=tmp,
                   HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9", ALL_PROXY="http://127.0.0.1:9",
                   NO_PROXY="localhost,127.0.0.1", DO_NOT_TRACK="1", OTEL_SDK_DISABLED="true")
        server_args = ["-X", "utf8", "-B", "-m", "genshin_corpus.mcp_offline",
                       "--repo", str(repo), "--audit-name", args.audit_name]
        settings = [
            'model = "offline-fixture"', 'model_provider = "offline_fixture"', "[model_providers.offline_fixture]",
            'name = "offline fixture"', 'base_url = "http://127.0.0.1:9/v1"', 'wire_api = "responses"',
            "[analytics]", "enabled = false", "[mcp_servers.gc_offline]", "command = " + json.dumps(sys.executable),
            "args = " + json.dumps(server_args), "cwd = " + json.dumps(str(repo)), "required = true",
            "startup_timeout_sec = 20", "tool_timeout_sec = 60", "[mcp_servers.gc_offline.env]",
            "PYTHONPATH = " + json.dumps(str(repo / "src")),
        ]
        (home / "config.toml").write_text("\n".join(settings), encoding="utf8")
        with (root / "process.log").open("xb") as log, connect(cmd, home, env, log) as rpc:
            rpc("initialize", {"clientInfo": {"name": "gc_offline_probe", "version": "1"},
                               "capabilities": {"experimentalApi": True}})
            rpc("initialized", {}, notification=True)
            reply = rpc("thread/start", {"cwd": tmp, "model": "offline-fixture", "modelProvider": "offline_fixture",
                                         "ephemeral": True, "approvalPolicy": "never", "sandbox": "read-only"})
            thread = reply["thread"]["id"]
            status = rpc("mcpServerStatus/list", {"threadId": thread, "serverName": "gc_offline"})
            server, = [s for s in status["data"] if s["name"] == "gc_offline"]

            def call(name, arguments, expect_error=False):
                result = rpc("mcpServer/tool/call", {"threadId": thread, "server": "gc_offline",
                                                      "tool": name, "arguments": arguments})
                require(bool(result.get("isError")) == expect_error, "Unexpected tool status: " + name)
                if not expect_error:
                    value = json.loads(result["content"][0]["text"])
                    require(value == result["structuredContent"], "Host text or structured evidence differs")
                    return value
                require("structuredContent" not in result, "Error supplied structured evidence")

            cases = call("list_cases", {})
            study = {"study": "host-smoke"}
            first = call("open_study", {**study, "case": "Q001", "request_id": "open"})
            packet = {**study, "packet_ref": first["packet_ref"]}
            require(first["first_packet_parity"] and "body" in first["projection"], "First Packet differs")
            checked = call("check_answer", {**packet, "answer": "[E01]"})
            require(checked["status"] == "PASS", "Citation did not bind")
            call("check_answer", {**packet, "answer": "[E999]"}, expect_error=True)
            report = {"host": subprocess.check_output([cmd, "--version"], env=env, cwd=home).decode().strip(),
                      "surface": "isolated app-server direct MCP tool call", "tools": sorted(server["tools"]),
                      "cases": [e["id"] for e in cases["cases"]], "binding": first["binding"], "packet": first["packet_id"],
                      "complete_first_body": True, "citations": checked["citations"], "rejected_unknown_e999": True,
                      "no_turn_start": True, "generation_calls": 0}
            target = root / "codex-client.json"
            with target.open("x", encoding="utf8") as output:
                json.dump(report, output, indent=2)
            print(target)


if __name__ == "__main__":
    main()
