"""Loopback-only ASGI product API and bundled Vue client."""
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import sqlite3

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles

from ..evidence_io import read, strict_json
from . import batches as grants, contract, presentation as display
from .credentials import OFFICIAL_ENDPOINT, clear_credential, read_credential, save_credential
from .diagnostics import inspect_assets
from .store import PRODUCT, Problem, ProductStore, root_identity
from .worker import ProductExecutor

REPO = Path(__file__).resolve().parents[3]
STATIC = Path(__file__).parent / "static"
DEFAULT_ROOT = REPO / "data/generation/personal-chat-v1"


class ChatStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if not path.startswith("assets/"):
            response.headers["Cache-Control"] = "no-store"
        return response


class Service:
    def __init__(self, root, repo, delay):
        self.repo = Path(repo)
        target = Path(root).resolve()
        if any(root_identity(p) == root_identity(self.repo / "data") for p in (target, *target.parents)):
            if root_identity(target.parent) != root_identity(self.repo / "data/generation"):
                raise ValueError("产品目录须与既有语料、索引和实验目录独立。")
        self.store = ProductStore(root)
        self.worker = ProductExecutor(self.store, repo, delay=delay)

    def runtime(self):
        issues = inspect_assets(self.repo)
        try:
            profile = read(self.repo / "config/clean-generation-v1.json")
            model, allowance = profile["model"], profile["context_tokens"]
            if not isinstance(model, str) or type(allowance) is not int or allowance <= 0:
                raise ValueError("Invalid generation profile")
        except (OSError, ValueError, TypeError, KeyError):
            model, allowance = "未配置", 0
            issues.append("生成配置")
        credential = read_credential(self.store.root)
        return {"product": PRODUCT, "instance": root_identity(self.store.root), "api_revision": contract.API_REVISION,
                "issues": issues,
                "live_model": model, "tokens_per_request": allowance, "endpoint": OFFICIAL_ENDPOINT,
                "data_root": str(self.store.root), "key_configured": bool(credential.key),
                "key_source": credential.source, "credential_storage_supported": os.name == "nt"}

    def require_live_settings_editable(self):
        batch = grants.latest(self.store, "LIVE")
        if batch and grants.status(self.store, batch)["state"] == "open":
            raise Problem("batch_open", "请先关闭当前真实问答批次，再修改 API 配置。")

    def save_live_settings(self, data):
        self.require_live_settings_editable()
        save_credential(self.store.root, data.endpoint, data.api_key.get_secret_value())
        return self.runtime()

    def clear_live_settings(self):
        self.require_live_settings_editable()
        clear_credential(self.store.root)
        return self.runtime()

    def chats(self):
        return {"items": self.store.list_chats()}

    def new_chat(self, data):
        return self.store.new_chat(data.mode, data.title.strip() or "新对话")

    def rename(self, sid, data):
        if not data.title.strip():
            raise Problem("empty_title", "请输入会话名称。", 422)
        return self.store.rename(sid, data.title.strip())

    def delete_chat(self, sid):
        with self.worker.condition:
            active = self.worker.active
            if self.store.has_work(sid=sid) or active and self.store.turn(active)["session"] == sid:
                raise Problem("chat_busy", "会话有正在处理的轮次，请先停止并等待本轮结束。")
            self.store.delete_chat(sid)
        return self.chats()

    def draft(self, sid):
        return self.store.get_draft(sid)

    def save_draft(self, sid, data):
        return self.store.save_draft(sid, data)

    def history(self, sid, before=2**63-1, limit=20):
        return display.history(self.store, sid, before, limit)

    def submit(self, sid, data):
        if self.worker.stop_event.is_set():
            raise Problem("service_stopping", "本地服务正在关闭。", 503)
        tid, _ = self.store.submit_draft(sid, data, lambda mode: grants.available(self.store, mode))
        self.worker.wake()
        return self.turn(tid)

    def turn(self, tid):
        self.store.input_for(tid)
        return display.turn_view(self.store, tid)

    def stop(self, tid):
        view = self.turn(tid)
        if view["can_stop"]:
            self.worker.cancel(tid)
        return self.turn(tid)

    def resume(self, tid):
        if not self.turn(tid)["can_resume"]:
            raise Problem("cannot_resume", "当前轮次无法按原许可继续。")
        if self.worker.stop_event.is_set():
            raise Problem("service_stopping", "本地服务正在关闭。", 503)
        self.store.request_continue(tid)
        self.worker.wake()
        return self.turn(tid)

    def citation(self, tid, packet, cid):
        return display.source(self.store, tid, packet, cid)

    def trace(self, tid):
        return display.trace(self.store, tid)

    def feedback(self):
        return display.reports(self.store)

    def report(self, tid, data):
        return display.add_feedback(self.store, tid, data)

    def batches(self):
        return {"items": [grants.status(self.store, b) for mode in ("LIVE", "MOCK") if (b := grants.latest(self.store, mode))]}

    def open_batch(self, data):
        return grants.open_batch(self.store, self.repo, data)

    def close_batch(self, bid):
        return grants.close_batch(self.store, bid, self.worker)


def number(value, low, high):
    try:
        n = int(value)
        if low <= n <= high:
            return n
    except (ValueError, TypeError):
        pass
    raise Problem("invalid_cursor", "分页或事件游标无效。", 422)


def event_frame(e):
    value = contract.TurnEvent.model_validate(e)
    return f"id: {value.sequence}\ndata: {value.model_dump_json()}\n\n"


def problem_for(exc, operation):
    if isinstance(exc, Problem):
        return exc
    sqlite_code = getattr(exc, "sqlite_errorcode", 0) & 0xff
    logging.getLogger(__name__).warning("Chat API %s failed (%s, SQLite=%d)",
                                       operation, type(exc).__name__, sqlite_code)
    if isinstance(exc, sqlite3.Error):
        if sqlite_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return Problem("storage_busy", "本地记录正在被占用，请稍后重试。", 503)
        if sqlite_code in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}:
            return Problem("integrity", "本地数据库校验失败。请保留数据以便复查。")
    if isinstance(exc, (OSError, ValueError, KeyError)) and not isinstance(exc, ValidationError):
        return Problem("integrity", "本地记录缺失或校验失败。请保留数据以便复查。")
    return Problem("internal_error", "本地服务遇到异常，请查看本地服务日志。", 500)


async def event_stream(service, tid, after):
    seq, idle = after, 0
    try:
        while True:
            events = await asyncio.to_thread(display.public_events, service.store, tid, seq)
            for event in events:
                seq = event["sequence"]
                yield event_frame(event)
                if event["kind"] == "settled":
                    return
            job = await asyncio.to_thread(service.store.job, tid)
            active = job["status"] in {"queued", "running"}
            if not active:
                # A job may have published its last events between the read and idle check.
                tail = await asyncio.to_thread(display.public_events, service.store, tid, seq)
                for event in tail:
                    seq = event["sequence"]
                    yield event_frame(event)
                yield event_frame({"sequence": seq, "kind": "settled", "text": ""})
                return
            idle += 1
            if idle % 50 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(.25)
    except Exception as exc:
        problem = problem_for(exc, "events")
        yield event_frame({"sequence": seq, "kind": "error", "text": str(problem)})


def endpoint(name, body, response):
    async def handle(request: Request):
        service = request.app.state.service
        kwargs = dict(request.path_params)

        def check_access():
            if "sid" in kwargs and name not in {"draft", "delete_chat"}:
                service.store.get_draft(kwargs["sid"])
            if "tid" in kwargs:
                item = service.store.input_for(kwargs["tid"])
                service.store.get_draft(item["intent"]["session"])

        if name == "events":
            await asyncio.to_thread(check_access)
            cursor = request.headers.get("last-event-id") or request.query_params.get("after", "0")
            return StreamingResponse(event_stream(service, kwargs["tid"], number(cursor, 0, 2**63-1)),
                                     media_type="text/event-stream", headers={"Cache-Control": "no-store"})
        if name == "history":
            kwargs["before"] = number(request.query_params.get("before", 2**63-1), 1, 2**63-1)
            kwargs["limit"] = number(request.query_params.get("limit", 20), 1, 50)
        if body:
            raw = await request.body()
            if len(raw) > 100000:
                raise Problem("body_too_large", "消息过长。", 413)
            try:
                kwargs["data"] = body.model_validate(strict_json(raw.decode("utf8")))
            except (ValidationError, UnicodeError, ValueError):
                raise Problem("invalid_input", "请求内容或字段无效。", 422) from None
        def execute():
            method = getattr(service, name)
            if request.method == "GET":
                check_access()
                result = method(**kwargs)
            else:
                with service.store.commands:
                    check_access()
                    result = method(**kwargs)
            return response.model_validate(result).model_dump()
        return JSONResponse(await asyncio.to_thread(execute), headers={"Cache-Control": "no-store"})
    return handle


def guarded_endpoint(name, body, response):
    handle = endpoint(name, body, response)

    async def guarded(request):
        try:
            return await handle(request)
        except Exception as exc:
            problem = problem_for(exc, name)
            return JSONResponse({"code": problem.code, "message": str(problem)}, problem.status,
                                headers={"Cache-Control": "no-store"})
    return guarded


class LoopbackOnly:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            host = headers.get(b"host", b"").decode("ascii", "replace")
            origin = headers.get(b"origin")
            write = scope["method"] not in {"GET", "HEAD"}
            if (host.split(":")[0] != "127.0.0.1" or origin and origin != ("http://" + host).encode()
                    or headers.get(b"sec-fetch-site") == b"cross-site"
                    or write and headers.get(b"x-gc-client") != b"local-chat-v1"):
                await JSONResponse({"code": "local_only", "message": "仅接受本机同源应用请求。"}, 403)(scope, receive, send)
                return
            async def secured(message):
                if message["type"] == "http.response.start":
                    message["headers"] += [(b"x-content-type-options", b"nosniff"),
                        (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")]
                await send(message)
            await self.app(scope, receive, secured)
        else:
            await self.app(scope, receive, send)


def create_app(root=DEFAULT_ROOT, repo=REPO, *, delay=.025):
    @asynccontextmanager
    async def lifespan(app):
        service = await asyncio.to_thread(Service, root, repo, delay)
        app.state.service = service
        try:
            yield
        finally:
            await asyncio.to_thread(service.worker.close)

    async def spec(request):
        return JSONResponse(contract.specification())

    routes = [Route("/api/v1" + path, guarded_endpoint(name, body, response), methods=[method]) for
              name, method, path, body, response in contract.ROUTES]
    routes.append(Route("/api/v1/openapi.json", spec))
    if STATIC.is_dir():
        routes.append(Mount("/", app=ChatStaticFiles(directory=STATIC, html=True)))
    app = Starlette(routes=routes, lifespan=lifespan)
    app.add_middleware(LoopbackOnly)
    return app
