"""The per-machine daemon (`hive up`). SPEC §3, §8, §10, §11, §13."""

from __future__ import annotations

import logging
import re
import shlex
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from .. import globs as G
from .. import permissions as P
from .. import prompts
from .. import ui
from ..permissions import role_class
from ..config import MachineConfig, Slot, find_repo_root, hive_home, load_repo_config
from . import gitops, wake
from .client import HiveError, ServerClient
from .shells import default_shell, resolve_shell, run_shell, tool_shell
from .store import Store

log = logging.getLogger("hive.daemon")

# Routine events reach the orchestrator together after `debounce_seconds` instead of one batch
# each: every batch re-reads its whole context. Failures, stuck agents, user messages,
# decisions and approval requests stay urgent.
ORCH_BATCHED = {"message.normal", "task.progress", "task.completed", "report.submitted",
                "review.verdict"}
UNFINISHED_TASK_STATES = "open,claimed,in_progress,review,blocked"
FINISH_FIELDS = ("result", "report_summary", "report_body")
FINISH_MAX_ATTEMPTS = 20  # retried with the heartbeat (every 20 s): several minutes of outage
TASK_SUMMARY_FIELDS = ("id", "state", "kind", "priority", "assignee", "title", "deps",
                       "path_hints")
ROSTER_FIELDS = ("name", "vendor", "roles", "online", "busy", "has_task", "exhausted",
                 "attached")
COALESCE_SECONDS = 3
MIN_GAP_SECONDS = 10
MAX_WAKES_PER_HOUR = 20
MAX_STOP_BLOCKS = 3
AUTO_RELEASE_MAX_WAIT = 1800  # wait at most this long for a running turn to finish

CLAUDE_WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
GEMINI_WRITE_TOOLS = {"write_file", "replace", "edit"}
CODEX_PATCH_TOOLS = {"apply_patch"}  # one patch may add, update, delete and move many files
# agy names hook tools after its step types (write_file, edit_file), its model-facing names
# differ (write_to_file, replace_file_content, ...); both are listed. The file is in `Path`.
AGY_WRITE_TOOLS = {"write_file", "edit_file", "write_to_file", "replace_file_content",
                   "multi_replace_file_content", "sed_file", "notebook_edit"}
AGY_PATH_ARGS = ("Path", "TargetFile", "AbsolutePath", "File")
READ_ONLY_TOOLS = {"Read", "Grep", "Glob", "LS", "read_file", "read_many_files", "glob",
                   "search_file_content", "list_directory", "grep", "view_file", "list_dir",
                   "grep_search", "find_by_name"}
SHELL_TOOLS = {"Bash", "PowerShell", "run_shell_command", "run_command"}
class StreamSink:
    """Shows a worker's formatted output locally and uploads it (admin-only view) in batches."""

    name_width = 0  # widest slot name on this machine, so echoed lines line up

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
            ui.out("\n".join(ui.stream_line(self.rt.name, l, name_width=self.name_width)
                              for l in lines))
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
        self.waiting_approvals: set[int] = set()  # request_approval calls blocked on a decision
        # Decisions a blocked request_approval call already consumed; its event may arrive later.
        self.consumed_approvals: deque[int] = deque(maxlen=200)
        self.sink: StreamSink | None = None
        self.quota_until = 0.0
        self.stop_blocks = 0
        self.noted = False  # the agent recorded progress (a task note) during this turn
        self.last_wake_end = 0.0
        self.wake_times: deque[float] = deque()
        self.lock = threading.RLock()
        self.releasing = False  # an automatic integration is running; don't start a wake
        self.repo_root = find_repo_root(Path(cfg.worktree)) if cfg.worktree else None
        self.repo_cfg = load_repo_config(self.repo_root)
        self._other_worktrees: tuple[float, list[Path]] = (0.0, [])

    def reload_config(self) -> None:
        """hive.toml changes (config uploads, or main advancing) take effect without restart."""
        if self.cfg.worktree:
            self.repo_root = find_repo_root(Path(self.cfg.worktree)) or self.repo_root
        self.repo_cfg = load_repo_config(self.repo_root)

    def other_worktrees(self) -> list[Path]:
        """Every other checkout of this repository (other slots, the user's own clone), which
        an agent must never write into. Cached for a minute."""
        at, paths = self._other_worktrees
        if time.time() - at < 60 or not self.cfg.worktree:
            return paths
        paths = []
        try:
            out = gitops.git(self.cfg.worktree, "worktree", "list", "--porcelain")
            own = Path(self.cfg.worktree).resolve()
            for line in out.splitlines():
                if line.startswith("worktree "):
                    p = Path(line[len("worktree "):]).resolve()
                    if p != own:
                        paths.append(p)
        except (gitops.GitError, OSError):
            pass
        self._other_worktrees = (time.time(), paths)
        return paths

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

    @property
    def rules(self) -> dict:
        try:
            return P.build_rules(self.repo_cfg)
        except ValueError:  # a broken [permissions] section must not disable the hard limits
            return P.build_rules({})


class Daemon:
    def __init__(self, mcfg: MachineConfig, store: Store | None = None, echo: bool = True):
        self.mcfg = mcfg
        self.store = store or Store(hive_home() / "state.db")
        self.slots = {s.name: SlotRuntime(s, mcfg.url_for(s)) for s in mcfg.slots}
        StreamSink.name_width = max((len(n) for n in self.slots), default=0)
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

    def refresh(self, rt: SlotRuntime) -> bool:
        try:
            rt.me = rt.client.get("/agents/me")
            rt.fleet = rt.me["fleet"]
            rt.leases = rt.client.get("/leases", holder=rt.name)
            return True
        except HiveError as e:
            log.warning("[%s] refresh failed: %s", rt.name, e)
            return False

    def wait_for_identity(self, timeout: float = 30, interval: float = 2) -> list[str]:
        """Fetch every slot's identity from the server, retrying; returns slots still unknown.
        Until this succeeds the daemon cannot tell which roles (and wake events) apply."""
        end = time.time() + timeout
        missing = [rt for rt in self.slots.values() if not rt.me]
        while True:
            missing = [rt for rt in missing if not self.refresh(rt)]
            if not missing or time.time() >= end or self.stop_event.is_set():
                return [rt.name for rt in missing]
            self.stop_event.wait(interval)

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
                    if not rt.me:  # the server was unreachable when the daemon started
                        self.refresh(rt)
                    r = rt.client.post("/agents/me/heartbeat",
                                       {"busy": rt.running, "attached": rt.attached})
                    rt.fleet = r["fleet"]
                    self.resend_confirms(rt)
                    self.retry_finishes(rt)
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
        # While the agent's identity is unknown (server unreachable so far) keep everything;
        # waking still waits until the identity is known (maybe_wake needs rt.active).
        if rt.me and kind not in rt.me.get("wake_on", wake_on_default(rt)):
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
        if t == "config.updated":
            rt.reload_config()
        if t == "agent.roles_changed" and p.get("name") == me and rt.attached:
            # Headless wakes start a fresh MCP server each time; an open interactive session
            # keeps the tool list it started with.
            self.store.add_notice(me, f"Your roles changed to {', '.join(p.get('roles', []))}. "
                                  "Exit and relaunch this session to load the matching Hive "
                                  "tools.", "high")
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
                    # The result and report left for this task describe the rejected version.
                    self.drop_finish(rt, p["id"])
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
        if t == "approval.decided":
            if p["id"] in rt.waiting_approvals or p["id"] in rt.consumed_approvals:
                return  # the blocked request_approval call picks the decision up itself
            verdict = "APPROVED" if p["approve"] else "denied"
            nxt = (f" Run it now with run_approved({p['id']})." if p["approve"] else "")
            self.trigger(rt, "approval.decided",
                         f"{p['by']} {verdict} your request #{p['id']} to run `{p['command']}`"
                         f"{': ' + p['note'] if p.get('note') else ''}.{nxt}")
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
        """The agent's unfinished tasks, the ones it can work on first: a task waiting for
        review needs nothing from it until the verdict."""
        order = {"in_progress": 0, "claimed": 1, "blocked": 2, "review": 3}
        try:
            mine = [t for t in rt.client.get("/tasks", assignee=rt.name) if t["state"] in order]
        except HiveError:
            return []
        return sorted(mine, key=lambda t: (order[t["state"]], t["id"]))

    def sync_worktree(self, rt: SlotRuntime, p: dict) -> None:
        if not rt.cfg.worktree:
            return
        with rt.lock:
            try:
                status = gitops.sync_to_main(rt.cfg.worktree, rt.main)
            except gitops.GitError as e:
                status = f"error: {e}"
        if status == "rebased":
            rt.reload_config()  # main may carry a new hive.toml
        else:
            self.store.add_notice(rt.name, f"main advanced to {p['sha'][:10]} by {p['by']} "
                                  f"(changed: {', '.join(p.get('paths', [])[:15])}). Your "
                                  f"worktree was not rebased ({status}); it will be rebased "
                                  "when you call lease_release.", "normal")
        log.info("[%s] main advanced to %s: %s", rt.name, p["sha"][:10], status)

    def auto_release(self, rt: SlotRuntime, task_id: int,
                     max_wait: float = AUTO_RELEASE_MAX_WAIT) -> None:
        # Never integrate while the agent is mid-turn: it may be editing leased files, and
        # those half-done edits would be committed. Block new wakes meanwhile.
        rt.releasing = True
        try:
            end = time.time() + max_wait
            while rt.running and time.time() < end and not self.stop_event.is_set():
                self.stop_event.wait(2)
            msg = self.store.get(rt.name, "last_commit_message",
                                 "hive: integrate reviewed work")
            res = self.release(rt, msg, task_id)
        finally:
            rt.releasing = False
        fin = res.get("finished")
        if res["status"] in ("integrated", "integrated_unconfirmed") and fin:
            # The agent left its result and report with lease_release. If both went through
            # (or Hive is still retrying one), nothing is left for it to do: it isn't woken
            # just to hear the news. finish_task wakes it itself if it had to give up.
            if fin["done"]:
                news = "the task was completed and your report filed"
            else:
                news = "Hive is still completing the task and filing your report"
            self.store.add_notice(rt.name, f"Review of task #{task_id} approved; it was "
                                  f"integrated and {news}.", "normal")
            return
        self.store.add_notice(rt.name, f"Review of task #{task_id} approved; integration "
                              f"result: {res['status']}: {res['message']}", "high")
        if res["status"] == "integrated":
            self.trigger(rt, "review.verdict", f"Task #{task_id} was approved and integrated.")
        else:
            self.trigger(rt, "review.verdict", f"Task #{task_id} was approved but integration "
                         f"needs you: {res['message'][:500]}")

    # ------------------------------------------------------------------ wakes

    def maybe_wake(self, rt: SlotRuntime) -> None:
        if rt.running or rt.releasing or rt.attached or not rt.active \
                or rt.quota_until > time.time():
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
            rt.noted = False
            self.refresh(rt)
            tasks = self.my_open_tasks(rt)
            focus = self.session_focus(rt, tasks)
            session = self.store.get(rt.name, "session", None)
            wakes = self.store.get(rt.name, "wakes_in_session", 0)
            why_fresh = self.session_rotation(rt, session, wakes, focus)
            if why_fresh:
                session, wakes, resume, seen = str(uuid.uuid4()), 0, False, {}
                log.info("[%s] fresh session (%s)", rt.name, why_fresh)
            else:
                resume = True
                seen = {int(k): v for k, v in
                        (self.store.get(rt.name, "session_seen", {}) or {}).items()}
            # A fresh session picking up a task that is already under way gets what's on disk.
            current = next((t for t in tasks if t["id"] == focus), None)
            handoff = (self.handoff_note(rt, focus) if why_fresh and current
                       and current["state"] in ("in_progress", "blocked") else "")
            msgs = self.store.undelivered(rt.name, "normal")
            prompt = wake.build_prompt(rt.name, rt.me, rt.fleet, trig, msgs, tasks,
                                       first_in_session=not resume,
                                       project_prompt=self.project_prompt(rt),
                                       seen=seen, handoff=handoff)
            cmd = wake.build_command(rt.cfg, session, resume, role_class(rt.roles))
            env = wake.slot_env(rt.cfg, self.mcfg.local_url, self.mcfg.local_secret)
            err = wake.prepare_vendor(rt.cfg, env, role_class(rt.roles))
            if err:
                log.warning("[%s] %s", rt.name, err)
                rt.sink.write([f"· {err}"])
            log.info("[%s] wake (%d triggers, resume=%s)", rt.name, len(trig), resume)
            rt.sink.start_run(time.strftime("%Y%m%d-%H%M%S"))
            rt.sink.write([f"· why: {t['summary'][:200]}" for t in trig])

            def started(proc):
                # Only now is the work handed over: if the CLI can't even start, the reasons
                # and messages stay pending and the wake is retried.
                rt.proc = proc
                self.store.consume_triggers(rt.name, max(t["id"] for t in trig))
                self.store.mark_injected(rt.name, [m["id"] for m in msgs])
                self.store.put(rt.name, "session_seen", {
                    **{str(k): v for k, v in seen.items()},
                    **{str(t["id"]): wake.notes_marker(t.get("notes") or "") for t in tasks}})
                if focus is not None:
                    self.store.put(rt.name, "session_task", focus)
                elif why_fresh:
                    self.store.put(rt.name, "session_task", None)

            res = wake.run_cli(rt.cfg, cmd, prompt, env, self.logdir / f"{rt.name}.log",
                               on_start=started, on_lines=rt.sink.write)
            if res.session_id:
                session = res.session_id
            elif rt.cfg.vendor in wake.SELF_ID_VENDORS and not resume:
                session = None  # the CLI never reported its new session: nothing to resume
            self.store.put(rt.name, "session", session)
            self.store.put(rt.name, "wakes_in_session", wakes + 1)
            self.store.put(rt.name, "last_wake", time.time())
            self.store.put(rt.name, "last_context", int(res.usage.get("context") or 0))
            if res.quota_hit:
                rt.sink.write(["· usage limit reached; pausing this agent for an hour"])
                rt.quota_until = time.time() + 3600
                # The turn may have failed before the model read its prompt: resend it all.
                self.store.put(rt.name, "session_seen", {})
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
                self.store.put(rt.name, "session_seen", {})
            log.info("[%s] wake finished rc=%s in %.0fs", rt.name, res.returncode, res.duration)
        except Exception:
            log.exception("[%s] wake failed", rt.name)
        finally:
            rt.proc = None
            rt.running = False
            rt.last_wake_end = time.time()

    def session_rotation(self, rt: SlotRuntime, session, wakes: int, focus) -> str | None:
        """Why the next headless turn should start a fresh session, or None to resume.

        Every request re-reads the whole session, so history that doesn't help the current
        work is pure cost. Workers start fresh for each new task (the wake prompt carries its
        spec, notes keep progress), and anyone starts fresh once the last turn's context grew
        past `[sessions] max_context_tokens` or after `max_wakes` turns."""
        cfg = rt.repo_cfg.get("sessions", {})
        if not session:
            return "new"
        if wakes >= int(cfg.get("max_wakes", wake.SESSION_ROTATE_AFTER)):
            return "wakes"
        limit = int(cfg.get("max_context_tokens", 120_000) or 0)
        if limit and int(self.store.get(rt.name, "last_context", 0) or 0) >= limit:
            return "context"
        if cfg.get("per_task", True) and not rt.is_orchestrator and focus is not None:
            last = self.store.get(rt.name, "session_task", None)
            if last is not None and last != focus:
                return "task"
        return None

    def session_focus(self, rt: SlotRuntime, tasks: list[dict]):
        """The task this turn is mainly about. The session's own task keeps the focus while
        the agent can still work on it, so a second task (e.g. one assigned while the first
        waits for review) doesn't flip the session back and forth; otherwise the first task
        the agent can work on."""
        last = self.store.get(rt.name, "session_task", None)
        for t in tasks:
            if t["id"] == last and t["state"] in ("in_progress", "claimed", "blocked"):
                return last
        return tasks[0]["id"] if tasks else None

    def handoff_note(self, rt: SlotRuntime, focus) -> str:
        """For a fresh session that continues a task begun in an earlier one: what's already
        on disk. The task's notes (in the prompt) say what was done and what's next."""
        if focus is None or not rt.cfg.worktree:
            return ""
        try:
            changed = gitops.changed_paths(rt.cfg.worktree)
            ahead = gitops.git(rt.cfg.worktree, "rev-list", "--count",
                               f"origin/{rt.main}..HEAD", check=False).strip()
        except gitops.GitError:
            return ""
        return (f"This is a fresh session continuing task #{focus}, begun in an earlier one. "
                f"Your task notes above are your record of it. In your worktree: "
                f"{len(changed)} uncommitted file(s)"
                + (f" ({', '.join(changed[:15])})" if changed else "")
                + f", {ahead or 0} commit(s) not yet in {rt.main}. Check them, then continue.")

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
            if event == "prompt" and not rt.running:  # an interactive turn begins
                rt.noted = False
            msgs = self.store.undelivered(slot, "normal")
            self.store.mark_injected(slot, [m["id"] for m in msgs])
            # A headless turn's wake prompt already says who the agent is, its role prompt and
            # which skill to follow; a resumed session already has them in its history.
            headless = rt.running and not rt.attached
            resumed = data.get("source") == "resume"
            parts = [] if headless else [
                f"[Hive] You are `{slot}` (roles: {', '.join(rt.roles)}); fleet state: {state}."]
            if event == "session_start" and not headless and not resumed:
                parts.append(f"Follow the "
                             f"{'hive-orchestrator' if rt.is_orchestrator else 'hive-worker'} "
                             "skill.")
                if rt.me.get("role_prompt"):
                    parts.append(rt.me["role_prompt"])
                extra = self.project_prompt(rt)
                if extra:
                    parts.append(extra)
            trig = self.store.pending_triggers(slot) if rt.attached else []
            if trig:  # interactive session: deliver queued events with the user's prompt
                self.store.consume_triggers(slot, max(t["id"] for t in trig))
                parts.append("Hive events since you last checked:\n" +
                             "\n".join(f"- {t['summary']}" for t in trig))
            if msgs:
                parts.append("Unread Hive messages:\n" +
                             "\n".join(wake.fmt_message(m) for m in msgs))
            return {"context": "\n\n".join(parts)} if parts else {}
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
                if dirty and not rt.noted:
                    reasons.append(
                        f"You hold leases with uncommitted changes ({dirty[:10]}). Finish with "
                        "lease_release(commit_message, task_id, ...), or, if the work must "
                        "pause, first record where you are and what's next with "
                        "task_update(task_id, note=...). Your next turn may start in a fresh "
                        "session that only has the task and its notes.")
            if reasons:
                rt.stop_blocks += 1
                return {"block": "\n\n".join(reasons)}
            return {}
        return {}

    def _check_shell(self, rt: SlotRuntime, command: str, shell: str) -> dict:
        """Hard limits for every agent; the approval route for workers. The orchestrator's own
        dangerous commands are left to its CLI, which asks the user (interactive session)."""
        rules = rt.rules["worker"]
        hit = P.matches(command, rules["forbidden"])
        if hit:
            return {"kind": "forbidden",
                    "deny": f"`{hit}` is never allowed for Hive agents, not even with approval. "
                            "Ask the user (via the orchestrator) to do it."}
        if P.pushes_to(command, rt.main):
            return {"kind": "forbidden",
                    "deny": f"Pushing to {rt.main} is not allowed. Integrate with "
                            "lease_release(commit_message, task_id)."}
        why = P.needs_approval(command, rules["approval"])
        if not why:
            return {}
        if rt.is_orchestrator:
            # Interactive session: let the user confirm. Covers forms the CLI's prefix rules
            # can't express, such as `Remove-Item dir -Recurse`.
            return {"kind": "ask", "ask": f"Hive: this command is dangerous ({why}). "
                                          "Confirm only if you intend it."}
        return {"kind": "approval",
                "deny": f"This command needs the orchestrator's approval ({why}). Call "
                        f"request_approval(command={command!r}, shell={shell!r}, "
                        "reason=<why it is needed and what it affects>); if approved, Hive "
                        "runs it in your worktree and returns the output."}

    def _pre_tool(self, rt: SlotRuntime, state: str, data: dict) -> dict:
        tool = data.get("tool_name", "")
        ti = data.get("tool_input") or {}
        agy = rt.cfg.vendor == "agy"
        if is_hive_tool(tool) or (agy and is_hive_mcp_call(tool, ti)):
            # agy asks before MCP calls, and a headless turn ends when nobody can answer.
            return {"allow": True} if agy else {}
        if not rt.active:
            if tool in READ_ONLY_TOOLS:
                return {}
            return {"deny": f"Hive fleet is {state}; your roles ({', '.join(rt.roles)}) must "
                            "not act now. End your turn; you will be woken when it is your turn."}
        if tool in SHELL_TOOLS:
            # Codex reports every shell as "Bash" but runs the user's shell (PowerShell on
            # Windows).
            shell = default_shell() if rt.cfg.vendor == "codex" else tool_shell(tool)
            command = command_text(ti.get("command") or ti.get("CommandLine") or "")
            verdict = self._check_shell(rt, command, shell)
            if verdict or not agy:
                return verdict
            return self._agy_shell(rt, command, shell)
        if agy and tool in AGY_WRITE_TOOLS:
            raw = next((ti[k] for k in AGY_PATH_ARGS if isinstance(ti.get(k), str)), "")
            if not raw:
                # Fail closed: a write Hive can't place would otherwise skip lease checks.
                return {"deny": f"Hive could not find which file this {tool} call writes, so "
                                "it can't check your leases. Use write_file or edit_file with "
                                "the file's path."}
            return self._check_write(rt, raw)
        if tool in CLAUDE_WRITE_TOOLS or tool in GEMINI_WRITE_TOOLS:
            raw = ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""
            return self._check_write(rt, raw) if raw else {}
        if tool in CODEX_PATCH_TOOLS:
            patch = ti.get("command") or ti.get("input") or ti.get("patch") or ""
            if isinstance(patch, list):  # argv form: ["apply_patch", "<patch>"]
                patch = "\n".join(map(str, patch))
            paths = patch_paths(str(patch))
            if not paths:
                # Fail closed: a patch Hive can't read would otherwise skip lease checks.
                return {"deny": "Hive could not find the files this patch changes, so it "
                                "can't check your leases. Use the standard apply_patch "
                                "format (*** Add File: / *** Update File: / *** Delete File:)."}
            for raw in paths:
                verdict = self._check_write(rt, raw)
                if verdict:
                    return verdict
        return {}

    def _agy_shell(self, rt: SlotRuntime, command: str, shell: str) -> dict:
        """agy: a command that passed the hard limits. In a headless turn agy refuses anything
        its rules would ask about and then ends the turn, so Hive approves the project's
        allowed commands itself and refuses the rest with what to do instead. The
        orchestrator's interactive session asks the user."""
        if P.allowed(command, rt.rules[role_class(rt.roles)]["allow"]):
            return {"allow": True}
        if rt.is_orchestrator:
            return {}
        return {"deny": "This command isn't on the project's list of commands agents may run "
                        "without asking ([permissions] in hive.toml), and nobody can confirm "
                        "it during your turn. Do it another way, or, if you need exactly this "
                        f"command, call request_approval(command={command!r}, shell={shell!r}, "
                        "reason=<why it is needed and what it affects>)."}

    def _check_write(self, rt: SlotRuntime, raw: str) -> dict:
        """Verdict for writing the file `raw` (absolute or relative to the worktree)."""
        rel = self._relpath(rt, raw)
        if rel is None:
            return {}
        if rel is False:
            return {"deny": f"{raw} is inside another checkout of this repository (another "
                            "agent's worktree or the user's own clone). Only edit files "
                            "in your own worktree."}
        if prompts.is_protected(rel):
            return {"deny": f"{rel} is one of the project's Hive files, which only the "
                            "user maintains. Agents may not edit .hive/prompts/ or "
                            ".hive/agent/."}
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

    @staticmethod
    def project_prompt(rt: SlotRuntime) -> str:
        """The project's role files, read from the agent's own checkout (.hive/prompts/)."""
        return prompts.session_addition(rt.repo_root, rt.roles, rt.repo_cfg, slot=rt.name,
                                        vendor=rt.cfg.vendor)

    def _relpath(self, rt: SlotRuntime, raw: str):
        """Repo-relative path in the agent's own worktree; None if outside every checkout of
        the repository; False if inside another checkout (another slot's worktree or the
        user's own clone), where agents must never write."""
        p = Path(raw)
        if not p.is_absolute():
            p = Path(rt.cfg.worktree) / p
        p = p.resolve()
        own = Path(rt.cfg.worktree).resolve()
        inside_own = p == own or own in p.parents
        others = [Path(o.cfg.worktree).resolve() for o in self.slots.values()
                  if o is not rt and o.cfg.worktree] + rt.other_worktrees()
        for o in others:
            if p == o or o in p.parents:
                if inside_own and (o == own or o in own.parents):
                    continue  # the clone that merely contains this agent's own worktree
                return False
        return p.relative_to(own).as_posix() if inside_own else None

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

    # ------------------------------------------------------------------ command approvals

    def request_approval(self, rt: SlotRuntime, command: str, reason: str, shell: str,
                         task_id: int | None, wait_seconds: float) -> dict:
        """Ask the orchestrator to approve a dangerous command; by default wait (without using
        tokens) for the decision and, if approved, run it straight away."""
        try:
            shell = resolve_shell(shell)
        except ValueError as e:
            raise HiveError(400, "bad_shell", str(e))
        verdict = self._check_shell(rt, command, shell)
        if verdict.get("kind") == "forbidden":
            raise HiveError(403, "forbidden_command", verdict["deny"])
        r = rt.client.post("/approvals", {"command": command, "reason": reason, "shell": shell,
                                          "task_id": task_id})
        aid = r["id"]
        end = time.time() + max(0.0, min(wait_seconds, 1500.0))
        rt.waiting_approvals.add(aid)
        state: dict = {}
        try:
            while time.time() < end and not self.stop_event.is_set():
                state = rt.client.get(f"/approvals/{aid}")
                if state["state"] != "requested":
                    break
                self.stop_event.wait(2)
            state = rt.client.get(f"/approvals/{aid}")
        finally:
            if state.get("state") not in (None, "requested"):
                rt.consumed_approvals.append(aid)  # its event must not queue another wake
            rt.waiting_approvals.discard(aid)
        if state["state"] == "approved":
            return {"approval_id": aid, "decision": "approved", "note": state.get("note"),
                    **self.run_approved(rt, aid)}
        if state["state"] == "denied":
            return {"approval_id": aid, "decision": "denied", "note": state.get("note"),
                    "message": "Not approved. Follow the note or find a safer approach."}
        return {"approval_id": aid, "decision": "pending",
                "message": "No decision yet. End your turn; you will be woken when the "
                           f"orchestrator decides. If approved, call run_approved({aid})."}

    def run_approved(self, rt: SlotRuntime, aid: int) -> dict:
        """Run an approved command exactly once, in this agent's worktree."""
        a = rt.client.post(f"/approvals/{aid}/claim")  # one-time: the server marks it used
        verdict = self._check_shell(rt, a["command"], a["shell"])
        if verdict.get("kind") == "forbidden":  # hard limits are re-checked here too
            rt.client.post(f"/approvals/{aid}/result", {"exit_code": -1,
                                                        "output": verdict["deny"]})
            raise HiveError(403, "forbidden_command", verdict["deny"])
        rt.sink.write([f"· approved #{aid}: {a['command'][:300]}"])
        with rt.lock:
            code, output = run_shell(rt.cfg.worktree, a["command"], a["shell"])
        rt.sink.write([f"· approved #{aid} exited {code}"])
        try:
            rt.client.post(f"/approvals/{aid}/result", {"exit_code": code, "output": output})
        except HiveError as e:
            log.warning("[%s] could not report result of approval #%s: %s", rt.name, aid, e)
        self.refresh(rt)
        return {"executed": True, "exit_code": code, "output": output[-8000:]}

    # ------------------------------------------------------------------ tools (SPEC §15)

    def release(self, rt: SlotRuntime, commit_message: str, task_id: int | None,
                finish: dict | None = None) -> dict:
        """The release transaction. `finish` (result, report_summary, report_body) completes
        the task and files the report as soon as the work is integrated: now, or after an
        approved review (kept until then), so the agent needs no further calls or wake."""
        if finish is None and task_id is not None:
            finish = self.get_finish(rt, task_id)
        res = self._release(rt, commit_message, task_id)
        if not finish or task_id is None:
            return res
        if res.get("status") == "review_pending":
            self.put_finish(rt, task_id, {k: finish[k] for k in FINISH_FIELDS if k in finish})
            res["message"] += (" Your result and report are kept; Hive completes the task and "
                               "files them when the review approves it. End your turn.")
        elif res.get("status") in ("integrated", "integrated_unconfirmed"):
            # Saved before the calls, so a failure (or a daemon restart) can't lose it.
            self.put_finish(rt, task_id, dict(finish, integrated=True))
            fin = res["finished"] = self.finish_task(rt, task_id)
            if not fin["done"]:
                res["message"] += (" Integrated, but " + "; ".join(fin["errors"]) + (
                    ". Hive retries in the background." if fin["retrying"] else
                    ". Do it yourself with task_complete / report_submit."))
        return res

    # A finish left with lease_release: kept until the task is completed and the report filed,
    # each step recorded as it succeeds so a retry only repeats what's missing.
    def get_finish(self, rt: SlotRuntime, task_id: int) -> dict | None:
        return (self.store.get(rt.name, "finishes", {}) or {}).get(str(task_id))

    def put_finish(self, rt: SlotRuntime, task_id: int, finish: dict) -> None:
        all_ = self.store.get(rt.name, "finishes", {}) or {}
        all_[str(task_id)] = finish
        self.store.put(rt.name, "finishes", all_)

    def drop_finish(self, rt: SlotRuntime, task_id: int) -> None:
        all_ = self.store.get(rt.name, "finishes", {}) or {}
        if all_.pop(str(task_id), None) is not None:
            self.store.put(rt.name, "finishes", all_)

    def finish_task(self, rt: SlotRuntime, task_id: int) -> dict:
        """Complete the task and file the report on the agent's behalf, doing only the steps
        not done yet. Transient failures (server unreachable, busy, integration not confirmed
        yet) keep the finish for a retry; a permanent one, or too many attempts, gives up and
        wakes the agent to finish by hand."""
        fin = self.get_finish(rt, task_id)
        if not fin:
            return {"done": True, "errors": [], "retrying": False}
        errors, permanent = [], False
        wants_report = bool(fin.get("report_summary") or fin.get("report_body"))
        steps = [("completed", f"/tasks/{task_id}/complete", {"result": fin.get("result", "")})]
        if wants_report:
            steps.append(("reported", "/reports", {
                "summary": fin.get("report_summary") or f"Task #{task_id} done",
                "body": fin.get("report_body", ""), "task_id": task_id}))
        for flag, path, body in steps:
            if fin.get(flag):
                continue
            try:
                rt.client.post(path, body)
                fin[flag] = True
            except HiveError as e:
                errors.append(f"{'task_complete' if flag == 'completed' else 'report_submit'} "
                              f"failed ({e})")
                permanent |= e.status in (400, 401, 403, 404)
        done = fin.get("completed") and (fin.get("reported") or not wants_report)
        fin["attempts"] = fin.get("attempts", 0) + 1
        give_up = not done and (permanent or fin["attempts"] >= FINISH_MAX_ATTEMPTS)
        if done or give_up:
            self.drop_finish(rt, task_id)
        else:
            self.put_finish(rt, task_id, fin)
        if give_up:
            missing = [w for w, f in (("task_complete", "completed"), ("report_submit", "reported"))
                       if not fin.get(f) and (f == "completed" or wants_report)]
            self.store.add_notice(rt.name, f"Task #{task_id} is integrated, but Hive could not "
                                  f"{' / '.join(missing)} for you: {'; '.join(errors)}. Call "
                                  f"{' and '.join(missing)} yourself.", "high")
            self.trigger(rt, "task.assigned", f"Task #{task_id} is integrated but needs you to "
                         f"call {' and '.join(missing)}.")
        return {"done": bool(done), "errors": errors, "retrying": not done and not give_up}

    def retry_finishes(self, rt: SlotRuntime) -> None:
        """Retry finishes whose task was integrated but not yet completed and reported."""
        for tid, fin in list((self.store.get(rt.name, "finishes", {}) or {}).items()):
            if fin.get("integrated"):
                self.finish_task(rt, int(tid))

    def _release(self, rt: SlotRuntime, commit_message: str, task_id: int | None) -> dict:
        with rt.lock:
            self.refresh(rt)
            if not rt.leases:
                return {"status": "nothing", "message": "You hold no leases."}
            self.store.put(rt.name, "last_commit_message", commit_message)
            checks = rt.repo_cfg.get("integration", {}).get("checks", [])
            res = gitops.release_transaction(
                rt.cfg.worktree, rt.client, rt.name, rt.leases, commit_message, task_id,
                rt.me.get("write_globs", []), checks, rt.main,
                checks_shell=rt.repo_cfg.get("integration", {}).get("shell", "native"))
            if res.pending_confirm:
                pending = self.store.get(rt.name, "pending_confirms", [])
                self.store.put(rt.name, "pending_confirms", pending + [res.pending_confirm])
            self.refresh(rt)
            return res.as_dict()

    def resend_confirms(self, rt: SlotRuntime) -> None:
        """Retry integrations whose confirmation didn't reach the server (main was pushed)."""
        pending = self.store.get(rt.name, "pending_confirms", [])
        if not pending:
            return
        left = [body for body in pending
                if not gitops.confirm(rt.client, body, delays=())]
        self.store.put(rt.name, "pending_confirms", left)
        if len(left) < len(pending):
            self.refresh(rt)

    def call_tool(self, slot: str, name: str, a: dict):
        rt = self.rt(slot)
        if name not in HIVE_TOOL_NAMES:
            raise HiveError(404, "no_such_tool", f"unknown tool {name}")
        if rt.me.get("tools") and name not in rt.me["tools"]:
            raise HiveError(403, "forbidden", f"your roles {rt.roles} do not permit {name}")
        c = rt.client
        if name == "whoami":
            self.refresh(rt)
            # The tool list, wake events and role prompt are already in the session; repeating
            # them in every whoami only costs tokens.
            return {k: rt.me.get(k) for k in ("name", "roles", "can_code", "write_globs",
                                               "active", "fleet")}
        if name == "roster":
            return [{k: a.get(k) for k in ROSTER_FIELDS} for a in c.get("/agents")]
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
            state = a.get("state") or UNFINISHED_TASK_STATES
            assignee = slot if a.get("mine") else a.get("assignee")
            rows = c.get("/tasks", state=None if state == "all" else state, assignee=assignee)
            since = int(a.get("since_id") or 0)
            rows = [t for t in rows if t["id"] > since][:max(1, int(a.get("limit") or 50))]
            return [{k: t.get(k) for k in TASK_SUMMARY_FIELDS} for t in rows]
        if name == "task_get":
            return c.get(f"/tasks/{int(a['task_id'])}")
        # Task changes answer with an acknowledgement, not the whole task (spec and notes the
        # agent already has): task_get is there for the details.
        if name == "task_claim":
            return task_ack(c.post(f"/tasks/{int(a['task_id'])}/claim"))
        # A progress note counts (for the stop hook) only once the server has saved it.
        if name == "task_update":
            r = task_ack(c.patch(f"/tasks/{int(a['task_id'])}", {"state": a.get("state"),
                                                                "note": a.get("note", "")}))
            rt.noted = rt.noted or bool(a.get("note"))
            return r
        if name == "task_handoff":
            r = task_ack(c.post(f"/tasks/{int(a['task_id'])}/handoff",
                                {"to": a["to"], "notes": a.get("notes", "")}))
            rt.noted = rt.noted or bool(a.get("notes"))
            return r
        if name == "task_complete":
            return task_ack(c.post(f"/tasks/{int(a['task_id'])}/complete",
                                   {"result": a.get("result", "")}))
        if name == "task_cancel":
            return task_ack(c.post(f"/tasks/{int(a['task_id'])}/cancel",
                                   {"reason": a.get("reason", "")}))
        if name == "task_assign":
            return task_ack(c.post(f"/tasks/{int(a['task_id'])}/assign", {"to": a["to"]}))
        if name == "report_stuck":
            r = c.post("/stuck", {"task_id": a.get("task_id"), "details": a["details"]})
            rt.noted = True
            return r
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
            finish = {k: a.get(k) for k in ("result", "report_summary", "report_body")
                      if a.get(k)}
            return self.release(rt, a.get("commit_message", ""), a.get("task_id"), finish)
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
            rows = c.get("/status", limit=int(a.get("limit", 5)))
            if a.get("full"):
                return rows
            return [{k: s.get(k) for k in ("id", "ts", "level", "summary")} for s in rows]
        if name == "decision_request":
            return c.post("/decisions", {k: a.get(k) for k in ("kind", "question", "options",
                                                               "context", "task_id", "freeze")
                                         if a.get(k) is not None})
        if name == "fleet_state":
            return c.get("/fleet")
        if name == "fleet_control":
            return c.post(f"/fleet/{a['action']}")
        if name == "review_verdict":
            return task_ack(c.post(f"/tasks/{int(a['task_id'])}/review",
                                   {"approve": bool(a["approve"]), "notes": a.get("notes", "")}))
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
            rows = c.get("/reports", kind=a.get("kind"), author=a.get("author"),
                         target=a.get("target"), state=a.get("state"),
                         limit=a.get("limit", 20))
            return [{k: r.get(k) for k in ("id", "kind", "author", "target", "task_id",
                                           "request_id", "state", "summary", "created")}
                    for r in rows]
        if name == "request_approval":
            return self.request_approval(rt, a["command"], a.get("reason", ""),
                                         a.get("shell", "auto"), a.get("task_id"),
                                         float(a.get("wait_seconds", 600)))
        if name == "run_approved":
            return self.run_approved(rt, int(a["approval_id"]))
        if name == "approval_list":
            return c.get("/approvals", state=a.get("state"), agent=a.get("agent"),
                         limit=a.get("limit", 20))
        if name == "approval_decide":
            return c.post(f"/approvals/{int(a['approval_id'])}/decide",
                          {"approve": bool(a["approve"]), "note": a.get("note", "")})
        if name == "report_get":
            return c.get(f"/reports/{int(a['report_id'])}")
        raise HiveError(404, "no_such_tool", name)


def task_ack(t: dict) -> dict:
    """What a task change returns to the agent: enough to see it worked."""
    if not isinstance(t, dict) or "id" not in t:
        return t
    return {k: t[k] for k in ("id", "state", "assignee") if k in t}


HIVE_TOOL_NAMES = {
    "whoami", "roster", "post", "dm", "inbox", "ack", "task_create", "task_list", "task_get",
    "task_claim", "task_update", "task_handoff", "task_complete", "task_cancel", "task_assign",
    "report_stuck", "lease_acquire", "lease_status", "lease_extend", "lease_release",
    "lease_force_release", "repo_state", "status_publish", "status_read", "decision_request",
    "fleet_state", "fleet_control", "review_verdict", "role_reassign", "await_events",
    "report_request", "report_submit", "report_list", "report_get", "request_approval",
    "run_approved", "approval_list", "approval_decide",
}


def is_hive_tool(tool: str) -> bool:
    """Matches the hive MCP tools under any CLI naming: mcp__hive__x (project config),
    mcp__plugin_hive_hive__x (Claude plugin), mcp_hive_x (Gemini FQN) or bare names."""
    if tool in HIVE_TOOL_NAMES:
        return True
    if tool.startswith("mcp_hive_") and tool[len("mcp_hive_"):] in HIVE_TOOL_NAMES:
        return True
    head, _, name = tool.rpartition("__")
    return name in HIVE_TOOL_NAMES and "hive" in head


def is_hive_mcp_call(tool: str, tool_input: dict) -> bool:
    """agy calls every MCP tool through `call_mcp_tool`; the plugin's server is hive_hive."""
    return (tool == "call_mcp_tool" and "hive" in str(tool_input.get("ServerName") or "")
            and tool_input.get("ToolName") in HIVE_TOOL_NAMES)


_PATCH_FILE = re.compile(
    r"^\s*\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.M)


def patch_paths(patch: str) -> list[str]:
    """Every file a Codex apply_patch touches, including both ends of a move."""
    return list(dict.fromkeys(_PATCH_FILE.findall(patch)))


def command_text(command) -> str:
    """A hook's shell command as one string; Codex may send it as an argv list."""
    if isinstance(command, (list, tuple)):
        return shlex.join(str(a) for a in command)
    return str(command)


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
    if t == "approval.requested":
        return f"{p['agent']} requests APPROVAL #{p['id']} to run `{p['command'][:500]}` " \
               f"({p['shell']}{', task #' + str(p['task_id']) if p.get('task_id') else ''}). " \
               f"Reason: {p['reason'][:800]} — decide with approval_decide({p['id']}, " \
               "approve, note); the agent is waiting."
    if t == "report.submitted":
        return f"{p['author']} filed report #{p['id']}" + \
            (f" (task #{p['task_id']})" if p.get("task_id") else "") + \
            f": {p['summary'][:300]} — read it with report_get({p['id']}) and cross-check " \
            "claims against the repository."
    return f"{t} by {ev['actor']}: {str(p)[:300]}"
