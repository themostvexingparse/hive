"""Database schema (SQLAlchemy Core). Portable across SQLite, MySQL and Postgres.

Timestamps are float epoch seconds. JSON columns hold lists/dicts.
"""

from __future__ import annotations

import os
from pathlib import Path

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


approvals = Table(
    "approvals", metadata,  # dangerous commands a worker may run once the orchestrator agrees
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("agent", String(32), nullable=False, index=True),
    Column("command", Text, nullable=False),
    Column("shell", String(16), nullable=False),  # bash | powershell
    Column("reason", Text, nullable=False),
    Column("task_id", Integer, nullable=True),
    # requested | approved | denied | executed | expired
    Column("state", String(16), nullable=False, index=True),
    Column("decided_by", String(32), nullable=True),
    Column("note", Text, nullable=True),
    Column("created", Float, nullable=False),
    Column("decided_at", Float, nullable=True),
    Column("executed_at", Float, nullable=True),
    Column("exit_code", Integer, nullable=True),
    Column("output", Text, nullable=True),
)

integrations = Table(
    "integrations", metadata,  # grants: main may advance to `sha` only via this one-time token
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("token", String(64), nullable=False, unique=True),
    Column("agent", String(32), nullable=False),
    Column("sha", String(64), nullable=False),
    Column("leases", JSON, nullable=False),  # {lease_id: fencing}
    Column("task_id", Integer, nullable=True),
    Column("state", String(16), nullable=False),  # granted | confirmed
    Column("created", Float, nullable=False),
)

# Computers paired with a short code (`hive machines invite`, then `hive join CODE` there), and
# which agent each one runs. A machine's token only reaches the /machines/me endpoints.
machines = Table(
    "machines", metadata,
    Column("name", String(32), primary_key=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("os", String(32), nullable=False, default=""),
    Column("clis", JSON, nullable=False),  # agent CLIs installed there
    Column("created", Float, nullable=False),
    Column("last_seen", Float, nullable=False, default=0.0),
)

pairing_codes = Table(
    "pairing_codes", metadata,
    Column("code_hash", String(64), primary_key=True),
    Column("expires", Float, nullable=False),
    # A reusable join key (hk_…) pairs any number of computers until it expires or is revoked;
    # a short code works once.
    Column("reusable", Boolean, nullable=False, default=False),
    Column("label", String(64), nullable=False, default=""),
    Column("created", Float, nullable=False, default=0.0),
    Column("uses", Integer, nullable=False, default=0),
)

placements = Table(
    "placements", metadata,
    Column("agent", String(32), primary_key=True),
    Column("machine", String(32), nullable=False, index=True),
    Column("claimed", Float, nullable=False, default=0.0),  # when the machine took its token
    # The slot's settings there ("" = the CLI's defaults), applied by that machine's daemon.
    Column("model", String(64), nullable=False, default=""),
    Column("permission_mode", String(32), nullable=False, default=""),
)

# Tables an archive flush empties. Their ids must keep counting up afterwards: daemons hold event
# cursors and cache message/approval ids, and agents remember task numbers. Postgres and MySQL
# sequences never go back; SQLite reuses max(id)+1 unless the table is AUTOINCREMENT.
FLUSHED_TABLES = (events, messages, tasks, leases, decisions, status_feed, streams, reports,
                  approvals, integrations)
for _t in FLUSHED_TABLES:
    _t.dialect_options["sqlite"]["autoincrement"] = True

SQLITE_JOURNALS = ("wal", "delete", "truncate")


def on_pythonanywhere() -> bool:
    return any(k in os.environ for k in ("PYTHONANYWHERE_DOMAIN", "PYTHONANYWHERE_SITE"))


def sqlite_journal_mode(explicit: str | None = None) -> str:
    """WAL on local disks; rollback journal on network filesystems such as PythonAnywhere's,
    where WAL's shared-memory index does not work."""
    mode = (explicit or os.environ.get("HIVE_SQLITE_JOURNAL") or
            ("delete" if on_pythonanywhere() else "wal")).lower()
    if mode not in SQLITE_JOURNALS:
        raise ValueError(f"HIVE_SQLITE_JOURNAL must be one of {SQLITE_JOURNALS}")
    return mode


def make_engine(url: str, sqlite_journal: str | None = None, small_pool: bool = False):
    """`small_pool`: for a project database, one of many on the server and often idle."""
    pool = {"pool_size": 2, "max_overflow": 4} if small_pool else {}
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}}
        memory = url in ("sqlite://", "sqlite:///:memory:")
        if memory:
            kwargs["poolclass"] = StaticPool
        else:
            kwargs.update(pool)
        engine = create_engine(url, **kwargs)
        if not memory and engine.url.database:
            Path(engine.url.database).expanduser().resolve().parent.mkdir(parents=True,
                                                                          exist_ok=True)
        journal = "memory" if memory else sqlite_journal_mode(sqlite_journal)
        engine.hive_sqlite_journal = journal

        @event.listens_for(engine, "connect")
        def _pragma(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute(f"PRAGMA journal_mode={journal.upper()}")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA synchronous=" + ("NORMAL" if journal == "wal" else "FULL"))
            cur.close()

        @event.listens_for(engine, "begin")
        def _begin(conn):
            # Writers take the write lock up front (like SELECT FOR UPDATE elsewhere); readers
            # (engines with the hive_read option) use a deferred BEGIN and never block writers
            # longer than a read, nor each other.
            if conn.get_execution_options().get("hive_read"):
                conn.exec_driver_sql("BEGIN")
            else:
                conn.exec_driver_sql("BEGIN IMMEDIATE")

        # Let SQLAlchemy's begin() emit our BEGIN instead of pysqlite's implicit one.
        @event.listens_for(engine, "connect")
        def _isolation(dbapi_conn, _):
            dbapi_conn.isolation_level = None
    else:
        engine = create_engine(url, pool_pre_ping=True, pool_recycle=280, **pool)
    metadata.create_all(engine)
    _add_missing_columns(engine, (machines, pairing_codes, placements))
    return engine


def _add_missing_columns(engine, tables) -> None:
    """create_all makes missing tables but never changes existing ones. These tables gained
    columns while they were new; add any a database made earlier lacks (each has a default)."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    for table in tables:
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            ddl = col.type.compile(dialect=engine.dialect)
            default = col.default.arg if col.default is not None else ""
            literal = (("1" if default else "0") if isinstance(default, bool)
                       else repr(default) if isinstance(default, str) else str(default))
            with engine.begin() as c:
                c.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl} "
                               f"NOT NULL DEFAULT {literal}"))
