"""Role definitions, permissions and fleet-state gating (SPEC §5, §7)."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace

# Tools every role gets.
BASE_TOOLS = frozenset({
    "whoami", "roster", "post", "dm", "inbox", "ack", "task_list", "task_get", "task_claim",
    "task_update", "task_handoff", "task_complete", "report_stuck", "fleet_state", "repo_state",
    "lease_status", "status_read", "report_submit", "report_list", "report_get",
})
LEASE_TOOLS = frozenset({"lease_acquire", "lease_extend", "lease_release"})
ORCHESTRATOR_TOOLS = frozenset({
    "task_create", "task_assign", "task_cancel", "lease_force_release", "role_reassign",
    "status_publish", "decision_request", "review_verdict", "fleet_control", "report_request",
    "await_events",
})
ALL_TOOLS = BASE_TOOLS | LEASE_TOOLS | ORCHESTRATOR_TOOLS

FLEET_STATES = ("setup", "planning", "awaiting_approval", "running", "frozen", "paused")

IMMEDIATE_WAKE_EVENTS = (
    "user.message", "decision.answered", "task.completed", "task.failed", "task.blocked",
    "agent.stuck", "review.verdict", "agent.quota_exhausted", "agent.offline", "lease.expired",
    "merge.conflict", "fleet.started", "orchestrator.failover", "report.submitted",
)


@dataclass(frozen=True)
class Role:
    name: str
    mandatory: bool = False
    min: int = 0
    max: int | None = None
    can_code: bool = False
    write_globs: tuple[str, ...] = ()
    tools: frozenset[str] = BASE_TOOLS
    task_kinds: tuple[str, ...] = ()
    fallback_to: str | None = None
    wake_on: tuple[str, ...] = ("task.assigned", "review.requested", "review.verdict",
                                "message.interrupt", "message.direct", "fleet.unfrozen",
                                "report.requested")
    prompt: str = ""
    # Fleet states in which holders of this role may work (SPEC §7 table).
    active_in: tuple[str, ...] = ("running",)


BUILTIN_ROLES: dict[str, Role] = {
    "orchestrator": Role(
        name="orchestrator", mandatory=True, min=1, max=1, can_code=False,
        write_globs=(".hive/**",),
        tools=BASE_TOOLS | ORCHESTRATOR_TOOLS,
        wake_on=IMMEDIATE_WAKE_EVENTS + ("message.direct", "message.interrupt", "task.progress",
                                         "message.normal", "task.assigned", "review.requested"),
        active_in=("planning", "awaiting_approval", "running", "frozen"),
        prompt=(
            "You are the ORCHESTRATOR of a Hive fleet. You never edit source code. You plan work, "
            "split it into tasks, assign them, monitor progress, review results when no reviewer "
            "exists, and keep the user informed. End every wake by calling status_publish with a "
            "concise progressive summary (what changed since your last update, blockers, next "
            "steps). Keep long-term notes in .hive/orchestrator/state.md and rewrite it at the end "
            "of every wake. Before any coding starts you MUST present the plan to the user with "
            "decision_request(kind='start_approval'). When an agent is stuck and you cannot resolve "
            "it yourself, call decision_request(kind='stuck') which freezes the fleet until the "
            "user answers."
        ),
    ),
    "programmer": Role(
        name="programmer", mandatory=True, min=1, can_code=True,
        tools=BASE_TOOLS | LEASE_TOOLS, task_kinds=("code", "test", "docs"),
        prompt=(
            "You are a PROGRAMMER in a Hive fleet. Work only on tasks assigned to you. Before "
            "editing any file, acquire a lease covering it with lease_acquire (request every path "
            "you need at once). When the work is complete and tests pass, call lease_release with "
            "a commit message; that commits, rebases, runs checks and integrates into main. Then "
            "call task_complete. If you cannot make progress, call report_stuck instead of "
            "guessing."
        ),
    ),
    "planner": Role(
        name="planner", write_globs=(".hive/plans/**",),
        tools=BASE_TOOLS | {"task_create"}, task_kinds=("plan",), fallback_to="orchestrator",
        active_in=("planning", "running"),
        prompt=("You are a PLANNER. Turn goals into detailed plans in .hive/plans/ and propose "
                "tasks (task_create) with clear specs, dependencies and path hints."),
    ),
    "reviewer": Role(
        name="reviewer", tools=BASE_TOOLS | {"review_verdict"}, task_kinds=("review",),
        fallback_to="orchestrator",
        prompt=("You are a REVIEWER. Inspect the diff of tasks in review (git diff main...<branch>) "
                "and call review_verdict(approve, notes). Be specific about required changes."),
    ),
    "tester": Role(
        name="tester", can_code=True, write_globs=("tests/**",),
        tools=BASE_TOOLS | LEASE_TOOLS, task_kinds=("test",), fallback_to="programmer",
        prompt="You are a TESTER. Write and run tests for completed work; report failures as tasks.",
    ),
}


def _coerce(name: str, data: dict, base: Role | None) -> Role:
    known = {f.name for f in fields(Role)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"role {name!r}: unknown fields {sorted(unknown)}")
    conv = dict(data)
    if "tools" in conv:
        conv["tools"] = ALL_TOOLS if conv["tools"] == "*" else BASE_TOOLS | frozenset(conv["tools"])
    for k in ("write_globs", "task_kinds", "wake_on", "active_in"):
        if k in conv:
            conv[k] = tuple(conv[k])
    if base is None:
        return Role(name=name, **conv)
    return replace(base, **conv)


def load_roles(overrides: dict | None = None) -> dict[str, Role]:
    roles = dict(BUILTIN_ROLES)
    for name, data in (overrides or {}).items():
        roles[name] = _coerce(name, data, roles.get(name))
    return roles


@dataclass
class RoleSet:
    """The effective permissions of one agent holding several roles."""

    roles: list[Role] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [r.name for r in self.roles]

    @property
    def tools(self) -> frozenset[str]:
        out: frozenset[str] = frozenset()
        for r in self.roles:
            out |= r.tools
        return out

    @property
    def can_code(self) -> bool:
        return any(r.can_code for r in self.roles)

    @property
    def write_globs(self) -> list[str]:
        return [g for r in self.roles for g in r.write_globs]

    @property
    def task_kinds(self) -> set[str]:
        return {k for r in self.roles for k in r.task_kinds}

    @property
    def wake_on(self) -> set[str]:
        return {w for r in self.roles for w in r.wake_on}

    def active(self, fleet_state: str) -> bool:
        return any(fleet_state in r.active_in for r in self.roles)

    @property
    def prompt(self) -> str:
        return "\n\n".join(r.prompt for r in self.roles if r.prompt)


def role_set(names: list[str], catalog: dict[str, Role]) -> RoleSet:
    missing = [n for n in names if n not in catalog]
    if missing:
        raise ValueError(f"unknown roles: {missing}")
    return RoleSet([catalog[n] for n in names])


def validate_assignment(names: list[str], catalog: dict[str, Role]) -> None:
    rs = role_set(names, catalog)
    if "orchestrator" in names and any(r.can_code for r in rs.roles):
        raise ValueError("orchestrator cannot be combined with a coding role")


def validate_fleet(agent_roles: dict[str, list[str]], catalog: dict[str, Role]) -> list[str]:
    """Return a list of problems; empty means the fleet may start (SPEC §5.3)."""
    problems = []
    counts = {name: 0 for name in catalog}
    for agent, names in agent_roles.items():
        try:
            validate_assignment(names, catalog)
        except ValueError as e:
            problems.append(f"{agent}: {e}")
            continue
        for n in names:
            counts[n] += 1
    for name, role in catalog.items():
        lo = max(role.min, 1 if role.mandatory else 0)
        if counts[name] < lo:
            problems.append(f"role {name!r} needs at least {lo} holder(s), has {counts[name]}")
        if role.max is not None and counts[name] > role.max:
            problems.append(f"role {name!r} allows at most {role.max} holder(s), has {counts[name]}")
    held = {n for n, c in counts.items() if c > 0}
    for name, role in catalog.items():
        for kind in role.task_kinds:
            if resolve_kind(kind, held, catalog) is None:
                problems.append(f"task kind {kind!r} has no holder and no working fallback")
    return problems


def resolve_kind(kind: str, held: set[str], catalog: dict[str, Role]) -> str | None:
    """Which held role handles a task kind: a direct holder first, then the fallback chain."""
    direct = [r.name for r in catalog.values() if kind in r.task_kinds]
    for name in direct:
        if name in held:
            return name
    for name in direct:
        seen = set()
        cur = catalog[name].fallback_to
        while cur and cur not in seen:
            if cur in held:
                return cur
            seen.add(cur)
            cur = catalog[cur].fallback_to if cur in catalog else None
    return None
