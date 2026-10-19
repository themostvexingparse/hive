"""Local per-machine state: event cursors, inbox cache, pending wake triggers, sessions."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

PRIORITY_RANK = {"info": 0, "normal": 1, "high": 2, "interrupt": 3}

SCHEMA = """
CREATE TABLE IF NOT EXISTS cursor (slot TEXT PRIMARY KEY, seq INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS inbox (
  slot TEXT NOT NULL, msg_id INTEGER NOT NULL, ts REAL NOT NULL, author TEXT NOT NULL,
  channel TEXT, recipient TEXT, priority TEXT NOT NULL, body TEXT NOT NULL,
  relevant INTEGER NOT NULL, injected INTEGER NOT NULL DEFAULT 0, acked INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (slot, msg_id));
CREATE TABLE IF NOT EXISTS triggers (
  id INTEGER PRIMARY KEY AUTOINCREMENT, slot TEXT NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL,
  urgent INTEGER NOT NULL, summary TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS kv (slot TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
  PRIMARY KEY (slot, key));
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()

    def _q(self, sql: str, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    # cursors / kv
    def cursor(self, slot: str) -> int:
        r = self._q("SELECT seq FROM cursor WHERE slot=?", (slot,))
        return r[0][0] if r else 0

    def set_cursor(self, slot: str, seq: int) -> None:
        self._q("INSERT INTO cursor(slot, seq) VALUES(?,?) ON CONFLICT(slot) DO UPDATE SET seq=?",
                (slot, seq, seq))

    def get(self, slot: str, key: str, default=None):
        r = self._q("SELECT value FROM kv WHERE slot=? AND key=?", (slot, key))
        return json.loads(r[0][0]) if r else default

    def put(self, slot: str, key: str, value) -> None:
        v = json.dumps(value)
        self._q("INSERT INTO kv(slot,key,value) VALUES(?,?,?) "
                "ON CONFLICT(slot,key) DO UPDATE SET value=?", (slot, key, v, v))

    # inbox
    def add_message(self, slot: str, m: dict, relevant: bool) -> None:
        self._q("INSERT OR IGNORE INTO inbox(slot,msg_id,ts,author,channel,recipient,priority,body,"
                "relevant) VALUES(?,?,?,?,?,?,?,?,?)",
                (slot, m["id"], m["ts"], m["author"], m.get("channel"), m.get("recipient"),
                 m["priority"], m["body"], int(relevant)))

    def add_notice(self, slot: str, body: str, priority: str = "normal") -> None:
        """A synthetic local message (negative ids) such as 'main advanced'."""
        with self.lock:
            low = self.db.execute("SELECT MIN(msg_id) FROM inbox WHERE slot=?", (slot,)).fetchone()
            mid = min(-1, (low[0] or 0) - 1)
            self.db.execute("INSERT INTO inbox(slot,msg_id,ts,author,channel,recipient,priority,"
                            "body,relevant,acked) VALUES(?,?,?,?,?,?,?,?,1,1)",
                            (slot, mid, time.time(), "hive", None, slot, priority, body))

    def undelivered(self, slot: str, min_priority: str) -> list[dict]:
        rank = PRIORITY_RANK[min_priority]
        rows = self._q("SELECT msg_id,ts,author,channel,recipient,priority,body FROM inbox "
                       "WHERE slot=? AND relevant=1 AND injected=0 ORDER BY ts", (slot,))
        keys = ("id", "ts", "author", "channel", "recipient", "priority", "body")
        return [dict(zip(keys, r)) for r in rows if PRIORITY_RANK[r[5]] >= rank]

    def mark_injected(self, slot: str, ids: list[int]) -> None:
        with self.lock:
            for i in ids:
                self.db.execute("UPDATE inbox SET injected=1 WHERE slot=? AND msg_id=?", (slot, i))

    def unacked_urgent(self, slot: str) -> list[int]:
        return [r[0] for r in self._q(
            "SELECT msg_id FROM inbox WHERE slot=? AND relevant=1 AND acked=0 AND msg_id>0 "
            "AND priority IN ('high','interrupt')", (slot,))]

    def mark_acked(self, slot: str, ids: list[int]) -> None:
        with self.lock:
            for i in ids:
                self.db.execute("UPDATE inbox SET acked=1, injected=1 WHERE slot=? AND msg_id=?",
                                (slot, i))

    def recent(self, slot: str, limit: int = 30, include_irrelevant: bool = True) -> list[dict]:
        rows = self._q("SELECT msg_id,ts,author,channel,recipient,priority,body,acked FROM inbox "
                       "WHERE slot=? AND (relevant=1 OR ?) ORDER BY ts DESC LIMIT ?",
                       (slot, int(include_irrelevant), limit))
        keys = ("id", "ts", "author", "channel", "recipient", "priority", "body", "acked")
        return [dict(zip(keys, r)) for r in reversed(rows)]

    # wake triggers
    def add_trigger(self, slot: str, kind: str, urgent: bool, summary: str) -> None:
        self._q("INSERT INTO triggers(slot,ts,kind,urgent,summary) VALUES(?,?,?,?,?)",
                (slot, time.time(), kind, int(urgent), summary[:2000]))

    def pending_triggers(self, slot: str) -> list[dict]:
        rows = self._q("SELECT id,ts,kind,urgent,summary FROM triggers WHERE slot=? AND consumed=0 "
                       "ORDER BY id", (slot,))
        return [dict(zip(("id", "ts", "kind", "urgent", "summary"), r)) for r in rows]

    def consume_triggers(self, slot: str, up_to_id: int) -> None:
        self._q("UPDATE triggers SET consumed=1 WHERE slot=? AND id<=?", (slot, up_to_id))
