"""Thin local Streamlit frontend for the accepted single-question RAG backend."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from genshin_corpus.ui.runtime import (
    UiConfigurationError,
    UiQuery,
    execute_query,
    prepare_production_state,
    production_artifact_paths,
    release_prepared_state,
    resolve_output_root,
    summarize_result,
    validate_prepared_state,
)


@st.cache_resource(
    scope="session",
    validate=validate_prepared_state,
    on_release=release_prepared_state,
)
def _prepared_state(paths_key: tuple[str, str, str]):
    return prepare_production_state(paths_key)


def _render_result(result: dict) -> None:
    summary = summarize_result(result)
    if summary["status"] == "succeeded":
        st.success("运行成功")
    else:
        st.error("运行失败")

    if summary["final_answer"]:
        st.subheader("回答")
        st.markdown(str(summary["final_answer"]))

    validation = summary["citation_validation"]
    if isinstance(validation, dict):
        st.subheader("引用验证")
        st.write({
            "完整性": validation.get("citation_integrity", validation.get("status")),
            "覆盖": validation.get("citation_coverage"),
            "语义忠实度": validation.get("semantic_faithfulness"),
        })

    st.subheader("证据")
    st.write({
        "证据块": summary["evidence_count"],
        "已用上下文字符": summary["used_context_chars"],
        "上下文预算": summary["total_context_chars"],
    })
    for item in summary["evidence"]:
        if isinstance(item, dict):
            with st.expander(str(item.get("evidence_id", "证据"))):
                st.write(item.get("text", ""))

    persistence = summary["persistence"]
    if persistence:
        st.subheader("落盘位置")
        st.write({key: value for key, value in persistence.items() if key in {"base_output_root", "run_root", "evidence_packet", "generation", "result"}})

    st.subheader("运行信息")
    st.write({
        "执行身份": summary["execution_identity"],
        "阶段耗时（秒）": summary["timing_seconds"],
    })
    if summary["error"]:
        st.warning(summary["error"])


def _render_result_safely(result: dict) -> None:
    try:
        _render_result(result)
    except Exception:
        st.error("结果展示失败：unexpected_error")


def main() -> None:
    st.set_page_config(page_title="Genshin Chronicle RAG", layout="wide")
    st.title("Genshin Chronicle RAG")
    question = st.text_area("问题", height=140)
    mode = st.radio("执行模式", ("evidence_only", "generate_answer"), horizontal=True)
    output_root = st.text_input("输出根目录", value=str(Path(".local/p04-single-question-ui")))

    submitted = st.button("运行", type="primary", disabled=not question.strip() or not output_root.strip())
    if not submitted:
        previous = st.session_state.get("last_result")
        if isinstance(previous, dict):
            _render_result_safely(previous)
        return

    try:
        resolved_output_root = resolve_output_root(output_root)
        paths_key = production_artifact_paths().resolved_key()
        query = UiQuery(question_text=question, execution_mode=mode, output_root=resolved_output_root)
        with st.status("正在运行", expanded=False):
            owner = _prepared_state(paths_key)
            with owner.lease() as prepared:
                result = execute_query(prepared, query)
        st.session_state["last_result"] = result
    except UiConfigurationError as exc:
        st.error(f"配置不可用：{exc}")
        return
    except Exception:
        st.error("运行准备失败：unexpected_error")
        return

    _render_result_safely(result)


if __name__ == "__main__":
    main()
