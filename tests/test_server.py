import time

import pytest

from hive.server.app import create_app
from hive.server.service import create_hive

ADMIN = "admin-secret"


class Client:
    def __init__(self, flask_client, token):
        self.c, self.h = flask_client, {"Authorization": f"Bearer {token}"}

    def __getattr__(self, method):
        def call(path, json=None, expect=None, **kw):
            r = getattr(self.c, method)("/api/v1" + path, json=json, headers=self.h, **kw)
            if expect is not None:
                assert r.status_code == expect, r.get_json()
            return r.get_json()
        return call


@pytest.fixture(params=["wal", "delete"])
def env(tmp_path, request):
    hive = create_hive(f"sqlite:///{tmp_path / 'hive.db'}", sqlite_journal=request.param,
                       tick_interval=0)
    app = create_app(hive, admin_token=ADMIN)
    fc = app.test_client()
    admin = Client(fc, ADMIN)
    admin.put("/config", {"integration": {"review": "off"}}, expect=200)
    agents = {}
    for name, roles in [("orch", ["orchestrator"]), ("prog1", ["programmer"]),
                        ("prog2", ["programmer"])]:
        tok = admin.post("/agents", {"name": name, "vendor": "claude", "roles": roles},
                         expect=201)["token"]
        agents[name] = Client(fc, tok)
        agents[name].post("/agents/me/heartbeat", {})
    return hive, admin, agents


def to_running(admin, agents):
    admin.post("/fleet/start", {"goal": "build it"}, expect=200)
    o = agents["orch"]
    o.post("/tasks", {"title": "A", "path_hints": ["src/a/**"]}, expect=201)
    o.post("/tasks", {"title": "B", "path_hints": ["src/b/**"]}, expect=201)
    d = o.post("/decisions", {"kind": "start_approval", "question": "OK?"}, expect=201)
    admin.post(f"/decisions/{d['id']}/answer", {"answer": "approve"}, expect=200)


def test_fleet_validation_requires_mandatory_roles(tmp_path):
    hive = create_hive(f"sqlite:///{tmp_path / 'h.db'}")
    admin = Client(create_app(hive, admin_token=ADMIN).test_client(), ADMIN)
    admin.post("/agents", {"name": "solo", "vendor": "claude", "roles": ["orchestrator"]},
               expect=201)
    r = admin.post("/fleet/start", {"goal": "x"}, expect=400)
    assert any("programmer" in p for p in r["problems"])


def test_orchestrator_cannot_code():
    from hive.roles import BUILTIN_ROLES, validate_assignment
    with pytest.raises(ValueError):
        validate_assignment(["orchestrator", "programmer"], BUILTIN_ROLES)


def test_start_gate_and_scheduling(env):
    hive, admin, ag = env
    admin.post("/fleet/start", {"goal": "build it"}, expect=200)
    assert admin.get("/fleet")["state"] == "planning"
    # programmers cannot take leases during planning
    ag["prog1"].post("/leases", {"globs": ["src/**"]}, expect=423)
    t = ag["orch"].post("/tasks", {"title": "A"}, expect=201)
    assert t["state"] == "draft" and t["assignee"] is None
    # programmers may not raise decisions
    ag["prog1"].post("/decisions", {"kind": "start_approval", "question": "?"}, expect=403)
    d = ag["orch"].post("/decisions", {"kind": "start_approval", "question": "Plan OK?"},
                        expect=201)
    assert admin.get("/fleet")["state"] == "awaiting_approval"
    admin.post(f"/decisions/{d['id']}/answer", {"answer": "reject", "note": "smaller"},
               expect=200)
    assert admin.get("/fleet")["state"] == "planning"
    d = ag["orch"].post("/decisions", {"kind": "start_approval", "question": "v2?"}, expect=201)
    admin.post(f"/decisions/{d['id']}/answer", {"answer": "approve"}, expect=200)
    assert admin.get("/fleet")["state"] == "running"
    task = admin.get(f"/tasks/{t['id']}")
    assert task["state"] == "claimed" and task["assignee"] in ("prog1", "prog2")
    evs = ag[task["assignee"]].get("/events?since=0")["events"]
    assert any(e["type"] == "task.assigned" and e["payload"]["assignee"] == task["assignee"]
               for e in evs)


def test_lease_conflicts_and_fencing(env):
    hive, admin, ag = env
    to_running(admin, ag)
    l1 = ag["prog1"].post("/leases", {"globs": ["src/a/**"]}, expect=201)
    r = ag["prog2"].post("/leases", {"globs": ["src/a/x.py", "src/c/**"]}, expect=409)
    assert r["conflicts"][0]["holder"] == "prog1"
    l2 = ag["prog2"].post("/leases", {"globs": ["src/b/**"]}, expect=201)
    assert l2["fencing"] > l1["fencing"]
    ag["orch"].post("/leases", {"globs": ["docs/**"]}, expect=403)  # orchestrator can't code
    ag["orch"].post(f"/leases/{l1['id']}/force-release", {"reason": "stale"}, expect=200)
    ag["prog1"].post("/integrate", {"sha": "a" * 40, "leases": {str(l1["id"]): l1["fencing"]}},
                     expect=410)
    ag["prog2"].post("/leases", {"globs": ["src/a/**"]}, expect=201)


def test_freeze_on_stuck_and_thaw(env):
    hive, admin, ag = env
    to_running(admin, ag)
    lease = ag["prog1"].post("/leases", {"globs": ["src/a/**"], "ttl": 60}, expect=201)
    tid = admin.get("/tasks?assignee=prog1")[0]["id"]
    ag["prog1"].post("/stuck", {"task_id": tid, "details": "API key missing"}, expect=200)
    orch_events = ag["orch"].get("/events?since=0")["events"]
    assert any(e["type"] == "agent.stuck" for e in orch_events)
    assert not any(e["type"] == "agent.stuck" for e in ag["prog2"].get("/events")["events"])
    d = ag["orch"].post("/decisions", {"kind": "stuck", "question": "Provide key?",
                                       "task_id": tid}, expect=201)
    assert admin.get("/fleet")["state"] == "frozen"
    ag["prog2"].post("/leases", {"globs": ["src/b/**"]}, expect=423)
    # second blocking decision queues
    d2 = ag["orch"].post("/decisions", {"kind": "stuck", "question": "second"}, expect=201)
    assert d2["state"] == "queued"
    time.sleep(0.05)
    admin.post(f"/decisions/{d['id']}/answer", {"answer": "here it is"}, expect=200)
    f = admin.get("/fleet")
    assert f["state"] == "frozen" and f["blocking_decision"] == d2["id"]
    admin.post(f"/decisions/{d2['id']}/answer", {"answer": "ok"}, expect=200)
    assert admin.get("/fleet")["state"] == "running"
    new = [l for l in admin.get("/leases") if l["id"] == lease["id"]][0]
    assert new["expires_at"] > lease["expires_at"]
    assert admin.get(f"/tasks/{tid}")["state"] == "in_progress"


def test_messages_audience_and_interrupt(env):
    hive, admin, ag = env
    ag["prog1"].post("/messages", {"recipient": "prog2", "body": "psst"}, expect=201)
    ag["prog1"].post("/messages", {"body": "hi all", "priority": "interrupt"}, expect=403)
    ag["orch"].post("/messages", {"body": "stop @prog1", "priority": "interrupt"}, expect=201)
    e2 = [e["type"] for e in ag["prog2"].get("/events")["events"] if e["type"] == "message.posted"]
    eo = [e for e in ag["orch"].get("/events")["events"] if e["type"] == "message.posted"]
    assert len(e2) == 2 and len(eo) == 1
    assert eo[0]["payload"]["message"]["mentions"] == ["prog1"]


def test_review_flow(env):
    hive, admin, ag = env
    admin.put("/config", {"integration": {"review": "orchestrator"}}, expect=200)
    to_running(admin, ag)
    t = admin.get("/tasks?assignee=prog1")[0]
    l = ag["prog1"].post("/leases", {"globs": t["path_hints"]}, expect=201)
    body = {"sha": "b" * 40, "leases": {str(l["id"]): l["fencing"]}, "task_id": t["id"]}
    r = ag["prog1"].post("/integrate", body, expect=200)
    assert r == {"status": "review_pending", "reviewer": "orch"}
    ag["prog1"].post(f"/tasks/{t['id']}/complete", {}, expect=409)
    ag["orch"].post(f"/tasks/{t['id']}/review", {"approve": True}, expect=200)
    assert ag["prog1"].post("/integrate", body, expect=200)["status"] == "push_main"
    ag["prog1"].post("/integrate/confirm", body, expect=200)
    assert admin.get(f"/tasks/{t['id']}")["state"] == "done"
    assert admin.get("/fleet")["main_sha"] == "b" * 40
    assert admin.get("/leases") == [] or all(x["id"] != l["id"] for x in admin.get("/leases"))


def test_role_change_of_mandatory_role_needs_user(env):
    hive, admin, ag = env
    r = ag["orch"].patch("/agents/prog2", {"roles": ["reviewer"]}, expect=200)
    assert r["status"] == "proposed"
    admin.post(f"/decisions/{r['decision_id']}/answer", {"answer": "approve"}, expect=200)
    roles = {a["name"]: a["roles"] for a in admin.get("/agents")}
    assert roles["prog2"] == ["reviewer"]
