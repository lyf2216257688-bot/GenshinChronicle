"""Verified, bounded read models for the product interface."""
from collections import Counter
import json
from pathlib import Path
import time
from uuid import uuid4

from ..evidence_io import digest, read, require
from ..rag.clean_conversation import VERSION, TERMINAL
from ..rag.clean_conversation_graph import citation_view, verify_sources
from .batches import status as batch_status
from .store import Problem

STAGES = {"context_started": "正在读取会话", "context_organized": "已整理前文", "retrieval_started": "正在检索和阅读资料",
          "ranking_complete": "已完成资料排序", "packet_ready": "已整理本轮证据", "request_prepared": "已准备回答请求",
          "generation_started": "正在生成", "checking": "正在核对引用"}
REASONS = {"citation_validation_failed": "引用与本轮证据不匹配，回答未发布。", "user_cancelled": "已停止本轮。",
           "source_fault": "来源读取失败。", "no_gain": "进一步查证未获得新证据。", "context_budget": "已达到上下文上限。",
           "budget_exhausted": "本轮预算已用尽。", "limited": "现有证据只支持部分结论。",
           "model_fault": "模型请求未正常完成。", "unknown": "远端结果未知，已保留原有占额。"}


def message(store, tid, role):
    with store.connect() as db:
        row = db.execute("SELECT ref FROM chat_messages WHERE turn=? AND role=?", (tid, role)).fetchone()
    if not row:
        return None
    item = store.load(json.loads(row[0]))
    require(item["turn_id"] == tid and item["role"] == role and item["message_id"] == digest([VERSION, tid, role]),
            "Message identity changed")
    return item


def publication(store, tid):
    item = message(store, tid, "assistant")
    if not item:
        return None, None
    result = store.load(item["result_ref"])
    require(result["turn_id"] == tid and result["session_id"] == item["session_id"] and
            item["citations"] == result["citations"] and item["content"] == (result["answer_text"] or result["delivery_note"]),
            "Published result changed")
    return item, result


def uncertain(store, binding, tid):
    directory = store.root / "batches" / binding["id"] / "campaigns" / digest(binding["campaign"])
    for slot in (directory / "batch/requests").glob("*/reservation.json"):
        reservation = read(slot)
        plan = read(directory / "mappings" / (reservation["fingerprint"] + ".json"))
        if plan["turn_id"] != tid:
            continue
        attempt = Path(reservation["attempt"])
        if not (attempt / "complete.json").exists() or read(attempt / "result.json").get("state") == "UNKNOWN":
            return True
    return False


def public_events(store, tid, after):
    events = store.events(tid, after)
    if not events:
        return []
    binding = store.binding(store.input_for(tid)["batch_id"])
    final_ops = set()
    if binding["mode"] == "LIVE" and any(e["kind"] == "answer_draft" for e in events):
        # G5 multi-operation tool/research drafts must never be concatenated into an answer.
        from ..model_call_sessions import VERSION as OP_VERSION
        for i in range(binding["settings"]["limits"]["turn_requests"]):
            key = "g5-request-" + str(i)
            ref = store.published_artifact(tid, key)
            if ref and store.load(ref)["phase"] == "final":
                final_ops.add(digest([OP_VERSION, store.turn(tid)["session"], tid, key]))
    out = []
    for e in events:
        kind, text = "refresh", ""
        if e["kind"] == "progress":
            kind, text = "stage", STAGES.get(e["stage"], "正在查证")
        elif e["kind"] == "answer_draft":
            if binding["mode"] == "MOCK" or e.get("operation_id") in final_ops:
                kind, text = "draft", e["text"]
        elif e["kind"] == "g4_complete":
            kind = "settled"
        elif e["kind"] == "interrupted":
            # A later explicit resume can append more events to this same turn.
            # Only the live stream's idle check can end a subscription here.
            kind, text = "stage", "执行中断"
        elif e["kind"] == "cancel_requested":
            kind, text = "stage", "正在停止"
        out.append({"sequence": e["sequence"], "kind": kind, "text": text,
                    "state": "running" if e["kind"] == "progress" else None})
    return out


def turn_view(store, worker, tid):
    entry = store.turn(tid)
    job = store.job(tid)["status"]
    with worker.condition:
        scheduled = tid in worker.pending or tid == worker.active
    base = {"id": tid, "chat_id": entry["session"], "sequence": entry["sequence"], "batch_id": "",
            "question": "", "user_message": digest([VERSION, tid, "user"]), "assistant_message": None, "result_hash": None,
            "state": job, "has_answer": False, "answer": None, "detail": "", "stop_reason": None, "citations": [],
            "can_trace": False, "can_stop": False, "can_resume": False, "watching": False,
            "stage": "", "draft": "", "cursor": 0}
    try:
        submitted = store.input_for(tid)
        base.update(batch_id=submitted["batch_id"], question=submitted["intent"]["text"])
        b = store.binding(submitted["batch_id"])
        item, result = publication(store, tid)
        if result:
            state = result["status"]
            stop = result.get("stop_reason")
            base.update(state=state, answer=result["answer_text"], has_answer=bool(result["answer_text"]),
                        assistant_message=item["message_id"], result_hash=item["result_ref"]["sha256"],
                        citations=result["citations"], stop_reason=stop,
                        can_trace=bool(result.get("sources_ref")),
                        detail=REASONS.get(stop, result.get("delivery_note", "")))
        if base["state"] not in {"queued", "running"} and uncertain(store, b, tid):
            base.update(state="unknown", detail=REASONS["unknown"])
        unfinished = job not in TERMINAL and not item
        cancelled = bool(entry["cancelled"])
        base["can_stop"] = unfinished and (not cancelled or not scheduled)
        if unfinished and not cancelled and not scheduled and base["state"] != "unknown":
            grant_state = batch_status(store, b)["state"]
            base["can_resume"] = grant_state == "open"
            if not base["can_resume"]:
                base["detail"] = {"closed": "原批次已关闭", "expired": "原批次已到期",
                                  "exhausted": "原批次额度已用尽", "read_only": "原批次绑定的执行环境已变化"
                                  }[grant_state] + "。可在新批次继续本会话。"
        base["watching"] = unfinished and scheduled
        if unfinished or (result and not result["answer_text"]):
            for e in public_events(store, tid, 0):
                base["cursor"] = e["sequence"]
                if e["kind"] == "stage":
                    base["stage"] = e["text"]
                if e["kind"] == "draft":
                    base["draft"] += e["text"]
        if unfinished and not base["stage"]:
            base["stage"] = "正在排队" if scheduled else "等待明确继续或停止"
    except (OSError, ValueError, KeyError):
        base.update(state="blocked", detail="本轮记录缺失或校验失败，已阻断受影响内容。", answer=None,
                    citations=[], has_answer=False, can_trace=False, watching=False, can_resume=False)
    return base


def history(store, worker, sid, before, limit):
    store.chat(sid)
    with store.connect() as db:
        rows = db.execute("""SELECT id,sequence FROM turns WHERE session=? AND sequence<?
            ORDER BY sequence DESC LIMIT ?""", (sid, before, limit + 1)).fetchall()
    selected = rows[:limit]
    return {"turns": [turn_view(store, worker, tid) for tid, _ in reversed(selected)],
            "before": selected[-1][1] if len(rows) > limit else None}


def distinct_positions(fragment):
    # A read receipt resolves chunk parts, aliases and context. One Clean location
    # can be encountered several times. Equal text at different locations stays visible.
    positions = {}
    for text in fragment["texts"]:
        positions.setdefault(digest(text["clean_ref"]), text)
    return list(positions.values())


def source(store, tid, packet, cid):
    item, result = publication(store, tid)
    addr = {"turn_id": tid, "packet_id": packet, "citation_id": cid}
    if not item or addr not in result["citations"]:
        raise Problem("unbound_citation", "这处引用没有绑定到本轮已发布回答。", 404)
    view = citation_view(store, tid, cid, packet_id=packet)
    f = view["fragment"]
    parent = view["parent"]["fragments"][0] if view["parent"] else None
    title = f["structure"].get("retrieval_text", "").splitlines()
    document = f["parent_context"].get("label_evidence", {}).get("document_title") or {}
    title = title[0] if title else document.get("text") or "资料 " + str(f["source_item"].get("content_id", ""))
    locators = []
    positions = distinct_positions(f)
    verified = {digest(p["clean_ref"]): p["text"] for p in positions}
    def original(part):
        value = verified[digest(part["clean_ref"])]
        require(value == part["text"], "Source passage differs from the read receipt")
        return value
    lines = []
    for part in f["structure"].get("parts", []):
        speaker = part.get("speaker")
        if isinstance(speaker, dict):
            speaker = speaker.get("text")
        lines.append({"text": original(part), "dialogue": "node_id" in part,
                      "speaker": speaker if isinstance(speaker, str) else None,
                      "conditions": [original(c) for c in part.get("condition_sources", [])],
                      "condition_status": part.get("condition_status")})
    for text in positions:
        lineage = text["raw_lineage"]
        locators.append({"clean": text["clean_ref"], "raw_run": lineage["raw_run_id"],
                         "raw_byte_revalidation": lineage["raw_byte_revalidation"],
                         "origins": lineage["origins"], "artifacts": [
                             {"id": a["artifact_id"], "path": a.get("path"), "sha256": a["sha256"]}
                             for a in lineage["artifacts"]]})
    return {"citation": addr, "title": title, "lines": lines, "texts": [x["text"] for x in positions],
            "support_note": "已核验本轮保存的引文及来源定位。请结合原文和上下文判断它能否支持回答中的结论。",
            "has_parent": bool(parent), "parent_texts": [x["text"] for x in distinct_positions(parent)] if parent else [],
            "context": {"资料": f["source_item"], "条件": f["conditions"], "叙事语境": f["source_context"],
                        "来源与支持范围": {"source_status": f["authority"], "semantic_support": view["semantic_support"]},
                        "父级语境": f["parent_context"], "原文结构": parent["structure"] if parent else f["structure"]},
            "locators": locators}


def trace(store, tid):
    _, result = publication(store, tid)
    if not result or not result.get("sources_ref"):
        raise Problem("no_trace", "本轮没有已发布的查证材料。", 404)
    sources = verify_sources(store, result["sources_ref"])
    queries = [sources["query"]] if "query" in sources else []
    reads = len(sources["reads"])
    if result.get("research_ref"):
        from ..rag.clean_research import verify_data
        data = verify_data(store, result["research_ref"])
        queries, reads = data["queries"], data["read_count"]
    labels = {"included": "已采用", "covered_duplicate": "已由同源材料覆盖", "not_selected": "未入选",
              "budget_excluded": "超出预算", "invalid_source": "来源校验失败", "context_expansion": "补充上下文"}
    counts = Counter(labels.get(d["disposition"], d["disposition"]) for d in sources["packet"]["dispositions"])
    return {"queries": [q["search_text"] for q in queries], "reads": reads,
            "dispositions": dict(counts), "stop_reason": REASONS.get(result.get("stop_reason"), {
                "sufficient": "研究流程判定材料充分", "fixture_complete": "离线演示完成",
                "fixed_chain_complete": "回答已完成"}.get(result.get("stop_reason"), result.get("stop_reason", "")))}


def add_feedback(store, tid, data):
    item, result = publication(store, tid)
    if not item:
        raise Problem("no_result", "本轮尚未发布结果。")
    addr = data.citation.model_dump() if data.citation else None
    if addr and addr not in result["citations"]:
        raise Problem("unbound_citation", "反馈引用与原回答不匹配。", 422)
    b = store.binding(store.input_for(tid)["batch_id"])
    fid = uuid4().hex
    record = {"id": fid, "chat_id": item["session_id"], "turn_id": tid, "question": store.input_for(tid)["intent"]["text"],
              "result_hash": item["result_ref"]["sha256"], "category": data.category, "note": data.note, "citation": addr,
              "engine": result["version"] + " / " + b["settings"].get("generation_profile", b["mode"]),
              "created": time.time()}
    ref = store.artifact(tid, "feedback-" + fid, record)
    with store.connect() as db:
        db.execute("INSERT INTO product_feedback VALUES (?,?,?,?)", (fid, item["session_id"], tid, json.dumps(ref)))
    return record


def reports(store):
    with store.connect() as db:
        rows = db.execute("SELECT ref FROM product_feedback ORDER BY rowid DESC").fetchall()
    return {"items": [store.load(json.loads(ref)) for (ref,) in rows]}
