"""Bind published answer markers to the exact synthesis Packet."""

import re

from ..generation.clean_evidence import validate_citations


_ID = r"E[0-9]+"
_IDS = rf"{_ID}(?:\s*[,，、]\s*{_ID})*"
_MARKER = re.compile("|".join(
    rf"{re.escape(left)}\s*(?:{_IDS})\s*{re.escape(right)}"
    for left, right in (("[", "]"), ("［", "］"), ("【", "】"), ("(", ")"), ("（", "）"))
))


def bind_answer(packet, answer):
    """Normalize only explicit, citation-only groups. The sealed response stays raw."""
    ordered_ids = {}

    def replace(match):
        ids = re.findall(_ID, match.group())
        ordered_ids.update(dict.fromkeys(ids))
        return "".join(f"[{identifier}]" for identifier in ids)

    display = _MARKER.sub(replace, answer)
    ids = list(ordered_ids)
    result = validate_citations(packet, ids)
    if not ids:
        result["status"] = "MISSING"
    return display, ids, result


def unbind_working_note(value):
    """Prevent old working-note markers from being read as final Packet addresses."""
    if isinstance(value, str):
        return _MARKER.sub("（前序查证标记）", value)
    if isinstance(value, list):
        return [unbind_working_note(item) for item in value]
    if isinstance(value, dict):
        return {key: unbind_working_note(item) for key, item in value.items()}
    return value
