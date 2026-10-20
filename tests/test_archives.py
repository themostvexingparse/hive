"""Archiving a fleet's history, clearing it, and reading archives back."""

import gzip
import json

from sqlalchemy import MetaData, create_engine

from hive.server import db
from hive.server.app import create_app
from hive.server.service import create_hive

from test_server import ADMIN, Client, env, to_running  # noqa: F401  (env is a fixture)


def busy_fleet(admin, ag):
    """A running fleet with a bit of everything, then paused with nothing in flight."""
    to_running(admin, ag)
    l1 = ag["prog1"].post("/leases", {"globs": ["src/a/**"]}, expect=201)
    ag["prog1"].post("/agents/me/stream", {"run": "r1", "lines": ["hello", "→ Bash ls"]})
    ag["orch"].post("/messages", {"channel": "#user", "body": "plan is ready"}, expect=201)
    admin.post("/messages", {"channel": "#user", "body": "thanks"}, expect=201)
    ag["orch"].post("/status", {"summary": "two tasks running"}, expect=201)
    admin.post("/fleet/pause", {}, expect=200)
    return l1


def test_snapshot_keeps_data_and_round_trips(env):
    hive, admin, ag = env
    busy_fleet(admin, ag)
    tasks_before = admin.get("/tasks")
    meta = admin.post("/admin/archives", {"label": "  mid   run "}, expect=201)
    assert meta["kind"] == "snapshot" and meta["label"] == "mid run"
    assert meta["counts"]["tasks"] == 2 and meta["counts"]["streams"] == 2
    assert meta["agents"] == ["orch", "prog1", "prog2"] and meta["goal"] == "build it"
    assert admin.get("/tasks") == tasks_before  # nothing cleared
    assert [m["id"] for m in admin.get("/admin/archives")] == [meta["id"]]

    fc = create_app(hive, admin_token=ADMIN).test_client()
    h = {"Authorization": f"Bearer {ADMIN}"}
    plain = fc.get(f"/api/v1/admin/archives/{meta['id']}", headers=h)
    data = plain.get_json()
    assert data["format"] == "hive-archive" and data["version"] == 1
    assert [t["title"] for t in data["tasks"]] == ["A", "B"]
    assert [m["body"] for m in data["messages"]][-2:] == ["plan is ready", "thanks"]
    assert all("token_hash" not in a for a in data["agents"])
    assert data["fleet"]["state"] == "paused" and "config" in data["fleet"]
    # Browsers get the stored gzip bytes as-is; downloads get the .json.gz file.
    gz = fc.get(f"/api/v1/admin/archives/{meta['id']}",
                headers={**h, "Accept-Encoding": "gzip, deflate"})
    assert gz.headers["Content-Encoding"] == "gzip"
    assert json.loads(gzip.decompress(gz.data)) == data
    dl = fc.get(f"/api/v1/admin/archives/{meta['id']}?download=1", headers=h)
    assert "attachment" in dl.headers["Content-Disposition"]
    assert json.loads(gzip.decompress(dl.data)) == data


def test_flush_clears_history_but_keeps_the_fleet_usable(env):
    hive, admin, ag = env
    l1 = busy_fleet(admin, ag)
    r = admin.post("/admin/archives", {"flush": True}, expect=409)
    assert "lease" in r["message"] and r["problems"]
    admin.post(f"/leases/{l1['id']}/force-release", {}, expect=200)

    head = admin.get("/events")["head"]
    max_task = max(t["id"] for t in admin.get("/tasks"))
    max_msg = max(m["id"] for m in admin.get("/messages?channel=%23user"))
    max_line = admin.get("/streams")["last_id"]

    meta = admin.post("/admin/archives", {"flush": True, "label": "v1"}, expect=201)
    assert meta["kind"] == "flush" and meta["counts"]["tasks"] == 2

    fl = admin.get("/fleet")
    assert fl["state"] == "setup" and fl["goal"] == "" and fl["orchestrator"] == "orch"
    assert admin.get("/tasks") == [] and admin.get("/status") == []
    assert admin.get("/streams")["lines"] == [] and admin.get("/leases") == []
    assert [a["name"] for a in admin.get("/agents")] == ["orch", "prog1", "prog2"]

    # Daemons resume from their old cursor and see exactly the flush event.
    evs = ag["prog1"].get(f"/events?since={head}")["events"]
    assert [(e["type"], e["payload"].get("archived")) for e in evs] == [("fleet.state", meta["id"])]

    # Agent tokens still work and ids keep counting up (cached ids and cursors stay valid).
    to_running(admin, ag)
    assert min(t["id"] for t in admin.get("/tasks")) > max_task
    m = ag["orch"].post("/messages", {"channel": "#user", "body": "again"}, expect=201)
    assert m["id"] > max_msg
    ag["prog1"].post("/agents/me/stream", {"run": "r2", "lines": ["x"]})
    assert admin.get(f"/streams?since_id={max_line}")["lines"][0]["text"] == "x"


def test_flush_needs_a_paused_or_setup_fleet(env):
    hive, admin, ag = env
    to_running(admin, ag)
    r = admin.post("/admin/archives", {"flush": True}, expect=409)
    assert r["error"] == "bad_state"
    assert admin.get("/admin/archives") == []  # nothing written for a refused flush
    assert len(admin.get("/tasks")) == 2


def test_flush_keeps_ids_on_a_database_made_before_autoincrement(tmp_path):
    """Databases created by older versions have plain INTEGER PRIMARY KEY tables, where SQLite
    would reuse ids after a delete; the flush rebuilds them."""
    url = f"sqlite:///{tmp_path / 'old.db'}"
    old = MetaData()
    for t in db.metadata.tables.values():
        t.to_metadata(old).dialect_options["sqlite"]["autoincrement"] = False
    old.create_all(create_engine(url))

    hive = create_hive(url, tick_interval=0)
    fc = create_app(hive, admin_token=ADMIN).test_client()
    admin = Client(fc, ADMIN)
    tok = admin.post("/agents", {"name": "orch", "vendor": "claude", "roles": ["orchestrator"]},
                     expect=201)["token"]
    admin.post("/messages", {"channel": "#user", "body": "one"}, expect=201)
    admin.post("/messages", {"channel": "#user", "body": "two"}, expect=201)
    head = admin.get("/events")["head"]
    assert head > 0
    admin.post("/admin/archives", {"flush": True}, expect=201)
    m = admin.post("/messages", {"channel": "#user", "body": "three"}, expect=201)
    assert m["id"] == 3
    assert Client(fc, tok).get(f"/events?since={head}")["events"][0]["seq"] == head + 1
    with hive.engine.connect() as c:
        sql = c.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='messages'").scalar()
    assert "AUTOINCREMENT" in sql


def test_archives_are_admin_only_and_ids_are_checked(env):
    hive, admin, ag = env
    meta = admin.post("/admin/archives", {}, expect=201)
    ag["orch"].get("/admin/archives", expect=403)
    ag["orch"].post("/admin/archives", {"flush": True}, expect=403)
    ag["orch"].get(f"/admin/archives/{meta['id']}", expect=403)
    admin.get("/admin/archives/nope", expect=404)
    admin.get("/admin/archives/..%5Chive.db", expect=404)
    admin.delete(f"/admin/archives/{meta['id']}", expect=200)
    admin.delete(f"/admin/archives/{meta['id']}", expect=404)
    assert admin.get("/admin/archives") == []


def test_admin_check_tells_the_admin_token_from_an_agents(env):
    """The dashboard signs in through this: an agent's token can read /fleet, but must not
    get into the admin panel (archives, live output and decisions are admin-only)."""
    hive, admin, ag = env
    assert admin.get("/admin/check", expect=200) == {"ok": True}
    assert ag["orch"].get("/fleet", expect=200)["state"] == "setup"   # what sign-in used to test
    assert ag["orch"].get("/admin/check", expect=403)["message"] == "admin token required"
    fc = create_app(hive, admin_token=ADMIN).test_client()
    assert fc.get("/api/v1/admin/check", headers={"Authorization": "Bearer nope"}).status_code == 403
    assert fc.get("/api/v1/admin/check").status_code == 401


def test_two_archives_in_the_same_second_get_distinct_ids(env):
    hive, admin, ag = env
    a = admin.post("/admin/archives", {}, expect=201)
    b = admin.post("/admin/archives", {}, expect=201)
    assert a["id"] != b["id"] and len(admin.get("/admin/archives")) == 2
