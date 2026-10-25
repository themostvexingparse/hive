"""`hive` command-line interface. SPEC §17."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

from . import __version__, ui
from .config import (DEFAULT_REPO_CONFIG, LOCAL_TOML, VENDORS, Slot,
                     find_repo_root, hive_home, load_admin_config, load_machine_config,
                     load_repo_config, local_config_path, machine_config_path, project_url,
                     repo_server_url, save_admin_config, save_machine_config,
                     split_project_url)
from .daemon.client import HiveError, ServerClient


def die(msg: str) -> None:
    ui.error(msg)
    raise SystemExit(1)


PROJECT: str | None = None  # --project: the project admin commands act on


def interactive() -> bool:
    """Whether a person is at the keyboard (so Hive may ask instead of failing)."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def yes(question: str, default: bool = True) -> bool:
    ans = ui.ask(f"{question} [{'Y/n' if default else 'y/N'}]", "y" if default else "n")
    return default if not ans else ans.lower() in ("y", "yes")


def admin_target() -> tuple[str, str]:
    url, token = load_admin_config().target(PROJECT)
    if not url:
        die("not logged in to a Hive server. Try Hive on this computer with `hive server` "
            "(it logs you in), or log in to a team server with `hive login --server URL`")
    if not token:
        die(f"no admin token for {url}; run `hive login --server {url}`")
    return url, token


def admin_client(sync: bool = True) -> ServerClient:
    c = ServerClient(*admin_target())
    if sync:
        sync_config(c)
    return c


def _sync_record() -> Path:
    return hive_home() / "config-sync.json"


def sync_config(c: ServerClient, strict: bool = False, quiet: bool = False) -> None:
    """Upload this repository's hive.toml when it changed since this computer last uploaded
    it to that server, so nobody has to remember `hive admin push-config`. Only from a main
    clone: an agent's worktree may hold an older main. With strict, an upload error stops the
    command; otherwise it is a warning. Without strict, only to the server (project) this
    hive.toml names: a command aimed elsewhere (--project, $HIVE_PROJECT, another default
    server) must not overwrite that project's settings with this repository's."""
    from .config import shared_config
    root = find_repo_root()
    if not root or not (root / ".git").is_dir():
        return
    where = c.base.removesuffix("/api/v1")
    if not strict and not _same_server(where, repo_server_url(root) or
                                       DEFAULT_REPO_CONFIG["server"]["url"]):
        return
    try:
        data = shared_config(root)
    except (ValueError, OSError) as e:  # TOMLDecodeError is a ValueError
        if strict:
            die(f"hive.toml: {e}")
        ui.warn(f"hive.toml wasn't uploaded: {e}")
        return
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    try:
        record = json.loads(_sync_record().read_text("utf-8"))
    except (OSError, ValueError):
        record = {}
    if record.get(where) == digest and not strict:
        return
    try:
        c.put("/config", data)
    except HiveError as e:
        if strict:
            fail(e)
        if e.code == "unreachable":
            return  # the command itself says so
        ui.warn(f"hive.toml wasn't uploaded: {e.message}")
        return
    if record.get(where) != digest and not quiet:
        ui.info(f"Uploaded {ui.path('hive.toml')} to the server (it changed).")
    record[where] = digest
    _sync_record().parent.mkdir(parents=True, exist_ok=True)
    _sync_record().write_text(json.dumps(record, indent=1), "utf-8")


def _same_server(a: str, b: str) -> bool:
    """Whether two URLs name the same server and project; every local host name counts as
    this computer."""
    def key(url: str) -> tuple[str, str]:
        base, project = split_project_url(url)
        if _is_local_url(base):
            base = "local:" + (base.rsplit(":", 1)[-1] if base.count(":") > 1 else "80")
        return base.lower(), project or "default"
    return key(a) == key(b)


def owner_client() -> ServerClient:
    """The server's own admin (the owner), who manages its projects."""
    cfg = load_admin_config()
    base = split_project_url(cfg.server_url)[0]
    if not base:
        die("not logged in to a Hive server; run `hive login --server URL`")
    token = os.environ.get("HIVE_ADMIN_TOKEN", "") or cfg.token_for(base)
    if not token:
        die(f"managing projects needs the server's own admin token; run "
            f"`hive login --server {base}` with it")
    return ServerClient(base, token)


def call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except HiveError as e:
        fail(e)


def fail(e: HiveError) -> None:
    if e.code == "unreachable":
        die(f"can't reach the Hive server ({e.message}). Is it running? On this computer: "
            "`hive server`; `hive doctor` checks the setup")
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

LOCAL_HOSTS = ("127.0.0.1", "localhost", "0.0.0.0", "::")


def server_token_path() -> Path:
    return hive_home() / "server.toml"


def _server_token(given: str | None) -> tuple[str, str]:
    """The admin token `hive server` runs with, and where it came from: --admin-token,
    $HIVE_ADMIN_TOKEN, the one an earlier `hive server` saved, or a new one (then saved)."""
    if given:
        return given, "--admin-token"
    if os.environ.get("HIVE_ADMIN_TOKEN"):
        return os.environ["HIVE_ADMIN_TOKEN"], "HIVE_ADMIN_TOKEN"
    path = server_token_path()
    if path.exists():
        with open(path, "rb") as f:
            token = tomllib.load(f).get("admin_token", "")
        if token:
            return token, str(path)
    import tomli_w
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps({"admin_token": token}), "utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token, "new"


def cmd_server(a):
    os.environ.setdefault("HIVE_DATABASE_URL", a.db)
    if a.sqlite_journal:
        os.environ["HIVE_SQLITE_JOURNAL"] = a.sqlite_journal
    token, source = _server_token(a.admin_token)
    os.environ["HIVE_ADMIN_TOKEN"] = token
    from werkzeug.serving import run_simple
    from .server.router import create_server
    server = create_server()
    projects = [p.slug for p in server.registry.list() if not p.archived]
    ui.banner(f"Hive server {__version__}", "development server; see docs/DEPLOYMENT.md")
    ui.kv([("listening", ui.path(f"http://{a.host}:{a.port}")),
           ("dashboard", ui.path(f"http://{a.host}:{a.port}/")),
           ("database", os.environ["HIVE_DATABASE_URL"]),
           ("projects", ", ".join(["default"] + projects)),
           ("admin token", "new, saved in " + ui.path(server_token_path()) if source == "new"
            else ui.dim(f"from {source}"))])
    if a.host in LOCAL_HOSTS:
        # This computer's own admin commands then just work, with no `hive login`.
        url = f"http://127.0.0.1:{a.port}"
        saved = load_admin_config(env=False)
        if saved.token_for(url) != token:
            # A login to another server stays the default: trying Hive locally mustn't
            # redirect (or lose) it.
            keep = bool(saved.server_url) and saved.server_url.rstrip("/") != url
            save_login(url, token, default=not keep)
            if keep:
                ui.ok(f"This computer is logged in to it as admin. Your admin commands still "
                      f"go to {ui.path(saved.server_url)}; switch with "
                      f"{ui.cmd(f'hive login --server {url}')}.")
            else:
                ui.ok(f"This computer is logged in to it as admin; in another terminal, run "
                      f"{ui.cmd('hive')} to see what's next.")
    ui.out(ui.dim("  Ctrl+C to stop.") + "\n")
    run_simple(a.host, a.port, server, threaded=True)


def save_login(url: str, token: str, default: bool = True) -> None:
    """Save `token` for `url` in admin.toml. A server's own URL (or a first login) becomes the
    default target, unless `default` is false. Only the file's own values are kept: a token
    from the environment is never written to it."""
    cfg = load_admin_config(env=False)
    if not cfg.server_url or (default and not split_project_url(url)[1]):
        if cfg.server_url and cfg.server_url.rstrip("/") != url and cfg.token:
            cfg.servers[cfg.server_url.rstrip("/")] = cfg.token  # keep the previous login
        cfg.server_url, cfg.token = url, token
        cfg.servers.pop(url, None)
    else:
        cfg.servers[url] = token
    save_admin_config(cfg)


def cmd_admin_login(a):
    url = a.server
    if not url:
        root = find_repo_root()
        url = (load_repo_config(root)["server"].get("url") if root else "") or \
            load_admin_config().server_url or "http://127.0.0.1:8765"
        if interactive():
            url = ui.ask(f"Hive server [{url}]:", url) or url
    url = url.rstrip("/")
    if PROJECT:
        url = project_url(url, PROJECT)
    token = a.token
    if not token:
        if not interactive():
            die("pass --token TOKEN (the server's admin token, or a project's)")
        import getpass
        token = getpass.getpass(ui.paint("? ", "bright_yellow", "bold")
                                + ui.paint(f"Admin token for {url} (hidden):", "bold") + " ")
        token = token.strip() or die("no token given")
    who = call(ServerClient(url, token).get, "/admin/check")
    save_login(url, token)
    project = who.get("project", "default")
    role = ("the server's admin (all projects)" if who.get("owner")
            else f"admin of project {project}")
    ui.ok(f"Logged in to {ui.path(url)} as {role}.")


def cmd_project_create(a):
    c = owner_client()
    r = call(c.post, "/admin/projects", {"slug": a.slug, "name": a.name or "",
                                         "db_url": a.db_url or "",
                                         "github_token_env": a.github_token_env or ""})
    url = project_url(c.base.removesuffix("/api/v1"), a.slug)
    ui.ok(f"Created project {ui.agent(a.slug)} at {ui.path(url)}.")
    ui.kv([("admin token", ui.paint(r["admin_token"], "bold", "bright_yellow")),
           ("", ui.dim("shown only once; it is admin of this project only")),
           ("dashboard", ui.path(url + "/"))])
    ui.section("Next")
    for i, t in enumerate([
            "In the project's repository: " + ui.cmd(f"hive init --server {url}"),
            "Its admin logs in once: " + ui.cmd(f"hive login --server {url}"),
            "Then, inside the repository: " + ui.cmd("hive") + ui.dim(
                " (it shows each next step; hive.toml names the project)")], 1):
        ui.out(f"  {ui.num(f'{i}.')} {t}")


def cmd_project_list(a):
    rows = call(owner_client().get, "/admin/projects")
    ui.table(["project", "name", "path", "database", "github token", "state"],
             [[ui.agent(p["slug"]), p.get("name", ""), p.get("path") or "/",
               p.get("database", "(server's)"), p.get("github_token_env") or "",
               "archived" if p.get("archived") else "active"] for p in rows])


def cmd_project_set(a):
    body = {"name": a.name, "github_token_env": a.github_token_env}
    if a.archive or a.unarchive:
        body["archived"] = bool(a.archive)
    p = call(owner_client().patch, f"/admin/projects/{a.slug}",
             {k: v for k, v in body.items() if v is not None})
    ui.ok(f"Project {ui.agent(p['slug'])}: " + ("archived (no longer served; its data is kept)"
                                                if p["archived"] else "active") + ".")


def cmd_project_rotate(a):
    r = call(owner_client().post, f"/admin/projects/{a.slug}/token")
    ui.ok(f"New admin token for project {ui.agent(a.slug)}; the old one no longer works.")
    ui.kv([("admin token", ui.paint(r["admin_token"], "bold", "bright_yellow"))])


def cmd_admin_enroll(a):
    c = admin_client()
    roles = [r.strip() for r in a.roles.split(",") if r.strip()]
    r = call(c.post, "/agents", {"name": a.name, "vendor": a.vendor, "roles": roles})
    ui.ok(f"Enrolled {ui.agent(r['name'])} ({a.vendor}; roles: {', '.join(roles)}).")
    ui.kv([("token", ui.paint(r["token"], "bold", "bright_yellow")),
           ("", ui.dim("shown only once; treat it like a password"))])
    ui.section("On the agent's machine, inside its clone of the repository, run")
    ui.out("  " + ui.cmd(join_line(r["name"], r["token"], c)))


def join_line(name: str, token: str, c: ServerClient) -> str:
    return f"hive join {name} --token {token} --server {c.base.removesuffix('/api/v1')}"


def set_roles(c: ServerClient, name: str, roles: list[str]) -> None:
    r = call(c.patch, f"/agents/{name}", {"roles": roles})
    if r.get("status") == "proposed":
        did = r.get("decision_id")
        ui.warn(f"The fleet is active, so this became decision #{did}; approve it with "
                + ui.cmd(f"hive approve {did}"), sys.stdout)
    else:
        ui.ok(f"{ui.agent(name)} now has roles: {', '.join(roles)}.")


def cmd_admin_roles(a):
    from .agents import parse_roles
    set_roles(admin_client(), a.name, parse_roles(a.roles))


def cmd_admin_backup(a):
    import requests
    url, token = admin_target()
    out = Path(a.output or time.strftime("hive-backup-%Y%m%d-%H%M%S.db"))
    try:
        with requests.get(url + "/api/v1/admin/backup", stream=True,
                          headers={"Authorization": f"Bearer {token}"}, timeout=300) as r:
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
    from .config import shared_config
    root = find_repo_root() or die("no hive.toml found (run `hive init`)")
    c = admin_client(sync=False)
    if (root / ".git").is_dir():
        sync_config(c, strict=True, quiet=True)  # also records it, so it isn't sent again
    else:
        try:
            data = shared_config(root)
        except ValueError as e:
            die(f"hive.toml: {e}")
        call(c.put, "/config", data)
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
    if PROJECT:
        server = project_url(server, PROJECT)
    try:
        written, cleaned = init_repo(root, server, a.main or default_branch(root), a.hive_cmd)
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
    checks_step = []
    if not load_repo_config(root)["integration"].get("checks"):
        guess = guess_test_command(root)
        if guess and interactive() and yes(f"Run {ui.cmd(guess)} before any agent's work "
                                           "reaches main (and refuse work that fails)?"):
            from . import settings
            settings.write(root / "hive.toml", "integration.checks", [guess])
            ui.ok(f"integration.checks = [\"{guess}\"] " + ui.dim("in hive.toml"))
        else:
            checks_step = ["Optional: run your tests before work reaches main: " + ui.cmd(
                f'hive config set integration.checks "{guess or "<your test command>"}"')]
    ui.section("Next")
    for i, t in enumerate(checks_step + [
            "Commit these files and push: "
            + ui.cmd('git add hive.toml .hive .gitignore && git commit -m "Set up Hive" '
                     "&& git push"),
            "Add agents: " + ui.cmd("hive agents add claude")
            + ui.dim("  (the first one plans; run it again for one that codes)")], 1):
        ui.out(f"  {ui.num(f'{i}.')} {t}")


def guess_test_command(root: Path) -> str:
    """The repository's usual test command, from the files that give it away ("" if none)."""
    pkg = root / "package.json"
    if pkg.exists():
        try:
            test = json.loads(pkg.read_text("utf-8")).get("scripts", {}).get("test", "")
        except (ValueError, OSError):
            test = ""
        if test and "no test specified" not in test:
            return "npm test"
    if (root / "Cargo.toml").exists():
        return "cargo test"
    if (root / "go.mod").exists():
        return "go test ./..."
    if (root / "pytest.ini").exists() or (root / "tests").is_dir() and any(
            (root / f).exists() for f in ("pyproject.toml", "setup.py", "setup.cfg")):
        return "python -m pytest -q"
    return ""


def default_branch(root: Path) -> str:
    """The branch finished work goes into: origin's default branch, else the current one."""
    for cmd in (["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                ["git", "branch", "--show-current"]):
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        name = r.stdout.strip().removeprefix("origin/")
        if r.returncode == 0 and name:
            return name
    return "main"


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


def _is_local_url(url: str) -> bool:
    host = split_project_url(url)[0].split("//")[-1].split("/")[0].rsplit(":", 1)[0]
    return host.strip("[]") in LOCAL_HOSTS


def cmd_join_machine(a):
    """`hive join CODE`: pair this computer with a project; it then runs whichever agents the
    admin places on it."""
    from . import agents, machines
    repo = Path(a.repo).resolve() if a.repo else agents.clone_here()
    if repo is None:
        die("run this inside this computer's clone of the repository (placed agents' "
            "worktrees are created next to it), or pass --repo <clone>")
    server = a.server
    if not server:
        toml_url = load_repo_config(repo)["server"].get("url", "") \
            if (repo / "hive.toml").exists() else ""
        if not toml_url or _is_local_url(toml_url):
            die("pass --server URL: the Hive server's address as this computer reaches it "
                "(`hive machines invite` prints the whole line)")
        server = toml_url
    server = server.rstrip("/")
    if not (repo / "hive.toml").exists():
        ui.warn(f"{repo} has no hive.toml yet: `git pull` it, so agents get Hive's files.")
    r = call(machines.pair, server, a.name, repo, a.machine_name or "")
    ui.ok(f"This computer is now {ui.agent(r['name'])} in {ui.path(server)}.")
    res = machines.sync(server)
    for line in machines.describe(res):
        ui.ok(line)
    for err in res.errors:
        ui.warn(err)
    if res.added:
        offer_login(res.added)
    else:
        ui.note("No agents are placed on it yet. On your computer: "
                + ui.cmd(f"hive agents add claude --on {r['name']}"))
    ui.section("Then run this computer's agents (it picks up new ones as they're placed)")
    ui.out("  " + ui.cmd("hive up"))


def cmd_join(a):
    from . import agents
    if not a.token:
        return cmd_join_machine(a)
    cfg = load_machine_config()
    old = next((s for s in cfg.slots if s.name == a.name), None)
    # The server belongs to this slot alone: other slots on the machine may be in other
    # projects, or on other servers, and must keep theirs.
    server = (a.server or (cfg.url_for(old) if old else "") or cfg.server_url).rstrip("/")
    c = ServerClient(server, a.token)
    me = call(c.get, "/agents/me")
    if me["name"] != a.name:
        die(f"token belongs to {me['name']!r}, not {a.name!r}")
    # The server knows which CLI the agent runs; --vendor is only a check.
    known = next((ag["vendor"] for ag in call(c.get, "/agents") if ag["name"] == a.name), "")
    if a.vendor and known and a.vendor != known:
        die(f"{a.name} runs {known} (set when it was created), not {a.vendor}")
    vendor = a.vendor or known or die("pass --vendor (the server didn't say which CLI "
                                      f"{a.name} runs)")
    repo = Path(a.repo).resolve() if a.repo else None
    if repo is None and not a.worktree:
        repo = agents.clone_here() or die(
            "run this inside your clone of the repository (the agent's worktree is created "
            "next to it), or pass --repo <clone> or --worktree <path>")
    try:
        slot, notes = agents.place(
            a.name, vendor, a.token, server, repo,
            worktree=Path(a.worktree).resolve() if a.worktree else None,
            config_dir=a.config_dir, permission_mode=a.permission_mode or "",
            model=a.model or "", hive_cmd=a.hive_cmd, replace=a.replace)
    except agents.SetupError as e:
        die(str(e))
    for n in notes:
        ui.ok(n) if n.startswith("Installed") else ui.info(n)
    ui.ok(f"Agent {ui.agent(a.name)} is set up on this computer.")
    ui.kv([("worktree", ui.path(slot.worktree)), ("account", ui.path(slot.config_dir))]
          + ([("model", a.model)] if a.model else []))
    offer_login([slot])
    ui.section("Then start this computer's agents")
    ui.out("  " + ui.cmd("hive up"))


def agents_logged_out(slot: Slot) -> bool:
    from .agents import is_logged_in
    return is_logged_in(slot) is False


def offer_login(slots: list[Slot]) -> None:
    """Log each new agent's account in now, if a person is here; else say how to later."""
    from . import agents
    cfg = load_machine_config()
    for slot in slots:
        if slot.vendor == "agy":
            # agy keeps its login in the OS keyring, not in its config folder.
            others = [s.name for s in cfg.slots if s.vendor == "agy" and s.name != slot.name]
            ui.warn("agy keeps its Google login in this computer's keyring, so every agy agent "
                    "on it uses the same account and shares its usage limits"
                    + (f" (also {', '.join(others)})." if others else "."))
            if others:
                continue  # already logged in, once for the whole computer
        who = "agy on this computer" if slot.vendor == "agy" else f"{slot.name}'s account"
        if interactive() and yes(f"Log {who} in now?"):
            ui.note(f"{slot.vendor} opens here: log in, then exit it "
                    + ui.dim("(/exit, or Ctrl+C twice)") + ".")
            try:
                agents.run_login(slot)
            except agents.SetupError as e:
                ui.warn(str(e))
                continue
            if agents.is_logged_in(slot) is False:
                ui.warn(f"{slot.name}'s account folder is still empty, so the login may not "
                        "have finished; run " + ui.cmd(f"hive agents login {slot.name}"))
            else:
                ui.ok(f"{ui.agent(slot.name)} is ready.")
        else:
            ui.note(f"Log {who} in once: " + ui.cmd(f"hive agents login {slot.name}"))


def run_interactive(slot: Slot, cfg, fresh: bool = False, initial: str | None = None,
                    orchestrator: bool = False) -> int:
    """Launch the slot's native CLI in this terminal with Hive attached for this session only."""
    import uuid
    from .daemon import wake
    from .daemon.store import Store
    store = Store(hive_home() / "state.db")
    session = None if fresh else store.get(slot.name, "session")
    if session and orchestrator:
        # A long-idle orchestrator conversation is mostly stale history that every request
        # would re-read; its memory is in .hive/orchestrator/state.md.
        hours = float(load_repo_config(Path(slot.worktree)).get("sessions", {})
                      .get("orchestrator_fresh_after_hours", 12) or 0)
        used = store.get(slot.name, "session_used", None)
        if hours and used and time.time() - float(used) > hours * 3600:
            ui.info(f"Last orchestrator session is over {hours:g} h old: starting a fresh one "
                    "(its memory is in .hive/orchestrator/state.md).")
            session = None
    resume = bool(session)
    if not resume:  # what earlier sessions were shown doesn't carry over to a new one
        store.put(slot.name, "session_seen", {})
        store.put(slot.name, "session_task", None)
        store.put(slot.name, "last_context", 0)
    if slot.vendor not in wake.SELF_ID_VENDORS:  # those are read back afterwards
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
    store.put(slot.name, "session_used", time.time())
    if p.returncode != 0 and resume:
        # The stored session may be gone (e.g. different machine); start a fresh one next time.
        store.put(slot.name, "session", None)
    if slot.vendor in wake.SELF_ID_VENDORS and slot.config_dir:
        found = wake.latest_session(slot, started)
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


def pick_orchestrator(d, cfg, name: str | None):
    """The orchestrator slot `hive up` attaches this terminal to; the others run headless.
    With several projects' orchestrators on this machine, the one of the project this command
    is about: --project (or $HIVE_PROJECT), else the repository it runs in."""
    orchs = [rt for rt in d.slots.values() if rt.is_orchestrator]
    if name:
        return next((rt for rt in orchs if rt.name == name), None) or die(
            f"{name} is not an orchestrator slot on this machine")
    if len(orchs) <= 1:
        return orchs[0] if orchs else None

    def project_of(url: str) -> str:
        return split_project_url(url)[1] or "default"

    project = PROJECT or os.environ.get("HIVE_PROJECT", "")
    if not project and (root := find_repo_root()):
        url = load_repo_config(root).get("server", {}).get("url", "")
        project = project_of(url) if url else ""
    found = [rt for rt in orchs if project and project_of(cfg.url_for(rt.cfg)) == project]
    if len(found) == 1:
        return found[0]
    listed = ", ".join(f"{rt.name} ({project_of(cfg.url_for(rt.cfg))})" for rt in orchs)
    die(f"this machine has several orchestrators: {listed}. Run `hive up` inside a project's "
        "repository, or choose one with --project NAME or --orchestrator NAME")


def cmd_up(a):
    from .daemon.core import Daemon
    from .daemon.localapi import serve
    hive_home().joinpath("logs").mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(hive_home() / "logs" / "daemon.log", encoding="utf-8")]
    handlers[0].setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    cfg = load_machine_config()
    if cfg.machines:
        from . import machines
        res = machines.sync()
        for line in machines.describe(res):
            ui.ok(line)
        for err in res.errors:
            ui.warn(f"placement: {err}")
        if res.added and interactive():
            offer_login([s for s in res.added if agents_logged_out(s)])
        cfg = load_machine_config()
    if not cfg.slots and not cfg.machines:
        die("no agents on this computer yet: add some with `hive agents add claude` (inside "
            "your clone), or pair it with a code from `hive machines invite`")
    save_machine_config(cfg)  # persists a generated local secret
    refresh_git_hooks(cfg)
    d = Daemon(cfg)
    # Each slot's roles decide how it runs, so they must be known before anything starts.
    servers = cfg.server_urls()
    ui.step(f"Connecting to {', '.join(ui.path(u) for u in servers)} ...")
    missing = d.wait_for_identity(timeout=30)
    if missing:
        where = ", ".join(f"{n} ({cfg.url_for(cfg.slot(n))})" for n in missing)
        die(f"could not reach the Hive server for {where} "
            "(is it running, and are the tokens in ~/.hive/machine.toml valid?)")
    orch = None if a.headless else pick_orchestrator(d, cfg, a.orchestrator)
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
    ui.kv([("server" if len(servers) == 1 else "servers",
            ", ".join(ui.path(u) for u in servers)),
           ("log", ui.path(hive_home() / "logs" / "daemon.log"))])
    ui.out()
    ui.table(["slot", "vendor", "roles"] + (["server"] if len(servers) > 1 else []),
             [[ui.agent(s.name), s.cfg.vendor, ", ".join(s.roles) or "?"]
              + ([cfg.url_for(s.cfg)] if len(servers) > 1 else [])
              for s in d.slots.values()])
    if not orch:
        ui.out()
        if not d.slots:
            names = ", ".join(m["name"] for m in cfg.machines.values())
            ui.info(f"No agents are placed on this computer ({names}) yet; they start here "
                    "within a minute of being placed. On your computer: "
                    + ui.cmd(f"hive agents add claude --on {names.split(', ')[0]}"))
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


def open_team_chat(c: ServerClient | None = None) -> None:
    """The chat with the orchestrator (hive/chat.py), its slash commands run here."""
    from .chat import Chat
    no_agent_session("The chat")
    c = c or admin_client()
    ui.banner(f"Hive {__version__}", "talk to the team · /help · /quit")
    Chat(c, run_command=chat_runner(PROJECT)).run()


def chat_runner(project: str | None):
    """How the chat runs a slash command or a decision's answer: as `hive …`, in the project
    the chat shows (main() resets --project for each command it parses)."""
    return lambda argv: main((["--project", project] if project else []) + list(argv))


def cmd_chat(a):
    if not a.slot:
        return open_team_chat()
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
    c = admin_client(sync=False)
    root = find_repo_root()
    if root and (root / ".git").is_dir():
        sync_config(c, strict=True)
    elif root:
        from .config import shared_config
        try:
            call(c.put, "/config", shared_config(root))
        except ValueError as e:
            die(f"hive.toml: {e}")
    f = call(c.post, "/fleet/start", {"goal": a.goal})
    ui.ok(f"Fleet is {ui.state(f['state'])}; orchestrator {ui.agent(f['orchestrator'])} is "
          "planning.")
    ui.note(f"When the plan is ready: {ui.cmd('hive decisions')} shows it, "
            f"{ui.cmd('hive approve')} approves it.")


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
        elif d["state"] == "open":
            # The ID is only needed when several decisions are open.
            ref = f" {d['id']}" if sum(r["state"] == "open" for r in rows) > 1 else ""
            ui.out()
            ui.out("  " + ui.paint("Answer with", "gray", "underline"))
            for line in answer_commands(d, ref):
                ui.out("    " + ui.cmd(line))
    if rows:
        ui.out()


def answer_commands(d: dict, ref: str = "") -> list[str]:
    opts = [o.lower() for o in d["options"] or []]
    if not opts:
        return [f'hive answer{ref} "<your answer>"']
    out = []
    if "approve" in opts:
        out.append(f"hive approve{ref}")
    if "reject" in opts:
        out.append(f'hive reject{ref} "<what to change>"')
    out += [f"hive answer{ref} {_quote(o)}" for o in d["options"]
            if o.lower() not in ("approve", "reject")]
    return out


def _quote(arg: str) -> str:
    return arg if arg and all(ch.isalnum() or ch in "-_.:/" for ch in arg) else f'"{arg}"'


def cmd_decide(a):
    answer_decision(admin_client(), a.id, a.answer, a.note or "")


def answer_decision(c: ServerClient, did: int, answer: str, note: str = "") -> None:
    d = call(c.post, f"/decisions/{did}/answer", {"answer": answer, "note": note})
    ui.ok(f"Decision #{d['id']} answered: {ui.paint(d['answer'], 'green', 'bold')}"
          + (ui.dim(f"  ({note})") if note else ""))


def pick_decision(c: ServerClient, words: list[str], verb: str) -> tuple[dict, list[str]]:
    """The open decision a shortcut answers, and the words left for its note or answer. A
    leading number names one; with a single open decision none is needed."""
    rows = [d for d in call(c.get, "/decisions", state="open") if d["state"] == "open"]
    if words and words[0].lstrip("#").isdigit():
        did = int(words[0].lstrip("#"))
        hit = next((d for d in rows if d["id"] == did), None)
        if hit:
            return hit, words[1:]
        if verb != "answer" or len(words) > 1:
            die(f"decision #{did} isn't waiting for an answer; "
                + ui.cmd("hive decisions --all") + " lists them all")
    if not rows:
        die("no decision is waiting for you" + ("" if verb == "answer" else
            f", so there is nothing to {verb}") + "; " + ui.cmd("hive status") + " shows "
            "what the fleet is doing")
    if len(rows) == 1:
        return rows[0], words
    listing = "\n".join(f"  #{d['id']}  {d['question'].splitlines()[0][:90]}" for d in rows)
    if not interactive():
        die(f"{len(rows)} decisions are open; say which: hive {verb} <number> …\n{listing}")
    ui.out(listing)
    ans = ui.ask("Which decision (number)?").lstrip("#")
    d = next((d for d in rows if str(d["id"]) == ans), None)
    return d or die(f"no open decision #{ans}"), words


def cmd_approve(a):
    c = admin_client()
    d, rest = pick_decision(c, a.words, "approve")
    opts = [o.lower() for o in d["options"] or []]
    if opts and "approve" not in opts:
        die(f"decision #{d['id']} isn't a yes/no question: {d['question'][:200]}\n"
            "Answer it with: " + " · ".join(answer_commands(d, f" {d['id']}")))
    answer_decision(c, d["id"], "approve", " ".join(rest))


def cmd_reject(a):
    c = admin_client()
    d, rest = pick_decision(c, a.words, "reject")
    opts = [o.lower() for o in d["options"] or []]
    if opts and "reject" not in opts:
        die(f"decision #{d['id']} can't be rejected: {d['question'][:200]}\n"
            "Answer it with: " + " · ".join(answer_commands(d, f" {d['id']}")))
    note = " ".join(rest)
    if not note and interactive():
        note = ui.ask("What should change? (Enter to skip)")
    answer_decision(c, d["id"], "reject", note)


def cmd_answer(a):
    c = admin_client()
    d, rest = pick_decision(c, a.words, "answer")
    text = " ".join(rest).strip()
    if not text:
        if not interactive():
            die('give the answer: hive answer "<your answer>"')
        ui.out("  " + ui.bold(d["question"].replace("\n", "\n  ")))
        if d["options"]:
            ui.out(ui.dim("  options: " + ", ".join(d["options"])))
        text = ui.ask("Your answer:") or die("no answer given")
    answer_decision(c, d["id"], text, a.note or "")


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


# ---------------------------------------------------------------------- hive agents …

def _known_roles(root: Path | None) -> set[str]:
    from .roles import load_roles
    try:
        return set(load_roles(load_repo_config(root).get("roles")))
    except ValueError:
        from .roles import BUILTIN_ROLES
        return set(BUILTIN_ROLES)


def parse_add_words(words: list[str], roles_known: set[str]) -> tuple[int, str, list[str]]:
    """`hive agents add [N] VENDOR [ROLES]` in any order: a number is the count, a vendor
    name the CLI, anything else roles ("programmers" means programmer)."""
    count, vendor, roles = 1, "", []
    for w in words:
        for part in [p for p in w.replace(",", " ").split() if p]:
            low = part.lower()
            if low.isdigit():
                count = int(low)
            elif low in VENDORS:
                vendor = low
            elif low in roles_known:
                roles.append(low)
            elif low.endswith("s") and low[:-1] in roles_known:
                roles.append(low[:-1])
            else:
                die(f"{part!r} is neither a CLI ({', '.join(VENDORS)}) nor a role "
                    f"({', '.join(sorted(roles_known))})")
    if not 1 <= count <= 20:
        die("add between 1 and 20 agents at a time")
    return count, vendor, roles


def cmd_agents_add(a):
    from . import agents
    root = find_repo_root()
    count, vendor, roles = parse_add_words(a.words, _known_roles(root))
    if not vendor:
        installed = [v for v in VENDORS if shutil.which(v)]
        if not interactive():
            die("say which CLI the agent runs: hive agents add claude|codex|gemini|agy")
        ui.out("  CLIs on this computer: " + (", ".join(installed) or ui.dim("none found")))
        vendor = (ui.ask(f"Which CLI? [{installed[0] if installed else 'claude'}]")
                  or (installed[0] if installed else "claude")).lower()
        if vendor not in VENDORS:
            die(f"{vendor!r} isn't one of {', '.join(VENDORS)}")
    if a.name and count > 1:
        die("--name names one agent; leave it out to add several (they're numbered)")
    if a.on and a.elsewhere:
        die("--on already says where it runs; leave out --elsewhere")
    repo = None
    if not a.elsewhere and not a.on:
        repo = agents.clone_here() or die(
            "run this inside your clone of the repository, where the agents' worktrees are "
            "created; or use --elsewhere for agents that run on another computer")
        if not (repo / "hive.toml").exists():
            die(f"{repo} isn't set up for Hive yet: run " + ui.cmd("hive init")
                + ", then commit and push what it writes")
        if not shutil.which(vendor):
            ui.warn(f"{vendor} isn't installed on this computer (not on PATH); install it "
                    "before the agent can run.")
    c = admin_client()
    server = c.base.removesuffix("/api/v1")
    roster = call(c.get, "/agents")
    if a.on:
        found = next((m for m in call(c.get, "/machines") if m["name"] == a.on), None)
        if found is None:
            die(f"no paired computer named {a.on!r}; " + ui.cmd("hive machines")
                + " lists them, " + ui.cmd("hive machines invite") + " pairs one")
        if found["clis"] and vendor not in found["clis"]:
            ui.warn(f"{vendor} wasn't installed on {a.on} when it last checked in "
                    f"(it has {', '.join(found['clis'])}).")
    taken = {ag["name"] for ag in roster} | {s.name for s in load_machine_config().slots}
    names = [a.name] if a.name else agents.free_names(vendor, count, taken)
    added: list[Slot] = []
    for i, name in enumerate(names):
        mine = roles or agents.default_roles(roster, i)
        r = call(c.post, "/agents", {"name": name, "vendor": vendor, "roles": mine})
        roster.append({"name": name, "vendor": vendor, "roles": mine})
        what = f"{ui.agent(name)} ({vendor}, {', '.join(mine)})"
        if a.on:
            ui.ok(f"Created {what}.")
            place_on(c, name, a.on, a.model or "", a.permission_mode or "")
            continue
        if a.elsewhere:
            ui.ok(f"Created {what}. On the computer that will run it, inside its clone, run:")
            ui.out("    " + ui.cmd(join_line(name, r["token"], c)))
            ui.out(ui.dim("    The token in it is shown only once; it works like a password."))
            continue
        try:
            slot, notes = agents.place(name, vendor, r["token"], server, repo,
                                       permission_mode=a.permission_mode or "",
                                       model=a.model or "")
        except agents.SetupError as e:
            call(c.delete, f"/agents/{name}")  # don't leave an agent nothing can run
            die(f"couldn't set up {name} on this computer: {e}")
        for n in notes:
            ui.info(n)
        ui.ok(f"Added {what} on this computer " + ui.dim(f"→ {slot.worktree}"))
        added.append(slot)
    if added and not a.no_login:
        offer_login(added)
    if added:
        ui.note("Start them with " + ui.cmd("hive up") + ui.dim(" (if it's already running, "
                                                               "restart it)"))


def _local_slots(url: str) -> dict[str, Slot]:
    cfg = load_machine_config()
    return {s.name: s for s in cfg.slots if cfg.url_for(s).rstrip("/") == url.rstrip("/")}


def cmd_agents_list(a):
    from . import agents
    c = admin_client()
    url = c.base.removesuffix("/api/v1")
    roster = call(c.get, "/agents")
    here = _local_slots(url)
    if not roster:
        ui.info("No agents yet. Add the first one (it plans and you talk to it): "
                + ui.cmd("hive agents add claude"))
        return

    def login(s: Slot | None) -> str:
        if s is None:
            return ""
        state = agents.is_logged_in(s)
        return ui.dim("keyring") if state is None else \
            ui.state("ok", label="yes") if state else ui.state("failed", label="no")

    def where(ag: dict) -> str:
        if ag.get("machine"):
            return ag["machine"] + ("" if ag.get("claimed") else ui.dim(" (not set up yet)"))
        return "this computer" if ag["name"] in here else ui.dim("elsewhere")

    ui.table(["name", "cli", "roles", "state", "where", "logged in"],
             [[ui.agent(ag["name"]), ag["vendor"], ", ".join(ag["roles"]), _agent_state(ag),
               where(ag), login(here.get(ag["name"]))] for ag in roster])
    gone = sorted(set(here) - {ag["name"] for ag in roster})
    for name in gone:
        ui.warn(f"{name} is set up on this computer but no longer on the server; clean it up "
                "with " + ui.cmd(f"hive agents rm {name}"))


def _slot_here(name: str) -> Slot:
    slot = next((s for s in load_machine_config().slots if s.name == name), None)
    return slot or die(f"{name} isn't set up on this computer; run this on the computer "
                       "that runs it (`hive agents list` shows where each agent is)")


def cmd_agents_login(a):
    from . import agents
    slot = _slot_here(a.name)
    ui.note(f"{slot.vendor} opens here: log in, then exit it "
            + ui.dim("(/exit, or Ctrl+C twice)") + ".")
    try:
        code = agents.run_login(slot, device=a.device)
    except agents.SetupError as e:
        die(str(e))
    if agents.is_logged_in(slot) is False:
        die(f"{slot.name}'s account folder is still empty: the login didn't finish"
            + (f" ({slot.vendor} exited with {code})" if code else ""))
    ui.ok(f"{ui.agent(slot.name)}'s account is set up.")


def _unless_default(value: str) -> str:
    """`default` on the command line means the CLI's own default, stored as ""."""
    return "" if value == "default" else value


def cmd_agents_edit(a):
    from .agents import parse_roles
    if a.roles is None and a.model is None and a.permission_mode is None and a.on is None:
        die("say what to change: --roles, --model, --permission-mode or --on")
    if a.roles is not None:
        set_roles(admin_client(), a.name, parse_roles(a.roles))
    c = admin_client() if a.on is not None or a.model is not None or \
        a.permission_mode is not None else None
    reset = _unless_default
    if a.on is not None:
        target = None if a.on.lower() == "none" else a.on
        here = _local_slots(c.base.removesuffix("/api/v1")).get(a.name)
        current: dict = {}
        if target and (a.model is None or a.permission_mode is None):
            # What isn't given is kept: the placement's settings, or this computer's slot's.
            ag = next((x for x in call(c.get, "/agents") if x["name"] == a.name), None) or \
                die(f"no agent named {a.name!r}")
            current = ag.get("placement") or ({"model": here.model,
                                                "permission_mode": here.permission_mode}
                                               if here else {})
        model = reset(a.model) if a.model is not None else current.get("model", "")
        mode = reset(a.permission_mode) if a.permission_mode is not None \
            else current.get("permission_mode", "")
        place_on(c, a.name, target, model or "", mode or "")
        if target and here and not here.placed:
            # The other computer's claim gives it a new token: this slot would stop working.
            from .agents import unplace
            unplace(a.name)
            ui.info(f"Took {a.name} off this computer (its account folder is kept).")
        return
    if a.model is not None or a.permission_mode is not None:
        url = c.base.removesuffix("/api/v1")
        ag = next((x for x in call(c.get, "/agents") if x["name"] == a.name), None) or \
            die(f"no agent named {a.name!r}")
        if a.name not in _local_slots(url) and ag.get("machine"):
            # Placed on another computer: its daemon applies the settings there.
            p = ag.get("placement") or {}
            model = reset(a.model) if a.model is not None else p.get("model", "")
            mode = reset(a.permission_mode) if a.permission_mode is not None \
                else p.get("permission_mode", "")
            call(c.put, f"/agents/{a.name}/placement", {"machine": ag["machine"],
                                                        "model": model,
                                                        "permission_mode": mode})
            ui.ok(f"{ui.agent(a.name)}: model {model or 'default'}, permission mode "
                  f"{mode or 'default'}, applied on {ag['machine']} at its next check "
                  + ui.dim("(within a minute while hive up runs there)"))
            return
        _slot_here(a.name)
        cfg = load_machine_config()
        slot = cfg.slot(a.name)
        if a.model is not None:
            slot.model = reset(a.model)
        if a.permission_mode is not None:
            slot.permission_mode = reset(a.permission_mode)
        save_machine_config(cfg)
        ui.ok(f"{ui.agent(a.name)}: model {slot.model or 'default'}, permission mode "
              f"{slot.permission_mode or 'default'} " + ui.dim("(from its next turn)"))


def cmd_agents_rm(a):
    from . import agents
    c = admin_client()
    here = _local_slots(c.base.removesuffix("/api/v1")).get(a.name)
    placed_on = next((ag.get("machine") for ag in call(c.get, "/agents")
                      if ag["name"] == a.name), None)
    if not a.yes:
        if not interactive():
            die(f"removing {a.name} can't be undone; add --yes to go ahead")
        if not yes(f"Remove {a.name}? Its unfinished tasks go back on the board and its "
                   "leases are released.", default=False):
            die("nothing removed")
    try:
        r = c.delete(f"/agents/{a.name}")
        ui.ok(f"Removed {ui.agent(a.name)} from the server."
              + (ui.dim(f" Tasks back on the board: {', '.join(f'#{t}' for t in r['requeued'])}.")
                 if r["requeued"] else ""))
    except HiveError as e:
        if e.code != "no_such_agent" or here is None:
            fail(e)
        ui.info(f"{a.name} wasn't on the server any more.")
    if here:
        _, gone = agents.unplace(a.name)
        ui.ok("Removed it from this computer" + ("." if gone else
              f"; its worktree {ui.path(here.worktree)} has uncommitted changes, so it was "
              "kept (delete it yourself when you no longer need them)."))
    elif placed_on:
        ui.note(f"{placed_on} takes it off by itself within a minute (while hive up runs there).")
    else:
        ui.note("If it was set up on another computer, run "
                + ui.cmd(f"hive agents rm {a.name}") + " there too, to clean up its folder.")


def cmd_agents_rotate(a):
    c = admin_client()
    ag = next((x for x in call(c.get, "/agents") if x["name"] == a.name), None) or \
        die(f"no agent named {a.name!r}")
    if ag.get("machine"):
        # Placed afresh, its computer claims it again, and the claim gives it a new token.
        p = ag.get("placement") or {}
        call(c.put, f"/agents/{a.name}/placement", {"machine": None})
        call(c.put, f"/agents/{a.name}/placement", {"machine": ag["machine"], **p})
        call(c.post, f"/agents/{a.name}/token")  # the old token stops working right away
        ui.ok(f"{ui.agent(a.name)}'s token was replaced; {ag['machine']} takes a new one at its "
              "next check (within a minute while hive up runs there).")
        return
    r = call(c.post, f"/agents/{a.name}/token")
    cfg = load_machine_config()
    slot = next((s for s in cfg.slots if s.name == a.name and
                 cfg.url_for(s).rstrip("/") == c.base.removesuffix("/api/v1")), None)
    if slot:
        slot.token = r["token"]
        save_machine_config(cfg)
        ui.ok(f"New token for {ui.agent(a.name)}, saved for its slot on this computer; the old "
              "one no longer works. Restart " + ui.cmd("hive up") + " if it's running.")
    else:
        ui.ok(f"New token for {ui.agent(a.name)}; the old one no longer works. On the computer "
              "that runs it, inside its clone, run:")
        ui.out("    " + ui.cmd(join_line(a.name, r["token"], c)))


# ---------------------------------------------------------------------- hive machines …

def lan_address() -> str:
    """This computer's address on its local network, as other computers there reach it."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent; it only picks a route
            return s.getsockname()[0]
    except OSError:
        return ""


def cmd_machines_invite(a):
    c = admin_client()
    url = c.base.removesuffix("/api/v1")
    r = call(c.post, "/machines/invite", {"reusable": a.reusable, "days": a.days,
                                          "label": a.label or ""} if a.reusable else {})
    shown = url
    if _is_local_url(url):
        ip = lan_address()
        base, project = split_project_url(url)
        port = base.rsplit(":", 1)[1] if base.count(":") > 1 else "8765"
        shown = project_url(f"http://{ip or '<this-computer>'}:{port}", project)
        ui.warn(f"Your server's address, {url}, only works on this computer. For another "
                "computer on your network to reach it, run the server as "
                + ui.cmd(f"hive server --host 0.0.0.0 --port {port}")
                + f" (and allow port {port} through the firewall); the line below uses "
                "this computer's network address.")
    if r.get("reusable"):
        ui.ok(f"Join key {ui.paint(r['code'], 'bold', 'bright_yellow')} "
              + ui.dim(f"(pairs any number of computers for {r['expires_in'] // 86400} days; "
                       "shown only once: keep it like a password)"))
        ui.section("On each computer, inside its clone of the repository (e.g. in a script)")
        ui.out("  " + ui.cmd(f"hive join {r['code']} --server {shown} --as <its name>"))
        ui.out()
        ui.note("List keys with " + ui.cmd("hive machines keys") + ", revoke one with "
                + ui.cmd("hive machines revoke ID") + ".")
        return
    ui.ok(f"Pairing code {ui.paint(r['code'], 'bold', 'bright_yellow')} "
          + ui.dim(f"(works once, for {r['expires_in'] // 60} minutes)"))
    ui.section("On the other computer, inside its clone of the repository, run")
    ui.out("  " + ui.cmd(f"hive join {r['code']} --server {shown}"))
    ui.out()
    ui.note("Then place agents on it from here: "
            + ui.cmd("hive agents add claude --on <its name>"))


def cmd_machines_keys(a):
    rows = call(admin_client().get, "/machines/keys")
    if not rows:
        ui.info("No join keys. Make one for scripts with "
                + ui.cmd("hive machines invite --reusable --days 7"))
        return
    ui.table(["id", "label", "made", "expires", "used"],
             [[r["id"], r["label"] or ui.dim("-"), time.strftime("%Y-%m-%d", time.localtime(
                 r["created"])), time.strftime("%Y-%m-%d", time.localtime(r["expires"])),
               str(r["uses"])] for r in rows])


def cmd_machines_revoke(a):
    call(admin_client().delete, f"/machines/keys/{a.id}")
    ui.ok(f"Join key {a.id} revoked; computers it already paired stay paired.")


def cmd_machines_list(a):
    rows = call(admin_client().get, "/machines")
    if not rows:
        ui.info("No paired computers. Pair one with " + ui.cmd("hive machines invite")
                + "; agents on this computer don't need it (" + ui.cmd("hive agents add")
                + ").")
        return
    ui.table(["machine", "state", "os", "CLIs", "last seen", "agents"],
             [[ui.agent(m["name"]), ui.state("online" if m["online"] else "offline"),
               m["os"], ", ".join(m["clis"]) or ui.dim("none"), ago(m["last_seen"]) + " ago",
               ", ".join(m["agents"]) or ui.dim("none")] for m in rows])


def cmd_machines_rm(a):
    c = admin_client()
    if not a.yes:
        if not interactive():
            die(f"unpairing {a.name} can't be undone; add --yes to go ahead")
        if not yes(f"Unpair {a.name}? Its agents stay, but stop running there.",
                   default=False):
            die("nothing removed")
    r = call(c.delete, f"/machines/{a.name}")
    ui.ok(f"Unpaired {ui.agent(a.name)}; its token no longer works."
          + (f" Agents to place elsewhere: {', '.join(r['unplaced'])} "
             + ui.dim("(hive agents edit NAME --on MACHINE)") if r["unplaced"] else ""))


def place_on(c: ServerClient, name: str, machine: str | None, model: str = "",
             permission_mode: str = "") -> None:
    call(c.put, f"/agents/{name}/placement", {"machine": machine, "model": model,
                                              "permission_mode": permission_mode})
    if machine:
        ui.ok(f"{ui.agent(name)} is placed on {ui.agent(machine)}: it is set up there within a "
              f"minute if `hive up` runs there, else when it next starts.")
        ui.note("Log its account in on that computer, once: "
                + ui.cmd(f"hive agents login {name}")
                + ui.dim(" (hive up there offers it too)"))
    else:
        ui.ok(f"{ui.agent(name)} isn't placed on any paired computer now.")


# ---------------------------------------------------------------------- hive plan / apply

def _fleet_path(given: str | None) -> Path:
    from .fleetfile import FLEET_FILE
    if given:
        return Path(given)
    root = find_repo_root()
    return (root or Path.cwd()) / FLEET_FILE


def _fleet_plan(a):
    from . import fleetfile
    c = admin_client()
    url = c.base.removesuffix("/api/v1")
    roster = call(c.get, "/agents")
    local = _local_slots(url)
    machines_ = {m["name"] for m in call(c.get, "/machines")}
    try:
        wants = fleetfile.load(_fleet_path(a.file))
        p = fleetfile.plan(wants, roster, local, machines_, prune=a.prune)
    except fleetfile.FleetFileError as e:
        die(str(e))
    return c, roster, local, p


SYMBOL = {"create": ("+", "green"), "roles": ("~", "yellow"), "move": ("→", "cyan"),
          "settings": ("~", "yellow"), "remove": ("-", "red")}


def _show_plan(p) -> None:
    for ch in p.changes:
        sym, colour = SYMBOL[ch.kind]
        ui.out(f"  {ui.paint(sym, colour, 'bold')} {ui.agent(ch.name)}  "
               f"{ui.paint(ch.kind, colour)}  {ch.detail}")
    if p.ok:
        ui.out(ui.dim(f"  unchanged: {', '.join(p.ok)}"))
    if p.unlisted:
        ui.out(ui.dim(f"  not in the file, kept: {', '.join(p.unlisted)} (--prune removes "
                      "them)"))


def cmd_plan(a):
    _, _, _, p = _fleet_plan(a)
    if not p.changes:
        ui.ok("The fleet already matches the file.")
        _show_plan(p)
        return
    ui.section("hive apply would", ui.dim(f"{len(p.changes)} change(s)"))
    _show_plan(p)
    ui.out()
    ui.note("Make these changes: " + ui.cmd("hive apply" + (" --prune" if a.prune else "")))


def cmd_apply(a):
    from . import agents, fleetfile
    c, roster, local, p = _fleet_plan(a)
    if not p.changes:
        ui.ok("The fleet already matches the file.")
        return
    ui.section("Changes", ui.dim(f"{len(p.changes)}"))
    _show_plan(p)
    ui.out()
    if not a.yes:
        if not interactive():
            die("add --yes to make these changes")
        if not yes("Make these changes?", default=False):
            die("nothing changed")
    url = c.base.removesuffix("/api/v1")
    need_here = any(ch.want and ch.want.on == fleetfile.HERE and ch.kind in ("create", "move")
                    for ch in p.changes)
    repo = agents.clone_here() if need_here else None
    if need_here and repo is None:
        die("agents on this computer need its clone: run this inside it")
    order = ["remove", "roles", "create", "move", "settings"]
    new_here: list[Slot] = []
    for ch in sorted(p.changes, key=lambda ch: order.index(ch.kind)):
        w = ch.want
        if ch.kind == "remove":
            call(c.delete, f"/agents/{ch.name}")
            if ch.name in local:
                agents.unplace(ch.name)
            ui.ok(f"Removed {ui.agent(ch.name)}.")
        elif ch.kind == "roles":
            set_roles(c, ch.name, w.roles)
        elif ch.kind == "create":
            r = call(c.post, "/agents", {"name": w.name, "vendor": w.vendor, "roles": w.roles})
            ui.ok(f"Created {ui.agent(w.name)} ({fleetfile.describe_want(w)}).")
            if w.on == fleetfile.ELSEWHERE:
                ui.out("    " + ui.cmd(join_line(w.name, r["token"], c)))
            elif w.on == fleetfile.HERE:
                new_here.append(_set_up_here(w, r["token"], url, repo))
            else:
                place_on(c, w.name, w.on, w.model, w.permission_mode)
        elif ch.kind == "move":
            if w.on == fleetfile.HERE:
                call(c.put, f"/agents/{w.name}/placement", {"machine": None})
                token = call(c.post, f"/agents/{w.name}/token")["token"]
                new_here.append(_set_up_here(w, token, url, repo))
            else:
                place_on(c, w.name, w.on, w.model, w.permission_mode)
                if w.name in local and not local[w.name].placed:
                    agents.unplace(w.name)
        elif ch.kind == "settings":
            if w.name in local:
                cfg = load_machine_config()
                slot = cfg.slot(w.name)
                slot.model, slot.permission_mode = w.model, w.permission_mode
                save_machine_config(cfg)
            else:
                call(c.put, f"/agents/{w.name}/placement",
                     {"machine": w.on, "model": w.model, "permission_mode": w.permission_mode})
            ui.ok(f"{ui.agent(w.name)}: {ch.detail}")
    if new_here:
        offer_login(new_here)
        ui.note("Start the agents on this computer with " + ui.cmd("hive up")
                + ui.dim(" (restart it if it's running)"))


def _set_up_here(w, token: str, url: str, repo: Path) -> Slot:
    from . import agents
    try:
        slot, _ = agents.place(w.name, w.vendor, token, url, repo, model=w.model,
                               permission_mode=w.permission_mode)
    except agents.SetupError as e:
        die(f"couldn't set up {w.name} on this computer: {e}")
    ui.ok(f"Set up {ui.agent(w.name)} on this computer " + ui.dim(f"→ {slot.worktree}"))
    return slot


def cmd_agents_export(a):
    from . import fleetfile
    c = admin_client()
    text = fleetfile.export(call(c.get, "/agents"), _local_slots(c.base.removesuffix("/api/v1")))
    if a.output:
        Path(a.output).write_text(text, "utf-8")
        ui.ok(f"Wrote {ui.path(a.output)}. " + ui.cmd("hive plan") + " compares it with the "
              "fleet.")
    else:
        sys.stdout.write(text)


# ---------------------------------------------------------------------- hive config …

def _config_root() -> Path:
    root = find_repo_root()
    return root or die("no hive.toml here or above; run this inside a repository set up with "
                       + ui.cmd("hive init"))


def _config_target(a, key: str = "") -> tuple[Path | None, Path, str]:
    """(repository root or None, the file a change goes to, its layer name)."""
    from .config import LOCAL_ONLY_SECTIONS, user_config_path
    if getattr(a, "user", False):
        if a.local:
            die("choose one: --local or --user")
        return find_repo_root(), user_config_path(), "user"
    root = _config_root()
    if a.local:
        local = local_config_path(root)
        if local is None:
            die("this is an agent's worktree whose clone isn't recorded on this computer: run "
                "this in your own clone")
        return root, local, LOCAL_TOML
    if key.split(".")[0] in LOCAL_ONLY_SECTIONS:
        die(f"{key} is set per computer, never in the committed hive.toml: add --local (this "
            "repository on this computer) or --user (every repository on this computer)")
    return root, root / "hive.toml", "hive.toml"


def cmd_config_list(a):
    from . import settings
    root = find_repo_root()
    rows = settings.effective(root)
    if not a.all:
        rows = [r for r in rows if r[2] != "default"]
        if not rows:
            ui.info("Everything is at its default. " + ui.cmd("hive config list --all")
                    + " shows every setting.")
            return
    ui.table(["setting", "value", "from"],
             [[k, settings.show(v), ui.dim(src) if src == "default" else src]
              for k, v, src in rows])


def cmd_config_get(a):
    from . import settings
    try:
        e = settings.explain(find_repo_root(), a.key)
    except settings.SettingError as err:
        die(str(err))
    if e["source"] is None:
        die(f"{a.key} isn't set")
    ui.out(settings.show(e["value"]))


def cmd_config_explain(a):
    from . import settings
    root = find_repo_root()
    try:
        e = settings.explain(root, a.key)
    except settings.SettingError as err:
        die(str(err))
    ui.section(a.key, ui.dim(f"from {e['source']}") if e["source"] else ui.dim("not set"))
    ui.kv([("value", ui.bold(settings.show(e["value"])) if e["source"] else ui.dim("(none)"))]
          + [(layer, settings.show(v) if v is not None else ui.dim("—"))
             for layer, _, v in e["layers"]])
    ui.out()
    if a.key.startswith("hooks."):
        ui.note("Hooks run on this computer, from hive.local.toml or ~/.hive/config.toml only "
                "(never from hive.toml), while hive up runs here.")
    elif e["source"] in (LOCAL_TOML, "user"):
        ui.note(f"Set in {'hive.local.toml' if e['source'] == LOCAL_TOML else '~/.hive/config.toml'}"
                ": it applies on this computer only (its daemon and agents); the server and "
                "other computers use hive.toml's value.")
    elif e["source"] == "preset":
        ui.note("From the preset hive.toml names (fleet.preset); a value in hive.toml "
                "overrides it.")
    else:
        ui.note("hive.toml values apply to the whole fleet; " + ui.cmd(
            f"hive config set {a.key} <value> --local") + " overrides one on this computer.")


def _after_config_change(root: Path | None, key: str, local: bool) -> None:
    if local or root is None:
        return
    ui.note("Commit and push hive.toml: agents' sessions use it once it is on main.")
    if load_admin_config().server_url:
        try:
            c = admin_client(sync=False)
        except SystemExit:
            return
        sync_config(c)


def _validate_or_restore(root: Path, path: Path, before: str | None) -> None:
    """The edited files must still make a valid configuration; otherwise undo the edit."""
    from . import permissions
    from .config import preset_layer
    from .roles import load_roles
    try:
        cfg = load_repo_config(root)
        load_roles(cfg.get("roles"))
        permissions.build_rules(cfg)
        if root:
            preset_layer(_read_hive_toml(root))
    except (ValueError, TypeError) as e:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(before, "utf-8")
        die(f"not changed: {e}")


def _read_hive_toml(root: Path) -> dict:
    p = root / "hive.toml"
    return tomllib.loads(p.read_text("utf-8")) if p.exists() else {}


def cmd_config_set(a):
    from . import settings
    try:
        settings.split_key(a.key)
    except settings.SettingError as e:
        die(str(e))
    root, path, layer = _config_target(a, a.key)
    try:
        value = settings.parse_value(" ".join(a.value), a.key)
        before = path.read_text("utf-8") if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        settings.write(path, a.key, value)
    except settings.SettingError as e:
        die(str(e))
    _validate_or_restore(root, path, before)
    if layer == LOCAL_TOML and settings.ensure_ignored(path.parent):
        ui.info(f"Added {LOCAL_TOML} to .gitignore (it holds this computer's settings).")
    ui.ok(f"{a.key} = {settings.show(value)} " + ui.dim(f"in {path}" if layer == "user"
                                                         else f"in {path.name}"))
    _after_config_change(root, a.key, layer != "hive.toml")


def cmd_config_unset(a):
    from . import settings
    root, path, layer = _config_target(a)
    try:
        before = path.read_text("utf-8") if path.exists() else None
        removed = settings.unset(path, a.key)
    except settings.SettingError as e:
        die(str(e))
    if not removed:
        die(f"{a.key} isn't set in {path.name}")
    _validate_or_restore(root, path, before)
    now_ = settings.explain(root, a.key)
    ui.ok(f"Removed {a.key} from {path.name}; it is now "
          f"{settings.show(now_['value'])} " + ui.dim(f"(from {now_['source']})"))
    _after_config_change(root, a.key, layer != "hive.toml")


def cmd_config_edit(a):
    root, path, layer = _config_target(a)
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or \
        ("notepad" if os.name == "nt" else "vi")
    before = path.read_text("utf-8") if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([*editor.split(), str(path)])
    if path.exists() and path.read_text("utf-8") != (before or ""):
        _validate_or_restore(root, path, before)
        ui.ok(f"Saved {path.name}.")
        _after_config_change(root, "", layer != "hive.toml")


# ---------------------------------------------------------------------- escape hatches

def no_agent_session(what: str) -> None:
    """Commands that act with the admin's login must not run inside an agent's session, even
    when an agent found a way to start them (its shell rules also forbid them)."""
    if os.environ.get("HIVE_SLOT"):
        die(f"{what} acts as the admin, so it doesn't run inside an agent's session")


def cmd_api(a):
    """Raw access to the server's API with your admin login, like `gh api`."""
    no_agent_session("hive api")
    body = None
    if a.data is not None:
        text = sys.stdin.read() if a.data == "-" else a.data
        try:
            body = json.loads(text) if text.strip() else {}
        except ValueError as e:
            die(f"the body must be JSON: {e}")
    path = "/" + a.path.strip("/").removeprefix("api/v1/")
    bad = [f for f in a.field or [] if "=" not in f]
    if bad:
        die(f"-f takes KEY=VALUE, not {bad[0]!r}")
    params = dict(f.split("=", 1) for f in a.field or []) or None
    c = admin_client(sync=False)
    out = call(c.request, a.method.upper(), path, json=body, params=params)
    sys.stdout.write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")


def find_plugin(args: list[str], commands) -> tuple[str, list[str]] | None:
    """`hive NAME …` for a NAME that isn't a Hive command runs `hive-NAME …` from PATH, as git,
    kubectl and cargo do with their extensions."""
    it = iter(enumerate(args))
    for i, arg in it:
        if arg in ("--color", "--project"):
            next(it, None)
            continue
        if arg.startswith("-"):
            continue
        if arg in commands:
            return None
        exe = shutil.which(f"hive-{arg}")
        return (exe, args[i + 1:]) if exe else None
    return None


def run_plugin(exe: str, rest: list[str]) -> None:
    no_agent_session(f"the plugin {Path(exe).name}")
    env = dict(os.environ, HIVE_HOME=str(hive_home()))
    if PROJECT:
        env["HIVE_PROJECT"] = PROJECT
    try:
        code = subprocess.run([exe, *rest], env=env).returncode
    except KeyboardInterrupt:
        code = 130
    raise SystemExit(code)


# ---------------------------------------------------------------------- hive (no command)

def cmd_home(a, chat: bool = True):
    """Where things stand, and the one next step: the first rung of docs/CLI_REDESIGN.md.
    Once the team exists and runs, in a terminal, it opens the chat with it instead."""
    from . import agents
    import requests
    cfg = load_admin_config()
    url, token = cfg.target(PROJECT)

    def step(what: str, command: str, why: str = "") -> None:
        ui.section("Next")
        ui.out(f"  {what}")
        ui.out(f"    {ui.cmd(command)}")
        if why:
            ui.out("  " + ui.dim(why))
        ui.out()
        ui.out(ui.dim("  hive help: the common commands · hive doctor: check everything"))

    ui.banner(f"Hive {__version__}", "several coding agents, one coordinated team")
    mcfg = load_machine_config()
    if (not url or not token) and mcfg.machines:
        # A paired computer that runs agents but isn't where the admin works.
        ui.kv([("paired with", ", ".join(f"{ui.path(u)} as {m['name']}"
                                         for u, m in mcfg.machines.items())),
               ("agents here", ", ".join(s.name for s in mcfg.slots) or ui.dim("none yet"))])
        for s in mcfg.slots:
            if agents.is_logged_in(s) is False:
                return step(f"Log {s.name}'s {s.vendor} account in once:",
                            f"hive agents login {s.name}")
        try:
            requests.get(mcfg.local_url + "/slots", timeout=1,
                         headers={"X-Hive-Local": mcfg.local_secret})
        except requests.RequestException:
            return step("Run the agents placed on this computer (in its own terminal):",
                        "hive up", "New ones are picked up while it runs.")
        return step("This computer is running its agents. Place more on it from the admin's "
                    "computer:", "hive agents add claude --on "
                    + next(iter(mcfg.machines.values()))["name"])
    if not url or not token:
        return step("Start a Hive server on this computer (in its own terminal):",
                    "hive server", "It logs this computer in. For a team server someone "
                    "else runs: hive login --server URL")
    repo = agents.clone_here()
    if repo is None:
        return step("Go to your project: Hive works inside a clone of its git repository.",
                    "cd path/to/your/repo && hive")
    if not (repo / "hive.toml").exists():
        return step(f"Set up {repo.name} for Hive:", "hive init",
                    "It writes hive.toml and the files agents use; then commit and push them.")
    c = ServerClient(url, token)
    try:
        sync_config(c)
        roster = c.get("/agents")
        fleet = c.get("/fleet")
        open_ = [d for d in c.get("/decisions", state="open") if d["state"] == "open"]
    except HiveError as e:
        if e.code == "unreachable":
            local = split_project_url(url)[0].split("//")[-1].split(":")[0] in LOCAL_HOSTS
            return step(f"The server at {url} isn't answering.",
                        "hive server" if local else "hive doctor",
                        "Start it in its own terminal." if local else "")
        fail(e)
    ui.kv([("project", repo.name), ("server", ui.path(url)),
           ("fleet", ui.state(fleet["state"])),
           ("agents", ", ".join(f"{ag['name']} ({', '.join(ag['roles'])})" for ag in roster)
            or ui.dim("none yet"))])
    if not any("orchestrator" in ag["roles"] for ag in roster):
        return step("Add the agent that plans the work (you'll chat with it):",
                    "hive agents add claude", "Any CLI works: claude, codex, gemini or agy.")
    from .roles import load_roles
    try:
        cat = load_roles(load_repo_config(repo).get("roles"))
    except ValueError:
        cat = {}
    if not any(cat.get(r) and cat[r].can_code for ag in roster for r in ag["roles"]):
        return step("Add an agent that writes code:", "hive agents add claude programmer",
                    "Use another account than the orchestrator's. Add several at once: "
                    "hive agents add 3 claude programmers")
    here = [s for s in mcfg.slots if mcfg.url_for(s).rstrip("/") == url.rstrip("/")]
    for s in here:
        if agents.is_logged_in(s) is False:
            return step(f"Log {s.name}'s {s.vendor} account in once:",
                        f"hive agents login {s.name}")
    if here:
        try:
            requests.get(mcfg.local_url + "/slots", timeout=1,
                         headers={"X-Hive-Local": mcfg.local_secret})
        except requests.RequestException:
            return step("Start this computer's agents (in its own terminal):", "hive up",
                        "Then run hive again here to talk to the team.")
    if chat and interactive():
        # The team exists and runs: from here on, `hive` is the conversation with it.
        return open_team_chat(c)
    if open_:
        d = open_[0]
        ui.section(f"Waiting for you: decision #{d['id']}")
        ui.out("  " + ui.bold(d["question"].splitlines()[0][:200]))
        ref = f" {d['id']}" if len(open_) > 1 else ""
        return step("Answer it:", "  ·  ".join(answer_commands(d, ref)),
                    "hive decisions shows the whole question.")
    if fleet["state"] == "setup":
        return step("Give the team a goal:", 'hive start "Add CSV export to the reports page"')
    step("The team is at work. Follow along:", "hive status",
         "Or: hive watch (live output) · hive say \"…\" (message the orchestrator)")


HELP = """\
usage: hive [COMMAND] ...

Several coding agents, one coordinated team. Run hive on its own: it shows the next step,
and once the team runs, it is the chat with it (hive next always shows the step).

Set up:
  hive server                    run a Hive server on this computer (logs you in)
  hive login                     log in to a Hive server someone else runs
  hive init                      set up this repository for Hive
  hive agents add CLI [ROLES]    add agents on this computer, e.g. hive agents add 2 codex
  hive up                        run this computer's agents

Work with the team:
  hive                           the chat with the team (/help inside it)
  hive start "GOAL"              give the team a goal
  hive status                    what is going on
  hive decisions                 what the team is asking you
  hive approve / hive reject     answer the plan or a yes/no question
  hive answer "TEXT"             answer any other question
  hive say "TEXT"                message the orchestrator
  hive watch [AGENT]             follow live output
  hive pause / hive resume       stop and restart all agents

Manage:
  hive agents                    list, add, login, edit, rm, rotate
  hive machines                  list, invite (pair another computer), rm
  hive plan / hive apply         compare / match the fleet to hive.fleet.toml
  hive join CODE --server URL    pair this computer, with a code from hive machines invite
  hive config                    list, get, set, unset, explain, edit
  hive doctor                    check the whole setup and list fixes

More:
  hive help TOPIC                how something works, e.g. hive help hooks
  hive help topics               every help topic
  hive help --all                every command, including hive admin ...
  hive COMMAND --help            one command's options
"""


class TopParser(ui.ArgumentParser):
    """`hive --help` shows the everyday commands; `hive help --all` argparse's full list."""

    full = False

    def format_help(self) -> str:
        if self.full:
            return super().format_help()
        return ui.colorize_help(HELP, sys.stdout)


def _subparser(parser: argparse.ArgumentParser, words: list[str]):
    """The parser of the command `words` (["agents", "add"]), or None if it isn't one."""
    for w in words:
        sub = next((x for x in parser._actions
                    if isinstance(x, argparse._SubParsersAction)), None)
        if sub is None or w not in sub.choices:
            return None
        parser = sub.choices[w]
    return parser


def cmd_help(a, parser: TopParser):
    """`hive help`: the common commands; `hive help TOPIC`: that help page (hive/help/), with
    the command's options when TOPIC is a command; `hive help topics`: every page."""
    from . import helpdocs
    words = [w for w in a.topic if w.lower() != "hive"]
    if not words:
        parser.full = a.all
        parser.print_help()
        return
    try:
        topics = helpdocs.load()
    except helpdocs.HelpError as e:
        die(str(e))
    if [w.lower() for w in words] in (["topics"], ["list"]):
        ui.out(helpdocs.index(topics, sys.stdout), end="")
        return
    command = _subparser(parser, [w.lower() for w in words])
    m = helpdocs.find(words, topics)
    if m.topic is None and command is not None:  # a command without its own page
        command.print_help()
        return
    if m.topic is not None:
        ui.out(helpdocs.render(m.topic, sys.stdout), end="")
        if command is not None:
            ui.out("")
            ui.out(ui.paint(f"Options of hive {' '.join(words).lower()}", "bold",
                            "bright_yellow", stream=sys.stdout))
            ui.out(ui.colorize_help(command.format_help(), sys.stdout).rstrip())
        if m.topic.see:
            ui.out("")
            ui.out(ui.dim("See also: ") + ", ".join(ui.cmd(f"hive help {s}")
                                                    for s in m.topic.see))
        return
    q = " ".join(words)
    if m.choices:
        ui.out(f"Several help topics cover {ui.bold(q)}:")
        for t in m.choices:
            ui.out(f"  {ui.cmd(('hive help ' + t.name).ljust(26))}{t.title}")
        return
    hint = (" Did you mean " + " or ".join(ui.cmd(f"hive help {s}") for s in m.suggestions)
            + "?") if m.suggestions else ""
    die(f"no help topic for {q!r}.{hint} `hive help topics` lists them all")


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
    p = TopParser(prog="hive", description="Several coding agents, one coordinated team. Run "
                                           "`hive` alone to see what to do next.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--color", choices=["auto", "always", "never"], default="auto",
                   help="colour the output (default: auto = only on a terminal; also "
                        "HIVE_COLOR, NO_COLOR)")
    p.add_argument("--project", help="the project admin commands act on, on your admin "
                                     "server (default: the one this repository's hive.toml "
                                     "names, else the server's default project; also "
                                     "HIVE_PROJECT)")
    p.set_defaults(fn=cmd_home)
    sub = p.add_subparsers(dest="cmd", parser_class=ui.ArgumentParser)  # not TopParser

    s = sub.add_parser("help", help="the common commands; with a topic, how it works in "
                                    "detail (--all: every command)")
    s.add_argument("--all", action="store_true", help="every command")
    s.add_argument("topic", nargs="*", help="a command or subject to explain, e.g. agents add, "
                                            "hooks, permissions (topics: all of them)")
    s.set_defaults(fn=lambda a: cmd_help(a, p))

    s = sub.add_parser("server", help="run a Hive server (for trials and local networks)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--db", default="sqlite:///hive.db")
    s.add_argument("--sqlite-journal", choices=["wal", "delete", "truncate"],
                   help="SQLite journal mode (default: wal; delete on network filesystems)")
    s.add_argument("--admin-token", help="default: $HIVE_ADMIN_TOKEN, else one made and saved "
                                         "in ~/.hive/server.toml")
    s.set_defaults(fn=cmd_server)

    def login_args(s):
        s.add_argument("--server", help="server URL, or a project's (…/p/NAME); default: "
                                        "hive.toml's, asked for in a terminal")
        s.add_argument("--token", help="admin token (default: asked for, without echoing it)")
        s.set_defaults(fn=cmd_admin_login)

    login_args(sub.add_parser("login", help="log in to a Hive server as its admin"))

    s = sub.add_parser("agents", help="list, add, log in, change or remove agents")
    s.set_defaults(fn=cmd_agents_list)
    ag = s.add_subparsers(dest="asub")
    s = ag.add_parser("list", help="every agent: roles, state, where it runs, login")
    s.set_defaults(fn=cmd_agents_list)
    s = ag.add_parser("add", help="create agents and set them up on this computer",
                      description="examples:\n"
                                  "  hive agents add claude\n"
                                  "  hive agents add 3 codex programmers\n"
                                  "  hive agents add gemini reviewer --elsewhere\n\n"
                                  "Without roles, the fleet's first agent is its orchestrator "
                                  "and later ones are\nprogrammers.")
    s.add_argument("words", nargs="*", metavar="[N] CLI [ROLES]",
                   help=f"how many, which CLI ({', '.join(VENDORS)}) and roles, in any order")
    s.add_argument("--name", help="name it (default: <cli>-<n>)")
    s.add_argument("--on", metavar="MACHINE",
                   help="run it on this paired computer (see hive machines)")
    s.add_argument("--elsewhere", action="store_true",
                   help="it runs on another, unpaired computer: print the line to run there")
    s.add_argument("--model", help="the AI model its CLI uses (default: the account's)")
    s.add_argument("--permission-mode", help="the CLI's own permission mode (see hive join)")
    s.add_argument("--no-login", action="store_true", help="don't offer to log in now")
    s.set_defaults(fn=cmd_agents_add)
    s = ag.add_parser("login", help="log an agent's CLI account in (once per agent)")
    s.add_argument("name")
    s.add_argument("--device", action="store_true",
                   help="codex: log in with a code on another device (over SSH)")
    s.set_defaults(fn=cmd_agents_login)
    s = ag.add_parser("edit", help="change an agent's roles, model or permission mode")
    s.add_argument("name")
    s.add_argument("--roles", help="replaces its roles, e.g. programmer,tester")
    s.add_argument("--model", help="this computer's slot; `default` for the account's")
    s.add_argument("--permission-mode", help="this computer's slot; `default` to reset")
    s.add_argument("--on", metavar="MACHINE", help="move it to this paired computer "
                                                   "(`none`: no paired computer)")
    s.set_defaults(fn=cmd_agents_edit)
    s = ag.add_parser("rm", help="remove an agent (server, and this computer)")
    s.add_argument("name")
    s.add_argument("-y", "--yes", action="store_true", help="don't ask first")
    s.set_defaults(fn=cmd_agents_rm)
    s = ag.add_parser("rotate", help="give an agent a new token (the old one stops working)")
    s.add_argument("name")
    s.set_defaults(fn=cmd_agents_rotate)
    s = ag.add_parser("export", help="write the fleet as a file for hive plan / hive apply")
    s.add_argument("-o", "--output", help="file to write (default: print it)")
    s.set_defaults(fn=cmd_agents_export)

    for verb, fn, text in (("plan", cmd_plan, "show how the fleet differs from "
                            "hive.fleet.toml"),
                           ("apply", cmd_apply, "make the fleet match hive.fleet.toml")):
        s = sub.add_parser(verb, help=text)
        s.add_argument("file", nargs="?", help="default: hive.fleet.toml next to hive.toml")
        s.add_argument("--prune", action="store_true",
                       help="also remove agents the file doesn't list")
        if verb == "apply":
            s.add_argument("-y", "--yes", action="store_true", help="don't ask first")
        s.set_defaults(fn=fn)

    s = sub.add_parser("machines", help="pair other computers and see them")
    s.set_defaults(fn=cmd_machines_list)
    mg = s.add_subparsers(dest="msub")
    s = mg.add_parser("list", help="paired computers: state, CLIs, agents")
    s.set_defaults(fn=cmd_machines_list)
    s = mg.add_parser("invite", help="a one-time code that pairs another computer")
    s.add_argument("--reusable", action="store_true",
                   help="a join key that pairs many computers (for scripts)")
    s.add_argument("--days", type=float, default=7, help="how long a join key works (7)")
    s.add_argument("--label", help="a note to recognise the join key by")
    s.set_defaults(fn=cmd_machines_invite)
    s = mg.add_parser("keys", help="the reusable join keys that still work")
    s.set_defaults(fn=cmd_machines_keys)
    s = mg.add_parser("revoke", help="stop a join key from pairing more computers")
    s.add_argument("id", help="as hive machines keys shows it")
    s.set_defaults(fn=cmd_machines_revoke)
    s = mg.add_parser("rm", help="unpair a computer (its agents stay, unplaced)")
    s.add_argument("name")
    s.add_argument("-y", "--yes", action="store_true", help="don't ask first")
    s.set_defaults(fn=cmd_machines_rm)

    s = sub.add_parser("config", help="read and change settings (hive.toml, hive.local.toml)")
    s.set_defaults(fn=cmd_config_list, all=False)
    cg = s.add_subparsers(dest="csub")
    s = cg.add_parser("list", help="the settings in force, and where each comes from")
    s.add_argument("--all", action="store_true", help="include those at their default")
    s.set_defaults(fn=cmd_config_list)
    s = cg.add_parser("get", help="print one setting's value")
    s.add_argument("key", help="e.g. integration.review")
    s.set_defaults(fn=cmd_config_get)
    s = cg.add_parser("explain", help="a setting's value in every layer, and which wins")
    s.add_argument("key")
    s.set_defaults(fn=cmd_config_explain)
    for verb, fn, text in (("set", cmd_config_set, "change a setting"),
                           ("unset", cmd_config_unset, "go back to the value below it")):
        s = cg.add_parser(verb, help=f"{text} (hive.toml; --local: this computer only)")
        s.add_argument("key")
        if verb == "set":
            s.add_argument("value", nargs="+", help='TOML (true, 90, ["npm test"]) or text')
        s.add_argument("--local", action="store_true",
                       help=f"in {LOCAL_TOML}: this computer only, never uploaded")
        s.add_argument("--user", action="store_true",
                       help="in ~/.hive/config.toml: you, in every repository on this computer")
        s.set_defaults(fn=fn)
    s = cg.add_parser("edit", help="open hive.toml (--local: hive.local.toml, --user: "
                                   "~/.hive/config.toml) in your editor")
    s.add_argument("--local", action="store_true")
    s.add_argument("--user", action="store_true")
    s.set_defaults(fn=cmd_config_edit)

    s = sub.add_parser("approve", help="approve the plan (or the yes/no question) waiting")
    s.add_argument("words", nargs="*", metavar="[ID] [NOTE]",
                   help="the decision's number, only needed when several are open")
    s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("reject", help="send the plan back, saying what to change")
    s.add_argument("words", nargs="*", metavar="[ID] [NOTE]")
    s.set_defaults(fn=cmd_reject)
    s = sub.add_parser("answer", help="answer the question waiting")
    s.add_argument("words", nargs="*", metavar="[ID] TEXT")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_answer)

    adm = sub.add_parser("admin", help="exact low-level commands (scripts, unusual setups)") \
        .add_subparsers(dest="sub", required=True)
    login_args(adm.add_parser("login", help="save an admin token (the server's, or a "
                                            "project's)"))
    prj = adm.add_parser("project", help="manage the server's projects (server admin only)")         .add_subparsers(dest="psub", required=True)
    s = prj.add_parser("create", help="add a project and print its admin token")
    s.add_argument("slug", help="lowercase name, used in its URL: /p/<slug>")
    s.add_argument("--name", help="display name")
    s.add_argument("--db-url", help="its database (default: a SQLite file next to the "
                                    "server's, or HIVE_PROJECT_DATABASE_URL on the server)")
    s.add_argument("--github-token-env", help="server environment variable holding the "
                                              "GitHub token that may move this repo's main")
    s.set_defaults(fn=cmd_project_create)
    s = prj.add_parser("list", help="list the projects")
    s.set_defaults(fn=cmd_project_list)
    s = prj.add_parser("set", help="rename, archive or change a project's GitHub token")
    s.add_argument("slug")
    s.add_argument("--name")
    s.add_argument("--github-token-env")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--archive", action="store_true", help="stop serving it (data is kept)")
    g.add_argument("--unarchive", action="store_true")
    s.set_defaults(fn=cmd_project_set)
    s = prj.add_parser("rotate", help="replace a project's admin token")
    s.add_argument("slug")
    s.set_defaults(fn=cmd_project_rotate)
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

    s = sub.add_parser("init", help="set up this repository for Hive")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--server")
    s.add_argument("--main", help="the branch finished work goes into (default: origin's "
                                  "default branch, else the current one)")
    s.add_argument("--hive-cmd", default="hive")
    s.set_defaults(fn=cmd_init)

    for name in ("doctor", "overview"):
        s = sub.add_parser(name, help="check this project's whole Hive setup, with fixes"
                           + ("" if name == "doctor" else " (same as hive doctor)"))
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

    s = sub.add_parser("join", help="pair this computer with a code, or set up one agent",
                       description="hive join CODE --server URL: pair this computer (the code "
                                   "is from `hive machines invite`).\nhive join NAME --token T: "
                                   "set up the agent NAME created elsewhere.")
    s.add_argument("name", help="a pairing code, or an agent's name (with --token)")
    s.add_argument("--token", help="the agent's token (not needed with a pairing code)")
    s.add_argument("--as", dest="machine_name",
                   help="with a code: this computer's name (default: its host name)")
    s.add_argument("--vendor", choices=VENDORS, help="default: the one it was created with")
    s.add_argument("--server")
    s.add_argument("--repo", help="local clone; the slot worktree is created next to it "
                                  "(default: the clone you're in)")
    s.add_argument("--worktree", help="explicit worktree path")
    s.add_argument("--config-dir", help="CLI account config dir (default ~/.hive/accounts/NAME)")
    s.add_argument("--permission-mode", help="claude: acceptEdits|bypassPermissions|…; "
                                             "gemini: auto_edit|yolo; codex: workspace-write|"
                                             "read-only|danger-full-access|yolo; "
                                             "agy: accept-edits|auto|plan")
    s.add_argument("--model", help="model for this slot's CLI (default: the "
                                   "account's default model)")
    s.add_argument("--hive-cmd", help="how the git hook calls hive (default: the one it "
                                      "already uses, else the one hive init set)")
    s.add_argument("--replace", action="store_true",
                   help="move an existing slot of this name to another server or project")
    s.set_defaults(fn=cmd_join)

    s = sub.add_parser("up", help="run this machine's slots (orchestrator: native interactive "
                                  "CLI; workers: headless with live output)")
    s.add_argument("-v", "--verbose", action="store_true")
    s.add_argument("--headless", action="store_true",
                   help="drive the orchestrator headlessly too (no interactive session)")
    s.add_argument("--orchestrator", metavar="NAME",
                   help="the orchestrator slot to attach to, if this machine has several "
                        "(default: the one of --project, or of the repository you're in)")
    s.set_defaults(fn=cmd_up)

    s = sub.add_parser("watch", help="follow agents' live output (admin only)")
    s.add_argument("agent", nargs="?", help="one agent (default: all)")
    s.add_argument("-n", type=int, default=200, help="initial lines")
    s.add_argument("--no-follow", dest="follow", action="store_false")
    s.set_defaults(fn=cmd_watch)

    s = sub.add_parser("chat", help="talk to the team (or, with a name, open that agent's own "
                                    "CLI session here)")
    s.add_argument("slot", nargs="?", help="an agent on this computer: attach to its session")
    s.add_argument("--fresh", action="store_true", help="with a name: start a new session")
    s.set_defaults(fn=cmd_chat)
    s = sub.add_parser("next", help="where things stand and the next step (hive without the "
                                    "chat)")
    s.set_defaults(fn=lambda a: cmd_home(a, chat=False))

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

    s = sub.add_parser("api", help="call the server's API with your admin login (scripts)",
                       description="examples:\n  hive api GET /fleet\n  hive api GET /tasks -f "
                                   "state=open\n  hive api POST /messages -d '{\"channel\": "
                                   "\"#user\", \"body\": \"hi\"}'")
    s.add_argument("method", choices=["GET", "POST", "PUT", "PATCH", "DELETE", "get", "post",
                                      "put", "patch", "delete"])
    s.add_argument("path", help="e.g. /fleet, /agents, /tasks/3 (see docs/SERVER.md)")
    s.add_argument("-d", "--data", help="JSON body (`-` reads it from standard input)")
    s.add_argument("-f", "--field", action="append", help="query parameter KEY=VALUE")
    s.set_defaults(fn=cmd_api)

    # --project and --color may come anywhere (`hive status --project acme`); the root parser
    # owns them. --color was applied above.
    rest, project = [], None
    it = iter(args)
    for arg in it:
        if arg == "--project":
            project = next(it, None)
        elif arg.startswith("--project="):
            project = arg.split("=", 1)[1]
        elif arg == "--color":
            next(it, None)
        elif not arg.startswith("--color="):
            rest.append(arg)
    global PROJECT
    plugin = find_plugin(rest, sub.choices)
    if plugin:
        PROJECT = project
        run_plugin(*plugin)
    a = p.parse_args((["--project", project] if project is not None else []) + rest)
    PROJECT = a.project
    a.fn(a)


if __name__ == "__main__":
    main()
