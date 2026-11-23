"""Build the product's bounded conversation projection and immutable local audit."""
from collections import defaultdict

from ..evidence_io import digest, require
from ..generation.clean_evidence import estimate_tokens
from ..rag.clean_context import PRODUCT_VERSION
from ..rag.clean_conversation import VERSION as MESSAGE_VERSION

BUDGET_EXCEEDED = "PRODUCT_CONTEXT_BUDGET_EXCEEDED"
REVIEW_TEXT = "[旧前提已被纠正；原消息仅供本地回查]"


def _protected_note(note, current_message_id):
    return note["kind"] in {"requirement", "correction_intent"} or current_message_id in note["message_ids"]


def _conversation(notes, current_message_id):
    active = []
    for note in notes:
        if note["status"] != "active" or not _protected_note(note, current_message_id):
            continue
        projected = dict(note)
        if current_message_id in note["message_ids"]:
            # The same original wording is already in the outer question field.
            del projected["text"]
            projected["text_source"] = "current_question"
        active.append(projected)
    return {"recent": [], "summary": [], "research_state": {
        "notes": active, "needs_review": [], "supported_judgments": [],
        "support_policy": "historical_statements_are_not_source_evidence"}}


def _dialogue_bytes(question, conversation):
    return estimate_tokens({"question": question, "conversation": conversation})


def check_context_budget(question, notes, *, current_message_id, history_bytes):
    require(type(history_bytes) is int and history_bytes > 0, "Finite product dialogue budget required")
    size = _dialogue_bytes(question, _conversation(notes, current_message_id))
    require(size <= history_bytes, BUDGET_EXCEEDED)
    return size


def _message(message, stale_messages):
    stale = message["message_id"] in stale_messages
    return {"message_id": message["message_id"], "turn_id": message["turn_id"], "role": message["role"],
            "content": REVIEW_TEXT if stale else message["content"],
            "authority": ("historical_assistant_not_fact" if message["role"] == "assistant" else
                          "user_input_not_verified_story_fact"),
            "requires_review": stale, "citations": [] if stale else message.get("citations", []),
            "outcome": message.get("status", "user_input")}


def _excerpt(row, original, chars):
    text = "" if row["requires_review"] else row["content"][:chars]
    return {key: row[key] for key in ("message_id", "turn_id", "role", "authority", "requires_review", "citations")} | {
        "excerpt": text, "omitted_chars": len(original["content"]) - len(text)}


def _prior_research(store, history, affected_turns, count):
    from ..rag.clean_research import verify_data

    for message in reversed(history):
        if message["role"] != "assistant" or not message.get("result_ref"):
            continue
        result = store.load(message["result_ref"])
        if not result.get("research_ref"):
            continue
        data = verify_data(store, result["research_ref"])
        affected = message["turn_id"] in affected_turns
        yield {"turn_id": message["turn_id"], "research_ref": result["research_ref"],
               "status": "needs_review" if affected else "unverified_model_research",
               "research": {} if affected else data["research"], "stop_reason": result["stop_reason"]}
        count -= 1
        if not count:
            break


def organize(store, tid, settings, *, system, include_research=False):
    settings = {key: settings[key] for key in ("history_bytes", "recent_turns", "excerpt_chars")}
    require(all(type(n) is int and n > 0 for n in settings.values()), "Finite product context settings required")
    turn = store.turn(tid)
    question = turn["question"]
    history = store.history(turn["session"], before=tid)
    notes = store.notes(turn["session"], before=tid)
    current_message = digest([MESSAGE_VERSION, tid, "user"])
    conversation = _conversation(notes, current_message)
    state = conversation["research_state"]
    if include_research:
        state["prior_research"] = []
    # Empty prior_research is optional. The submit check uses the same protected
    # projection regardless of which engine runs the turn.
    remaining = settings["history_bytes"] - _dialogue_bytes(question, conversation)
    if include_research and remaining < 0:
        del state["prior_research"]
        remaining = settings["history_bytes"] - _dialogue_bytes(question, conversation)
    require(remaining >= 0, BUDGET_EXCEEDED)

    def add(entries, item):
        nonlocal remaining
        cost = estimate_tokens(item) + bool(entries)
        if cost > remaining:
            return False
        entries.append(item)
        remaining -= cost
        return True

    stale_notes = [note for note in notes if note["status"] == "needs_review"]
    active_notes = [note for note in notes if note["status"] == "active"]
    stale_messages = {mid for note in stale_notes for mid in note["message_ids"]}
    active_messages = {mid for note in active_notes for mid in note["message_ids"]}
    history_order = {message["message_id"]: index for index, message in enumerate(history)}
    exchanges = defaultdict(list)
    for message in history:
        if message["message_id"] not in active_messages:
            exchanges[message["turn_id"]].append(message)

    visible_ids = {mid for note in state["notes"] for mid in note["message_ids"]}
    # Try the newest whole messages first. A long previous answer must not
    # prevent admission of the original user question from that exchange.
    recent = conversation["recent"]
    for exchange in reversed(list(exchanges.values())[-settings["recent_turns"]:]):
        for message in exchange:
            if add(recent, _message(message, stale_messages)):
                visible_ids.add(message["message_id"])
    recent.sort(key=lambda item: history_order[item["message_id"]])

    omitted_note_ids = []
    for note in reversed(active_notes):
        if not _protected_note(note, current_message):
            if add(state["notes"], note):
                visible_ids.update(note["message_ids"])
            else:
                omitted_note_ids.append(note["note_id"])
    note_order = {note["note_id"]: index for index, note in enumerate(notes)}
    state["notes"].sort(key=lambda note: note_order[note["note_id"]])

    if "prior_research" in state:
        affected_turns = {note["turn_id"] for note in stale_notes}
        for research in _prior_research(store, history, affected_turns, settings["recent_turns"]):
            add(state["prior_research"], research)
        state["prior_research"].reverse()

    for note in reversed(stale_notes):
        if not add(state["needs_review"], {"note_id": note["note_id"], "status": note["status"]}):
            break
    state["needs_review"].reverse()

    summary = conversation["summary"]
    for message in reversed(history):
        if message["message_id"] in visible_ids or message["message_id"] in active_messages:
            continue
        row = _message(message, stale_messages)
        if add(summary, _excerpt(row, message, settings["excerpt_chars"])):
            visible_ids.add(message["message_id"])
    summary.reverse()
    value = {"version": PRODUCT_VERSION, "session_id": turn["session"], "turn_id": tid,
             "question_digest": digest(question), "history_digest": digest(history), "notes_digest": digest(notes),
             **conversation, "protocol_history": [], "tools": [], "system": system,
             "organization": {"method": "deterministic_extractive_index", **settings, "remote_calls": 0,
                              "semantic_acceptance": "NOT_EVALUATED",
                              "compressed": len(recent) != len(history),
                              "original_message_ids": [message["message_id"] for message in history],
                              "requires_review_message_ids": sorted(stale_messages),
                              "omitted_note_ids": list(reversed(omitted_note_ids)),
                              "omitted_message_ids": [mid for mid in history_order if mid not in visible_ids],
                              "dialogue_bytes": settings["history_bytes"] - remaining}}
    value["context_id"] = digest(value)
    return value


def snapshot(store, tid, settings, *, system, include_research=False):
    """Pin the exact context across a worker crash before its graph checkpoint."""
    published = next((event["ref"] for event in store.events(tid)
                      if event["kind"] == "product_context_published"), None)
    ref = store.published_artifact(tid, "context")
    require(published is None or published == ref, "Published product context changed")
    if ref is None:
        value = organize(store, tid, settings, system=system, include_research=include_research)
        ref = store.artifact(tid, "context", value, atomic=True)
    else:
        value = store.load(ref)
        turn = store.turn(tid)
        require(value["version"] == PRODUCT_VERSION and value["turn_id"] == tid
                and value["session_id"] == turn["session"] and value["system"] == system
                and value["question_digest"] == digest(turn["question"])
                and value["context_id"] == digest({key: item for key, item in value.items() if key != "context_id"})
                and all(value["organization"][key] == settings[key]
                        for key in ("history_bytes", "recent_turns", "excerpt_chars")),
                "Product context identity changed")
        conversation = {key: value[key] for key in ("recent", "summary", "research_state")}
        require(_dialogue_bytes(turn["question"], conversation) == value["organization"]["dialogue_bytes"]
                <= settings["history_bytes"], "Product context budget changed")
    if published is None:
        store.event(tid, "product-context-published", {"kind": "product_context_published", "ref": ref}, atomic=True)
    return ref
