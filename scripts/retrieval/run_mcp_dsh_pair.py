"""Run the separately authorized, finite DSH comparison on release 20261124."""

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import hmac
import os
from pathlib import Path
import secrets
import subprocess
import sys
from threading import Thread
import time

from genshin_corpus.evidence_io import read, require, sha, strict_json, write
from genshin_corpus.indexing.benchmark_io import child_process, ram_guard

from dsh_model_dispatch import Dispatch, ROUTE, SIDE_SECONDS


REPO = Path.cwd().resolve()
ROOT = REPO / "data/generation/mcp-live-v1"
RELEASE = "20261124"
CASES = ("H01", "H02", "Q068", "X01", "X02", "H04")
DISABLED = ("llm-retry", "llm-deepseek", "llm-deepseek-account", "session-title-llm", "remote-access",
            "otel", "typert-gateway", "session-telemetry-otel", "web-search-deepseek", "web-deepseek-api",
            "web-fetch-http", "tool-web", "tool-bash", "tool-pwsh", "tool-workflow", "tool-subagent",
            "tool-subagent-fork", "tool-fs", "tool-fs-search")
INSTRUCTIONS = (
    "只用 GC MCP 查证。首查逐字使用下方原题。读取证据包全部分页，按需查阅相关原文和补查。"
    "写作时逐条检查引文位置及结论范围，在最终包下用 [E01] 等格式引用并核对绑定。\n\n"
)


def source_hashes(path):
    return {item.relative_to(path).as_posix(): sha(item) for item in sorted(path.rglob("*.py"))}


def register(input_root, dsh, out):
    baseline = read(input_root / "baseline.json")
    trial = read(input_root / "trial-registration.json")
    official = read(REPO / "config/mcp-live-v1.json")
    release = official["releases"][RELEASE]["artifact"]
    require(baseline["dsh"] == sha(dsh) and baseline["release"] == release["sha256"]
            and baseline["catalog"] == sha(REPO / "config/mcp-live-v1.json"),
            "20261124 baseline or installed DSH changed")
    sources = {"base": input_root / "baseline-src", "revised": REPO / "src"}
    manifests = {arm: {"path": str(path), "files": source_hashes(path)} for arm, path in sources.items()}
    require(manifests["base"]["files"] == baseline["sources"], "Frozen baseline sources changed")
    questions = {case: read(input_root / f"perf-{case}.json")["question"]
                 if case not in trial["independent"] else trial["independent"][case]["question"] for case in CASES}
    value = {"release": release, "catalog_sha256": baseline["catalog"], "dsh": str(dsh), "dsh_sha256": sha(dsh),
             "model": ROUTE["model"], "endpoint": ROUTE["endpoint"], "thinking": "disabled",
             "source_manifests": manifests, "questions": questions, "instructions": INSTRUCTIONS,
             "case_order": list(CASES),
             "independent_anchors": {"path": str(input_root / "independent-source-anchors.json"),
                                     "sha256": sha(input_root / "independent-source-anchors.json")},
             "runner_sha256": sha(__file__), "dispatch_sha256": sha(Path(__file__).with_name("dsh_model_dispatch.py"))}
    require(not out.exists(), "Use a new registration path")
    write(out, value)
    print(sha(out), flush=True)


def profile(port, python, source, audit):
    return [
        {"id": "llm-pi-ai", "config": {"providers": {"gc-loopback": {"api": "openai-completions",
            "baseURL": f"http://127.0.0.1:{port}/v1", "apiKeyEnv": "GC_DSH_LOCAL_KEY",
            "models": [{"id": ROUTE["model"], "contextWindow": 100000, "maxTokens": 2048, "reasoningEfforts": False}]}}}},
        {"id": "agent-default-model", "config": {"provider": "gc-loopback", "model": ROUTE["model"]}},
        *({"id": name, "disabled": True} for name in DISABLED),
        {"insert": [{"id": "gc-live", "name": "@deepseek-ai/dsh-mcp-client", "config": {
            "transport": "stdio", "serverName": "gc", "cwd": str(REPO), "command": python,
            "args": ["-X", "utf8", "-B", "-m", "genshin_corpus.mcp_live", "--repo", str(REPO),
                     "--audit-name", audit, "--release", RELEASE],
            "env": {"PYTHONPATH": source}, "startupTimeoutMs": 20000, "toolCallTimeoutMs": 600000,
            "failOnStartupError": True, "reconnect": {"enabled": False}}}]},
    ]


def check_release(reg):
    require(sha(REPO / "config/mcp-live-v1.json") == reg["catalog_sha256"]
            and sha(REPO / reg["release"]["path"]) == reg["release"]["sha256"], "DSH_RELEASE_CHANGED")


def run_side(root, reg, case, arm, dispatch, server, token):
    name = f"{case.lower()}-{arm}"
    audit = f"gc-dsh-pair-{name}"
    require(not (ROOT / audit).exists(), "DSH_SIDE_AUDIT_EXISTS")
    source = reg["source_manifests"][arm]
    check_release(reg)
    require(source_hashes(Path(source["path"])) == source["files"], "DSH_CODE_CHANGED")
    ram_guard(read(REPO / "config/clean-retrieval-v1.json"))
    folder = root / name
    folder.mkdir()
    patch = folder / "profile-patch.json"
    write(patch, profile(server.server_address[1], sys.executable, source["path"], audit))
    temp = folder / "tmp"
    temp.mkdir()
    env = {k: v for k, v in os.environ.items()
           if not k.upper().endswith(("_API_KEY", "_BASE_URL")) and k.upper() not in {"PYTHONPATH", "DSH_HOME"}}
    env.update(DSH_HOME=str(folder / "host"), ELECTRON_RUN_AS_NODE="1", GC_DSH_LOCAL_KEY=token,
               NO_PROXY="127.0.0.1", HTTP_PROXY="http://127.0.0.1:1", HTTPS_PROXY="http://127.0.0.1:1",
               NODE_USE_ENV_PROXY="1", TEMP=str(temp), TMP=str(temp), PYTHONUTF8="1")
    cli = Path(reg["dsh"]).parent / "resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js"
    command = [reg["dsh"], "--expose-internals", str(cli), "headless", "--patch", str(patch),
               "--json", "--approval-policy", "never", reg["instructions"] + reg["questions"][case]]
    dispatch.open_side(name)
    server.side = name
    started = time.monotonic()
    status = "DSH_HOST_TIMEOUT"
    code = None
    try:
        with (folder / "events.jsonl").open("xb") as stdout, (folder / "stderr.log").open("xb") as stderr:
            with child_process(command, cwd=REPO, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr) as proc:
                code = proc.wait(timeout=SIDE_SECONDS)
        status = "HOST_EXITED" if code == 0 else "HOST_FAILED"
    except subprocess.TimeoutExpired:
        dispatch.stop(status)
    finally:
        server.side = None
    side = dispatch.sides[name]
    write(folder / "result.json", {"case": case, "arm": arm, "status": status, "returncode": code,
          "model_attempts": side.dispatched, "first_queries": side.opens, "follow_queries": side.follows,
          "wall_seconds": time.monotonic() - started, "audit": audit})
    print(case, arm, status, f"{side.dispatched} attempts", flush=True)


def execute(registration, approval, out):
    require(sha(registration) == approval, "DSH_APPROVAL_REGISTRATION_MISMATCH")
    reg = read(registration)
    check_release(reg)
    require(reg["model"] == ROUTE["model"] and reg["endpoint"] == ROUTE["endpoint"] and
            reg["dsh_sha256"] == sha(reg["dsh"]) and reg["thinking"] == "disabled" and
            tuple(reg["case_order"]) == CASES and reg["runner_sha256"] == sha(__file__) and
            reg["dispatch_sha256"] == sha(Path(__file__).with_name("dsh_model_dispatch.py")) and
            sha(reg["independent_anchors"]["path"]) == reg["independent_anchors"]["sha256"], "DSH_REGISTRATION_CHANGED")
    from genshin_corpus.local_chat.api import DEFAULT_ROOT
    from genshin_corpus.local_chat.credentials import read_credential

    key = read_credential(DEFAULT_ROOT).key
    require(bool(key), "DSH_OFFICIAL_CREDENTIAL_UNAVAILABLE")
    out.mkdir()
    write(out / "registration.json", reg)
    dispatch = Dispatch(out / "upstream", approved_registration=approval, key=key)
    token = secrets.token_urlsafe(24)

    class Gateway(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            safe = self.path == "/v1/chat/completions" and hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + token)
            if not safe:
                self.send_error(403)
                return
            try:
                check_release(reg)
                size = int(self.headers["Content-Length"])
                require(self.server.side and 0 < size <= 4 * 1024 * 1024, "DSH_REQUEST_SIZE_OR_SIDE")
                request = strict_json(self.rfile.read(size).decode("utf8"))
                raw = dispatch.forward(self.server.side, request)
            except Exception as exc:
                write(out / f"gateway-error-{time.time_ns()}.json",
                      {"side": self.server.side, "type": type(exc).__name__,
                       "detail": str(exc) if isinstance(exc, ValueError) else None})
                if not str(exc).startswith("DSH_SIDE_"):
                    dispatch.stop("DSH_GATEWAY_FAILURE")
                self.send_error(400)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    with HTTPServer(("127.0.0.1", 0), Gateway) as server:
        server.side = None
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for index, case in enumerate(CASES):
                arms = ("base", "revised") if index % 2 == 0 else ("revised", "base")
                for arm in arms:
                    if dispatch.stopped:
                        return
                    run_side(out, reg, case, arm, dispatch, server, token)
        finally:
            server.shutdown()
            write(out / "complete.json", {"stopped": dispatch.stopped, "sides_started": len(dispatch.sides),
                  "input_tokens": dispatch.input_tokens, "output_tokens": dispatch.output_tokens,
                  "accounting": dispatch.shared.accounting()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--dsh", type=Path)
    parser.add_argument("--approved-registration")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    require(args.registration.resolve().is_relative_to(ROOT), "Use an isolated MCP research registration")
    if args.approved_registration:
        require(not args.inputs and not args.dsh and args.output and args.output.resolve().parent == ROOT,
                "Use a new MCP batch root")
        execute(args.registration, args.approved_registration, args.output.resolve())
    else:
        require(args.inputs and args.dsh and not args.output, "Supply registration inputs and installed DSH")
        register(args.inputs.resolve(), args.dsh.resolve(), args.registration.resolve())


if __name__ == "__main__":
    main()
