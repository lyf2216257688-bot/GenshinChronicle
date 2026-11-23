"""Typed public transport contract. The server and generated Vue client share these models."""
import json
from pathlib import Path
from typing import Annotated, Literal, get_args
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic.json_schema import models_json_schema

Mode = Literal["MOCK", "LIVE"]
Kind = Literal["question", "requirement", "correction_fact", "correction_intent"]
Category = Literal["answer", "missing_evidence", "citation", "other"]
Title = Annotated[str, Field(min_length=1, max_length=100)]
Message = Annotated[str, Field(max_length=12000)]
RuntimeRevision = Literal[2]
API_REVISION, = get_args(RuntimeRevision)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Error(Model):
    code: str
    message: str


class Runtime(Model):
    product: Literal["gc-personal-chat-1"]
    instance: str
    api_revision: RuntimeRevision
    issues: list[str]
    key_configured: bool
    key_source: Literal["local", "environment", "missing", "unavailable"]
    credential_storage_supported: bool
    endpoint: str
    data_root: str
    live_model: str
    tokens_per_request: int


class LiveSettings(Model):
    endpoint: Annotated[str, Field(min_length=1, max_length=512)]
    api_key: Annotated[SecretStr, Field(min_length=1, max_length=512)]


class NewChat(Model):
    mode: Mode
    title: Title = "新对话"


class Rename(Model):
    title: Title


class Chat(Model):
    id: str
    title: str
    mode: Mode


class Draft(Model):
    token: str
    revision: Annotated[int, Field(ge=0)]
    text: Message
    kind: Kind
    target: str | None


class Grant(Model):
    mode: Mode
    requests: Annotated[int, Field(ge=2, le=10)]
    minutes: Annotated[int, Field(ge=1, le=120)]
    confirm_live: bool


class Batch(Model):
    id: str
    mode: Mode
    state: Literal["open", "closed", "expired", "exhausted", "read_only"]
    model: str
    expires: float
    requests: int
    max_requests: int
    known_tokens: int
    max_tokens: int
    unknown_usage: int


class Citation(Model):
    turn_id: str
    packet_id: str
    citation_id: str


class Turn(Model):
    id: str
    chat_id: str
    sequence: int
    batch_id: str
    question: str
    user_message: str
    assistant_message: str | None
    result_hash: str | None
    state: Literal["queued", "running", "interrupted", "completed", "partial", "cancelled", "unknown", "failed", "blocked"]
    has_answer: bool
    answer: str | None
    detail: str
    stop_reason: str | None
    citations: list[Citation]
    can_trace: bool
    can_stop: bool
    can_resume: bool
    watching: bool
    stage: str
    draft: str
    cursor: int


class Page(Model):
    turns: list[Turn]
    before: int | None


class TurnEvent(Model):
    sequence: int
    kind: Literal["stage", "draft", "refresh", "settled", "error"]
    text: str
    state: Literal["running"] | None = None


class SourceLine(Model):
    text: str
    dialogue: bool
    speaker: str | None
    conditions: list[str]
    condition_status: str | None


class Source(Model):
    citation: Citation
    title: str
    support_note: str
    lines: list[SourceLine]
    texts: list[str]
    parent_texts: list[str]
    has_parent: bool
    context: dict
    locators: list[dict]


class Trace(Model):
    queries: list[str]
    reads: int
    dispositions: dict[str, int]
    stop_reason: str


class Report(Model):
    category: Category
    note: Annotated[str, Field(min_length=1, max_length=4000)]
    citation: Citation | None = None


class Feedback(Report):
    id: str
    chat_id: str
    turn_id: str
    question: str
    result_hash: str
    engine: str
    created: float


class Chats(Model):
    items: list[Chat]


class Batches(Model):
    items: list[Batch]


class Reports(Model):
    items: list[Feedback]


# operation id, method, resource, request model, response model
ROUTES = (
    ("runtime", "GET", "/runtime", None, Runtime),
    ("save_live_settings", "PUT", "/live-settings", LiveSettings, Runtime),
    ("clear_live_settings", "DELETE", "/live-settings", None, Runtime),
    ("chats", "GET", "/chats", None, Chats),
    ("new_chat", "POST", "/chats", NewChat, Chat),
    ("rename", "PATCH", "/chats/{sid}", Rename, Chat),
    ("delete_chat", "DELETE", "/chats/{sid}", None, Chats),
    ("draft", "GET", "/chats/{sid}/draft", None, Draft),
    ("save_draft", "PUT", "/chats/{sid}/draft", Draft, Draft),
    ("history", "GET", "/chats/{sid}/turns", None, Page),
    ("submit", "POST", "/chats/{sid}/turns", Draft, Turn),
    ("turn", "GET", "/turns/{tid}", None, Turn),
    ("stop", "POST", "/turns/{tid}/stop", None, Turn),
    ("resume", "POST", "/turns/{tid}/resume", None, Turn),
    ("events", "GET", "/turns/{tid}/events", None, TurnEvent),
    ("citation", "GET", "/turns/{tid}/packets/{packet}/citations/{cid}", None, Source),
    ("trace", "GET", "/turns/{tid}/trace", None, Trace),
    ("report", "POST", "/turns/{tid}/feedback", Report, Feedback),
    ("feedback", "GET", "/feedback", None, Reports),
    ("batches", "GET", "/batches", None, Batches),
    ("open_batch", "POST", "/batches", Grant, Batch),
    ("close_batch", "POST", "/batches/{bid}/close", None, Batch),
)


def specification():
    import re
    models = {Error, *(m for row in ROUTES for m in row[3:] if m is not None)}
    _, schemas = models_json_schema([(m, "validation") for m in sorted(models, key=lambda m: m.__name__)],
                                     ref_template="#/components/schemas/{model}")
    def content(m, media="application/json"):
        return {media: {"schema": {"$ref": "#/components/schemas/" + m.__name__}}}
    paths = {}
    for name, method, path, body, result in ROUTES:
        params = [{"name": p, "in": "path", "required": True, "schema": {"type": "string"}}
                  for p in re.findall(r"{(\w+)}", path)]
        if name == "history":
            params.extend([
                {"name": "before", "in": "query", "description": "Exclusive turn sequence",
                 "schema": {"type": "integer", "minimum": 1, "maximum": 2**63-1}},
                {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}},
            ])
        if name == "events":
            params.extend([{"name": field, "in": location, "description": "Last received event sequence",
                            "schema": {"type": "integer", "minimum": 0, "maximum": 2**63-1}}
                           for field, location in (("after", "query"), ("Last-Event-ID", "header"))])
        if method != "GET":
            params.append({"name": "X-GC-Client", "in": "header", "required": True,
                           "schema": {"type": "string", "const": "local-chat-v1"}})
        media = "text/event-stream" if name == "events" else "application/json"
        op = {"operationId": name, "parameters": params, "responses": {
            "200": {"description": "Success", "content": content(result, media)},
            "default": {"description": "Product error", "content": content(Error)}}}
        if body:
            op["requestBody"] = {"required": True, "content": content(body)}
        paths.setdefault("/api/v1" + path, {})[method.lower()] = op
    return {"openapi": "3.1.0", "info": {"title": "GenshinChronicle Local Chat", "version": "1.0.0"},
            "paths": paths, "components": {"schemas": schemas["$defs"]}}


if __name__ == "__main__":
    output = Path(__file__).resolve().parents[3] / "frontend/openapi.json"
    output.write_text(json.dumps(specification(), ensure_ascii=False, indent=2) + "\n", encoding="utf8")
