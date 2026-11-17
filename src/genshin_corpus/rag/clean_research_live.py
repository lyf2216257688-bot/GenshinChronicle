"""Explicit finite G5 authorization; no second sender or retry mechanism."""
import argparse
from pathlib import Path
import time

from ..evidence_io import digest, read, require, seal, sha, verify
from ..generation.clean_chat import ENDPOINT, MODEL, ROUTES, DEFAULT_ROUTE
from ..generation.clean_chat_transport import ChatSessions
from ..retrieval.clean_based.runtime_v2 import LocalPipeline, JsonWorker
from .clean_store import put
from .clean_research import VERSION, TOOLS, code_identity, fixture_settings, verify_data


def validate_settings(s):
    g6 = s.get("generation_profile") == "gc-g6-official-1"
    require(s.get("retrieval_combination", "S0") in ({"S0", "S2"} if g6 else {"S0"}), "EXPLICIT_G6_REQUIRED_FOR_S2")
    if s.get("generation_profile") in {"gc-g5-official-2", "gc-g5-official-3", "gc-g5-official-4", "gc-g6-official-1"}:
        revised = s["generation_profile"] in {"gc-g5-official-3", "gc-g5-official-4", "gc-g6-official-1"}
        require(s.get("engine") == VERSION and s.get("mode") == "LIVE"
            and s.get("route") == DEFAULT_ROUTE and s.get("thinking") == "dynamic", "G5_OFFICIAL_SCOPE_REQUIRED")
        l = s["limits"]
        require(set(l) == {"campaign_requests", "campaign_tokens", "turn_requests", "turn_tokens"}
            and all(type(v) is int and v > 0 for v in l.values())
            and l["campaign_requests"] <= (20 if g6 else 10 if revised else 8) and 2 <= l["turn_requests"] <= 6
            and s["context_tokens"] <= 196608
            and l["campaign_tokens"] == l["campaign_requests"] * s["context_tokens"]
            and l["turn_tokens"] == l["turn_requests"] * s["context_tokens"], "FINITE_OFFICIAL_LIMITS_REQUIRED")
        require(s["roles"] == {"simple": {"thinking": "disabled", "output_tokens": 4096},
            "research": {"thinking": "disabled", "output_tokens": 8192},
            "analysis": {"thinking": "enabled", "output_tokens": 16384},
            "synthesis": {"thinking": "enabled", "output_tokens": 32768}}, "OFFICIAL_ROLE_LIMITS_CHANGED")
        bounds = {"timeout_seconds": 300, "worker_timeout_seconds": 1800, "max_searches": 6,
            "max_tools": 16, "max_rounds": 5, "max_reads": 48, "initial_reads": 6,
            "research_seconds": 3600, "feedback_bytes": 40000, "history_bytes": 24000,
            "recent_turns": 2, "excerpt_chars": 160}
        require(all(type(s.get(k)) is int and 0 < s[k] <= v for k,v in bounds.items()), "OFFICIAL_HARD_LIMIT_REQUIRED")
        ceiling = 5 if g6 else 2.5 if revised else 2
        require(s.get("fee_risk_usd") == ceiling and l["campaign_tokens"] * 1.2 / 1000000 <= ceiling,
                "OFFICIAL_LIST_PRICE_RISK_CEILING")
        if revised: require(type(s.get("tool_error_corrections")) is int and 0 <= s["tool_error_corrections"] <= 2,
                            "FINITE_TOOL_CORRECTIONS_REQUIRED")
        if s["generation_profile"] in {"gc-g5-official-4", "gc-g6-official-1"}:
            require(s.get("stop_policy") == "evidence-value-1", "G5_STOP_POLICY_CHANGED")
        return
    require(s.get("engine") == VERSION and s.get("mode") == "LIVE" and s.get("thinking") == "disabled",
            "G5_LIVE_SCOPE_REQUIRED")
    l = s["limits"]
    require(set(l) == {"campaign_requests", "campaign_tokens", "turn_requests", "turn_tokens"}
            and all(type(n) is int and n > 0 for n in l.values())
            and l["campaign_requests"] <= 10 and 2 <= l["turn_requests"] <= 4
            and l["campaign_tokens"] == l["campaign_requests"] * s["context_tokens"]
            and l["turn_tokens"] == l["turn_requests"] * s["context_tokens"], "FINITE_G5_LIMITS_REQUIRED")
    bounds = {"context_tokens": 67072, "output_tokens": 2048, "timeout_seconds": 120,
        "worker_timeout_seconds": 1800, "max_searches": 4, "max_tools": 8, "max_rounds": 3,
        "max_reads": 32, "initial_reads": 6, "research_seconds": 1800, "feedback_bytes": 26000,
        "history_bytes": 16000, "recent_turns": 2, "excerpt_chars": 160}
    require(all(type(s.get(k)) is int and 0 < s[k] <= v for k, v in bounds.items()), "G5_LIMIT_INCREASE_REJECTED")


def prepare(store, repo, authorization, *, requests=8, seconds=7200, stage="g5", turn_requests=None):
    require(stage in {"g5", "g6"}, "Unknown preparation stage")
    require(type(requests) is int and 0 < requests <= (20 if stage == "g6" else 10)
            and type(seconds) is int and 0 < seconds <= 7200
            and isinstance(authorization, str) and authorization.strip(), "FINITE_AUTHORIZATION_REQUIRED")
    repo = Path(repo).resolve()
    config = read(repo / "config/clean-generation-v1.json")
    route = ROUTES[config["route"]]
    require(config["route"] == DEFAULT_ROUTE and config["endpoint"] == route["endpoint"]
        and config["model"] == route["model"] and config["api_key_env"] == route["key_env"], "OFFICIAL_CONFIG_ROUTE_CHANGED")
    settings = {**fixture_settings(), "mode": "LIVE", "campaign": "g5-official-" + digest(str(store.root))[:20],
        "generation_profile": config["version"], "route": config["route"], "thinking": config["thinking"],
        "roles": config["roles"], "context_tokens": config["context_tokens"], "output_tokens": 32768,
        "timeout_seconds": config["timeout_seconds"], "history_bytes": 24000, "worker_timeout_seconds": 1800,
        "max_searches": 6, "max_tools": 16, "max_rounds": 5, "max_reads": 48, "research_seconds": 3600,
        "feedback_bytes": 40000, "fee_risk_usd": 2.5, "tool_error_corrections": config["tool_error_corrections"],
        "stop_policy": config["stop_policy"]}
    if stage == "g6":
        settings.update(generation_profile="gc-g6-official-1", retrieval_combination="S0",
                        campaign="g6-official-" + digest(str(store.root))[:20], fee_risk_usd=5)
    per_turn = min(6, requests) if turn_requests is None else turn_requests
    require(type(per_turn) is int and 2 <= per_turn <= min(6, requests)
            and (turn_requests is None or stage == "g6"), "FINITE_G6_TURN_LIMIT_REQUIRED")
    settings["limits"] = {"campaign_requests": requests, "campaign_tokens": requests * settings["context_tokens"],
        "turn_requests": per_turn, "turn_tokens": per_turn * settings["context_tokens"]}
    validate_settings(settings)
    evidence = repo / "data/generation/g5-official-diagnostic-20261114/call"
    verify(evidence)
    off = read(evidence / "result.json")
    off_profile = read(evidence / "plan.json")["manifest"]["profile"]
    require(off["state"] == "CONTENT_VERIFIED" and off_profile["endpoint"] == route["endpoint"]
        and off_profile["model"] == route["model"] and off_profile["thinking"] == "disabled"
        and off_profile["stream"] is True, "OFFICIAL_DISABLED_TOOL_EVIDENCE_REQUIRED")
    thinking = repo / "data/generation/g3-official-thinking-20261114"
    verify(thinking / "audit")
    caps = read(thinking / "capabilities.json")["capabilities"]
    require(any(c["status"] == "OBSERVED_LIMITED_PASS" and "sse_thinking_native" in c["capability"]
        and all(p["endpoint"] == route["endpoint"] and p["model"] == route["model"]
            and p["thinking"] == "enabled" for p in c["profiles"]) for c in caps), "OFFICIAL_THINKING_TOOLS_EVIDENCE_REQUIRED")
    from ..retrieval.clean_based.runtime_v2 import code_identity as upstream
    permit = {"version": VERSION, "live_enabled": True, "data_root": str(store.root), "repo": str(repo),
        "campaign": settings["campaign"], "settings": settings, "authorization": authorization,
        "expires_at": time.time() + seconds, "endpoint": route["endpoint"], "model": route["model"], "code": code_identity(),
        "upstream_code": upstream(), "tool_schema": digest(TOOLS),
        "assets": {n: sha(repo / n) for n in ("config/clean-retrieval-v1.json", "config/index-selected-v1.json",
                                             "config/reranking-selected-v1.json", "config/clean-generation-v1.json")},
        "protocol_evidence": {"path": str(evidence), "complete_sha256": sha(evidence / "complete.json")},
        "thinking_evidence": {"path": str(thinking / "audit"), "complete_sha256": sha(thinking / "audit/complete.json"),
            "capabilities_path": str(thinking / "capabilities.json"), "capabilities_sha256": sha(thinking / "capabilities.json")},
        "max_attempts": 1, "unknown_retries": 0, "format_retries": 0,
        "fee_control": "requests_and_conservative_tokens_actual_fee_unknown",
        "profile_status": "G5_NEW_SCHEMA_PROMPT_FINITE_VALIDATION_AUTHORIZED"}
    if stage == "g6":
        permit["retrieval_combinations"] = ["S0", "S2"]
        permit["profile_status"] = "G6_PAIRED_S0_S2_FINITE_VALIDATION_AUTHORIZED"
        for name in ("config/clean-generation-s2-v1.json", "config/recall-experiment-v1.json"):
            permit["assets"][name] = sha(repo / name)
    put(store.root / "live/permit.json", permit)
    ResearchSessions(store, settings["campaign"], settings["limits"], permit=permit)
    return settings


def permit_for(store, settings):
    validate_settings(settings)
    require((store.root / "live/permit.json").is_file(), "G5_FINITE_PERMIT_REQUIRED")
    p = read(store.root / "live/permit.json")
    expected = settings
    if settings.get("generation_profile") == "gc-g6-official-1":
        require(p.get("retrieval_combinations") == ["S0", "S2"], "G6_COMBINATIONS_CHANGED")
        expected = {**settings, "retrieval_combination": "S0"}
    require(p["version"] == VERSION and p["settings"] == expected and p["data_root"] == str(store.root)
            and p["campaign"] == settings["campaign"] and p["live_enabled"] is True, "G5_PERMIT_SCOPE_CHANGED")
    revision = store.root / "live/code-revision"
    if revision.exists():
        verify(revision)
        updated = read(revision / "permit.json"); change = updated["code_revision"]
        require(change["base_permit_sha256"] == sha(store.root / "live/permit.json")
            and set(updated) == set(p) | {"code_revision"}
            and {k: v for k,v in updated.items() if k not in {"code", "code_revision"}} ==
                {k: v for k,v in p.items() if k != "code"}, "G5_CODE_REVISION_EXPANDED_SCOPE")
        for path, expected in change["sealed_calls"].items():
            require(sha(store.root / path) == expected, "G5_CODE_REVISION_PRIOR_CALL_CHANGED")
        p = updated
    return p


class ResearchSessions(ChatSessions):
    def reconcile(self, opid):
        plan = self.operation(opid)
        if list((self.root / "ledger" / plan["fingerprint"] / "recoveries").glob("*/reservation.json")):
            from .clean_research_retry import reconcile_origin
            return reconcile_origin(self, opid)
        return super().reconcile(opid)

    def _admit_live(self, plan):
        p = self.permit
        route = ROUTES[p["settings"].get("route", "tokenmetro")]
        from ..retrieval.clean_based.runtime_v2 import code_identity as upstream
        require(p == permit_for(self.store, p["settings"]) and p["code"] == code_identity()
                and p["upstream_code"] == upstream() and p["data_root"] == str(self.store.root)
                and p["campaign"] == self.campaign and p["settings"]["limits"] == self.limits
                and p["endpoint"] == route["endpoint"] and p["model"] == route["model"] and p["live_enabled"] is True
                and p["tool_schema"] == digest(TOOLS) and time.time() < p["expires_at"], "G5_PERMIT_MISSING_OR_EXPIRED")
        require(not (self.store.root / "live/closed.json").exists(), "G5_CAMPAIGN_CLOSED")
        require(p["max_attempts"] == 1 and p["unknown_retries"] == p["format_retries"] == 0, "G5_NO_RETRIES")
        for n, expected in p["assets"].items(): require(sha(Path(p["repo"]) / n) == expected, "G5_ASSET_CHANGED")
        evidence = p["protocol_evidence"]
        require(sha(Path(evidence["path"]) / "complete.json") == evidence["complete_sha256"], "G5_CAPABILITY_CHANGED")
        if "thinking_evidence" in p:
            e = p["thinking_evidence"]
            verify(Path(evidence["path"])); verify(Path(e["path"]))
            require(sha(Path(e["path"]) / "complete.json") == e["complete_sha256"]
                and sha(Path(e["capabilities_path"])) == e["capabilities_sha256"], "G5_THINKING_CAPABILITY_CHANGED")
        turn = self.store.turn(plan["turn_id"]); settings = self.store.chat(turn["session"])["settings"]
        require(permit_for(self.store, settings) == p and plan["body"]["permit"] == p, "G5_PLAN_SCOPE_CHANGED")
        for path in (self.root / "operations").glob("*/prepared.json"):
            op = read(path)
            if op["operation_id"] == plan["operation_id"]: continue
            status = self.reconcile(op["operation_id"])
            require(status["status"] != "UNKNOWN", "G5_CAMPAIGN_UNKNOWN_STOP")
            if status["status"] == "sealed":
                require(status["result"]["state"] == "CONTENT_VERIFIED"
                        and status["result"].get("kind") in {"tool_calls", "final_text", "tool_argument_errors"}, "G5_CAMPAIGN_PROTOCOL_STOP")
        # Persisted source-bound preview precedes reservation; verify all source
        # artifacts and exact schema/whole-message limits again at admission.
        manifest = plan["body"]["manifest"]
        previews = [self.store.load(r) for n in range(settings["limits"]["turn_requests"])
                    if (r := self.store.published_artifact(turn["id"], "g5-request-" + str(n)))]
        envelope = next((v for v in previews if v["manifest"] == manifest), None)
        from .clean_research import role_for
        data = verify_data(self.store, envelope["data_ref"]) if envelope is not None else {}
        role = role_for(settings, final=envelope is not None and envelope["phase"] == "final", analysis=data.get("analysis", False))
        require(envelope is not None and manifest["wire"]["thinking"] == {"type": role["thinking"]}
                and manifest["profile"]["endpoint"] == route["endpoint"]
                and manifest["wire"]["model"] == route["model"] and manifest["wire"]["stream"] is True
                and manifest["wire"].get("tools", []) == ([] if envelope["phase"] == "final" else TOOLS),
                "G5_REQUEST_OUTSIDE_VERIFIED_PREVIEW")
        require((manifest.get("tool_argument_errors") == "feedback") ==
                (bool(settings.get("tool_error_corrections")) and envelope["phase"] != "final"),
                "G5_TOOL_ERROR_POLICY_CHANGED")
        require(time.time() < data["started_at"] + settings["research_seconds"], "G5_TURN_DEADLINE")
        from .clean_conversation_graph import verify_sources
        verify_sources(self.store, envelope["sources_ref"])
        require(manifest["limits"] == {"input_bytes": settings["context_tokens"] - role["output_tokens"],
                "context_tokens": settings["context_tokens"], "output_tokens": role["output_tokens"],
                "timeout_seconds": settings["timeout_seconds"]}, "G5_REQUEST_LIMITS_CHANGED")
        put(self.store.root / "live/previews" / (plan["operation_id"] + ".json"), envelope)

    def reserve(self, *args):
        self._admit_live(read(self.root / "mappings" / (args[0] + ".json")))
        return super().reserve(*args)


class ResearchPipeline:
    """Reuse the verified CPU reader within a turn; GPUs still exit per query."""
    def __init__(self, store, tid, repo):
        self.store, self.tid, self.repo = store, tid, Path(repo)
        self.pipeline = None; self._reader = None; self.worker_timings = []

    @property
    def reader(self):
        if self.pipeline: return self.pipeline.reader
        if self._reader is None:
            from .clean_live import LivePipeline
            self._reader = LivePipeline(self.store, self.tid, self.repo)
        return self._reader.reader

    def run(self, query, *, parents=(), evidence_refs=()):
        settings = self.store.chat(self.store.turn(self.tid)["session"])["settings"]
        require(query.combination == settings.get("retrieval_combination", "S0"), "RESEARCH_COMBINATION_CHANGED")
        config = "config/clean-generation-s2-v1.json" if query.combination == "S2" else "config/clean-retrieval-v1.json"
        output = self.store.root / "local_queries" / self.tid / query.query_id
        if output.exists():
            verify(output); result = read(output / "ranked.json")
            require(result["query"] == query.value(), "G5_LOCAL_QUERY_REPLAY_CHANGED")
            return result
        start = time.perf_counter()
        self.worker_timings.clear()
        if self.pipeline is None:
            self.pipeline = LocalPipeline(self.repo, self.repo / config,
                self.repo / "config/reranking-selected-v1.json", output,
                worker_factory=lambda *a, **kw: TimedWorker(self.worker_timings, *a, **kw))
        else:
            self.pipeline.reader.binding.check()
            output.mkdir(parents=True, exist_ok=False); self.pipeline.output = output
            self.pipeline.resources = []
        init_seconds = time.perf_counter() - start
        result = self.pipeline.run(query, parents=parents, evidence_refs=evidence_refs)
        put(output / "g5-timing.json", {"reader_pipeline_init_seconds": init_seconds,
            "total_seconds": time.perf_counter() - start, "cpu_reader_reused": len(parents) > 0,
            "workers": self.worker_timings,
            "gpu_policy": "unchanged_serial_job_owned_workers"})
        seal(output); return result

    def close(self):
        if self.pipeline: self.pipeline.close()
        if self._reader: self._reader.close()


class TimedWorker(JsonWorker):
    """Measure unchanged worker startup, inference and confirmed shutdown."""
    def __init__(self, records, *args, **kwargs):
        self.measurement = {"module": args[2], "arguments": args[3]}
        self.measured_close = False
        records.append(self.measurement)
        tick = time.perf_counter()
        super().__init__(*args, **kwargs)
        self.measurement["ready_seconds"] = time.perf_counter() - tick

    def request(self, value):
        tick = time.perf_counter()
        try: return super().request(value)
        finally: self.measurement["request_seconds"] = time.perf_counter() - tick

    def close(self):
        tick = time.perf_counter()
        super().close()
        if not self.measured_close:
            self.measurement["confirmed_close_seconds"] = time.perf_counter() - tick
            self.measured_close = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True); parser.add_argument("--repo", default=".")
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare"); prep.add_argument("--authorization", required=True)
    prep.add_argument("--requests", type=int, default=8)
    prep.add_argument("--stage", choices=("g5", "g6"), default="g5")
    prep.add_argument("--turn-requests", type=int)
    new = sub.add_parser("new"); new.add_argument("--title", default="原神主动研究")
    new.add_argument("--combination", choices=("S0", "S2"), default="S0")
    submit = sub.add_parser("submit"); submit.add_argument("--session", required=True)
    submit.add_argument("--token", required=True); submit.add_argument("--question", required=True)
    submit.add_argument("--note-kind"); submit.add_argument("--run", action="store_true")
    cont = sub.add_parser("continue"); cont.add_argument("--turn", required=True)
    sub.add_parser("close")
    a = parser.parse_args(argv)
    from .clean_conversation import ConversationStore
    store = ConversationStore(a.root)
    if a.action == "prepare": value = prepare(store, a.repo, a.authorization, requests=a.requests, stage=a.stage,
                                             turn_requests=a.turn_requests)
    elif a.action == "new":
        settings = read(store.root / "live/permit.json")["settings"]
        if settings.get("generation_profile") == "gc-g6-official-1":
            settings = {**settings, "retrieval_combination": a.combination}
        else: require(a.combination == "S0", "EXPLICIT_G6_REQUIRED_FOR_S2")
        permit_for(store, settings)
        value = store.create_chat(a.title, settings)
    elif a.action == "close": put(store.root / "live/closed.json", {"reason": "campaign_completed"}); value = "closed"
    else:
        tid = store.enqueue(a.session, a.token, a.question, note_kind=a.note_kind) if a.action == "submit" else a.turn
        value = {"turn_id": tid}
        if a.action == "continue" or a.run:
            from .clean_research import run_turn
            pipeline = ResearchPipeline(store, tid, a.repo)
            try: value = run_turn(store, tid, pipeline, continue_turn=a.action == "continue")
            finally: pipeline.close()
    print(__import__("json").dumps(value, ensure_ascii=False))
    return 0


if __name__ == "__main__": raise SystemExit(main())
