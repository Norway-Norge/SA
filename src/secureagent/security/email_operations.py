"""Durable local operation identities for approved email sends."""

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from secureagent.security.workspace import is_link_or_reparse


class EmailOperationStore:
    def __init__(self, directory: Path):
        self.path = directory / "email-operations.sqlite3"
        if os.path.lexists(self.path) and (is_link_or_reparse(self.path) or not self.path.is_file()):
            raise ValueError("邮件操作记录不是安全的普通文件")
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS email_operations (
                operation_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, parameter_hash TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, UNIQUE(session_id, parameter_hash))""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def parameter_hash(client, args):
        # Hash the trusted effective SMTP configuration and exact message, never the credential.
        payload = {"version": 1, "server": client.smtp_host, "port": client.smtp_port,
                   "account": client.smtp_user, "sender": client.sender, "transport": "smtp_tls",
                   "to": args["to"], "subject": args["subject"], "body": args["body"]}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def prepare(self, session, parameter_hash):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT operation_id, state FROM email_operations WHERE session_id=? AND parameter_hash=?",
                             (session, parameter_hash)).fetchone()
            if row is None:
                operation_id = str(uuid.uuid4())
                db.execute("INSERT INTO email_operations VALUES (?, ?, ?, 'PREPARED', NULL)",
                           (operation_id, session, parameter_hash))
                return operation_id, "PREPARED", True
            operation_id, state = row
            if state == "DENIED":
                db.execute("UPDATE email_operations SET state='PREPARED', result=NULL WHERE operation_id=?", (operation_id,))
                return operation_id, "PREPARED", True
            return operation_id, state, False

    def begin_send(self, operation_id, session, parameter_hash):
        with self._connect() as db:
            cursor = db.execute("""UPDATE email_operations SET state='DISPATCHING', result=NULL
                WHERE operation_id=? AND session_id=? AND parameter_hash=? AND state='PREPARED'""",
                (operation_id, session, parameter_hash))
            return cursor.rowcount == 1

    def finish(self, operation_id, session, parameter_hash, state, result):
        if state not in {"SENT", "UNKNOWN", "DENIED"}:
            raise ValueError("邮件操作状态无效")
        with self._connect() as db:
            cursor = db.execute("""UPDATE email_operations SET state=?, result=?
                WHERE operation_id=? AND session_id=? AND parameter_hash=? AND state=?""",
                (state, json.dumps(result, ensure_ascii=False), operation_id, session, parameter_hash,
                 "PREPARED" if state == "DENIED" else "DISPATCHING"))
            if cursor.rowcount != 1:
                raise ValueError("邮件操作状态或参数不匹配")

    def get(self, operation_id, session):
        with self._connect() as db:
            row = db.execute("SELECT state, result FROM email_operations WHERE operation_id=? AND session_id=?",
                             (operation_id, session)).fetchone()
        if row is None:
            return None
        state, result = row
        return {"operation_id": operation_id, "status": "UNKNOWN" if state == "DISPATCHING" else state,
                "recorded_state": state, "result": json.loads(result) if result else None}
