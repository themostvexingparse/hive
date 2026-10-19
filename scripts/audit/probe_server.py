"""Audit probes for server-side suspects. Each prints REPRODUCED / not reproduced."""
import sys
import tempfile
import traceback
from pathlib import Path

from hive.server.app import create_app
from hive.server.service import create_hive

ADMIN = "admin-secret"
results = []


class C:
    def __init__(self, fc, token):
        self.c, self.h = fc, {"Authorization": f"Bearer {token}"}

    def req(self, method, path, json=None, headers=None):
        r = getattr(self.c, method)("/api/v1" + path, json=json, headers=headers or self.h)
        return r.status_code, r.get_json(silent=True)

    def get(self, p): return self.req("get", p)
    def post(self, p, j=None): return self.req("post", p, j or {})
    def patch(self, p, j=None): return self.req("patch", p, j or {})
    def put(self, p, j=None): return self.req("put", p, j or {})


def world(review="off", extra_agents=(), config=None):
    d = Path(tempfile.mkdtemp())
    hive = create_hive(f"sqlite:///{d / 'h.db'}", tick_interval=0)
    fc = create_app(hive, admin_token=ADMIN).test_client()
    admin = C(fc, ADMIN)
    cfg = {"integration": {"review": review}}
    cfg.update(config or {})
    admin.put("/config", cfg)
    ag = {}
    for name, roles in [("orch", ["orchestrator"]), ("prog1", ["programmer"]),
                        ("prog2", ["programmer"]), *extra_agents]:
        s, r = admin.post("/agents", {"name": name, "vendor": "claude", "roles": roles})
        assert s == 201, r
        ag[name] = C(fc, r["token"])
        ag[name].post("/agents/me/heartbeat")
    return hive, fc, admin, ag


def to_running(admin, ag, tasks=({"title": "A", "path_hints": ["src/a/**"]},)):
    admin.post("/fleet/start", {"goal": "g"})
    for t in tasks:
        ag["orch"].post("/tasks", dict(t))
    s, d = ag["orch"].post("/decisions", {"kind": "start_approval", "question": "?"})
    admin.post(f"/decisions/{d['id']}/answer", {"answer": "approve"})


def probe(name):
    def deco(fn):
        try:
            ok, detail = fn()
        except Exception as e:  # noqa
            ok, detail = None, "probe crashed: " + "".join(traceback.format_exception_only(e))
        results.append((name, ok, detail))
        return fn
    return deco


@probe("P1 confirm_integration releases another agent's lease / moves main without checks")
def _():
    hive, fc, admin, ag = world()
    to_running(admin, ag)
    s, l1 = ag["prog1"].post("/leases", {"globs": ["src/a/**"]})
    s, r = ag["prog2"].post("/integrate/confirm",
                            {"sha": "f" * 40, "leases": {str(l1["id"]): 0}})
    lease = [x for x in hive.list_leases(active_only=False) if x["id"] == l1["id"]][0]
    s2, r2 = ag["orch"].post("/integrate/confirm", {"sha": "e" * 40, "leases": {}})
    f = hive.get_fleet()
    return (lease["state"] == "released" and s == 200,
            f"prog2 confirm -> {s}, prog1's lease now {lease['state']}; orchestrator (no "
            f"lease_release tool) confirm -> {s2}; main_sha now {(f['main_sha'] or 'unset')[:6]}")


@probe("P2a review bypass: integrate without task_id skips mandatory review")
def _():
    hive, fc, admin, ag = world(review="orchestrator")
    to_running(admin, ag)
    s, l = ag["prog1"].post("/leases", {"globs": ["src/a/**"]})
    s, r = ag["prog1"].post("/integrate", {"sha": "a" * 40,
                                          "leases": {str(l["id"]): l["fencing"]}})
    return (r.get("status") == "push_main",
            f"review=orchestrator, integrate without task_id -> {r}")


@probe("P2b review bypass: reuse another (already approved) task id")
def _():
    hive, fc, admin, ag = world(review="orchestrator")
    to_running(admin, ag, tasks=({"title": "A", "path_hints": ["src/a/**"]},
                                 {"title": "B", "path_hints": ["src/b/**"]}))
    tasks = {t["title"]: t for t in admin.get("/tasks")[1]}
    a = tasks["A"]
    owner = ag[a["assignee"]]
    other = ag["prog2" if a["assignee"] == "prog1" else "prog1"]
    s, l = owner.post("/leases", {"globs": ["src/a/**"]})
    body = {"sha": "b" * 40, "leases": {str(l["id"]): l["fencing"]}, "task_id": a["id"]}
    owner.post("/integrate", body)
    ag["orch"].post(f"/tasks/{a['id']}/review", {"approve": True})
    owner.post("/integrate", body)
    owner.post("/integrate/confirm", body)
    s, l2 = other.post("/leases", {"globs": ["src/b/**"]})
    s, r = other.post("/integrate", {"sha": "c" * 40, "leases": {str(l2["id"]): l2["fencing"]},
                                     "task_id": a["id"]})
    return (r.get("status") == "push_main",
            f"other agent integrates unreviewed work quoting done+approved task #{a['id']} "
            f"-> {r}")


@probe("P3 draft task created with an assignee is never started after approval")
def _():
    hive, fc, admin, ag = world()
    admin.post("/fleet/start", {"goal": "g"})
    s, t = ag["orch"].post("/tasks", {"title": "pre", "assignee": "prog1"})
    s, d = ag["orch"].post("/decisions", {"kind": "start_approval", "question": "?"})
    admin.post(f"/decisions/{d['id']}/answer", {"answer": "approve"})
    hive.tick(force=True)
    task = admin.get(f"/tasks/{t['id']}")[1]
    evs = [e for e in ag["prog1"].get("/events?since=0")[1]["events"]
           if e["type"] == "task.assigned" and e["payload"]["id"] == t["id"]]
    return (task["state"] == "open" and task["assignee"] == "prog1" and not evs,
            f"after approval: state={task['state']} assignee={task['assignee']}, "
            f"task.assigned events={len(evs)}")


@probe("P4 path_hints with '..' crashes with 500 instead of 400")
def _():
    hive, fc, admin, ag = world()
    s, r = ag["orch"].post("/tasks", {"title": "x", "path_hints": ["../etc/passwd"]})
    return (s == 500 or s is None, f"status {s}")


@probe("P5 removing a custom role from config while an agent holds it breaks the server")
def _():
    hive, fc, admin, ag = world(extra_agents=(("docs", ["writer"]),),
                                config={"roles": {"writer": {"task_kinds": ["docs2"]}}})
    s0, _ = ag["docs"].get("/agents/me")
    sput, _ = admin.put("/config", {"integration": {"review": "off"}})  # drops 'writer'
    s1, _ = ag["docs"].get("/agents/me")
    to_running_ok = True
    s2, r2 = admin.post("/fleet/start", {"goal": "g"})
    return (s1 == 500, f"PUT /config -> {sput}; whoami before={s0}, after={s1}; fleet start={s2} "
                       f"{(r2 or {}).get('error')}")


@probe("P6 answering a stuck decision while paused leaves the fleet frozen forever")
def _():
    hive, fc, admin, ag = world()
    to_running(admin, ag)
    s, d = ag["orch"].post("/decisions", {"kind": "stuck", "question": "help"})
    admin.post("/fleet/pause")
    admin.post(f"/decisions/{d['id']}/answer", {"answer": "ok"})
    f = admin.get("/fleet")[1]
    s, r = admin.post("/fleet/resume")
    return (f["state"] == "frozen" and f["blocking_decision"] is None,
            f"state={f['state']} blocking_decision={f['blocking_decision']}; resume -> {s} "
            f"{(r or {}).get('message')}")


@probe("P7 `git -C . push origin main` is not recognised as a push to main (approvable)")
def _():
    hive, fc, admin, ag = world()
    to_running(admin, ag)
    s, a = ag["prog1"].post("/approvals", {"command": "git -C . push origin main",
                                          "reason": "x"})
    s2, d = ag["orch"].post(f"/approvals/{a['id']}/decide", {"approve": True}) if s == 201 \
        else (None, None)
    return (s == 201 and s2 == 200,
            f"request -> {s}, orchestrator approve -> {s2} (should be 403 forbidden)")


@probe("P11 an agent holding programmer+reviewer can approve its own work")
def _():
    hive, fc, admin, ag = world(review="reviewer",
                                extra_agents=(("rev", ["reviewer"]),))
    admin.patch("/agents/prog1", {"roles": ["programmer", "reviewer"]})
    s, tok = admin.post("/agents/prog1/token")
    from types import SimpleNamespace
    prog1 = C(fc, tok["token"])
    prog1.post("/agents/me/heartbeat")
    to_running(admin, {**ag, "prog1": prog1}, tasks=({"title": "A", "assignee": "prog1",
                                                      "path_hints": ["src/a/**"]},))
    t = [x for x in admin.get("/tasks")[1] if x["title"] == "A"][0]
    if t["state"] != "claimed":
        prog1.post(f"/tasks/{t['id']}/claim")
    s, l = prog1.post("/leases", {"globs": ["src/a/**"]})
    prog1.post("/integrate", {"sha": "d" * 40, "leases": {str(l["id"]): l["fencing"]},
                              "task_id": t["id"]})
    s, r = prog1.post(f"/tasks/{t['id']}/review", {"approve": True})
    return (s == 200 and r.get("approved"),
            f"task state before verdict: review; self-verdict -> {s}, approved={r.get('approved')}")


@probe("P13 malformed numeric input returns 500")
def _():
    hive, fc, admin, ag = world()
    to_running(admin, ag)
    s1, _ = ag["prog1"].post("/leases", {"globs": ["x/**"], "ttl": "abc"})
    s2, _ = ag["prog1"].post("/messages/ack", {"ids": ["x"]})
    s3, _ = ag["prog1"].post("/agents/me/quota", {"until": "soon"})
    return (500 in (s1, s2, s3), f"ttl='abc' -> {s1}, ack ids=['x'] -> {s2}, until='soon' -> {s3}")


@probe("P14 non-ASCII bearer token returns 500")
def _():
    hive, fc, admin, ag = world()
    r = fc.get("/api/v1/fleet", headers={"Authorization": "Bearer tökén"})
    return (r.status_code == 500, f"status {r.status_code}")


@probe("P15 any agent can read other agents' approved-command output")
def _():
    hive, fc, admin, ag = world()
    to_running(admin, ag)
    s, a = ag["prog1"].post("/approvals", {"command": "rm -r build", "reason": "x"})
    ag["orch"].post(f"/approvals/{a['id']}/decide", {"approve": True})
    ag["prog1"].post(f"/approvals/{a['id']}/claim")
    ag["prog1"].post(f"/approvals/{a['id']}/result", {"exit_code": 0, "output": "SECRET=xyz"})
    s, rows = ag["prog2"].get("/approvals")
    return (any(r.get("output") == "SECRET=xyz" for r in rows),
            "prog2 sees prog1's command output via GET /approvals")


@probe("P16 lease paths differing only in case do not conflict")
def _():
    hive, fc, admin, ag = world()
    to_running(admin, ag)
    s1, _ = ag["prog1"].post("/leases", {"globs": ["src/App.py"]})
    s2, _ = ag["prog2"].post("/leases", {"globs": ["src/app.py"]})
    return (s1 == 201 and s2 == 201, f"src/App.py -> {s1}, src/app.py -> {s2} "
                                     "(same file on Windows/macOS)")


@probe("P18 any agent can answer a report request addressed to someone else")
def _():
    hive, fc, admin, ag = world()
    to_running(admin, ag)
    s, req = ag["orch"].post("/reports/requests", {"target": "prog1", "about": "status"})
    s, r = ag["prog2"].post("/reports", {"summary": "fake", "request_id": req["id"]})
    rq = [x for x in admin.get("/reports?kind=request")[1] if x["id"] == req["id"]][0]
    return (s == 201 and rq["state"] == "answered",
            f"prog2 answers prog1's request -> {s}; request state {rq['state']}")


@probe("P20 review mode 'reviewer' with no eligible reviewer leaves the task in review with "
       "reviewer=None")
def _():
    hive, fc, admin, ag = world(review="reviewer",
                                extra_agents=(("both", ["programmer", "reviewer"]),))
    # Only reviewer is 'both'; make sure the task lands on 'both'.
    admin.patch("/agents/prog1", {"roles": ["orchestrator"]}) if False else None
    to_running(admin, ag, tasks=({"title": "A", "assignee": "both"},))
    t = [x for x in admin.get("/tasks")[1] if x["title"] == "A"][0]
    both_tok = admin.post("/agents/both/token")[1]["token"]
    both = C(fc, both_tok)
    both.post("/agents/me/heartbeat")
    if t["state"] != "claimed":
        both.post(f"/tasks/{t['id']}/claim")
    s, l = both.post("/leases", {"globs": ["src/**"]})
    s, r = both.post("/integrate", {"sha": "1" * 40, "leases": {str(l["id"]): l["fencing"]},
                                    "task_id": t["id"]})
    return (r.get("reviewer") is None and r.get("status") == "review_pending",
            f"integrate -> {r}")


@probe("P21 invalid [permissions] config makes approval requests 500")
def _():
    hive, fc, admin, ag = world()
    sput, _ = admin.put("/config", {"permissions": {"preset": "bogus"}})
    to_running(admin, ag)
    s, r = ag["prog1"].post("/approvals", {"command": "rm -r build", "reason": "x"})
    return (s == 500, f"PUT /config with preset='bogus' -> {sput}; approval request -> {s}")


@probe("P22 a task depending on a cancelled task can never start (no notice)")
def _():
    hive, fc, admin, ag = world()
    admin.post("/fleet/start", {"goal": "g"})
    s, a = ag["orch"].post("/tasks", {"title": "A"})
    s, b = ag["orch"].post("/tasks", {"title": "B", "deps": [a["id"]]})
    s, d = ag["orch"].post("/decisions", {"kind": "start_approval", "question": "?"})
    admin.post(f"/decisions/{d['id']}/answer", {"answer": "approve"})
    ag["orch"].post(f"/tasks/{a['id']}/cancel", {"reason": "not needed"})
    hive.tick(force=True)
    bt = admin.get(f"/tasks/{b['id']}")[1]
    return (bt["state"] == "open" and bt["assignee"] is None,
            f"B after A cancelled: state={bt['state']} assignee={bt['assignee']}")


@probe("P23 approval decided while fleet frozen cannot be claimed; claim marks nothing "
       "(control: expected 423)")
def _():
    hive, fc, admin, ag = world()
    to_running(admin, ag)
    s, a = ag["prog1"].post("/approvals", {"command": "rm -r build", "reason": "x"})
    ag["orch"].post(f"/approvals/{a['id']}/decide", {"approve": True})
    ag["orch"].post("/decisions", {"kind": "stuck", "question": "?"})
    s, r = ag["prog1"].post(f"/approvals/{a['id']}/claim")
    return (False, f"claim while frozen -> {s} (expected behaviour; control probe)")


for name, ok, detail in results:
    tag = "REPRODUCED" if ok else ("PROBE ERROR" if ok is None else "not reproduced")
    print(f"[{tag}] {name}\n    {detail}")
