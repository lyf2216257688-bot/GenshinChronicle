"""Versioned Clean contracts and source-preserving JSON primitives."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, TypedDict

from ..evidence_io import digest, strict_json

SCHEMA_VERSION = "gc-clean-1"
CLEANER_VERSION = "obc-clean-1"
RULESET_VERSION = "obc-clean-rules-2"
RENDERER_VERSION = "clean-reader-1"


class TextValue(TypedDict, total=False):
    text: str
    origin_id: str
    segments: list[dict[str, Any]]
    marks: list[dict[str, Any]]


class CleanRecord(TypedDict):
    schema_version: str
    observation_id: str
    source_item: dict[str, str]
    input: dict[str, Any]
    artifacts: list[dict[str, str]]
    origins: dict[str, dict[str, Any]]
    document: dict[str, Any]
    sections: list[dict[str, Any]]
    relations: list[dict[str, Any]]
    media: list[dict[str, Any]]
    extensions: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]
    coverage: dict[str, Any]


@dataclass(frozen=True)
class CleanRef:
    observation_id: str
    block_id: str
    value_pointer: str
    char_start: int
    char_end: int

    def __post_init__(self) -> None:
        if not self.observation_id or not self.block_id:
            raise ValueError("CleanRef requires observation and block identities")
        if type(self.char_start) is not int or type(self.char_end) is not int:
            raise ValueError("CleanRef ranges must be integer code point offsets")
        if self.char_start < 0 or self.char_end < self.char_start:
            raise ValueError("Invalid CleanRef range")


@dataclass(frozen=True)
class BlockSelector:
    """OR within a dimension, AND across dimensions. Tags match exact triples."""

    channel_ids: tuple[str, ...] = ()
    component_ids: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    field_keys: tuple[str, ...] = ()
    field_prefixes: tuple[str, ...] = ()
    field_labels: tuple[str, ...] = ()
    tags: tuple[tuple[str, str, str], ...] = ()


class SourceNumber(str):
    """A JSON number token, including -0, exponent notation and trailing zeros."""

    def __new__(cls, token: str, source_type: str = "integer") -> SourceNumber:
        instance = super().__new__(cls, token)
        instance.source_type = source_type
        return instance


def source_json(text: str) -> Any:
    # Reuse the shared duplicate-key/nonfinite checks before lexical decoding.
    strict_json(text)
    return json.loads(
        text,
        parse_int=SourceNumber,
        parse_float=lambda token: SourceNumber(token, "number"),
    )


def pointer_token(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def pointer_get(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise ValueError("JSON Pointer must be empty or start with /")
    for part in pointer[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not part.isdigit() or (len(part) > 1 and part.startswith("0")):
                raise ValueError("Invalid JSON array index")
            value = value[int(part)]
        else:
            value = value[part]
    return value


def source_leaves(value: Any, path: str = ""):
    """Count empty containers as source occurrences as well as scalar values."""
    if isinstance(value, dict) and value:
        for key, child in value.items():
            yield from source_leaves(child, path + "/" + pointer_token(key))
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):
            yield from source_leaves(child, path + "/" + str(index))
    else:
        yield path


def dependencies() -> dict[str, str]:
    return {
        "schema_version": SCHEMA_VERSION,
        "cleaner_version": CLEANER_VERSION,
        "ruleset_version": RULESET_VERSION,
    }


def observation_id(source_item: dict[str, str], raw_run_id: str, fingerprint: str) -> str:
    return digest({"item": source_item, "raw_run_id": raw_run_id, "input": fingerprint,
                   "versions": dependencies()})
