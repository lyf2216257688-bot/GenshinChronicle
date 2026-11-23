"""Budgeted navigation over the current turn's sealed ranked candidates."""
from difflib import SequenceMatcher

from ..chunking.dialogue import label
from ..evidence_io import digest, require
from ..generation.clean_chat import encoded


VERSION = "gc-clean-candidate-catalog-2"
CATALOG_TOOL = "list_candidates"
MAX_CANDIDATES = 100
INITIAL_CATALOG_BYTES = 16_000
CATALOG_PAGE_ROWS = 20
CATALOG_PAGE_BYTES = 8_000
CATALOG_ARGUMENTS = {
    "query_id": {"type": "string", "minLength": 1, "maxLength": 80},
    "after_rank": {"type": "integer", "minimum": 0, "maximum": MAX_CANDIDATES},
}
READ_SCOPES = ("chunk", "parent", "natural_parent", "graph")


def _ranked_group(store, data, query_id):
    queries = data["queries"]
    groups = data["groups"]
    require(len(queries) == len(groups), "Candidate query/group count changed")
    matches = [i for i, query in enumerate(queries) if query["query_id"] == query_id]
    require(len(matches) <= 1, "Duplicate candidate query ID")
    if not matches:
        return None
    index = matches[0]
    group = store.load(groups[index])
    candidates = group["candidates"]
    require(group["version"] == "gc-clean-ranked-candidates-2" and group["query"] == queries[index]
            and group["status"] == ("complete" if candidates else "empty")
            and len(candidates) <= MAX_CANDIDATES, "Candidate group changed")
    require(all(c["reranking"]["final_rank"] == rank for rank, c in enumerate(candidates, 1)),
            "Candidate group ordering changed")
    require(len({c["chunk_id"] for c in candidates}) == len(candidates), "Duplicate ranked candidate")
    return group


def catalog_argument_issue(store, data, args):
    """Check query membership and the cursor before any action in a tool batch."""
    group = _ranked_group(store, data, args["query_id"])
    if group is None:
        return {"path": "$.query_id", "code": "UNKNOWN_CANDIDATE_QUERY",
                "allowed": [query["query_id"] for query in data["queries"]]}
    total = len(group["candidates"])
    if type(args["after_rank"]) is not int or not 0 <= args["after_rank"] <= total:
        return {"path": "$.after_rank", "code": "CANDIDATE_CURSOR_OUT_OF_RANGE", "maximum": total}
    return None


def _preview(parts, matcher):
    # Oriented by literal overlap only. This chooses a navigation excerpt and
    # neither changes candidate rank nor licenses a quotation in an answer.
    ranked_lines = []
    first = None
    for index, part in enumerate(parts):
        body = part["text"]
        if not body.strip():
            continue
        lead = body[:48]
        if first is None:
            first = lead
        matcher.set_seq1(body)
        match = matcher.find_longest_match()
        if sum(char.isalnum() for char in body[match.a:match.a + match.size]) >= 3:
            # Show the full line if it fits, including its literal speaker
            # prefix or opening context when the match is near the end.
            start = min(max(0, match.a - 10), max(0, len(body) - 48))
            ranked_lines.append((-match.size, index, start, body[start:start + 48], lead))
    if not ranked_lines:
        return [first] if first is not None else []

    selected = {}
    for _, index, start, text, _ in sorted(ranked_lines):
        if text not in selected:
            selected[text] = (index, start)
        if len(selected) == 2:
            break
    # A single matching line can be long, such as a complete voice-over.
    # Give it a separate lead when the matching excerpt starts well inside it.
    if len(selected) == 1:
        _, index, start, _, lead = min(ranked_lines)
        if start >= 24:
            selected.setdefault(lead, (index, 0))
    return sorted(selected, key=selected.get)


def _row(candidate, rank, handle, matcher, body_status, source_defaults, chunk_visible):
    source = candidate["source"]
    context = source["source_context"]
    labels = context["label_evidence"]
    title = label(labels.get("document_title"))
    section = label(labels.get("section_title"))
    subsection = label(context.get("group_title"))
    condition = next((value["text"] for condition in source["conditions"] for value in condition["sources"]
                      if value.get("text")), None)
    item = {key: value for key, value in source["source_item"].items() if key not in source_defaults}
    row = {"rank": rank, "handle": handle, "source_item": item, "body_status": body_status}
    for name, value, limit in (("title", title, 36), ("section", section, 24),
                               ("subsection", subsection, 24), ("condition_cue", condition, 24)):
        if value:
            row[name] = value[:limit]
    # A second copy of visible source wording adds no navigation value.
    if not chunk_visible:
        preview = _preview(candidate["chunk"]["parts"], matcher)
        if preview:
            row["preview"] = preview
    return row


def _byte_bounded_page(query_id, total, after_rank, rows, source_defaults, max_bytes, fits):
    def payload(count):
        through_rank = after_rank + count
        return {
            "version": VERSION, "query_id": query_id, "total_candidates": total,
            "after_rank": after_rank, "through_rank": through_rank,
            "next_after_rank": through_rank if through_rank < total else None,
            "navigation_only": True, "source_defaults": source_defaults, "rows": rows[:count],
        }

    def admissible(value):
        return len(encoded(value)) <= max_bytes and (fits is None or fits(value))

    largest = payload(len(rows))
    if admissible(largest):
        return largest
    # A row always costs more than the change in the count/cursor fields.
    lower, upper = 1, len(rows) - 1
    best = None
    while lower <= upper:
        count = (lower + upper) // 2
        value = payload(count)
        if admissible(value):
            best, lower = value, count + 1
        else:
            upper = count - 1
    return best


def candidate_page(store, data, query_id, *, after_rank=0, max_rows=MAX_CANDIDATES,
                   max_bytes=INITIAL_CATALOG_BYTES, packet=None, exposed_ids=(), fits=None):
    """Return the largest fitting ordered prefix, or None when no row fits.

    The store verifies the existing group and receipt hashes. Every row uses
    a previously registered handle. An optional monotonic predicate can check
    the enclosing request as well as the catalog's own UTF-8 byte limit.
    """
    group = _ranked_group(store, data, query_id)
    require(group is not None, "UNKNOWN_CANDIDATE_QUERY")
    candidates = group["candidates"]
    total = len(candidates)
    require(type(after_rank) is int and 0 <= after_rank <= total, "CANDIDATE_CURSOR_OUT_OF_RANGE")
    require(type(max_rows) is int and 0 < max_rows <= MAX_CANDIDATES
            and type(max_bytes) is int and max_bytes > 0, "INVALID_CANDIDATE_PAGE_LIMIT")
    handles = {record["chunk_id"]: handle for handle, record in data["handles"].items()}
    require(len(handles) == len(data["handles"]), "Conflicting handles for candidate")
    reads = data["reads"]
    packet_receipts = {item["receipt_id"] for item in packet["evidence"]} if packet is not None else set()
    exposed = set(exposed_ids)
    history_receipts = {rid for rid, ref in reads.items()
                        if exposed and any(fragment["fragment_id"] in exposed
                                           for fragment in store.load(ref)["fragments"])}
    failed_chunks = {failure["chunk_id"] for failure in data["failures"]}
    matcher = SequenceMatcher(None, "", group["query"]["search_text"][:500], autojunk=False)
    available = candidates[after_rank:after_rank + max_rows]
    source_items = [candidate["source"]["source_item"] for candidate in available]
    source_defaults = {}
    if source_items:
        first = source_items[0]
        source_defaults = {key: first[key] for key in ("source", "locale")
                           if key in first and all(first[key] == item.get(key) for item in source_items)}
    rows = []
    for rank, candidate in enumerate(available, after_rank + 1):
        chunk = candidate["chunk_id"]
        require(chunk in handles, "Ranked candidate has no registered handle")
        handle = handles[chunk]
        scope_ids = {scope: data["read_keys"].get(digest([handle, scope])) for scope in READ_SCOPES}
        receipt_ids = set(scope_ids.values()) & reads.keys()
        if receipt_ids & packet_receipts:
            body_status = "in_current_packet"
        elif receipt_ids & history_receipts:
            body_status = "exposed_in_history"
        elif receipt_ids:
            body_status = "read_not_exposed"
        else:
            body_status = "read_failed" if chunk in failed_chunks else "not_read"
        chunk_visible = scope_ids["chunk"] in packet_receipts or scope_ids["chunk"] in history_receipts
        rows.append(_row(candidate, rank, handle, matcher, body_status, source_defaults, chunk_visible))
    return _byte_bounded_page(query_id, total, after_rank, rows, source_defaults, max_bytes, fits)
