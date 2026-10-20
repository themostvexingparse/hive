"""Server business logic. Every public method runs in its own transaction.

Writers are serialized by locking the single `fleet` row (SELECT FOR UPDATE; BEGIN IMMEDIATE on
SQLite), which keeps lease grants, fencing counters and state transitions atomic without any
background worker (SPEC §18). Periodic duties run lazily in `tick()` at the start of requests.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
import re
import secrets
import threading
import time
from dataclasses import dataclass

import requests
from sqlalchemy import and_, delete, func, insert, or_, select, update

from .. import globs as G
from .. import permissions as P
from ..config import VENDORS
from ..roles import RoleSet, load_roles, resolve_kind, role_set, validate_assignment, validate_fleet
from . import db
from .archives import FORMAT as ARCHIVE_FORMAT, VERSION as ARCHIVE_VERSION, ArchiveStore, default_dir
from .db import (agents, approvals, decisions, events, fleet, integrations, leases, messages,
                 receipts, reports, status_feed, streams, tasks)

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
RESERVED = {"user", "system", "admin"}
PRIORITIES = ("info", "normal", "high", "interrupt")
TASK_STATES = ("draft", "open", "claimed", "in_progress", "review", "done", "blocked", "failed",
               "cancelled")
ACTIVE_TASK_STATES = ("claimed", "in_progress", "blocked")
MAX_BODY = 8192
RATE_PER_MIN = 20
LEASE_TTL_DEFAULT, LEASE_TTL_MAX = 600, 3600
STREAM_LINE_MAX, STREAM_BATCH_MAX, STREAM_KEEP = 4000, 500, 20000
SCHEDULE_FALLBACK_SECONDS = 15
APPROVAL_TTL = 3600  # an approved command must be run within an hour


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


@dataclass
class Actor:
    kind: str  # "agent" | "admin"
    name: str
    roles: list[str]

    @property
    def is_admin(self) -> bool:
        return self.kind == "admin"


ADMIN = Actor("admin", "user", [])


def now() -> float:
    return time.time()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _n(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def row(r) -> dict | None:
    return dict(r._mapping) if r is not None else None


class Hive:
    def __init__(self, engine, *, github_token: str = "", offline_after: float = 90,
                 failover_after: float = 300, tick_interval: float = 1.0,
                 archive_dir: str | None = None):
        self.engine = engine
        self.archives = ArchiveStore(archive_dir or default_dir(engine.url))
        self.github_token = github_token
        self.offline_after = offline_after
        self.failover_after = failover_after
        self.tick_interval = tick_interval
        self._last_tick = 0.0
        self._last_sched = 0.0
        self._tick_lock = threading.Lock()
        self.is_sqlite = engine.dialect.name == "sqlite"
        # SQLite: one writer per process at a time, so threads queue here instead of contending
        # for the file lock (which on network filesystems is slow and fragile).
        self._write_lock = threading.RLock()
        self._reader = engine.execution_options(hive_read=True)
        with self._write() as c:
            if c.execute(select(fleet.c.id)).first() is None:
                c.execute(insert(fleet).values(id=1, state="setup", since=now(), config={},
                                               fencing_counter=0, goal=""))

    # ------------------------------------------------------------------ transactions

    @contextmanager
    def _write(self):
        if self.is_sqlite:
            with self._write_lock, self.engine.begin() as c:
                yield c
        else:
            with self.engine.begin() as c:
                yield c

    def _read(self):
        """A read-only transaction: no write lock on SQLite, no row locks elsewhere."""
        return self._reader.begin()

    # ------------------------------------------------------------------ helpers

    def _fleet(self, c, lock: bool = True) -> dict:
        q = select(fleet).where(fleet.c.id == 1)
        if lock:
            q = q.with_for_update()
        return row(c.execute(q).first())

    def _catalog(self, f: dict):
        return load_roles((f.get("config") or {}).get("roles"))

    def _cfg(self, f: dict, section: str) -> dict:
        return (f.get("config") or {}).get(section, {}) or {}

    def _review_mode(self, f: dict) -> str:
        return self._cfg(f, "integration").get("review", "off")

    def _emit(self, c, type_: str, actor: str, audience: str = "*", **payload) -> int:
        r = c.execute(insert(events).values(ts=now(), type=type_, actor=actor, audience=audience,
                                            payload=payload))
        return r.inserted_primary_key[0]

    def _agent(self, c, name: str) -> dict:
        a = row(c.execute(select(agents).where(agents.c.name == name)).first())
        if a is None:
            raise ApiError(404, "no_such_agent", f"no agent named {name!r}")
        return a

    def _roleset(self, f: dict, actor: Actor) -> RoleSet:
        return role_set(actor.roles, self._catalog(f))

    def _require_tool(self, f: dict, actor: Actor, tool: str) -> None:
        if actor.is_admin:
            return
        if tool not in self._roleset(f, actor).tools:
            raise ApiError(403, "forbidden",
                           f"your roles {actor.roles} do not permit {tool!r}")

    def _require_active(self, f: dict, actor: Actor) -> None:
        if actor.is_admin:
            return
        if not self._roleset(f, actor).active(f["state"]):
            raise ApiError(423, "fleet_" + f["state"],
                           f"fleet is {f['state']}; your roles {actor.roles} must wait")

    def _task(self, c, task_id: int) -> dict:
        t = row(c.execute(select(tasks).where(tasks.c.id == task_id)).first())
        if t is None:
            raise ApiError(404, "no_such_task", f"no task #{task_id}")
        return t

    def _set_fleet(self, c, **values) -> None:
        if "state" in values:
            values.setdefault("since", now())
        c.execute(update(fleet).where(fleet.c.id == 1).values(**values))

    # ------------------------------------------------------------------ auth

    def authenticate(self, token: str) -> Actor:
        with self._read() as c:
            a = row(c.execute(select(agents).where(agents.c.token_hash == hash_token(token)))
                    .first())
        if a is None:
            raise ApiError(401, "bad_token", "unknown agent token")
        return Actor("agent", a["name"], list(a["roles"]))

    # ------------------------------------------------------------------ config & agents

    def set_config(self, actor: Actor, config: dict) -> dict:
        if not actor.is_admin:
            raise ApiError(403, "forbidden", "admin only")
        try:
            catalog = load_roles(config.get("roles"))
            P.build_rules(config)
        except (ValueError, TypeError) as e:
            raise ApiError(400, "bad_config", str(e))
        with self._write() as c:
            self._fleet(c)
            problems = []
            for a in c.execute(select(agents.c.name, agents.c.roles)):
                try:
                    validate_assignment(list(a.roles), catalog)
                except ValueError as e:
                    problems.append(f"{a.name}: {e}")
            if problems:
                raise ApiError(409, "roles_in_use", "the new config doesn't fit current agents' "
                               "roles; change their roles first: " + "; ".join(problems),
                               problems=problems)
            self._set_fleet(c, config=config)
            self._emit(c, "config.updated", actor.name)
        return {"ok": True}

    def enroll(self, actor: Actor, name: str, vendor: str, roles: list[str]) -> dict:
        if not actor.is_admin:
            raise ApiError(403, "forbidden", "admin only")
        if not NAME_RE.match(name) or name in RESERVED:
            raise ApiError(400, "bad_name", "agent names must match ^[a-z][a-z0-9-]{1,31}$")
        if vendor not in VENDORS:
            raise ApiError(400, "bad_vendor", f"vendor must be one of {', '.join(VENDORS)}")
        token = "hv_" + secrets.token_urlsafe(32)
        with self._write() as c:
            f = self._fleet(c)
            try:
                validate_assignment(roles, self._catalog(f))
            except ValueError as e:
                raise ApiError(400, "bad_roles", str(e))
            if c.execute(select(agents.c.name).where(agents.c.name == name)).first():
                raise ApiError(409, "exists", f"agent {name!r} already exists")
            c.execute(insert(agents).values(name=name, vendor=vendor, roles=roles,
                                            token_hash=hash_token(token), status="offline",
                                            attached=False, quota_until=0.0, last_seen=0.0,
                                            created=now()))
            self._emit(c, "agent.enrolled", actor.name, name=name, vendor=vendor, roles=roles)
        return {"name": name, "token": token}

    def rotate_token(self, actor: Actor, name: str) -> dict:
        if not actor.is_admin:
            raise ApiError(403, "forbidden", "admin only")
        token = "hv_" + secrets.token_urlsafe(32)
        with self._write() as c:
            self._agent(c, name)
            c.execute(update(agents).where(agents.c.name == name)
                      .values(token_hash=hash_token(token)))
        return {"name": name, "token": token}

    def roster(self) -> list[dict]:
        with self._read() as c:
            rows = c.execute(select(agents).order_by(agents.c.name)).fetchall()
            busy = {r.assignee for r in c.execute(
                select(tasks.c.assignee).where(tasks.c.state.in_(ACTIVE_TASK_STATES)))}
        t = now()
        out = []
        for r in rows:
            d = row(r)
            d.pop("token_hash")
            d["online"] = t - d["last_seen"] < self.offline_after
            d["exhausted"] = d["quota_until"] > t
            d["has_task"] = d["name"] in busy
            out.append(d)
        return out

    def whoami(self, actor: Actor) -> dict:
        with self._read() as c:
            f = self._fleet(c, lock=False)
        rs = self._roleset(f, actor) if not actor.is_admin else None
        return {
            "name": actor.name, "kind": actor.kind, "roles": actor.roles,
            "tools": sorted(rs.tools) if rs else [], "can_code": rs.can_code if rs else False,
            "write_globs": rs.write_globs if rs else [], "role_prompt": rs.prompt if rs else "",
            "wake_on": sorted(rs.wake_on) if rs else [],
            "fleet": self._public_fleet(f), "active": rs.active(f["state"]) if rs else True,
        }

    def set_roles(self, actor: Actor, name: str, roles: list[str]) -> dict:
        """Admin applies directly. The orchestrator applies directly unless a mandatory role is
        affected, in which case a (non-blocking) role_change decision is raised for the user."""
        with self._write() as c:
            f = self._fleet(c)
            self._require_tool(f, actor, "role_reassign")
            cat = self._catalog(f)
            try:
                validate_assignment(roles, cat)
            except ValueError as e:
                raise ApiError(400, "bad_roles", str(e))
            a = self._agent(c, name)
            touched = set(a["roles"]) ^ set(roles)
            if not actor.is_admin and any(cat[r].mandatory for r in touched if r in cat):
                did = self._raise_decision(
                    c, f, actor, "role_change",
                    f"Change roles of {name} from {a['roles']} to {roles}?",
                    ["approve", "reject"], json.dumps({"agent": name, "roles": roles}), None,
                    blocking=False)
                return {"status": "proposed", "decision_id": did}
            self._apply_roles(c, actor.name, name, roles, a["roles"])
        return {"status": "applied"}

    def _apply_roles(self, c, by: str, name: str, roles: list[str], old: list[str]) -> None:
        c.execute(update(agents).where(agents.c.name == name).values(roles=roles))
        self._emit(c, "agent.roles_changed", by, name=name, roles=roles, old=old)

    def heartbeat(self, actor: Actor, busy: bool = False, attached: bool = False) -> dict:
        with self._write() as c:
            a = self._agent(c, actor.name)
            was_offline = now() - a["last_seen"] >= self.offline_after
            if was_offline:
                # Lock the fleet row before touching agents, like every other writer does;
                # the opposite order can deadlock on MySQL/Postgres.
                f = self._fleet(c)
            c.execute(update(agents).where(agents.c.name == actor.name).values(
                last_seen=now(), status="busy" if busy else "online", attached=attached))
            if was_offline:
                self._emit(c, "agent.online", actor.name, name=actor.name)
                self._schedule(c, f)  # a returning agent may unblock waiting tasks
            f = self._fleet(c, lock=False)
        return {"fleet": self._public_fleet(f)}

    def report_quota(self, actor: Actor, until: float, detail: str = "") -> dict:
        with self._write() as c:
            self._fleet(c)
            c.execute(update(agents).where(agents.c.name == actor.name).values(quota_until=until))
            self._emit(c, "agent.quota_exhausted", actor.name, "role:orchestrator",
                       name=actor.name, until=until, detail=detail[:500])
        return {"ok": True}

    # ------------------------------------------------------------------ events

    def list_events(self, actor: Actor, since: int = 0, limit: int = 200) -> dict:
        limit = max(1, min(limit, 500))
        q = select(events).where(events.c.seq > since).order_by(events.c.seq).limit(limit)
        if not actor.is_admin:
            aud = ["*", f"agent:{actor.name}"] + [f"role:{r}" for r in actor.roles]
            q = q.where(events.c.audience.in_(aud))
        with self._read() as c:
            rows = [row(r) for r in c.execute(q)]
            head = c.execute(select(func.max(events.c.seq))).scalar() or 0
        return {"events": rows, "head": head}

    # ------------------------------------------------------------------ messages

    def post_message(self, actor: Actor, body: str, channel: str | None = "#general",
                     recipient: str | None = None, priority: str = "normal",
                     thread_id: int | None = None) -> dict:
        if priority not in PRIORITIES:
            raise ApiError(400, "bad_priority", f"priority must be one of {PRIORITIES}")
        if not body or len(body.encode()) > MAX_BODY:
            raise ApiError(400, "bad_body", f"body must be 1..{MAX_BODY} bytes")
        if recipient:
            channel = None
        elif not channel or not re.match(r"^#[a-z0-9][a-z0-9_-]{0,62}$", channel):
            raise ApiError(400, "bad_channel", "channel must look like #name")
        mentions = sorted(set(re.findall(r"@([a-z][a-z0-9-]{1,31})", body)))
        with self._write() as c:
            f = self._fleet(c)
            if not actor.is_admin:
                self._require_tool(f, actor, "dm" if recipient else "post")
                if priority == "interrupt" and "orchestrator" not in actor.roles:
                    raise ApiError(403, "forbidden", "only the orchestrator and user may interrupt")
                recent = c.execute(select(func.count()).select_from(messages).where(and_(
                    messages.c.author == actor.name, messages.c.ts > now() - 60))).scalar()
                if recent >= RATE_PER_MIN:
                    raise ApiError(429, "rate_limited", "message rate limit (20/min) reached")
            if recipient and recipient != "user":
                self._agent(c, recipient)
            m = dict(ts=now(), channel=channel, author=actor.name, recipient=recipient,
                     priority=priority, thread_id=thread_id, body=body, mentions=mentions)
            mid = c.execute(insert(messages).values(**m)).inserted_primary_key[0]
            m["id"] = mid
            if recipient == "user":
                aud = "admin"
            elif recipient:
                aud = f"agent:{recipient}"
            else:
                aud = "*"
            self._emit(c, "message.posted", actor.name, aud, message=m)
        return m

    def list_messages(self, actor: Actor, channel: str | None = None, since_id: int = 0,
                      limit: int = 100, dm_with: str | None = None) -> list[dict]:
        q = select(messages).where(messages.c.id > since_id)
        if channel:
            q = q.where(messages.c.channel == channel)
        elif dm_with:
            me = actor.name
            q = q.where(or_(and_(messages.c.author == me, messages.c.recipient == dm_with),
                            and_(messages.c.author == dm_with, messages.c.recipient == me)))
        elif not actor.is_admin:
            q = q.where(or_(messages.c.recipient.is_(None),
                            messages.c.recipient == actor.name, messages.c.author == actor.name))
        q = q.order_by(messages.c.id.desc()).limit(max(1, min(limit, 500)))
        with self._read() as c:
            return [row(r) for r in reversed(c.execute(q).fetchall())]

    def ack(self, actor: Actor, ids: list[int]) -> dict:
        with self._write() as c:
            have = {r.message_id for r in c.execute(select(receipts.c.message_id).where(and_(
                receipts.c.agent == actor.name, receipts.c.message_id.in_(ids))))}
            for i in set(ids) - have:
                c.execute(insert(receipts).values(message_id=i, agent=actor.name, acked_at=now()))
        return {"acked": sorted(set(ids))}

    # ------------------------------------------------------------------ tasks

    def create_task(self, actor: Actor, title: str, spec: str = "", kind: str = "code",
                    deps: list[int] | None = None, path_hints: list[str] | None = None,
                    priority: int = 0, assignee: str | None = None) -> dict:
        if not title or len(title) > 200:
            raise ApiError(400, "bad_title", "title must be 1..200 chars")
        with self._write() as c:
            f = self._fleet(c)
            self._require_tool(f, actor, "task_create")
            for d in deps or []:
                self._task(c, d)
            try:
                hints = [G.normalize(p) for p in path_hints or []]
            except ValueError as e:
                raise ApiError(400, "bad_path_hints", str(e))
            state = "open" if f["state"] in ("running", "frozen", "paused") or kind == "plan" \
                else "draft"
            tid = c.execute(insert(tasks).values(
                title=title, spec=spec, kind=kind, state=state, priority=priority,
                deps=deps or [], assignee=None, created_by=actor.name, path_hints=hints,
                notes="", result="", approved=False, created=now(), updated=now()
            )).inserted_primary_key[0]
            self._emit(c, "task.created", actor.name, id=tid, title=title, kind=kind, state=state)
            if assignee:
                self._assign(c, f, actor.name, tid, assignee)
            else:
                self._schedule(c, f)
            return self._task(c, tid)

    def list_tasks(self, state: str | None = None, assignee: str | None = None) -> list[dict]:
        q = select(tasks).order_by(tasks.c.id)
        if state:
            q = q.where(tasks.c.state.in_(state.split(",")))
        if assignee:
            q = q.where(tasks.c.assignee == assignee)
        with self._read() as c:
            return [row(r) for r in c.execute(q)]

    def get_task(self, task_id: int) -> dict:
        with self._read() as c:
            return self._task(c, task_id)

    def assign_task(self, actor: Actor, task_id: int, to: str) -> dict:
        with self._write() as c:
            f = self._fleet(c)
            self._require_tool(f, actor, "task_assign")
            self._assign(c, f, actor.name, task_id, to)
            return self._task(c, task_id)

    def _assign(self, c, f: dict, by: str, task_id: int, to: str) -> None:
        t = self._task(c, task_id)
        a = self._agent(c, to)
        if t["state"] in ("done", "cancelled"):
            raise ApiError(409, "bad_state", f"task #{task_id} is {t['state']}")
        state = t["state"] if t["state"] == "draft" else "claimed"
        c.execute(update(tasks).where(tasks.c.id == task_id).values(
            assignee=to, state=state, updated=now(),
            branch=t["branch"] or f"hive/{to}"))
        if state != "draft":
            self._emit(c, "task.assigned", by, "*", id=task_id, title=t["title"],
                       assignee=to, kind=t["kind"], previous=t["assignee"])

    def claim_task(self, actor: Actor, task_id: int) -> dict:
        with self._write() as c:
            f = self._fleet(c)
            self._require_active(f, actor)
            t = self._task(c, task_id)
            if t["state"] != "open" and not (t["state"] == "claimed"
                                             and t["assignee"] == actor.name):
                raise ApiError(409, "bad_state", f"task #{task_id} is {t['state']}")
            cat = self._catalog(f)
            held = self._held_roles(c)
            if resolve_kind(t["kind"], held, cat) not in actor.roles and not actor.is_admin:
                raise ApiError(403, "wrong_role",
                               f"task kind {t['kind']!r} is not handled by your roles")
            self._assign(c, f, actor.name, task_id, actor.name)
            return self._task(c, task_id)

    def update_task(self, actor: Actor, task_id: int, state: str | None = None,
                    note: str = "") -> dict:
        with self._write() as c:
            f = self._fleet(c)
            t = self._task(c, task_id)
            privileged = actor.is_admin or "orchestrator" in actor.roles
            if t["assignee"] != actor.name and not privileged:
                raise ApiError(403, "not_assignee", f"task #{task_id} is not assigned to you")
            vals: dict = {"updated": now()}
            if state:
                allowed = {"in_progress", "blocked", "failed"} | (
                    set(TASK_STATES) if privileged else set())
                if state not in allowed:
                    raise ApiError(400, "bad_state", f"cannot set state {state!r}")
                vals["state"] = state
            if note:
                vals["notes"] = (t["notes"] + f"\n[{actor.name}] {note}").strip()[-20000:]
            c.execute(update(tasks).where(tasks.c.id == task_id).values(**vals))
            ev = {"blocked": "task.blocked", "failed": "task.failed"}.get(state or "",
                                                                          "task.progress")
            self._emit(c, ev, actor.name, "role:orchestrator", id=task_id, title=t["title"],
                       state=state or t["state"], note=note[:2000])
            if state in ("failed", "cancelled"):
                self._block_dependents(c, task_id, state)
            if state in ("failed", "cancelled", "done"):
                self._schedule(c, f)
            return self._task(c, task_id)

    def _block_dependents(self, c, task_id: int, why: str) -> None:
        """Tasks waiting on a task that will never be done can't start: block them and tell
        the orchestrator instead of leaving them open forever."""
        for t in c.execute(select(tasks).where(tasks.c.state.in_(("open", "draft")))).fetchall():
            if task_id not in (t.deps or []):
                continue
            note = f"[system] dependency #{task_id} was {why}"
            c.execute(update(tasks).where(tasks.c.id == t.id).values(
                state="blocked", updated=now(), notes=(t.notes + "\n" + note).strip()))
            self._emit(c, "task.blocked", "system", "role:orchestrator", id=t.id,
                       title=t.title, state="blocked",
                       note=f"dependency #{task_id} was {why}; re-plan, or remove the "
                            "dependency and reopen it")

    def handoff_task(self, actor: Actor, task_id: int, to: str, notes: str) -> dict:
        with self._write() as c:
            f = self._fleet(c)
            t = self._task(c, task_id)
            if t["assignee"] != actor.name and "orchestrator" not in actor.roles \
                    and not actor.is_admin:
                raise ApiError(403, "not_assignee", f"task #{task_id} is not assigned to you")
            c.execute(update(tasks).where(tasks.c.id == task_id).values(
                notes=(t["notes"] + f"\n[handoff {actor.name}→{to}] {notes}").strip()))
            self._assign(c, f, actor.name, task_id, to)
            return self._task(c, task_id)

    def complete_task(self, actor: Actor, task_id: int, result: str = "") -> dict:
        with self._write() as c:
            f = self._fleet(c)
            t = self._task(c, task_id)
            if t["assignee"] != actor.name and not actor.is_admin \
                    and "orchestrator" not in actor.roles:
                raise ApiError(403, "not_assignee", f"task #{task_id} is not assigned to you")
            if t["state"] == "review" or (t["pending_sha"] and not t["approved"]):
                raise ApiError(409, "awaiting_review", "this task's integration awaits review")
            c.execute(update(tasks).where(tasks.c.id == task_id).values(
                state="done", result=result[:20000], updated=now()))
            self._emit(c, "task.completed", actor.name, "*", id=task_id, title=t["title"],
                       assignee=t["assignee"], result=result[:2000])
            self._schedule(c, f)
            return self._task(c, task_id)

    def cancel_task(self, actor: Actor, task_id: int, reason: str = "") -> dict:
        return self.update_task(actor, task_id, "cancelled", reason)

    def report_stuck(self, actor: Actor, task_id: int | None, details: str) -> dict:
        with self._write() as c:
            self._fleet(c)
            if task_id:
                t = self._task(c, task_id)
                c.execute(update(tasks).where(tasks.c.id == task_id).values(
                    state="blocked", updated=now(),
                    notes=(t["notes"] + f"\n[stuck {actor.name}] {details}").strip()))
            self._emit(c, "agent.stuck", actor.name, "role:orchestrator", name=actor.name,
                       task_id=task_id, details=details[:4000])
        return {"ok": True, "message": "The orchestrator was notified. End your turn and wait."}

    def review_verdict(self, actor: Actor, task_id: int, approve: bool, notes: str = "") -> dict:
        with self._write() as c:
            f = self._fleet(c)
            self._require_tool(f, actor, "review_verdict")
            t = self._task(c, task_id)
            if t["state"] != "review":
                raise ApiError(409, "bad_state", f"task #{task_id} is not in review")
            if t["assignee"] == actor.name:
                raise ApiError(403, "self_review", "you cannot review your own task")
            vals = {"approved": approve, "updated": now(),
                    "notes": (t["notes"] + f"\n[review {actor.name}: "
                              f"{'approved' if approve else 'changes requested'}] {notes}").strip()}
            if not approve:
                vals.update(state="in_progress", pending_sha=None)
            c.execute(update(tasks).where(tasks.c.id == task_id).values(**vals))
            self._emit(c, "review.verdict", actor.name, "*", id=task_id, approve=approve,
                       notes=notes[:4000], assignee=t["assignee"])
            return self._task(c, task_id)

    def _held_roles(self, c) -> set[str]:
        return {r for a in c.execute(select(agents.c.roles)) for r in a.roles}

    def _schedule(self, c, f: dict) -> None:
        """Auto-assign ready open tasks to idle, available agents (SPEC §9)."""
        if f["state"] not in ("running", "planning"):
            return
        cat = self._catalog(f)
        all_tasks = [row(r) for r in c.execute(select(tasks))]
        done = {t["id"] for t in all_tasks if t["state"] == "done"}
        busy = {t["assignee"] for t in all_tasks if t["state"] in ACTIVE_TASK_STATES}
        t_now = now()
        pool = [row(a) for a in c.execute(select(agents))]
        held = {r for a in pool for r in a["roles"]}
        ready = sorted((t for t in all_tasks if t["state"] == "open" and not t["assignee"]
                        and set(t["deps"]) <= done), key=lambda t: (-t["priority"], t["id"]))
        for t in ready:
            role = resolve_kind(t["kind"], held, cat)
            if role is None:
                continue
            cands = [a for a in pool if role in a["roles"] and a["name"] not in busy
                     and a["quota_until"] <= t_now
                     and t_now - a["last_seen"] < self.offline_after
                     and role_set(a["roles"], cat).active(f["state"])]
            if not cands:
                continue

            def locality(a):
                touched = [x for x in all_tasks if x["assignee"] == a["name"]]
                score = sum(1 for x in touched for h in x["path_hints"] for p in t["path_hints"]
                            if G.overlaps(h, p))
                return (-score, a["name"])

            chosen = sorted(cands, key=locality)[0]
            self._assign(c, f, "system", t["id"], chosen["name"])
            busy.add(chosen["name"])

    # ------------------------------------------------------------------ leases

    def acquire_lease(self, actor: Actor, globs_: list[str], ttl: int = LEASE_TTL_DEFAULT,
                      reason: str = "", task_id: int | None = None) -> dict:
        if not globs_:
            raise ApiError(400, "bad_globs", "at least one glob is required")
        try:
            norm = [G.normalize(g) for g in globs_]
        except ValueError as e:
            raise ApiError(400, "bad_globs", str(e))
        ttl = max(30, min(int(ttl), LEASE_TTL_MAX))
        with self._write() as c:
            f = self._fleet(c)
            self._require_tool(f, actor, "lease_acquire")
            self._require_active(f, actor)
            if f["state"] in ("frozen", "paused"):
                raise ApiError(423, "fleet_" + f["state"], f"fleet is {f['state']}")
            self._expire_leases(c, f)
            conflicts = []
            for l in c.execute(select(leases).where(and_(leases.c.state == "active",
                                                         leases.c.holder != actor.name))):
                if G.any_overlap(norm, l.globs):
                    conflicts.append({"id": l.id, "holder": l.holder, "globs": l.globs,
                                      "expires_at": l.expires_at, "reason": l.reason})
            if conflicts:
                raise ApiError(409, "lease_conflict", "requested paths are leased by others",
                               conflicts=conflicts)
            fence = f["fencing_counter"] + 1
            self._set_fleet(c, fencing_counter=fence)
            lid = c.execute(insert(leases).values(
                holder=actor.name, globs=norm, fencing=fence, task_id=task_id,
                reason=reason[:500], acquired=now(), expires_at=now() + ttl, state="active"
            )).inserted_primary_key[0]
            self._emit(c, "lease.acquired", actor.name, "*", id=lid, holder=actor.name,
                       globs=norm, fencing=fence)
            return row(c.execute(select(leases).where(leases.c.id == lid)).first())

    def list_leases(self, holder: str | None = None, active_only: bool = True) -> list[dict]:
        q = select(leases).order_by(leases.c.id)
        if active_only:
            q = q.where(leases.c.state == "active")
        if holder:
            q = q.where(leases.c.holder == holder)
        with self._read() as c:
            return [row(r) for r in c.execute(q)]

    def _own_lease(self, c, actor: Actor, lease_id: int) -> dict:
        l = row(c.execute(select(leases).where(leases.c.id == lease_id)).first())
        if l is None:
            raise ApiError(404, "no_such_lease", f"no lease #{lease_id}")
        if l["holder"] != actor.name and not actor.is_admin:
            raise ApiError(403, "not_holder", f"lease #{lease_id} is held by {l['holder']}")
        if l["state"] != "active":
            raise ApiError(410, "lease_" + l["state"], f"lease #{lease_id} is {l['state']}")
        return l

    def extend_lease(self, actor: Actor, lease_id: int, ttl: int = LEASE_TTL_DEFAULT) -> dict:
        ttl = max(30, min(int(ttl), LEASE_TTL_MAX))
        with self._write() as c:
            f = self._fleet(c)
            self._expire_leases(c, f)
            l = self._own_lease(c, actor, lease_id)
            exp = max(l["expires_at"], now() + ttl)
            c.execute(update(leases).where(leases.c.id == lease_id).values(expires_at=exp))
            l["expires_at"] = exp
            return l

    def release_lease(self, actor: Actor, lease_id: int) -> dict:
        """Release without integrating (abandon). Integration releases via integrate()."""
        with self._write() as c:
            self._fleet(c)
            l = self._own_lease(c, actor, lease_id)
            c.execute(update(leases).where(leases.c.id == lease_id).values(state="released"))
            self._emit(c, "lease.released", actor.name, "*", id=lease_id, holder=l["holder"],
                       globs=l["globs"])
        return {"ok": True}

    def force_release_lease(self, actor: Actor, lease_id: int, reason: str = "") -> dict:
        with self._write() as c:
            f = self._fleet(c)
            self._require_tool(f, actor, "lease_force_release")
            l = row(c.execute(select(leases).where(leases.c.id == lease_id)).first())
            if l is None or l["state"] != "active":
                raise ApiError(404, "no_such_lease", f"no active lease #{lease_id}")
            c.execute(update(leases).where(leases.c.id == lease_id).values(state="revoked"))
            self._set_fleet(c, fencing_counter=f["fencing_counter"] + 1)
            self._emit(c, "lease.revoked", actor.name, "*", id=lease_id, holder=l["holder"],
                       globs=l["globs"], reason=reason)
        return {"ok": True}

    def _expire_leases(self, c, f: dict) -> None:
        if f["state"] in ("frozen", "paused"):
            return
        for l in c.execute(select(leases).where(and_(leases.c.state == "active",
                                                     leases.c.expires_at < now()))).fetchall():
            c.execute(update(leases).where(leases.c.id == l.id).values(state="expired"))
            self._emit(c, "lease.expired", "system", "*", id=l.id, holder=l.holder,
                       globs=l.globs)

    # ------------------------------------------------------------------ integration (SPEC §13)

    def integrate(self, actor: Actor, sha: str, base_sha: str, lease_fences: dict[str, int],
                  task_id: int | None = None, paths: list[str] | None = None) -> dict:
        if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
            raise ApiError(400, "bad_sha", "sha must be a full 40-hex commit id")
        try:
            fences = {int(k): int(v) for k, v in (lease_fences or {}).items()}
        except (TypeError, ValueError):
            raise ApiError(400, "bad_leases", "leases must map lease ids to fencing numbers")
        if not fences:
            raise ApiError(400, "bad_leases", "integration requires the leases being released")
        with self._write() as c:
            f = self._fleet(c)
            self._require_tool(f, actor, "lease_release")
            if f["state"] in ("frozen", "paused"):
                raise ApiError(423, "fleet_" + f["state"], f"fleet is {f['state']}")
            self._expire_leases(c, f)
            self._check_fences(c, actor, fences)
            if self._review_mode(f) != "off":
                if not task_id:
                    raise ApiError(400, "task_required",
                                   "review is required: pass the task_id of your task")
                t = self._task(c, task_id)
                if t["assignee"] != actor.name:
                    raise ApiError(403, "not_assignee",
                                   f"task #{task_id} is not assigned to you")
                if t["state"] not in ("claimed", "in_progress", "review", "blocked"):
                    raise ApiError(409, "bad_state", f"task #{task_id} is {t['state']}")
                # An approval only counts for the review it was given in.
                if not (t["state"] == "review" and t["approved"]):
                    reviewer = self._pick_reviewer(c, f, t)
                    c.execute(update(tasks).where(tasks.c.id == task_id).values(
                        state="review", pending_sha=sha, reviewer=reviewer, approved=False,
                        updated=now()))
                    self._emit(c, "review.requested", actor.name, "*", id=task_id,
                               title=t["title"], reviewer=reviewer, sha=sha,
                               branch=t["branch"], assignee=actor.name)
                    return {"status": "review_pending", "reviewer": reviewer}
            token = secrets.token_urlsafe(24)
            c.execute(insert(integrations).values(
                token=token, agent=actor.name, sha=sha,
                leases={str(k): v for k, v in fences.items()}, task_id=task_id,
                state="granted", created=now()))
            repo = self._cfg(f, "repo")
        github, main = repo.get("github"), repo.get("main", "main")
        if github and self.github_token:
            r = requests.patch(
                f"https://api.github.com/repos/{github}/git/refs/heads/{main}",
                json={"sha": sha, "force": False}, timeout=20,
                headers={"Authorization": f"Bearer {self.github_token}",
                         "Accept": "application/vnd.github+json"})
            if r.status_code == 422:
                raise ApiError(409, "not_fast_forward", "main moved; rebase and retry")
            if r.status_code >= 300:
                raise ApiError(502, "github_error", f"GitHub returned {r.status_code}: "
                                                    f"{r.text[:300]}")
            return self.confirm_integration(actor, token, sha, paths)
        return {"status": "push_main", "main": main, "grant": token}

    def _check_fences(self, c, actor: Actor, fences: dict[int, int]) -> None:
        for lid, fence in fences.items():
            l = self._own_lease(c, actor, lid)
            if l["fencing"] != fence:
                raise ApiError(409, "stale_fence", f"lease #{lid} fencing token is stale")

    def confirm_integration(self, actor: Actor, grant: str, sha: str,
                            paths: list[str] | None = None) -> dict:
        """Record that main advanced to `sha`. Only valid with the one-time grant integrate()
        issued to this agent for this sha, while its leases are still held."""
        if not grant:
            raise ApiError(400, "grant_required", "confirm requires the grant from integrate")
        with self._write() as c:
            f = self._fleet(c)
            g = row(c.execute(select(integrations).where(integrations.c.token == grant))
                    .first())
            if g is None or g["agent"] != actor.name:
                raise ApiError(403, "bad_grant", "unknown integration grant")
            if g["state"] != "granted":
                raise ApiError(409, "grant_used", "this integration was already confirmed")
            if g["sha"] != sha:
                raise ApiError(409, "grant_mismatch", "grant was issued for a different commit")
            fences = {int(k): int(v) for k, v in g["leases"].items()}
            self._check_fences(c, actor, fences)
            c.execute(update(integrations).where(integrations.c.id == g["id"])
                      .values(state="confirmed"))
            c.execute(update(leases).where(and_(leases.c.id.in_(list(fences)),
                                                leases.c.state == "active"))
                      .values(state="released"))
            self._set_fleet(c, main_sha=sha)
            task_id = g["task_id"]
            self._emit(c, "main.advanced", actor.name, "*", sha=sha, by=actor.name,
                       paths=(paths or [])[:500], task_id=task_id)
            if task_id:
                t = self._task(c, task_id)
                if t["state"] == "review" and t["approved"]:
                    c.execute(update(tasks).where(tasks.c.id == task_id).values(
                        state="done", pending_sha=None, approved=False, updated=now()))
                    self._emit(c, "task.completed", actor.name, "*", id=task_id,
                               title=t["title"], assignee=t["assignee"], result="integrated")
                    self._schedule(c, self._fleet(c))
        return {"status": "integrated", "sha": sha}

    def report_conflict(self, actor: Actor, task_id: int | None, detail: str) -> dict:
        with self._write() as c:
            self._emit(c, "merge.conflict", actor.name, "role:orchestrator", name=actor.name,
                       task_id=task_id, detail=detail[:4000])
        return {"ok": True}

    def _pick_reviewer(self, c, f: dict, t: dict) -> str | None:
        cat = self._catalog(f)
        pool = [row(a) for a in c.execute(select(agents))]
        mode = self._review_mode(f)
        role = "orchestrator" if mode == "orchestrator" else resolve_kind(
            "review", {r for a in pool for r in a["roles"]}, cat)
        cands = [a for a in pool if role in a["roles"] and a["name"] != t["assignee"]]
        if not cands:  # e.g. the only reviewer is the author: the orchestrator reviews instead
            cands = [a for a in pool if "orchestrator" in a["roles"]
                     and a["name"] != t["assignee"]]
        cands.sort(key=lambda a: (a["quota_until"] > now(), -a["last_seen"]))
        return cands[0]["name"] if cands else None

    # ------------------------------------------------------------------ fleet & decisions

    def _public_fleet(self, f: dict) -> dict:
        return {k: f[k] for k in ("state", "prev_state", "since", "frozen_at",
                                  "blocking_decision", "orchestrator", "main_sha", "goal")}

    def get_fleet(self) -> dict:
        with self._read() as c:
            f = self._fleet(c, lock=False)
            out = self._public_fleet(f)
            out["review"] = self._review_mode(f)
            if f["blocking_decision"]:
                out["decision"] = row(c.execute(select(decisions).where(
                    decisions.c.id == f["blocking_decision"])).first())
            return out

    def start_fleet(self, actor: Actor, goal: str) -> dict:
        if not actor.is_admin:
            raise ApiError(403, "forbidden", "only the user starts the fleet")
        with self._write() as c:
            f = self._fleet(c)
            if f["state"] != "setup":
                raise ApiError(409, "bad_state", f"fleet is already {f['state']}")
            pool = {a.name: list(a.roles) for a in c.execute(select(agents))}
            problems = validate_fleet(pool, self._catalog(f))
            if problems:
                raise ApiError(400, "invalid_fleet", "; ".join(problems), problems=problems)
            orch = next(n for n, r in pool.items() if "orchestrator" in r)
            self._set_fleet(c, state="planning", orchestrator=orch, goal=goal)
            self._emit(c, "fleet.started", actor.name, "*", goal=goal, orchestrator=orch)
            self._emit(c, "fleet.state", actor.name, "*", state="planning")
        self.post_message(ADMIN, f"New goal for the fleet:\n\n{goal}", channel="#user",
                          priority="high")
        return self.get_fleet()

    def fleet_control(self, actor: Actor, action: str) -> dict:
        with self._write() as c:
            f = self._fleet(c)
            self._require_tool(f, actor, "fleet_control")
            if action == "pause":
                if f["state"] == "paused":
                    return self._public_fleet(f)
                # While paused, an open blocking decision records that the fleet was frozen or
                # awaiting approval underneath; prev_state keeps the state to return to after
                # that decision is answered.
                blocked = f["state"] in ("frozen", "awaiting_approval")
                self._set_fleet(c, state="paused",
                                prev_state=f["prev_state"] if blocked else f["state"],
                                frozen_at=f["frozen_at"] or now())
            elif action == "resume":
                if f["state"] != "paused":
                    raise ApiError(409, "bad_state", "fleet is not paused")
                if f["blocking_decision"]:
                    d = row(c.execute(select(decisions).where(
                        decisions.c.id == f["blocking_decision"])).first())
                    if d and d["kind"] == "start_approval":
                        self._thaw_leases(c, f)
                        self._set_fleet(c, state="awaiting_approval", frozen_at=None)
                    else:  # still frozen: lease clocks stay stopped until it is answered
                        self._set_fleet(c, state="frozen")
                else:
                    target = f["prev_state"] or "running"
                    self._thaw_leases(c, f)
                    self._set_fleet(c, state=target, prev_state=None, frozen_at=None)
                    if target == "running":
                        self._emit(c, "fleet.unfrozen", actor.name, "*", reason="resumed")
            else:
                raise ApiError(400, "bad_action", "action must be pause or resume")
            nf = self._fleet(c)
            self._emit(c, "fleet.state", actor.name, "*", state=nf["state"])
            self._schedule(c, nf)
            return self._public_fleet(nf)

    def reset_fleet(self, actor: Actor) -> dict:
        """Admin escape hatch: return to setup (keeps agents, tasks and history)."""
        if not actor.is_admin:
            raise ApiError(403, "forbidden", "admin only")
        with self._write() as c:
            self._fleet(c)
            self._set_fleet(c, state="setup", prev_state=None, frozen_at=None,
                            blocking_decision=None)
            self._emit(c, "fleet.state", actor.name, "*", state="setup")
        return self.get_fleet()

    def request_decision(self, actor: Actor, kind: str, question: str,
                         options: list[str] | None = None, context: str = "",
                         task_id: int | None = None, freeze: bool | None = None) -> dict:
        if kind not in ("start_approval", "stuck", "question"):
            raise ApiError(400, "bad_kind", "kind must be start_approval, stuck or question")
        with self._write() as c:
            f = self._fleet(c)
            self._require_tool(f, actor, "decision_request")
            if kind == "start_approval":
                if f["state"] != "planning":
                    raise ApiError(409, "bad_state", "start approval is only valid in planning")
                n = c.execute(select(func.count()).select_from(tasks)
                              .where(tasks.c.state == "draft")).scalar()
                if not n:
                    raise ApiError(409, "no_plan", "create the planned tasks (as drafts) first")
                options = options or ["approve", "reject"]
                blocking = True
            else:
                blocking = freeze if freeze is not None else kind == "stuck"
            did = self._raise_decision(c, f, actor, kind, question, options or [], context,
                                       task_id, blocking)
            return row(c.execute(select(decisions).where(decisions.c.id == did)).first())

    def _raise_decision(self, c, f, actor: Actor, kind, question, options, context, task_id,
                        blocking: bool) -> int:
        queued = blocking and f["blocking_decision"] is not None
        did = c.execute(insert(decisions).values(
            kind=kind, raised_by=actor.name, question=question[:4000], options=options,
            context=context[:20000], task_id=task_id, blocking=blocking,
            state="queued" if queued else "open", created=now())).inserted_primary_key[0]
        if blocking and not queued:
            self._activate_blocking(c, f, did, kind)
        self._emit(c, "decision.requested", actor.name, "*", id=did, kind=kind,
                   question=question[:2000], options=options, blocking=blocking, queued=queued)
        c.execute(insert(status_feed).values(
            ts=now(), author=actor.name, level="decision-needed",
            summary=f"Decision #{did} ({kind}): {question[:400]}", body=context[:20000]))
        return did

    def _activate_blocking(self, c, f: dict, did: int, kind: str) -> None:
        if f["state"] == "paused":
            # Record it; resume enters frozen/awaiting_approval. prev_state stays the state to
            # return to once this decision is answered.
            self._set_fleet(c, blocking_decision=did)
            if f["frozen_at"] is None:
                self._set_fleet(c, frozen_at=now())
            return
        if kind == "start_approval":
            self._set_fleet(c, state="awaiting_approval", blocking_decision=did)
            self._emit(c, "fleet.state", "system", "*", state="awaiting_approval")
        else:
            prev = f["state"] if f["state"] not in ("frozen",) else f["prev_state"]
            self._set_fleet(c, state="frozen", prev_state=prev, frozen_at=now(),
                            blocking_decision=did)
            self._emit(c, "fleet.frozen", "system", "*", decision_id=did)
            self._emit(c, "fleet.state", "system", "*", state="frozen")

    def list_decisions(self, state: str | None = None) -> list[dict]:
        q = select(decisions).order_by(decisions.c.id)
        if state:
            q = q.where(decisions.c.state.in_(state.split(",")))
        with self._read() as c:
            return [row(r) for r in c.execute(q)]

    def answer_decision(self, actor: Actor, did: int, answer: str, note: str = "") -> dict:
        if not actor.is_admin:
            raise ApiError(403, "forbidden", "only the user answers decisions")
        with self._write() as c:
            f = self._fleet(c)
            d = row(c.execute(select(decisions).where(decisions.c.id == did)).first())
            if d is None:
                raise ApiError(404, "no_such_decision", f"no decision #{did}")
            if d["state"] != "open":
                raise ApiError(409, "bad_state", f"decision #{did} is {d['state']}")
            c.execute(update(decisions).where(decisions.c.id == did).values(
                state="answered", answer=answer, note=note, answered_at=now()))
            approved = answer.strip().lower() in ("approve", "approved", "yes", "y", "ok")
            paused = f["state"] == "paused"
            if d["kind"] == "start_approval":
                if approved:
                    self._open_drafts(c, actor.name)
                target = "running" if approved else "planning"
                if paused:  # stay paused; resume returns to the new state
                    self._set_fleet(c, prev_state=target, blocking_decision=None)
                else:
                    self._set_fleet(c, state=target, blocking_decision=None)
                    self._emit(c, "fleet.state", actor.name, "*", state=target)
            elif d["blocking"]:
                target = f["prev_state"] or "running"
                if d["task_id"]:
                    c.execute(update(tasks).where(and_(tasks.c.id == d["task_id"],
                                                       tasks.c.state == "blocked"))
                              .values(state="in_progress", updated=now()))
                if paused:  # stay paused; resume thaws and returns to `target`
                    self._set_fleet(c, prev_state=target, blocking_decision=None)
                else:
                    self._thaw_leases(c, f)
                    self._set_fleet(c, state=target, prev_state=None, frozen_at=None,
                                    blocking_decision=None)
                    self._emit(c, "fleet.unfrozen", actor.name, "*", decision_id=did,
                               answer=answer, note=note)
                    self._emit(c, "fleet.state", actor.name, "*", state=target)
            elif d["kind"] == "role_change" and approved:
                spec = json.loads(d["context"])
                a = self._agent(c, spec["agent"])
                self._apply_roles(c, actor.name, spec["agent"], spec["roles"], a["roles"])
            self._emit(c, "decision.answered", actor.name, "*", id=did, kind=d["kind"],
                       answer=answer, note=note, task_id=d["task_id"])
            # Activate the next queued blocking decision, if any.
            f = self._fleet(c)
            if f["blocking_decision"] is None:
                nxt = c.execute(select(decisions).where(decisions.c.state == "queued")
                                .order_by(decisions.c.id)).first()
                if nxt is not None:
                    c.execute(update(decisions).where(decisions.c.id == nxt.id)
                              .values(state="open"))
                    self._activate_blocking(c, f, nxt.id, nxt.kind)
            self._schedule(c, self._fleet(c))
            return row(c.execute(select(decisions).where(decisions.c.id == did)).first())

    def _open_drafts(self, c, by: str) -> None:
        """Plan approved: drafts become open; pre-assigned drafts are handed to their agent."""
        for t in c.execute(select(tasks).where(tasks.c.state == "draft")).fetchall():
            if t.assignee:
                c.execute(update(tasks).where(tasks.c.id == t.id)
                          .values(state="claimed", updated=now()))
                self._emit(c, "task.assigned", by, "*", id=t.id, title=t.title,
                           assignee=t.assignee, kind=t.kind, previous=None)
            else:
                c.execute(update(tasks).where(tasks.c.id == t.id)
                          .values(state="open", updated=now()))

    def _thaw_leases(self, c, f: dict) -> None:
        """Extend active leases by the time the fleet spent frozen (SPEC §7)."""
        if not f["frozen_at"]:
            return
        delta = now() - f["frozen_at"]
        c.execute(update(leases).where(leases.c.state == "active")
                  .values(expires_at=leases.c.expires_at + delta))

    # ------------------------------------------------------------------ status feed

    def publish_status(self, actor: Actor, summary: str, body: str = "",
                       level: str = "fyi") -> dict:
        if level not in ("fyi", "decision-needed", "alert"):
            raise ApiError(400, "bad_level", "level must be fyi, decision-needed or alert")
        with self._write() as c:
            f = self._fleet(c)
            self._require_tool(f, actor, "status_publish")
            sid = c.execute(insert(status_feed).values(
                ts=now(), author=actor.name, level=level, summary=summary[:500],
                body=body[:50000])).inserted_primary_key[0]
            self._emit(c, "status.published", actor.name, "admin", id=sid, level=level,
                       summary=summary[:500])
        return {"id": sid}

    def list_status(self, since_id: int = 0, limit: int = 50) -> list[dict]:
        q = (select(status_feed).where(status_feed.c.id > since_id)
             .order_by(status_feed.c.id.desc()).limit(max(1, min(limit, 200))))
        with self._read() as c:
            return [row(r) for r in reversed(c.execute(q).fetchall())]

    # ------------------------------------------------------------------ live output streams

    def append_stream(self, actor: Actor, run: str, lines: list[str]) -> dict:
        if actor.is_admin:
            raise ApiError(403, "forbidden", "streams are uploaded by agents")
        lines = [str(l)[:STREAM_LINE_MAX] for l in lines[:STREAM_BATCH_MAX]]
        if not lines:
            return {"ok": True}
        t = now()
        with self._write() as c:
            c.execute(insert(streams), [{"agent": actor.name, "ts": t, "run": run[:64],
                                         "text": l} for l in lines])
            # Keep a bounded tail per agent.
            cut = c.execute(select(streams.c.id).where(streams.c.agent == actor.name)
                            .order_by(streams.c.id.desc()).offset(STREAM_KEEP).limit(1)).scalar()
            if cut:
                c.execute(delete(streams).where(and_(streams.c.agent == actor.name,
                                                     streams.c.id <= cut)))
        return {"ok": True}

    def read_stream(self, actor: Actor, agent: str | None, since_id: int = 0,
                    limit: int = 500) -> dict:
        if not actor.is_admin:
            raise ApiError(403, "forbidden", "agent output streams are visible to the user only")
        q = select(streams).where(streams.c.id > since_id)
        if agent:
            q = q.where(streams.c.agent == agent)
        limit = max(1, min(limit, 2000))
        with self._read() as c:
            if since_id <= 0:  # initial view: the latest `limit` lines
                rows = list(reversed(c.execute(q.order_by(streams.c.id.desc()).limit(limit))
                                     .fetchall()))
            else:
                rows = c.execute(q.order_by(streams.c.id).limit(limit)).fetchall()
        lines = [row(r) for r in rows]
        return {"lines": lines, "last_id": lines[-1]["id"] if lines else since_id}

    # ------------------------------------------------------------------ reports

    def request_report(self, actor: Actor, target: str, about: str,
                       task_id: int | None = None) -> dict:
        with self._write() as c:
            f = self._fleet(c)
            self._require_tool(f, actor, "report_request")
            self._agent(c, target)
            rid = c.execute(insert(reports).values(
                kind="request", author=actor.name, target=target, task_id=task_id,
                summary=about[:500], body=about[:8000], state="open", created=now()
            )).inserted_primary_key[0]
            self._emit(c, "report.requested", actor.name, f"agent:{target}", id=rid,
                       about=about[:2000], task_id=task_id, by=actor.name)
        return {"id": rid, "state": "open"}

    def submit_report(self, actor: Actor, summary: str, body: str = "",
                      task_id: int | None = None, request_id: int | None = None) -> dict:
        if not summary:
            raise ApiError(400, "bad_summary", "summary is required")
        with self._write() as c:
            self._fleet(c)
            if request_id:
                r = row(c.execute(select(reports).where(reports.c.id == request_id)).first())
                if r is None or r["kind"] != "request":
                    raise ApiError(404, "no_such_request", f"no report request #{request_id}")
                if r["target"] != actor.name and not actor.is_admin:
                    raise ApiError(403, "not_target",
                                   f"report request #{request_id} is addressed to {r['target']}")
                c.execute(update(reports).where(reports.c.id == request_id)
                          .values(state="answered"))
            rid = c.execute(insert(reports).values(
                kind="report", author=actor.name, request_id=request_id, task_id=task_id,
                summary=summary[:500], body=body[:50000], state="filed", created=now()
            )).inserted_primary_key[0]
            self._emit(c, "report.submitted", actor.name, "role:orchestrator", id=rid,
                       summary=summary[:500], task_id=task_id, request_id=request_id,
                       author=actor.name)
        return {"id": rid}

    def list_reports(self, kind: str | None = None, author: str | None = None,
                     target: str | None = None, state: str | None = None,
                     limit: int = 50) -> list[dict]:
        q = select(reports).order_by(reports.c.id.desc()).limit(max(1, min(limit, 200)))
        for col, val in (("kind", kind), ("author", author), ("target", target),
                         ("state", state)):
            if val:
                q = q.where(getattr(reports.c, col) == val)
        with self._read() as c:
            return [row(r) for r in reversed(c.execute(q).fetchall())]

    def get_report(self, rid: int) -> dict:
        with self._read() as c:
            r = row(c.execute(select(reports).where(reports.c.id == rid)).first())
        if r is None:
            raise ApiError(404, "no_such_report", f"no report #{rid}")
        return r

    # ------------------------------------------------------------------ command approvals

    def _check_command(self, f: dict, command: str) -> None:
        """Hard limits that no approval can lift (also enforced by the daemon)."""
        rules = P.build_rules(f.get("config") or {})["worker"]
        hit = P.matches(command, rules["forbidden"])
        if hit:
            raise ApiError(403, "forbidden_command",
                           f"`{hit}` is never allowed for agents, even with approval")
        main = self._cfg(f, "repo").get("main", "main")
        if P.pushes_to(command, main):
            raise ApiError(403, "forbidden_command",
                           f"pushing to {main} is not allowed; integration goes through "
                           "lease_release")

    def request_approval(self, actor: Actor, command: str, reason: str, shell: str = "bash",
                         task_id: int | None = None) -> dict:
        command = (command or "").strip()
        if not command or len(command) > 4000:
            raise ApiError(400, "bad_command", "command must be 1..4000 characters")
        if not reason.strip():
            raise ApiError(400, "bad_reason", "explain why the command is needed")
        if shell not in ("bash", "powershell", "cmd", "sh"):
            raise ApiError(400, "bad_shell", "shell must be bash, powershell, cmd or sh")
        with self._write() as c:
            f = self._fleet(c)
            self._require_tool(f, actor, "request_approval")
            self._require_active(f, actor)
            self._check_command(f, command)
            aid = c.execute(insert(approvals).values(
                agent=actor.name, command=command, shell=shell, reason=reason[:4000],
                task_id=task_id, state="requested", created=now())).inserted_primary_key[0]
            self._emit(c, "approval.requested", actor.name, "role:orchestrator", id=aid,
                       agent=actor.name, command=command, shell=shell, reason=reason[:2000],
                       task_id=task_id)
            return row(c.execute(select(approvals).where(approvals.c.id == aid)).first())

    def _approval(self, c, aid: int, write: bool = True) -> dict:
        a = row(c.execute(select(approvals).where(approvals.c.id == aid)).first())
        if a is None:
            raise ApiError(404, "no_such_approval", f"no approval #{aid}")
        if a["state"] == "approved" and now() - (a["decided_at"] or 0) > APPROVAL_TTL:
            if write:
                c.execute(update(approvals).where(approvals.c.id == aid)
                          .values(state="expired"))
            a["state"] = "expired"
        return a

    @staticmethod
    def _approval_view(actor: Actor, a: dict) -> dict:
        """Command output may contain secrets: only the user and the requester see it."""
        if not actor.is_admin and actor.name != a["agent"]:
            a = {**a, "output": None}
        return a

    def decide_approval(self, actor: Actor, aid: int, approve: bool, note: str = "") -> dict:
        with self._write() as c:
            f = self._fleet(c)
            self._require_tool(f, actor, "approval_decide")
            a = self._approval(c, aid)
            if a["agent"] == actor.name:
                raise ApiError(403, "self_approval", "an agent cannot approve its own request")
            if a["state"] != "requested":
                raise ApiError(409, "bad_state", f"approval #{aid} is {a['state']}")
            if approve:
                self._check_command(f, a["command"])
            c.execute(update(approvals).where(approvals.c.id == aid).values(
                state="approved" if approve else "denied", decided_by=actor.name,
                note=note[:4000], decided_at=now()))
            self._emit(c, "approval.decided", actor.name, f"agent:{a['agent']}", id=aid,
                       approve=approve, note=note[:2000], command=a["command"],
                       by=actor.name)
            return self._approval_view(actor, self._approval(c, aid))

    def claim_approval(self, actor: Actor, aid: int) -> dict:
        """One-time use: marks an approved command as executing and returns it."""
        with self._write() as c:
            f = self._fleet(c)
            self._require_active(f, actor)
            a = self._approval(c, aid)
            if a["agent"] != actor.name:
                raise ApiError(403, "not_requester", f"approval #{aid} belongs to {a['agent']}")
            if a["state"] != "approved":
                raise ApiError(409 if a["state"] != "expired" else 410,
                               "approval_" + a["state"], f"approval #{aid} is {a['state']}")
            self._check_command(f, a["command"])
            c.execute(update(approvals).where(approvals.c.id == aid).values(
                state="executed", executed_at=now()))
            a["state"] = "executed"
            return a

    def approval_result(self, actor: Actor, aid: int, exit_code: int, output: str) -> dict:
        with self._write() as c:
            self._fleet(c)
            a = self._approval(c, aid)
            if a["agent"] != actor.name or a["state"] != "executed":
                raise ApiError(409, "bad_state", f"approval #{aid} was not claimed by you")
            c.execute(update(approvals).where(approvals.c.id == aid).values(
                exit_code=int(exit_code), output=(output or "")[-8000:]))
            self._emit(c, "approval.executed", actor.name, "role:orchestrator", id=aid,
                       agent=actor.name, command=a["command"], exit_code=int(exit_code))
        return {"ok": True}

    def get_approval(self, actor: Actor, aid: int) -> dict:
        with self._read() as c:  # expiry is shown here, recorded when someone tries to use it
            return self._approval_view(actor, self._approval(c, aid, write=False))

    def list_approvals(self, actor: Actor, state: str | None = None,
                       agent: str | None = None, limit: int = 50) -> list[dict]:
        q = select(approvals).order_by(approvals.c.id.desc()).limit(max(1, min(limit, 200)))
        if state:
            q = q.where(approvals.c.state.in_(state.split(",")))
        if agent:
            q = q.where(approvals.c.agent == agent)
        with self._read() as c:
            return [self._approval_view(actor, row(r))
                    for r in reversed(c.execute(q).fetchall())]

    # ------------------------------------------------------------------ maintenance

    def tick(self, force: bool = False) -> None:
        t = now()
        if not force and t - self._last_tick < self.tick_interval:
            return
        if not self._tick_lock.acquire(blocking=False):
            return
        try:
            self._last_tick = t
            if not self._tick_due(t):
                return
            with self._write() as c:
                f = self._fleet(c)
                self._expire_leases(c, f)
                for a in c.execute(select(agents).where(agents.c.status != "offline")).fetchall():
                    if a.last_seen and t - a.last_seen >= self.offline_after:
                        c.execute(update(agents).where(agents.c.name == a.name)
                                  .values(status="offline"))
                        self._emit(c, "agent.offline", "system", "role:orchestrator",
                                   name=a.name)
                self._failover(c, f)
                self._schedule(c, self._fleet(c))
        finally:
            self._tick_lock.release()

    def _tick_due(self, t: float) -> bool:
        """Cheap read-only check so an idle server never takes the write lock in tick()."""
        with self._read() as c:
            f = self._fleet(c, lock=False)
            if f["state"] not in ("frozen", "paused") and c.execute(
                    select(leases.c.id).where(and_(leases.c.state == "active",
                                                   leases.c.expires_at < t)).limit(1)).first():
                return True
            if c.execute(select(agents.c.name).where(and_(
                    agents.c.status != "offline", agents.c.last_seen > 0,
                    agents.c.last_seen <= t - self.offline_after)).limit(1)).first():
                return True
            if f["orchestrator"] and f["state"] != "setup" and \
                    self._cfg(f, "orchestrator").get("standby"):
                a = c.execute(select(agents.c.last_seen, agents.c.quota_until)
                              .where(agents.c.name == f["orchestrator"])).first()
                if a and ((a.last_seen and t - a.last_seen > self.failover_after)
                          or a.quota_until > t):
                    return True
            # Scheduling normally happens on every state change; this is only a safety net.
            if t - self._last_sched >= SCHEDULE_FALLBACK_SECONDS and \
                    f["state"] in ("running", "planning") and c.execute(
                        select(tasks.c.id).where(and_(tasks.c.state == "open",
                                                      tasks.c.assignee.is_(None))).limit(1)
                    ).first():
                self._last_sched = t
                return True
        return False

    # ------------------------------------------------------------------ backups

    def backup_sqlite(self, dest_path: str) -> None:
        """Consistent online copy of a SQLite database, made inside the server process so no
        second process ever opens the live file (important on network filesystems)."""
        if not self.is_sqlite or getattr(self.engine, "hive_sqlite_journal", None) == "memory":
            raise ApiError(400, "not_sqlite",
                           "backups via the API are for SQLite; use your database's dump tool")
        with self._write_lock:
            raw = self.engine.raw_connection()
            try:
                dst = sqlite3.connect(dest_path)
                try:
                    raw.driver_connection.backup(dst)
                finally:
                    dst.close()
            finally:
                raw.close()

    # ------------------------------------------------------------------ archives

    # What an archive holds besides the fleet and agents; events, receipts and integration grants
    # are delivery plumbing (and grants are secrets), so they are left out.
    ARCHIVED = (("tasks", tasks), ("decisions", decisions), ("messages", messages),
                ("status", status_feed), ("reports", reports), ("approvals", approvals),
                ("leases", leases), ("streams", streams))

    def _snapshot(self, c, t: float, kind: str, label: str) -> dict:
        f = self._fleet(c, lock=False)
        fl = {**self._public_fleet(f), "review": self._review_mode(f), "config": f["config"]}
        ags = []
        for r in c.execute(select(agents).order_by(agents.c.name)):
            a = row(r)
            a.pop("token_hash")
            ags.append(a)
        data = {"format": ARCHIVE_FORMAT, "version": ARCHIVE_VERSION, "created": t,
                "kind": kind, "label": label, "fleet": fl, "agents": ags}
        for key, tbl in self.ARCHIVED:
            data[key] = [row(r) for r in c.execute(select(tbl).order_by(tbl.c.id))]
        return data

    def _archive_meta(self, data: dict) -> dict:
        fl = data["fleet"]
        times = [x[k] for key, k in (("tasks", "created"), ("messages", "ts"), ("status", "ts"),
                                     ("streams", "ts")) for x in data[key]]
        return {"created": data["created"], "kind": data["kind"], "label": data["label"],
                "goal": fl["goal"],
                "state": fl["state"], "agents": [a["name"] for a in data["agents"]],
                "counts": {k: len(data[k]) for k, _ in self.ARCHIVED},
                "first": min(times, default=None)}

    def create_archive(self, actor: Actor, label: str = "", flush: bool = False) -> dict:
        """Save everything the dashboard shows into an archive file. With `flush`, also clear it
        from the database and return the fleet to setup; agents, their tokens, the config, the
        orchestrator assignment and main's sha stay."""
        if not actor.is_admin:
            raise ApiError(403, "forbidden", "admin only")
        label = " ".join(str(label or "").split())[:200]
        t = now()
        if not flush:
            with self._read() as c:
                data = self._snapshot(c, t, "snapshot", label)
            return self.archives.write(self.archives.new_id(t), data, self._archive_meta(data))
        with self._write() as c:
            f = self._fleet(c)
            self._check_flushable(c, f)
            data = self._snapshot(c, t, "flush", label)
            aid = self.archives.new_id(t)
            # The file is complete on disk before anything is deleted. If clearing fails, the
            # transaction rolls back and the archive is removed again.
            meta = self.archives.write(aid, data, self._archive_meta(data))
            try:
                self._flush(c, actor, aid)
            except BaseException:
                self.archives.delete(aid)
                raise
        return meta

    def _check_flushable(self, c, f: dict) -> None:
        if f["state"] not in ("setup", "paused"):
            raise ApiError(409, "bad_state", "pause the fleet before archiving and clearing it")
        problems = []
        n = c.execute(select(func.count()).select_from(leases)
                      .where(leases.c.state == "active")).scalar()
        if n:
            problems.append(f"{_n(n, 'file lease is', 'file leases are')} still held; "
                            f"revoke {'it' if n == 1 else 'them'} first")
        n = c.execute(select(func.count()).select_from(approvals)
                      .where(approvals.c.state.in_(("requested", "approved")))).scalar()
        if n:
            it = "it" if n == 1 else "them"
            problems.append(f"{_n(n, 'command approval is', 'command approvals are')} waiting "
                            f"or about to run; deny {it} or let {it} run first")
        n = c.execute(select(func.count()).select_from(integrations)
                      .where(integrations.c.state == "granted")).scalar()
        if n:
            problems.append(f"{_n(n, 'merge into main is', 'merges into main are')} in "
                            "progress; wait for it to finish")
        if problems:
            raise ApiError(409, "busy", "Can't clear yet: " + "; ".join(problems) + ".",
                           problems=problems)

    def _flush(self, c, actor: Actor, aid: str) -> None:
        highs = {}
        for tbl in db.FLUSHED_TABLES:
            pk = list(tbl.primary_key.columns)[0]
            highs[tbl.name] = c.execute(select(func.max(pk))).scalar() or 0
        c.execute(delete(receipts))
        for tbl in db.FLUSHED_TABLES:
            c.execute(delete(tbl))
        if self.is_sqlite:
            self._keep_counting(c, highs)
        self._set_fleet(c, state="setup", prev_state=None, frozen_at=None,
                        blocking_decision=None, goal="")
        self._emit(c, "fleet.state", actor.name, "*", state="setup", archived=aid)

    @staticmethod
    def _keep_counting(c, highs: dict[str, int]) -> None:
        """SQLite would hand out max(id)+1 = 1 again on the now-empty tables. Rebuild any table
        created before ids were AUTOINCREMENT (it is empty, so this is cheap) and seed its
        counter with the old high-water mark."""
        for tbl in db.FLUSHED_TABLES:
            sql = c.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                                    (tbl.name,)).scalar() or ""
            if "AUTOINCREMENT" in sql.upper():
                continue  # sqlite_sequence already remembers the highest id ever used
            tbl.drop(c)
            tbl.create(c)
            c.exec_driver_sql("DELETE FROM sqlite_sequence WHERE name=?", (tbl.name,))
            c.exec_driver_sql("INSERT INTO sqlite_sequence(name, seq) VALUES(?, ?)",
                              (tbl.name, highs[tbl.name]))

    def list_archives(self, actor: Actor) -> list[dict]:
        if not actor.is_admin:
            raise ApiError(403, "forbidden", "admin only")
        return self.archives.list()

    def archive_file(self, actor: Actor, aid: str):
        if not actor.is_admin:
            raise ApiError(403, "forbidden", "admin only")
        try:
            return self.archives.path(aid)
        except KeyError:
            raise ApiError(404, "no_such_archive", f"no archive {aid!r}")

    def delete_archive(self, actor: Actor, aid: str) -> dict:
        if not actor.is_admin:
            raise ApiError(403, "forbidden", "admin only")
        try:
            self.archives.delete(aid)
        except KeyError:
            raise ApiError(404, "no_such_archive", f"no archive {aid!r}")
        return {"ok": True}

    def _failover(self, c, f: dict) -> None:
        orch = f["orchestrator"]
        if not orch or f["state"] == "setup":
            return
        a = row(c.execute(select(agents).where(agents.c.name == orch)).first())
        t = now()
        if a is None:
            return
        down = (a["last_seen"] and t - a["last_seen"] > self.failover_after) or \
            a["quota_until"] > t
        if not down:
            return
        cat = self._catalog(f)
        for name in self._cfg(f, "orchestrator").get("standby", []):
            s = row(c.execute(select(agents).where(agents.c.name == name)).first())
            if s is None or s["quota_until"] > t or t - s["last_seen"] > self.offline_after:
                continue
            # Swap roles: the standby becomes orchestrator, the old orchestrator takes its roles.
            new_roles = ["orchestrator"] + [r for r in s["roles"] if r in cat
                                            and not cat[r].can_code and r != "orchestrator"]
            self._apply_roles(c, "system", name, new_roles, s["roles"])
            # The old orchestrator takes over the standby's roles and keeps its own other ones.
            old_roles = list(dict.fromkeys(
                [r for r in s["roles"] if r != "orchestrator"]
                + [r for r in a["roles"] if r != "orchestrator"]))
            self._apply_roles(c, "system", orch, old_roles, a["roles"])
            c.execute(update(tasks).where(and_(tasks.c.assignee == name,
                                               tasks.c.state.in_(ACTIVE_TASK_STATES)))
                      .values(assignee=None, state="open", updated=t))
            self._set_fleet(c, orchestrator=name)
            self._emit(c, "orchestrator.failover", "system", "*", old=orch, new=name)
            c.execute(insert(status_feed).values(
                ts=t, author="system", level="alert",
                summary=f"Orchestrator failover: {orch} → {name}", body=""))
            return


def create_hive(database_url: str, sqlite_journal: str | None = None, **kw) -> Hive:
    return Hive(db.make_engine(database_url, sqlite_journal), **kw)
