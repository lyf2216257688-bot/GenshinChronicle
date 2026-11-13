"""Small reproducible source-text review bundle over already captured P4 results."""
import csv
from statistics import median

from ...build_runtime import atomic_json
from ...evidence_io import digest, read, require, sha, write
from ...indexing.benchmark_io import attempt, receipt


def percentile(values, fraction):
    import math
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def representative(rows, config):
    """One case per predeclared diagnostic direction, including a low-change control."""
    cutoff = str(config["cutoffs"][0])
    def displaced(r, name):
        return r["diagnostics"]["comparisons"][name][cutoff]["left_minus_right"]["only_count"]
    selectors = [
        ("Sparse lexical residual", lambda r: len(r["diagnostics"]["sparse_unique_vs_other_lexical"]["residual_vs_dense"]["not_proven_contained_by_right"])),
        ("BM25F lexical residual", lambda r: len(r["diagnostics"]["bm25f_unique_vs_other_lexical"]["residual_vs_dense"]["not_proven_contained_by_right"])),
        ("S0 candidates displaced by S1", lambda r: displaced(r, "S0/f1_max -> S1/f1_max")),
        ("F1 vs F0 change", lambda r: displaced(r, "S2/f1_max -> S2/f0")),
        ("Structural cleanup change", lambda r: displaced(r, "S2/f1_max -> S2/f1_max/no_cleanup")),
        ("Low-change control", lambda r: -displaced(r, "S0/f1_max -> S2/f1_max"))]
    chosen = []
    for label, score in selectors[:config["representative_questions"]]:
        pending = [r for r in rows if r["question"]["question_id"] not in {c["question_id"] for c in chosen}]
        if pending:
            best = min(pending, key=lambda r: (-score(r), r["question"]["question_id"]))
            chosen.append({"question_id": best["question"]["question_id"], "reason": label, "diagnostic_value": score(best)})
    return chosen


def tsv(path, header, rows):
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def report_job(output, plan, raw, comparisons, config):
    require(set(comparisons) == {q["question_id"] for q in plan["questions"]}, "Incomplete report set")
    folder = attempt(output / "reports", "report")
    summaries, timings, ranks, measures, question_links = [], [], [], [], []
    for q in plan["questions"]:
        qid, unit = q["question_id"], comparisons[q["question_id"]]
        result = read(unit / "result.json")
        require(result["question"] == q and result["input_bundle_id"] == plan["input_bundle_id"], "Report input mismatch")
        # Discard candidate payloads after each question. At most small diagnostics stay resident.
        summaries.append({"question": q, "diagnostics": result["diagnostics"]})
        question_links.append({"question_id": qid, "question": q["question"], "folder": unit.relative_to(output).as_posix()})
        for group in raw:
            value = read(raw[group][qid] / "result.json")
            for ch, stage in value["stages"].items():
                res = stage["resources"]
                timings.append([qid, ch, stage["elapsed_seconds"], stage["encode_seconds"], stage["scan_seconds"],
                    res.get("available_ram_bytes"), res.get("rss_bytes"), res.get("cuda", {}).get("peak_allocated_bytes"),
                    res.get("cuda", {}).get("peak_reserved_bytes")])
        for key, result_profile in result["profiles"].items():
            for candidate in result_profile["candidates"]:
                ranks.append([qid, key, candidate["organized_rank"], candidate["fusion_rank"], candidate["chunk_id"],
                              candidate["input_row"], candidate["fusion_score"], candidate["deferred_by"] or ""])
            for k, values in result["diagnostics"]["profiles"][key]["cutoffs"].items():
                measures.append([qid, key, k, *[values[c] for c in ("chunks", "parents", "observations", "max_chunks_one_parent",
                                                                  "body_union_codepoints", "body_repeated_codepoints")]])
    selected = representative(summaries, config)
    costs = {}
    for ch in ("dense", "sparse", "bm25f"):
        values = [r[2] for r in timings if r[1] == ch]
        costs[ch] = {"queries": len(values), "sum_seconds": sum(values), "median_seconds": median(values),
                     "p95_observed_seconds": percentile(values, .95), "first_query_seconds": values[0],
                     "remaining_median_seconds": median(values[1:]) if len(values) > 1 else None}
    startup = []
    for path in sorted((output / "workers").glob("*/preparation.json")):
        ready = read(path)
        startup.append({"folder": path.parent.relative_to(output).as_posix(), **ready})
    tsv(folder / "costs.tsv", ["question_id", "channel", "elapsed_seconds", "encode_seconds", "scan_seconds", "available_ram_bytes",
        "worker_rss_bytes", "cuda_peak_allocated_bytes", "cuda_peak_reserved_bytes"], timings)
    tsv(folder / "ranks.tsv", ["question_id", "profile", "organized_rank", "fusion_rank", "chunk_id", "input_row", "score", "deferred_by"], ranks)
    tsv(folder / "coverage.tsv", ["question_id", "profile", "cutoff", "chunks", "parents", "observations", "max_chunks_one_parent",
        "body_union_codepoints", "body_repeated_codepoints"], measures)
    write(folder / "diagnostics.json", summaries)
    write(folder / "questions.json", question_links)
    summary = {"scope": plan["scope"], "questions": len(plan["questions"]), "profiles": list(plan["profiles"]),
               "costs": costs, "worker_preparation": startup, "representative_selection": selected,
               "decision": "USER_DECIDES; no automatic S0/S1/S2 adoption", "quality": "UNKNOWN until source-text inspection",
               "metric_scope": "top-depth route windows and finite cutoffs; not corpus recall, nDCG or semantic truth"}
    count = len(summaries)
    summary["channel_averages"] = {ch: {key: sum(r["diagnostics"]["channels"][ch][key] for r in summaries) / count
        for key in ("chunks", "parents", "body_union_codepoints")} for ch in costs}
    summary["lexical_residuals"] = {}
    for ch in ("sparse", "bm25f"):
        residuals = [r["diagnostics"][ch + "_unique_vs_other_lexical"] for r in summaries]
        summary["lexical_residuals"][ch] = {
            "question_candidate_occurrences_unique_vs_other": sum(len(r["ids"]) for r in residuals),
            "also_dense_same_id": sum(len(r["also_dense"]) for r in residuals),
            "not_proven_contained_by_dense": sum(len(r["residual_vs_dense"]["not_proven_contained_by_right"]) for r in residuals),
            "questions_with_residual": sum(bool(r["residual_vs_dense"]["not_proven_contained_by_right"]) for r in residuals)}
    cutoff = str(config["cutoffs"][0])
    summary["front_changes"] = {name: {
        "mean_left_displaced": sum(r["diagnostics"]["comparisons"][name][cutoff]["left_minus_right"]["only_count"] for r in summaries) / count,
        "mean_right_added": sum(r["diagnostics"]["comparisons"][name][cutoff]["right_minus_left"]["only_count"] for r in summaries) / count,
        "questions_with_left_displaced": sum(bool(r["diagnostics"]["comparisons"][name][cutoff]["left_minus_right"]["only_count"]) for r in summaries)}
        for name in summaries[0]["diagnostics"]["comparisons"]}
    write(folder / "summary.json", summary)
    text = ["# P4 召回对照报告", "", f"范围：{plan['scope']}；{len(plan['questions'])} 题；12 个复用原始命中的配置。", "",
            "工程对照报告；完整70题只有 scope=70q、questions=70 才成立。没有自动相关性标签或胜出算法。", "",
            "主对照为 S0/S1/S2 的 F1_max；F0、F1_rrf 与关闭结构整理是有限控制项。所有组合使用同一份 Dense 原始命中；",
            "通道双路深度相同、候选上限相同。S2 的两个词法通道共享一半权重，不通过增加总权重获益。", "",
            "## 自动诊断与原文", "",
            "[逐题诊断](diagnostics.json)保存宏微分别/并集重合率、双向独有、Dense覆盖及同范围包含证明。",
            "[覆盖表](coverage.tsv)和[完整名次表](ranks.tsv)保留所有题及12配置的变化；",
            "[逐题目录](questions.json)指向全部原文，而非只保留精选案例。", "",
            "Chunk 独有不等于证据独有；字符覆盖不等于剧情有效性。只有同观测/父层/限定/语境的严格包含才标记安全包含。",
            "字符并集仅统计主正文，不将别名、不同条件或显示顺序解释为语义等价。单篇资料不设配额。", "",
            "抽查时依次看：是否回答问题、必要条件/选项是否保留、两词法独有是否已被Dense提供、S0前排被挤出后是否损失关键线索。",
            "不要求盲审、完整标注或nDCG；最终结合有效证据、独有收益、明显退化与运行成本由用户选择。", "",
            "## 词法独有与Dense覆盖", "", "按题累计候选出现次数，同一Chunk跨题可能重复；均只针对既定TopK窗口。", "",
            "| 词法通道 | 相对另一词法独有 | Dense同ID覆盖 | 未证实被Dense包含 | 有剩余候选的题数 |",
            "|---|---:|---:|---:|---:|"]
    for ch, row in summary["lexical_residuals"].items():
        text.append(f"| {ch} | {row['question_candidate_occurrences_unique_vs_other']} | {row['also_dense_same_id']} | {row['not_proven_contained_by_dense']} | {row['questions_with_residual']} |")
    text += ["", f"## 前排变化@{cutoff}", "", "左侧被挤出/右侧新增只是身份变化，须看原文判断损益。", "",
             "| 对照 | 平均左侧被挤出 | 平均右侧新增 | 出现挤出的题数 |", "|---|---:|---:|---:|"]
    for name, row in summary["front_changes"].items():
        text.append(f"| {name} | {row['mean_left_displaced']:.2f} | {row['mean_right_added']:.2f} | {row['questions_with_left_displaced']} |")
    text += ["",
            "## 有方向的代表性案例", "",
            "以下按预先定义的差异方向取例，包含低差异控制；是诊断样本，不能代表70题质量比例。", ""]
    for item in selected:
        qid = item["question_id"]
        source = comparisons[qid] / "cases.md"
        with (folder / (qid + ".md")).open("xb") as handle:
            handle.write(source.read_bytes())
        text += [f"- [{qid} 实际原文]({qid}.md)：{item['reason']}；指标={item['diagnostic_value']}"]
    text += ["", "## 运行成本", "", "| 通道 | 查询数 | 中位秒 | 观测P95秒 | 首题秒 |", "|---|---:|---:|---:|---:|"]
    for ch, c in costs.items():
        text.append(f"| {ch} | {c['queries']} | {c['median_seconds']:.3f} | {c['p95_observed_seconds']:.3f} | {c['first_query_seconds']:.3f} |")
    text += ["", "[逐题耗时及资源](costs.tsv)；加载/校验开销单列在 summary.json 的 worker_preparation。",
             "Dense/Sparse 共用一个模型、两次单题前向；Dense首题含延迟模型加载，Sparse随后复用。BM25F在模型进程退出后运行。",
             "profile诊断的serial_channel_seconds是通道分量加和，另列融合耗时；它不是实测在线延迟。",
             "文件缓存、固定通道顺序、冷启动和首题效应会影响时间；未计入后续重排/生成，也未据此开展调参。", "",
             "## 验证与选型", "", "Run 已检查完整检查点和绑定；请另行执行 Validate，独立回放所有排名、处置和诊断并逐候选核对原文。",
             "Report只重建报告，不重新检索。没有重建索引、调用付费API或切换Serving。", "",
             "有效剧情证据及明显退化需要结合上述原文判断；本报告不把差异自动改写为收益/退化，不设置机械零损失门槛。", ""]
    (folder / "report.md").write_text("\n".join(text), encoding="utf-8")
    receipt(folder, digest(plan), kind="report", scope=plan["scope"])
    atomic_json(output / "report.json", {"folder": folder.relative_to(output).as_posix(),
        "receipt_sha256": sha(folder / "complete.json"), "report": (folder / "report.md").relative_to(output).as_posix()})
    return summary
