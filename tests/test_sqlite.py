"""SQLite deployment behaviour, including the PythonAnywhere free-tier setup (rollback journal on
a network filesystem, one web worker)."""

import sqlite3
import threading
import time

import pytest

from hive.server.app import create_app
from hive.server.db import sqlite_journal_mode
from hive.server.service import ADMIN, create_hive

ADMIN_TOKEN = "admin-secret"


def journal_of(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        con.close()


def test_journal_mode_selection(monkeypatch, tmp_path):
    monkeypatch.delenv("HIVE_SQLITE_JOURNAL", raising=False)
    monkeypatch.delenv("PYTHONANYWHERE_DOMAIN", raising=False)
    monkeypatch.delenv("PYTHONANYWHERE_SITE", raising=False)
    assert sqlite_journal_mode() == "wal"
    monkeypatch.setenv("PYTHONANYWHERE_DOMAIN", "pythonanywhere.com")
    assert sqlite_journal_mode() == "delete"
    monkeypatch.setenv("HIVE_SQLITE_JOURNAL", "truncate")
    assert sqlite_journal_mode() == "truncate"
    monkeypatch.setenv("HIVE_SQLITE_JOURNAL", "bogus")
    with pytest.raises(ValueError):
        sqlite_journal_mode()

    db = tmp_path / "nested" / "dir" / "hive.db"  # parent directories are created
    hive = create_hive(f"sqlite:///{db}", sqlite_journal="delete")
    assert db.exists() and journal_of(db) == "delete"
    assert hive.engine.hive_sqlite_journal == "delete"


@pytest.mark.parametrize("journal", ["wal", "delete"])
def test_reads_and_idle_ticks_never_wait_for_the_write_lock(tmp_path, journal):
    db = tmp_path / "hive.db"
    hive = create_hive(f"sqlite:///{db}", sqlite_journal=journal, tick_interval=0)
    hive.enroll(ADMIN, "orch", "claude", ["orchestrator"])
    # Another writer holds the write lock (RESERVED) for a while.
    other = sqlite3.connect(db, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    try:
        t0 = time.time()
        hive.list_tasks()
        hive.list_events(ADMIN, 0)
        hive.get_fleet()
        hive.roster()
        hive.tick(force=True)  # idle fleet: read-only precheck, no write transaction
        assert time.time() - t0 < 2, "reads or idle tick waited for the write lock"
    finally:
        other.execute("ROLLBACK")
        other.close()


def test_in_process_writers_are_serialized(tmp_path):
    hive = create_hive(f"sqlite:///{tmp_path / 'hive.db'}", sqlite_journal="delete")
    hive.set_config(ADMIN, {})
    errors = []

    def enroll(i):
        try:
            hive.enroll(ADMIN, f"agent-{i}", "claude", ["programmer"])
        except Exception as e:  # pragma: no cover - surfaced below
            errors.append(e)

    threads = [threading.Thread(target=enroll, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(hive.roster()) == 20


def test_backup_endpoint_returns_consistent_copy(tmp_path):
    hive = create_hive(f"sqlite:///{tmp_path / 'hive.db'}", sqlite_journal="delete")
    hive.enroll(ADMIN, "orch", "claude", ["orchestrator"])
    client = create_app(hive, admin_token=ADMIN_TOKEN).test_client()
    assert client.get("/api/v1/admin/backup").status_code == 401
    r = client.get("/api/v1/admin/backup", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    assert r.status_code == 200
    out = tmp_path / "copy.db"
    out.write_bytes(r.data)
    con = sqlite3.connect(out)
    try:
        assert con.execute("SELECT name FROM agents").fetchall() == [("orch",)]
    finally:
        con.close()
    health = client.get("/api/v1/health").get_json()
    assert health == {"ok": True, "sqlite_journal": "delete"}


def test_busy_database_maps_to_503_and_client_retries(tmp_path, monkeypatch):
    from sqlalchemy.exc import OperationalError

    hive = create_hive(f"sqlite:///{tmp_path / 'hive.db'}")
    app = create_app(hive, admin_token=ADMIN_TOKEN)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OperationalError("SELECT", {}, sqlite3.OperationalError("database is locked"))
        return {"state": "setup"}

    monkeypatch.setattr(hive, "get_fleet", flaky)
    c = app.test_client()
    r = c.get("/api/v1/fleet", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    assert r.status_code == 503 and r.headers["Retry-After"] == "1"

    # The daemon/admin client retries 503s transparently.
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from hive.daemon.client import ServerClient
        monkeypatch.setattr("hive.daemon.client.time.sleep", lambda s: None)
        calls["n"] = 1
        assert ServerClient(f"http://127.0.0.1:{srv.server_port}",
                            ADMIN_TOKEN).get("/fleet") == {"state": "setup"}
        assert calls["n"] == 3
    finally:
        srv.shutdown()
