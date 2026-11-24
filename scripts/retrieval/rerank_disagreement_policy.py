"""Candidate-only research policies. The workbench does not import this module."""

import re

from genshin_corpus.chunking.dialogue import label
from genshin_corpus.evidence_io import digest, require
from genshin_corpus.retrieval.clean_based.core import covered_by
from genshin_corpus.retrieval.clean_based.query_v2 import ResearchQuery


SLOT = 6
ROUTE_LIMIT = 20
RANK_GAP = 20
REGISTRATION = "gc-rerank-disagreement-study-1"
ARMS = ("baseline", "origin_slot", "disagreement_only", "guarded_disagreement")

NARROW_QUESTION = re.compile(r"前身|出身|来历|起源|原本来自|最初.{0,10}(?:称号|身份)|早年.{0,12}(?:称号|身份)")
NARROW_VOICE = re.compile(r"诞生|出生|来自|出身|来历|起源|最初|原本|前身|最早|流落")

PERIOD = re.compile(r"前身|出身|来历|起源|原本|最初|最早|早年|幼年|小时候|曾经|还没|之前|以前|当时|那时|一度|过去|卸任|怎么来的")
SOURCE_PERIOD = re.compile(r"诞生|出生|幼年|小时候|当年|早年|那时|最初|早先|之前|原本|前身|还没|曾|起初|最早|流落")
RELATION = {
    "origin": re.compile(r"是|来自|源于|出身|出生|诞生|起源|变成|救活|复活"),
    "identity": re.compile(r"称(?:作|为|呼|号)|(?:以|之|取|改|本)名|名字|叫|出任|担任|辞职|回任|身份"),
}
IDENTITY_ASK = re.compile(r"称号|化名|名字|名号|什么名|身份|什么职务")
HYPOTHETICAL = re.compile(r"难道|假如|如果|可能是|会不会|据说|传闻|猜测|也许|似乎|或许|以为|不知|谁知道")
GAMEPLAY_SECTIONS = frozenset({"天赋", "天赋演示", "命之座", "培养素材", "角色突破", "角色技能"})


def route_ranks(row):
    return {channel: min((h["rank"] for h in row["hits"] if h["channel"] == channel), default=None)
            for channel in ("bm25f", "dense")}


def source_labels(row):
    source = row["source"]["source_context"]
    labels = source["label_evidence"]
    return (label(labels.get("document_title")), label(labels.get("section_title")),
            label(source.get("group_title")))


def narrow(row, question):
    document, section, group = source_labels(row)
    return (document and document in question and section == "配音展示" and group
            and any("我" in p["text"] and NARROW_VOICE.search(p["text"]) for p in row["chunk"]["parts"]))


def anchored_relation(row, question, intent):
    """A scope screen over literal text and existing labels, with no answer knowledge."""
    document, section, group = source_labels(row)
    if not document or document not in question or not section or section in GAMEPLAY_SECTIONS:
        return None
    for part in row["chunk"]["parts"]:
        if part.get("condition_status") == "unresolved" or part.get("condition_sources"):
            continue
        for sentence in re.split(r"[。！？\n]", part["text"]):
            if not sentence or HYPOTHETICAL.search(sentence) or sentence.lstrip().startswith(("「", "“")):
                continue
            voice_owner = section == "配音展示" and group and "我" in sentence
            if (document in sentence or voice_owner) and SOURCE_PERIOD.search(sentence) and RELATION[intent].search(sentence):
                return part["clean_ref"]
    return None


def apply_policy(group, arm):
    """Return the V2 group and a trace. Caller validates source and score bindings."""
    require(arm in ARMS and group["version"] == "gc-clean-ranked-candidates-2", "Unexpected study arm or group")
    query = ResearchQuery.parse(group["query"])
    rows = group["candidates"]
    require([row["reranking"]["final_rank"] for row in rows] == list(range(1, len(rows) + 1))
            and len({row["chunk_id"] for row in rows}) == len(rows), "Invalid baseline ordering")
    if rows:
        require(group["reranking_run"].get("profile") == "bge_structural", "Study requires bge_structural input")
    trace = {"arm": arm, "reason": "baseline", "shortlist": [], "promoted": None, "displaced": None}
    if arm == "baseline":
        return group, trace
    if query.kind != "original" or query.combination != "S0" or len(rows) <= SLOT:
        return group, {**trace, "reason": "outside_first_s0_query_or_no_slot"}

    question = query.original_question
    intent = "identity" if IDENTITY_ASK.search(question) else "origin"
    if arm == "origin_slot" and not NARROW_QUESTION.search(question):
        return group, {**trace, "reason": "outside_original_question_pattern"}

    choices = []
    for row in sorted(rows[SLOT:], key=lambda item: item["organized_rank"]):
        ranking = row["reranking"]
        if ranking["covered_by"] is not None:
            continue
        ranks = route_ranks(row)
        if not all(v is not None and v <= ROUTE_LIMIT for v in ranks.values()):
            continue
        gap = ranking["relevance_rank"] - row["organized_rank"]
        if arm == "origin_slot":
            if not narrow(row, question):
                continue
        elif gap < RANK_GAP:
            continue
        witness = anchored_relation(row, question, intent) if arm == "guarded_disagreement" and PERIOD.search(question) else None
        trace["shortlist"].append({"chunk_id": row["chunk_id"], "input_row": row["input_row"],
                                   "ranks": ranks, "gap": gap, "structural_rank": ranking["final_rank"],
                                   "scope_witness": witness})
        if arm != "guarded_disagreement" or witness is not None:
            if arm != "guarded_disagreement" or row.get("deferred_by") is None:
                choices.append(row)

    if arm == "guarded_disagreement":
        if not PERIOD.search(question):
            return group, {**trace, "reason": "scope_not_supported"}
        if not choices:
            return group, {**trace, "reason": "no_literal_scope_witness"}
        if any(anchored_relation(row, question, intent) for row in rows[:SLOT]):
            return group, {**trace, "reason": "scope_witness_already_in_first_six"}
        if len(choices) != 1:
            return group, {**trace, "reason": "ambiguous_contenders"}
        old = rows[SLOT - 1]
        proof = any(covered_by(old["chunk"], other["chunk"]) for other in rows[:SLOT - 1])
        if not proof and source_labels(old)[1] not in GAMEPLAY_SECTIONS:
            return group, {**trace, "reason": "cannot_justify_displacing_original_sixth"}
    if not choices:
        return group, {**trace, "reason": "no_candidate"}

    picked = choices[0]
    ordered = rows[:SLOT - 1] + [picked] + [row for row in rows[SLOT - 1:] if row is not picked]
    affected = [{**row, "reranking": {**row["reranking"], "structural_rank": row["reranking"]["final_rank"],
                                       "final_rank": i}} for i, row in enumerate(ordered, 1)]
    trace.update(reason="one_boundary_insertion", promoted=picked["chunk_id"], displaced=rows[SLOT - 1]["chunk_id"])
    policy = {"id": REGISTRATION, "arm": arm, "slot": SLOT, "route_limit": ROUTE_LIMIT,
              "rank_gap": None if arm == "origin_slot" else RANK_GAP,
              "baseline_policy_id": group["policy_id"], "baseline_group_digest": digest(group)}
    return {**group, "candidates": affected, "study_ordering": {**policy, "ordering_digest": digest(policy)}}, trace
