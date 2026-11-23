"""Local submission/checkpoint references; immutable files remain evidence owners."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from ..evidence_io import digest, inside, read, require, sha, write
from ..model_calls import locked

VERSION = "gc-clean-research-state-1"


def put(path, value, *, atomic=False):
    """Idempotent create-only publication, rejecting a conflicting partial file."""
    path = Path(path)
    if path.exists():
        require(read(path) == value, "Immutable receipt differs: " + str(path))
    elif not atomic:
        write(path, value)
    else:
        # The caller holds the lock for this publication. A process exit during
        # the write leaves only the unreferenced temporary file.
        attempt = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            write(attempt, value)
            attempt.rename(path)
        finally:
            attempt.unlink(missing_ok=True)
    return path


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, version TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS turns(
              id TEXT PRIMARY KEY, session TEXT NOT NULL REFERENCES sessions(id),
              submit_token TEXT NOT NULL, question TEXT NOT NULL, campaign TEXT NOT NULL,
              sequence INTEGER NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0,
              UNIQUE(session, submit_token), UNIQUE(sequence));
            CREATE TABLE IF NOT EXISTS events(
              sequence INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL,
              turn TEXT NOT NULL, event_key TEXT NOT NULL, payload TEXT NOT NULL,
              UNIQUE(turn,event_key));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / "sessions.sqlite", timeout=5)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def submit(self, session, submit_token, question, campaign):
        require(all(isinstance(v, str) and v.strip() for v in (session, submit_token, question, campaign)),
                "Missing submission identity")
        tid = digest([VERSION, session, submit_token])
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO sessions VALUES (?,?)", (session, VERSION))
            require(db.execute("SELECT version FROM sessions WHERE id=?", (session,)).fetchone()[0] == VERSION,
                    "Incompatible session; read-only display required")
            old = db.execute("SELECT question,campaign FROM turns WHERE id=?", (tid,)).fetchone()
            if old:
                require(old == (question, campaign), "Submit token reused for different content/campaign")
            else:
                seq = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM turns").fetchone()[0]
                db.execute("INSERT INTO turns(id,session,submit_token,question,campaign,sequence) VALUES (?,?,?,?,?,?)",
                           (tid, session, submit_token, question, campaign, seq))
        return tid

    def turn(self, tid):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM turns WHERE id=?", (tid,)).fetchone()
        require(row is not None, "Unknown turn")
        return dict(row)

    def cancelled(self, tid):
        return bool(self.turn(tid)["cancelled"])

    def next_turn(self):
        with self.connect() as db:
            row = db.execute("""SELECT id FROM turns WHERE cancelled=0 AND NOT EXISTS
                (SELECT 1 FROM events WHERE events.turn=turns.id AND event_key='turn_complete')
                ORDER BY sequence LIMIT 1""").fetchone()
        return row[0] if row else None

    def cancel(self, tid):
        with locked(self.root / "locks/admission.lock"):
            self.turn(tid)
            with self.connect() as db:
                db.execute("UPDATE turns SET cancelled=1 WHERE id=?", (tid,))
            self.event(tid, "cancel", {"kind": "cancel_requested"})

    def event(self, tid, key, payload, *, atomic=False):
        turn = self.turn(tid)
        require(not {"version", "session_id", "turn_id"}.intersection(payload), "Event cannot replace its identity")
        value = {"version": VERSION, "session_id": turn["session"], "turn_id": tid, **payload}
        path = self.root / "events" / tid / (digest(key) + ".json")
        # Serialize event repair and publication; files precede SQLite references.
        with locked(self.root / "locks/events.lock"):
            put(path, value, atomic=atomic)
            wire = json.dumps({"path": str(path.relative_to(self.root)), "sha256": sha(path)}, sort_keys=True)
            with self.connect() as db:
                db.execute("INSERT OR IGNORE INTO events(session,turn,event_key,payload) VALUES (?,?,?,?)",
                           (turn["session"], tid, key, wire))
                row = db.execute("SELECT sequence,payload FROM events WHERE turn=? AND event_key=?", (tid, key)).fetchone()
                require(row[1] == wire, "Event reference changed")
        return row[0]

    def events(self, tid, after=0):
        self.turn(tid)
        with self.connect() as db:
            rows = db.execute("SELECT sequence,payload FROM events WHERE turn=? AND sequence>? ORDER BY sequence",
                              (tid, after)).fetchall()
        output = []
        for seq, wire in rows:
            ref = json.loads(wire); path = inside(self.root, ref["path"])
            require(sha(path) == ref["sha256"], "Event file missing or changed")
            output.append({"sequence": seq, **read(path)})
        return output

    @contextmanager
    def execute(self, tid):
        turn = self.turn(tid)
        with locked(self.root / "locks/application.lock"):
            with locked(self.root / "locks" / ("session-" + digest(turn["session"]) + ".lock")):
                complete = any(e["kind"] == "mock_complete" for e in self.events(tid))
                require(complete or self.cancelled(tid) or self.next_turn() == tid,
                        "TURN_QUEUED: an earlier turn must finish or be cancelled")
                yield

    @contextmanager
    def gpu(self):
        with locked(self.root / "locks/gpu.lock"):
            yield
