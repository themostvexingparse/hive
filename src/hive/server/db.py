"""Database schema (SQLAlchemy Core). Portable across SQLite, MySQL and Postgres.

Timestamps are float epoch seconds. JSON columns hold lists/dicts.
"""

from __future__ import annotations

from sqlalchemy import (JSON, Boolean, Column, Float, Integer, MetaData, String, Table, Text,
                        create_engine, event)
from sqlalchemy.pool import StaticPool

metadata = MetaData()

agents = Table(
    "agents", metadata,
    Column("name", String(32), primary_key=True),
    Column("vendor", String(16), nullable=False),
    Column("roles", JSON, nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("status", String(16), nullable=False, default="offline"),  # online|offline|busy
    Column("attached", Boolean, nullable=False, default=False),
    Column("quota_until", Float, nullable=False, default=0.0),
    Column("last_seen", Float, nullable=False, default=0.0),
    Column("created", Float, nullable=False),
)

events = Table(
    "events", metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("ts", Float, nullable=False),
    Column("type", String(48), nullable=False, index=True),
    Column("actor", String(32), nullable=False),
    # '*' everyone · 'agent:<name>' · 'role:<role>' · 'admin' (user only)
    Column("audience", String(64), nullable=False, index=True),
    Column("payload", JSON, nullable=False),
)

messages = Table(
    "messages", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", Float, nullable=False),
    Column("channel", String(64), nullable=True, index=True),
    Column("author", String(32), nullable=False),
    Column("recipient", String(32), nullable=True, index=True),
    Column("priority", String(16), nullable=False),
    Column("thread_id", Integer, nullable=True),
    Column("body", Text, nullable=False),
    Column("mentions", JSON, nullable=False),
)

receipts = Table(
    "receipts", metadata,
    Column("message_id", Integer, primary_key=True),
    Column("agent", String(32), primary_key=True),
    Column("acked_at", Float, nullable=False),
)

tasks = Table(
    "tasks", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("title", String(200), nullable=False),
    Column("spec", Text, nullable=False, default=""),
    Column("kind", String(32), nullable=False, default="code"),
    Column("state", String(16), nullable=False, index=True),
    Column("priority", Integer, nullable=False, default=0),
    Column("deps", JSON, nullable=False),
    Column("assignee", String(32), nullable=True, index=True),
    Column("reviewer", String(32), nullable=True),
    Column("created_by", String(32), nullable=False),
    Column("path_hints", JSON, nullable=False),
    Column("branch", String(128), nullable=True),
    Column("pending_sha", String(64), nullable=True),
    Column("approved", Boolean, nullable=False, default=False),
    Column("notes", Text, nullable=False, default=""),
    Column("result", Text, nullable=False, default=""),
    Column("created", Float, nullable=False),
    Column("updated", Float, nullable=False),
)

leases = Table(
    "leases", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("holder", String(32), nullable=False, index=True),
    Column("globs", JSON, nullable=False),
    Column("fencing", Integer, nullable=False),
    Column("task_id", Integer, nullable=True),
    Column("reason", String(500), nullable=False, default=""),
    Column("acquired", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("state", String(16), nullable=False, index=True),  # active|released|expired|revoked
)

fleet = Table(
    "fleet", metadata,
    Column("id", Integer, primary_key=True),
    Column("state", String(24), nullable=False),
    Column("prev_state", String(24), nullable=True),
    Column("since", Float, nullable=False),
    Column("config", JSON, nullable=False),  # repo hive.toml as uploaded by the user
    Column("frozen_at", Float, nullable=True),
    Column("blocking_decision", Integer, nullable=True),
    Column("orchestrator", String(32), nullable=True),  # current active orchestrator
    Column("fencing_counter", Integer, nullable=False, default=0),
    Column("main_sha", String(64), nullable=True),
    Column("goal", Text, nullable=False, default=""),
)

decisions = Table(
    "decisions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String(32), nullable=False),  # start_approval|stuck|role_change|question
    Column("raised_by", String(32), nullable=False),
    Column("question", Text, nullable=False),
    Column("options", JSON, nullable=False),
    Column("context", Text, nullable=False, default=""),
    Column("task_id", Integer, nullable=True),
    Column("blocking", Boolean, nullable=False),
    Column("state", String(16), nullable=False),  # queued|open|answered
    Column("answer", Text, nullable=True),
    Column("note", Text, nullable=True),
    Column("created", Float, nullable=False),
    Column("answered_at", Float, nullable=True),
)

status_feed = Table(
    "status_feed", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", Float, nullable=False),
    Column("author", String(32), nullable=False),
    Column("level", String(24), nullable=False),  # fyi|decision-needed|alert
    Column("summary", String(500), nullable=False),
    Column("body", Text, nullable=False, default=""),
)


streams = Table(
    "streams", metadata,  # live agent output; readable by the admin only (never by agents)
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("agent", String(32), nullable=False, index=True),
    Column("ts", Float, nullable=False),
    Column("run", String(64), nullable=False),
    Column("text", Text, nullable=False),
)

reports = Table(
    "reports", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String(16), nullable=False),  # request|report
    Column("author", String(32), nullable=False),
    Column("target", String(32), nullable=True),  # request: agent asked to report
    Column("request_id", Integer, nullable=True),  # report: the request it answers
    Column("task_id", Integer, nullable=True),
    Column("summary", String(500), nullable=False),
    Column("body", Text, nullable=False, default=""),
    Column("state", String(16), nullable=False),  # open|answered (requests) / filed (reports)
    Column("created", Float, nullable=False),
)


def make_engine(url: str):
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)

        @event.listens_for(engine, "connect")
        def _pragma(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        @event.listens_for(engine, "begin")
        def _begin(conn):
            # Serialize writers: one IMMEDIATE transaction at a time, matching SELECT FOR UPDATE.
            conn.exec_driver_sql("BEGIN IMMEDIATE")

        # Let SQLAlchemy's begin() emit our BEGIN instead of pysqlite's implicit one.
        @event.listens_for(engine, "connect")
        def _isolation(dbapi_conn, _):
            dbapi_conn.isolation_level = None
    else:
        engine = create_engine(url, pool_pre_ping=True, pool_recycle=280)
    metadata.create_all(engine)
    return engine
