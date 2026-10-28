"""Pure Responses user-input compatibility adapter for Phase05 F2.

No API calls, no retries, no changes to the frozen F2 runner. This does NOT
repair the provider's SSE reasoning IDs: it records that protocol failure and
separately accepts a mechanically verifiable assistant message.
"""
from hashlib import sha256

from . import navi_f2_c1 as c1, navi_f2_preflight as pre
from .navi_f1_prepare import value_hash
from .navi_v02_message_verifier import inspect


def render_user_wire(case, *, max_output_tokens=12000):
    """Render the successfully observed user-input Responses shape for either arm."""
    messages = case.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        raise ValueError("Expected two frozen messages")
    if value_hash(messages) != case.get("messages_sha256"):
        raise ValueError("Frozen input fingerprint mismatch")
    if [m.get("role") for m in messages] != ["system", "user"]:
        raise ValueError("Frozen message roles differ")
    if not all(isinstance(m.get("content"), str) and m["content"] for m in messages):
        raise ValueError("Empty or malformed prompt/source content")
    if type(max_output_tokens) is not int or max_output_tokens <= 0:
        raise ValueError("Invalid output limit")
    return {
        "model": c1.MODEL,
        "stream": True,
        "max_output_tokens": max_output_tokens,
        "input": messages[0]["content"] + "\n\n" + messages[1]["content"],
    }


def render_user_json_mode_wire(case, *, max_output_tokens=16000):
    """Return the exact Responses shape verified by BOTH F2 prompt arms."""
    original = render_user_wire(case, max_output_tokens=max_output_tokens)
    return {
        "model": original["model"],
        "stream": original["stream"],
        "max_output_tokens": original["max_output_tokens"],
        "input": [{"role": "user", "content": original["input"]}],
        "text": {"format": {"type": "json_object"}},
    }


def assess_content(raw_events, sdk_events, case, *, request_wire,
                   schema=None, packet=None, evidence=None, legacy_validator=None):
    """Assess a completed archived stream without concealing upstream defects.

    Return a mechanically accepted result only if message event integrity,
    SDK/raw equality, local contract and evidence ID bindings all pass.
    This does not certify the meaning of the Navi proposition.
    """
    if not isinstance(request_wire, dict):
        raise ValueError("Archived wire request is required")
    output_cap = request_wire.get("max_output_tokens")
    acceptable = (
        render_user_wire(case, max_output_tokens=output_cap),
        render_user_json_mode_wire(case, max_output_tokens=output_cap),
    )
    if request_wire not in acceptable:
        raise ValueError("Archived request and frozen user-input wire differ")
    wire = request_wire
    checked = inspect(raw_events, sdk_events)
    problems = checked["reasoning_id_conflicts"]
    if checked["full_stream"] != "PASS" and not problems:
        raise ValueError("Unexplained full-stream protocol failure")
    if case.get("arm") not in ("legacy", "rc1"):
        raise ValueError("Unknown frozen F2 arm")
    if case["arm"] == "legacy" and any(x is None for x in (packet, evidence, legacy_validator)):
        raise ValueError("Legacy source and validator required")
    if case["arm"] == "rc1" and schema is None:
        raise ValueError("v0.2 schema required")
    verdict = pre.validate_content(
        checked["message_text"], case, packet, evidence, schema, legacy_validator
    )
    mechanical = verdict.get("status") == "VALIDATED_MECHANICAL_ONLY"
    return {
        "state": (
            "CONTENT_VERIFIED_PROTOCOL_WARNING"
            if mechanical and problems else
            "CONTENT_VERIFIED"
            if mechanical else
            "CONTENT_INVALID"
        ),
        "mechanical_status": "PASS" if mechanical else "FAIL",
        "full_stream_status": checked["full_stream"],
        "protocol_warning": "REASONING_ITEM_ID_CONFLICT" if problems else None,
        "reasoning_id_conflicts": problems,
        "message_sha256": sha256(checked["message_text"].encode("utf-8")).hexdigest(),
        "usage": checked["usage"],
        "max_output_tokens": wire["max_output_tokens"],
        "output_token_headroom": wire["max_output_tokens"] - checked["usage"]["output_tokens"],
        "near_output_limit": checked["usage"]["output_tokens"] * 100 >= wire["max_output_tokens"] * 95,
        "source_request_id": case.get("request_id"),
        "validation": verdict,
        "semantic_quality": "NOT_VALIDATED",
        "human_acceptance": "NOT_REVIEWED",
        "navigation_status": "INACTIVE",
        "formal_f2_execution_authorized": False,
    }
