"""Opt-in R1 discovery research. No production imports invoke this module.

Generated research data stays under a new data/retrieval run. Provider attempts
are immutable, reserved before dispatch, and never reissued after UNKNOWN.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import sha256
import gzip
import json
import math
import os
from pathlib import Path
import random
import re
import threading
import time
from typing import Any

import numpy as np

from genshin_corpus.canonical.fingerprints import sha256_json

CANONICAL = "data/canonical/mihoyo_obc/zh-cn/phase03-batch5b-p01eb-full-20260824/metadata/manifest.json"
RU = "data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/ru/metadata/manifest.json"
LEXICAL = ".local/p04-block-a-closure-20260914-r2/field-aware-lexical/metadata/manifest.json"
DENSE = ".local/p04-qwen-full-batch-beijing-20260909/final/dense/metadata/manifest.json"
VERSION = "p05-navi-r1-discovery-0.1"
IMPLEMENTATION_BYTES = Path(__file__).read_bytes()
IMPLEMENTATION_SHA256 = sha256(IMPLEMENTATION_BYTES).hexdigest()

AUTHORITY = (
    "这是《原神》真实来源文本的离线研究。只依照此次提供的正文，不用训练记忆补完人物身份。"
    "游戏内小说、传说和隐喻有解释价值，但有价值的联读不等于已证实的历史身份或因果。"
    "保留说话者、否定、条件、传说和作者推测；攻略推荐不作为剧情。"
    "只输出一个严格JSON对象，不写代码围栏或额外说明。引用refs只能使用此次提供的局部段号。"
)
PROMPTS = {
    "ordinary": AUTHORITY + (
        '独立阅读这份资料，形成普通内容画像，不设计开放问题或跨资料发现策略。'
        '输出{"summary":"200至500字具体内容概述", "refs":["u1"],'
        '"key_details":[{"text":"具体细节", "refs":["u1"],"quote":"原文连续短引文"}]}'
        '。key_details最多5条。不能仅列标签。'
    ),
    "a0": AUTHORITY + (
        '独立理解这份资料，为未来未知目标的发现保留具体信息。不得猜另一份资料的标题或身份。'
        '输出{"content":"200至500字具体内容理解","content_refs":["u1"],'
        '"supplies":[{"text":"此文能提供的具体背景/解释/信息", "refs":["u1"],"quote":"连续原文"}],'
        '"needs":[{"text":"确有依据但此文尚未解释的问题/缺口", "refs":["u1"],"quote":"连续原文"}],'
        '"motifs":[{"text":"具体象征结构/特殊机制/平行叙事，不要泛泛命运月亮爱情", "refs":["u1"],"quote":"连续原文"}],'
        '"limitations":"来源角色和证据限制"}。三个数组各最多2条，允许全部为空。'
        '不为凑数编造开放问题。问题不是原文事实，机制对应不自动构成同一人物或事件。'
    ),
    "rewrite": AUTHORITY + (
        '只根据起点正文产生6条不同的、用于找未知补充资料的查询，每条不超过180字。'
        '兼顾原文细节、可解释缺口、不同措辞和叙事机制。不能使用未在正文出现的专名或猜测目标标题。'
        '输出{"queries":["查询1","查询2","查询3","查询4","查询5","查询6"]}。'
    ),
    "judge": AUTHORITY + (
        '盲评A与B的具体联读价值。你不知道检索方法和预期答案。不要因为同名、通用意象或推荐配装判相关。'
        '输出{"verdict":"useful|tentative|reject|insufficient",'
        '"relation":"direct_reference|background|contrast|literary_parallel|duplicate|gameplay_only|none",'
        '"increment":"B具体增加什么", "claim_level":"textual|interpretive|identity_or_cause",'
        '"a_refs":["u1"],"b_refs":["u1"],"a_quote":"A连续原文短引",'
        '"b_quote":"B连续原文短引","limitations":"反解释和不足","unsupported_identity":false}。'
        '有具体锚点但尚不能确证的文学对应可判tentative，不应假装历史已证实。'
        'duplicate可以有版本对照价值，但不能当独立历史佐证。'
    ),
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def once(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def record_warming(run: Path, job: str, events):
    # An earlier circuit may stop after unauthenticated warming, before any
    # model reservation. Preserve that log when a later process warms again.
    index=0
    while True:
        suffix='' if index==0 else '-restart-'+str(index)
        try:
            once(run/'connection-warming'/(job+suffix+'.json'),{'events':events,
                'authorization_sent':False,'model_request_sent':False})
            return
        except FileExistsError:index+=1


def environment() -> dict[str, str]:
    values = dict(os.environ)
    # Read configured credentials without persisting or printing their values.
    env_path = Path.home() / ".codex" / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return values


def source_units(record: dict) -> tuple[list[dict], list[dict]]:
    """Explicit research-body policy, with every exclusion accounted for."""
    result, excluded = [], []
    memberships = record["record_metadata"].get("channel_memberships", [])
    channels = {str(c.get("channel_id")) if isinstance(c, dict) else str(c) for c in memberships}
    kind = "other"
    if "43" in channels:
        kind = "quest"
    for section in record["sections"]:
        name = section["source_metadata"].get("name", "")
        for unit in section["units"]:
            reason = None
            if unit["kind"] != "rich_text":
                reason = "non_rich_text_structural_carrier"
            text = unit.get("value", {}).get("normalized_text", "")
            if not reason and not text:
                reason = "empty_body"
            if not reason and re.search("搭配推荐|攻略|成长|突破|天赋|技能|命之座|数值|生日邮件|任务奖励|获取方式|角色关系网", name):
                reason = "gameplay_editorial_or_unselected_module"
            if not reason and kind == "quest" and name != "剧情对话":
                reason = "quest_non_dialogue_module"
            if not reason and "5" in channels and name != "相关故事":
                reason = "weapon_non_story_module"
            if not reason and "218" in channels and name not in {"生之花", "死之羽", "时之沙", "空之杯", "理之冠"}:
                reason = "artifact_non_story_module"
            if not reason and "25" in channels and not re.search("角色详细|角色故事|神之眼|神之心|邪眼", name):
                reason = "character_unselected_module"
            if not reason and len(text) < 55:
                reason = "short_metadata_or_label"
            address = {"section": section["ordinal"], "unit": unit["ordinal"]}
            if reason:
                excluded.append(address | {"reason": reason})
            else:
                result.append(address | {"section_name": name, "text": text,
                    "content_role": unit.get("content_role"), "provenance": unit.get("provenance"),
                    "parent_component_key": unit["parent_component_key"]})
    return result, excluded


def text_payload(doc: dict, *, max_chars: int | None = None) -> dict:
    segments = []
    used = 0
    for unit in doc["units"]:
        if max_chars is not None and used + len(unit["text"]) > max_chars:
            continue
        segments.append({"id": unit["id"], "section": unit["section_name"], "text": unit["text"]})
        used += len(unit["text"])
    return {"title": doc["title"], "segments": segments,
        "coverage": {"delivered_chars": used, "scoped_chars": sum(len(u["text"]) for u in doc["units"]),
                     "complete_scoped_body": len(segments) == len(doc["units"]),
                     "whole_page_semantic_coverage": False}}


def validate_profile(value: dict, payload: dict, kind: str, *, require_quote=True) -> tuple[dict, list[dict]]:
    segments = {u["id"]: u["text"] for u in payload["segments"]}
    key = "summary" if kind == "ordinary" else "content"
    if not isinstance(value, dict) or not isinstance(value.get(key), str) or not value[key].strip():
        raise ValueError("missing content profile")
    refs_key = "refs" if kind == "ordinary" else "content_refs"
    if not value.get(refs_key) or not set(value[refs_key]) <= segments.keys():
        raise ValueError("unbound content refs")
    clean = dict(value)
    rejected = []
    for field in (["key_details"] if kind == "ordinary" else ["needs", "supplies", "motifs"]):
        if not isinstance(value.get(field), list):
            raise ValueError("missing feature array " + field)
        clean[field] = []
        for item in value[field]:
            refs = item.get("refs", []) if isinstance(item, dict) else []
            quote = item.get("quote", "") if isinstance(item, dict) else ""
            quote_bound=isinstance(quote,str) and bool(quote) and any(quote in segments.get(ref,'') for ref in refs)
            if (not refs or not set(refs) <= segments.keys() or (require_quote and not quote_bound) or not item.get("text")):
                rejected.append({"field": field, "item": item, "reason": "quote_or_ref_not_bound"})
            else:
                clean[field].append(item|{'quote_binding':'exact' if quote_bound else 'UNBOUND_NOT_EVIDENCE'})
    return clean, rejected


class CircuitOpen(RuntimeError):
    pass


def proven_not_sent(terminal: dict) -> bool:
    """Only a positively observed local DNS failure, never TLS/reset/timeout."""
    diagnostics = (terminal.get("error") or {}).get("diagnostics", {})
    observations = diagnostics.get("observations", {})
    exceptions = diagnostics.get("exceptions", [])
    return bool(
        terminal.get("http_status") is None and not terminal.get("stream_complete")
        and observations.get("http_response_hook_observed") is False
        and observations.get("stream_events_observed") == 0
        and observations.get("responses_terminal_observed") is False
        and any(e.get("type") == "socket.gaierror" and e.get("codes", {}).get("errno") == 11001 for e in exceptions)
    )


def traced_not_sent(traces):
    sent=any(e.endswith('send_request_headers.started') for e in traces)
    return not sent and any(e in {'connection.connect_tcp.failed','connection.start_tls.failed'} for e in traces)


def quarantine_fingerprints(parent):
    """A paid request is identified by content, never by a new job label."""
    found={}
    for run in parent.glob('p05-navi-r1-discovery-20261025-r*'):
        for terminal_path in (run/'attempts').glob('*/terminal.json'):
            terminal=read_json(terminal_path)
            stem=terminal_path.parent
            if terminal.get('state')!='UNKNOWN' or (stem/'dispatch-reconciliation.json').exists():continue
            request=stem/'request.json'
            if not request.exists():request=stem/'sdk/request.json'
            if request.exists():found[request_fingerprint(read_json(request))]=str(stem)
    return found


def request_fingerprint(wire):
    """Ignore job labels, JSON formatting and delivery-accounting metadata."""
    instructions=wire.get('instructions')
    payload=wire.get('input')
    messages=wire.get('messages',[])
    if messages:
        instructions='\n'.join(m['content'] for m in messages if m.get('role')=='system')
        payload=[m['content'] for m in messages if m.get('role')=='user']
        payload=payload[0] if len(payload)==1 else payload
    if isinstance(payload,str):
        try:payload=json.loads(payload)
        except ValueError:pass
    def semantic(value):
        if isinstance(value,dict):return {k:semantic(v) for k,v in value.items() if k not in {'coverage','context_selection'}}
        if isinstance(value,list):return [semantic(v) for v in value]
        return value
    return sha256_json({'model':wire.get('model'),'instructions':instructions,'input':semantic(payload),
                       'embedding_parameters':wire.get('parameters')})


class Campaign:
    """Thread-safe cumulative reservations, including incomplete attempts."""
    def __init__(self, run: Path, *, audit_only=False):
        self.run = run
        self.lock = threading.RLock()
        self.config = read_json(run / "protocol.json")
        if (run / "operating-point.json").exists():
            operating = read_json(run / "operating-point.json")
            self.config["circuit"] = self.config["circuit"] | operating["circuit"]
        for amendment in sorted(run.glob('operating-point-amendment-*.json')):
            self.config['circuit']=self.config['circuit']|read_json(amendment).get('circuit',{})
        self.env = environment()
        self.clients = threading.local()
        self.quarantined=quarantine_fingerprints(run.parent)
        snapshot=run/'code-snapshots'/(IMPLEMENTATION_SHA256+'.py')
        if not audit_only and not snapshot.exists():
            snapshot.parent.mkdir(exist_ok=True)
            with snapshot.open('xb') as handle:handle.write(IMPLEMENTATION_BYTES)
        self.unknown = 0
        self.bad = 0
        self.stop = False
        self.reservations = {}
        self.terminals = {}
        for p in sorted((run / "attempts").glob("*/issued.json")):
            issued = read_json(p)
            self.reservations[p.parent.name] = issued
            terminal = p.parent / "terminal.json"
            if terminal.exists():
                self.terminals[p.parent.name] = read_json(terminal)
                reconciliation = p.parent / "dispatch-reconciliation.json"
                if reconciliation.exists():
                    evidence = p.parent / "sdk" / "terminal.json"
                    justified = evidence.exists() and proven_not_sent(read_json(evidence))
                    if not evidence.exists():
                        # SDK construction failed; no SDK request, hook, wire or
                        # stream exists. This is a local preflight, not a call.
                        justified = not (p.parent / "sdk").exists()
                    if not justified:
                        raise ValueError("unjustified attempt reclassification")
                    self.terminals[p.parent.name] = self.terminals[p.parent.name] | {
                        "state": "NOT_SENT", "usage": {"input_tokens": 0, "output_tokens": 0}}
            else:
                self.unknown += 1
        for value in self.terminals.values():
            self.unknown += value.get("state") == "UNKNOWN"

    def totals(self):
        model = embedding = tokens = spent = 0
        for key, issued in self.reservations.items():
            terminal = self.terminals.get(key, {})
            model += issued["kind"] == "model"
            embedding += issued["kind"] == "embedding"
            usage = terminal.get("usage")
            if isinstance(usage, dict) and isinstance(usage.get("input_tokens"), int) and isinstance(usage.get("output_tokens"), int):
                inp, out = usage["input_tokens"], usage["output_tokens"]
                tokens += inp + out
                spent += (inp * issued["input_rate"] + out * issued["output_rate"]) / 1e6
            else:
                tokens += issued["reserved_tokens"]
                spent += issued["reserved_rmb"]
        return {"model_attempts": model, "embedding_attempts": embedding,
            "accounted_or_reserved_tokens": tokens, "accounted_or_reserved_rmb": spent,
            "unknown_attempts": self.unknown,
            "money_basis": "conservative_accounting_rates_not_provider_invoice"}

    def reserve(self, job: str, kind: str, inp_chars: int, max_output: int):
        with self.lock:
            if self.stop or self.unknown >= self.config["circuit"]["max_unknown_attempts"]:
                raise CircuitOpen("unknown execution circuit open")
            if self.unknown >= 5 and self.unknown / max(len(self.reservations), 1) > .2:
                raise CircuitOpen("unknown execution ratio exceeds 20 percent")
            stem = self.run / "attempts" / job
            if stem.exists():
                raise CircuitOpen("immutable issued job cannot be reissued: " + job)
            totals = self.totals()
            rates = self.config["circuit"]["rates_rmb_per_million"][kind]
            # UTF-8 byte count is intentionally a generous token reservation.
            reserved = inp_chars * 3 + max_output
            cost = (inp_chars * 3 * rates[0] + max_output * rates[1]) / 1e6
            limits = self.config["circuit"]
            if (totals["model_attempts"] + (kind == "model") > limits["max_model_attempts"] or
                totals["embedding_attempts"] + (kind == "embedding") > limits["max_embedding_attempts"] or
                totals["accounted_or_reserved_tokens"] + reserved > limits["max_tokens"] or
                totals["accounted_or_reserved_rmb"] + cost > limits["max_accounting_rmb"]):
                self.stop = True
                raise CircuitOpen("cumulative reservation ceiling")
            issued = {"job": job, "kind": kind, "time": now(), "input_rate": rates[0],
                "output_rate": rates[1], "reserved_tokens": reserved, "reserved_rmb": cost,
                "implementation_sha256": IMPLEMENTATION_SHA256}
            once(stem / "issued.json", issued)
            self.reservations[job] = issued
            return stem

    def resolve(self, stem: Path, terminal: dict):
        with self.lock:
            once(stem / "terminal.json", terminal)
            self.terminals[stem.name] = terminal
            self.unknown += terminal["state"] == "UNKNOWN"
            if terminal['state']=='UNKNOWN':
                request=stem/'request.json'
                if not request.exists():request=stem/'sdk/request.json'
                if request.exists():self.quarantined[request_fingerprint(read_json(request))]=str(stem)

    def check_quarantine(self,wire):
        with self.lock:
            if request_fingerprint(wire) in self.quarantined:
                raise CircuitOpen('request content is quarantined after UNKNOWN, independent of job/cohort name')

    def model(self, job: str, kind: str, payload: dict, *, model=None, route=None):
        """Use the established SDK wire/parser, explicitly one attempt only."""
        if model is None:
            model, route = self.config["models"]["generation"]
        if route == "bailian":
            return self.qwen_call(job, kind, payload, model)
        result_path = self.run / "responses" / (job + ".json")
        if result_path.exists():
            return read_json(result_path)
        from .navi_e2_experiment import E2SDK
        # Configuration validation cannot consume a provider reservation.
        sdk = E2SDK(route, model=model, environment=self.env, max_attempts=1,
                    mode="standard", format_retry_enabled=False)
        wire = {"model": model, "instructions": PROMPTS[kind],
            "input": json.dumps(payload, ensure_ascii=False), "stream": True, "max_output_tokens": 8192}
        self.check_quarantine(wire)
        stem = self.reserve(job, "model", len(wire["input"]) + len(wire["instructions"]), 8192)
        once(stem / "input.json", {"kind": kind, "payload": payload, "prompt_sha256": sha256_json(PROMPTS[kind])})
        started = time.monotonic()
        try:
            terminal = sdk(stem / "sdk", wire, self.config["identity"], attempt_limit=1)
            raw_state = terminal["execution_state"]
            state = "NOT_SENT" if proven_not_sent(terminal) else "UNKNOWN" if raw_state == "EXECUTION UNKNOWN" else raw_state
            normalized = terminal.get("usage_normalized", {})
            usage = terminal.get("usage") if isinstance(terminal.get("usage"), dict) else None
            if state == "NOT_SENT":
                usage = {"input_tokens": 0, "output_tokens": 0}
            result = {"state": state, "model": model, "route": route, "complete": terminal["complete"],
                "usage": usage, "latency_ms": terminal["latency_ms"], "raw_state": raw_state}
            if terminal["complete"]:
                content = terminal["content"].strip()
                try:
                    value = json.loads(content)
                    if not isinstance(value, dict):
                        raise ValueError("JSON object required")
                    result["value"] = value
                except (ValueError, TypeError) as exc:
                    result["parse_error"] = type(exc).__name__
            else:
                result["error_type"] = (terminal.get("error") or {}).get("type", "incomplete")
        except Exception as exc:
            # Any exception after reservation may have reached the provider.
            result = {"state": "UNKNOWN", "complete": False, "usage": None, "error_type": type(exc).__name__,
                      "latency_ms": round((time.monotonic() - started) * 1000, 3)}
        self.resolve(stem, result)
        once(result_path, result)
        return result

    def embed(self, role: str, texts: list[str]) -> np.ndarray:
        from genshin_corpus.retrieval.qwen_embedding import (
            QwenEmbeddingRequest, DashScopeQwenEmbeddingConfig, DashScopeQwenEmbeddingTransport,
            QwenEmbeddingTransportError,
        )
        request = QwenEmbeddingRequest(role, tuple(texts))
        job = "embed-" + request.request_identity
        result_path = self.run / "embeddings" / (job + ".npz")
        if result_path.exists():
            return np.load(result_path)["vectors"]
        if self.config.get("traced_persistent_transport"):
            config = DashScopeQwenEmbeddingConfig(region=self.env["DASHSCOPE_QWEN_REGION"],
                endpoint=self.env["DASHSCOPE_QWEN_EMBEDDING_ENDPOINT"],workspace=self.env["DASHSCOPE_QWEN_WORKSPACE"],timeout_seconds=90)
            transport=DashScopeQwenEmbeddingTransport.from_environment(config,environment=self.env)
            result=self.qwen_traced(job,"embedding",transport.wire_payload(request),transport.endpoint,config.api_key_env)
            if not result.get("complete"):
                raise CircuitOpen("embedding missing; UNKNOWN never retried")
            response=transport._response_from_bytes(request,Path(result['raw_response_path']).read_bytes(),None)
            vectors=np.asarray(response.vectors,dtype=np.float32)
            if vectors.shape!=(len(texts),2048) or not np.isfinite(vectors).all():
                raise ValueError("embedding shape/finite mismatch")
            norms=np.linalg.norm(vectors,axis=1,keepdims=True)
            if (norms<=0).any():raise ValueError("zero embedding")
            vectors/=norms
            result_path.parent.mkdir(parents=True,exist_ok=True)
            with result_path.open('xb') as handle:np.savez_compressed(handle,vectors=vectors)
            return vectors
        stem = self.reserve(job, "embedding", sum(map(len, texts)), 0)
        once(stem / "request.json", request.identity_projection())
        try:
            config = DashScopeQwenEmbeddingConfig(region=self.env["DASHSCOPE_QWEN_REGION"],
                endpoint=self.env["DASHSCOPE_QWEN_EMBEDDING_ENDPOINT"], workspace=self.env["DASHSCOPE_QWEN_WORKSPACE"],
                timeout_seconds=90)
            transport = DashScopeQwenEmbeddingTransport.from_environment(config, environment=self.env)
            response = transport.embed(request)
            (stem / "response.json").write_bytes(response.raw_response_bytes)
            vectors = np.asarray(response.vectors, dtype=np.float32)
            if vectors.shape != (len(texts), 2048) or not np.isfinite(vectors).all():
                raise ValueError("embedding shape or finite check")
            if response.returned_model not in {None, request.model_id} or response.returned_role not in {None, role}:
                raise ValueError("embedding returned identity conflict")
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if (norms <= 0).any():
                raise ValueError("zero embedding")
            vectors /= norms
            raw = json.loads(response.raw_response_bytes)
            usage = raw.get("usage", {})
            tokens = usage.get("total_tokens", usage.get("input_tokens"))
            result = {"state": "RESOLVED", "usage": {"input_tokens": tokens, "output_tokens": 0} if isinstance(tokens, int) else None,
                "returned_model": response.returned_model, "returned_role": response.returned_role}
            self.resolve(stem, result)
            result_path.parent.mkdir(parents=True, exist_ok=True)
            with result_path.open("xb") as handle:
                np.savez_compressed(handle, vectors=vectors)
            return vectors
        except Exception as exc:
            raw = getattr(exc, "raw_response_bytes", None)
            if raw:
                (stem / "error-response.bin").write_bytes(raw)
            state = "REJECTED" if getattr(exc, "status_code", None) in {400,401,403,404,422,429} else "UNKNOWN"
            self.resolve(stem, {"state": state, "usage": None, "error_type": type(exc).__name__})
            raise CircuitOpen("embedding failed; no automatic reissue") from None

    def qwen_call(self, job: str, kind: str, payload: dict, model_id: str):
        """Independent existing dated Qwen route, isolated research parameters."""
        from genshin_corpus.generation.generation import (
            BailianControlConfig, BailianOpenAICompatibleTransport, workspace_from_bailian_base_url,
            BASELINE_QWEN_MODEL_ID, EXACT_SNAPSHOT_POLICY, REQUESTED_ALIAS_POLICY,
        )
        import httpx
        result_path = self.run / "responses" / (job + ".json")
        if result_path.exists():
            return read_json(result_path)
        config = BailianControlConfig(region="cn-beijing", endpoint=self.env["BAILIAN_BASE_URL"],
            workspace=workspace_from_bailian_base_url(self.env["BAILIAN_BASE_URL"]),
            model_id=model_id, model_reference_policy=EXACT_SNAPSHOT_POLICY if model_id == BASELINE_QWEN_MODEL_ID else REQUESTED_ALIAS_POLICY,
            max_output_tokens=4096, max_attempts=1, timeout_seconds=120)
        transport = BailianOpenAICompatibleTransport.from_environment(config, environment=self.env)
        body = {"model": config.model_id, "messages": [{"role": "system", "content": PROMPTS[kind]},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            "generation_parameters": {"enable_thinking": False, "temperature": 0.0, "max_output_tokens": 4096}}
        wire = transport.wire_payload(body)
        self.check_quarantine(wire)
        if self.config.get("traced_persistent_transport"):
            return self.qwen_traced(job, kind, wire, transport.endpoint, config.api_key_env)
        stem = self.reserve(job, "model", len(json.dumps(wire, ensure_ascii=False)), 4096)
        once(stem / "request.json", wire)
        started = time.monotonic()
        try:
            with httpx.Client(timeout=120, follow_redirects=False) as client:
                response = client.post(transport.endpoint, json=wire,
                    headers={"Authorization": "Bearer " + self.env[config.api_key_env]})
                status, raw = response.status_code, response.content
                if any(value.encode() in raw for key, value in self.env.items() if key.endswith("_API_KEY") and value):
                    raise ValueError("credential echo refused")
                (stem / "raw-response.bin").write_bytes(raw)
                once(stem / "response-metadata.json", {"status": status})
                parsed = json.loads(raw)
            if status != 200:
                state = "REJECTED" if status in {400,401,403,404,422,429} else "UNKNOWN"
                result = {"state": state, "complete": False, "usage": None, "http_status": status}
            else:
                usage = parsed.get("usage", {})
                normalized = {"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")}
                choice = parsed["choices"][0]
                complete = choice["finish_reason"] == "stop" and parsed.get("model") == config.model_id
                result = {"state": "RESOLVED", "complete": complete, "usage": normalized,
                    "returned_model": parsed.get("model"), "finish_reason": choice["finish_reason"],
                    "model": model_id, "route": "bailian"}
                if complete:
                    try:
                        result["value"] = json.loads(choice["message"]["content"])
                    except ValueError:
                        result["parse_error"] = "invalid_JSON"
        except Exception as exc:
            result = {"state": "UNKNOWN", "complete": False, "usage": None, "error_type": type(exc).__name__}
        result["latency_ms"] = round((time.monotonic() - started) * 1000, 3)
        self.resolve(stem, result)
        once(result_path, result)
        return result

    def qwen_traced(self, job, kind, wire, endpoint, key_env):
        """Persistent TLS, with proof-based pre-header connection retry only.

        Connection warming has no auth or model body and cannot generate tokens.
        A sent request without a complete response remains UNKNOWN forever.
        """
        import httpx
        self.check_quarantine(wire)
        result_path=self.run/"responses"/(job+".json")
        client=getattr(self.clients,"client",None)
        if client is None:
            proxy=self.env.get('NAVI_R1_PROXY_URL')
            if proxy and proxy!='http://127.0.0.1:7890':
                raise ValueError('unverified experimental proxy URL')
            client=httpx.Client(timeout=httpx.Timeout(150,connect=20),follow_redirects=False,proxy=proxy,trust_env=False,
                limits=httpx.Limits(max_connections=1,max_keepalive_connections=1,keepalive_expiry=120))
            self.clients.client=client
            warmed=False
            warm_events=[]
            for index in range(6):
                try:
                    response=client.get(endpoint.rsplit('/chat/completions',1)[0],timeout=httpx.Timeout(10,connect=5))
                    warm_events.append({"index":index,"status":response.status_code})
                    warmed=True
                    break
                except httpx.TransportError as exc:
                    warm_events.append({"index":index,"error_type":type(exc).__name__})
                    time.sleep(.5)
            record_warming(self.run,job,warm_events)
            if not warmed:
                return {"state":"NOT_SENT","complete":False,"usage":{"input_tokens":0,"output_tokens":0},"error_type":"connection_warming_exhausted"}
        result=None
        for attempt in range(1,4):
            name=job+"-connection-"+str(attempt)
            stem=self.reserve(name,"embedding" if kind=='embedding' else "model",len(json.dumps(wire,ensure_ascii=False)),0 if kind=='embedding' else 4096)
            once(stem/"request.json",wire)
            traces=[]
            def trace(event,info):
                # Never persist info: it can contain authentication headers.
                traces.append(event)
            started=time.monotonic()
            try:
                request=client.build_request("POST",endpoint,json=wire,
                    headers={"Authorization":"Bearer "+self.env[key_env]})
                request.extensions["trace"]=trace
                response=client.send(request)
                raw=response.content
                if any(value.encode() in raw for key,value in self.env.items() if key.endswith('_API_KEY') and value):
                    raise ValueError("credential echo refused")
                (stem/"raw-response.bin").write_bytes(raw)
                once(stem/"response-metadata.json",{"status":response.status_code})
                parsed=json.loads(raw)
                if response.status_code!=200:
                    result={"state":"REJECTED" if response.status_code in {400,401,403,404,422,429} else "UNKNOWN",
                            "complete":False,"usage":None,"http_status":response.status_code}
                else:
                    usage=parsed.get("usage",{})
                    if kind=='embedding':
                        tokens=usage.get('total_tokens',usage.get('input_tokens'))
                        result={'state':'RESOLVED','complete':True,'usage':{'input_tokens':tokens,'output_tokens':0},
                            'model':wire['model'],'returned_model':parsed.get('model'),'route':'dashscope',
                            'raw_response_path':str(stem/'raw-response.bin')}
                        if parsed.get('model') not in {None,wire['model']} or not parsed.get('output',{}).get('embeddings'):
                            result['complete']=False
                    else:
                        choice=parsed["choices"][0]
                        complete=choice["finish_reason"]=='stop' and parsed.get('model')==wire['model']
                        result={"state":"RESOLVED","complete":complete,"usage":{
                            "input_tokens":usage.get('prompt_tokens'),"output_tokens":usage.get('completion_tokens')},
                            "model":wire['model'],"returned_model":parsed.get('model'),"route":"bailian",
                            "finish_reason":choice['finish_reason']}
                        if complete:
                            try:result['value']=json.loads(choice['message']['content'])
                            except ValueError:result['parse_error']='invalid_JSON'
            except Exception as exc:
                sent=any(e.endswith('send_request_headers.started') for e in traces)
                known_pre_send=traced_not_sent(traces)
                result={"state":"NOT_SENT" if known_pre_send else "UNKNOWN","complete":False,
                    "usage":{"input_tokens":0,"output_tokens":0} if known_pre_send else None,
                    "error_type":type(exc).__name__,"header_send_started":sent}
            result['latency_ms']=round((time.monotonic()-started)*1000,3)
            once(stem/'transport-trace.json',{"events":traces,"credential_values_persisted":False})
            self.resolve(stem,result)
            if result['state']!='NOT_SENT':break
            time.sleep(.5)
        once(result_path,result)
        return result

    def qwen_judge(self, job: str, payload: dict):
        model, route = self.config["models"]["independent_judge"]
        return self.qwen_call(job, "judge", payload, model)


def parallel(tasks, function, workers=4):
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(function, task): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            try:
                results[str(task)] = future.result()
            except Exception as exc:
                results[str(task)] = {"error_type": type(exc).__name__, "error": str(exc)[:200]}
            print(json.dumps({"completed": len(results), "total": len(tasks), "task": str(task)}, ensure_ascii=False), flush=True)
    return results


# These reference proposals are evaluator-only. No profile/query call receives
# this collection, expected labels, target IDs, target titles or pair rationales.
REFERENCE_PROPOSALS = [
    ("D01", "dev", "witch", "1682", ["1563"], "interpretive"),
    ("D02", "dev", "witch", "1563", ["1983", "2617", "2624"], "interpretive"),
    ("D03", "dev", "snow", "1362", ["1377"], "background"),
    ("D04", "dev", "snow", "1629", ["1362"], "contrast"),
    ("D05", "dev", "spring", "646", ["7480"], "direct_reference"),
    ("D06", "dev", "time_tree", "3470", ["3587"], "literary_parallel"),
    ("D07", "dev", "watatsumi", "3227", ["3611", "3508"], "background"),
    ("D08", "dev", "seelie", "1148", ["5404"], "literary_parallel"),
    ("D09", "dev", "watatsumi", "3226", ["3470"], "duplicate_control"),
    ("D10", "dev", "witch", "1563", ["55"], "negative_identity_from_gear"),
    ("D11", "dev", "seelie", "1146", ["1221"], "negative_generic_moon_fairy"),
    ("D12", "dev", "spring", "1221", ["646", "7480"], "interpretive"),
    ("H01", "holdout", "orchestra", "1638", ["207", "176"], "background"),
    ("H02", "holdout", "alley_thief", "1683", ["1684", "1685"], "background"),
    ("H03", "holdout", "stone_dragon", "1288", ["1987", "4008"], "background"),
    ("H04", "holdout", "thunderbird", "1632", ["3121"], "interpretive"),
    ("H05", "holdout", "narzissenkreuz", "6350", ["500037"], "literary_parallel"),
    ("H06", "holdout", "fox_teacher", "2321", ["2288", "2555"], "background"),
    ("H07", "holdout", "funeral_fire", "1612", ["1627"], "background"),
    ("H08", "holdout", "orchestra", "176", ["1638", "207"], "background"),
    ("H09", "holdout", "alley_thief", "1685", ["1683", "1684"], "background"),
    ("H10", "holdout", "stone_dragon", "1288", ["296"], "negative_all_dragons_same"),
    ("H11", "holdout", "thunderbird", "1632", ["2555"], "negative_thunder_equivalence"),
    ("H12", "holdout", "narzissenkreuz", "6350", ["7045"], "negative_theatre_equivalence"),
]


def record_reader(root: Path):
    manifest = read_json(root / CANONICAL)
    entries = {e["parsed_identity_key"].rsplit(":", 1)[-1]: e for e in manifest["records"]}
    def read(cid):
        entry = entries[str(cid)]
        data = (root / entry["canonical_record_path"]).read_bytes()
        if sha256(data).hexdigest() != entry["canonical_record_sha256"]:
            raise ValueError("Canonical record integrity failure")
        return json.loads(data), entry
    return manifest, entries, read


def split_occurrence(unit, limit=8000):
    text = unit["text"]
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + limit // 2, end)
            if boundary > start:
                end = boundary + 1
        yield unit | {"text": text[start:end], "start": start, "end": end,
                      "whole_occurrence": start == 0 and end == len(text)}
        start = end


def batches(doc, limit=14000):
    result, current, length = [], [], 0
    for unit in doc["units"]:
        if current and length + len(unit["text"]) > limit:
            result.append(current)
            current, length = [], 0
        current.append(unit)
        length += len(unit["text"])
    if current:
        result.append(current)
    return result


def stage_prepare(run: Path):
    root = Path.cwd()
    run.mkdir(parents=True, exist_ok=False)
    manifest, entries, read = record_reader(root)
    proposals = [dict(zip(["id", "split", "family", "source", "targets", "kind"], row)) for row in REFERENCE_PROPOSALS]
    required = {p["source"] for p in proposals} | {cid for p in proposals for cid in p["targets"]}
    selected = {}
    def admit(cid, required=False):
        record, entry = read(cid)
        units, excluded = source_units(record)
        if not units:
            if required:
                raise ValueError("required reference source has no scoped body: " + cid)
            return False
        # Distractors remain complete within the same declared body policy.
        if not required and sum(len(u["text"]) for u in units) > 28000:
            return False
        split = [part for unit in units for part in split_occurrence(unit)]
        for index, unit in enumerate(split, 1):
            unit["id"] = "u" + str(index)
        selected[cid] = {"cid": cid, "title": record["record_metadata"]["name"],
            "record_id": record["record_id"], "source_identity": record["source_identity"],
            "canonical_path": entry["canonical_record_path"], "canonical_sha256": entry["canonical_record_sha256"],
            "channels": record["record_metadata"].get("channel_memberships", []),
            "units": split, "excluded": excluded,
            "source_layout": record["record_metadata"].get("source_template_layout", {}),
            "reading_note": "Source presentation/ordinals are not in-world chronology; no branch path inferred."}
        return True
    for cid in sorted(required, key=int):
        admit(cid, True)
    # Source-category stratified deterministic distractors, never selected by
    # observed method performance. Deliberate hard distractors are also frozen.
    extra = ["3158", "1601", "4335", "3386", "3539", "1983", "2617", "3867", "171", "2289", "239", "197", "418", "177"]
    for cid in extra:
        if cid not in selected:
            admit(cid)
    strata = defaultdict(list)
    all_ids = list(entries)
    random.Random(20261025).shuffle(all_ids)
    for cid in all_ids:
        record, entry = read(cid)
        ch = set(map(str, record["record_metadata"].get("channel_memberships", [])))
        group = next((c for c in ["68", "5", "218", "25", "20", "43", "13"] if c in ch), None)
        if group and not record["record_metadata"].get("name", "").startswith("【原神"):
            strata[group].append(cid)
    for offset in range(max(map(len, strata.values()))):
        for group in sorted(strata):
            if len(selected) >= 128:
                break
            if offset < len(strata[group]):
                cid = strata[group][offset]
                if cid not in selected:
                    admit(cid)
        if len(selected) >= 128:
            break
    if len(selected) != 128:
        raise ValueError("exact 128 source scope unavailable")
    # Verify the official artifact, scan every row, retain compact official
    # locators and bind all selected rich-text occurrences without invented IDs.
    rm = read_json(root / RU)
    rupath = (root / RU).parent.parent / rm["artifacts"]["retrieval_units"]["path"]
    if file_hash(rupath) != rm["artifacts"]["retrieval_units"]["sha256"]:
        raise ValueError("RU artifact integrity failure")
    locators, occurrence_rows = {}, defaultdict(list)
    with gzip.open(rupath, "rt", encoding="utf-8") as handle:
        for count, line in enumerate(handle, 1):
            row = json.loads(line)
            a = row["source"]["canonical_address"]
            cid = a["source_identity_key"].rsplit(":", 1)[-1]
            locators[row["unit_id"]] = [cid, a["section_ordinal"], a["canonical_unit_ordinal"], a["canonical_unit_kind"]]
            if cid in selected and a["canonical_unit_kind"] == "rich_text":
                occurrence_rows[(cid, a["section_ordinal"], a["canonical_unit_ordinal"])].append(row)
    if count != rm["artifacts"]["retrieval_units"]["row_count"] or len(locators) != count:
        raise ValueError("official RU count or unique-ID failure")
    for cid, doc in selected.items():
        original, _ = read(cid)
        for unit in doc["units"]:
            rows = sorted(occurrence_rows[(cid, unit["section"], unit["unit"])], key=lambda r: r["fragment_selector"].get("fragment_index", 0))
            original_text = next(u for s in original["sections"] if s["ordinal"] == unit["section"] for u in s["units"] if u["ordinal"] == unit["unit"])["value"]["normalized_text"]
            if "".join(r["retrieval_visible_text"] for r in rows) != original_text:
                raise ValueError("incomplete RU body mapping")
            unit["official_unit_ids"] = [r["unit_id"] for r in rows]
            unit["occurrence_text_sha256"] = sha256(original_text.encode()).hexdigest()
    corpus = {"documents": list(selected.values()), "scope": "128 source pages; explicitly selected narrative bodies",
        "selection_seed": 20261025, "selected_occurrences": sum(len(d["units"]) for d in selected.values())}
    protocol = {"version": VERSION, "created_at": now(), "source_manifest_sha256": file_hash(root / CANONICAL),
        "ru_build_identity": rm["build_identity"], "ru_artifact_sha256": file_hash(rupath),
        "corpus_identity": sha256_json(corpus), "references_identity": sha256_json(proposals),
        "prompts": PROMPTS, "prompt_policy": "Frozen before any provider call; no held-out prompt tuning",
        "models": {"generation": ["deepseek-v4.1-flash", "jizhi"], "independent_judge": ["glm-5.3-flash", "jizhi_glm"],
                   "secondary_judge": ["gpt-6-luna", "tokenmetro"], "embedding": "qwen3.7-text-embedding"},
        "circuit": {"max_model_attempts": 1800, "max_embedding_attempts": 2400, "max_tokens": 25000000,
            "max_accounting_rmb": 500, "max_unknown_attempts": 3,
            "rates_rmb_per_million": {"model": [15, 80], "embedding": [5, 0]}},
        "failure_policy": "No UNKNOWN retry, no provider fallback. Isolated failed jobs stay missing. Three UNKNOWN attempts stop paid dispatch. Resolved invalid outputs stay failed; no silent repairs.",
        "retrieval": {"queries_per_method": 6, "raw_ru_depth": 500, "final_document_k": [5,10,20], "rrf_k": 60,
            "bridge": "two direct queries plus four queries from two discovered intermediate documents; six total",
            "full_scope": "all 535802 raw RUs; semantic profiles only on the 128 frozen documents"},
        "reference_limits": "Operator-independent model review, not independent human acceptance. Targets hidden from generators; analyst constructed corpus is an enriched research set, not random corpus recall."}
    protocol["identity"] = sha256_json(protocol)
    once(run / "protocol.json", protocol)
    once(run / "corpus.json", corpus)
    once(run / "evaluator-only" / "reference-proposals.json", proposals)
    once(run / "input-snapshot" / "current-phase-before-run.json", {"sha256": file_hash(root / "docs/current-phase.md")})
    with gzip.open(run / "official-locators.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(locators, handle, ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({"documents": len(selected), "body_chars": sum(len(u["text"]) for d in selected.values() for u in d["units"]),
        "batches": sum(len(batches(d)) for d in selected.values()), "cases": len(proposals), "protocol": protocol["identity"]}), flush=True)


def stage_canary(run: Path):
    campaign = Campaign(run)
    docs = {d["cid"]: d for d in read_json(run / "corpus.json")["documents"]}
    primary = campaign.model("canary-a0", "a0", text_payload(docs["1682"]))
    if "value" in primary:
        validate_profile(primary["value"], text_payload(docs["1682"]), "a0")
    independent = campaign.model("canary-independent", "judge", {"A": text_payload(docs["1362"]), "B": text_payload(docs["1377"])},
        model="glm-5.3-flash", route="jizhi_glm")
    second = campaign.model("canary-secondary", "judge", {"A": text_payload(docs["1362"]), "B": text_payload(docs["1377"])},
        model="gpt-6-luna", route="tokenmetro")
    vectors = campaign.embed("query", ["离乡求学后归来，灾厄夺走重要之人，随后以生命为火焚烧魔物"])
    once(run / "canary-results.json", {"primary": primary, "independent": independent, "secondary": second,
        "embedding_shape": list(vectors.shape), "totals": campaign.totals()})
    print(json.dumps({"primary": primary.get("state"), "independent": independent.get("state"),
        "secondary": second.get("state"), "primary_valid": "value" in primary, "totals": campaign.totals()}), flush=True)


def stage_preflight_reconcile(run: Path):
    reconciled = []
    for name in ["canary-a0", "canary-independent", "canary-secondary"]:
        stem = run / "attempts" / name
        sdk = stem / "sdk" / "terminal.json"
        if sdk.exists():
            if not proven_not_sent(read_json(sdk)):
                raise CircuitOpen("actual unknown attempt must remain quarantined")
            reason = "Observed socket.gaierror errno11001; DNS resolution failed before TCP/HTTP; zero provider response/stream."
        elif not (stem / "sdk").exists():
            reason = "SDK configuration constructor rejected missing dedicated GLM base/key before any wire request."
        else:
            raise CircuitOpen("cannot prove non-dispatch")
        once(stem / "dispatch-reconciliation.json", {"derived_state": "NOT_SENT", "reason": reason,
            "original_terminal_preserved": True, "time": now(), "usage": 0})
        reconciled.append(name)
    once(run / "protocol-amendment-01.json", {"time": now(), "reason": "Pre-generation infrastructure facts, before any model output/ranking.",
        "independent_judge": ["gpt-6-luna", "tokenmetro"], "secondary_judge": ["deepseek-v4.1-flash", "jizhi"],
        "no_glm_dispatch": True, "prompts_scope_splits_unchanged": True, "reconciled": reconciled})
    print(json.dumps(Campaign(run).totals()), flush=True)


def stage_canary2(run: Path):
    campaign = Campaign(run)
    docs = {d["cid"]: d for d in read_json(run / "corpus.json")["documents"]}
    primary = campaign.model("canary2-a0", "a0", text_payload(docs["1362"]))
    independent = campaign.model("canary2-independent", "judge", {"A": text_payload(docs["1682"]), "B": text_payload(docs["1563"])},
        model="gpt-6-luna", route="tokenmetro")
    vectors = campaign.embed("query", ["离乡求学后归来，灾厄夺走重要之人，随后以生命为火焚烧魔物"])
    once(run / "canary2-results.json", {"primary": primary, "independent": independent,
        "embedding_shape": list(vectors.shape), "totals": campaign.totals()})
    print(json.dumps({"primary": primary, "independent": independent, "totals": campaign.totals()}, ensure_ascii=False), flush=True)


def profile_payload(doc, batch):
    payload = text_payload(doc | {"units": batch})
    payload["coverage"]["document_scoped_chars"] = sum(len(u["text"]) for u in doc["units"])
    payload["coverage"]["complete_document_scope"] = len(batch) == len(doc["units"])
    return payload


def stage_profiles(run: Path):
    campaign = Campaign(run)
    docs = {d["cid"]: d for d in read_json(run / "corpus.json")["documents"]}
    jobs = []
    for cid, doc in docs.items():
        for index, batch in enumerate(batches(doc)):
            for kind in ("ordinary", "a0"):
                jobs.append((cid, index, kind, 0))
    # Necessary repeats, independently issued with the same frozen input/prompt.
    # No semantic correction or prompt adjustment uses held-out results.
    repeated = ["1682", "1362", "1148", "1638", "1288", "1632", "6350", "2321"]
    for cid in repeated:
        for repeat in (1, 2):
            for index, batch in enumerate(batches(docs[cid])):
                for kind in ("ordinary", "a0"):
                    jobs.append((cid, index, kind, repeat))
    def work(job):
        cid, index, kind, repeat = job
        payload = profile_payload(docs[cid], batches(docs[cid])[index])
        result = campaign.model(f"profile-{kind}-{cid}-{index}-r{repeat}", kind, payload)
        output = {"job": list(job), "state": result["state"], "complete": False}
        if "value" in result:
            try:
                clean, rejected = validate_profile(result["value"], payload, kind)
                output.update(complete=True, value=clean, rejected_features=rejected)
            except (ValueError, TypeError, KeyError) as exc:
                output["validation_error"] = str(exc)
        return output
    results = parallel(jobs, work, workers=4)
    once(run / "profiles.json", {"batches": list(results.values()), "totals": campaign.totals()})


def load_profiles(run: Path, repeat=0):
    by_doc = defaultdict(lambda: {"ordinary": [], "a0": []})
    for item in read_json(run / "profiles.json")["batches"]:
        if item.get("complete") and item["job"][3] == repeat:
            by_doc[item["job"][0]][item["job"][2]].append(item)
    for value in by_doc.values():
        for kind in value:
            value[kind].sort(key=lambda x: x["job"][1])
    return by_doc


def bound_judgment(value, a, b):
    if value.get("verdict") not in {"useful", "tentative", "reject", "insufficient"}:
        raise ValueError("invalid verdict")
    output = dict(value)
    support_bound=True
    for prefix, payload in [("a", a), ("b", b)]:
        segments = {s["id"]: s["text"] for s in payload["segments"]}
        refs = value.get(prefix + "_refs", [])
        quote = value.get(prefix + "_quote", "")
        bound=bool(refs and set(refs)<=segments.keys() and quote and any(quote in segments[r] for r in refs))
        support_bound=support_bound and bound
        if value["verdict"] in {"useful", "tentative"} and not bound:
            raise ValueError("judge supporting quote/ref unbound: " + prefix)
    output["source_binding_status"] = "verified_for_delivered_text" if support_bound else "not_verified_not_required_for_rejection_or_insufficiency"
    output["human_semantic_acceptance"] = "NOT_REVIEWED"
    return output


def stage_references(run: Path):
    campaign = Campaign(run)
    docs = {d["cid"]: d for d in read_json(run / "corpus.json")["documents"]}
    proposals = read_json(run / "evaluator-only/reference-proposals.json")
    pairs = sorted({(case["source"], target) for case in proposals for target in case["targets"]})
    def work(pair):
        cid, target = pair
        a, b = text_payload(docs[cid]), text_payload(docs[target])
        result = campaign.qwen_judge("reference-" + cid + "-" + target, {"A": a, "B": b})
        out = {"source": cid, "target": target, "complete": False, "state": result["state"]}
        if "value" in result:
            try:
                out.update(complete=True, judgment=bound_judgment(result["value"], a, b))
            except (ValueError, TypeError, KeyError) as exc:
                out["validation_error"] = str(exc)
        return out
    results = parallel(pairs, work, workers=3)
    once(run / "evaluator-only/reference-review.json", {"pairs": list(results.values()), "totals": campaign.totals(),
        "independence": "Different model from generators, frozen before rankings; not independent human truth."})


def stage_rewrite(run: Path):
    campaign = Campaign(run)
    docs = {d["cid"]: d for d in read_json(run / "corpus.json")["documents"]}
    seeds = sorted({case["source"] for case in read_json(run / "evaluator-only/reference-proposals.json")})
    jobs = [(cid, repeat) for cid in seeds for repeat in (0, 1, 2)]
    def work(job):
        cid, repeat = job
        result = campaign.model(f"rewrite-{cid}-r{repeat}", "rewrite", text_payload(docs[cid]))
        queries = result.get("value", {}).get("queries", [])
        complete = len(queries) == 6 and all(isinstance(q, str) and 0 < len(q) <= 360 for q in queries)
        return {"source": cid, "repeat": repeat, "complete": complete, "queries": queries if complete else [],
                "state": result["state"]}
    results = parallel(jobs, work)
    once(run / "rewrites.json", {"queries": list(results.values()), "totals": campaign.totals()})


def sparse_bm25(field_matrices, lengths, weights):
    """Algebraically identical to the existing field-aware BM25 scoring."""
    from scipy import sparse
    combined = None
    for field, matrix in field_matrices.items():
        matrix = matrix.tocsr().astype(np.float64)
        average = float(np.mean(lengths[field]))
        normalization = .25 + .75 * np.asarray(lengths[field]) / average if average else np.full(matrix.shape[0], .25)
        weighted = sparse.diags(weights[field] / normalization) @ matrix
        combined = weighted if combined is None else combined + weighted
    combined = combined.tocsr()
    combined.eliminate_zeros()
    df = np.bincount(combined.indices, minlength=combined.shape[1])
    idf = np.log(1 + (combined.shape[0] - df + .5) / (df + .5))
    combined.data = idf[combined.indices] * (combined.data * 2.2 / (combined.data + 1.2))
    return combined


def stage_index(run: Path):
    """Lossless research acceleration of the existing lexical scoring formula."""
    from array import array
    from scipy import sparse
    from genshin_corpus.retrieval.candidate_retrieval import (
        FIELD_AWARE_LEXICAL_FIELDS, DEFAULT_FIELD_AWARE_LEXICAL_WEIGHTS,
        validate_accepted_qwen_dense_manifest,
    )
    root = Path.cwd()
    cache = run / "index"
    cache.mkdir(exist_ok=True)
    if (cache / "manifest.json").exists():
        raise ValueError("completed index cannot be overwritten")
    manifest = read_json(root / LEXICAL)
    desc = manifest["artifacts"]["index"]
    path = (root / LEXICAL).parent.parent / desc["path"]
    if file_hash(path) != desc["sha256"]:
        raise ValueError("lexical source integrity failure")
    fields = FIELD_AWARE_LEXICAL_FIELDS
    row_indices = {f: array("i") for f in fields}
    columns = {f: array("i") for f in fields}
    frequencies = {f: array("d") for f in fields}
    lengths = {f: array("i") for f in fields}
    vocab, ids = {}, []
    started = time.monotonic()
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            row = json.loads(line)
            ids.append(row["unit_id"])
            for field in fields:
                value = row["fields"][field]
                lengths[field].append(value["length"])
                if sum(value["tf"].values()) != value["length"]:
                    raise ValueError("lexical TF accounting")
                for token, freq in value["tf"].items():
                    column = vocab.setdefault(token, len(vocab))
                    row_indices[field].append(index)
                    columns[field].append(column)
                    frequencies[field].append(freq)
            if (index + 1) % 100000 == 0:
                print(json.dumps({"lexical_rows": index + 1}), flush=True)
    if len(ids) != desc["row_count"]:
        raise ValueError("lexical row count")
    matrices = {f: sparse.coo_matrix((np.frombuffer(frequencies[f], dtype=np.float64),
        (np.frombuffer(row_indices[f], dtype=np.int32), np.frombuffer(columns[f], dtype=np.int32))),
        shape=(len(ids), len(vocab))).tocsr() for f in fields}
    matrix = sparse_bm25(matrices, {f: np.asarray(lengths[f]) for f in fields}, DEFAULT_FIELD_AWARE_LEXICAL_WEIGHTS)
    sparse.save_npz(cache / "lexical-bm25.npz", matrix)
    once(cache / "lexical-vocab.json", vocab)
    with gzip.open(cache / "lexical-ids.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(ids, handle)
    dm = validate_accepted_qwen_dense_manifest(root / DENSE)
    once(cache / "manifest.json", {"source_lexical_sha256": desc["sha256"], "source_dense": dm["arm_build_identity"],
        "matrix_sha256": file_hash(cache / "lexical-bm25.npz"), "shape": list(matrix.shape), "nnz": matrix.nnz,
        "seconds": time.monotonic() - started, "weights": dict(DEFAULT_FIELD_AWARE_LEXICAL_WEIGHTS),
        "formula": "idf * (weighted_length_normalized_tf * 2.2)/(weighted_length_normalized_tf+1.2)"})
    print(json.dumps({"index_complete": True, "seconds": time.monotonic() - started}), flush=True)


def stage_cohort2(run: Path):
    """Explicit new complete-model cohort; no failed request is resumed."""
    import shutil
    old = run.parent / "p05-navi-r1-discovery-20261025-r1"
    run.mkdir(exist_ok=False)
    for name in ["corpus.json", "official-locators.json.gz"]:
        shutil.copy2(old / name, run / name)
    (run / "evaluator-only").mkdir()
    shutil.copy2(old / "evaluator-only/reference-proposals.json", run / "evaluator-only/reference-proposals.json")
    protocol = read_json(old / "protocol.json")
    protocol.pop("identity")
    protocol.update(created_at=now(), parent_protocol_sha256=file_hash(old / "protocol.json"),
        separate_model_cohort=True,
        reason="DeepSeek pilot stopped on cumulative unknown transport failures; all raw attempts preserved. Full comparison uses one separately configured Qwen model consistently across every generation arm, not per-request fallback.",
        models={"generation": ["qwen3.7-plus-2026-05-26", "bailian"],
                "independent_judge": ["qwen3.8-max", "bailian"], "embedding": "qwen3.7-text-embedding"},
        previous_unknown_requests_quarantined=True)
    protocol["identity"] = sha256_json(protocol)
    once(run / "protocol.json", protocol)
    campaign = Campaign(run)
    docs = {d["cid"]: d for d in read_json(run / "corpus.json")["documents"]}
    result = campaign.model("cohort2-primary-canary", "a0", text_payload(docs["1377"]))
    judge = campaign.qwen_judge("cohort2-judge-canary", {"A": text_payload(docs["1638"]), "B": text_payload(docs["207"])})
    once(run / "cohort2-canary.json", {"primary": result, "judge": judge, "totals": campaign.totals()})
    print(json.dumps({"primary": {k:v for k,v in result.items() if k!='value'},
        "judge": {k:v for k,v in judge.items() if k!='value'}, "totals": campaign.totals()}, ensure_ascii=False), flush=True)


def stage_transport_ready(run: Path):
    once(run / "protocol-amendment-02.json", {"time": now(),
        "transport": "Existing validated Bailian workspace endpoint; urllib no redirect/no retry; full raw response retained.",
        "unchanged": "128 documents, source splits, prompts, candidate budgets, model IDs, outputs and scoring",
        "scope": "Before complete-model cohort production stages; canary2-judge remains quarantined, never reissued."})
    campaign = Campaign(run)
    docs = {d["cid"]: d for d in read_json(run / "corpus.json")["documents"]}
    result = campaign.qwen_judge("independent-transport-probe", {"A": text_payload(docs["1683"]), "B": text_payload(docs["1684"])})
    print(json.dumps({k:v for k,v in result.items() if k!='value'}, ensure_ascii=False), flush=True)


def stage_models(run: Path):
    if not (run / "operating-point.json").exists():
        once(run / "operating-point.json", {"time": now(),
        "reason": "Fixed before full comparison dispatch; isolated unknowns remain fully reserved, never retried. Initial provider pilot is a separate frozen cohort.",
        "circuit": {"max_unknown_attempts": 20},
        "additional_circuit": "At least 5 UNKNOWN and over 20 percent of issued jobs stops further dispatch.",
        "no_changes_to_prompts_or_splits": True})
    stage_references(run)
    stage_profiles(run)
    stage_rewrite(run)
    print(json.dumps({"models_finished": True, "totals": Campaign(run).totals()}), flush=True)


def stage_cohort3(run: Path):
    import shutil
    old=run.parent/'p05-navi-r1-discovery-20261025-r2-qwen'
    run.mkdir(exist_ok=False)
    for name in ['corpus.json','official-locators.json.gz']:
        shutil.copy2(old/name,run/name)
    (run/'evaluator-only').mkdir()
    shutil.copy2(old/'evaluator-only/reference-proposals.json',run/'evaluator-only/reference-proposals.json')
    protocol=read_json(old/'protocol.json')
    protocol.pop('identity')
    protocol.update(created_at=now(),parent_protocol_sha256=file_hash(old/'protocol.json'),
        separate_model_cohort=True,traced_persistent_transport=True,
        reason='Complete single-model Max cohort after TLS infrastructure diagnosis. Previous model cohorts and UNKNOWN attempts remain immutable and are not retried or silently merged.',
        models={'generation':['qwen3.8-max','bailian'],'independent_judge':['qwen3.7-plus-2026-05-26','bailian'],'embedding':'qwen3.7-text-embedding'},
        failure_policy='Warm unauthenticated connection before reservation. At most three attempts only after positive TCP/TLS failure trace with no request headers started. UNKNOWN is never retried. No provider fallback.')
    protocol['circuit']['max_accounting_rmb']=450
    protocol['identity']=sha256_json(protocol)
    once(run/'protocol.json',protocol)
    once(run/'operating-point.json',{'time':now(),'circuit':{'max_unknown_attempts':10},
        'reason':'Traced persistent transport complete cohort; earlier quarantined cohorts retain at least 50 RMB of the overall 500 RMB accounting ceiling.'})
    campaign=Campaign(run)
    docs={d['cid']:d for d in read_json(run/'corpus.json')['documents']}
    result=campaign.model('max-cohort-canary','a0',text_payload(docs['3121']))
    once(run/'max-cohort-canary.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='value'},ensure_ascii=False),flush=True)


def shorten(text, limit=1800):
    return text[:limit]


def representations(doc, profile):
    slots = []
    for kind in ["ordinary", "a0"]:
        for batch in profile.get(kind, []):
            value = batch["value"]
            if kind == "ordinary":
                details = value.get("key_details", [])
                slots.append({"cid": doc["cid"], "role": "ordinary", "text": value["summary"] + "\n" + "\n".join(x["text"] for x in details),
                    "refs": sorted(set(value["refs"]) | {r for x in details for r in x["refs"]})})
            else:
                slots.append({"cid": doc["cid"], "role": "content", "text": value["content"] + "\n" + value.get("limitations", ""),
                    "refs": value["content_refs"]})
                for role in ["needs", "supplies", "motifs"]:
                    for item in value[role]:
                        slots.append({"cid": doc["cid"], "role": role, "text": item["text"], "refs": item["refs"]})
    for index, slot in enumerate(slots):
        slot["id"] = doc["cid"] + ":" + str(index)
        slot["unshortened_chars"] = len(slot["text"])
        slot["text"] = shorten(slot["text"], 6000)
    return slots


def query_bank(doc, slots):
    def texts(role):
        return [s["text"] for s in slots if s["role"] == role]
    fallback = shorten(doc["title"] + " " + doc["units"][0]["text"], 1800)
    content = shorten("\n".join(texts("content")) or fallback)
    result = [{"text": content, "role": "content"}]
    for role in ["needs", "supplies"]:
        values = texts(role)
        # Exact same bank is reused for merged and role-filtered retrieval.
        # Feature selection is source-independent, with no reference targeting.
        for index in range(2):
            result.append({"text": shorten(values[index]) if index < len(values) else content,
                           "role": role if index < len(values) else "content"})
    motifs = texts("motifs")
    result.append({"text": shorten(motifs[0]) if motifs else content, "role": "motifs" if motifs else "content"})
    return result


def embed_many(campaign, role, texts):
    values = sorted(set(texts))
    found = {}
    for start in range(0, len(values), 10):
        batch = values[start:start+10]
        vectors = campaign.embed(role, batch)
        found.update(zip(batch, vectors))
        if start % 100 == 0:
            print(json.dumps({"embedding_role": role, "done": min(start+10,len(values)), "total": len(values)}), flush=True)
    return found


def stage_representations(run: Path):
    campaign = Campaign(run)
    docs = {d["cid"]: d for d in read_json(run / "corpus.json")["documents"]}
    profiles = load_profiles(run)
    slots = [s for cid, doc in docs.items() for s in representations(doc, profiles[cid])]
    # Diagnostic only: source IDs stay mechanically bound; unbound quotations
    # remain explicitly unusable as quotes, not silently normalized/repaired.
    locator_profiles=defaultdict(lambda:{'ordinary':[],'a0':[]})
    for item in read_json(run/'profiles.json')['batches']:
        job=item.get('job',[])
        if len(job)!=4 or job[3]!=0:continue
        cid,index,kind,_=job
        response=read_json(run/'responses'/f'profile-{kind}-{cid}-{index}-r0.json')
        if 'value' not in response:continue
        try:
            value,rejected=validate_profile(response['value'],profile_payload(docs[cid],batches(docs[cid])[index]),kind,require_quote=False)
            locator_profiles[cid][kind].append({'job':job,'value':value,'complete':True,'rejected_features':rejected})
        except (ValueError,TypeError,KeyError):pass
    for values in locator_profiles.values():
        for kind in values:values[kind].sort(key=lambda x:x['job'][1])
    locator_slots=[s for cid,doc in docs.items() for s in representations(doc,locator_profiles[cid])]
    for slot in locator_slots:slot['id']+=':locator'
    once(run/'feature-policy-sensitivity.json',{'created_at':now(),'before_any_retrieval_rankings':not(run/'rankings.json').exists(),
        'primary':'strict exact-quote feature binding','diagnostic':'same raw model outputs, valid source refs required; malformed quotes unusable as evidence; no semantic repairs or new model calls',
        'targets_splits_prompts_unchanged':True,'source_scope_documents':128})
    seeds = sorted({c["source"] for c in read_json(run / "evaluator-only/reference-proposals.json")})
    banks = {cid: query_bank(docs[cid], [s for s in slots if s["cid"] == cid]) for cid in seeds}
    locator_banks={cid:query_bank(docs[cid],[s for s in locator_slots if s['cid']==cid]) for cid in seeds}
    queries = set()
    for bank in banks.values():
        queries.update(q["text"] for q in bank)
    for bank in locator_banks.values():queries.update(q['text'] for q in bank)
    for doc in docs.values():
        for query in query_bank(doc, [s for s in slots if s["cid"] == doc["cid"]]):
            queries.add(query["text"])
    rewrites = read_json(run / "rewrites.json")["queries"]
    raw = {}
    ordinary = {}
    locator_ordinary={}
    for cid in seeds:
        doc = docs[cid]
        # Single-query raw baseline plus six independent original passages for
        # the equal search-budget baseline, with explicit finite projections.
        raw[cid] = [shorten(doc["title"] + " " + u["text"], 1800) for u in doc["units"][:6]]
        raw[cid] = (raw[cid] * 6)[:6]
        ordinary[cid] = shorten("\n".join(s["text"] for s in slots if s["cid"] == cid and s["role"] == "ordinary") or doc["units"][0]["text"])
        locator_ordinary[cid]=shorten('\n'.join(s['text'] for s in locator_slots if s['cid']==cid and s['role']=='ordinary') or doc['units'][0]['text'])
        queries.update(raw[cid])
        queries.add(ordinary[cid])
        queries.add(locator_ordinary[cid])
    for item in rewrites:
        queries.update(item["queries"])
    document_vectors = embed_many(campaign, "document", [s["text"] for s in slots+locator_slots])
    query_vectors = embed_many(campaign, "query", list(queries))
    ordered_queries = sorted(query_vectors)
    once(run / "representations.json", {"slots": slots, "banks": banks, "raw": raw, "ordinary": ordinary,
        'locator_slots':locator_slots,'locator_banks':locator_banks,'locator_ordinary':locator_ordinary,
        "query_texts": ordered_queries, "slot_information_identity": sha256_json(slots),
        "merged_role_matched_same_information": True, "totals": campaign.totals()})
    with (run / "representation-vectors.npz").open("xb") as handle:
        np.savez_compressed(handle, documents=np.stack([document_vectors[s["text"]] for s in slots]),
                            locator_documents=np.stack([document_vectors[s['text']] for s in locator_slots]),
                            queries=np.stack([query_vectors[q] for q in ordered_queries]))


def rrf(rankings, limit=500):
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, item in enumerate(ranking, 1):
            scores[str(item)] += 1 / (60 + rank)
    return [cid for cid in sorted(scores, key=lambda cid: (-scores[cid], cid))[:limit]]


def stable_top(scores, ids, count=500, eligible=None, positive_only=True):
    indices = np.arange(len(ids)) if eligible is None else np.asarray(eligible)
    valid = indices[np.isfinite(scores[indices]) & ((scores[indices] > 0) if positive_only else True)]
    if len(valid) > count:
        threshold = np.partition(scores[valid], len(valid) - count)[len(valid) - count]
        valid = valid[scores[valid] >= threshold]
    return sorted(valid, key=lambda i: (-float(scores[i]), ids[i]))[:count]


class LocalProfileRetriever:
    def __init__(self, slots, vectors, query_vectors):
        from genshin_corpus.retrieval.lexical import analyze
        self.slots, self.vectors, self.query_vectors = slots, vectors, query_vectors
        self.analyze = analyze
        self.tfs = [Counter(analyze(s["text"])) for s in slots]

    def search(self, source, query, *, roles=None, ordinary=False, mode="hybrid", allowed=None):
        selected = [i for i,s in enumerate(self.slots) if s["cid"] != source
            and (s["role"] == "ordinary") == ordinary and (roles is None or s["role"] in roles)
            and (allowed is None or s["cid"] in allowed)]
        if not selected:
            return [], {}
        if mode=='dense':
            values=self.vectors[selected] @ self.query_vectors[query]
            indices=sorted(range(len(selected)),key=lambda j:(-float(values[j]),self.slots[selected[j]]['id']))
            documents,support=[],{}
            for j in indices:
                slot=self.slots[selected[j]]
                if slot['cid'] not in support:
                    documents.append(slot['cid'])
                    support[slot['cid']]={'slot':slot['id'],'refs':slot['refs'],'role':slot['role']}
            return documents[:500],support
        # DF and length are held identical between merged/role variants: role
        # matching is an admission mask, not a different scoring statistic.
        population = [i for i,s in enumerate(self.slots) if (s["role"] == "ordinary") == ordinary]
        tokens = Counter(self.analyze(query))
        average = sum(sum(self.tfs[i].values()) for i in population) / max(len(population),1)
        df = {t: sum(t in self.tfs[i] for i in population) for t in tokens}
        lex, dense = {}, {}
        vector = self.query_vectors[query]
        for i in selected:
            dense[i] = float(self.vectors[i] @ vector)
            length = sum(self.tfs[i].values())
            score = 0.0
            for token, frequency in tokens.items():
                tf = self.tfs[i].get(token,0)
                if tf:
                    score += frequency * math.log(1 + (len(population)-df[token]+.5)/(df[token]+.5)) * tf*2.2/(tf+1.2*(.25+.75*length/(average or 1)))
            lex[i] = score
        ranks = {}
        for name, scores in [("lexical", lex), ("dense", dense)]:
            ranks[name] = sorted(selected, key=lambda i:(-scores[i],self.slots[i]["id"]))
        if mode == "hybrid":
            indices = list(map(int, rrf([list(map(str,ranks["lexical"])),list(map(str,ranks["dense"]))],len(selected))))
        else:
            indices = ranks[mode]
        documents, support = [], {}
        for i in indices:
            cid = self.slots[i]["cid"]
            if cid not in support:
                documents.append(cid)
                support[cid] = {"slot":self.slots[i]["id"],"refs":self.slots[i]["refs"],"role":self.slots[i]["role"]}
        return documents[:500], support


def stage_local(run: Path):
    """All local routes and repeat variants, with cached embeddings only."""
    rep = read_json(run / "representations.json")
    arrays = np.load(run / "representation-vectors.npz")
    qvectors = dict(zip(rep["query_texts"], arrays["queries"]))
    retriever = LocalProfileRetriever(rep["slots"],arrays["documents"],qvectors)
    locator_retriever=LocalProfileRetriever(rep['locator_slots'],arrays['locator_documents'],qvectors)
    docs = {d["cid"]:d for d in read_json(run / "corpus.json")["documents"]}
    rewrite = {(v["source"],v["repeat"]):v for v in read_json(run / "rewrites.json")["queries"]}
    role_target = {"content":["content"],"needs":["supplies"],"supplies":["needs"],"motifs":["motifs"]}
    outputs = {}
    for cid, bank in rep["banks"].items():
        methods = {}
        ranking, support = retriever.search(cid, rep["ordinary"][cid], ordinary=True)
        methods["ordinary_profile"] = {"documents":ranking,"support":support,"query_count":1}
        ranking,support=retriever.search(cid,bank[0]['text'],roles=['content'])
        methods['a0_content_only']={'documents':ranking,'support':support,'query_count':1}
        for name, role_filter in [("a0_merged",False),("bidirectional",True)]:
            rankings, supports = [], {}
            for query in bank:
                rank, supp = retriever.search(cid,query["text"],roles=role_target[query["role"]] if role_filter else None)
                rankings.append(rank)
                for target, value in supp.items():
                    supports.setdefault(target,value)
            methods[name] = {"documents":rrf(rankings),"support":supports,"query_count":len(bank)}
        # Direct motif channel is measured separately; two-hop gets exactly six
        # searches, not six direct searches plus free expansion searches.
        motif_query = bank[-1]
        rank, supp = retriever.search(cid,motif_query["text"],roles=["motifs"])
        methods["motif_only"] = {"documents":rank,"support":supp,"query_count":1}
        bridge_rankings, bridge_supports, paths = [], {}, []
        for query in [bank[0],bank[-1]]:
            rank, supp = retriever.search(cid,query["text"])
            bridge_rankings.append(rank)
            bridge_supports.update(supp)
        intermediates = rrf(bridge_rankings,2)
        for mid in intermediates:
            midbank = query_bank(docs[mid],[s for s in rep["slots"] if s["cid"]==mid])
            for query in [midbank[1],midbank[-1]]:
                rank,supp=retriever.search(cid,query["text"])
                rank=[t for t in rank if t!=mid]
                bridge_rankings.append(rank)
                paths.append({"intermediate":mid,"query":query,"documents":rank[:20]})
                for target,value in supp.items():
                    bridge_supports.setdefault(target,value)
        methods["narrative_bridge"]={"documents":rrf(bridge_rankings),"support":bridge_supports,"paths":paths,"query_count":len(bridge_rankings)}
        ranking,support=locator_retriever.search(cid,rep['locator_ordinary'][cid],ordinary=True)
        methods['ordinary_profile_locator']={'documents':ranking,'support':support,'query_count':1}
        for name,filtered in [('a0_merged_locator',False),('bidirectional_locator',True)]:
            ranks,supports=[],{}
            for q in rep['locator_banks'][cid]:
                rank,supp=locator_retriever.search(cid,q['text'],roles=role_target[q['role']] if filtered else None)
                ranks.append(rank)
                for t,v in supp.items():supports.setdefault(t,v)
            methods[name]={'documents':rrf(ranks),'support':supports,'query_count':6,'source_information':'same source-locator-only shadow features'}
        outputs[cid]=methods
    once(run/"local-profile-rankings.json",{"sources":outputs,"same_information_identity":rep["slot_information_identity"],
        "budget_note":"merged and bidirectional use the exact same six query strings and target vectors; only role eligibility differs"})


def stage_full(run: Path):
    """Existing full-corpus raw retrieval and the matched 128-page restriction."""
    from scipy import sparse
    from genshin_corpus.retrieval.lexical import analyze
    root = Path.cwd()
    index = run / "index"
    if not (index / "manifest.json").exists():
        index = run.parent / "p05-navi-r1-discovery-20261025-r1/index"
    metadata = read_json(index / "manifest.json")
    if file_hash(index/"lexical-bm25.npz") != metadata["matrix_sha256"]:
        raise ValueError("accelerated lexical index integrity")
    matrix = sparse.load_npz(index/"lexical-bm25.npz")
    vocab = read_json(index/"lexical-vocab.json")
    with gzip.open(index/"lexical-ids.json.gz","rt",encoding="utf-8") as handle:
        lex_ids = json.load(handle)
    with gzip.open(run/"official-locators.json.gz","rt",encoding="utf-8") as handle:
        locators = json.load(handle)
    dm = read_json(root/DENSE)
    dense_root=(root/DENSE).parent.parent
    # Full integrity was checked by the index stage. Recheck descriptor binding,
    # immutable source size and row mapping here without hashing 4GB per query.
    vectors=np.load(dense_root/dm["artifacts"]["vectors"]["path"],mmap_mode="r")
    with gzip.open(dense_root/dm["artifacts"]["rows"]["path"],"rt",encoding="utf-8") as handle:
        dense_ids=[json.loads(line)["unit_id"] for line in handle]
    if len(dense_ids)!=535802 or vectors.shape!=(535802,2048):
        raise ValueError("accepted Dense shape/row binding")
    rep=read_json(run/"representations.json")
    arrays=np.load(run/"representation-vectors.npz")
    qvectors=dict(zip(rep["query_texts"],arrays["queries"]))
    local=read_json(run/"local-profile-rankings.json")["sources"]
    rewrites={(x["source"],x["repeat"]):x for x in read_json(run/"rewrites.json")["queries"]}
    required=set()
    for cid,bank in rep["banks"].items():
        required.update(rep["raw"][cid])
        required.add(rep["ordinary"][cid])
        required.update(q["text"] for q in bank)
        required.update(q['text'] for q in rep['locator_banks'][cid])
        required.add(rep['locator_ordinary'][cid])
        for repeat in [0,1,2]:
            required.update(rewrites.get((cid,repeat),{}).get("queries",[]))
        required.update(path["query"]["text"] for path in local[cid]["narrative_bridge"]["paths"])
    required=sorted(required)
    source_counts=Counter(v[0] for v in locators.values())
    # Reserve enough ranked rows to remove all source self-hits BEFORE applying
    # the shared 500-candidate arm budget. No target/reference is consulted.
    raw_wide_depth=500+max(source_counts[cid] for cid in rep['banks'])
    selected={d["cid"] for d in read_json(run/"corpus.json")["documents"]}
    lex_local=np.array([i for i,uid in enumerate(lex_ids) if locators[uid][0] in selected])
    dense_local=np.array([i for i,uid in enumerate(dense_ids) if locators[uid][0] in selected])
    raw_queries={}
    started=time.monotonic()
    for offset in range(0,len(required),16):
        batch=required[offset:offset+16]
        qmatrix=np.stack([qvectors[q] for q in batch])
        dense_scores=np.asarray(vectors @ qmatrix.T)
        qt=sparse.lil_matrix((len(batch),len(vocab)),dtype=np.float64)
        for j,q in enumerate(batch):
            for token,frequency in Counter(analyze(q)).items():
                if token in vocab:
                    qt[j,vocab[token]]=frequency
        lexical_scores=np.asarray((matrix @ qt.tocsr().T).toarray())
        for j,q in enumerate(batch):
            value={}
            for scope,lexmask,densemask in [("full",None,None),("local",lex_local,dense_local)]:
                lex_rank=stable_top(lexical_scores[:,j],lex_ids,raw_wide_depth,lexmask)
                dense_rank=stable_top(dense_scores[:,j],dense_ids,raw_wide_depth,densemask,positive_only=False)
                arms={"lexical":[lex_ids[i] for i in lex_rank],"dense":[dense_ids[i] for i in dense_rank]}
                arms["hybrid"]=rrf([arms["lexical"],arms["dense"]],500)
                value[scope]=arms
            raw_queries[q]=value
        print(json.dumps({"full_queries_completed":min(offset+16,len(required)),"total":len(required)}),flush=True)
    # Materialize page and evidence candidates only after unit-level fusion.
    def pages(source, queries, scope, mode):
        ranks, support={},defaultdict(list)
        rankings=[]
        for query in queries:
            docs=[]
            arms=raw_queries[query][scope]
            lexical=[uid for uid in arms['lexical'] if locators[uid][0]!=source][:500]
            dense=[uid for uid in arms['dense'] if locators[uid][0]!=source][:500]
            candidates=rrf([lexical,dense],500) if mode=='hybrid' else lexical if mode=='lexical' else dense
            for uid in candidates:
                target=locators[uid][0]
                if target==source:
                    continue
                if target not in docs:
                    docs.append(target)
                if uid not in support[target]:
                    support[target].append(uid)
            rankings.append(docs)
        return {"documents":rrf(rankings),"ru_support":dict(support),"query_count":len(queries)}
    output={}
    for cid,bank in rep["banks"].items():
        output[cid]={}
        for scope in ["local","full"]:
            methods={}
            for mode in ["lexical","dense","hybrid"]:
                methods["raw_"+mode]=pages(cid,rep["raw"][cid][:1],scope,mode)
                methods["raw_"+mode+"_budget6"]=pages(cid,rep["raw"][cid],scope,mode)
                for repeat in [0,1,2]:
                    qs=rewrites.get((cid,repeat),{}).get("queries",[])
                    methods[f"rewrite_{mode}_r{repeat}"]=pages(cid,qs,scope,mode)
            if scope=="local":
                methods.update(local[cid])
            else:
                for method in ["ordinary_profile","a0_content_only","a0_merged","bidirectional","narrative_bridge","motif_only",
                               'ordinary_profile_locator','a0_merged_locator','bidirectional_locator']:
                    if method=="ordinary_profile":
                        qs=[rep["ordinary"][cid]]
                    elif method=='a0_content_only':
                        qs=[bank[0]['text']]
                    elif method=='ordinary_profile_locator':qs=[rep['locator_ordinary'][cid]]
                    elif method.endswith('_locator'):qs=[q['text'] for q in rep['locator_banks'][cid]]
                    elif method=="motif_only":
                        qs=[bank[-1]["text"]]
                    elif method=="narrative_bridge":
                        qs=[bank[0]["text"],bank[-1]["text"]]+[p["query"]["text"] for p in local[cid][method]["paths"]]
                    else:
                        qs=[q["text"] for q in bank]
                    projection=pages(cid,qs,scope,"hybrid")
                    methods[method+"_query_only"]=projection
                    methods[method]={"documents":rrf([projection["documents"],local[cid][method]["documents"]]),
                        "ru_support":projection["ru_support"],"support":local[cid][method].get("support",{}),
                        "query_count":len(qs),"augmentation":"128-profile subset combined with complete raw corpus",
                        "paths":local[cid][method].get("paths",[])}
            output[cid][scope]=methods
    once(run/"rankings.json",{"sources":output,"raw_query_rankings":raw_queries,
        "full_corpus_ru_count":535802,"local_profile_document_count":128,"full_profile_corpus":False,
        "index_dependency":str(index),"index_manifest_sha256":file_hash(index/"manifest.json"),
        "self_exclusion":"source rows excluded before the 500-unit candidate arm budget and fusion",
        "seconds":time.monotonic()-started,"totals":Campaign(run).totals()})


def choose_context(doc, source_text, preferred_refs=(), limit=12000):
    """Deterministic source text selection; omissions remain explicit."""
    from genshin_corpus.retrieval.lexical import analyze
    terms=Counter(analyze(source_text))
    def score(unit):
        tf=Counter(analyze(unit["text"]))
        return sum(min(f,tf.get(t,0)) for t,f in terms.items()) / math.sqrt(max(len(unit["text"]),1))
    ordered=sorted(doc["units"],key=lambda u:(u["id"] not in preferred_refs,-score(u),u["section"],u["unit"],u["start"]))
    keep=[]
    length=0
    for unit in ordered:
        if length+len(unit["text"])<=limit:
            keep.append(unit)
            length+=len(unit["text"])
    # Preserve presentation order among admitted segments, not score order.
    chosen={u["id"] for u in keep}
    return text_payload(doc|{"units":[u for u in doc["units"] if u["id"] in chosen]}) | {
        "context_selection":{"scoped_total_chars":sum(len(u["text"]) for u in doc["units"]),"delivered_chars":length,
                             "omitted_segments":[u["id"] for u in doc["units"] if u["id"] not in chosen]}}


def stage_quality(run: Path, output_name='quality.json'):
    """Method-blind dual-source adjudication of every primary-arm Top-5 pair."""
    campaign=Campaign(run)
    all_rankings=read_json(run/"rankings.json")["sources"]
    docs={d["cid"]:d for d in read_json(run/"corpus.json")["documents"]}
    _,_,read=record_reader(Path.cwd())
    primary=["raw_lexical","raw_dense","raw_hybrid","raw_hybrid_budget6",
             "rewrite_lexical_r0","rewrite_dense_r0","rewrite_hybrid_r0",
             "ordinary_profile","a0_content_only","a0_merged","bidirectional","motif_only","narrative_bridge",
             'ordinary_profile_locator','a0_merged_locator','bidirectional_locator']
    pool={}
    for cid,scopes in all_rankings.items():
        for scope,methods in scopes.items():
            for method in primary:
                for rank,target in enumerate(methods[method]["documents"][:5],1):
                    pair=(cid,target)
                    pool.setdefault(pair,[]).append({"scope":scope,"method":method,"rank":rank})
            # A two-hop discovery is not a verified path until both edges have
            # their own dual-source review. No relation is inferred transitively.
            bridge=methods['narrative_bridge']
            for path in bridge.get('paths',[]):
                mid=path['intermediate']
                for target in bridge['documents'][:5]:
                    if target in path['documents']:
                        pool.setdefault((cid,mid),[]).append({'scope':scope,'method':'bridge_edge1','rank':0,'final_target':target})
                        pool.setdefault((mid,target),[]).append({'scope':scope,'method':'bridge_edge2','rank':0,'original_source':cid})
    proposals=read_json(run/"evaluator-only/reference-proposals.json")
    for case in proposals:
        for target in case["targets"]:
            pool.setdefault((case["source"],target),[])
    for target in sorted({b for a,b in pool if b not in docs},key=int):
        record,entry=read(target)
        units,excluded=source_units(record)
        split=[part for unit in units for part in split_occurrence(unit)]
        for index,unit in enumerate(split,1):unit["id"]="u"+str(index)
        docs[target]={"cid":target,"title":record["record_metadata"]["name"],"units":split,
            "record_id":record['record_id'],"source_identity":record['source_identity'],
            "canonical_path":entry["canonical_record_path"],"canonical_sha256":entry["canonical_record_sha256"],"excluded":excluded}
    external={'documents':[d for cid,d in docs.items() if cid not in {x['cid'] for x in read_json(run/'corpus.json')['documents']}],
        'purpose':'Bounded B verification of full-corpus candidates, not additional A0 generation'}
    external_path=run/'external-reading-documents.json'
    if external_path.exists():
        if sha256_json(read_json(external_path))!=sha256_json(external):raise ValueError('external reading scope changed')
    else:once(external_path,external)
    def work(pair):
        source,target=pair
        a,b=docs[source],docs[target]
        if not b["units"]:
            return {"source":source,"target":target,"complete":False,"reason":"no_narrative_body","methods":pool[pair]}
        atext="\n".join(u["text"] for u in a["units"])
        ap=choose_context(a,"\n".join(u["text"] for u in b["units"]),limit=12000)
        bp=choose_context(b,atext,limit=12000)
        try:result=campaign.qwen_judge(f"quality-{source}-{target}",{"A":ap,"B":bp})
        except CircuitOpen as exc:
            return {'source':source,'target':target,'complete':False,'state':'NOT_DISPATCHED_QUARANTINED_OR_CIRCUIT',
                    'reason':str(exc),'methods':pool[pair]}
        out={"source":source,"target":target,"complete":False,"state":result["state"],"methods":pool[pair],
            "delivered":{"A":ap,"B":bp}}
        if "value" in result:
            try:
                out.update(complete=True,judgment=bound_judgment(result["value"],ap,bp))
            except (ValueError,TypeError,KeyError) as exc:
                out["validation_error"]=str(exc)
        return out
    results=parallel(sorted(pool),work,workers=4)
    once(run/output_name,{"pairs":list(results.values()),"primary_methods":primary,
        "selection":"All distinct Top-5 pairs across primary methods and both scopes, plus every reference proposal; no success-only sampling.",
        "totals":campaign.totals()})


def stage_stability(run: Path):
    campaign=Campaign(run)
    docs={d["cid"]:d for d in read_json(run/"corpus.json")["documents"]}
    rep=read_json(run/"representations.json")
    arrays=np.load(run/"representation-vectors.npz")
    qvectors=dict(zip(rep["query_texts"],arrays["queries"]))
    vector_by_text={s["text"]:v for s,v in zip(rep["slots"],arrays["documents"])}
    base=read_json(run/"local-profile-rankings.json")["sources"]
    repeats={}
    query_texts=[]
    doc_texts=[]
    for repeat in [1,2]:
        profiles=load_profiles(run,repeat)
        repeated_slots={cid:representations(docs[cid],p) for cid,p in profiles.items() if p["a0"] and p["ordinary"]}
        merged=[s for s in rep["slots"] if s["cid"] not in repeated_slots]
        merged += [s for slots in repeated_slots.values() for s in slots]
        banks={cid:query_bank(docs[cid],slots) for cid,slots in repeated_slots.items()}
        ordinary={cid:shorten("\n".join(s["text"] for s in slots if s["role"]=="ordinary")) for cid,slots in repeated_slots.items()}
        repeats[repeat]={"slots":merged,"banks":banks,"ordinary":ordinary}
        doc_texts += [s["text"] for s in merged if s["text"] not in vector_by_text]
        query_texts += [q["text"] for b in banks.values() for q in b if q["text"] not in qvectors]
        query_texts += [q for q in ordinary.values() if q not in qvectors]
    vector_by_text.update(embed_many(campaign,"document",doc_texts))
    qvectors.update(embed_many(campaign,"query",query_texts))
    results={}
    role_target={"content":["content"],"needs":["supplies"],"supplies":["needs"],"motifs":["motifs"]}
    for repeat,config in repeats.items():
        slots=config["slots"]
        retriever=LocalProfileRetriever(slots,np.stack([vector_by_text[s["text"]] for s in slots]),qvectors)
        for cid,bank in config["banks"].items():
            methods={}
            methods["ordinary_profile"]=retriever.search(cid,config["ordinary"][cid],ordinary=True)[0]
            for name,filtered in [("a0_merged",False),("bidirectional",True)]:
                methods[name]=rrf([retriever.search(cid,q["text"],roles=role_target[q["role"]] if filtered else None)[0] for q in bank])
            for name,rank in methods.items():
                baseline=base[cid][name]["documents"]
                a,b=set(rank[:20]),set(baseline[:20])
                results.setdefault(cid,{})[str(repeat)+"-"+name]={"documents":rank,"top20_jaccard":len(a&b)/max(len(a|b),1)}
    # Arrival simulations reuse frozen independently generated inputs. Dense
    # ranks avoid using future-corpus lexical DF in the simulated old index.
    retriever=LocalProfileRetriever(rep["slots"],arrays["documents"],qvectors)
    allbanks={cid:query_bank(doc,[s for s in rep["slots"] if s["cid"]==cid]) for cid,doc in docs.items()}
    precalculated={}
    for cid,bank in allbanks.items():
        precalculated[cid]=[retriever.search(cid,q["text"],roles=role_target[q["role"]],mode="dense")[0] for q in bank]
    ordered=sorted(docs,key=int)
    shuffled=ordered.copy()
    random.Random(20261025).shuffle(shuffled)
    orders={"source_id":ordered,"reverse_source_id":list(reversed(ordered)),"random":shuffled}
    arrival={}
    for name,order in orders.items():
        old=set()
        edges=set()
        for cid in order:
            candidates=rrf([[t for t in rank if t in old] for rank in precalculated[cid]],20)
            edges.update(tuple(sorted([cid,t])) for t in candidates)
            old.add(cid)
        arrival[name]={"edges":[list(e) for e in sorted(edges)],"edge_count":len(edges),"queries_per_arrival":6}
    once(run/"stability.json",{"profile_repeat_rankings":results,"arrival":arrival,
        "limitations":"Arrival simulations use a frozen research scope and cached independent embeddings, not a deployed incremental scheduler; source-ID orders are not release chronology.",
        "totals":campaign.totals()})


NEGATIVE_CLAIMS={
    "D09":"这两段近重复文本构成两份相互独立的历史证词。",
    "D10":"可莉就是炽烈的炎之魔女。",
    "D11":"迪奥娜就是竹林月夜中的三月女神之一。",
    "H10":"无工之剑所述的古老石龙就是和璞鸢所述的八虬。",
    "H11":"如雷的盛怒中的雷鸟就是飞雷之弦振的武器主人。",
    "H12":"纯水流华中的歌裴莉娅就是水仙之梦中游戏里的公主。",
}


def stage_negative(run: Path):
    campaign=Campaign(run)
    docs={d["cid"]:d for d in read_json(run/"corpus.json")["documents"]}
    cases={c["id"]:c for c in read_json(run/"evaluator-only/reference-proposals.json")}
    # The ordinary relation judges remain blind. Explicit bad propositions are
    # a separately reported assertion-control task, never retrieval gold edits.
    tasks=[(caseid,model) for caseid in NEGATIVE_CLAIMS for model in ["qwen3.7-plus-2026-05-26","qwen3.8-max"]]
    def work(task):
        caseid,model=task
        case=cases[caseid]
        payload={"A":text_payload(docs[case["source"]]),"B":text_payload(docs[case["targets"][0]]),
            "candidate_claim_to_check":NEGATIVE_CLAIMS[caseid],
            "task_override":"只核查candidate_claim_to_check能否由A/B支持。verdict useful=支持该命题，reject=不支持，insufficient=无法判断；不要把一般联读价值当成支持命题。保留原JSON引文格式。"}
        result=campaign.qwen_call("negative-"+caseid+"-"+model,"judge",payload,model)
        return {"case":caseid,"model":model,"claim":NEGATIVE_CLAIMS[caseid],"result":result}
    results=parallel(tasks,work,workers=3)
    once(run/"negative-controls.json",{"checks":list(results.values()),"totals":campaign.totals()})


def recall_metrics(cases, methods, k):
    items=[]
    families=defaultdict(list)
    for case in cases:
        rank=methods.get(case['source'],[])
        found=set(rank[:k])&set(case['targets'])
        value=len(found)/len(case['targets'])
        reciprocal=max((1/(rank.index(t)+1) for t in case['targets'] if t in rank[:k]),default=0)
        items.append({'case':case['id'],'recall':value,'hit':bool(found),'reciprocal_rank':reciprocal})
        families[case['family']].append(value)
    return {'case_count':len(items),'hits':sum(x['hit'] for x in items),
        'macro_case_recall':sum(x['recall'] for x in items)/max(len(items),1),
        'macro_family_recall':sum(sum(v)/len(v) for v in families.values())/max(len(families),1),
        'mrr':sum(x['reciprocal_rank'] for x in items)/max(len(items),1),'cases':items}


def stage_summarize(run: Path):
    rankings=read_json(run/'rankings.json')['sources']
    proposals=read_json(run/'evaluator-only/reference-proposals.json')
    quality=read_json(quality_path(run))
    references=read_json(run/'evaluator-only/reference-review.json')
    pair_quality={(p['source'],p['target']):p for p in quality['pairs']}
    reference_quality={(p['source'],p['target']):p for p in references['pairs']}
    positives=[c for c in proposals if not c['kind'].startswith('negative') and c['kind']!='duplicate_control']
    methods=sorted(next(iter(rankings.values()))['local'])
    statistics={}
    for scope in ['local','full']:
        statistics[scope]={}
        available=sorted(next(iter(rankings.values()))[scope])
        for name in available:
            ranks={cid:s[scope][name]['documents'] for cid,s in rankings.items()}
            entry={}
            for split in ['dev','holdout']:
                cases=[c for c in positives if c['split']==split]
                entry[split]={str(k):recall_metrics(cases,ranks,k) for k in [1,5,10,20]}
                samples=[]
                for cid in {c['source'] for c in proposals if c['split']==split}:
                    for target in ranks[cid][:5]:
                        p=pair_quality.get((cid,target))
                        if not p or not p.get('complete'):
                            samples.append('unreviewed')
                        else:
                            samples.append(p['judgment']['verdict'])
                entry[split]['quality_top5']={'counts':dict(Counter(samples)),'total':len(samples),
                    'strict_useful_rate':samples.count('useful')/max(len(samples),1),
                    'useful_or_tentative_rate':(samples.count('useful')+samples.count('tentative'))/max(len(samples),1)}
            statistics[scope][name]=entry
    unique=[]
    for case in positives:
        cid=case['source']
        for scope in ['local','full']:
            base=set(rankings[cid][scope]['raw_hybrid_budget6']['documents'][:20])
            rewrite=set(rankings[cid][scope]['rewrite_hybrid_r0']['documents'][:20])
            for name in ['ordinary_profile','a0_merged','bidirectional','narrative_bridge']:
                hit=set(rankings[cid][scope][name]['documents'][:20])&set(case['targets'])
                unique.append({'case':case['id'],'split':case['split'],'scope':scope,'method':name,
                    'hits':sorted(hit),'novel_vs_raw':sorted(hit-base),'novel_vs_rewrite':sorted(hit-rewrite),
                    'novel_vs_both':sorted(hit-base-rewrite)})
    # Do not replace original proposals with model labels: both are reported.
    reference_status=[]
    for case in proposals:
        for target in case['targets']:
            p=reference_quality.get((case['source'],target),{})
            reference_status.append({'case':case['id'],'target':target,'kind':case['kind'],'split':case['split'],
                'review':p.get('judgment',{}).get('verdict','unreviewed'),
                'relation':p.get('judgment',{}).get('relation'), 'complete':p.get('complete',False)})
    profiles=read_json(run/'profiles.json')['batches']
    profile_status={kind:{'requested_batches':sum(p.get('job',[None,None,None,None])[2]==kind for p in profiles),
        'valid_batches':sum(p.get('complete',False) and p['job'][2]==kind for p in profiles),
        'rejected_features':sum(len(p.get('rejected_features',[])) for p in profiles if p.get('job',[None,None,None,None])[2]==kind)} for kind in ['ordinary','a0']}
    costs=[]
    for name in ['p05-navi-r1-discovery-20261025-r1','p05-navi-r1-discovery-20261025-r2-qwen',run.name]:
        directory=run.parent/name
        campaign=Campaign(directory,audit_only=True)
        cost=campaign.totals()|{'cohort':name}
        cost['terminal_states']=dict(Counter(t.get('state','UNRESOLVED') for t in campaign.terminals.values()))
        cost['known_input_tokens']=sum(t.get('usage',{}).get('input_tokens',0) for t in campaign.terminals.values() if isinstance(t.get('usage'),dict) and isinstance(t['usage'].get('input_tokens'),int))
        cost['known_output_tokens']=sum(t.get('usage',{}).get('output_tokens',0) for t in campaign.terminals.values() if isinstance(t.get('usage'),dict) and isinstance(t['usage'].get('output_tokens'),int))
        grouped=defaultdict(lambda:{'attempts':0,'input_tokens':0,'output_tokens':0,'unknown':0,'latency_ms':0})
        for job,issued in campaign.reservations.items():
            group='embedding' if issued['kind']=='embedding' else 'other'
            for prefix in ['profile-ordinary','profile-a0','rewrite','reference','quality','negative']:
                if job.startswith(prefix):group=prefix
            t=campaign.terminals.get(job,{})
            grouped[group]['attempts']+=1
            grouped[group]['unknown']+=t.get('state')=='UNKNOWN'
            grouped[group]['latency_ms']+=t.get('latency_ms',0)
            usage=t.get('usage') or {}
            if isinstance(usage.get('input_tokens'),int):grouped[group]['input_tokens']+=usage['input_tokens']
            if isinstance(usage.get('output_tokens'),int):grouped[group]['output_tokens']+=usage['output_tokens']
        cost['by_stage']=dict(grouped)
        costs.append(cost)
    stability=read_json(run/'stability.json')
    arrival={}
    for name,value in stability['arrival'].items():
        edges={tuple(e) for e in value['edges']}
        arrival[name]={'edge_count':len(edges)}
        for split in ['dev','holdout']:
            cases=[c for c in positives if c['split']==split]
            arrival[name][split]={'hits':sum(any(tuple(sorted([c['source'],t])) in edges for t in c['targets']) for c in cases),'cases':len(cases)}
    grounding=[]
    docs={d['cid']:d for d in read_json(run/'corpus.json')['documents']}
    for case in positives:
        cid=case['source']
        for scope in ['local','full']:
            for method in ['raw_hybrid_budget6','rewrite_hybrid_r0','ordinary_profile','a0_merged','bidirectional','narrative_bridge']:
                result=rankings[cid][scope][method]
                for target in case['targets']:
                    reference=reference_quality.get((cid,target),{})
                    judgment=reference.get('judgment',{})
                    refs=judgment.get('b_refs',[])
                    target_units={u['id']:u for u in docs[target]['units']}
                    expected=set(uid for ref in refs if ref in target_units for uid in target_units[ref]['official_unit_ids'])
                    raw_ids=set(result.get('ru_support',{}).get(target,[]))
                    profile_refs=set(result.get('support',{}).get(target,{}).get('refs',[]))
                    page_hit=target in result['documents'][:20]
                    grounding.append({'case':case['id'],'scope':scope,'method':method,'target':target,'page_hit':page_hit,
                        'reference_body_bound':bool(expected),'raw_occurrence_hit':page_hit and bool(raw_ids&expected),
                        'profile_occurrence_hit':page_hit and bool(profile_refs&set(refs)),
                        'caveat':'Canonical occurrence overlap is not exact quote visibility or final Evidence Packet admission.'})
    summary={'version':VERSION,'scope_documents':128,'reference_cases':len(proposals),'positive_proposal_cases':len(positives),
        'development_families':sorted({c['family'] for c in proposals if c['split']=='dev'}),
        'holdout_families':sorted({c['family'] for c in proposals if c['split']=='holdout'}),
        'statistics':statistics,'unique_increments':unique,'reference_review':reference_status,
        'profile_status':profile_status,'arrival':arrival,'costs':costs,'grounding':grounding,
        'quality_review_counts':dict(Counter(p.get('judgment',{}).get('verdict','unreviewed') for p in quality['pairs'])),
        'limitations':['All R0 cases are development only. Held-out families were never used for prompt tuning.',
            'Reference proposals are analyst selected; model review is not independent human gold.',
            'Full raw retrieval uses all 535802 RUs. Only 128 documents have semantic profiles; no complete-corpus semantic-index claim.',
            'Candidate source occurrence hits do not establish exact quote visibility, historical truth, final QA improvement or production adoption.',
            'Actual provider invoice/cost is UNKNOWN; explicit conservative accounting includes quarantined UNKNOWN reservations.']}
    once(run/'summary.json',summary)
    lines=['# Phase05 R1 measured comparison','',f'128 documents; {len(proposals)} proposal cases; {len(positives)} positive proposal cases.',
        '', 'Source-bound model-reviewed research; no production adoption and no human semantic acceptance.','',
        '## Held-out proposal recall','', '| Scope | Method | Hit@5 | Hit@20 | Macro recall@20 | Useful@5 |',
        '|---|---|---:|---:|---:|---:|']
    main=['raw_lexical','raw_dense','raw_hybrid','raw_hybrid_budget6','rewrite_hybrid_r0','ordinary_profile','a0_merged','bidirectional','narrative_bridge']
    for scope in ['local','full']:
        for name in main:
            entry=statistics[scope][name]['holdout']
            lines.append(f"| {scope} | {name} | {entry['5']['hits']}/{entry['5']['case_count']} | {entry['20']['hits']}/{entry['20']['case_count']} | {entry['20']['macro_case_recall']:.3f} | {entry['quality_top5']['strict_useful_rate']:.3f} |")
    lines+=['','## Limits','']+['- '+v for v in summary['limitations']]
    (run/'measured-report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'summary':str(run/'summary.json'),'quality':summary['quality_review_counts'],'costs':[{k:v for k,v in c.items() if k!='by_stage'} for c in costs]},ensure_ascii=False),flush=True)


def stage_finish(run: Path):
    stage_representations(run)
    stage_local(run)
    stage_full(run)
    stage_quality(run)
    stage_stability(run)
    stage_negative(run)
    stage_summarize(run)


def stage_scope_controls(run: Path):
    """Diagnostic scope-matched controls, retaining the original score report."""
    controls=read_json(run/'scope-control-rankings.json')['sources']
    original=read_json(quality_path(run))['pairs']
    known={(p['source'],p['target']):p for p in original}
    docs={d['cid']:d for d in read_json(run/'corpus.json')['documents']}
    docs.update({d['cid']:d for d in read_json(run/'external-reading-documents.json')['documents']})
    _,_,read=record_reader(Path.cwd())
    needed={(cid,target) for cid,methods in controls.items() for rank in methods.values() for target in rank[:5]}
    missing=sorted(needed-known.keys())
    for cid in {b for a,b in missing if b not in docs}:
        record,entry=read(cid);units,excluded=source_units(record)
        split=[part for u in units for part in split_occurrence(u)]
        for index,u in enumerate(split,1):u['id']='u'+str(index)
        docs[cid]={'cid':cid,'title':record['record_metadata']['name'],'units':split,
            'record_id':record['record_id'],'canonical_sha256':entry['canonical_record_sha256'],'canonical_path':entry['canonical_record_path']}
    campaign=Campaign(run)
    def work(pair):
        a,b=pair;out={'source':a,'target':b,'complete':False}
        if not docs[b]['units']:return out|{'reason':'no_narrative_body'}
        ap=choose_context(docs[a],'\n'.join(u['text'] for u in docs[b]['units']))
        bp=choose_context(docs[b],'\n'.join(u['text'] for u in docs[a]['units']))
        try:result=campaign.qwen_judge(f'quality-{a}-{b}',{'A':ap,'B':bp})
        except CircuitOpen:return out|{'reason':'quarantined_or_circuit_no_provider_call'}
        out['state']=result['state']
        if 'value' in result:
            try:out.update(complete=True,judgment=bound_judgment(result['value'],ap,bp))
            except (ValueError,KeyError,TypeError) as exc:out['validation_error']=str(exc)
        return out|{'delivered':{'A':ap,'B':bp}}
    reviewed=parallel(missing,work,workers=4)
    known.update({(p['source'],p['target']):p for p in reviewed.values() if 'source'in p})
    cases=[c for c in read_json(run/'evaluator-only/reference-proposals.json') if not c['kind'].startswith('negative') and c['kind']!='duplicate_control']
    stats={}
    for method in next(iter(controls.values())):
        stats[method]={}
        for split in ['dev','holdout']:
            selected=[c for c in cases if c['split']==split]
            ranks={cid:v[method] for cid,v in controls.items()}
            value={str(k):recall_metrics(selected,ranks,k) for k in [1,5,10,20]}
            verdicts=[]
            for cid in {c['source'] for c in read_json(run/'evaluator-only/reference-proposals.json') if c['split']==split}:
                for target in ranks[cid][:5]:
                    p=known.get((cid,target),{})
                    verdicts.append(p.get('judgment',{}).get('verdict','unreviewed') if p.get('complete') else 'unreviewed')
            value['quality_top5']={'counts':dict(Counter(verdicts)),'total':len(verdicts)}
            stats[method][split]=value
    once(run/'scope-controls-report.json',{'post_ranking_diagnostic':True,'no_prompt_or_gold_changes':True,
        'statistics':stats,'additional_pairs':list(reviewed.values()),'reused_pairs':len(needed)-len(missing),'totals':campaign.totals()})
    print(json.dumps({'scope_controls_done':True,'additional_pairs':len(missing),'totals':campaign.totals()}),flush=True)


def stage_continue_quality(run: Path):
    amendment=run/'operating-point-amendment-03.json'
    if not amendment.exists():
        once(amendment,{'time':now(),'circuit':{'max_unknown_attempts':50},
            'reason':'The 10-UNKNOWN absolute threshold stopped the first review pass at 496/959 responses despite a low aggregate unknown rate and ample reserved budget. Continue only never-issued jobs; prior UNKNOWN results are read, never resent.',
            'unchanged':'500 RMB campaign envelope, 450 RMB final-cohort cap, request/token ceilings, 20-percent unknown-ratio circuit, corpus, prompts, models, rankings, reference proposals',
            'original_quality_file_preserved':True})
    if not (run/'quality-continuation.json').exists():stage_quality(run,'quality-continuation.json')
    if not (run/'stability.json').exists():stage_stability(run)
    if not (run/'negative-controls.json').exists():stage_negative(run)
    if not (run/'summary.json').exists():stage_summarize(run)
    if not (run/'scope-controls-report.json').exists():stage_scope_controls(run)


def quality_path(run):
    return next(run/name for name in ['quality-final.json','quality-continuation.json','quality.json'] if (run/name).exists())


def stage_finalize_quality(run):
    """Complete only local failures with no issued request; preserve old report."""
    original=read_json(run/'quality-continuation.json')
    docs={d['cid']:d for d in read_json(run/'corpus.json')['documents']+read_json(run/'external-reading-documents.json')['documents']}
    campaign=Campaign(run);fixed=[]
    for p in original['pairs']:
        if 'source' in p:fixed.append(p);continue
        match=re.search(r'quality-(\d+)-(\d+)',p.get('error',''))
        if not match:raise ValueError('unidentified failed pair')
        a,b=match.groups();job=f'quality-{a}-{b}'
        # This repair is permitted only for the observed pre-reservation
        # filesystem collision, not any network/provider failure.
        if p.get('error_type')!='FileExistsError' or list((run/'attempts').glob(job+'*')):
            raise ValueError('failed pair has an issued attempt; cannot reissue')
        ap=choose_context(docs[a],'\n'.join(u['text'] for u in docs[b]['units']))
        bp=choose_context(docs[b],'\n'.join(u['text'] for u in docs[a]['units']))
        out={'source':a,'target':b,'complete':False,'repair_reason':'pre-reservation warming-log collision',
            'delivered':{'A':ap,'B':bp}}
        try:
            result=campaign.qwen_judge(job,{'A':ap,'B':bp});out['state']=result['state']
            if 'value' in result:
                try:out.update(complete=True,judgment=bound_judgment(result['value'],ap,bp))
                except (ValueError,KeyError,TypeError) as exc:out['validation_error']=str(exc)
        except CircuitOpen:out['state']='NOT_DISPATCHED_QUARANTINED_OR_CIRCUIT'
        fixed.append(out)
    once(run/'quality-final.json',original|{'pairs':fixed,'parent_sha256':file_hash(run/'quality-continuation.json'),
        'completion_note':'Two pre-reservation local collisions processed; no issued UNKNOWN request retried.','totals':campaign.totals()})


def stage_complete(run):
    for filename,stage in [('quality-final.json',stage_finalize_quality),('summary.json',stage_summarize),
        ('scope-controls-report.json',stage_scope_controls),('embedding-drift.json',stage_embedding_drift),
        ('supplemental-analysis.json',stage_analysis)]:
        if not (run/filename).exists():stage(run)


def stage_audit(run: Path):
    """Provider-free final integrity, credential and unknown-reissue audit."""
    roots=sorted(p for p in run.parent.glob('p05-navi-r1-discovery-20261025-r*') if (p/'protocol.json').exists())
    attempts=[];leaks=[];missing_terminals=[];missing_code=[]
    secrets=[v.encode() for k,v in environment().items() if k.endswith('_API_KEY') and len(v)>10]
    for root in roots:
        for issued_path in (root/'attempts').glob('*/issued.json'):
            stem=issued_path.parent;issued=read_json(issued_path)
            terminal=stem/'terminal.json'
            if not terminal.exists():missing_terminals.append(str(stem))
            value=read_json(terminal) if terminal.exists() else {'state':'UNKNOWN'}
            if (stem/'dispatch-reconciliation.json').exists():value={'state':'NOT_SENT'}
            request=stem/'request.json'
            if not request.exists():request=stem/'sdk/request.json'
            if request.exists():attempts.append((issued['time'],str(stem),request_fingerprint(read_json(request)),value.get('state')))
            snapshot=root/'code-snapshots'/(issued.get('implementation_sha256','missing')+'.py')
            if snapshot.exists() and file_hash(snapshot)!=issued['implementation_sha256']:missing_code.append(str(snapshot))
        for path in root.rglob('*'):
            if path.is_file() and path.suffix.lower() in {'.json','.jsonl','.bin','.md','.py','.txt'}:
                raw=path.read_bytes()
                if any(secret in raw for secret in secrets):leaks.append(str(path))
    unknown={};violations=[]
    for time_,stem,fingerprint,state in sorted(attempts):
        if state!='NOT_SENT' and fingerprint in unknown:
            violations.append({'prior_unknown':unknown[fingerprint],'later':stem,'state':state})
        if state=='UNKNOWN':unknown.setdefault(fingerprint,stem)
    docs=read_json(run/'corpus.json')['documents']+read_json(run/'external-reading-documents.json')['documents']
    sources={d['canonical_path']:d['canonical_sha256'] for d in docs}
    source_failures=[path for path,expected in sources.items() if file_hash(Path(path))!=expected]
    quote_audit=Counter()
    for p in read_json(quality_path(run))['pairs']:
        if not p.get('complete'):continue
        value=p['judgment'];verdict=value['verdict']
        bound=True
        for prefix,key in [('a','A'),('b','B')]:
            segments={s['id']:s['text'] for s in p.get('delivered',{}).get(key,{}).get('segments',[])}
            refs=value.get(prefix+'_refs',[]);quote=value.get(prefix+'_quote','')
            bound=bound and bool(refs and set(refs)<=segments.keys() and quote and any(quote in segments[r] for r in refs))
        quote_audit[verdict+('/two_quotes_bound' if bound else '/quotes_unbound_or_delivery_absent')]+=1
    inventory=[]
    for path in sorted(run.rglob('*')):
        if path.is_file() and path.name not in {'final-audit.json','artifact-inventory.json'}:
            inventory.append({'path':str(path.relative_to(run)),'bytes':path.stat().st_size,'sha256':file_hash(path)})
    once(run/'artifact-inventory.json',{'files':inventory,'file_count':len(inventory)})
    report={'time':now(),'credential_leak_files':leaks,'missing_terminals':missing_terminals,'invalid_code_snapshots':missing_code,
        'canonical_sources_checked':len(sources),'canonical_hash_failures':source_failures,
        'unknown_reissues_all_cohorts':violations,
        'unknown_reissues_final_cohort':[v for v in violations if run.name in v['later']],
        'quality_quote_audit':dict(quote_audit),
        'early_reject_binding_label_note':'The original gate did not require quotes for reject/insufficient; old verified labels for these verdicts are not quote verification. Positive useful/tentative required both exact quotes.',
        'inventory_file_count':len(inventory),'limitations':'Credential scan covers configured API key values in uncompressed text/raw-response files; historical artifacts are preserved, not rewritten.'}
    once(run/'final-audit.json',report)
    print(json.dumps(report,ensure_ascii=False),flush=True)


def stage_analysis(run: Path):
    """Final read-only analysis of frozen outputs, including diagnostic controls."""
    ranks=read_json(run/'rankings.json')['sources']
    controls=read_json(run/'scope-control-rankings.json')['sources']
    pairs=read_json(quality_path(run))['pairs']
    extra=read_json(run/'scope-controls-report.json')['additional_pairs']
    quality={(p['source'],p['target']):p for p in pairs+extra if 'source' in p}
    proposals=read_json(run/'evaluator-only/reference-proposals.json')
    docs={d['cid']:d for d in read_json(run/'corpus.json')['documents']}
    main=['raw_lexical','raw_dense','raw_hybrid','raw_hybrid_budget6','rewrite_hybrid_r0',
        'ordinary_profile','a0_content_only','a0_merged','bidirectional','narrative_bridge',
        'a0_merged_locator','bidirectional_locator']
    comparison={}
    novelty={}
    for scope in ['local','full']:
        comparison[scope]={}
        methods={m:{cid:v[scope][m]['documents'] for cid,v in ranks.items()} for m in main}
        if scope=='full':methods.update({m:{cid:v[m] for cid,v in controls.items()} for m in next(iter(controls.values()))})
        for name,rank in methods.items():
            value={}
            for split in ['dev','holdout']:
                seeds={c['source'] for c in proposals if c['split']==split}
                selected=[c for c in proposals if c['split']==split and not c['kind'].startswith('negative') and c['kind']!='duplicate_control']
                verdicts=[]
                for cid in seeds:
                    for target in rank[cid][:5]:
                        p=quality.get((cid,target),{})
                        verdicts.append(p.get('judgment',{}).get('verdict','unreviewed') if p.get('complete') else 'unreviewed')
                counts=dict(Counter(verdicts)); reviewed=len(verdicts)-counts.get('unreviewed',0)
                useful=counts.get('useful',0)
                value[split]={str(k):recall_metrics(selected,rank,k) for k in [5,20]}
                value[split]['quality']={'counts':counts,'n':len(verdicts),'reviewed':reviewed,
                    'useful_among_reviewed':useful/max(reviewed,1),
                    'useful_rate_lower':useful/max(len(verdicts),1),
                    'useful_rate_upper':(useful+counts.get('unreviewed',0))/max(len(verdicts),1)}
            comparison[scope][name]=value
        if scope=='full':
            for split in ['dev','holdout']:
                seeds={c['source'] for c in proposals if c['split']==split}
                baselines={m:{(cid,t) for cid in seeds for t in methods[m][cid][:5]} for m in ['raw_hybrid_budget6','rewrite_hybrid_r0','raw6_subset_augmented']}
                for name in ['ordinary_profile','a0_merged','bidirectional','narrative_bridge']:
                    candidates={(cid,t) for cid in seeds for t in methods[name][cid][:5]}
                    for base,bset in baselines.items():
                        novel=candidates-bset; counts=Counter()
                        for pair in novel:
                            p=quality.get(pair,{})
                            counts[p.get('judgment',{}).get('verdict','unreviewed') if p.get('complete') else 'unreviewed']+=1
                        novelty[f'{split}/{name}/vs/{base}']={'pairs':[list(p) for p in sorted(novel)],'counts':dict(counts)}
    stability=read_json(run/'stability.json')
    repeated=defaultdict(list)
    for cid,results in stability['profile_repeat_rankings'].items():
        for key,value in results.items():repeated[key.split('-',1)[1]].append(value['top20_jaccard'])
    repeat_stats={k:{'n':len(v),'mean':float(np.mean(v)),'minimum':min(v),'maximum':max(v)} for k,v in repeated.items()}
    rewrite_stability={}
    for scope in ['local','full']:
        values=[]
        for cid,result in ranks.items():
            a=set(result[scope]['rewrite_hybrid_r0']['documents'][:20])
            for repeat in [1,2]:
                b=set(result[scope][f'rewrite_hybrid_r{repeat}']['documents'][:20])
                values.append(len(a&b)/max(len(a|b),1))
        rewrite_stability[scope]={'n':len(values),'mean':float(np.mean(values)),'minimum':min(values)}
    arrival=stability['arrival'];arrival_overlap={}
    for a in arrival:
        for b in arrival:
            if a>=b:continue
            x={tuple(v) for v in arrival[a]['edges']};y={tuple(v) for v in arrival[b]['edges']}
            arrival_overlap[a+'/'+b]=len(x&y)/len(x|y)
    negative=[]
    for item in read_json(run/'negative-controls.json')['checks']:
        if 'case' not in item:negative.append(item);continue
        case=next(c for c in proposals if c['id']==item['case']);r=item['result']
        item={k:v for k,v in item.items() if k!='result'}|{'state':r.get('state'),'raw_verdict':r.get('value',{}).get('verdict')}
        try:
            bound_judgment(r['value'],text_payload(docs[case['source']]),text_payload(docs[case['targets'][0]]))
            item['source_binding']='PASS'
        except (ValueError,KeyError,TypeError):item['source_binding']='FAIL_OR_MISSING'
        negative.append(item)
    costs=[]
    for directory in sorted(run.parent.glob('p05-navi-r1-discovery-20261025-r*')):
        if not (directory/'protocol.json').exists():continue
        campaign=Campaign(directory,audit_only=True)
        known_in=known_out=known_cost=0;states=Counter();stages=defaultdict(lambda:Counter())
        for job,issued in campaign.reservations.items():
            terminal=campaign.terminals.get(job,{});states[terminal.get('state','UNRESOLVED')]+=1
            group='embedding' if issued['kind']=='embedding' else next((p for p in ['profile-ordinary','profile-a0','rewrite','reference','quality','negative'] if job.startswith(p)),'other')
            stages[group]['reservations']+=1;stages[group][terminal.get('state','UNRESOLVED')]+=1
            usage=terminal.get('usage') or {};i=usage.get('input_tokens');o=usage.get('output_tokens')
            if isinstance(i,int) and isinstance(o,int):
                known_in+=i;known_out+=o;known_cost+=(i*issued['input_rate']+o*issued['output_rate'])/1e6
                stages[group]['input_tokens']+=i;stages[group]['output_tokens']+=o
        costs.append(campaign.totals()|{'cohort':directory.name,'states':dict(states),'known_input_tokens':known_in,
            'known_output_tokens':known_out,'known_accounting_rmb':known_cost,'stages':dict(stages)})
    body={'exclusions':dict(Counter(e['reason'] for d in docs.values() for e in d['excluded'])),
        'scoped_body_characters':sum(len(u['text']) for d in docs.values() for u in d['units']),
        'split_parts':sum(not u['whole_occurrence'] for d in docs.values() for u in d['units'])}
    once(run/'supplemental-analysis.json',{'comparison':comparison,'top5_unique_pairs':novelty,'profile_stability':repeat_stats,
        'rewrite_stability':rewrite_stability,'arrival_edge_jaccard':arrival_overlap,'negative_controls':negative,
        'costs_final':costs,'body_selection':body,
        'caveats':['Scope controls added after ranking; diagnostic, not preregistered.',
            'Model judgments and source binding are distinct; unreviewed is not rejection.',
            'Repeated profile stability compares two repeats to baseline and changes selected source/target slots together.']})
    print(json.dumps({'analysis_done':True,'costs':costs,'stability':repeat_stats},ensure_ascii=False),flush=True)


def stage_matched_body(run: Path):
    """Post-ranking control for body selection and remote embedding drift."""
    from scipy import sparse
    from genshin_corpus.retrieval.lexical import analyze
    corpus=read_json(run/'corpus.json')['documents']
    wanted={uid:d['cid'] for d in corpus for u in d['units'] for uid in u['official_unit_ids']}
    rm=read_json(Path(RU));source=Path(RU).parent.parent/rm['artifacts']['retrieval_units']['path']
    units={}
    with gzip.open(source,'rt',encoding='utf-8') as handle:
        for line in handle:
            u=json.loads(line)
            if u['unit_id'] in wanted:units[u['unit_id']]=u['retrieval_visible_text']
    if units.keys()!=wanted.keys():raise ValueError('scoped body RU mismatch')
    ids=sorted(units);texts=[units[uid] for uid in ids]
    selection=run/'matched-body-selection.json'
    if not selection.exists():once(selection,{'post_ranking_diagnostic':True,'unit_ids':ids,'source':rm['artifacts']['retrieval_units']['sha256'],
        'reason':'Exact-input 12-RU drift diagnostic found non-identical geometry. Compare the same selected narrative body with archived/current embeddings; no prompt or target changes.'})
    campaign=Campaign(run)
    vectors_by_text=embed_many(campaign,'document',texts)
    current=np.stack([vectors_by_text[t] for t in texts])
    dm=read_json(Path(DENSE));dr=Path(DENSE).parent.parent
    with gzip.open(dr/dm['artifacts']['rows']['path'],'rt',encoding='utf-8') as handle:rows={x['unit_id']:x['occurrence_index'] for x in map(json.loads,handle)}
    archived=np.load(dr/dm['artifacts']['vectors']['path'],mmap_mode='r')
    old=np.stack([archived[rows[i]] for i in ids])
    index=run.parent/'p05-navi-r1-discovery-20261025-r1/index'
    with gzip.open(index/'lexical-ids.json.gz','rt',encoding='utf-8') as handle:lexrows={uid:i for i,uid in enumerate(json.load(handle))}
    matrix=sparse.load_npz(index/'lexical-bm25.npz')[[lexrows[i] for i in ids]]
    vocab=read_json(index/'lexical-vocab.json')
    rep=read_json(run/'representations.json');arrays=np.load(run/'representation-vectors.npz')
    qvectors=dict(zip(rep['query_texts'],arrays['queries']))
    rewrites={(x['source'],x['repeat']):x['queries'] for x in read_json(run/'rewrites.json')['queries']}
    output={};cosines=np.sum(old*current,axis=1)/(np.linalg.norm(old,axis=1)*np.linalg.norm(current,axis=1))
    for cid,bank in rep['banks'].items():
        banks={'raw':[rep['raw'][cid][0]],'raw6':rep['raw'][cid], 'rewrite':rewrites[(cid,0)],
            'ordinary_query':[rep['ordinary'][cid]],'a0_query':[q['text'] for q in bank]}
        output[cid]={};eligible=[i for i,uid in enumerate(ids) if wanted[uid]!=cid]
        for name,queries in banks.items():
            arms=defaultdict(list)
            for q in queries:
                qt=sparse.lil_matrix((1,len(vocab)),dtype=np.float64)
                for term,n in Counter(analyze(q)).items():
                    if term in vocab:qt[0,vocab[term]]=n
                lex=np.asarray((matrix@qt.tocsr().T).toarray()).ravel()
                lr=[ids[i] for i in stable_top(lex,ids,500,eligible)]
                for label,vs in [('archived',old),('current',current)]:
                    ds=vs@qvectors[q];dense=[ids[i] for i in stable_top(ds,ids,500,eligible,positive_only=False)]
                    for mode,rank in [('lexical',lr),('dense',dense),('hybrid',rrf([lr,dense],500))]:
                        pages=list(dict.fromkeys(wanted[uid] for uid in rank))
                        arms[label+'_'+mode].append(pages)
            output[cid].update({name+'_'+arm:rrf(ranks) for arm,ranks in arms.items()})
    proposals=read_json(run/'evaluator-only/reference-proposals.json');stats={}
    for method in next(iter(output.values())):
        ranks={cid:m[method] for cid,m in output.items()};stats[method]={}
        for split in ['dev','holdout']:
            cases=[c for c in proposals if c['split']==split and not c['kind'].startswith('negative') and c['kind']!='duplicate_control']
            stats[method][split]={str(k):recall_metrics(cases,ranks,k) for k in [5,20]}
    once(run/'matched-body-control.json',{'post_ranking_diagnostic':True,'sources':output,'statistics':stats,
        'ru_count':len(ids),'cosine_min':float(cosines.min()),'cosine_median':float(np.median(cosines)),
        'limitations':'Only 128 scoped narrative bodies. Does not repair compatibility of the full archived 535802-RU Dense index; no new gold or prompt tuning.',
        'totals':campaign.totals()})


def stage_embedding_drift(run: Path):
    """Small exact-input geometry diagnostic; never changes archived rankings."""
    output=run/'embedding-drift.json'
    if output.exists():raise ValueError('completed diagnostic is immutable')
    docs=sorted(read_json(run/'corpus.json')['documents'],key=lambda d:int(d['cid']))
    selected=[docs[i] for i in np.linspace(0,len(docs)-1,12,dtype=int)]
    wanted={uid:d['cid'] for d in selected for u in d['units'] for uid in u['official_unit_ids']}
    manifest=read_json(Path(RU)); desc=manifest['artifacts']['retrieval_units']
    source=Path(RU).parent.parent/desc['path']
    if file_hash(source)!=desc['sha256']:raise ValueError('RU integrity failure')
    found={}
    with gzip.open(source,'rt',encoding='utf-8') as handle:
        for line in handle:
            unit=json.loads(line);uid=unit['unit_id']
            if uid in wanted and wanted[uid] not in found and 100<=len(unit['retrieval_visible_text'])<=2500:
                found[wanted[uid]]={'source':wanted[uid],'unit_id':uid,'text':unit['retrieval_visible_text']}
    dm=read_json(Path(DENSE));dense_root=Path(DENSE).parent.parent
    row_path=dense_root/dm['artifacts']['rows']['path']
    if file_hash(row_path)!=dm['artifacts']['rows']['sha256']:raise ValueError('Dense row integrity failure')
    rows={}
    with gzip.open(row_path,'rt',encoding='utf-8') as handle:
        for line in handle:
            value=json.loads(line);rows[value['unit_id']]=value['occurrence_index']
    samples=[found[d['cid']] for d in selected if d['cid'] in found]
    selection={'policy':'12 evenly spaced source IDs; first archived RU of scoped body with 100..2500 characters; no rank/target selection',
        'requested_sources':[d['cid'] for d in selected],'samples':samples,
        'ru_sha256':desc['sha256'],'dense_vectors_sha256':dm['artifacts']['vectors']['sha256'],
        'purpose':'Detect large remote-alias geometry drift; diagnostic only, no rank recomputation'}
    selection_path=run/'embedding-drift-selection.json'
    if selection_path.exists():
        if read_json(selection_path)!=selection:raise ValueError('diagnostic selection changed')
    else:once(selection_path,selection)
    campaign=Campaign(run)
    current=campaign.embed('document',[s['text'] for s in samples])
    archived=np.load(dense_root/dm['artifacts']['vectors']['path'],mmap_mode='r')
    results=[]
    for sample,vector in zip(samples,current):
        old=archived[rows[sample['unit_id']]]
        cosine=float(old@vector/(np.linalg.norm(old)*np.linalg.norm(vector)))
        results.append({k:v for k,v in sample.items() if k!='text'}|{'cosine':cosine,'characters':len(sample['text'])})
    once(output,{'samples':results,'minimum_cosine':min(r['cosine'] for r in results),
        'median_cosine':float(np.median([r['cosine'] for r in results])),
        'limitations':'Small exact-input diagnostic cannot prove unchanged remote model weights across all inputs.',
        'totals':campaign.totals()})


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage")
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    globals()["stage_" + args.stage](args.run)


if __name__ == "__main__":
    cli()
