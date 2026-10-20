"""`hive` command-line interface. SPEC §17."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

from . import __version__, ui
from .config import (VENDORS, AdminConfig, Slot, find_repo_root, hive_home, load_admin_config,
                     load_machine_config, load_repo_config, machine_config_path,
                     save_admin_config, save_machine_config)
from .daemon.client import HiveError, ServerClient


def die(msg: str) -> None:
    ui.error(msg)
    raise SystemExit(1)


def admin_client() -> ServerClient:
    cfg = load_admin_config()
    if not cfg.server_url or not cfg.token:
        die("not logged in as admin; run `hive admin login --server URL --token TOKEN`")
    return ServerClient(cfg.server_url, cfg.token)


def call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except HiveError as e:
        detail = e.data.get("problems") or e.data.get("conflicts")
        if isinstance(detail, list):
            detail = "\n".join(f"  - {d if isinstance(d, str) else json.dumps(d)}"
                               for d in detail)
        elif detail:
            detail = json.dumps(detail, indent=1)
        die(f"{ui.paint(e.code, 'bold', stream=sys.stderr)}: {e.message}"
            + (f"\n{ui.dim(detail, sys.stderr)}" if detail else ""))


ago = ui.ago


# ---------------------------------------------------------------------- server & admin

def cmd_server(a):
    os.environ.setdefault("HIVE_DATABASE_URL", a.db)
    if a.sqlite_journal:
        os.environ["HIVE_SQLITE_JOURNAL"] = a.sqlite_journal
    if a.admin_token:
        os.environ["HIVE_ADMIN_TOKEN"] = a.admin_token
    if not os.environ.get("HIVE_ADMIN_TOKEN"):
        die("set HIVE_ADMIN_TOKEN (or pass --admin-token)")
    from .server.app import create_app
    app = create_app()
    ui.banner(f"Hive server {__version__}", "development server; see docs/DEPLOYMENT.md")
    ui.kv([("listening", ui.path(f"http://{a.host}:{a.port}")),
           ("dashboard", ui.path(f"http://{a.host}:{a.port}/")),
           ("database", os.environ["HIVE_DATABASE_URL"])])
    ui.out(ui.dim("  Ctrl+C to stop.") + "\n")
    app.run(host=a.host, port=a.port, threaded=True)


def cmd_admin_login(a):
    c = ServerClient(a.server, a.token)
    call(c.get, "/fleet")
    save_admin_config(AdminConfig(a.server, a.token))
    ui.ok(f"Logged in to {ui.path(a.server)} as admin.")


def cmd_admin_enroll(a):
    c = admin_client()
    roles = [r.strip() for r in a.roles.split(",") if r.strip()]
    r = call(c.post, "/agents", {"name": a.name, "vendor": a.vendor, "roles": roles})
    ui.ok(f"Enrolled {ui.agent(r['name'])} ({a.vendor}; roles: {', '.join(roles)}).")
    ui.kv([("token", ui.paint(r["token"], "bold", "bright_yellow")),
           ("", ui.dim("shown only once; treat it like a password"))])
    ui.section("On the agent's machine run")
    ui.out("  " + ui.cmd(f"hive join {r['name']} --vendor {a.vendor} --token {r['token']} "
                         f"--server {c.base.removesuffix('/api/v1')} --repo <path-to-clone>"))


def cmd_admin_roles(a):
    c = admin_client()
    roles = [r.strip() for r in a.roles.split(",") if r.strip()]
    r = call(c.patch, f"/agents/{a.name}", {"roles": roles})
    if r.get("status") == "proposed":
        did = r.get("decision_id")
        ui.warn(f"The fleet is active, so this became decision #{did}; approve it with "
                + ui.cmd(f"hive decide {did} approve"), sys.stdout)
    else:
        ui.ok(f"{ui.agent(a.name)} now has roles: {', '.join(roles)}.")


def cmd_admin_backup(a):
    import requests
    cfg = load_admin_config()
    if not cfg.server_url or not cfg.token:
        die("not logged in as admin; run `hive admin login --server URL --token TOKEN`")
    out = Path(a.output or time.strftime("hive-backup-%Y%m%d-%H%M%S.db"))
    try:
        with requests.get(cfg.server_url.rstrip("/") + "/api/v1/admin/backup", stream=True,
                          headers={"Authorization": f"Bearer {cfg.token}"}, timeout=300) as r:
            if r.status_code != 200:
                try:
                    detail = r.json().get("message", r.text)
                except ValueError:
                    detail = r.text[:300]
                die(f"backup failed ({r.status_code}): {detail}")
            tmp = out.with_suffix(out.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            os.replace(tmp, out)
    except requests.RequestException as e:
        die(f"backup failed: {e}")
    ui.ok(f"Saved {ui.path(out)} ({out.stat().st_size / 1e6:.1f} MB).")


def cmd_admin_push_config(a):
    root = find_repo_root() or die("no hive.toml found (run `hive init`)")
    with open(root / "hive.toml", "rb") as f:
        data = tomllib.load(f)
    call(admin_client().put, "/config", data)
    ui.ok("Uploaded hive.toml to the server.")


# ---------------------------------------------------------------------- repo & machine setup

def cmd_init(a):
    from .init import init_repo
    from .prompts import PromptError
    root = Path(a.path).resolve()
    if not (root / ".git").exists():
        die(f"{root} is not a git repository")
    # hive.toml's [server] url is informational: record the server this machine really uses.
    mcfg = load_machine_config() if machine_config_path().exists() else None
    server = (a.server or load_admin_config().server_url or (mcfg.server_url if mcfg else "")
              or "http://127.0.0.1:8765")
    try:
        written, cleaned = init_repo(root, server, a.main, a.hive_cmd)
    except PromptError as e:
        die(f"{e}. Nothing in .hive/agent/ was changed.")
    ui.section("Wired for Hive", ui.dim(str(root)))
    for f in written:
        ui.out(f"  {ui.paint('+', 'green', 'bold')} {f}")
    for f in cleaned:
        ui.out(f"  {ui.paint('-', 'yellow', 'bold')} {f} {ui.dim('(old hive wiring removed)')}")
    ui.out()
    ui.info("The agent bundles in .hive/agent/ load only in sessions launched by `hive`.")
    _prompt_problems(root)
    ui.section("Next")
    for i, t in enumerate(["Review " + ui.path("hive.toml") + " (roles, checks, permissions).",
                           "Commit these files and push.",
                           "Upload the config: " + ui.cmd("hive admin push-config")], 1):
        ui.out(f"  {ui.num(f'{i}.')} {t}")


def _prompt_problems(root: Path) -> None:
    from . import prompts
    from .roles import load_roles
    cfg = load_repo_config(root)
    try:
        roles = load_roles(cfg.get("roles"))
    except ValueError:
        return  # hive.toml's own problems are reported where it is used
    for p in prompts.check(root, cfg, roles):
        ui.warn(p)


def cmd_prompts(a):
    """Show what Hive tells an agent with the given roles: its skill (built-in text plus the
    project's rules) and the text added at the start of each of its sessions."""
    from . import prompts
    from .init import ORCHESTRATOR_SKILL, WORKER_SKILL
    from .roles import load_roles
    root = find_repo_root(Path(a.path).resolve())
    if not root:
        die(f"no hive.toml in {Path(a.path).resolve()} or above; run `hive init` first")
    cfg = load_repo_config(root)
    try:
        catalog = load_roles(cfg.get("roles"))
    except ValueError as e:
        die(f"hive.toml: {e}")
    roles = [r.strip() for r in (a.role or "programmer").split(",") if r.strip()]
    missing = [r for r in roles if r not in catalog]
    if missing:
        die(f"unknown role(s) {', '.join(missing)}; roles: {', '.join(sorted(catalog))}")
    skill = "hive-orchestrator" if "orchestrator" in roles else "hive-worker"
    builtin = ORCHESTRATOR_SKILL if skill == "hive-orchestrator" else WORKER_SKILL
    try:
        addition = prompts.skill_addition(root, skill, cfg)
    except prompts.PromptError as e:
        die(str(e))
    pfile = f"{prompts.PROMPTS_DIR.as_posix()}/{prompts.SKILL_FILES[skill]}"

    ui.section(f"Skill: {skill}", ui.dim(f"roles {', '.join(roles)}"))
    if a.full:
        ui.out(builtin.rstrip())
    else:
        ui.out(ui.dim(f"  (built-in text, {len(builtin.splitlines())} lines; --full shows it)"))
    ui.out(addition.rstrip() if addition else
           ui.dim(f"  No project rules: {pfile} is missing or has only comments."))
    on_disk = root / ".hive" / "agent" / "claude" / "skills" / skill / "SKILL.md"
    if on_disk.exists() and on_disk.read_text("utf-8") != builtin + addition:
        ui.warn(f"{on_disk.relative_to(root).as_posix()} differs from this. Run "
                + ui.cmd("hive init") + " and commit .hive/agent/ to update it.")

    ui.section("Added at the start of each session")
    role_text = "\n\n".join(catalog[r].prompt for r in roles if catalog[r].prompt)
    extra = prompts.session_addition(root, roles, cfg, slot=a.slot or "<slot>",
                                     vendor=a.vendor)
    if role_text:
        ui.out(ui.dim("  From hive.toml (role prompts; agents get them from the server after "
                      "`hive admin push-config`):"))
        ui.out(role_text)
    if extra:
        files = ", ".join(f"{prompts.PROMPTS_DIR.as_posix()}/roles/{r}.md"
                          for r, path in prompts.role_files(root, roles)
                          if prompts.has_content(path))
        ui.out(ui.dim(f"  From {files} (read from each agent's checkout of main):"))
        ui.out(extra)
    if not role_text and not extra:
        ui.out(ui.dim("  Nothing beyond Hive's built-in session header."))
    for p in prompts.check(root, cfg, catalog):
        ui.warn(p)


def cmd_overview(a):
    from . import overview
    overview.render(overview.collect(Path(a.path), offline=a.offline))


def cmd_join(a):
    from .daemon import gitops
    from .init import install_git_hook
    cfg = load_machine_config()
    if a.server:
        cfg.server_url = a.server
    c = ServerClient(cfg.server_url, a.token)
    me = call(c.get, "/agents/me")
    if me["name"] != a.name:
        die(f"token belongs to {me['name']!r}, not {a.name!r}")
    if a.vendor == "codex" and a.permission_mode:
        from .daemon.wake import CODEX_MODES
        if a.permission_mode not in CODEX_MODES:
            die(f"codex --permission-mode must be one of {', '.join(CODEX_MODES)}")
    if a.worktree:
        wt = Path(a.worktree).resolve()
    elif a.repo:
        repo = Path(a.repo).resolve()
        wt = repo.parent / f"{repo.name}-{a.name}"
    else:
        die("pass --repo <clone> (a worktree is created next to it) or --worktree <path>")
    if a.repo:
        repo = Path(a.repo).resolve()
        main = load_repo_config(repo)["repo"].get("main", "main")
        try:
            gitops.ensure_worktree(repo, wt, f"hive/{a.name}", main)
        except gitops.GitError as e:
            die(str(e))
        hook = install_git_hook(repo, a.hive_cmd)
        if hook:
            ui.ok(f"Installed the git pre-commit hook at {ui.path(hook)}")
    config_dir = a.config_dir or str(hive_home() / "accounts" / a.name)
    Path(config_dir).mkdir(parents=True, exist_ok=True)
    cfg.slots = [s for s in cfg.slots if s.name != a.name] + [Slot(
        name=a.name, vendor=a.vendor, token=a.token, worktree=str(wt), config_dir=config_dir,
        permission_mode=a.permission_mode or "", model=a.model or "")]
    save_machine_config(cfg)
    var = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}.get(a.vendor, "GEMINI_CLI_HOME")
    ui.ok(f"Slot {ui.agent(a.name)} ready.")
    ui.kv([("worktree", ui.path(wt)), ("account", ui.path(config_dir))]
          + ([("model", a.model)] if a.model else []))
    ui.section("Log this slot's account in once (interactive)")
    # Codex has a login command; `--device-auth` also works over SSH, without a local browser.
    ui.out(login_instructions(var, config_dir, str(wt),
                              "codex login" if a.vendor == "codex" else a.vendor))
    ui.out(f"  {ui.dim('or, while `hive up` runs:')}  {ui.cmd(f'hive chat {a.name}')}")
    ui.section("Then start the machine")
    ui.out("  " + ui.cmd("hive up"))


def login_instructions(var: str, config_dir: str, worktree: str, cli: str) -> str:
    """Shell-appropriate commands to start a CLI with a private config folder."""
    if os.name == "nt":
        return (f"  {ui.dim('PowerShell')}  "
                + ui.cmd(f"cd \"{worktree}\"; $env:{var} = \"{config_dir}\"; {cli}; "
                         f"Remove-Item Env:{var}") + "\n"
                f"  {ui.dim('cmd.exe   ')}  "
                + ui.cmd(f"cd /d \"{worktree}\" && set \"{var}={config_dir}\" && {cli}"))
    return "  " + ui.cmd(f"cd '{worktree}' && {var}='{config_dir}' {cli}")


def run_interactive(slot: Slot, cfg, fresh: bool = False, initial: str | None = None,
                    orchestrator: bool = False) -> int:
    """Launch the slot's native CLI in this terminal with Hive attached for this session only."""
    import uuid
    from .daemon import wake
    from .daemon.store import Store
    store = Store(hive_home() / "state.db")
    session = None if fresh else store.get(slot.name, "session")
    resume = bool(session)
    if slot.vendor != "codex":  # Codex picks its own session id; it is read back afterwards
        session = session or str(uuid.uuid4())
        store.put(slot.name, "session", session)
    env = wake.slot_env(slot, cfg.local_url, cfg.local_secret)
    role = "orchestrator" if orchestrator else "worker"
    err = wake.prepare_vendor(slot, env, role)
    if err:
        ui.warn(f"{err}. Hive tools will be missing in this session.")
    if initial is None and orchestrator:
        initial = wake.ORCH_RESUME_PROMPT if resume else wake.ORCH_FIRST_PROMPT
    cmd = wake.build_interactive_command(slot, session or "", resume, initial, role)
    started = time.time()
    p = subprocess.run(wake.resolve(cmd), cwd=slot.worktree, env=env)
    if p.returncode != 0 and resume:
        # The stored session may be gone (e.g. different machine); start a fresh one next time.
        store.put(slot.name, "session", None)
    if slot.vendor == "codex" and slot.config_dir:
        found = wake.codex_latest_session(slot.config_dir, started)
        if found:
            store.put(slot.name, "session", found)
        elif not resume:
            store.put(slot.name, "session", None)
    return p.returncode


def refresh_git_hooks(cfg) -> None:
    """Rewrite Hive's pre-commit hook in each slot's repository (older versions blocked the
    user's own commits when `hive` was not on PATH). Foreign hooks are left alone."""
    from .init import install_git_hook
    seen = set()
    for s in cfg.slots:
        if not s.worktree or s.worktree in seen or not Path(s.worktree).exists():
            continue
        seen.add(s.worktree)
        try:
            install_git_hook(Path(s.worktree), quiet=True)
        except (OSError, subprocess.CalledProcessError):
            pass


def cmd_up(a):
    from .daemon.core import Daemon
    from .daemon.localapi import serve
    hive_home().joinpath("logs").mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(hive_home() / "logs" / "daemon.log", encoding="utf-8")]
    handlers[0].setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    cfg = load_machine_config()
    if not cfg.slots:
        die("no slots on this machine; run `hive join` first")
    save_machine_config(cfg)  # persists a generated local secret
    refresh_git_hooks(cfg)
    d = Daemon(cfg)
    # Each slot's roles decide how it runs, so they must be known before anything starts.
    ui.step(f"Connecting to {ui.path(cfg.server_url)} ...")
    missing = d.wait_for_identity(timeout=30)
    if missing:
        die(f"could not reach the Hive server at {cfg.server_url} for {', '.join(missing)} "
            "(is it running, and are the tokens in ~/.hive/machine.toml valid?)")
    orch = None if a.headless else next(
        (rt for rt in d.slots.values() if rt.is_orchestrator), None)
    if orch:
        # Mark it attached before the wake loop starts, so no headless turn can race the
        # interactive session on the same conversation.
        orch.attached = True
        d.set_echo(False)
    d.start_background()
    if not orch:
        console = logging.StreamHandler()
        console.setFormatter(ui.LogFormatter(console.stream))
        handlers.append(console)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, handlers=handlers)
    srv = serve(d)
    ui.banner(f"Hive daemon {__version__}", "orchestrator mode" if orch else "worker mode")
    ui.kv([("server", ui.path(cfg.server_url)),
           ("log", ui.path(hive_home() / "logs" / "daemon.log"))])
    ui.out()
    ui.table(["slot", "vendor", "roles"],
             [[ui.agent(s.name), s.cfg.vendor, ", ".join(s.roles) or "?"]
              for s in d.slots.values()])
    if not orch:
        ui.out()
        ui.info("Streaming agent output below (the admin also sees it with "
                + ui.cmd("hive watch") + "). Ctrl+C to stop.")
        ui.out()
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            d.stop()
        return

    # Orchestrator mode: the terminal belongs to the native CLI; workers on this machine keep
    # running headless and stream to the server (watch them with `hive watch` elsewhere).
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    others = [n for n in d.slots if n != orch.name]
    if others:
        ui.info(f"Workers on this machine ({', '.join(ui.agent(n) for n in others)}) run in "
                f"the background; watch them with {ui.cmd('hive watch')}.")
    try:
        while True:
            ui.step(f"Launching {orch.cfg.vendor} as orchestrator {ui.agent(orch.name)} ...")
            ui.out()
            run_interactive(orch.cfg, cfg, orchestrator=True)
            ui.out()
            ans = ui.ask("Orchestrator session ended. Relaunch? [Y/n]", "n").lower()
            if ans in ("n", "no", "q"):
                break
    except KeyboardInterrupt:
        pass
    finally:
        orch.attached = False
        srv.shutdown()
        d.stop()


def cmd_chat(a):
    import requests
    cfg = load_machine_config()
    slot = next((s for s in cfg.slots if s.name == a.slot), None) or die(f"no slot {a.slot}")
    hdr = {"X-Hive-Local": cfg.local_secret}
    is_orch = False
    try:
        r = requests.post(f"{cfg.local_url}/slot/{slot.name}/attach", json={"attached": True},
                          headers=hdr, timeout=5).json()
        if r.get("running"):
            ui.note("A headless wake is running for this slot; it will finish first.")
        slots = requests.get(f"{cfg.local_url}/slots", headers=hdr, timeout=5).json()
        is_orch = any(s["name"] == slot.name and "orchestrator" in s["roles"] for s in slots)
    except (requests.RequestException, ValueError, KeyError):
        ui.warn("The daemon is not reachable; hooks and tools will not work until "
                "`hive up` runs.")
    try:
        # initial="" → no automatic first prompt; the role still selects the permission file.
        run_interactive(slot, cfg, fresh=a.fresh, initial="", orchestrator=is_orch)
    finally:
        try:
            requests.post(f"{cfg.local_url}/slot/{slot.name}/attach", json={"attached": False},
                          headers=hdr, timeout=5)
        except requests.RequestException:
            pass


def cmd_watch(a):
    """Follow agents' live output (admin only; agents can never read streams)."""
    c = admin_client()
    last = 0
    first = True
    name_width = 0
    try:
        while True:
            r = call(c.get, "/streams", agent=a.agent, since_id=last,
                     limit=a.n if first else 1000)
            if r["lines"]:
                if not a.agent:
                    name_width = max([name_width] + [len(l["agent"]) for l in r["lines"]])
                ui.out("\n".join(ui.stream_line(None if a.agent else l["agent"], l["text"],
                                                name_width=name_width) for l in r["lines"]))
            last, first = r["last_id"], False
            if not a.follow:
                return
            time.sleep(1.5)
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------- user operations

def cmd_start(a):
    c = admin_client()
    root = find_repo_root()
    if root:
        with open(root / "hive.toml", "rb") as f:
            call(c.put, "/config", tomllib.load(f))
    f = call(c.post, "/fleet/start", {"goal": a.goal})
    ui.ok(f"Fleet is {ui.state(f['state'])}; orchestrator {ui.agent(f['orchestrator'])} is "
          "planning.")
    ui.note(f"You will be asked to approve the plan: {ui.cmd('hive decisions')}")


def cmd_decisions(a):
    rows = call(admin_client().get, "/decisions", state=None if a.all else "open,queued")
    if not rows:
        ui.ok("No open decisions.")
    for d in rows:
        tags = [ui.state(d["state"]), d["kind"]]
        if d["blocking"]:
            tags.append(ui.paint("freezes the fleet", "red", "bold"))
        ui.section(f"Decision #{d['id']}", " · ".join(tags))
        ui.out(ui.dim(f"  from {d['raised_by']}, {ago(d['created'])} ago"))
        ui.out()
        ui.out("  " + ui.bold(d["question"].replace("\n", "\n  ")))
        if d["context"]:
            ui.out()
            ui.out("  " + ui.paint("Context", "gray", "underline"))
            ui.out("  " + d["context"].replace("\n", "\n  "))
        if d["answer"]:
            ui.out()
            ui.kv([("answer", ui.paint(d["answer"], "green", "bold")), ("note", d["note"])])
        elif d["options"]:
            ui.out()
            ui.out("  " + ui.paint("Answer with", "gray", "underline"))
            for o in d["options"]:
                ui.out("    " + ui.cmd(f"hive decide {d['id']} {_quote(o)}"))
        else:
            ui.out()
            ui.out("  " + ui.paint("Answer with", "gray", "underline") + "  "
                   + ui.cmd(f"hive decide {d['id']} \"<your answer>\""))
    if rows:
        ui.out()


def _quote(arg: str) -> str:
    return arg if arg and all(ch.isalnum() or ch in "-_.:/" for ch in arg) else f'"{arg}"'


def cmd_decide(a):
    d = call(admin_client().post, f"/decisions/{a.id}/answer", {"answer": a.answer,
                                                                "note": a.note or ""})
    ui.ok(f"Decision #{d['id']} answered: {ui.paint(d['answer'], 'green', 'bold')}")


def cmd_say(a):
    call(admin_client().post, "/messages", {"channel": "#user", "body": a.text,
                                            "priority": "high"})
    ui.ok("Sent to #user; the orchestrator will be woken.")


def _agent_state(ag: dict) -> str:
    if ag["exhausted"]:
        return ui.state("exhausted", label="out of quota")
    return ui.state(ag["status"] if ag["online"] else "offline")


def cmd_status(a):
    c = admin_client()
    f = call(c.get, "/fleet")
    ui.section("Fleet", ui.state(f["state"]) + ui.dim(f"  since {ago(f['since'])} ago"))
    ui.kv([("orchestrator", ui.agent(f["orchestrator"])),
           ("review", str(f.get("review") or "")),
           ("main", ui.paint((f["main_sha"] or "")[:10], "yellow")),
           ("goal", (f.get("goal") or "")[:200])])
    if f.get("decision"):
        ui.out()
        ui.warn(f"Waiting for your decision #{f['decision']['id']}: "
                f"{f['decision']['question'][:200]}", sys.stdout)
        ui.out("    " + ui.cmd("hive decisions"))

    agents = call(c.get, "/agents")
    ui.section("Agents", ui.dim(f"{sum(1 for ag in agents if ag['online'])}/{len(agents)} online"))
    ui.table(["name", "vendor", "roles", "state"],
             [[ui.agent(ag["name"]), ag["vendor"], ", ".join(ag["roles"]), _agent_state(ag)]
              for ag in agents])

    tasks = call(c.get, "/tasks")
    shown = [t for t in tasks if a.all or t["state"] not in ("done", "cancelled")]
    if tasks:
        hidden = len(tasks) - len(shown)
        ui.section("Tasks", ui.dim(f"{len(shown)} shown" + (f", {hidden} finished hidden "
                                                             "(--all)" if hidden else "")))
        if shown:
            ui.table(["id", "state", "assignee", "title"],
                     [[ui.dim(f"#{t['id']}"), ui.state(t["state"]), ui.agent(t["assignee"]),
                       t["title"]] for t in shown])

    leases = call(c.get, "/leases")
    if leases:
        ui.section("Leases", ui.dim(str(len(leases))))
        ui.table(["id", "holder", "expires", "paths"],
                 [[ui.dim(f"#{l['id']}"), ui.agent(l["holder"]),
                   _expires(l["expires_at"]), ", ".join(l["globs"])] for l in leases])

    feed = call(c.get, "/status", limit=a.n)
    if feed:
        ui.section("Status feed", ui.dim(f"latest {len(feed)}"))
        if not a.verbose:
            ui.table(["ago", "level", "by", "summary"],
                     [[ui.dim(ago(s["ts"])), ui.state(s["level"]), ui.agent(s["author"]),
                       s["summary"]] for s in feed])
        for s in feed if a.verbose else []:
            ui.out(f"  {ui.dim(ago(s['ts']) + ' ago')}  {ui.state(s['level'])}  "
                   f"{ui.agent(s['author'])}")
            ui.out("    " + ui.bold(s["summary"]))
            if s["body"]:
                ui.out("    " + s["body"].replace("\n", "\n    "))
            ui.out()
    ui.out()


def _expires(ts: float) -> str:
    m = int((ts - time.time()) / 60)
    return ui.state("expiring" if m < 2 else "ok", label=f"in {m}m")


def cmd_fleet(action):
    def run(a):
        f = call(admin_client().post, f"/fleet/{action}")
        ui.ok(f"Fleet is now {ui.state(f['state'])}.")
    return run


# ---------------------------------------------------------------------- agent-side entry points

def cmd_mcp(a):
    from .mcp_server import main
    main(a.slot)


def cmd_hook(a):
    from .hooks import run_hook
    raise SystemExit(run_hook(a.event, a.vendor))


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # legacy Windows consoles (cp1252)
        except AttributeError:
            pass
    # Decide on colour before argparse prints anything (help, usage errors).
    args = sys.argv[1:] if argv is None else argv
    for i, arg in enumerate(args):
        mode = arg.split("=", 1)[1] if arg.startswith("--color=") else \
            args[i + 1] if arg == "--color" and i + 1 < len(args) else None
        if mode in ("auto", "always", "never"):
            ui.set_mode(mode)
    p = ui.ArgumentParser(prog="hive", description="Multi-agent harness. See SPEC.md.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--color", choices=["auto", "always", "never"], default="auto",
                   help="colour the output (default: auto = only on a terminal; also "
                        "HIVE_COLOR, NO_COLOR)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("server", help="run the hive server (development)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--db", default="sqlite:///hive.db")
    s.add_argument("--sqlite-journal", choices=["wal", "delete", "truncate"],
                   help="SQLite journal mode (default: wal; delete on network filesystems)")
    s.add_argument("--admin-token")
    s.set_defaults(fn=cmd_server)

    adm = sub.add_parser("admin", help="user/admin operations").add_subparsers(dest="sub",
                                                                               required=True)
    s = adm.add_parser("login")
    s.add_argument("--server", required=True)
    s.add_argument("--token", required=True)
    s.set_defaults(fn=cmd_admin_login)
    s = adm.add_parser("enroll", help="create an agent and print its token")
    s.add_argument("name")
    s.add_argument("--vendor", choices=VENDORS, required=True)
    s.add_argument("--roles", required=True, help="comma-separated, e.g. programmer,tester")
    s.set_defaults(fn=cmd_admin_enroll)
    s = adm.add_parser("roles", help="change an agent's roles")
    s.add_argument("name")
    s.add_argument("roles")
    s.set_defaults(fn=cmd_admin_roles)
    s = adm.add_parser("push-config", help="upload hive.toml to the server")
    s.set_defaults(fn=cmd_admin_push_config)
    s = adm.add_parser("backup", help="download a copy of the server's SQLite database")
    s.add_argument("-o", "--output", help="file to write (default hive-backup-<time>.db)")
    s.set_defaults(fn=cmd_admin_backup)

    s = sub.add_parser("init", help="wire this repository for Hive")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--server")
    s.add_argument("--main", default="main")
    s.add_argument("--hive-cmd", default="hive")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("overview", help="describe this project's Hive setup in detail")
    s.add_argument("path", nargs="?", default=".", help="project folder (default: here)")
    s.add_argument("--offline", action="store_true",
                   help="don't contact the server or the local daemon")
    s.set_defaults(fn=cmd_overview)

    s = sub.add_parser("prompts", help="show what Hive tells agents with given roles")
    s.add_argument("--role", help="comma-separated roles (default: programmer)")
    s.add_argument("--vendor", default="claude", choices=VENDORS,
                   help="fills {{vendor}} in role files")
    s.add_argument("--slot", help="agent name used for {{slot}} (default: <slot>)")
    s.add_argument("--full", action="store_true", help="also print the built-in skill text")
    s.add_argument("--path", default=".", help="repository (default: current directory)")
    s.set_defaults(fn=cmd_prompts)

    s = sub.add_parser("join", help="add an agent slot to this machine")
    s.add_argument("name")
    s.add_argument("--token", required=True)
    s.add_argument("--vendor", choices=VENDORS, required=True)
    s.add_argument("--server")
    s.add_argument("--repo", help="local clone; the slot worktree is created next to it")
    s.add_argument("--worktree", help="explicit worktree path")
    s.add_argument("--config-dir", help="CLI account config dir (default ~/.hive/accounts/NAME)")
    s.add_argument("--permission-mode", help="claude: acceptEdits|bypassPermissions|…; "
                                             "gemini: auto_edit|yolo; codex: workspace-write|"
                                             "read-only|danger-full-access|yolo")
    s.add_argument("--model", help="model for this slot's CLI (default: the "
                                   "account's default model)")
    s.add_argument("--hive-cmd", default="hive")
    s.set_defaults(fn=cmd_join)

    s = sub.add_parser("up", help="run this machine's slots (orchestrator: native interactive "
                                  "CLI; workers: headless with live output)")
    s.add_argument("-v", "--verbose", action="store_true")
    s.add_argument("--headless", action="store_true",
                   help="drive the orchestrator headlessly too (no interactive session)")
    s.set_defaults(fn=cmd_up)

    s = sub.add_parser("watch", help="follow agents' live output (admin only)")
    s.add_argument("agent", nargs="?", help="one agent (default: all)")
    s.add_argument("-n", type=int, default=200, help="initial lines")
    s.add_argument("--no-follow", dest="follow", action="store_false")
    s.set_defaults(fn=cmd_watch)

    s = sub.add_parser("chat", help="attach interactively to a slot's session")
    s.add_argument("slot")
    s.add_argument("--fresh", action="store_true", help="start a new session")
    s.set_defaults(fn=cmd_chat)

    s = sub.add_parser("start", help="start the fleet with a goal (→ planning)")
    s.add_argument("goal")
    s.set_defaults(fn=cmd_start)
    s = sub.add_parser("decisions", help="list decisions waiting for you")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_decisions)
    s = sub.add_parser("decide", help="answer a decision")
    s.add_argument("id", type=int)
    s.add_argument("answer")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_decide)
    s = sub.add_parser("say", help="message the orchestrator in #user")
    s.add_argument("text")
    s.set_defaults(fn=cmd_say)
    s = sub.add_parser("status", help="fleet overview")
    s.add_argument("--all", action="store_true")
    s.add_argument("-n", type=int, default=5)
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_status)
    for action in ("pause", "resume", "reset"):
        sub.add_parser(action, help=f"{action} the fleet").set_defaults(fn=cmd_fleet(action))

    s = sub.add_parser("mcp", help="(used by agent CLIs) stdio MCP server")
    s.add_argument("--slot")
    s.set_defaults(fn=cmd_mcp)
    s = sub.add_parser("hook", help="(used by agent CLIs) hook bridge")
    s.add_argument("event")
    # Free-form on purpose: a usage error exits with 2, which Claude Code and Codex treat as
    # "block", so an unknown vendor (version skew) would stall the agent instead of failing open.
    s.add_argument("--vendor", help=f"{', '.join(VENDORS)}; others get Claude Code's format")
    s.set_defaults(fn=cmd_hook)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
