"""`hive overview`: everything about this project's Hive setup in one report.

Facts are gathered first (`collect`, a plain dict that tests can inspect) and printed second
(`render`). Local facts never need the network. The fleet section asks the server with the
admin login, or with the token of one of this machine's slots for this repository, and the
machine section asks the local daemon if `hive up` is running; both are skipped with
`offline=True` and reported as unavailable when they can't be reached.
"""

from __future__ import annotations

import json
import sys
import subprocess
from pathlib import Path

import requests

from . import __version__, permissions, prompts, ui
from .config import (find_repo_root, hive_home, load_admin_config, load_machine_config,
                     load_repo_config, machine_config_path)
from .roles import BUILTIN_ROLES, load_roles

TIMEOUT = 5


def _git(cwd: Path, *args: str) -> str:
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def _common_dir(path: Path) -> Path | None:
    """The shared .git directory of a clone or any of its worktrees."""
    if not path.is_dir():
        return None
    common = _git(path, "rev-parse", "--git-common-dir")
    return (path / common).resolve() if common else None


def _problem(problems: list, msg: str, fix: str = "") -> None:
    problems.append({"msg": msg, "fix": fix})


# ---------------------------------------------------------------------- collecting

def collect(start: Path, offline: bool = False) -> dict:
    start = Path(start).resolve()
    root = find_repo_root(start)
    data: dict = {"start": str(start), "root": str(root) if root else None,
                  "version": __version__, "problems": []}
    problems = data["problems"]
    if root is None:
        data["git_root"] = _git(start, "rev-parse", "--show-toplevel") or None
        return data
    cfg = load_repo_config(root)
    data["config"] = cfg
    data["repo"] = _repo(root, cfg, problems)
    data["roles"] = _roles(root, cfg, problems)
    data["permissions"] = _permissions(cfg, problems)
    data["prompts"] = _prompts(root, cfg, data["roles"], problems)
    data["machine"] = _machine(root, offline, problems)
    data["server"] = _server(root, cfg, data["machine"], data["roles"], offline, problems)
    return data


def _repo(root: Path, cfg: dict, problems: list) -> dict:
    from .init import bundle_drift, bundle_hive_cmd
    main = cfg["repo"].get("main", "main")
    common = _common_dir(root)
    hook = (common / "hooks" / "pre-commit") if common else None
    hook_text = hook.read_text("utf-8", "replace") if hook and hook.exists() else ""
    gi = root / ".gitignore"
    ignored = gi.exists() and ".hive/orchestrator/" in gi.read_text("utf-8", "replace").split()
    info = {
        "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "origin": _git(root, "remote", "get-url", "origin"),
        "main": main,
        "main_on_origin": bool(_git(root, "rev-parse", "--verify", "-q", f"origin/{main}")),
        "github": cfg["repo"].get("github", ""),
        "hook": "hive" if "hook git-pre-commit" in hook_text else
                ("other" if hook_text else "missing"),
        "orchestrator_memory_ignored": ignored,
        "bundles": (root / ".hive" / "agent").is_dir(),
        "bundle_version": None, "bundle_hive_cmd": bundle_hive_cmd(root), "drift": [],
    }
    if not info["origin"]:
        _problem(problems, "The repository has no `origin` remote; agents integrate by pushing "
                           "to it.", "git remote add origin <url>")
    elif not info["main_on_origin"]:
        _problem(problems, f"origin/{main} doesn't exist locally (`[repo] main` is {main!r}).",
                 "git fetch origin")
    if not ignored:
        _problem(problems, ".hive/orchestrator/ isn't in .gitignore, so the orchestrator's "
                           "private memory could be committed.", "hive init")
    # Each launch generates its agent's bundle (wake.refresh_bundle), so the committed copy in
    # .hive/agent/ is only read by older Hive versions, or if generating fails: its drift is
    # shown, not a problem. A bundle that can't be generated is.
    if info["bundles"]:
        try:
            plugin = json.loads((root / ".hive/agent/claude/.claude-plugin/plugin.json")
                                .read_text("utf-8"))
            info["bundle_version"] = plugin.get("version")
        except (OSError, ValueError):
            pass
    try:
        info["drift"] = bundle_drift(root, cfg) if info["bundles"] else []
    except (prompts.PromptError, ValueError) as e:
        info["drift"] = []
        _problem(problems, f"Hive can't build the agents' files from hive.toml and "
                           f".hive/prompts/: {e}. Launches fall back to the committed "
                           ".hive/agent/ copy.", "fix the file named above")
    if info["drift"]:
        info["drift_why"] = (f"made by Hive {info['bundle_version']}, this is {__version__}"
                             if info["bundle_version"] != __version__
                             else "hive.toml [permissions] or .hive/prompts/ changed since")
    return info


def _roles(root: Path, cfg: dict, problems: list) -> dict:
    custom = cfg.get("roles") or {}
    try:
        catalog = load_roles(custom)
    except ValueError as e:
        _problem(problems, f"hive.toml [roles]: {e}")
        catalog = dict(BUILTIN_ROLES)
    rows = []
    for name, r in catalog.items():
        origin = ("custom" if name not in BUILTIN_ROLES else
                  "built-in, changed" if name in custom else "built-in")
        prompt_from = []
        if r.prompt:
            prompt_from.append("hive.toml" if "prompt" in custom.get(name, {}) else "built-in")
        rf = prompts.prompts_dir(root) / "roles" / f"{name}.md"
        if prompts.has_content(rf):
            prompt_from.append(f"roles/{name}.md")
        rows.append({"name": name, "origin": origin, "mandatory": r.mandatory, "min": r.min,
                     "max": r.max, "can_code": r.can_code, "task_kinds": list(r.task_kinds),
                     "write_globs": list(r.write_globs), "fallback_to": r.fallback_to,
                     "active_in": list(r.active_in), "prompt_from": prompt_from})
    return {"list": rows, "names": list(catalog)}


def _permissions(cfg: dict, problems: list) -> dict:
    perms = cfg.get("permissions") or {}
    out = {"preset": perms.get("preset", "standard"), "allow": list(perms.get("allow") or []),
           "approval": list(perms.get("approval") or []), "deny": list(perms.get("deny") or []),
           "classes": {}}
    try:
        rules = permissions.build_rules(cfg)
    except ValueError as e:
        _problem(problems, f"hive.toml [permissions]: {e}")
        return out
    out["classes"] = {cls: {k: len(v) for k, v in r.items()} for cls, r in rules.items()}
    return out


def _prompts(root: Path, cfg: dict, roles: dict, problems: list) -> list[dict]:
    pdir = prompts.prompts_dir(root)
    files = [(n, pdir / n, "skill") for n in prompts.SKILL_FILES.values()]
    rdir = pdir / "roles"
    if rdir.is_dir():
        files += [(f"roles/{p.name}", p, "role") for p in sorted(rdir.glob("*.md"))]
    rows = []
    for name, path, kind in files:
        try:
            text = prompts._read(path, strict=True)
            status = ("missing" if not path.exists() else
                      "active" if text else "comments only")
        except prompts.PromptError:
            text, status = "", "too large"
        rows.append({"file": name, "status": status, "kind": kind,
                     "lines": len(text.splitlines()), "bytes": len(text.encode("utf-8"))})
    for p in prompts.check(root, cfg, roles["names"]):
        _problem(problems, p)
    return rows


def _machine(root: Path, offline: bool, problems: list) -> dict:
    mcfg = load_machine_config()
    info = {"config": str(machine_config_path()), "exists": machine_config_path().exists(),
            "server_url": mcfg.server_url if machine_config_path().exists() else "",
            "local_url": mcfg.local_url, "daemon": None, "slots": [], "other_slots": 0,
            "admin": bool(load_admin_config().token), "home": str(hive_home())}
    mine = _common_dir(root)
    live = {}
    if not info["exists"]:
        info["daemon"] = "no slots here"
    elif not offline:
        try:
            r = requests.get(mcfg.local_url + "/slots", timeout=1,
                             headers={"X-Hive-Local": mcfg.local_secret})
            if r.ok:
                info["daemon"] = "running"
                live = {s["name"]: s for s in r.json()}
            else:
                info["daemon"] = f"answers HTTP {r.status_code}"
        except requests.RequestException:
            info["daemon"] = "not running"
    for s in mcfg.slots:
        wt = Path(s.worktree)
        if not (mine and _common_dir(wt) == mine):
            info["other_slots"] += 1
            continue
        account = Path(s.config_dir) if s.config_dir else None
        row = {"name": s.name, "vendor": s.vendor, "worktree": s.worktree,
               "worktree_ok": wt.is_dir(), "branch": _git(wt, "rev-parse", "--abbrev-ref", "HEAD")
               if wt.is_dir() else "", "account": s.config_dir,
               "account_used": bool(account and account.is_dir() and any(account.iterdir())),
               "permission_mode": s.permission_mode or "default", "token": s.token,
               "server_url": mcfg.url_for(s), "live": live.get(s.name)}
        info["slots"].append(row)
        if not row["worktree_ok"]:
            _problem(problems, f"Slot {s.name}'s worktree {s.worktree} doesn't exist.",
                     f"hive join {s.name} --vendor {s.vendor} --token <token> --repo <clone>")
        elif row["branch"] != f"hive/{s.name}":
            _problem(problems, f"Slot {s.name}'s worktree is on {row['branch'] or '?'}, not "
                               f"hive/{s.name}.")
        if not row["account_used"]:
            _problem(problems, f"Slot {s.name}'s account folder is empty: its CLI has probably "
                               "never been logged in.", f"hive agents login {s.name}")
    urls = list(dict.fromkeys(r["server_url"].rstrip("/") for r in info["slots"]))
    if urls:
        info["server_url"] = urls[0]
    if len(urls) > 1:
        _problem(problems, "This project's slots on this machine use different servers or "
                           f"projects ({', '.join(urls)}); they won't see each other.",
                 f"hive join <slot> --replace --server {urls[0]} --token <token> ...")
    seen = {}
    for row in info["slots"]:
        if row["worktree"] in seen:
            _problem(problems, f"Slots {seen[row['worktree']]} and {row['name']} share the "
                               f"worktree {row['worktree']}; each slot needs its own.")
        seen.setdefault(row["worktree"], row["name"])
    return info


def _same(a: str, b: str) -> bool:
    return a.rstrip("/").lower() == b.rstrip("/").lower()


def _server(root: Path, cfg: dict, machine: dict, roles: dict, offline: bool,
            problems: list) -> dict:
    from .daemon.client import HiveError, ServerClient
    # Which server (and project on it) is really in use. Agents connect with their slot's
    # address in machine.toml (`hive join --server`). Admin commands use admin.toml's
    # (`hive admin login`), or the project hive.toml's [server] url names (…/p/<project>).
    admin = load_admin_config()
    admin_url, admin_token = admin.target(start=root)
    toml_url = cfg["server"].get("url", "")
    agents_url = machine["server_url"] if machine["slots"] else ""
    if agents_url:
        url, source = agents_url, "this project's slots on this machine (machine.toml)"
    elif admin_url:
        url, source = admin_url, "your admin login (admin.toml)"
    else:
        url, source = toml_url, "hive.toml (no slots or admin login on this machine)"
    info = {"url": url, "source": source, "toml_url": toml_url, "agents_url": agents_url,
            "admin_url": admin_url, "reachable": None, "auth": None, "fleet": None,
            "agents": [], "tasks": {}, "leases": 0, "error": ""}
    if agents_url and admin_url and not _same(agents_url, admin_url):
        _problem(problems, f"This project's agents here use {agents_url}, but your admin "
                           f"commands use {admin_url}: they manage different fleets.",
                 f"hive login --server {agents_url}")
    if toml_url and url and not _same(toml_url, url):
        _problem(problems, f"hive.toml's [server] url ({toml_url}) is out of date; this project "
                           f"uses {url}. It tells people setting up new machines where the "
                           "server is, and admin commands run here use it when it names a "
                           "project.",
                 f'set [server] url = "{url}" in hive.toml, then commit')
    if offline or not url:
        return info
    if admin_token and _same(admin_url, url):
        token, info["auth"] = admin_token, "admin login"
    elif agents_url:
        token, info["auth"] = machine["slots"][0]["token"], f"slot {machine['slots'][0]['name']}"
    else:
        token = ""
    try:
        r = requests.get(url.rstrip("/") + "/api/v1/health", timeout=TIMEOUT)
        info["reachable"] = r.ok
        info["health"] = r.json() if r.ok else {}
    except (requests.RequestException, ValueError) as e:
        info["reachable"], info["error"] = False, type(e).__name__
        _problem(problems, f"The server at {url} can't be reached ({info['error']}).")
        return info
    if not token:
        info["error"] = "no credentials on this machine"
        return info
    c = ServerClient(url, token, timeout=TIMEOUT)
    try:
        info["fleet"] = c.get("/fleet")
        info["agents"] = c.get("/agents")
        for t in c.get("/tasks"):
            info["tasks"][t["state"]] = info["tasks"].get(t["state"], 0) + 1
        info["leases"] = len(c.get("/leases"))
    except HiveError as e:
        info["error"] = f"{e.code}: {e.message}"
        _problem(problems, f"The server refused {info['auth']}'s credentials ({info['error']}).")
        return info
    local_review = cfg["integration"].get("review", "off")
    if info["fleet"].get("review") not in (None, local_review):
        _problem(problems, f"The server's review policy ({info['fleet']['review']}) differs "
                           f"from hive.toml ({local_review}): the uploaded config is stale.",
                 "hive status  (any admin command uploads a changed hive.toml)")
    enrolled = {a["name"] for a in info["agents"]}
    for s in machine["slots"]:
        if s["name"] not in enrolled:
            _problem(problems, f"Slot {s['name']} is set up on this machine but isn't enrolled "
                               "on the server.", f"hive admin enroll {s['name']} ...")
    held = {r for a in info["agents"] for r in a["roles"]}
    for r in roles["list"]:
        count = sum(1 for a in info["agents"] if r["name"] in a["roles"])
        if (r["mandatory"] or r["min"]) and count < max(r["min"], 1 if r["mandatory"] else 0):
            _problem(problems, f"Role {r['name']} needs at least {max(r['min'], 1)} agent(s); "
                               f"{count} enrolled.",
                     f"hive admin enroll NAME --vendor claude --roles {r['name']}")
    info["held_roles"] = sorted(held)
    return info


# ---------------------------------------------------------------------- rendering

def _yes(flag: bool, yes: str = "yes", no: str = "no") -> str:
    return ui.state("ok", label=yes) if flag else ui.state("failed", label=no)


def render(d: dict) -> None:
    ui.banner("Hive overview", d["root"] or d["start"])
    if d["root"] is None:
        ui.out()
        ui.warn("This folder isn't a Hive project: no hive.toml here or in any parent folder.",
                sys.stdout)
        ui.out("  " + ("Wire this repository for Hive with " + ui.cmd("hive init")
                       if d.get("git_root") else "Run it inside a git repository, then "
                       + ui.cmd("hive init")))
        ui.out()
        return
    cfg, repo = d["config"], d["repo"]

    ui.section("Repository")
    ui.kv([("root", ui.path(d["root"])),
           ("branch", repo["branch"] or ui.dim("?")),
           ("origin", repo["origin"] or ui.state("failed", label="none")),
           ("integrates into", f"{repo['main']}" + ("" if repo["main_on_origin"]
                                                     else ui.dim("  (origin/" + repo["main"]
                                                                 + " not fetched)"))),
           ("GitHub mode", repo["github"] or ui.dim("off (agents push main themselves)")),
           ("commit hook", {"hive": ui.state("ok", label="installed"),
                            "other": ui.state("pending", label="another hook is installed"),
                            "missing": ui.dim("not installed (hive join installs it)")}
            [repo["hook"]]),
           ("agent bundles", ui.state("ok", label="generated at each launch")
            + (ui.dim(f"  (committed .hive/agent/: {len(repo['drift'])} file(s) older, "
                      f"{repo['drift_why']}; only older Hive versions read it)")
               if repo["drift"] else
               ui.dim(f"  (committed .hive/agent/ from Hive {repo['bundle_version']} matches)")
               if repo["bundles"] else ""))])

    ui.section("Configuration", ui.dim("hive.toml"))
    integ, orch = cfg["integration"], cfg["orchestrator"]
    ui.kv([("server", ui.path(cfg["server"].get("url", "")) + ui.dim("  (informational)")),
           ("review", str(integ.get("review", "off"))),
           ("checks", "\n".join(integ.get("checks") or []) or ui.dim("none")),
           ("checks shell", str(integ.get("shell", "native"))),
           ("standby", ", ".join(orch.get("standby") or []) or ui.dim("none")),
           ("debounce", f"{orch.get('debounce_seconds', 90)} s"),
           ("heartbeat", f"every {orch.get('heartbeat_hours', 4)} h")])

    perms = d["permissions"]
    ui.section("Shell-command permissions", ui.dim(f"preset {perms['preset']}"))
    if perms["classes"]:
        ui.table(["class", "allowed", "need approval", "never"],
                 [[cls, n["allow"], n["approval"], n["forbidden"]]
                  for cls, n in perms["classes"].items()])
    extra = [("extra allow", ", ".join(perms["allow"])),
             ("extra approval", ", ".join(perms["approval"])),
             ("extra deny", ", ".join(perms["deny"]))]
    if any(v for _, v in extra):
        ui.out()
        ui.kv(extra)

    ui.section("Roles", ui.dim(f"{len(d['roles']['list'])} defined"))
    rows = []
    for r in d["roles"]["list"]:
        count = (f"{r['min']}+" if r["max"] is None else
                 str(r["max"]) if r["min"] == r["max"] else f"{r['min']}-{r['max']}")
        rows.append([ui.bold(r["name"]),
                     ui.dim(r["origin"]),
                     ui.state("pending", label="required") if r["mandatory"] else ui.dim("optional"),
                     count,
                     "code" if r["can_code"] else ui.dim(", ".join(r["write_globs"]) or "no writes"),
                     ", ".join(r["task_kinds"]) or ui.dim("-"),
                     " + ".join(r["prompt_from"]) or ui.dim("none")])
    ui.table(["role", "origin", "need", "holders", "writes", "task kinds", "prompt"], rows)

    ui.section("Project prompts", ui.dim(prompts.PROMPTS_DIR.as_posix()))
    labels = {"active": lambda r: ui.state("ok", label=f"active, {r['lines']} line(s)"),
              "comments only": lambda r: ui.dim("comments only (adds nothing)"),
              "missing": lambda r: ui.dim("missing"),
              "too large": lambda r: ui.state("failed", label="too large")}
    ui.table(["file", "status", "goes into"],
             [[r["file"], labels[r["status"]](r),
               ("the " + ("orchestrator" if r["file"] == "orchestrator.md" else "worker")
                + " skill") if r["kind"] == "skill" else "session start"]
              for r in d["prompts"]])

    m = d["machine"]
    ui.section("This machine", ui.dim(m["home"]))
    ui.kv([("machine config", ui.path(m["config"]) if m["exists"] else ui.dim("none (no slots "
                                                                            "joined here)")),
           ("hive up", {None: ui.dim("not checked"), "running": ui.state("ok", label="running"),
                        "not running": ui.dim("not running"),
                        "no slots here": ui.dim("-")}.get(m["daemon"], m["daemon"])),
           ("admin login", "yes" if m["admin"] else ui.dim("no")),
           ("other slots", str(m["other_slots"]) + ui.dim(" (other repositories)")
            if m["other_slots"] else "")])
    if m["slots"]:
        ui.out()
        rows = []
        for s in m["slots"]:
            live = s["live"]
            state = (ui.dim("-") if m["daemon"] != "running" else
                     ui.state("failed", label="not loaded") if not live else
                     ui.state("busy", label="working") if live["running"] else
                     ui.state("ok", label="interactive") if live["attached"] else
                     ui.state("online", label="idle"))
            rows.append([ui.agent(s["name"]), s["vendor"], state,
                         s["branch"] or ui.state("failed", label="missing"),
                         _yes(s["account_used"], "logged in", "not logged in"),
                         ui.path(s["worktree"])])
        ui.table(["slot", "vendor", "now", "branch", "account", "worktree"], rows)

    srv = d["server"]
    ui.section("Fleet", ui.dim(srv["url"]))
    ui.kv([("server", ui.path(srv["url"]) + ui.dim(f"  from {srv['source']}")),
           ("agents use", ui.path(srv["agents_url"]) if srv["agents_url"]
            and not _same(srv["agents_url"], srv["url"]) else ""),
           ("admin uses", ui.path(srv["admin_url"]) if srv["admin_url"]
            and not _same(srv["admin_url"], srv["url"]) else ""),
           ("hive.toml says", ui.path(srv["toml_url"]) + ui.dim("  (informational)")
            if srv["toml_url"] and not _same(srv["toml_url"], srv["url"]) else "")])
    if srv["reachable"] is None:
        ui.out(ui.dim("  Not checked (--offline)."))
    elif not srv["reachable"]:
        ui.out("  " + ui.state("failed", label=f"Unreachable ({srv['error']})"))
    elif srv["fleet"] is None:
        ui.out("  " + ui.state("ok", label="Reachable") + ui.dim(
            f"; {srv['error'] or 'no details'}. Log in with `hive admin login` to see the "
            "fleet."))
    else:
        f = srv["fleet"]
        online = sum(1 for a in srv["agents"] if a["online"])
        open_tasks = sum(n for s, n in srv["tasks"].items() if s not in ("done", "cancelled"))
        ui.kv([("state", ui.state(f["state"])),
               ("goal", (f.get("goal") or ui.dim("none"))[:200]),
               ("orchestrator", ui.agent(f.get("orchestrator"))),
               ("main", ui.paint((f.get("main_sha") or "")[:10], "yellow") or ui.dim("-")),
               ("agents", f"{online}/{len(srv['agents'])} online"),
               ("tasks", f"{open_tasks} open" + ui.dim(
                   "  (" + ", ".join(f"{n} {s}" for s, n in sorted(srv["tasks"].items())) + ")"
                   if srv["tasks"] else "")),
               ("leases", f"{srv['leases']} active"),
               ("seen as", ui.dim(srv["auth"]))])
        if srv["agents"]:
            ui.out()
            here = {s["name"] for s in d["machine"]["slots"]}
            ui.table(["agent", "vendor", "roles", "state", "where"],
                     [[ui.agent(a["name"]), a["vendor"], ", ".join(a["roles"]),
                       ui.state("online" if a["online"] else "offline"),
                       "this machine" if a["name"] in here else ui.dim("elsewhere")]
                      for a in srv["agents"]])

    ui.section("Problems", ui.dim(str(len(d["problems"])) if d["problems"] else "none"))
    if not d["problems"]:
        ui.ok("Nothing to fix.")
    for p in d["problems"]:
        ui.warn(p["msg"], sys.stdout)
        if p["fix"]:
            ui.out("     " + ui.cmd(p["fix"]))
    ui.out()
