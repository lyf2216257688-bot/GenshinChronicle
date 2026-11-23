"""G4 local chat records layered over the unchanged G1 submission/event store."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from threading import Lock, RLock
import time
from uuid import uuid4

from ..evidence_io import digest, inside, read, require, sha
from ..model_calls import locked
from .clean_store import Store, put

VERSION = "gc-clean-conversation-1"
TERMINAL = {"completed", "partial", "cancelled", "failed", "blocked"}
NOTE_KINDS = {"requirement", "user_assertion", "hypothesis", "unresolved", "correction_intent", "correction_fact"}
_GUARD = Lock()
_EVENT_LOCKS = {}


class ConversationStore(Store):
    def __init__(self, root):
        self.shutdown_event = None
        self.shutdown_file = None
        # Independent worktrees have long paths. Keep the Windows extended
        # absolute spelling consistent in DB/operation identities from day one.
        root = str(Path(root).resolve())
        if os.name == "nt" and not root.startswith("\\\\?\\"):
            root = "\\\\?\\UNC\\" + root[2:] if root.startswith("\\\\") else "\\\\?\\" + root
        super().__init__(root)
        with _GUARD:
            self.event_lock = _EVENT_LOCKS.setdefault(str(self.root), RLock())
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS chats(
              id TEXT PRIMARY KEY, title TEXT NOT NULL, campaign TEXT NOT NULL,
              settings TEXT NOT NULL, version TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chat_jobs(
              turn TEXT PRIMARY KEY REFERENCES turns(id), status TEXT NOT NULL,
              metadata TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chat_messages(
              id TEXT PRIMARY KEY, session TEXT NOT NULL, turn TEXT NOT NULL,
              role TEXT NOT NULL, ref TEXT NOT NULL, UNIQUE(turn,role));
            CREATE TABLE IF NOT EXISTS chat_notes(
              id TEXT PRIMARY KEY, session TEXT NOT NULL, turn TEXT NOT NULL, ref TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chat_drafts(
              session TEXT PRIMARY KEY, token TEXT NOT NULL, text TEXT NOT NULL);
            """)
        self.recover_submissions()

    def create_chat(self, title, settings, *, session=None):
        session = session or uuid4().hex
        require(isinstance(title, str) and title.strip(), "Chat title required")
        require(settings.get("mode") in {"MOCK", "LIVE"}, "G4_MODE_INVALID")
        if settings["mode"] == "LIVE":
            if settings.get("engine") == "gc-clean-research-1":
                from .clean_research_live import permit_for
            else:
                from .clean_live import permit_for
            campaign = permit_for(self, settings)["campaign"]
        else:
            campaign = "g4-mock-" + session
        wire = json.dumps(settings, sort_keys=True)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO chats VALUES (?,?,?,?,?)", (session, title, campaign, wire, VERSION))
            old = db.execute("SELECT title,campaign,settings,version FROM chats WHERE id=?", (session,)).fetchone()
            require(old == (title, campaign, wire, VERSION), "Chat configuration/identity changed")
        return session

    def chats(self):
        with self.connect() as db:
            return [{"id": r[0], "title": r[1]} for r in db.execute("SELECT id,title FROM chats ORDER BY rowid DESC")]

    def chat(self, session):
        with self.connect() as db:
            row = db.execute("SELECT title,campaign,settings,version FROM chats WHERE id=?", (session,)).fetchone()
        require(row is not None and row[3] == VERSION, "Unknown/incompatible chat")
        return {"id": session, "title": row[0], "campaign": row[1], "settings": json.loads(row[2]), "version": row[3]}

    def artifact(self, tid, key, value, *, atomic=False):
        self.turn(tid)
        path = self.root / "chat_artifacts" / tid / (digest(key) + ".json")
        put(path, value, atomic=atomic)
        return {"path": path.relative_to(self.root).as_posix(), "sha256": sha(path)}

    def load(self, ref):
        path = inside(self.root, ref["path"])
        require(sha(path) == ref["sha256"], "Chat artifact missing or changed")
        return read(path)

    def published_artifact(self, tid, key):
        """Find a create-only stage file in the file/checkpoint publication gap.
        The stage must validate its content and all bound input references.
        """
        self.turn(tid)
        path = self.root / "chat_artifacts" / tid / (digest(key) + ".json")
        return {"path": path.relative_to(self.root).as_posix(), "sha256": sha(path)} if path.exists() else None

    def message(self, tid, role, content, *, atomic=False, **extra):
        turn = self.turn(tid)
        mid = digest([VERSION, tid, role])
        value = {"version": VERSION, "message_id": mid, "session_id": turn["session"],
                 "turn_id": tid, "role": role, "content": content, **extra}
        with self.connect() as db:
            old = db.execute("SELECT ref FROM chat_messages WHERE id=?", (mid,)).fetchone()
        if old:
            require(self.load(json.loads(old[0])) == value, "Message changed")
            return mid
        ref = self.artifact(tid, "message-" + role, value, atomic=atomic)
        with self.connect() as db:
            wire = json.dumps(ref, sort_keys=True)
            db.execute("INSERT OR IGNORE INTO chat_messages VALUES (?,?,?,?,?)", (mid, turn["session"], tid, role, wire))
            require(db.execute("SELECT ref FROM chat_messages WHERE id=?", (mid,)).fetchone()[0] == wire,
                    "Message reference changed")
        return mid

    def history(self, session, *, before=None):
        self.chat(session)
        limit = self.turn(before)["sequence"] if before else 2**63 - 1
        with self.connect() as db:
            rows = db.execute("""SELECT m.ref FROM chat_messages m JOIN turns t ON t.id=m.turn
                WHERE m.session=? AND t.sequence<? ORDER BY t.sequence, CASE m.role WHEN 'user' THEN 0 ELSE 1 END""",
                (session, limit)).fetchall()
        messages = [self.load(json.loads(r[0])) for r in rows]
        for message in messages:
            if message.get("result_ref"):
                result = self.load(message["result_ref"])
                if result.get("sources_ref"):
                    from .clean_conversation_graph import verify_sources
                    verify_sources(self, result["sources_ref"])
        return messages

    def enqueue(self, session, token, text, *, note_kind=None, targets=(), scenario="normal"):
        chat = self.chat(session)
        require(note_kind is None or note_kind in NOTE_KINDS, "Unknown research note kind")
        require(scenario in {"normal", "limited", "retract", "source_conflict", "disconnect"}, "Unknown Mock scenario")
        catalog = {n["note_id"] for n in self.notes(session)}
        require(set(targets) <= catalog, "Correction/dependency belongs to another chat")
        require(not targets or note_kind is not None, "Targets require an explicit research note")
        metadata = {"note_kind": note_kind, "targets": list(targets), "scenario": scenario}
        # Persist the complete intent BEFORE G1 commits the stable turn. This
        # closes the two-table publication gap without altering G1's code/hash.
        intent = {"version": VERSION, "session": session, "token": token, "text": text,
                  "campaign": chat["campaign"], "metadata": metadata}
        put(self.root / "chat_submissions" / (digest([VERSION, session, token]) + ".json"), intent)
        tid = self.submit(session, token, text, chat["campaign"])
        with self.connect() as db:
            wire = json.dumps(metadata, sort_keys=True)
            db.execute("INSERT OR IGNORE INTO chat_jobs VALUES (?,?,?)", (tid, "queued", wire))
            require(db.execute("SELECT metadata FROM chat_jobs WHERE turn=?", (tid,)).fetchone()[0] == wire,
                    "Submit token reused with different metadata")
        self.ensure_submission(tid)
        return tid

    def recover_submissions(self):
        with self.connect() as db:
            rows = db.execute("""SELECT t.id,t.session,t.submit_token,t.question,t.campaign FROM turns t
                JOIN chats c ON c.id=t.session WHERE NOT EXISTS
                (SELECT 1 FROM chat_jobs j WHERE j.turn=t.id)""").fetchall()
        for tid, session, token, text, campaign in rows:
            intent = read(self.root / "chat_submissions" / (digest([VERSION, session, token]) + ".json"))
            require((intent["version"], intent["session"], intent["token"], intent["text"], intent["campaign"]) ==
                    (VERSION, session, token, text, campaign), "Submission intent differs from committed turn")
            with self.connect() as db:
                db.execute("INSERT OR IGNORE INTO chat_jobs VALUES (?,?,?)",
                           (tid, "queued", json.dumps(intent["metadata"], sort_keys=True)))
            self.ensure_submission(tid)

    def ensure_submission(self, tid):
        """Repair the crash gap before message publication from the immutable
        submitted text/metadata. Existing referenced files must still verify.
        """
        turn, metadata = self.turn(tid), self.job(tid)["metadata"]
        mid = self.message(tid, "user", turn["question"], input_kind=metadata["note_kind"] or "question")
        if metadata["note_kind"]:
            self.add_note(tid, metadata["note_kind"], turn["question"], message_ids=[mid], dependencies=metadata["targets"])
        self.event(tid, "g4-submitted", {"kind": "submitted", "mode": self.chat(turn["session"])["settings"]["mode"]})

    def jobs(self, session=None):
        with self.connect() as db:
            rows = db.execute("""SELECT j.turn,j.status,j.metadata FROM chat_jobs j JOIN turns t ON t.id=j.turn
                WHERE (? IS NULL OR t.session=?) ORDER BY t.sequence""", (session, session)).fetchall()
        return [{"turn_id": t, "status": s, "metadata": json.loads(m)} for t, s, m in rows]

    def job(self, tid):
        return next(j for j in self.jobs(self.turn(tid)["session"]) if j["turn_id"] == tid)

    def status(self, tid, status):
        require(status in TERMINAL | {"queued", "running", "interrupted"}, "Unknown turn status")
        with self.connect() as db:
            db.execute("UPDATE chat_jobs SET status=? WHERE turn=?", (status, tid))

    def add_note(self, tid, kind, text, *, message_ids=(), dependencies=()):
        require(kind in NOTE_KINDS, "Unknown note kind")
        turn = self.turn(tid)
        history = self.history(turn["session"])
        require(set(message_ids) <= {m["message_id"] for m in history}, "Foreign message reference")
        known = {n["note_id"] for n in self.notes(turn["session"])}
        require(set(dependencies) <= known, "Foreign/cyclic note dependency")
        value = {"version": VERSION, "session_id": turn["session"], "turn_id": tid, "kind": kind,
                 "text": text, "message_ids": list(message_ids), "dependencies": list(dependencies),
                 "authority": "user_requirement" if kind in {"requirement", "correction_intent"} else "unverified_user_or_hypothesis"}
        nid = digest(value)
        value["note_id"] = nid
        with self.connect() as db:
            old = db.execute("SELECT ref FROM chat_notes WHERE id=?", (nid,)).fetchone()
        if old:
            require(self.load(json.loads(old[0])) == value, "Research note changed")
            return nid
        ref = self.artifact(tid, "note-" + nid, value)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO chat_notes VALUES (?,?,?,?)", (nid, turn["session"], tid, json.dumps(ref)))
        return nid

    def notes(self, session, *, before=None):
        limit = self.turn(before)["sequence"] if before else 2**63 - 1
        with self.connect() as db:
            rows = db.execute("""SELECT n.ref FROM chat_notes n JOIN turns t ON t.id=n.turn
                WHERE n.session=? AND t.sequence<=? ORDER BY t.sequence,n.rowid""", (session, limit)).fetchall()
        notes = [self.load(json.loads(r[0])) for r in rows]
        stale = set()
        for i, n in enumerate(notes):
            if n["kind"].startswith("correction_"):
                # An unscoped correction conservatively invalidates all prior
                # premises of that class; the user need not know internal IDs.
                scope = {"requirement", "correction_intent"} if n["kind"] == "correction_intent" else {
                    "user_assertion", "hypothesis", "unresolved", "correction_fact"}
                stale.update(n["dependencies"] or [p["note_id"] for p in notes[:i] if p["kind"] in scope])
        while True:
            extra = {n["note_id"] for n in notes if not n["kind"].startswith("correction_") and set(n["dependencies"]) & stale}
            if extra <= stale: break
            stale |= extra
        return [{**n, "status": "needs_review" if n["note_id"] in stale else "active"} for n in notes]

    def draft(self, session):
        self.chat(session)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO chat_drafts VALUES (?,?,?)", (session, uuid4().hex, ""))
            token, text = db.execute("SELECT token,text FROM chat_drafts WHERE session=?", (session,)).fetchone()
        return {"token": token, "text": text}

    def edit_draft(self, session, text, *, new=False):
        self.draft(session)
        # A submitted draft remains stable through refresh/double click. Editing
        # to a new message, or explicitly repeating it, allocates the next token.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            token_before, text_before = db.execute("SELECT token,text FROM chat_drafts WHERE session=?", (session,)).fetchone()
            old = {"token": token_before, "text": text_before}
            used = db.execute("SELECT 1 FROM turns WHERE session=? AND submit_token=?", (session, old["token"])).fetchone()
            token = uuid4().hex if new or used and text != old["text"] else old["token"]
            db.execute("UPDATE chat_drafts SET token=?,text=? WHERE session=?", (token, text, session))
        return {"token": token, "text": text}

    def request_continue(self, tid):
        require(not self.cancelled(tid), "Cancelled turn cannot resume dispatch; submit a new turn")
        require(self.job(tid)["status"] not in TERMINAL, "Terminal turn is read-only")
        self.status(tid, "queued")

    def stopping(self):
        return bool(self.shutdown_event and self.shutdown_event.is_set() or
                    self.shutdown_file and Path(self.shutdown_file).exists())

    def cancelled(self, tid):
        # The shared bridge consults this at admission and G3 at read/send
        # boundaries. Shutdown stops admission without forging a user cancel.
        return self.stopping() or super().cancelled(tid)

    def event(self, *args, **kwargs):
        with self.event_lock:
            tid = args[0] if args else kwargs["tid"]
            key = args[1] if len(args) > 1 else kwargs["key"]
            with self.connect() as db:
                old = db.execute("SELECT payload FROM events WHERE turn=? AND event_key=?", (tid, key)).fetchone()
            if old: self.load(json.loads(old[0]))
            for attempt in range(100):
                try: return super().event(*args, **kwargs)
                except OSError:
                    if attempt == 99: raise
                    time.sleep(.01)

    def cancel(self, tid):
        # G1's lock is nonblocking. UI cancellation may briefly race an
        # admission critical section in the same local process.
        for attempt in range(100):
            try: return super().cancel(tid)
            except OSError:
                if attempt == 99: raise
                time.sleep(.01)

    @contextmanager
    def execute(self, tid):
        turn = self.turn(tid)
        with locked(self.root / "locks/application.lock"):
            with locked(self.root / "locks" / ("session-" + digest(turn["session"]) + ".lock")):
                done = self.job(tid)["status"] in TERMINAL
                require(done or self.cancelled(tid) or self.next_turn() == tid,
                        "TURN_QUEUED: an earlier turn must finish or be cancelled")
                yield
