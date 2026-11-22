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
                CREATE INDEX IF NOT EXISTS product_history ON turns(session,sequence);
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
                GROUP BY c.id ORDER BY COALESCE(MAX(t.sequence),0) DESC,c.rowid DESC""").fetchall()
        return [{"id": sid, "title": title, "mode": json.loads(settings)["mode"]} for sid, title, settings in rows]

    def rename(self, sid, title):
        self.chat(sid)
        with self.connect() as db:
            db.execute("UPDATE chats SET title=? WHERE id=?", (title, sid))
        return self.chat_info(sid)

    def get_draft(self, sid):
        with self.connect() as db:
            row = db.execute("SELECT token,revision,text,kind,target FROM product_drafts WHERE session=?", (sid,)).fetchone()
        if not row:
            raise Problem("chat_missing", "会话不存在。", 404)
        return dict(zip(("token", "revision", "text", "kind", "target"), row))

    def check_target(self, sid, kind, target):
        if kind.startswith("correction_") != bool(target):
            raise Problem("invalid_target", "纠正消息需要指定同一会话的原消息。", 422)
        if target:
            with self.connect() as db:
                row = db.execute("SELECT ref FROM chat_messages WHERE id=? AND session=?", (target, sid)).fetchone()
            if not row:
                raise Problem("invalid_target", "关联消息不在本会话中。", 422)
            original = self.load(json.loads(row[0]))
            require(original["message_id"] == target and original["session_id"] == sid, "Message identity changed")

    def job(self, tid):
        with self.connect() as db:
            row = db.execute("SELECT status,metadata FROM chat_jobs WHERE turn=?", (tid,)).fetchone()
        if not row:
            raise Problem("turn_missing", "轮次不存在。", 404)
        return {"turn_id": tid, "status": row[0], "metadata": json.loads(row[1])}

    def next_turn(self):
        # Parked turns retain their original checkpoint and budget. Only submitted or
        # explicitly resumed work competes for the single local execution slot.
        with self.connect() as db:
            row = db.execute("""SELECT t.id FROM turns t JOIN chat_jobs j ON j.turn=t.id
                WHERE t.cancelled=0 AND j.status IN ('queued','running')
                ORDER BY t.sequence LIMIT 1""").fetchone()
        return row[0] if row else None

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
            campaign = batch["campaign"]
            submission = {"schema": PRODUCT, "intent": intent, "batch_id": batch["id"],
                          "campaign": campaign, "settings_digest": digest(batch["settings"])}
            file = self.root / "submissions" / (digest(submission) + ".json")
            put(file, submission)
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
        self.ensure_submission(tid)
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

    def notes(self, session, *, before=None):
        """A revision is visible from its own turn onward. Never annotate a past turn in place."""
        limit = self.turn(before)["sequence"] if before else 2**63 - 1
        with self.connect() as db:
            rows = db.execute("""SELECT i.ref,t.id FROM product_inputs i JOIN turns t ON t.id=i.turn
                JOIN chat_jobs j ON j.turn=t.id WHERE t.session=? AND t.sequence<=?
                AND json_extract(j.metadata,'$.note_kind') IS NOT NULL ORDER BY t.sequence""", (session, limit)).fetchall()
        notes = {}
        for wire, tid in rows:
            item = self.load(json.loads(wire))["intent"]
            kind, target = item["kind"], item["target"]
            mid = digest([CHAT_VERSION, tid, "user"])
            if target:
                with self.connect() as db:
                    source = db.execute("SELECT turn,ref FROM chat_messages WHERE session=? AND id=?",
                                        (session, target)).fetchone()
                require(source is not None, "Missing correction target")
                original = self.load(json.loads(source[1]))
                require(original["message_id"] == target and original["turn_id"] == source[0], "Correction target changed")
                if target not in notes:
                    notes[target] = {"note_id": target, "turn_id": source[0], "session_id": session,
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
                    if self.job(tid)["status"] in TERMINAL:
                        return
                    return super().cancel(tid)
            except OSError:
                if attempt == 99:
                    raise
                time.sleep(.01)
