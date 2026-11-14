"""Explicit opt-in G4 Streamlit chat. Only synthetic Mock execution is exposed."""
import os
from pathlib import Path

import streamlit as st

from genshin_corpus.rag.clean_conversation import ConversationStore, TERMINAL
from genshin_corpus.rag.clean_conversation_graph import fixture_settings, citation_view
from genshin_corpus.rag.clean_executor import LocalExecutor

REPO = Path(__file__).resolve().parents[3]
ROOT = os.environ.get("GC_CLEAN_CHAT_ROOT", str(REPO / "data/generation/clean-v1-local"))
LABELS = {"context_started": "读取本对话历史", "context_organized": "上下文整理完成",
          "retrieval_started": "正在执行Mock召回", "ranking_complete": "Mock排序完成",
          "packet_ready": "原文Packet已保存", "request_prepared": "Mock请求已准备",
          "generation_started": "正在接收Mock草稿", "checking": "正在检查引用与来源"}
KINDS = {"普通追问": None, "要求与范围": "requirement", "用户剧情断言（待核验）": "user_assertion",
         "假说": "hypothesis", "未解问题": "unresolved", "纠正意图或范围": "correction_intent",
         "纠正剧情前提（待核验）": "correction_fact"}


@st.cache_resource
def executor(root, repo):
    return LocalExecutor(root, repo)


def render_citation(store, citation):
    try:
        view = citation_view(store, citation["turn_id"], citation["citation_id"], packet_id=citation["packet_id"])
        fragment = view["fragment"]
        st.write(fragment["source_item"])
        for text in fragment["texts"]:
            st.text(text["text"])
        st.caption("条件及叙事语境")
        st.json({"conditions": fragment["conditions"], "source_context": fragment["source_context"],
                 "parent_context": fragment["parent_context"], "authority": fragment["authority"]}, expanded=False)
        with st.expander("Parent原文与结构"):
            if view["parent"]:
                for text in view["parent"]["fragments"][0]["texts"]: st.text(text["text"])
                st.json(view["parent"]["fragments"][0]["structure"], expanded=False)
            else: st.warning("Parent尚未读取或读取失败。")
        with st.expander("来源定位"):
            st.json({"turn_id": view["turn_id"], "packet_id": view["packet_id"], "citation_id": view["citation_id"],
                     "source_snapshot": view["receipt"]["source_snapshot"],
                     "clean_and_raw_locators": fragment["texts"]}, expanded=False)
    except (ValueError, OSError, KeyError):
        st.error("来源文件缺失或校验失败；此引用已阻断。")


@st.fragment(run_every=.5)
def transcript(store, worker, sid):
    try: history = store.history(sid)
    except (ValueError, OSError, KeyError):
        st.error("会话引用的文件缺失或校验失败，当前恢复已阻断。请保留本地数据用于检查。")
        return
    for message in history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant":
                st.caption("Mock · " + message["status"] + " · 语义尚未验收")
                for citation in message.get("citations", []):
                    with st.expander("原文引用 " + citation["citation_id"]): render_citation(store, citation)
                if message["status"] != "completed":
                    drafts = [e["text"] for e in store.events(message["turn_id"]) if e["kind"] == "answer_draft"]
                    if drafts:
                        with st.expander("未完成草稿 · 不是最终答案"): st.text("".join(drafts))
                    result = store.load(message["result_ref"])
                    if result.get("sources_ref"):
                        packet = store.load(result["sources_ref"])["packet"]
                        with st.expander("本轮已保存的原文材料"):
                            for cid in packet["citations"]:
                                render_citation(store, {"turn_id": message["turn_id"], "packet_id": packet["packet_id"], "citation_id": cid})
    for job in store.jobs(sid):
        tid = job["turn_id"]
        if job["status"] in TERMINAL: continue
        events = store.events(tid)
        progress = [e for e in events if e["kind"] == "progress"]
        if job["status"] == "interrupted":
            st.error("本轮执行已中断。已有记录保留；继续将沿用原操作与预算。来源、引用或版本问题可能需要先处理。")
        elif job["status"] == "queued":
            st.info("轮次已保存，等待本地执行；重开应用后请点击继续。")
        else:
            st.info(LABELS.get(progress[-1]["stage"], job["status"]) if progress else "正在执行本地轮次")
        drafts = [e["text"] for e in events if e["kind"] == "answer_draft"]
        if drafts:
            st.caption("草稿 · 尚未完成检查")
            st.markdown("".join(drafts))
        if worker and st.button("停止本轮", key="cancel-" + tid):
            worker.cancel(tid); st.rerun()
        if worker and tid != worker.active and tid not in worker.pending:
            if st.button("继续本轮", key="continue-" + tid):
                worker.schedule(tid, continue_turn=True); st.rerun()
    with st.expander("本对话研究要点", expanded=False):
        for note in store.notes(sid):
            st.write({"类型": note["kind"], "内容": note["text"], "状态": note["status"], "身份": note["authority"]})
        st.caption("要点可通过下方纠正消息修订。旧助手回答不会自动成为事实。")
    with st.expander("完整本地历史与整理记录", expanded=False):
        st.json(store.history(sid), expanded=False)
        for job in store.jobs(sid):
            from genshin_corpus.rag.clean_conversation_graph import saved_result
            result = saved_result(store, job["turn_id"])
            if result and result.get("context_ref"):
                ctx = store.load(result["context_ref"])
                st.json({"turn_id": job["turn_id"], "organization": ctx["organization"],
                         "summary": ctx["summary"], "research_state": ctx["research_state"]}, expanded=False)


def main():
    st.set_page_config(page_title="GC 本地研究对话 · Mock", layout="wide")
    st.title("GC 本地研究对话")
    st.warning("G4 离线 Mock：仅使用小型合成资料和模拟回答。当前入口未接入真实连续问答；渠道能力与运行授权见 current-phase。")
    store = ConversationStore(ROOT)
    try: worker = executor(str(store.root), str(REPO))
    except (OSError, ValueError):
        worker = None
        st.info("本地执行器不可用或数据根正被占用；当前页面只读，请保留本地记录以便检查。")
    with st.sidebar:
        st.caption("所有会话仅保存在本机")
        if st.button("新建对话", disabled=worker is None):
            sid = store.create_chat("Mock研究对话", fixture_settings())
            st.query_params["chat"] = sid; st.rerun()
        chats = store.chats()
        if not chats:
            st.info("点击新建对话开始。")
            return
        ids = [c["id"] for c in chats]
        labels = {c["id"]: c["title"] + " · " + c["id"][:8] for c in chats}
        current = st.query_params.get("chat")
        sid = st.selectbox("本地对话", ids, index=ids.index(current) if current in ids else 0,
                           format_func=lambda value: labels[value])
        st.query_params["chat"] = sid
    transcript(store, worker, sid)
    draft = store.draft(sid)
    key = "composer-" + sid
    def edit(): store.edit_draft(sid, st.session_state[key])
    text = st.text_area("继续提问或纠正研究要点", value=draft["text"], key=key, on_change=edit, disabled=worker is None)
    kind = st.selectbox("这条消息的类型", list(KINDS), key="kind-" + sid)
    notes = store.notes(sid)
    targets = st.multiselect("关联或纠正的要点", [n["note_id"] for n in notes],
                            format_func=lambda nid: next(n["text"][:70] for n in notes if n["note_id"] == nid), key="targets-" + sid)
    if st.button("发送", disabled=worker is None or not text.strip()):
        try:
            draft = store.edit_draft(sid, text)
            tid = store.enqueue(sid, draft["token"], text, note_kind=KINDS[kind], targets=targets)
            worker.schedule(tid)
            st.rerun()
        except ValueError as exc: st.error(str(exc))
    if st.button("作为新消息再次发送相同文字", disabled=worker is None or not text.strip()):
        draft = store.edit_draft(sid, text, new=True)
        tid = store.enqueue(sid, draft["token"], text, note_kind=KINDS[kind], targets=targets)
        worker.schedule(tid); st.rerun()


if __name__ == "__main__": main()
