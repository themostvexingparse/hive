"""`hive mcp`: the stdio MCP server each agent CLI launches. SPEC §15.

Every tool forwards to the local daemon, which talks to the hive-server (or runs the git release
transaction for lease_release). Only tools permitted by the agent's roles are registered.
"""

from __future__ import annotations

import json
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .local import LocalDaemon

Priority = Literal["info", "normal", "high", "interrupt"]


def build_server(slot: str | None = None) -> MCPServer:
    d = LocalDaemon(slot)
    if not d.slot:
        raise SystemExit("hive mcp: cannot determine the slot (set HIVE_SLOT or run inside a "
                         "slot worktree)")
    try:
        allowed = set(d.tool("whoami", {})["result"]["tools"]) | {"whoami"}
    except Exception:
        allowed = None

    server = MCPServer(
        "hive",
        instructions=(f"Hive coordination tools for agent `{d.slot}`. Use them to communicate "
                      "with other agents and the user, manage tasks, and take file leases "
                      "before editing."))

    def call(name: str, **args) -> str:
        try:
            # Blocking tools wait up to their own limit, plus time to run an approved command.
            wait = float(args.get("timeout_seconds") or args.get("wait_seconds") or 0)
            r = d.tool(name, args, timeout=wait + 900)
        except Exception as e:
            raise ToolError(f"Hive daemon unreachable: {e}. Is `hive up` running?")
        if "error" in r:
            extra = {k: v for k, v in r.items() if k not in ("error", "message")}
            raise ToolError(f"{r['error']}: {r.get('message')}"
                            + (f" {json.dumps(extra)}" if extra else ""))
        return json.dumps(r["result"], indent=1, default=str)

    def tool(fn):
        if allowed is None or fn.__name__ in allowed:
            server.add_tool(fn, structured_output=False)
        return fn

    @tool
    def whoami() -> str:
        """Your agent name, roles, permitted tools, write permissions and the fleet state."""
        return call("whoami")

    @tool
    def roster() -> str:
        """All agents with roles, online/busy state and quota status."""
        return call("roster")

    @tool
    def post(body: str, channel: str = "#general", priority: Priority = "normal",
             thread_id: int | None = None) -> str:
        """Post to a channel (#general, #user to talk to the user, #task-<id>, ...).
        Mention agents with @name. Priority: info (inbox only), normal (next turn),
        high (after their next tool call), interrupt (orchestrator only)."""
        return call("post", body=body, channel=channel, priority=priority, thread_id=thread_id)

    @tool
    def dm(to: str, body: str, priority: Priority = "normal") -> str:
        """Send a direct message to one agent (or 'user')."""
        return call("dm", to=to, body=body, priority=priority)

    @tool
    def inbox(limit: int = 30, include_other_channels: bool = False) -> str:
        """Recent messages relevant to you (DMs, mentions, #general, your task channels)."""
        return call("inbox", limit=limit, include_other_channels=include_other_channels)

    @tool
    def ack(ids: list[int]) -> str:
        """Acknowledge messages you have handled (required for high/interrupt messages)."""
        return call("ack", ids=ids)

    @tool
    def task_create(title: str, spec: str = "", kind: str = "code", deps: list[int] | None = None,
                    path_hints: list[str] | None = None, priority: int = 0,
                    assignee: str | None = None) -> str:
        """Create a task. kind: plan|code|review|test|docs|<custom>. deps are task ids that must
        be done first. path_hints are globs the work will likely touch. During planning, tasks
        are drafts until the user approves the plan."""
        return call("task_create", title=title, spec=spec, kind=kind, deps=deps,
                    path_hints=path_hints, priority=priority, assignee=assignee)

    @tool
    def task_list(state: str | None = None, assignee: str | None = None) -> str:
        """List tasks, optionally filtered by state (comma-separated) and assignee."""
        return call("task_list", state=state, assignee=assignee)

    @tool
    def task_get(task_id: int) -> str:
        """Full details of one task including spec and notes."""
        return call("task_get", task_id=task_id)

    @tool
    def task_claim(task_id: int) -> str:
        """Claim an open task that your roles can handle."""
        return call("task_claim", task_id=task_id)

    @tool
    def task_update(task_id: int, note: str = "",
                    state: Literal["in_progress", "blocked", "failed"] | None = None) -> str:
        """Record progress on a task (and optionally change its state)."""
        return call("task_update", task_id=task_id, note=note, state=state)

    @tool
    def task_handoff(task_id: int, to: str, notes: str) -> str:
        """Hand a task to another agent with notes on what is done and what is next."""
        return call("task_handoff", task_id=task_id, to=to, notes=notes)

    @tool
    def task_complete(task_id: int, result: str) -> str:
        """Mark your task done with a short result summary (after lease_release integrated it)."""
        return call("task_complete", task_id=task_id, result=result)

    @tool
    def task_cancel(task_id: int, reason: str = "") -> str:
        """Cancel a task (orchestrator)."""
        return call("task_cancel", task_id=task_id, reason=reason)

    @tool
    def task_assign(task_id: int, to: str) -> str:
        """Assign or reassign a task to an agent (orchestrator)."""
        return call("task_assign", task_id=task_id, to=to)

    @tool
    def report_stuck(details: str, task_id: int | None = None) -> str:
        """Tell the orchestrator you cannot make progress; explain exactly what is missing.
        End your turn afterwards."""
        return call("report_stuck", details=details, task_id=task_id)

    @tool
    def lease_acquire(globs: list[str], reason: str = "", ttl: int = 600,
                      task_id: int | None = None) -> str:
        """Lease repo paths before editing them (all-or-nothing). Globs are repo-relative:
        'src/app.py', 'src/api/**'. A conflict lists who holds what."""
        return call("lease_acquire", globs=globs, reason=reason, ttl=ttl, task_id=task_id)

    @tool
    def lease_status() -> str:
        """Your leases and all active leases in the fleet."""
        return call("lease_status")

    @tool
    def lease_extend(lease_id: int, ttl: int = 600) -> str:
        """Extend one of your leases (the daemon also does this automatically)."""
        return call("lease_extend", lease_id=lease_id, ttl=ttl)

    @tool
    def lease_release(commit_message: str = "", task_id: int | None = None,
                      abandon: bool = False) -> str:
        """Finish work on your leased paths: commits them, rebases onto main, runs checks and
        integrates into main (or submits for review), then frees the leases. abandon=true frees
        the leases without integrating."""
        return call("lease_release", commit_message=commit_message, task_id=task_id,
                    abandon=abandon)

    @tool
    def lease_force_release(lease_id: int, reason: str) -> str:
        """Revoke another agent's lease (orchestrator)."""
        return call("lease_force_release", lease_id=lease_id, reason=reason)

    @tool
    def repo_state() -> str:
        """Your branch, how far you are behind/ahead of main, and changed files."""
        return call("repo_state")

    @tool
    def status_publish(summary: str, body: str = "",
                       level: Literal["fyi", "decision-needed", "alert"] = "fyi") -> str:
        """Publish a progress update to the user's status feed (orchestrator)."""
        return call("status_publish", summary=summary, body=body, level=level)

    @tool
    def status_read(limit: int = 10) -> str:
        """Read the latest status feed entries."""
        return call("status_read", limit=limit)

    @tool
    def decision_request(kind: Literal["start_approval", "stuck", "question"], question: str,
                         options: list[str] | None = None, context: str = "",
                         task_id: int | None = None, freeze: bool | None = None) -> str:
        """Ask the user to decide (orchestrator). start_approval: present the plan; the fleet
        starts coding only after approval. stuck: an agent is blocked; freezes all agents until
        answered. question: non-blocking unless freeze=true."""
        return call("decision_request", kind=kind, question=question, options=options,
                    context=context, task_id=task_id, freeze=freeze)

    @tool
    def fleet_state() -> str:
        """Fleet state (planning/awaiting_approval/running/frozen/paused), goal and open
        blocking decision."""
        return call("fleet_state")

    @tool
    def fleet_control(action: Literal["pause", "resume"]) -> str:
        """Pause or resume the whole fleet (orchestrator)."""
        return call("fleet_control", action=action)

    @tool
    def review_verdict(task_id: int, approve: bool, notes: str = "") -> str:
        """Approve a task in review (it is then integrated) or request changes."""
        return call("review_verdict", task_id=task_id, approve=approve, notes=notes)

    @tool
    def role_reassign(agent: str, roles: list[str]) -> str:
        """Change an agent's roles (orchestrator). Changes to mandatory roles need the user."""
        return call("role_reassign", agent=agent, roles=roles)

    @tool
    def await_events(timeout_seconds: int = 600) -> str:
        """Wait (without using tokens) until something in the fleet needs you, then return a
        digest of events and messages. Urgent events return within seconds; routine progress is
        batched. Call this whenever you have nothing else to do. The user can interrupt it."""
        return call("await_events", timeout_seconds=timeout_seconds)

    @tool
    def request_approval(command: str, reason: str,
                         shell: Literal["auto", "bash", "powershell", "cmd"] = "auto",
                         task_id: int | None = None, wait_seconds: int = 600) -> str:
        """Ask the orchestrator to approve a command that needs approval (pushes, history
        rewrites, recursive or forced deletes). Give the exact command and why it is needed and
        what it affects. shell is the shell the command is written for (auto: PowerShell on
        Windows, bash elsewhere). Waits up to wait_seconds (using no tokens) for the decision; if
        approved, Hive runs the command once in your worktree and returns its output. If no
        decision arrives in time, end your turn; you will be woken, then call run_approved."""
        return call("request_approval", command=command, reason=reason, shell=shell,
                    task_id=task_id, wait_seconds=wait_seconds)

    @tool
    def run_approved(approval_id: int) -> str:
        """Run a command the orchestrator approved (once, in your worktree) and get its
        output."""
        return call("run_approved", approval_id=approval_id)

    @tool
    def approval_list(state: str | None = None, agent: str | None = None,
                      limit: int = 20) -> str:
        """List command approval requests (state: requested, approved, denied, executed,
        expired; comma-separated)."""
        return call("approval_list", state=state, agent=agent, limit=limit)

    @tool
    def approval_decide(approval_id: int, approve: bool, note: str = "") -> str:
        """Approve or deny an agent's request to run a dangerous command (orchestrator). Approve
        only if it is needed for the agent's task and limited to its own work; prefer telling
        the agent a safer alternative in the note. Escalate to the user with decision_request
        when unsure."""
        return call("approval_decide", approval_id=approval_id, approve=approve, note=note)

    @tool
    def report_request(agent: str, about: str, task_id: int | None = None) -> str:
        """Ask an agent to compile a report (orchestrator). Be specific about what to cover;
        verify the answer against the repository afterwards."""
        return call("report_request", agent=agent, about=about, task_id=task_id)

    @tool
    def report_submit(summary: str, body: str = "", task_id: int | None = None,
                      request_id: int | None = None) -> str:
        """File a report for the orchestrator: what you did, where (files, commits), how it was
        verified, open issues. Pass request_id when answering a report request. Never include
        secrets."""
        return call("report_submit", summary=summary, body=body, task_id=task_id,
                    request_id=request_id)

    @tool
    def report_list(kind: Literal["request", "report"] | None = None, author: str | None = None,
                    target: str | None = None, state: str | None = None, limit: int = 20) -> str:
        """List reports and report requests (newest last)."""
        return call("report_list", kind=kind, author=author, target=target, state=state,
                    limit=limit)

    @tool
    def report_get(report_id: int) -> str:
        """Read one report in full."""
        return call("report_get", report_id=report_id)

    return server


def main(slot: str | None = None) -> None:
    build_server(slot).run("stdio")
