"""One immutable execution directory per finite grant, one durable conversation catalog."""
import json
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4

from .. import model_calls
from ..evidence_io import digest, read, require, sha
from ..generation.clean_chat_transport import ChatSessions
from ..rag import clean_research_live
from ..rag.clean_conversation_graph import fixture_settings
from ..rag.clean_store import put
from .credentials import read_credential
from .store import Problem


def adapter_identity():
    folder = Path(__file__).parent
    return {name: sha(folder / name) for name in ("store.py", "batches.py", "worker.py", "credentials.py", "context.py")}


class ExecutionView:
    """Batch-local permits, checkpoints and attempts; shared verified messages, events and evidence."""
    def __init__(self, catalog, binding, tid):
        self.catalog, self.binding, self.tid = catalog, binding, tid
        turn = catalog.turn(tid)
        self.session = turn["session"]
        self.root = catalog.root / "batches" / binding["id"]
        submission = catalog.input_for(tid)
        require(submission["batch_id"] == binding["id"] and turn["campaign"] == binding["campaign"]
                and submission["settings_digest"] == digest(binding["settings"])
                and binding["adapter"] == adapter_identity(), "Incompatible product turn or adapter")
        if binding["mode"] == "LIVE":
            require(sha(self.root / "live/permit.json") == binding["permit_sha256"], "Permit file changed")

    def chat(self, sid):
        require(sid == self.session, "Foreign conversation")
        return {**self.catalog.chat(sid), "settings": self.binding["settings"], "campaign": self.binding["campaign"]}

    def cancelled(self, tid):
        if self.catalog.stopping():
            raise ValueError("APPLICATION_SHUTDOWN_CONTINUE_REQUIRED")
        if self.catalog.cancelled(tid) or (self.root / "operator-closed.json").is_file():
            return True
        if self.binding["mode"] == "MOCK" and time.time() >= self.binding["expires"]:
            raise model_calls.Deferred("BATCH_EXPIRED")
        return False

    def product_context(self, tid, settings, *, system, include_research=False):
        from .context import snapshot
        require(tid == self.tid and settings == self.binding["settings"], "Foreign context binding")
        return snapshot(self.catalog, tid, settings, system=system, include_research=include_research)

    def __getattr__(self, name):
        return getattr(self.catalog, name)


class LedgerReader(model_calls.Batch):
    """Read shared accounting with its original seal verification and no budget initialization."""
    def __init__(self, root, limits):
        self.root, self.window_start, self._outcome_cache = root, 0, {}
        self.config = read(root / "config.json")
        require(self.config == {"version": model_calls.VERSION, "max_requests": limits["campaign_requests"],
                                "max_tokens": limits["campaign_tokens"]}, "Budget scope changed")


def latest(store, mode):
    with store.connect() as db:
        row = db.execute("SELECT id FROM product_batches WHERE mode=? ORDER BY rowid DESC LIMIT 1", (mode,)).fetchone()
    return store.binding(row[0]) if row else None


def same_execution_profile(b, root, product_root):
    if b["adapter"] != adapter_identity():
        return False
    if b["mode"] == "MOCK":
        return True
    credential = read_credential(product_root)
    if not credential.key or credential.revision != b.get("credential_revision"):
        return False
    from ..retrieval.clean_based.runtime_v2 import code_identity as upstream
    permit = clean_research_live.permit_for(SimpleNamespace(root=root), b["settings"])
    try:
        return (permit["code"] == clean_research_live.code_identity() and permit["upstream_code"] == upstream()
                and all(sha(Path(permit["repo"]) / name) == expected for name, expected in permit["assets"].items()))
    except OSError:
        return False


def status(store, b):
    root = store.root / "batches" / b["id"]
    limits = b["settings"]["limits"]
    ledger = root / "campaigns" / digest(b["campaign"]) / "batch"
    totals = LedgerReader(ledger, limits).accounting()
    if b["mode"] == "LIVE":
        require(sha(root / "live/permit.json") == b["permit_sha256"], "Permit binding changed")
    state = "open"
    if (root / "live/closed.json").exists() or (root / "operator-closed.json").exists():
        state = "closed"
    elif time.time() >= b["expires"]:
        state = "expired"
    elif totals["request_reservations"] >= limits["campaign_requests"] or totals["known_tokens"] >= limits["campaign_tokens"]:
        state = "exhausted"
    elif not same_execution_profile(b, root, store.root):
        state = "read_only"
    return {"id": b["id"], "mode": b["mode"], "state": state, "expires": b["expires"],
            "requests": totals["request_reservations"], "max_requests": limits["campaign_requests"],
            "known_tokens": totals["known_tokens"], "max_tokens": limits["campaign_tokens"],
            "unknown_usage": totals["unknown_usage_requests"], "model": b["model"]}


def available(store, mode):
    b = latest(store, mode)
    if not b or status(store, b)["state"] != "open":
        raise Problem("batch_required", "请先开启一个有效的问答批次。")
    return b


def open_batch(store, repo, data):
    previous = latest(store, data.mode)
    if previous and status(store, previous)["state"] == "open":
        raise Problem("batch_open", "已有可用批次。")
    credential_revision = None
    if data.mode == "LIVE":
        if data.confirm_live is not True:
            raise Problem("consent_required", "请明确确认本批次对官方模型的有限调用。", 422)
        from .diagnostics import inspect_assets
        missing = inspect_assets(repo)
        if missing:
            raise Problem("assets_missing", "本地资产未就绪：" + "、".join(missing))
        credential = read_credential(store.root)
        if not credential.key:
            raise Problem("key_missing", "请在批次设置的真实问答配置中保存可用的 API Key。")
        credential_revision = credential.revision
    bid = uuid4().hex
    root = store.root / "batches" / bid
    root.mkdir(parents=True, exist_ok=False)
    scope = SimpleNamespace(root=root)
    expires = time.time() + data.minutes * 60
    permit_hash = None
    if data.mode == "LIVE":
        settings = clean_research_live.prepare(scope, repo,
            f"Local user confirmed official G5, {data.requests} requests, {data.minutes} minutes, batch {bid}",
            requests=data.requests, seconds=data.minutes * 60)
        permit = clean_research_live.permit_for(scope, settings)
        expires, campaign, model = permit["expires_at"], permit["campaign"], permit["model"]
        permit_hash = sha(root / "live/permit.json")
    else:
        settings = fixture_settings()
        limits = settings["limits"]
        limits.update(campaign_requests=data.requests, campaign_tokens=data.requests * settings["context_tokens"])
        campaign, model = "mock-" + bid, "离线模拟"
        ChatSessions(scope, campaign, limits)
    binding = {"id": bid, "mode": data.mode, "settings": settings, "expires": expires, "campaign": campaign,
               "adapter": adapter_identity(), "model": model, "permit_sha256": permit_hash}
    if data.mode == "LIVE":
        binding["credential_revision"] = credential_revision
    path = root / "binding.json"
    put(path, binding)
    with store.connect() as db:
        ref = {"path": path.relative_to(store.root).as_posix(), "sha256": sha(path)}
        db.execute("INSERT INTO product_batches VALUES (?,?,?,?,?)",
                   (bid, campaign, data.mode, json.dumps(ref), time.time()))
    return status(store, binding)


def close_batch(store, bid, worker):
    binding = store.binding(bid)
    root = store.root / "batches" / bid
    for attempt in range(100):
        try:
            with model_calls.locked(root / "locks/admission.lock"):
                put(root / "operator-closed.json", {"reason": "user_closed", "batch": bid}, atomic=True)
            break
        except OSError:
            if attempt == 99:
                raise
            time.sleep(.01)
    with store.connect() as db:
        rows = db.execute("""SELECT t.id FROM turns t JOIN chat_jobs j ON j.turn=t.id
            WHERE t.campaign=? AND j.status IN ('queued','running','interrupted')
            AND NOT EXISTS (SELECT 1 FROM product_deleted_chats d WHERE d.session=t.session)
            """, (binding["campaign"],)).fetchall()
    for (tid,) in rows:
        worker.cancel(tid)
    return status(store, binding)
