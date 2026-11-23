"""Durable product identities and atomic submissions on the original turn/event store."""
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

from ..evidence_io import digest, read, require, sha
from ..model_calls import locked
from ..rag.clean_conversation import ConversationStore, TERMINAL, VERSION as CHAT_VERSION
from ..rag.clean_store import VERSION as TURN_VERSION, put

PRODUCT = "gc-personal-chat-1"


def root_identity(folder):
    path = str(Path(folder).resolve())
    if os.name == "nt":
        if path.startswith("\\\\?\\UNC\\"):
            path = "\\\\" + path[8:]
        elif path.startswith("\\\\?\\"):
            path = path[4:]
    return digest(os.path.normcase(path))


class Problem(Exception):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.status = code, status


def open_product_root(folder):
    folder = Path(folder).resolve()
    marker = folder / "product.json"
    folder.mkdir(parents=True, exist_ok=True)
    if not marker.exists() and any(folder.iterdir()):
        raise ValueError("请选择空目录或现有 GC 产品数据目录。")
    put(marker, {"schema": PRODUCT})
    return folder


class ProductStore(ConversationStore):
    def __init__(self, folder):
        super().__init__(open_product_root(folder))
        self.commands = threading.RLock()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS product_batches(
                    id TEXT PRIMARY KEY, campaign TEXT UNIQUE NOT NULL, mode TEXT NOT NULL,
                    ref TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS product_drafts(
                    session TEXT PRIMARY KEY REFERENCES chats(id), token TEXT NOT NULL,
                    revision INTEGER NOT NULL, text TEXT NOT NULL, kind TEXT NOT NULL, target TEXT);
                CREATE TABLE IF NOT EXISTS product_inputs(
                    turn TEXT PRIMARY KEY REFERENCES turns(id), ref TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS product_feedback(
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, turn TEXT NOT NULL, ref TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS product_deleted_chats(
                    session TEXT PRIMARY KEY REFERENCES chats(id), deleted_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS product_history ON turns(session,sequence);
                CREATE INDEX IF NOT EXISTS product_queue_status ON chat_jobs(status);
                CREATE INDEX IF NOT EXISTS product_turn_events ON events(turn,sequence);
            """)

    def new_chat(self, mode, title):
        sid = uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO chats VALUES (?,?,?,?,?)",
                       (sid, title or "新对话", "per-turn", json.dumps({"mode": mode}), CHAT_VERSION))
            db.execute("INSERT INTO product_drafts VALUES (?,?,0,'','question',NULL)", (sid, uuid4().hex))
        return self.chat_info(sid)

    def chat_info(self, sid):
        chat = self.chat(sid)
        return {"id": sid, "title": chat["title"], "mode": chat["settings"]["mode"]}

    def list_chats(self):
        with self.connect() as db:
            rows = db.execute("""SELECT c.id,c.title,c.settings FROM chats c LEFT JOIN turns t ON t.session=c.id
                WHERE NOT EXISTS (SELECT 1 FROM product_deleted_chats d WHERE d.session=c.id)
                GROUP BY c.id ORDER BY COALESCE(MAX(t.sequence),0) DESC,c.rowid DESC""").fetchall()
        return [{"id": sid, "title": title, "mode": json.loads(settings)["mode"]} for sid, title, settings in rows]

    def rename(self, sid, title):
        self.chat(sid)
        with self.connect() as db:
            db.execute("UPDATE chats SET title=? WHERE id=?", (title, sid))
        return self.chat_info(sid)

    def get_draft(self, sid):
        with self.connect() as db:
            row = db.execute("""SELECT token,revision,text,kind,target FROM product_drafts p
                WHERE p.session=? AND NOT EXISTS (SELECT 1 FROM product_deleted_chats d WHERE d.session=p.session)
                """, (sid,)).fetchone()
        if not row:
            raise Problem("chat_missing", "会话不存在。", 404)
        return dict(zip(("token", "revision", "text", "kind", "target"), row))

    def delete_chat(self, sid):
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM product_drafts WHERE session=?", (sid,)).fetchone():
                raise Problem("chat_missing", "会话不存在。", 404)
            db.execute("INSERT INTO product_deleted_chats VALUES (?,?) ON CONFLICT(session) DO NOTHING",
                       (sid, time.time()))

    def check_target(self, sid, kind, target):
        if kind.startswith("correction_") != bool(target):
            raise Problem("invalid_target", "纠正消息需要指定同一会话的原消息。", 422)
        if target and not self.correction_source_turn(sid, target):
            raise Problem("invalid_target", "关联消息不在本会话中。", 422)

    def correction_source_turn(self, sid, target):
        with self.connect() as db:
            db.execute("BEGIN")
            source = db.execute("SELECT turn,ref FROM chat_messages WHERE session=? AND id=?",
                                (sid, target)).fetchone()
            if source is None:
                # The committed submission is visible before the worker
                # publishes the corresponding message file and reference.
                unpublished = db.execute("""SELECT t.id FROM turns t JOIN product_inputs i ON i.turn=t.id
                    WHERE t.session=? AND NOT EXISTS
                    (SELECT 1 FROM chat_messages m WHERE m.turn=t.id AND m.role='user')""", (sid,)).fetchall()
        if source:
            original = self.load(json.loads(source[1]))
            require((original["message_id"], original["session_id"], original["turn_id"]) == (target, sid, source[0]),
                    "Correction target changed")
            return source[0]
        for (tid,) in unpublished:
            if digest([CHAT_VERSION, tid, "user"]) == target:
                self.input_for(tid)
                return tid
        return None

    def job(self, tid):
        with self.connect() as db:
            row = db.execute("SELECT status,metadata FROM chat_jobs WHERE turn=?", (tid,)).fetchone()
        if not row:
            raise Problem("turn_missing", "轮次不存在。", 404)
        return {"turn_id": tid, "status": row[0], "metadata": json.loads(row[1])}

    def next_turn(self):
        # The child must retain its slot if an earlier parked turn is resumed
        # after the claim but before the child acquires the application lock.
        with self.connect() as db:
            row = db.execute("""SELECT t.id FROM turns t JOIN chat_jobs j ON j.turn=t.id
                WHERE t.cancelled=0 AND j.status IN ('queued','running')
                AND NOT EXISTS (SELECT 1 FROM product_deleted_chats d WHERE d.session=t.session)
                ORDER BY j.status='running' DESC,t.sequence LIMIT 1""").fetchone()
        return row[0] if row else None

    def park_old_jobs(self):
        with self.connect() as db:
            db.execute("UPDATE chat_jobs SET status='interrupted' WHERE status IN ('queued','running')")

    def claim(self):
        with self.connect() as db:
            # Idle polling must not take SQLite's write lock.
            if db.execute("SELECT 1 FROM chat_jobs WHERE status='queued' LIMIT 1").fetchone() is None:
                return None
            row = db.execute("""UPDATE chat_jobs SET status='running' WHERE turn=(
                SELECT t.id FROM turns t JOIN chat_jobs j ON j.turn=t.id
                WHERE j.status='queued'
                AND NOT EXISTS (SELECT 1 FROM product_deleted_chats d WHERE d.session=t.session)
                ORDER BY t.sequence LIMIT 1)
                RETURNING turn,CASE WHEN json_valid(metadata)
                THEN json_extract(metadata,'$.continue_requested') ELSE 0 END""").fetchone()
            return (row[0], bool(row[1])) if row else None

    def request_continue(self, tid):
        with self.connect() as db:
            changed = db.execute("""UPDATE chat_jobs
                SET status='queued',metadata=json_set(metadata,'$.continue_requested',json('true'))
                WHERE turn=? AND status='interrupted'
                AND EXISTS (SELECT 1 FROM turns WHERE id=? AND cancelled=0)""", (tid, tid))
            if not changed.rowcount:
                raise Problem("cannot_resume", "当前轮次无法按原许可继续。")

    def interrupt(self, tid):
        with self.connect() as db:
            db.execute("UPDATE chat_jobs SET status='interrupted' WHERE turn=? AND status='running'", (tid,))

    def has_work(self, *, sid=None):
        with self.connect() as db:
            return db.execute("""SELECT 1 FROM chat_jobs j JOIN turns t ON t.id=j.turn
                WHERE j.status IN ('queued','running') AND (? IS NULL OR t.session=?)
                AND NOT EXISTS (SELECT 1 FROM product_deleted_chats d WHERE d.session=t.session)
                LIMIT 1""", (sid, sid)).fetchone() is not None

    def event_cursor(self, tid):
        with self.connect() as db:
            return db.execute("SELECT COALESCE(MAX(sequence),0) FROM events WHERE turn=?", (tid,)).fetchone()[0]

    def save_draft(self, sid, data):
        self.check_target(sid, data.kind, data.target)
        with self.connect() as db:
            updated = db.execute("""UPDATE product_drafts SET text=?,kind=?,target=?,revision=revision+1
                WHERE session=? AND token=? AND revision=?""",
                (data.text, data.kind, data.target, sid, data.token, data.revision))
            if not updated.rowcount:
                raise Problem("draft_conflict", "草稿已在其他窗口更改，请重新载入。")
        return self.get_draft(sid)

    def submit_draft(self, sid, data, active_binding):
        """Token replay is checked before selecting a batch, including after expiry."""
        text = data.text
        if not text.strip():
            raise Problem("empty_question", "请输入问题。", 422)
        chat = self.chat_info(sid)
        tid = digest([TURN_VERSION, sid, data.token])
        intent = {"session": sid, "token": data.token, "text": text, "kind": data.kind, "target": data.target}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT ref FROM product_inputs WHERE turn=?", (tid,)).fetchone()
            if old:
                saved = self.load(json.loads(old[0]))
                if saved["intent"] != intent:
                    raise Problem("token_conflict", "同一提交令牌已用于其他内容。")
                return tid, False
            draft = db.execute("SELECT token,revision FROM product_drafts WHERE session=?", (sid,)).fetchone()
            if draft != (data.token, data.revision):
                raise Problem("draft_conflict", "草稿版本已变化，请重新载入。")
            self.check_target(sid, data.kind, data.target)
            batch = active_binding(chat["mode"])
            from .context import check_context_budget
            notes = self.notes(sid, prospective=(intent, tid))
            try:
                check_context_budget(text, notes, current_message_id=digest([CHAT_VERSION, tid, "user"]),
                                     history_bytes=batch["settings"]["history_bytes"])
            except ValueError as exc:
                if str(exc) != "PRODUCT_CONTEXT_BUDGET_EXCEEDED" and not isinstance(exc, UnicodeError):
                    raise
                raise Problem("context_budget_exceeded", "本条输入与有效的补充条件超出上下文预算。请缩短问题或修订条件。", 422) from None
            campaign = batch["campaign"]
            submission = {"schema": PRODUCT, "intent": intent, "batch_id": batch["id"],
                          "campaign": campaign, "settings_digest": digest(batch["settings"])}
            file = self.root / "submissions" / (digest(submission) + ".json")
            put(file, submission, atomic=True)
            ref = json.dumps({"path": file.relative_to(self.root).as_posix(), "sha256": sha(file)})
            seq = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM turns").fetchone()[0]
            db.execute("INSERT OR IGNORE INTO sessions VALUES (?,?)", (sid, TURN_VERSION))
            db.execute("""INSERT INTO turns(id,session,submit_token,question,campaign,sequence)
                       VALUES (?,?,?,?,?,?)""", (tid, sid, data.token, text, campaign, seq))
            kind = None if data.kind == "question" else data.kind
            metadata = {"note_kind": kind, "targets": [], "scenario": "normal", "target_message": data.target}
            db.execute("INSERT INTO chat_jobs VALUES (?,?,?)", (tid, "queued", json.dumps(metadata)))
            db.execute("INSERT INTO product_inputs VALUES (?,?)", (tid, ref))
            db.execute("UPDATE product_drafts SET token=?,revision=revision+1,text='',kind='question',target=NULL WHERE session=?",
                       (uuid4().hex, sid))
            if chat["title"] == "新对话":
                db.execute("UPDATE chats SET title=? WHERE id=?", (" ".join(text.split())[:36], sid))
        return tid, True

    def input_for(self, tid):
        with self.connect() as db:
            row = db.execute("SELECT ref FROM product_inputs WHERE turn=?", (tid,)).fetchone()
        if not row:
            raise Problem("turn_missing", "轮次不存在。", 404)
        value = self.load(json.loads(row[0]))
        t = self.turn(tid)
        require((value["intent"]["session"], value["intent"]["text"], value["intent"]["token"], value["campaign"])
                == (t["session"], t["question"], t["submit_token"], t["campaign"]), "Submission identity differs")
        return value

    def ensure_submission(self, tid):
        item = self.input_for(tid)["intent"]
        self.message(tid, "user", item["text"], input_kind=item["kind"])
        mode = self.chat_info(item["session"])["mode"]
        self.event(tid, "g4-submitted", {"kind": "submitted", "mode": mode})

    def artifact(self, tid, key, value, *, atomic=True):
        return super().artifact(tid, key, value, atomic=atomic)

    def message(self, tid, role, content, *, atomic=True, **extra):
        return super().message(tid, role, content, atomic=atomic, **extra)

    def event(self, tid, key, payload, *, atomic=True):
        return super().event(tid, key, payload, atomic=atomic)

    def notes(self, session, *, before=None, prospective=None):
        """A revision is visible from its own turn onward. Never annotate a past turn in place."""
        limit = self.turn(before)["sequence"] if before else 2**63 - 1
        with self.connect() as db:
            rows = db.execute("""SELECT i.ref,t.id FROM product_inputs i JOIN turns t ON t.id=i.turn
                JOIN chat_jobs j ON j.turn=t.id WHERE t.session=? AND t.sequence<=?
                AND json_extract(j.metadata,'$.note_kind') IS NOT NULL ORDER BY t.sequence""", (session, limit)).fetchall()
        items = [(self.load(json.loads(wire))["intent"], tid) for wire, tid in rows]
        if prospective and prospective[0]["kind"] != "question":
            items.append(prospective)
        notes = {}
        for item, tid in items:
            kind, target = item["kind"], item["target"]
            mid = digest([CHAT_VERSION, tid, "user"])
            if target:
                source = self.correction_source_turn(session, target)
                require(source is not None, "Missing correction target")
                if target not in notes:
                    notes[target] = {"note_id": target, "turn_id": source, "session_id": session,
                                     "message_ids": [target], "kind": "corrected_message", "text": "",
                                     "authority": "historical_message", "dependencies": []}
                notes[target]["status"] = "needs_review"
                # A later revision of the same premise supersedes the earlier revision.
                obsolete = {target}
                for previous in notes.values():
                    if obsolete.intersection(previous["dependencies"]):
                        previous["status"] = "needs_review"
                        obsolete.add(previous["note_id"])
            notes[mid] = {"note_id": mid, "turn_id": tid, "session_id": session, "message_ids": [mid],
                          "kind": kind, "text": item["text"], "dependencies": [target] if target else [],
                          "status": "active", "authority": "user_requirement" if kind in
                          {"requirement", "correction_intent"} else "unverified_user_or_hypothesis"}
        return list(notes.values())

    def binding(self, bid):
        with self.connect() as db:
            row = db.execute("SELECT ref FROM product_batches WHERE id=?", (bid,)).fetchone()
        if not row:
            raise Problem("batch_missing", "找不到问答批次。", 404)
        binding = self.load(json.loads(row[0]))
        require(binding["id"] == bid, "Batch identity differs")
        return binding

    def cancel(self, tid):
        batch = self.input_for(tid)["batch_id"]
        # SessionCalls reserves beneath the exact batch root. Serialize user cancel with that lock.
        for attempt in range(100):
            try:
                with locked(self.root / "batches" / batch / "locks/admission.lock"):
                    with self.connect() as db:
                        db.execute("BEGIN IMMEDIATE")
                        status = db.execute("SELECT status FROM chat_jobs WHERE turn=?", (tid,)).fetchone()[0]
                        if status in TERMINAL:
                            return
                        db.execute("UPDATE turns SET cancelled=1 WHERE id=?", (tid,))
                        if status == "interrupted":
                            db.execute("UPDATE chat_jobs SET status='queued' WHERE turn=?", (tid,))
                break
            except OSError:
                if attempt == 99:
                    raise
                time.sleep(.01)
        self.ensure_cancellation(tid)

    def ensure_cancellation(self, tid):
        self.event(tid, "cancel", {"kind": "cancel_requested"})
