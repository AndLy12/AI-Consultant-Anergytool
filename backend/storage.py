import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from core.contracts import Message

SESSION_TTL = 30 * 24 * 3600


class StoreError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sessions (
              id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, expires_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS chats (
              id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES sessions(id),
              title TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS chats_owner ON chats(owner_id, created_at);
            CREATE TABLE IF NOT EXISTS turns (
              id TEXT PRIMARY KEY, chat_id TEXT NOT NULL REFERENCES chats(id),
              request_id TEXT NOT NULL, content TEXT NOT NULL, state TEXT NOT NULL,
              result TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
              UNIQUE(chat_id, request_id));
            CREATE INDEX IF NOT EXISTS turns_chat ON turns(chat_id, created_at);
            PRAGMA user_version=1;
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def session(self, token: str | None) -> str:
        if not token or len(token) > 200:
            raise StoreError(401, "session_required")
        with self.connection() as db:
            row = db.execute("SELECT id FROM sessions WHERE token_hash=? AND expires_at>?",
                             (self.digest(token), time.time())).fetchone()
        if row is None:
            raise StoreError(401, "session_required")
        return row["id"]

    def new_session(self) -> str:
        token = secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute("INSERT INTO sessions VALUES(?,?,?)",
                       (str(uuid4()), self.digest(token), time.time() + SESSION_TTL))
        return token

    @staticmethod
    def owned(db, owner: str, chat_id: str):
        row = db.execute("SELECT * FROM chats WHERE id=? AND owner_id=?", (chat_id, owner)).fetchone()
        if row is None:
            raise StoreError(404, "chat_not_found")
        return dict(row)

    def new_chat(self, owner: str, title: str):
        chat = dict(id=str(uuid4()), owner_id=owner, title=title, archived=0, created_at=time.time())
        with self.connection() as db:
            db.execute("INSERT INTO chats VALUES(:id,:owner_id,:title,:archived,:created_at)", chat)
        chat.pop("owner_id")
        chat["archived"] = False
        return chat

    def list_chats(self, owner: str, offset: int):
        with self.connection() as db:
            return [dict(r) for r in db.execute(
                "SELECT id,title,archived,created_at FROM chats WHERE owner_id=? "
                "ORDER BY created_at DESC,id LIMIT 100 OFFSET ?", (owner, offset))]

    def get_chat(self, owner: str, chat_id: str):
        with self.connection() as db:
            chat = self.owned(db, owner, chat_id)
            rows = db.execute("SELECT * FROM turns WHERE chat_id=? ORDER BY rowid", (chat_id,)).fetchall()
        chat.pop("owner_id")
        chat["archived"] = bool(chat["archived"])
        messages = []
        for r in rows:
            messages.append(dict(id=r["id"] + ":user", role="user", content=r["content"],
                                 request_id=r["request_id"], state=r["state"], created_at=r["created_at"]))
            if r["state"] == "complete":
                result = json.loads(r["result"])
                messages.append(dict(id=r["id"] + ":assistant", role="assistant", content=result["answer"],
                                     result=result, created_at=r["updated_at"]))
        return dict(**chat, messages=messages)

    def archive(self, owner: str, chat_id: str):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.owned(db, owner, chat_id)
            busy = db.execute("SELECT 1 FROM turns WHERE chat_id=? AND state='processing' AND updated_at>?",
                              (chat_id, time.time() - 60)).fetchone()
            if busy:
                raise StoreError(409, "chat_busy")
            db.execute("UPDATE chats SET archived=1 WHERE id=?", (chat_id,))

    def reserve(self, owner: str, chat_id: str, request_id: str, content: str):
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            chat = self.owned(db, owner, chat_id)
            prior = db.execute("SELECT * FROM turns WHERE chat_id=? AND request_id=?", (chat_id, request_id)).fetchone()
            if prior and prior["content"] != content:
                raise StoreError(409, "idempotency_conflict")
            if prior and prior["state"] == "complete":
                return prior["id"], json.loads(prior["result"]), ()
            if chat["archived"]:
                raise StoreError(409, "chat_archived")
            db.execute("UPDATE turns SET state='failed' WHERE chat_id=? AND state='processing' AND updated_at<=?",
                       (chat_id, now - 60))
            if db.execute("SELECT 1 FROM turns WHERE chat_id=? AND state='processing'", (chat_id,)).fetchone():
                raise StoreError(409, "chat_busy")
            # Retrying an older failed turn after a newer turn would reorder the conversation.
            if prior and db.execute("SELECT 1 FROM turns WHERE chat_id=? AND rowid>(SELECT rowid FROM turns WHERE id=?)",
                                    (chat_id, prior["id"])).fetchone():
                raise StoreError(409, "stale_retry")
            if not prior and db.execute("SELECT count(*) FROM turns WHERE chat_id=?", (chat_id,)).fetchone()[0] >= 200:
                raise StoreError(409, "chat_limit_reached")
            history = []
            for r in db.execute("SELECT content,result FROM turns WHERE chat_id=? AND state='complete' ORDER BY rowid", (chat_id,)):
                history.extend([Message("user", r["content"]), Message("assistant", json.loads(r["result"])["answer"])])
            turn_id = prior["id"] if prior else str(uuid4())
            if prior:
                db.execute("UPDATE turns SET state='processing',updated_at=? WHERE id=?", (now, turn_id))
            else:
                db.execute("INSERT INTO turns VALUES(?,?,?,?,'processing',NULL,?,?)",
                           (turn_id, chat_id, request_id, content, now, now))
            history.append(Message("user", content))
        return turn_id, None, tuple(history)

    def finish(self, turn_id: str, result: dict | None):
        with self.connection() as db:
            db.execute("UPDATE turns SET state=?,result=?,updated_at=? WHERE id=? AND state='processing'",
                       ("complete" if result else "failed", json.dumps(result, ensure_ascii=False) if result else None,
                        time.time(), turn_id))
