"""Token efficiency: what agents are sent and when their sessions start fresh
(docs/TOKEN_EFFICIENCY_AUDIT.md). Sizes are budgets: a change that makes them grow should be
deliberate."""

import asyncio
import json
import time

import pytest

from hive import cli, mcp_server, roles
from hive.config import MachineConfig, Slot
from hive.daemon import core, wake
from hive.daemon.client import HiveError
from hive.daemon.store import Store
from hive.server.service import ADMIN, Actor, create_hive

UNREACHABLE = "http://127.0.0.1:9"


# ------------------------------------------------------------------ helpers

def schemas_for(role_names, vendor="claude"):
    allowed = set().union(*(roles.BUILTIN_ROLES[r].tools for r in role_names))

    class D:
        slot = "p1"
        vendor = ""

        def tool(self, name, args, timeout=None):
            return {"result": {"tools": sorted(allowed)}}

    D.vendor = vendor
    old = mcp_server.LocalDaemon
    mcp_server.LocalDaemon = lambda slot=None: D()
    try:
        server = mcp_server.build_server("p1")
    finally:
        mcp_server.LocalDaemon = old
    return server, asyncio.run(server.list_tools())


TASK = {"id": 7, "title": "Add login", "spec": "S" * 1500, "kind": "code", "state": "in_progress",
        "priority": 1, "deps": [], "path_hints": ["src/auth/**"], "assignee": "s1",
        "notes": "n" * 300, "branch": "hive/s1", "reviewer": None, "created_by": "orch"}


class FakeClient:
    """Stands in for the server; records calls and answers like it."""

    def __init__(self, tasks=None):
        self.tasks = tasks or []
        self.calls = []

    def get(self, path, **params):
        self.calls.append(("GET", path, params))
        if path == "/tasks":
            rows = self.tasks
            if params.get("state"):
                rows = [t for t in rows if t["state"] in params["state"].split(",")]
            if params.get("assignee"):
                rows = [t for t in rows if t["assignee"] == params["assignee"]]
            return rows
        if path == "/reports":
            return [{"id": 1, "kind": "report", "author": "s2", "target": None, "task_id": 7,
                     "request_id": None, "state": "filed", "summary": "Did it",
                     "body": "B" * 2000, "created": 1.0}]
        if path == "/status":
            return [{"id": 3, "ts": 1.0, "author": "orch", "level": "fyi", "summary": "Going well",
                     "body": "B" * 1500}]
        if path == "/agents":
            return [{"name": "s1", "vendor": "claude", "roles": ["programmer"], "online": True,
                     "busy": False, "has_task": True, "exhausted": False, "attached": False,
                     "last_seen": 1.0, "quota_until": 0, "created": 1.0}]
        return {}

    def post(self, path, body=None):
        self.calls.append(("POST", path, body))
        if path.startswith("/tasks/"):
            return dict(TASK, state="done")
        return {"id": 99}

    def patch(self, path, body=None):
        self.calls.append(("PATCH", path, body))
        return dict(TASK)


def make_daemon(tmp_path, roles_=("programmer",), tasks=None, **cfg):
    slot = Slot(name="s1", vendor="claude", token="t", worktree=str(tmp_path))
    d = core.Daemon(MachineConfig(server_url=UNREACHABLE, slots=[slot]),
                    Store(tmp_path / "state.db"), echo=False)
    rt = d.slots["s1"]
    rt.me = {"name": "s1", "roles": list(roles_), "tools": sorted(roles.ALL_TOOLS),
             "can_code": True, "write_globs": [], "active": True, "role_prompt": "ROLE PROMPT",
             "wake_on": ["task.assigned"], "fleet": {"state": "running"}}
    rt.fleet = {"state": "running"}
    rt.client = FakeClient(tasks)
    d.refresh = lambda rt_: True
    rt.repo_cfg.setdefault("sessions", {}).update(cfg)
    return d, rt


# ------------------------------------------------------------------ tool surface

def test_tool_schemas_have_no_boilerplate_and_stay_within_budget():
    for role_names, budget in ((["programmer"], 9_000), (["orchestrator"], 12_500)):
        _, tools = schemas_for(role_names)
        listing = json.dumps([{"name": t.name, "description": t.description,
                               "input_schema": t.input_schema} for t in tools])
        assert '"title": "' not in listing  # the generator's per-field titles
        assert len(listing) <= budget, f"{role_names} tool schemas grew to {len(listing)} chars"
        for t in tools:  # every required argument is still described
            props = t.input_schema.get("properties", {})
            assert set(t.input_schema.get("required", [])) <= set(props), t.name


def test_slim_schemas_still_accept_null_and_reject_bad_values():
    server, _ = schemas_for(["programmer"])
    asyncio.run(server.call_tool("task_update", {"task_id": 3, "state": None}))
    with pytest.raises(Exception):
        asyncio.run(server.call_tool("task_update", {"task_id": 3, "state": "bogus"}))


def test_gemini_gets_schemas_without_anyof_or_type_lists():
    """Gemini's function calling rejects both forms of a nullable argument."""
    server, tools = schemas_for(["orchestrator"], vendor="gemini")
    listing = json.dumps([t.input_schema for t in tools])
    assert "anyOf" not in listing
    for t in tools:
        for name, prop in t.input_schema.get("properties", {}).items():
            assert isinstance(prop.get("type"), str), (t.name, name, prop)
            assert None not in prop.get("enum", []), (t.name, name)
    update = next(t for t in tools if t.name == "task_update").input_schema
    assert update["properties"]["state"] == {"enum": ["in_progress", "blocked", "failed"],
                                             "type": "string"}
    assert update["required"] == ["task_id"]          # optional arguments stay optional
    asyncio.run(server.call_tool("task_update", {"task_id": 3}))
    _, claude = schemas_for(["orchestrator"])         # other CLIs keep explicit nulls
    assert any(isinstance(p.get("type"), list) for t in claude
               for p in t.input_schema.get("properties", {}).values())


def test_tool_results_are_compact_json():
    assert mcp_server.compact_json({"a": [1, 2], "b": None}) == '{"a":[1,2],"b":null}'


def test_task_list_returns_unfinished_summaries(tmp_path):
    rows = [dict(TASK, id=i, state=s) for i, s in
            enumerate(["done", "open", "in_progress", "cancelled", "review"], 1)]
    d, rt = make_daemon(tmp_path, tasks=rows)
    out = d.call_tool("s1", "task_list", {})
    assert [t["id"] for t in out] == [2, 3, 5]
    assert all("spec" not in t and "notes" not in t for t in out)
    assert set(out[0]) == set(core.TASK_SUMMARY_FIELDS)
    assert [t["id"] for t in d.call_tool("s1", "task_list", {"state": "all"})] == [1, 2, 3, 4, 5]
    assert [t["id"] for t in d.call_tool("s1", "task_list", {"since_id": 2, "limit": 1})] == [3]
    d.call_tool("s1", "task_list", {"mine": True})
    assert rt.client.calls[-1][2]["assignee"] == "s1"


def test_task_list_tool_says_when_there_are_more(tmp_path):
    class D:
        slot = "p1"

        def tool(self, name, args, timeout=None):
            if name == "whoami":
                return {"result": {"tools": ["task_list"]}}
            return {"result": [{"id": i} for i in range(1, args["limit"] + 1)]}

    old = mcp_server.LocalDaemon
    mcp_server.LocalDaemon = lambda slot=None: D()
    try:
        server = mcp_server.build_server("p1")
    finally:
        mcp_server.LocalDaemon = old
    r = asyncio.run(server.call_tool("task_list", {"limit": 2}))
    text = r.content[0].text
    assert text.startswith('[{"id":1},{"id":2}]') and "since_id=2" in text


@pytest.mark.parametrize("tool,args", [
    ("task_update", {"task_id": 7, "state": "in_progress"}),
    ("task_complete", {"task_id": 7, "result": "ok"}),
    ("task_claim", {"task_id": 7}),
    ("review_verdict", {"task_id": 7, "approve": True}),
])
def test_task_changes_answer_with_an_ack(tmp_path, tool, args):
    d, _ = make_daemon(tmp_path)
    out = d.call_tool("s1", tool, args)
    assert set(out) <= {"id", "state", "assignee"} and out["id"] == 7


def test_lists_of_reports_and_status_are_summaries(tmp_path):
    d, _ = make_daemon(tmp_path, ("orchestrator",))
    rep = d.call_tool("s1", "report_list", {})
    assert rep[0]["summary"] == "Did it" and "body" not in rep[0]
    st = d.call_tool("s1", "status_read", {})
    assert st[0]["summary"] == "Going well" and "body" not in st[0]
    assert "body" in d.call_tool("s1", "status_read", {"full": True})[0]


def test_whoami_and_roster_are_slim(tmp_path):
    d, _ = make_daemon(tmp_path)
    me = d.call_tool("s1", "whoami", {})
    assert me["roles"] == ["programmer"] and me["fleet"]["state"] == "running"
    assert "tools" not in me and "role_prompt" not in me and "wake_on" not in me
    roster = d.call_tool("s1", "roster", {})
    assert set(roster[0]) == set(core.ROSTER_FIELDS)


# ------------------------------------------------------------------ sessions

def test_workers_start_a_fresh_session_for_each_new_task(tmp_path):
    d, rt = make_daemon(tmp_path)
    assert d.session_rotation(rt, None, 0, 7) == "new"
    d.store.put("s1", "session_task", 7)
    assert d.session_rotation(rt, "sess", 3, 7) is None          # same task: resume
    assert d.session_rotation(rt, "sess", 3, None) is None       # no task (a report request)
    assert d.session_rotation(rt, "sess", 3, 8) == "task"        # a new task: fresh
    rt.repo_cfg["sessions"]["per_task"] = False
    assert d.session_rotation(rt, "sess", 3, 8) is None


def test_sessions_rotate_on_context_size_and_turn_count(tmp_path):
    d, rt = make_daemon(tmp_path, max_context_tokens=50_000, max_wakes=5)
    d.store.put("s1", "session_task", 7)
    d.store.put("s1", "last_context", 49_000)
    assert d.session_rotation(rt, "sess", 1, 7) is None
    d.store.put("s1", "last_context", 51_000)
    assert d.session_rotation(rt, "sess", 1, 7) == "context"
    d.store.put("s1", "last_context", 0)
    assert d.session_rotation(rt, "sess", 5, 7) == "wakes"


def test_orchestrator_is_not_rotated_per_task(tmp_path):
    d, rt = make_daemon(tmp_path, ("orchestrator",))
    d.store.put("s1", "session_task", 7)
    assert d.session_rotation(rt, "sess", 1, 8) is None


def test_resumed_wake_prompts_send_only_whats_new(tmp_path):
    me = {"roles": ["programmer"], "role_prompt": "ROLE"}
    t = dict(TASK, notes="first note. second note.")
    first = wake.build_prompt("s1", me, {"state": "running"}, [], [], [t], True)
    assert "S" * 100 in first and "ROLE" in first
    again = wake.build_prompt("s1", me, {"state": "running"}, [], [], [t], False,
                              seen={7: wake.notes_marker("first note.")})
    assert "S" * 100 not in again and "Spec as before" in again
    assert "second note." in again and "first note." not in again
    handoff = wake.build_prompt("s1", me, {"state": "running"}, [], [], [t], True,
                                handoff="HANDOFF NOTE")
    assert handoff.index("HANDOFF NOTE") > handoff.index("Your tasks")


def test_hooks_do_not_repeat_the_wake_prompt(tmp_path):
    d, rt = make_daemon(tmp_path)
    rt.running = True                      # a headless turn is in progress
    assert d.handle_hook("s1", "prompt", {}) == {}
    assert d.handle_hook("s1", "session_start", {"source": "startup"}) == {}
    rt.running, rt.attached = False, True  # an interactive session
    resumed = d.handle_hook("s1", "session_start", {"source": "resume"})["context"]
    assert "[Hive] You are `s1`" in resumed and "ROLE PROMPT" not in resumed
    compacted = d.handle_hook("s1", "session_start", {"source": "compact"})["context"]
    assert "ROLE PROMPT" in compacted


def test_token_meter_reads_each_cli():
    m = wake.TokenMeter()
    usage = {"input_tokens": 10, "cache_read_input_tokens": 30_000,
             "cache_creation_input_tokens": 500, "output_tokens": 200}
    for _ in range(2):  # Claude repeats a message's usage for each content block
        m.feed({"type": "assistant", "message": {"id": "m1", "usage": usage, "content": []}})
    m.feed({"type": "assistant", "message": {"id": "m2", "usage": dict(usage, input_tokens=90),
                                             "content": []}})
    assert m.summary() == {"input": 61_100, "output": 400, "requests": 2, "context": 30_590}
    assert "context 31k" in m.line()
    codex = wake.TokenMeter()
    codex.feed({"type": "turn.completed", "usage": {"input_tokens": 5000, "output_tokens": 300}})
    assert codex.summary()["input"] == 5000 and codex.summary()["context"] == 0
    gem = wake.TokenMeter()
    gem.feed({"type": "result", "stats": {"input_tokens": 700, "output_tokens": 20}})
    assert gem.summary()["output"] == 20


def test_stale_orchestrator_session_starts_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("HIVE_HOME", str(tmp_path / "home"))
    (tmp_path / "hive.toml").write_text("[sessions]\norchestrator_fresh_after_hours = 1\n")
    store = Store(tmp_path / "home" / "state.db")
    store.put("orch", "session", "old-session")
    store.put("orch", "session_used", time.time() - 7200)
    seen = {}

    def fake_run(cmd, cwd=None, env=None):
        seen["cmd"] = cmd

        class P:
            returncode = 0
        return P()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(wake, "prepare_vendor", lambda *a: None)
    slot = Slot(name="orch", vendor="claude", token="t", worktree=str(tmp_path))
    cfg = MachineConfig(server_url=UNREACHABLE, slots=[slot])
    cli.run_interactive(slot, cfg, orchestrator=True)
    assert "--resume" not in seen["cmd"] and "old-session" not in seen["cmd"]
    store.put("orch", "session", "recent")
    store.put("orch", "session_used", time.time())
    cli.run_interactive(slot, cfg, orchestrator=True)
    assert "--resume" in seen["cmd"] and "recent" in seen["cmd"]


def test_ending_a_turn_with_unfinished_work_requires_a_note(tmp_path, monkeypatch):
    d, rt = make_daemon(tmp_path)
    rt.leases = [{"id": 1, "globs": ["src/auth/**"]}]
    monkeypatch.setattr(core.gitops, "changed_paths", lambda wt: ["src/auth/login.py"])
    rt.running = True
    blocked = d.handle_hook("s1", "stop", {})
    assert "task_update(task_id, note=" in blocked["block"]
    d.call_tool("s1", "task_update", {"task_id": 7, "note": "Login form done; tests next."})
    assert d.handle_hook("s1", "stop", {}) == {}
    rt.noted = False                                   # a status-only update isn't a note
    d.call_tool("s1", "task_update", {"task_id": 7, "state": "in_progress"})
    assert "block" in d.handle_hook("s1", "stop", {})


def test_default_context_limit_leaves_room_for_the_clis_own_compaction(tmp_path):
    d, rt = make_daemon(tmp_path)
    rt.repo_cfg["sessions"].pop("max_context_tokens", None)
    d.store.put("s1", "session_task", 7)
    d.store.put("s1", "last_context", 100_000)
    assert d.session_rotation(rt, "sess", 1, 7) is None
    d.store.put("s1", "last_context", 125_000)
    assert d.session_rotation(rt, "sess", 1, 7) == "context"


# ------------------------------------------------------------------ fewer turns and batches

def test_one_call_finish_completes_and_reports_after_integration(tmp_path):
    d, rt = make_daemon(tmp_path)
    d._release = lambda rt_, msg, tid: {"status": "integrated", "message": "ok"}
    res = d.release(rt, "msg", 7, {"result": "done", "report_summary": "S", "report_body": "B"})
    assert res["finished"] == {"done": True, "errors": [], "retrying": False}
    assert d.get_finish(rt, 7) is None
    posted = [(c[1], c[2]) for c in rt.client.calls if c[0] == "POST"]
    assert ("/tasks/7/complete", {"result": "done"}) in posted
    assert ("/reports", {"summary": "S", "body": "B", "task_id": 7}) in posted


def test_after_approved_review_the_task_finishes_without_waking_the_agent(tmp_path):
    d, rt = make_daemon(tmp_path)
    outcome = iter([{"status": "review_pending", "message": "in review."},
                    {"status": "integrated", "message": "ok"}])
    d._release = lambda rt_, msg, tid: next(outcome)
    first = d.release(rt, "msg", 7, {"result": "done", "report_summary": "S"})
    assert "kept" in first["message"] and not any(c[0] == "POST" for c in rt.client.calls)
    d.auto_release(rt, 7, max_wait=0)
    assert any(c[1] == "/tasks/7/complete" for c in rt.client.calls)
    assert d.store.pending_triggers("s1") == []          # nobody was woken
    assert d.get_finish(rt, 7) is None


def test_routine_events_are_batched_for_the_orchestrator(tmp_path):
    d, rt = make_daemon(tmp_path, ("orchestrator",))
    rt.me["wake_on"] = list(roles.IMMEDIATE_WAKE_EVENTS)
    d.trigger(rt, "task.completed", "Task #7 done")
    d.trigger(rt, "agent.stuck", "s2 is stuck")
    urgency = {t["kind"]: t["urgent"] for t in d.store.pending_triggers("s1")}
    assert urgency == {"task.completed": False, "agent.stuck": True}


# ------------------------------------------------------------------ audit fixes

def _fleet_with_task():
    import tempfile
    hive = create_hive(f"sqlite:///{tempfile.mkdtemp()}/t.db", tick_interval=0)
    hive.enroll(ADMIN, "orch", "claude", ["orchestrator"])
    hive.enroll(ADMIN, "p1", "claude", ["programmer"])
    o, p = Actor("agent", "orch", ["orchestrator"]), Actor("agent", "p1", ["programmer"])
    hive.heartbeat(o), hive.heartbeat(p)
    hive.start_fleet(ADMIN, "goal")
    t = hive.create_task(o, "T", spec="S" * 100)
    d = hive.request_decision(o, "start_approval", "ok?")
    hive.answer_decision(ADMIN, d["id"], "approve")
    hive.tick(force=True)
    return hive, p, t["id"]


def test_new_notes_still_arrive_once_notes_are_trimmed_at_20k():
    """Notes keep only their last 20,000 characters; past that their length stops growing,
    which used to hide every later note from a resumed session."""
    hive, p, tid = _fleet_with_task()
    for i in range(60):
        hive.update_task(p, tid, "in_progress", f"note {i} " + "x" * 400)
    seen = {tid: wake.notes_marker(hive.get_task(tid)["notes"])}
    hive.update_task(p, tid, "in_progress", "IMPORTANT NEW NOTE")
    task = hive.get_task(tid)
    assert len(task["notes"]) == 20000
    prompt = wake.build_prompt("p1", {"roles": ["programmer"]}, {"state": "running"}, [], [],
                               [task], False, seen=seen)
    assert "IMPORTANT NEW NOTE" in prompt and "Spec as before" in prompt


def test_unknown_or_lost_notes_marker_resends_notes_rather_than_dropping_them():
    t = dict(TASK, notes="alpha. beta. gamma.")
    me, fleet = {"roles": ["programmer"]}, {"state": "running"}
    for marker in (300, "text that is no longer there"):   # old format; marker trimmed away
        prompt = wake.build_prompt("s1", me, fleet, [], [], [t], False, seen={7: marker})
        assert "Notes (latest): alpha. beta. gamma." in prompt


def test_long_notes_say_they_were_cut():
    t = dict(TASK, notes="n" * 5000)
    prompt = wake.build_prompt("s1", {"roles": ["programmer"]}, {"state": "running"}, [], [],
                               [t], True)
    assert "earlier notes cut; task_get has them all" in prompt


def test_finish_keeps_what_failed_and_retries_only_that(tmp_path):
    d, rt = make_daemon(tmp_path)
    d._release = lambda rt_, msg, tid: {"status": "integrated", "message": "ok"}
    real_post, down = rt.client.post, {"reports": True}

    def post(path, body=None):
        if path == "/reports" and down["reports"]:
            raise HiveError(0, "unreachable", "network blip")
        return real_post(path, body)

    rt.client.post = post
    res = d.release(rt, "msg", 7, {"result": "done", "report_summary": "S", "report_body": "B"})
    assert res["finished"]["done"] is False and res["finished"]["retrying"] is True
    assert "retries in the background" in res["message"]
    kept = d.get_finish(rt, 7)
    assert kept["completed"] is True and not kept.get("reported")
    down["reports"] = False
    completes = sum(1 for c in rt.client.calls if c[1] == "/tasks/7/complete")
    d.retry_finishes(rt)                                   # the heartbeat's retry
    assert d.get_finish(rt, 7) is None
    assert sum(1 for c in rt.client.calls if c[1] == "/tasks/7/complete") == completes
    assert any(c[1] == "/reports" for c in rt.client.calls)


def test_finish_gives_up_on_a_permanent_error_and_wakes_the_agent(tmp_path):
    d, rt = make_daemon(tmp_path)
    d.store.put("s1", "session", None)
    d._release = lambda rt_, msg, tid: {"status": "integrated", "message": "ok"}
    real_post = rt.client.post

    def post(path, body=None):
        if path.endswith("/complete"):
            raise HiveError(403, "not_assignee", "task #7 is not assigned to you")
        return real_post(path, body)

    rt.client.post = post
    res = d.release(rt, "msg", 7, {"result": "done", "report_summary": "S"})
    assert res["finished"]["retrying"] is False and "yourself" in res["message"]
    assert d.get_finish(rt, 7) is None
    assert any("task_complete" in t["summary"] for t in d.store.pending_triggers("s1"))


def test_failed_finish_after_review_does_not_pass_as_done(tmp_path):
    d, rt = make_daemon(tmp_path)
    outcome = iter([{"status": "review_pending", "message": "in review."},
                    {"status": "integrated", "message": "ok"}])
    d._release = lambda rt_, msg, tid: next(outcome)
    d.release(rt, "msg", 7, {"result": "done", "report_summary": "S", "report_body": "B"})
    real_post = rt.client.post

    def post(path, body=None):
        if path == "/reports":
            raise HiveError(0, "unreachable", "network blip")
        return real_post(path, body)

    rt.client.post = post
    d.auto_release(rt, 7, max_wait=0)
    assert d.get_finish(rt, 7)["completed"] is True        # kept for the retry
    notices = " ".join(n["body"] for n in d.store.undelivered("s1", "info"))
    assert "still completing the task" in notices


def test_requested_changes_discard_the_kept_finish(tmp_path):
    d, rt = make_daemon(tmp_path)
    d._release = lambda rt_, msg, tid: {"status": "review_pending", "message": "in review."}
    d.release(rt, "msg", 7, {"result": "old result", "report_summary": "old report"})
    assert d.get_finish(rt, 7)
    d.handle_event(rt, {"type": "review.verdict", "actor": "rev", "seq": 1, "payload": {
        "id": 7, "assignee": "s1", "approve": False, "notes": "fix the tests"}})
    assert d.get_finish(rt, 7) is None


def test_completing_a_done_task_records_the_result_without_a_second_event():
    hive, p, tid = _fleet_with_task()
    hive.complete_task(p, tid, "first")
    events = len(hive.list_events(ADMIN, 0, 1000)["events"])
    t = hive.complete_task(p, tid, "the agent's result")
    assert t["state"] == "done" and t["result"] == "the agent's result"
    assert len(hive.list_events(ADMIN, 0, 1000)["events"]) == events


def test_a_task_waiting_for_review_does_not_take_the_session_focus(tmp_path):
    a_review = dict(TASK, id=1, state="review")
    b_claimed = dict(TASK, id=2, state="claimed")
    d, rt = make_daemon(tmp_path, tasks=[a_review, b_claimed])
    assert d.session_focus(rt, d.my_open_tasks(rt)) == 2       # the task it can work on
    d.store.put("s1", "session_task", 2)
    a_review["state"] = "in_progress"                            # A comes back from review
    b_claimed["state"] = "in_progress"
    assert d.session_focus(rt, d.my_open_tasks(rt)) == 2       # the session keeps B
    b_claimed["state"] = "review"
    assert d.session_focus(rt, d.my_open_tasks(rt)) == 1       # B done for now: A


def test_progress_note_counts_only_once_it_is_saved(tmp_path):
    d, rt = make_daemon(tmp_path)

    def failing_patch(path, body=None):
        raise HiveError(0, "unreachable", "down")

    rt.client.patch = failing_patch
    with pytest.raises(HiveError):
        d.call_tool("s1", "task_update", {"task_id": 7, "note": "where I am"})
    assert rt.noted is False
    rt.client.patch = FakeClient().patch
    d.call_tool("s1", "task_update", {"task_id": 7, "note": "where I am"})
    assert rt.noted is True
