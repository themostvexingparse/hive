"""The per-machine daemon (`hive up`). SPEC §3, §8, §10, §11, §13."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from .. import globs as G
from ..config import MachineConfig, Slot, find_repo_root, hive_home, load_repo_config
from . import gitops, wake
from .client import HiveError, ServerClient
from .store import Store

log = logging.getLogger("hive.daemon")

ORCH_BATCHED = {"message.normal", "task.progress"}
COALESCE_SECONDS = 3
MIN_GAP_SECONDS = 10
MAX_WAKES_PER_HOUR = 20
MAX_STOP_BLOCKS = 3

CLAUDE_WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
GEMINI_WRITE_TOOLS = {"write_file", "replace", "edit"}
READ_ONLY_TOOLS = {"Read", "Grep", "Glob", "LS", "read_file", "read_many_files", "glob",
                   "search_file_content", "list_directory", "grep"}


class StreamSink:
    """Shows a worker's formatted output locally and uploads it (admin-only view) in batches."""

    def __init__(self, rt: "SlotRuntime", echo: bool):
        self.rt, self.echo = rt, echo
        self.buf: list[str] = []
        self.run = ""
        self.lock = threading.Lock()

    def start_run(self, run: str) -> None:
        self.run = run
        self.write([f"· wake {run}"])

    def write(self, lines: list[str]) -> None:
        if self.echo:
            for l in lines:
                print(f"[{self.rt.name}] {l}", flush=True)
        with self.lock:
            self.buf.extend(lines)

    def flush(self) -> None:
        with self.lock:
            lines, self.buf = self.buf[:500], self.buf[500:]
        if not lines:
            return
        try:
            self.rt.client.post("/agents/me/stream", {"run": self.run, "lines": lines})
        except HiveError as e:
            log.debug("[%s] stream upload failed: %s", self.rt.name, e)
            with self.lock:
                self.buf = (lines + self.buf)[-5000:]


class SlotRuntime:
    def __init__(self, cfg: Slot, server_url: str):
        self.cfg = cfg
        self.name = cfg.name
        self.client = ServerClient(server_url, cfg.token)
        self.me: dict = {}
        self.fleet: dict = {}
        self.leases: list[dict] = []
        self.proc = None
        self.running = False
        self.attached = False  # a human-facing interactive session owns this slot
        self.awaiting = False  # the interactive agent is blocked in await_events
        self.sink: StreamSink | None = None
        self.quota_until = 0.0
        self.stop_blocks = 0
        self.last_wake_end = 0.0
        self.wake_times: deque[float] = deque()
        self.lock = threading.RLock()
        self.repo_root = find_repo_root(Path(cfg.worktree)) if cfg.worktree else None
        self.repo_cfg = load_repo_config(self.repo_root)

    @property
    def roles(self) -> list[str]:
        return self.me.get("roles", [])

    @property
    def is_orchestrator(self) -> bool:
        return "orchestrator" in self.roles

    @property
    def active(self) -> bool:
        return bool(self.me.get("active"))

    @property
    def main(self) -> str:
        return self.repo_cfg.get("repo", {}).get("main", "main")


class Daemon:
    def __init__(self, mcfg: MachineConfig, store: Store | None = None, echo: bool = True):
        self.mcfg = mcfg
        self.store = store or Store(hive_home() / "state.db")
        self.slots = {s.name: SlotRuntime(s, mcfg.server_url) for s in mcfg.slots}
        for rt in self.slots.values():
            rt.sink = StreamSink(rt, echo)
        self.stop_event = threading.Event()
        self.logdir = hive_home() / "logs"

    def set_echo(self, echo: bool) -> None:
        for rt in self.slots.values():
            rt.sink.echo = echo

    # ------------------------------------------------------------------ lifecycle

    def start_background(self) -> None:
        for rt in self.slots.values():
            self.refresh(rt)
        for target in (self.poll_loop, self.heartbeat_loop, self.wake_loop, self.stream_loop):
            threading.Thread(target=target, daemon=True, name=target.__name__).start()

    def stream_loop(self) -> None:
        while not self.stop_event.is_set():
            for rt in list(self.slots.values()):
                rt.sink.flush()
            self.stop_event.wait(1)

    def stop(self) -> None:
        self.stop_event.set()

    def rt(self, slot: str) -> SlotRuntime:
        if slot not in self.slots:
            raise KeyError(f"unknown slot {slot!r}")
        return self.slots[slot]

    def refresh(self, rt: SlotRuntime) -> None:
        try:
            rt.me = rt.client.get("/agents/me")
            rt.fleet = rt.me["fleet"]
            rt.leases = rt.client.get("/leases", holder=rt.name)
        except HiveError as e:
            log.warning("[%s] refresh failed: %s", rt.name, e)

    # ------------------------------------------------------------------ loops

    def poll_loop(self) -> None:
        while not self.stop_event.is_set():
            for rt in list(self.slots.values()):
                self.poll_once(rt)
            self.stop_event.wait(self.mcfg.poll_interval)

    def poll_once(self, rt: SlotRuntime) -> None:
        try:
            since = self.store.cursor(rt.name)
            data = rt.client.get("/events", since=since, limit=200)
        except HiveError as e:
            log.debug("[%s] poll failed: %s", rt.name, e)
            return
        for ev in data["events"]:
            try:
                self.handle_event(rt, ev)
            except Exception:
                log.exception("[%s] failed to handle event %s", rt.name, ev.get("seq"))
            self.store.set_cursor(rt.name, ev["seq"])

    def heartbeat_loop(self) -> None:
        while not self.stop_event.is_set():
            for rt in list(self.slots.values()):
                try:
                    r = rt.client.post("/agents/me/heartbeat",
                                       {"busy": rt.running, "attached": rt.attached})
                    rt.fleet = r["fleet"]
                    for l in list(rt.leases):
                        if l["expires_at"] - time.time() < 300:
                            l.update(rt.client.post(f"/leases/{l['id']}/extend", {"ttl": 600}))
                except HiveError as e:
                    if e.status == 410:
                        self.refresh(rt)
                    log.debug("[%s] heartbeat failed: %s", rt.name, e)
            self.stop_event.wait(20)

    def wake_loop(self) -> None:
        while not self.stop_event.is_set():
            for rt in list(self.slots.values()):
                try:
                    self.maybe_wake(rt)
                except Exception:
                    log.exception("[%s] wake check failed", rt.name)
            self.stop_event.wait(1)

    # ------------------------------------------------------------------ events

    def trigger(self, rt: SlotRuntime, kind: str, summary: str, urgent: bool | None = None) -> None:
        if kind not in rt.me.get("wake_on", wake_on_default(rt)):
            return
        if urgent is None:
            urgent = not (rt.is_orchestrator and kind in ORCH_BATCHED)
        self.store.add_trigger(rt.name, kind, urgent, summary)

    def handle_event(self, rt: SlotRuntime, ev: dict) -> None:
        t, p, actor = ev["type"], ev["payload"], ev["actor"]
        me = rt.name
        if t == "message.posted":
            m = p["message"]
            if m["author"] == me:
                return
            relevant = (m.get("recipient") == me or me in m.get("mentions", [])
                        or m.get("channel") == "#general"
                        or (m.get("channel") == "#user" and rt.is_orchestrator)
                        or m["priority"] == "interrupt"
                        or (m.get("channel") or "").startswith("#task-"))
            self.store.add_message(me, m, relevant)
            if not relevant:
                return
            who = f"{m['author']} in {m.get('channel') or 'DM'}"
            if m.get("channel") == "#user" and m["author"] == "user":
                self.trigger(rt, "user.message", f"The user wrote in #user (msg {m['id']})")
            elif m["priority"] == "interrupt":
                self.trigger(rt, "message.interrupt", f"INTERRUPT from {who}")
            elif m.get("recipient") == me or me in m.get("mentions", []):
                self.trigger(rt, "message.direct", f"Message for you from {who}")
            elif m["priority"] != "info":
                self.trigger(rt, "message.normal", f"New message from {who}")
            return
        if t in ("fleet.state", "agent.roles_changed", "config.updated", "orchestrator.failover",
                 "fleet.started"):
            self.refresh(rt)
        if t in ("lease.acquired", "lease.released", "lease.expired", "lease.revoked") \
                and p.get("holder") == me:
            self.refresh(rt)
            if t in ("lease.expired", "lease.revoked"):
                self.store.add_notice(me, f"Your lease #{p['id']} on {p['globs']} was "
                                      f"{t.split('.')[1]} ({p.get('reason', '')}). Re-acquire "
                                      "before editing those paths.", "high")
            return
        if t == "task.assigned":
            if p["assignee"] == me:
                self.trigger(rt, "task.assigned", f"Task #{p['id']} was assigned to you: "
                             f"{p['title']}")
                if rt.running:
                    self.store.add_notice(me, f"Task #{p['id']} was assigned to you: "
                                          f"{p['title']}", "high")
            return
        if t == "review.requested":
            if p.get("reviewer") == me:
                self.trigger(rt, "review.requested",
                             f"Review requested for task #{p['id']} ({p['title']}) by "
                             f"{p['assignee']}: inspect `git diff origin/{rt.main}...{p['sha']}` "
                             f"(branch {p.get('branch')}) and call review_verdict.")
            return
        if t == "review.verdict":
            if p.get("assignee") == me:
                if p["approve"]:
                    threading.Thread(target=self.auto_release, args=(rt, p["id"]),
                                     daemon=True).start()
                else:
                    self.store.add_notice(me, f"Review of task #{p['id']} requested changes: "
                                          f"{p['notes']}", "high")
                    self.trigger(rt, "review.verdict", f"Review of task #{p['id']} requested "
                                 f"changes: {p['notes'][:500]}")
                return
            if rt.is_orchestrator:
                self.trigger(rt, "review.verdict", f"Review verdict on task #{p['id']}: "
                             f"{'approved' if p['approve'] else 'changes requested'}")
            return
        if t == "fleet.frozen":
            self.store.add_notice(me, "The fleet is FROZEN while the user answers decision "
                                  f"#{p.get('decision_id')}. Stop working and end your turn; you "
                                  "will be resumed.", "interrupt")
            return
        if t == "fleet.unfrozen":
            note = f" User answer: {p.get('answer')!r}. {p.get('note') or ''}" \
                if p.get("answer") else ""
            if self.my_open_tasks(rt):
                self.trigger(rt, "fleet.unfrozen", "The fleet was unfrozen; resume your task."
                             + note)
            return
        if t == "main.advanced":
            if p.get("by") != me:
                self.sync_worktree(rt, p)
            return
        if t == "report.requested":
            self.trigger(rt, "report.requested",
                         f"{p['by']} requests a report (request #{p['id']}"
                         f"{', task #' + str(p['task_id']) if p.get('task_id') else ''}): "
                         f"{p['about'][:1500]} — answer with report_submit(request_id="
                         f"{p['id']}, ...).")
            return
        if t == "decision.answered":
            if rt.is_orchestrator:
                self.trigger(rt, t, f"The user answered decision #{p['id']} ({p['kind']}): "
                             f"{p['answer']!r}. Note: {p.get('note') or '-'}")
            return
        if rt.is_orchestrator:
            self.trigger(rt, t, summarize_event(ev))

    def my_open_tasks(self, rt: SlotRuntime) -> list[dict]:
        try:
            return [t for t in rt.client.get("/tasks", assignee=rt.name)
                    if t["state"] in ("claimed", "in_progress", "blocked", "review")]
        except HiveError:
            return []

    def sync_worktree(self, rt: SlotRuntime, p: dict) -> None:
        if not rt.cfg.worktree:
            return
        with rt.lock:
            try:
                status = gitops.sync_to_main(rt.cfg.worktree, rt.main)
            except gitops.GitError as e:
                status = f"error: {e}"
        if status != "rebased":
            self.store.add_notice(rt.name, f"main advanced to {p['sha'][:10]} by {p['by']} "
                                  f"(changed: {', '.join(p.get('paths', [])[:15])}). Your "
                                  f"worktree was not rebased ({status}); it will be rebased "
                                  "when you call lease_release.", "normal")
        log.info("[%s] main advanced to %s: %s", rt.name, p["sha"][:10], status)

    def auto_release(self, rt: SlotRuntime, task_id: int) -> None:
        msg = self.store.get(rt.name, "last_commit_message", "hive: integrate reviewed work")
        res = self.release(rt, msg, task_id)
        self.store.add_notice(rt.name, f"Review of task #{task_id} approved; integration "
                              f"result: {res['status']}: {res['message']}", "high")
        if res["status"] == "integrated":
            self.trigger(rt, "review.verdict", f"Task #{task_id} was approved and integrated.")
        else:
            self.trigger(rt, "review.verdict", f"Task #{task_id} was approved but integration "
                         f"needs you: {res['message'][:500]}")

    # ------------------------------------------------------------------ wakes

    def maybe_wake(self, rt: SlotRuntime) -> None:
        if rt.running or rt.attached or not rt.active or rt.quota_until > time.time():
            return
        trig = self.store.pending_triggers(rt.name)
        now = time.time()
        if not trig:
            if rt.is_orchestrator:
                hb = float(rt.repo_cfg["orchestrator"].get("heartbeat_hours", 4)) * 3600
                last = self.store.get(rt.name, "last_wake", now)
                if last == now:
                    self.store.put(rt.name, "last_wake", now)
                elif now - last > hb and rt.fleet.get("state") == "running":
                    self.store.add_trigger(rt.name, "heartbeat", True,
                                           "Periodic check-in: review progress and publish "
                                           "status.")
            return
        if now - rt.last_wake_end < MIN_GAP_SECONDS:
            return
        urgent = [t for t in trig if t["urgent"]]
        if urgent:
            if now - urgent[0]["ts"] < COALESCE_SECONDS:
                return
        else:
            debounce = float(rt.repo_cfg["orchestrator"].get("debounce_seconds", 90))
            if now - trig[0]["ts"] < debounce:
                return
        while rt.wake_times and now - rt.wake_times[0] > 3600:
            rt.wake_times.popleft()
        if len(rt.wake_times) >= MAX_WAKES_PER_HOUR:
            return
        rt.running = True
        threading.Thread(target=self.run_wake, args=(rt, trig), daemon=True,
                         name=f"wake-{rt.name}").start()

    def run_wake(self, rt: SlotRuntime, trig: list[dict]) -> None:
        try:
            rt.wake_times.append(time.time())
            rt.stop_blocks = 0
            self.refresh(rt)
            session = self.store.get(rt.name, "session", None)
            wakes = self.store.get(rt.name, "wakes_in_session", 0)
            if not session or wakes >= wake.SESSION_ROTATE_AFTER:
                session, wakes, resume = str(uuid.uuid4()), 0, False
            else:
                resume = True
            msgs = self.store.undelivered(rt.name, "normal")
            prompt = wake.build_prompt(rt.name, rt.me, rt.fleet, trig, msgs,
                                       self.my_open_tasks(rt), first_in_session=not resume)
            self.store.mark_injected(rt.name, [m["id"] for m in msgs])
            cmd = wake.build_command(rt.cfg, session, resume)
            env = wake.slot_env(rt.cfg, self.mcfg.local_url, self.mcfg.local_secret)
            wake.ensure_gemini_extension(rt.cfg, env)
            log.info("[%s] wake (%d triggers, resume=%s)", rt.name, len(trig), resume)
            self.store.consume_triggers(rt.name, max(t["id"] for t in trig))
            rt.sink.start_run(time.strftime("%Y%m%d-%H%M%S"))
            rt.sink.write([f"· why: {t['summary'][:200]}" for t in trig])

            def started(proc):
                rt.proc = proc

            res = wake.run_cli(rt.cfg, cmd, prompt, env, self.logdir / f"{rt.name}.log",
                               on_start=started, on_lines=rt.sink.write)
            self.store.put(rt.name, "session", session)
            self.store.put(rt.name, "wakes_in_session", wakes + 1)
            self.store.put(rt.name, "last_wake", time.time())
            if res.quota_hit:
                rt.sink.write(["· usage limit reached; pausing this agent for an hour"])
                rt.quota_until = time.time() + 3600
                try:
                    rt.client.post("/agents/me/quota", {"until": rt.quota_until,
                                                        "detail": res.output[-500:]})
                except HiveError:
                    pass
                self.store.add_trigger(rt.name, "quota.retry", True,
                                       "Retrying after your usage limit reset.")
            elif res.returncode != 0 and resume:
                # A broken session should not wedge the slot: start fresh next time.
                self.store.put(rt.name, "session", None)
            log.info("[%s] wake finished rc=%s in %.0fs", rt.name, res.returncode, res.duration)
        except Exception:
            log.exception("[%s] wake failed", rt.name)
        finally:
            rt.proc = None
            rt.running = False
            rt.last_wake_end = time.time()

    # ------------------------------------------------------------------ hooks (SPEC §11)

    def handle_hook(self, slot: str, event: str, data: dict) -> dict:
        rt = self.rt(slot)
        state = rt.fleet.get("state", "setup")
        if event == "pre_tool":
            return self._pre_tool(rt, state, data)
        if event == "post_tool":
            msgs = self.store.undelivered(slot, "high")
            if msgs:
                self.store.mark_injected(slot, [m["id"] for m in msgs])
                return {"context": "New urgent Hive messages:\n" +
                        "\n".join(wake.fmt_message(m) for m in msgs) +
                        ("\nAn INTERRUPT means: stop your current step and handle it first."
                         if any(m["priority"] == "interrupt" for m in msgs) else "")}
            return {}
        if event in ("prompt", "session_start"):
            rt.stop_blocks = 0
            msgs = self.store.undelivered(slot, "normal")
            self.store.mark_injected(slot, [m["id"] for m in msgs])
            parts = [f"[Hive] You are `{slot}` (roles: {', '.join(rt.roles)}); fleet state: "
                     f"{state}."]
            if event == "session_start":
                parts.append(f"Follow the "
                             f"{'hive-orchestrator' if rt.is_orchestrator else 'hive-worker'} "
                             "skill.")
                if rt.me.get("role_prompt"):
                    parts.append(rt.me["role_prompt"])
            trig = self.store.pending_triggers(slot) if rt.attached else []
            if trig:  # interactive session: deliver queued events with the user's prompt
                self.store.consume_triggers(slot, max(t["id"] for t in trig))
                parts.append("Hive events since you last checked:\n" +
                             "\n".join(f"- {t['summary']}" for t in trig))
            if msgs:
                parts.append("Unread Hive messages:\n" +
                             "\n".join(wake.fmt_message(m) for m in msgs))
            return {"context": "\n\n".join(parts)}
        if event == "stop":
            if state in ("frozen", "paused") and not rt.is_orchestrator:
                return {}
            if rt.stop_blocks >= MAX_STOP_BLOCKS:
                return {}
            reasons = []
            if rt.attached and any(t["urgent"] for t in self.store.pending_triggers(slot)):
                reasons.append("Hive events are waiting for you; call await_events to receive "
                               "them (it returns immediately).")
            pending = self.store.undelivered(slot, "high")
            if pending:
                self.store.mark_injected(slot, [m["id"] for m in pending])
                reasons.append("You have unhandled urgent messages:\n" +
                               "\n".join(wake.fmt_message(m) for m in pending))
            elif self.store.unacked_urgent(slot):
                reasons.append(f"Acknowledge handled urgent messages with ack("
                               f"{self.store.unacked_urgent(slot)}).")
            if rt.leases and rt.cfg.worktree:
                try:
                    dirty = [p for p in gitops.changed_paths(rt.cfg.worktree)
                             if G.covered(p, [g for l in rt.leases for g in l["globs"]])]
                except gitops.GitError:
                    dirty = []
                if dirty:
                    reasons.append(f"You hold leases with uncommitted changes ({dirty[:10]}). "
                                   "Finish and call lease_release(commit_message), or explain "
                                   "in task_update why the work is paused.")
            if reasons:
                rt.stop_blocks += 1
                return {"block": "\n\n".join(reasons)}
            return {}
        return {}

    def _pre_tool(self, rt: SlotRuntime, state: str, data: dict) -> dict:
        tool = data.get("tool_name", "")
        if is_hive_tool(tool):
            return {}
        if not rt.active:
            if tool in READ_ONLY_TOOLS:
                return {}
            return {"deny": f"Hive fleet is {state}; your roles ({', '.join(rt.roles)}) must "
                            "not act now. End your turn; you will be woken when it is your turn."}
        if tool in CLAUDE_WRITE_TOOLS or tool in GEMINI_WRITE_TOOLS:
            ti = data.get("tool_input") or {}
            raw = ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""
            if not raw:
                return {}
            rel = self._relpath(rt, raw)
            if rel is None:
                return {}
            if rel is False:
                return {"deny": f"{raw} is inside another agent's worktree."}
            allowed = list(rt.me.get("write_globs", []))
            if rt.me.get("can_code"):
                allowed += [g for l in rt.leases for g in l["globs"]]
            if not G.covered(rel, allowed):
                if not rt.me.get("can_code"):
                    return {"deny": f"Your roles ({', '.join(rt.roles)}) may not edit {rel}. "
                                    f"You may only write {allowed or 'nothing'}; ask a "
                                    "programmer via a task instead."}
                return {"deny": f"No lease covers {rel}. Call lease_acquire(globs=[...]) for all "
                                "paths you need first (e.g. the file or its directory/**)."}
        return {}

    def _relpath(self, rt: SlotRuntime, raw: str):
        """Repo-relative path; None if outside every worktree; False if in another slot's."""
        p = Path(raw)
        if not p.is_absolute():
            p = Path(rt.cfg.worktree) / p
        p = p.resolve()
        for other in self.slots.values():
            if other is rt or not other.cfg.worktree:
                continue
            try:
                p.relative_to(Path(other.cfg.worktree).resolve())
                return False
            except ValueError:
                pass
        try:
            return p.relative_to(Path(rt.cfg.worktree).resolve()).as_posix()
        except ValueError:
            return None

    # ------------------------------------------------------------------ interactive orchestrator

    def await_events(self, slot: str, timeout: float = 600) -> dict:
        """Block until events are due for an interactive session, then return a digest.

        Urgent events return almost immediately; batched ones (progress, ordinary messages)
        after the configured debounce, so the waiting session costs no tokens in between.
        """
        rt = self.rt(slot)
        timeout = max(5.0, min(float(timeout), 1800.0))
        debounce = float(rt.repo_cfg["orchestrator"].get("debounce_seconds", 90))
        end = time.time() + timeout
        rt.awaiting = True
        try:
            while not self.stop_event.is_set():
                now = time.time()
                trig = self.store.pending_triggers(slot)
                urgent = [t for t in trig if t["urgent"]]
                due = (urgent and now - urgent[0]["ts"] >= COALESCE_SECONDS) or \
                      (trig and now - trig[0]["ts"] >= debounce) or \
                      (trig and now >= end)
                if due:
                    self.store.consume_triggers(slot, max(t["id"] for t in trig))
                    msgs = self.store.undelivered(slot, "normal")
                    self.store.mark_injected(slot, [m["id"] for m in msgs])
                    return {"events": len(trig),
                            "digest": wake.digest(rt.fleet, trig, msgs)}
                if now >= end:
                    return {"events": 0, "digest": "No new events. Call await_events again "
                                                   "to keep waiting."}
                self.stop_event.wait(0.5)
            return {"events": 0, "digest": "Hive daemon is shutting down."}
        finally:
            rt.awaiting = False

    # ------------------------------------------------------------------ tools (SPEC §15)

    def release(self, rt: SlotRuntime, commit_message: str, task_id: int | None) -> dict:
        with rt.lock:
            self.refresh(rt)
            if not rt.leases:
                return {"status": "nothing", "message": "You hold no leases."}
            self.store.put(rt.name, "last_commit_message", commit_message)
            checks = rt.repo_cfg.get("integration", {}).get("checks", [])
            res = gitops.release_transaction(
                rt.cfg.worktree, rt.client, rt.name, rt.leases, commit_message, task_id,
                rt.me.get("write_globs", []), checks, rt.main)
            self.refresh(rt)
            return res.as_dict()

    def call_tool(self, slot: str, name: str, a: dict):
        rt = self.rt(slot)
        if name not in HIVE_TOOL_NAMES:
            raise HiveError(404, "no_such_tool", f"unknown tool {name}")
        if rt.me.get("tools") and name not in rt.me["tools"]:
            raise HiveError(403, "forbidden", f"your roles {rt.roles} do not permit {name}")
        c = rt.client
        if name == "whoami":
            self.refresh(rt)
            return rt.me
        if name == "roster":
            return c.get("/agents")
        if name == "post":
            return c.post("/messages", {"body": a["body"], "channel": a.get("channel") or
                                        "#general", "priority": a.get("priority", "normal"),
                                        "thread_id": a.get("thread_id")})
        if name == "dm":
            return c.post("/messages", {"body": a["body"], "recipient": a["to"],
                                        "priority": a.get("priority", "normal")})
        if name == "inbox":
            msgs = self.store.recent(slot, int(a.get("limit", 30)),
                                     bool(a.get("include_other_channels", False)))
            self.store.mark_injected(slot, [m["id"] for m in msgs])
            return msgs
        if name == "ack":
            ids = [int(i) for i in a.get("ids", [])]
            self.store.mark_acked(slot, ids)
            pos = [i for i in ids if i > 0]
            return c.post("/messages/ack", {"ids": pos}) if pos else {"acked": ids}
        if name == "task_create":
            return c.post("/tasks", {k: a[k] for k in ("title", "spec", "kind", "deps",
                                                        "path_hints", "priority", "assignee")
                                     if a.get(k) is not None})
        if name == "task_list":
            return c.get("/tasks", state=a.get("state"), assignee=a.get("assignee"))
        if name == "task_get":
            return c.get(f"/tasks/{int(a['task_id'])}")
        if name == "task_claim":
            return c.post(f"/tasks/{int(a['task_id'])}/claim")
        if name == "task_update":
            return c.patch(f"/tasks/{int(a['task_id'])}", {"state": a.get("state"),
                                                          "note": a.get("note", "")})
        if name == "task_handoff":
            return c.post(f"/tasks/{int(a['task_id'])}/handoff", {"to": a["to"],
                                                                   "notes": a.get("notes", "")})
        if name == "task_complete":
            return c.post(f"/tasks/{int(a['task_id'])}/complete", {"result": a.get("result", "")})
        if name == "task_cancel":
            return c.post(f"/tasks/{int(a['task_id'])}/cancel", {"reason": a.get("reason", "")})
        if name == "task_assign":
            return c.post(f"/tasks/{int(a['task_id'])}/assign", {"to": a["to"]})
        if name == "report_stuck":
            return c.post("/stuck", {"task_id": a.get("task_id"), "details": a["details"]})
        if name == "lease_acquire":
            r = c.post("/leases", {"globs": a["globs"], "reason": a.get("reason", ""),
                                   "ttl": int(a.get("ttl", 600)), "task_id": a.get("task_id")})
            self.refresh(rt)
            return r
        if name == "lease_status":
            return {"mine": c.get("/leases", holder=slot), "all": c.get("/leases")}
        if name == "lease_extend":
            return c.post(f"/leases/{int(a['lease_id'])}/extend", {"ttl": int(a.get("ttl", 600))})
        if name == "lease_release":
            if a.get("abandon"):
                for l in list(rt.leases):
                    c.post(f"/leases/{l['id']}/release")
                self.refresh(rt)
                return {"status": "abandoned", "message": "Leases released without integrating."}
            return self.release(rt, a.get("commit_message", ""), a.get("task_id"))
        if name == "lease_force_release":
            return c.post(f"/leases/{int(a['lease_id'])}/force-release",
                          {"reason": a.get("reason", "")})
        if name == "repo_state":
            wt = rt.cfg.worktree
            gitops.git(wt, "fetch", "origin", check=False)
            return {"branch": gitops.git(wt, "rev-parse", "--abbrev-ref", "HEAD"),
                    "head": gitops.head(wt),
                    "main": gitops.git(wt, "rev-parse", f"origin/{rt.main}", check=False),
                    "behind_main": gitops.git(wt, "rev-list", "--count",
                                              f"HEAD..origin/{rt.main}", check=False),
                    "ahead_of_main": gitops.git(wt, "rev-list", "--count",
                                                f"origin/{rt.main}..HEAD", check=False),
                    "changed": gitops.changed_paths(wt)}
        if name == "status_publish":
            return c.post("/status", {"summary": a["summary"], "body": a.get("body", ""),
                                      "level": a.get("level", "fyi")})
        if name == "status_read":
            return c.get("/status", limit=int(a.get("limit", 10)))
        if name == "decision_request":
            return c.post("/decisions", {k: a.get(k) for k in ("kind", "question", "options",
                                                               "context", "task_id", "freeze")
                                         if a.get(k) is not None})
        if name == "fleet_state":
            return c.get("/fleet")
        if name == "fleet_control":
            return c.post(f"/fleet/{a['action']}")
        if name == "review_verdict":
            return c.post(f"/tasks/{int(a['task_id'])}/review",
                          {"approve": bool(a["approve"]), "notes": a.get("notes", "")})
        if name == "role_reassign":
            return c.patch(f"/agents/{a['agent']}", {"roles": a["roles"]})
        if name == "await_events":
            return self.await_events(slot, float(a.get("timeout_seconds", 600)))
        if name == "report_request":
            return c.post("/reports/requests", {"target": a["agent"], "about": a["about"],
                                                "task_id": a.get("task_id")})
        if name == "report_submit":
            return c.post("/reports", {"summary": a["summary"], "body": a.get("body", ""),
                                       "task_id": a.get("task_id"),
                                       "request_id": a.get("request_id")})
        if name == "report_list":
            return c.get("/reports", kind=a.get("kind"), author=a.get("author"),
                         target=a.get("target"), state=a.get("state"),
                         limit=a.get("limit", 20))
        if name == "report_get":
            return c.get(f"/reports/{int(a['report_id'])}")
        raise HiveError(404, "no_such_tool", name)


HIVE_TOOL_NAMES = {
    "whoami", "roster", "post", "dm", "inbox", "ack", "task_create", "task_list", "task_get",
    "task_claim", "task_update", "task_handoff", "task_complete", "task_cancel", "task_assign",
    "report_stuck", "lease_acquire", "lease_status", "lease_extend", "lease_release",
    "lease_force_release", "repo_state", "status_publish", "status_read", "decision_request",
    "fleet_state", "fleet_control", "review_verdict", "role_reassign", "await_events",
    "report_request", "report_submit", "report_list", "report_get",
}


def is_hive_tool(tool: str) -> bool:
    """Matches the hive MCP tools under any CLI naming: mcp__hive__x (project config),
    mcp__plugin_hive_hive__x (Claude plugin), or bare/prefixed names (Gemini)."""
    if tool in HIVE_TOOL_NAMES:
        return True
    head, _, name = tool.rpartition("__")
    return name in HIVE_TOOL_NAMES and "hive" in head


def wake_on_default(rt: SlotRuntime) -> set[str]:
    from ..roles import BUILTIN_ROLES
    out: set[str] = set()
    for r in rt.roles:
        if r in BUILTIN_ROLES:
            out |= set(BUILTIN_ROLES[r].wake_on)
    return out


def summarize_event(ev: dict) -> str:
    t, p = ev["type"], ev["payload"]
    if t == "task.completed":
        return f"Task #{p['id']} ({p['title']}) completed by {p.get('assignee')}: " \
               f"{p.get('result', '')[:300]}"
    if t in ("task.blocked", "task.failed", "task.progress"):
        return f"Task #{p['id']} ({p['title']}) {t.split('.')[1]} — {ev['actor']}: " \
               f"{p.get('note', '')[:300]}"
    if t == "agent.stuck":
        return f"{p['name']} is STUCK on task #{p.get('task_id')}: {p['details'][:800]}"
    if t == "agent.quota_exhausted":
        return f"{p['name']} is out of quota until {time.strftime('%H:%M', time.localtime(p['until']))}"
    if t == "agent.offline":
        return f"{p['name']} went offline"
    if t == "lease.expired":
        return f"Lease #{p['id']} of {p['holder']} on {p['globs']} expired"
    if t == "merge.conflict":
        return f"{p['name']} hit a merge conflict on task #{p.get('task_id')}: " \
               f"{p['detail'][:500]}"
    if t == "fleet.started":
        return f"The user started the fleet with goal: {p.get('goal', '')[:1000]}"
    if t == "orchestrator.failover":
        return f"Orchestrator failover: {p['old']} → {p['new']}"
    if t == "report.submitted":
        return f"{p['author']} filed report #{p['id']}" + \
            (f" (task #{p['task_id']})" if p.get("task_id") else "") + \
            f": {p['summary'][:300]} — read it with report_get({p['id']}) and cross-check " \
            "claims against the repository."
    return f"{t} by {ev['actor']}: {str(p)[:300]}"
