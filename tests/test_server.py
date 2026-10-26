import time

import pytest

from hive.server.app import create_app
from hive.server import service as sservice
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
    r = ag["prog1"].post("/integrate", body, expect=200)
    assert r["status"] == "push_main" and r["grant"]
    ag["prog1"].post("/integrate/confirm", {"grant": r["grant"], "sha": "b" * 40}, expect=200)
    task = admin.get(f"/tasks/{t['id']}")
    assert task["state"] == "done" and not task["approved"]  # approval is spent
    assert admin.get("/fleet")["main_sha"] == "b" * 40
    assert admin.get("/leases") == [] or all(x["id"] != l["id"] for x in admin.get("/leases"))


def test_confirm_requires_a_matching_one_time_grant(env):
    """Audit H1: confirm used to trust any caller."""
    hive, admin, ag = env
    to_running(admin, ag)
    l1 = ag["prog1"].post("/leases", {"globs": ["src/a/**"]}, expect=201)
    fences = {str(l1["id"]): l1["fencing"]}
    # No grant / someone else's lease: refused, lease untouched.
    ag["prog2"].post("/integrate/confirm", {"sha": "f" * 40, "leases": fences}, expect=400)
    ag["orch"].post("/integrate/confirm", {"grant": "forged", "sha": "e" * 40}, expect=403)
    assert any(x["id"] == l1["id"] for x in admin.get("/leases"))
    assert admin.get("/fleet")["main_sha"] is None
    # A real grant only works for its own agent, its own sha, once.
    r = ag["prog1"].post("/integrate", {"sha": "a" * 40, "leases": fences}, expect=200)
    ag["prog2"].post("/integrate/confirm", {"grant": r["grant"], "sha": "a" * 40}, expect=403)
    ag["prog1"].post("/integrate/confirm", {"grant": r["grant"], "sha": "b" * 40}, expect=409)
    ag["prog1"].post("/integrate/confirm", {"grant": r["grant"], "sha": "a" * 40}, expect=200)
    ag["prog1"].post("/integrate/confirm", {"grant": r["grant"], "sha": "a" * 40}, expect=409)
    assert admin.get("/fleet")["main_sha"] == "a" * 40
    # Integrating without naming the leases is refused.
    ag["prog2"].post("/integrate", {"sha": "c" * 40, "leases": {}}, expect=400)


def test_confirm_refused_after_lease_revoked(env):
    hive, admin, ag = env
    to_running(admin, ag)
    l1 = ag["prog1"].post("/leases", {"globs": ["src/a/**"]}, expect=201)
    r = ag["prog1"].post("/integrate", {"sha": "a" * 40,
                                        "leases": {str(l1["id"]): l1["fencing"]}}, expect=200)
    ag["orch"].post(f"/leases/{l1['id']}/force-release", {"reason": "stale"}, expect=200)
    ag["prog1"].post("/integrate/confirm", {"grant": r["grant"], "sha": "a" * 40}, expect=410)
    assert admin.get("/fleet")["main_sha"] is None


def test_review_gate_cannot_be_bypassed(env):
    """Audit H2: no task_id, someone else's task, or an old approval must not skip review."""
    hive, admin, ag = env
    admin.put("/config", {"integration": {"review": "orchestrator"}}, expect=200)
    to_running(admin, ag)
    tasks = {t["assignee"]: t for t in admin.get("/tasks")}
    t1, t2 = tasks["prog1"], tasks["prog2"]
    l1 = ag["prog1"].post("/leases", {"globs": t1["path_hints"]}, expect=201)
    body1 = {"sha": "1" * 40, "leases": {str(l1["id"]): l1["fencing"]}}
    ag["prog1"].post("/integrate", body1, expect=400)                       # no task_id
    ag["prog1"].post("/integrate", {**body1, "task_id": t2["id"]}, expect=403)  # not mine
    # Finish t1 through a genuine review.
    ag["prog1"].post("/integrate", {**body1, "task_id": t1["id"]}, expect=200)
    ag["orch"].post(f"/tasks/{t1['id']}/review", {"approve": True}, expect=200)
    r = ag["prog1"].post("/integrate", {**body1, "task_id": t1["id"]}, expect=200)
    ag["prog1"].post("/integrate/confirm", {"grant": r["grant"], "sha": "1" * 40}, expect=200)
    # prog2 can't reuse t1's (spent) approval, and t1 itself is done.
    l2 = ag["prog2"].post("/leases", {"globs": t2["path_hints"]}, expect=201)
    body2 = {"sha": "2" * 40, "leases": {str(l2["id"]): l2["fencing"]}}
    ag["prog2"].post("/integrate", {**body2, "task_id": t1["id"]}, expect=403)
    l3 = ag["prog1"].post("/leases", {"globs": ["src/z/**"]}, expect=201)
    ag["prog1"].post("/integrate", {"sha": "3" * 40, "leases": {str(l3["id"]): l3["fencing"]},
                                    "task_id": t1["id"]}, expect=409)
    r = ag["prog2"].post("/integrate", {**body2, "task_id": t2["id"]}, expect=200)
    assert r["status"] == "review_pending"


def test_answering_while_paused_keeps_pause_and_resume_restores(env):
    """Audit M1: answering a freezing decision while paused used to strand the fleet."""
    hive, admin, ag = env
    to_running(admin, ag)
    l = ag["prog1"].post("/leases", {"globs": ["src/a/**"], "ttl": 60}, expect=201)
    d = ag["orch"].post("/decisions", {"kind": "stuck", "question": "help"}, expect=201)
    admin.post("/fleet/pause", expect=200)
    admin.post("/fleet/resume", expect=200)              # still blocked -> back to frozen
    assert admin.get("/fleet")["state"] == "frozen"
    admin.post("/fleet/pause", expect=200)
    time.sleep(0.05)
    admin.post(f"/decisions/{d['id']}/answer", {"answer": "ok"}, expect=200)
    f = admin.get("/fleet")
    assert f["state"] == "paused" and f["blocking_decision"] is None
    admin.post("/fleet/resume", expect=200)
    assert admin.get("/fleet")["state"] == "running"
    lease = [x for x in admin.get("/leases") if x["id"] == l["id"]][0]
    assert lease["expires_at"] > l["expires_at"]          # frozen+paused time was credited


def test_queued_decision_activates_while_paused(env):
    hive, admin, ag = env
    to_running(admin, ag)
    d1 = ag["orch"].post("/decisions", {"kind": "stuck", "question": "1"}, expect=201)
    d2 = ag["orch"].post("/decisions", {"kind": "stuck", "question": "2"}, expect=201)
    admin.post("/fleet/pause", expect=200)
    admin.post(f"/decisions/{d1['id']}/answer", {"answer": "ok"}, expect=200)
    f = admin.get("/fleet")
    assert f["state"] == "paused" and f["blocking_decision"] == d2["id"]
    admin.post("/fleet/resume", expect=200)
    assert admin.get("/fleet")["state"] == "frozen"
    admin.post(f"/decisions/{d2['id']}/answer", {"answer": "ok"}, expect=200)
    assert admin.get("/fleet")["state"] == "running"


def test_plan_approved_while_paused_stays_paused(env):
    """Audit L13."""
    hive, admin, ag = env
    admin.post("/fleet/start", {"goal": "g"}, expect=200)
    ag["orch"].post("/tasks", {"title": "A"}, expect=201)
    d = ag["orch"].post("/decisions", {"kind": "start_approval", "question": "?"}, expect=201)
    admin.post("/fleet/pause", expect=200)
    admin.post(f"/decisions/{d['id']}/answer", {"answer": "approve"}, expect=200)
    assert admin.get("/fleet")["state"] == "paused"
    assert all(t["assignee"] is None for t in admin.get("/tasks"))   # nobody starts yet
    admin.post("/fleet/resume", expect=200)
    assert admin.get("/fleet")["state"] == "running"
    assert all(t["state"] == "claimed" for t in admin.get("/tasks"))  # scheduled on resume


def test_pre_assigned_draft_is_handed_over_on_approval(env):
    """Audit M2."""
    hive, admin, ag = env
    admin.post("/fleet/start", {"goal": "g"}, expect=200)
    t = ag["orch"].post("/tasks", {"title": "pre", "assignee": "prog2"}, expect=201)
    assert t["state"] == "draft"
    d = ag["orch"].post("/decisions", {"kind": "start_approval", "question": "?"}, expect=201)
    admin.post(f"/decisions/{d['id']}/answer", {"answer": "approve"}, expect=200)
    assert admin.get(f"/tasks/{t['id']}")["state"] == "claimed"
    assert any(e["type"] == "task.assigned" and e["payload"]["id"] == t["id"]
               for e in ag["prog2"].get("/events?since=0")["events"])


def test_config_is_validated_against_agents_and_permissions(env):
    """Audit M9 and L6."""
    hive, admin, ag = env
    admin.put("/config", {"roles": {"writer": {"task_kinds": ["prose"]}}}, expect=200)
    admin.patch("/agents/prog2", {"roles": ["programmer", "writer"]}, expect=200)
    r = admin.put("/config", {}, expect=409)               # 'writer' still held by prog2
    assert "prog2" in r["message"]
    assert ag["prog2"].get("/agents/me")["roles"] == ["programmer", "writer"]   # still works
    admin.put("/config", {"permissions": {"preset": "bogus"}}, expect=400)
    admin.put("/config", {"roles": {"x": {"no_such_field": 1}}}, expect=400)


def test_bad_input_is_400_not_500(env):
    """Audit L2."""
    hive, admin, ag = env
    to_running(admin, ag)
    ag["orch"].post("/tasks", {"title": "x", "path_hints": ["../etc/passwd"]}, expect=400)
    ag["prog1"].post("/leases", {"globs": ["x/**"], "ttl": "abc"}, expect=400)
    ag["prog1"].post("/messages/ack", {"ids": ["x"]}, expect=400)
    ag["prog1"].post("/messages/ack", {"ids": "1,2"}, expect=400)
    ag["prog1"].post("/agents/me/quota", {"until": "soon"}, expect=400)
    ag["prog1"].post("/stuck", {"task_id": "first", "details": "x"}, expect=400)
    ag["prog1"].post("/integrate", {"sha": "a" * 40, "leases": ["1"]}, expect=400)
    r = hive_client(hive).get("/api/v1/fleet", headers={"Authorization": "Bearer tökén"})
    assert r.status_code == 401


def hive_client(hive):
    return create_app(hive, admin_token=ADMIN).test_client()


def test_command_output_only_for_requester_and_user(env):
    """Audit L3 and L20."""
    hive, admin, ag = env
    to_running(admin, ag)
    a = ag["prog1"].post("/approvals", {"command": "rm -r build", "reason": "x"}, expect=201)
    ag["orch"].post(f"/approvals/{a['id']}/decide", {"approve": True}, expect=200)
    ag["prog1"].post(f"/approvals/{a['id']}/claim", expect=200)
    ag["prog1"].post(f"/approvals/{a['id']}/result", {"exit_code": 0, "output": "SECRET"},
                     expect=200)
    for who in ("prog2", "orch"):
        assert all(x["output"] is None for x in ag[who].get("/approvals"))
        assert ag[who].get(f"/approvals/{a['id']}")["output"] is None
    assert ag["prog1"].get(f"/approvals/{a['id']}")["output"] == "SECRET"
    assert admin.get("/approvals")[-1]["output"] == "SECRET"


def test_reading_an_expired_approval_does_not_write(env):
    hive, admin, ag = env
    to_running(admin, ag)
    a = ag["prog1"].post("/approvals", {"command": "rm -r b", "reason": "x"}, expect=201)
    ag["orch"].post(f"/approvals/{a['id']}/decide", {"approve": True}, expect=200)
    from hive.server import service
    old, service.APPROVAL_TTL = service.APPROVAL_TTL, -1
    try:
        assert ag["prog1"].get(f"/approvals/{a['id']}")["state"] == "expired"
        with hive.engine.connect() as c:   # not yet recorded: reads stay read-only
            raw = c.exec_driver_sql("SELECT state FROM approvals WHERE id=?",
                                    (a["id"],)).scalar()
        assert raw == "approved"
        ag["prog1"].post(f"/approvals/{a['id']}/claim", expect=410)
    finally:
        service.APPROVAL_TTL = old


def test_only_the_addressee_answers_a_report_request(env):
    """Audit L5."""
    hive, admin, ag = env
    to_running(admin, ag)
    req = ag["orch"].post("/reports/requests", {"target": "prog1", "about": "x"}, expect=201)
    ag["prog2"].post("/reports", {"summary": "fake", "request_id": req["id"]}, expect=403)
    ag["prog1"].post("/reports", {"summary": "real", "request_id": req["id"]}, expect=201)


def test_dependents_of_cancelled_task_are_blocked_and_reported(env):
    """Audit L7."""
    hive, admin, ag = env
    admin.post("/fleet/start", {"goal": "g"}, expect=200)
    a = ag["orch"].post("/tasks", {"title": "A"}, expect=201)
    b = ag["orch"].post("/tasks", {"title": "B", "deps": [a["id"]]}, expect=201)
    d = ag["orch"].post("/decisions", {"kind": "start_approval", "question": "?"}, expect=201)
    admin.post(f"/decisions/{d['id']}/answer", {"answer": "approve"}, expect=200)
    ag["orch"].post(f"/tasks/{a['id']}/cancel", {"reason": "dropped"}, expect=200)
    assert admin.get(f"/tasks/{b['id']}")["state"] == "blocked"
    assert any(e["type"] == "task.blocked" and e["payload"]["id"] == b["id"]
               for e in ag["orch"].get("/events?since=0")["events"])


def test_failover_keeps_old_orchestrators_other_roles(tmp_path):
    """Audit L18."""
    from hive.server.service import ADMIN as A, Actor
    hive = create_hive(f"sqlite:///{tmp_path / 'h.db'}", tick_interval=0,
                       failover_after=0.01, offline_after=60)
    hive.set_config(A, {"orchestrator": {"standby": ["stand"]}})
    hive.enroll(A, "orch", "claude", ["orchestrator", "planner"])
    hive.enroll(A, "stand", "claude", ["programmer"])
    hive.enroll(A, "prog", "claude", ["programmer"])
    for n, r in (("orch", ["orchestrator", "planner"]), ("stand", ["programmer"]),
                 ("prog", ["programmer"])):
        hive.heartbeat(Actor("agent", n, r))
    hive.start_fleet(A, "g")
    time.sleep(0.05)
    hive.heartbeat(Actor("agent", "stand", ["programmer"]))
    hive.tick(force=True)
    roles = {a["name"]: a["roles"] for a in hive.roster()}
    assert roles["stand"] == ["orchestrator"]
    assert set(roles["orch"]) == {"programmer", "planner"}


def test_deadlock_errors_are_retryable(env):
    """Audit M13: database deadlocks map to 503 + Retry-After like 'database is locked'."""
    import sqlite3
    from sqlalchemy.exc import OperationalError
    hive, admin, ag = env
    app = create_app(hive, admin_token=ADMIN)

    def boom():
        raise OperationalError("UPDATE", {}, sqlite3.OperationalError(
            "(1213, 'Deadlock found when trying to get lock; try restarting transaction')"))

    hive.get_fleet = boom
    r = app.test_client().get("/api/v1/fleet", headers={"Authorization": f"Bearer {ADMIN}"})
    assert r.status_code == 503 and r.headers["Retry-After"] == "1"


def test_no_self_review_and_reviewer_fallback(env):
    """Audit M5 and M10."""
    hive, admin, ag = env
    admin.put("/config", {"integration": {"review": "reviewer"}}, expect=200)
    admin.patch("/agents/prog1", {"roles": ["programmer", "reviewer"]}, expect=200)
    to_running(admin, ag)
    t = [x for x in admin.get("/tasks") if x["assignee"] == "prog1"][0]
    l = ag["prog1"].post("/leases", {"globs": t["path_hints"]}, expect=201)
    r = ag["prog1"].post("/integrate", {"sha": "1" * 40, "task_id": t["id"],
                                        "leases": {str(l["id"]): l["fencing"]}}, expect=200)
    assert r == {"status": "review_pending", "reviewer": "orch"}  # only reviewer is the author
    ag["prog1"].post(f"/tasks/{t['id']}/review", {"approve": True}, expect=403)
    ag["orch"].post(f"/tasks/{t['id']}/review", {"approve": True}, expect=200)


def test_command_approvals(env):
    hive, admin, ag = env
    to_running(admin, ag)
    req = ag["prog1"].post("/approvals", {"command": "git push origin hive/prog1",
                                          "reason": "share branch for review"}, expect=201)
    assert req["state"] == "requested"
    evs = ag["orch"].get("/events?since=0")["events"]
    assert any(e["type"] == "approval.requested" and e["payload"]["id"] == req["id"]
               for e in evs)
    # Hard limits: never requestable.
    ag["prog1"].post("/approvals", {"command": "hive decide 1 approve", "reason": "x"},
                     expect=403)
    ag["prog1"].post("/approvals", {"command": "git push origin main", "reason": "x"},
                     expect=403)
    # Only the orchestrator decides; nobody approves their own request.
    ag["prog2"].post(f"/approvals/{req['id']}/decide", {"approve": True}, expect=403)
    ag["prog1"].post(f"/approvals/{req['id']}/claim", expect=409)  # not approved yet
    ag["orch"].post(f"/approvals/{req['id']}/decide", {"approve": True, "note": "ok"},
                    expect=200)
    assert any(e["type"] == "approval.decided" for e in ag["prog1"].get("/events")["events"])
    ag["prog2"].post(f"/approvals/{req['id']}/claim", expect=403)  # not the requester
    claimed = ag["prog1"].post(f"/approvals/{req['id']}/claim", expect=200)
    assert claimed["command"] == "git push origin hive/prog1"
    ag["prog1"].post(f"/approvals/{req['id']}/claim", expect=409)  # one-time use
    ag["prog1"].post(f"/approvals/{req['id']}/result", {"exit_code": 0, "output": "done"},
                     expect=200)
    assert admin.get(f"/approvals/{req['id']}")["exit_code"] == 0

    own = ag["orch"].post("/approvals", {"command": "git push origin hive/orch",
                                         "reason": "x"}, expect=201)
    ag["orch"].post(f"/approvals/{own['id']}/decide", {"approve": True}, expect=403)

    # Approvals expire if not used in time.
    late = ag["prog2"].post("/approvals", {"command": "rm -r build", "reason": "clean"},
                            expect=201)
    ag["orch"].post(f"/approvals/{late['id']}/decide", {"approve": True}, expect=200)
    from hive.server import service
    old = service.APPROVAL_TTL
    service.APPROVAL_TTL = -1
    try:
        ag["prog2"].post(f"/approvals/{late['id']}/claim", expect=410)
    finally:
        service.APPROVAL_TTL = old


def test_role_change_of_mandatory_role_needs_user(env):
    hive, admin, ag = env
    r = ag["orch"].patch("/agents/prog2", {"roles": ["reviewer"]}, expect=200)
    assert r["status"] == "proposed"
    admin.post(f"/decisions/{r['decision_id']}/answer", {"answer": "approve"}, expect=200)
    roles = {a["name"]: a["roles"] for a in admin.get("/agents")}
    assert roles["prog2"] == ["reviewer"]


def test_dashboard_is_served_without_external_assets(tmp_path):
    hive = create_hive(f"sqlite:///{tmp_path / 'hive.db'}", tick_interval=0)
    r = create_app(hive, admin_token=ADMIN).test_client().get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and r.mimetype == "text/html"
    assert "Hive admin" in html and 'id="out"' in html
    assert "<script src=" not in html and "<link rel=\"stylesheet\"" not in html  # self-contained


def test_reviewers_decide_command_approvals_first_and_the_orchestrator_gets_the_rest(env):
    hive, admin, ag = env
    to_running(admin, ag)

    def requested(who, aid):
        return [e for e in ag[who].get("/events")["events"]
                if e["type"] == "approval.requested" and e["payload"]["id"] == aid]

    # No reviewer yet: the orchestrator gets the request at once.
    a = ag["prog1"].post("/approvals", {"command": "rm -r build", "reason": "x"}, expect=201)
    assert a["sent_to"] == ["orchestrator"] and requested("orch", a["id"])

    tok = admin.post("/agents", {"name": "rev1", "vendor": "claude", "roles": ["reviewer"]},
                     expect=201)["token"]
    ag["rev1"] = Client(hive_client(hive), tok)
    ag["rev1"].post("/agents/me/heartbeat", {})  # online
    b = ag["prog1"].post("/approvals", {"command": "git reset --hard", "reason": "y"},
                         expect=201)
    assert b["sent_to"] == ["reviewer"]
    assert requested("rev1", b["id"]) and not requested("orch", b["id"])

    # Only the requester's daemon hands an undecided request on, to the orchestrator.
    ag["prog2"].post(f"/approvals/{b['id']}/escalate", expect=403)
    assert ag["prog1"].post(f"/approvals/{b['id']}/escalate", expect=200)["escalated"]
    assert requested("orch", b["id"])[-1]["payload"]["escalated"]

    # A reviewer may decide; nobody decides their own request; a decided one isn't handed on.
    ag["prog1"].post(f"/approvals/{b['id']}/decide", {"approve": True}, expect=403)
    ag["rev1"].post(f"/approvals/{b['id']}/decide", {"approve": True, "note": "ok"},
                    expect=200)
    assert not ag["prog1"].post(f"/approvals/{b['id']}/escalate", expect=200)["escalated"]
    # A reviewer's own request goes to the orchestrator: no other reviewer is there.
    c = ag["rev1"].post("/approvals", {"command": "rm -r out", "reason": "z"}, expect=201)
    assert c["sent_to"] == ["orchestrator"]


def test_review_sha_binding_and_integrate_path_validation(env):
    hive, admin, ag = env
    admin.put("/config", {"integration": {"review": "orchestrator"}}, expect=200)
    to_running(admin, ag)
    t = ag["orch"].post("/tasks", {"title": "feat", "assignee": "prog1"}, expect=201)
    lease = ag["prog1"].post("/leases", {"globs": ["src/**"], "task_id": t["id"]}, expect=201)
    fences = {str(lease["id"]): lease["fencing"]}

    # Manual transition to 'review' is rejected, and review_verdict before integrate is 409.
    ag["prog1"].patch(f"/tasks/{t['id']}", {"state": "review"}, expect=400)
    ag["orch"].post(f"/tasks/{t['id']}/review", {"approve": True}, expect=409)

    # Protected paths and unleased paths are rejected in integrate().
    sha_a = "a" * 40
    sha_b = "b" * 40
    r = ag["prog1"].post("/integrate", {"sha": sha_a, "base_sha": "", "leases": fences,
                                        "task_id": t["id"], "paths": [".hive/prompts/worker.md"]},
                         expect=403)
    assert r["error"] == "protected_path"
    r = ag["prog1"].post("/integrate", {"sha": sha_a, "base_sha": "", "leases": fences,
                                        "task_id": t["id"], "paths": ["docs/readme.md"]},
                         expect=403)
    assert r["error"] == "path_not_leased"
    r = ag["prog1"].post("/integrate", {"sha": sha_a, "base_sha": "not-a-sha", "leases": fences,
                                        "task_id": t["id"], "paths": ["src/a.py"]},
                         expect=400)
    assert r["error"] == "bad_sha"

    # Submit sha_a for review and approve it.
    res = ag["prog1"].post("/integrate", {"sha": sha_a, "base_sha": "", "leases": fences,
                                          "task_id": t["id"], "paths": ["src/a.py"]}, expect=200)
    assert res["status"] == "review_pending"
    ag["orch"].post(f"/tasks/{t['id']}/review", {"approve": True, "notes": "lgtm"}, expect=200)

    # Submitting a different sha_b must NOT inherit sha_a's approval.
    res_b = ag["prog1"].post("/integrate", {"sha": sha_b, "base_sha": "", "leases": fences,
                                            "task_id": t["id"], "paths": ["src/a.py"]}, expect=200)
    assert res_b["status"] == "review_pending"
    task_now = admin.get(f"/tasks/{t['id']}")
    assert task_now["pending_sha"] == sha_b and task_now["approved"] is False

    # Re-approve sha_b, obtain grant, then verify reviewer rejection revokes the unconfirmed grant.
    ag["orch"].post(f"/tasks/{t['id']}/review", {"approve": True}, expect=200)
    grant1 = ag["prog1"].post("/integrate", {"sha": sha_b, "base_sha": "", "leases": fences,
                                             "task_id": t["id"], "paths": ["src/a.py"]},
                              expect=200)["grant"]
    ag["orch"].post(f"/tasks/{t['id']}/review", {"approve": False, "notes": "wait"}, expect=200)
    ag["prog1"].post("/integrate/confirm", {"grant": grant1, "sha": sha_b, "paths": ["src/a.py"]},
                     expect=409)

    # Re-submit sha_b, approve, get grant, verify confirm_integration also checks paths & base_sha.
    ag["prog1"].post("/integrate", {"sha": sha_b, "base_sha": "", "leases": fences,
                                    "task_id": t["id"], "paths": ["src/a.py"]}, expect=200)
    ag["orch"].post(f"/tasks/{t['id']}/review", {"approve": True}, expect=200)
    grant2 = ag["prog1"].post("/integrate", {"sha": sha_b, "base_sha": "", "leases": fences,
                                             "task_id": t["id"], "paths": ["src/a.py"]},
                              expect=200)["grant"]
    ag["prog1"].post("/integrate/confirm", {"grant": grant2, "sha": sha_b,
                                            "paths": [".claude/settings.json"]}, expect=403)
    ag["prog1"].post("/integrate/confirm", {"grant": grant2, "sha": sha_b,
                                            "paths": ["unleased/x.py"]}, expect=403)
    ag["prog1"].post("/integrate/confirm", {"grant": grant2, "sha": sha_b,
                                            "paths": ["src/a.py"]}, expect=200)
    assert admin.get("/fleet")["main_sha"] == sha_b


def test_integrate_rejects_protected_paths_under_a_wildcard_lease(env):
    hive, admin, ag = env
    to_running(admin, ag)
    lease = ag["prog1"].post("/leases", {"globs": ["**"]}, expect=201)
    fences = {str(lease["id"]): lease["fencing"]}
    for p in (".hive/prompts/worker.md", ".hive/agent/claude/hooks/hooks.json"):
        r = ag["prog1"].post("/integrate", {"sha": "d" * 40, "base_sha": "", "leases": fences,
                                            "paths": [p]}, expect=403)
        assert r["error"] == "protected_path"


def test_dot_segment_paths_cannot_be_leased_twice(env):
    hive, admin, ag = env
    to_running(admin, ag)
    ag["prog1"].post("/leases", {"globs": ["src/./app.py"]}, expect=201)
    r = ag["prog2"].post("/leases", {"globs": ["src/app.py"]}, expect=409)
    assert r["error"] == "lease_conflict"


def test_reset_clears_the_old_runs_leases_decisions_and_approvals(env):
    hive, admin, ag = env
    to_running(admin, ag)
    ag["prog1"].post("/leases", {"globs": ["src/locked.py"]}, expect=201)
    approval = ag["prog1"].post("/approvals", {"command": "rm -r out", "reason": "z"}, expect=201)
    stale = ag["orch"].post("/decisions", {"kind": "question", "question": "Old blocker?",
                                           "freeze": True}, expect=201)
    queued = ag["orch"].post("/decisions", {"kind": "stuck", "question": "Old queued?"},
                             expect=201)
    admin.post("/fleet/reset", {}, expect=200)
    assert admin.get("/leases") == []
    assert admin.get("/decisions?state=open") == [] and admin.get("/decisions?state=queued") == []
    states = {d["id"]: d["state"] for d in admin.get("/decisions")}
    assert states[stale["id"]] == states[queued["id"]] == "cancelled"
    assert admin.get(f"/approvals/{approval['id']}")["state"] == "denied"
    # The next run can lease the same file and is not unfrozen by the old decision.
    to_running(admin, ag)
    ag["prog2"].post("/leases", {"globs": ["src/locked.py"]}, expect=201)
    admin.post(f"/decisions/{stale['id']}/answer", {"answer": "yes"}, expect=409)


def test_answering_a_decision_the_fleet_is_not_waiting_on_leaves_it_frozen(env):
    hive, admin, ag = env
    to_running(admin, ag)
    current = ag["orch"].post("/decisions", {"kind": "stuck", "question": "Now?"}, expect=201)
    other = ag["orch"].post("/decisions", {"kind": "stuck", "question": "Later?"}, expect=201)
    assert other["state"] == "queued"
    # A blocking decision left open while another one blocks (e.g. from before a restart).
    with hive._write() as c:
        c.execute(sservice.update(sservice.decisions)
                  .where(sservice.decisions.c.id == other["id"]).values(state="open"))
    admin.post(f"/decisions/{other['id']}/answer", {"answer": "ok"}, expect=200)
    f = admin.get("/fleet")
    assert f["state"] == "frozen" and f["blocking_decision"] == current["id"]
    admin.post(f"/decisions/{current['id']}/answer", {"answer": "ok"}, expect=200)
    assert admin.get("/fleet")["state"] == "running"


def test_preassigned_tasks_wait_for_their_dependencies(env):
    hive, admin, ag = env
    to_running(admin, ag)
    o = ag["orch"]
    t1 = o.post("/tasks", {"title": "step 1"}, expect=201)
    t2 = o.post("/tasks", {"title": "step 2", "deps": [t1["id"]], "assignee": "prog2"},
                expect=201)
    assert t2["state"] == "open" and t2["assignee"] == "prog2"   # reserved, not started
    ag["prog1"].post(f"/tasks/{t2['id']}/claim", {}, expect=409)  # reserved for prog2
    ag["prog2"].post(f"/tasks/{t2['id']}/claim", {}, expect=409)  # deps not done yet
    t3 = o.post("/tasks", {"title": "step 3", "deps": [t1["id"]]}, expect=201)
    o.post(f"/tasks/{t3['id']}/assign", {"to": "prog1"}, expect=200)
    assert admin.get(f"/tasks/{t3['id']}")["state"] == "open"
    # step 1 done: both are handed over.
    who = admin.get(f"/tasks/{t1['id']}")["assignee"]
    if not who:
        who = "prog1"
        ag[who].post(f"/tasks/{t1['id']}/claim", {}, expect=200)
    ag[who].post(f"/tasks/{t1['id']}/complete", {"result": "ok"}, expect=200)
    assert admin.get(f"/tasks/{t2['id']}")["state"] == "claimed"
    assert admin.get(f"/tasks/{t3['id']}")["state"] == "claimed"


def test_failed_tasks_block_their_dependents_transitively(env):
    hive, admin, ag = env
    to_running(admin, ag)
    o = ag["orch"]
    t1 = o.post("/tasks", {"title": "step 1"}, expect=201)
    t2 = o.post("/tasks", {"title": "step 2", "deps": [t1["id"]], "assignee": "prog2"},
                expect=201)
    t3 = o.post("/tasks", {"title": "step 3", "deps": [t2["id"]]}, expect=201)
    t4 = o.post("/tasks", {"title": "step 4", "deps": [t3["id"]]}, expect=201)
    o.patch(f"/tasks/{t1['id']}", {"state": "failed", "note": "broken"}, expect=200)
    for t in (t2, t3, t4):
        assert admin.get(f"/tasks/{t['id']}")["state"] == "blocked", t["title"]


def test_giving_the_orchestrator_role_hands_it_over(env):
    hive, admin, ag = env
    to_running(admin, ag)
    admin.patch("/agents/prog1", {"roles": ["orchestrator"]}, expect=200)
    roles = {a["name"]: a["roles"] for a in admin.get("/agents")}
    assert roles["prog1"] == ["orchestrator"] and "orchestrator" not in roles["orch"]
    assert admin.get("/fleet")["orchestrator"] == "prog1"
    admin.patch("/agents/orch", {"roles": ["programmer"]}, expect=200)
    admin.delete("/agents/orch", expect=200)            # no longer the fleet's orchestrator
    admin.delete("/agents/prog1", expect=409)           # it is now


def test_role_max_is_enforced_for_other_roles(env):
    hive, admin, ag = env
    admin.put("/config", {"roles": {"docs": {"can_code": True, "task_kinds": ["docs"],
                                             "max": 1}}}, expect=200)
    admin.patch("/agents/prog1", {"roles": ["programmer", "docs"]}, expect=200)
    r = admin.patch("/agents/prog2", {"roles": ["programmer", "docs"]}, expect=409)
    assert r["error"] == "role_full"


def test_reopened_task_is_free_for_any_agent(env):
    hive, admin, ag = env
    to_running(admin, ag)
    o = ag["orch"]
    t = o.post("/tasks", {"title": "flaky", "assignee": "prog1"}, expect=201)
    o.patch(f"/tasks/{t['id']}", {"state": "failed", "note": "prog1 broke"}, expect=200)
    for a in ("prog1", "prog2"):   # finish their start-up tasks so both are idle
        for x in admin.get(f"/tasks?assignee={a}"):
            if x["state"] in ("claimed", "in_progress"):
                ag[a].post(f"/tasks/{x['id']}/complete", {"result": "ok"}, expect=200)
    admin.patch("/agents/prog1", {"roles": ["reviewer"]}, expect=200)   # prog1 can't code now
    r = o.patch(f"/tasks/{t['id']}", {"state": "open", "note": "retry"}, expect=200)
    assert r["state"] == "claimed" and r["assignee"] == "prog2"
    assert r["branch"] == "hive/prog2" and r["approved"] is False
