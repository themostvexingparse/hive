"""The fleet as a file (docs/CLI_REDESIGN.md, rung 3): `hive plan` shows how the agents on the
server differ from hive.fleet.toml, `hive apply` makes them match, `hive agents export` writes
the file from what exists. The server stays the source of truth; the file is applied to it.

    [[agents]]
    name = "orch"                 # optional: default <vendor>-<n>, numbered through the file
    vendor = "claude"
    roles = ["orchestrator"]      # or role = "orchestrator"
    on = "desk"                   # a paired machine; leave out for the computer you apply
                                  # from; "elsewhere" leaves where it runs alone
    count = 3                     # several alike: name-1, name-2, … (with a name)
    model = "…"                   # optional slot settings
    permission_mode = "…"
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .config import VENDORS

FLEET_FILE = "hive.fleet.toml"
HERE, ELSEWHERE = "", "elsewhere"
KEYS = {"name", "vendor", "roles", "role", "on", "count", "model", "permission_mode"}


class FleetFileError(ValueError):
    pass


@dataclass
class Want:
    name: str
    vendor: str
    roles: list[str]
    on: str = HERE  # a machine's name, HERE (this computer) or ELSEWHERE (unmanaged)
    model: str = ""
    permission_mode: str = ""


@dataclass
class Change:
    kind: str  # create | roles | move | settings | remove
    name: str
    want: Want | None = None
    detail: str = ""


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    unlisted: list[str] = field(default_factory=list)  # on the server, not in the file
    ok: list[str] = field(default_factory=list)  # already as the file says


def load(path: Path) -> list[Want]:
    try:
        data = tomllib.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        raise FleetFileError(f"no {path.name} here; write one, or start from the current fleet "
                             f"with `hive agents export > {path.name}`") from None
    except tomllib.TOMLDecodeError as e:
        raise FleetFileError(f"{path.name}: {e}") from None
    entries = data.get("agents", [])
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise FleetFileError(f"{path.name}: agents are [[agents]] tables")
    out: list[Want] = []
    numbered: dict[str, int] = {}
    for i, e in enumerate(entries, 1):
        where = f"{path.name}, agent #{i}"
        unknown = set(e) - KEYS
        if unknown:
            raise FleetFileError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}; "
                                 f"keys: {', '.join(sorted(KEYS))}")
        vendor = str(e.get("vendor", "")).lower()
        if vendor not in VENDORS:
            raise FleetFileError(f"{where}: vendor must be one of {', '.join(VENDORS)}")
        roles = e.get("roles", [e["role"]] if "role" in e else [])
        if isinstance(roles, str):
            roles = [r.strip() for r in roles.split(",") if r.strip()]
        if not roles:
            raise FleetFileError(f"{where}: say what it does: roles = [\"programmer\"]")
        count = e.get("count", 1)
        if not isinstance(count, int) or not 1 <= count <= 50:
            raise FleetFileError(f"{where}: count is a whole number from 1 to 50")
        name = str(e.get("name", ""))
        if name and count == 1:
            names = [name]
        else:
            prefix = name or vendor
            start = numbered.get(prefix, 0)
            names = [f"{prefix}-{start + k}" for k in range(1, count + 1)]
            numbered[prefix] = start + count
        for n in names:
            out.append(Want(n, vendor, [str(r) for r in roles], str(e.get("on", HERE)),
                            str(e.get("model", "")), str(e.get("permission_mode", ""))))
    seen: set[str] = set()
    for w in out:
        if w.name in seen:
            raise FleetFileError(f"{path.name}: {w.name} is listed twice")
        seen.add(w.name)
    return out


def where_now(agent: dict, local_names: set[str]) -> str:
    if agent.get("machine"):
        return agent["machine"]
    return HERE if agent["name"] in local_names else ELSEWHERE


def settings_now(agent: dict, local: dict) -> tuple[str, str]:
    if agent.get("machine"):
        p = agent.get("placement") or {}
        return p.get("model", ""), p.get("permission_mode", "")
    s = local.get(agent["name"])
    return (s.model, s.permission_mode) if s else ("", "")


def plan(wants: list[Want], roster: list[dict], local: dict, machines: set[str],
         prune: bool = False) -> Plan:
    """What would change. `local` is this computer's slots for this server, by name."""
    out = Plan()
    have = {a["name"]: a for a in roster}
    for w in wants:
        if w.on not in (HERE, ELSEWHERE) and w.on not in machines:
            raise FleetFileError(f"{w.name}: no paired machine named {w.on!r} "
                                 f"(paired: {', '.join(sorted(machines)) or 'none'})")
        a = have.get(w.name)
        if a is None:
            out.changes.append(Change("create", w.name, w, describe_want(w)))
            continue
        if a["vendor"] != w.vendor:
            raise FleetFileError(f"{w.name} runs {a['vendor']}, not {w.vendor}; an agent's CLI "
                                 "can't change: remove it, or give the file's one another name")
        before = len(out.changes)
        if sorted(a["roles"]) != sorted(w.roles):
            out.changes.append(Change("roles", w.name, w,
                                      f"roles {', '.join(a['roles'])} → {', '.join(w.roles)}"))
        now = where_now(a, set(local))
        if w.on != ELSEWHERE and now != w.on:
            out.changes.append(Change("move", w.name, w,
                                      f"{label(now)} → {label(w.on)}"))
        elif w.on != ELSEWHERE and (w.model, w.permission_mode) != settings_now(a, local) \
                and now != ELSEWHERE:
            m, p = settings_now(a, local)
            out.changes.append(Change("settings", w.name, w,
                                      f"model {m or 'default'} → {w.model or 'default'}, "
                                      f"permission mode {p or 'default'} → "
                                      f"{w.permission_mode or 'default'}"))
        if len(out.changes) == before:
            out.ok.append(w.name)
    listed = {w.name for w in wants}
    for name in sorted(set(have) - listed):
        if prune:
            out.changes.append(Change("remove", name, None, "not in the file"))
        else:
            out.unlisted.append(name)
    return out


def label(on: str) -> str:
    return "this computer" if on == HERE else "unmanaged" if on == ELSEWHERE else on


def describe_want(w: Want) -> str:
    extra = [f"model {w.model}"] if w.model else []
    extra += [f"permission mode {w.permission_mode}"] if w.permission_mode else []
    return (f"{w.vendor}, {', '.join(w.roles)}, on {label(w.on)}"
            + (f" ({', '.join(extra)})" if extra else ""))


def export(roster: list[dict], local: dict) -> str:
    """The fleet as a file, every agent named, so applying it changes nothing."""
    entries = []
    for a in roster:
        e = {"name": a["name"], "vendor": a["vendor"], "roles": list(a["roles"])}
        on = where_now(a, set(local))
        if on:
            e["on"] = on
        model, mode = settings_now(a, local)
        if model:
            e["model"] = model
        if mode:
            e["permission_mode"] = mode
        entries.append(e)
    from .settings import show
    head = ("# The fleet, as `hive agents export` wrote it. `hive plan` shows what applying it\n"
            "# would change; `hive apply` does it. Leave `on` out for agents on the computer\n"
            "# you apply from; \"elsewhere\" leaves where an agent runs alone.\n")
    blocks = ["[[agents]]\n" + "".join(f"{k} = {show(v)}\n" for k, v in e.items())
              for e in entries]
    return head + "".join("\n" + b for b in blocks)
