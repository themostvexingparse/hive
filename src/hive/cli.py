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

from . import __version__
from .config import (AdminConfig, Slot, find_repo_root, hive_home, load_admin_config,
                     load_machine_config, load_repo_config, save_admin_config,
                     save_machine_config)
from .daemon.client import HiveError, ServerClient


def die(msg: str) -> None:
    print(f"hive: {msg}", file=sys.stderr)
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
        die(f"{e.code}: {e.message}" + (f"\n{json.dumps(detail, indent=1)}" if detail else ""))


def ago(ts: float | None) -> str:
    if not ts:
        return "never"
    s = int(time.time() - ts)
    return f"{s}s" if s < 60 else f"{s // 60}m" if s < 3600 else f"{s // 3600}h"


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
    create_app().run(host=a.host, port=a.port, threaded=True)


def cmd_admin_login(a):
    c = ServerClient(a.server, a.token)
    call(c.get, "/fleet")
    save_admin_config(AdminConfig(a.server, a.token))
    print(f"Logged in to {a.server}")


def cmd_admin_enroll(a):
    c = admin_client()
    roles = [r.strip() for r in a.roles.split(",") if r.strip()]
    r = call(c.post, "/agents", {"name": a.name, "vendor": a.vendor, "roles": roles})
    print(f"Enrolled {r['name']} ({a.vendor}; roles: {', '.join(roles)}).\n"
          f"Token (shown once): {r['token']}\n\nOn the agent's machine run:\n"
          f"  hive join {r['name']} --vendor {a.vendor} --token {r['token']} "
          f"--server {c.base.removesuffix('/api/v1')} --repo <path-to-clone>")


def cmd_admin_roles(a):
    c = admin_client()
    roles = [r.strip() for r in a.roles.split(",") if r.strip()]
    print(call(c.patch, f"/agents/{a.name}", {"roles": roles}))


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
    print(f"Saved {out} ({out.stat().st_size / 1e6:.1f} MB)")


def cmd_admin_push_config(a):
    root = find_repo_root() or die("no hive.toml found (run `hive init`)")
    with open(root / "hive.toml", "rb") as f:
        data = tomllib.load(f)
    call(admin_client().put, "/config", data)
    print("Uploaded hive.toml to the server.")


# ---------------------------------------------------------------------- repo & machine setup

def cmd_init(a):
    from .init import init_repo
    root = Path(a.path).resolve()
    if not (root / ".git").exists():
        die(f"{root} is not a git repository")
    server = a.server or load_admin_config().server_url or "http://127.0.0.1:8765"
    written, cleaned = init_repo(root, server, a.main, a.hive_cmd)
    for f in written:
        print(f"  wrote {f}")
    for f in cleaned:
        print(f"  removed old hive wiring from {f}")
    print("\nThe agent bundles in .hive/agent/ load only in sessions launched by `hive`.\n"
          "Next: review hive.toml, commit these files, push, then `hive admin push-config`.")


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
            print(f"Installed git pre-commit hook at {hook}")
    config_dir = a.config_dir or str(hive_home() / "accounts" / a.name)
    Path(config_dir).mkdir(parents=True, exist_ok=True)
    cfg.slots = [s for s in cfg.slots if s.name != a.name] + [Slot(
        name=a.name, vendor=a.vendor, token=a.token, worktree=str(wt), config_dir=config_dir,
        permission_mode=a.permission_mode or "")]
    save_machine_config(cfg)
    var = "CLAUDE_CONFIG_DIR" if a.vendor == "claude" else "GEMINI_CLI_HOME"
    print(f"Slot {a.name} ready: worktree {wt}\n"
          f"Log this slot's account in once (interactive), from the worktree:\n"
          + login_instructions(var, config_dir, str(wt), a.vendor)
          + f"\n(or simply `hive chat {a.name}` while `hive up` runs). Then run `hive up`.")


def login_instructions(var: str, config_dir: str, worktree: str, cli: str) -> str:
    """Shell-appropriate commands to start a CLI with a private config folder."""
    if os.name == "nt":
        return (f"  PowerShell:  cd \"{worktree}\"; $env:{var} = \"{config_dir}\"; {cli}; "
                f"Remove-Item Env:{var}\n"
                f"  cmd.exe:     cd /d \"{worktree}\" && set \"{var}={config_dir}\" && {cli}")
    return f"  cd '{worktree}' && {var}='{config_dir}' {cli}"


def run_interactive(slot: Slot, cfg, fresh: bool = False, initial: str | None = None,
                    orchestrator: bool = False) -> int:
    """Launch the slot's native CLI in this terminal with Hive attached for this session only."""
    import uuid
    from .daemon import wake
    from .daemon.store import Store
    store = Store(hive_home() / "state.db")
    session = None if fresh else store.get(slot.name, "session")
    resume = bool(session)
    session = session or str(uuid.uuid4())
    store.put(slot.name, "session", session)
    env = wake.slot_env(slot, cfg.local_url, cfg.local_secret)
    err = wake.ensure_gemini_extension(slot, env)
    if err:
        print(f"Warning: {err}. Hive tools will be missing in this session.")
    if initial is None and orchestrator:
        initial = wake.ORCH_RESUME_PROMPT if resume else wake.ORCH_FIRST_PROMPT
    cmd = wake.build_interactive_command(slot, session, resume, initial,
                                         "orchestrator" if orchestrator else "worker")
    p = subprocess.run(wake.resolve(cmd), cwd=slot.worktree, env=env)
    if p.returncode != 0 and resume:
        # The stored session may be gone (e.g. different machine); start a fresh one next time.
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
    cfg = load_machine_config()
    if not cfg.slots:
        die("no slots on this machine; run `hive join` first")
    save_machine_config(cfg)  # persists a generated local secret
    refresh_git_hooks(cfg)
    d = Daemon(cfg)
    # Each slot's roles decide how it runs, so they must be known before anything starts.
    print(f"Connecting to {cfg.server_url} ...")
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
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    srv = serve(d)
    names = ", ".join(f"{s.name} ({', '.join(s.roles) or '?'})" for s in d.slots.values())
    print(f"hive daemon up - server {cfg.server_url} - slots: {names}")
    if not orch:
        print("Streaming agent output below (also visible to the admin via `hive watch`).")
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
        print(f"Workers on this machine ({', '.join(others)}) run in the background; "
              "watch them with `hive watch`.")
    try:
        while True:
            print(f"Launching {orch.cfg.vendor} as orchestrator {orch.name}...")
            run_interactive(orch.cfg, cfg, orchestrator=True)
            try:
                ans = input("\nOrchestrator session ended. Relaunch? [Y/n] ").strip().lower()
            except EOFError:
                ans = "n"
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
            print("Note: a headless wake is running for this slot; it will finish first.")
        slots = requests.get(f"{cfg.local_url}/slots", headers=hdr, timeout=5).json()
        is_orch = any(s["name"] == slot.name and "orchestrator" in s["roles"] for s in slots)
    except (requests.RequestException, ValueError, KeyError):
        print("Warning: daemon not reachable; hooks and tools will not work until `hive up`.")
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
    try:
        while True:
            r = call(c.get, "/streams", agent=a.agent, since_id=last,
                     limit=a.n if first else 1000)
            for line in r["lines"]:
                prefix = "" if a.agent else f"[{line['agent']}] "
                print(f"{prefix}{line['text']}", flush=True)
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
    print(f"Fleet is {f['state']}; orchestrator {f['orchestrator']} is planning. You will be asked "
          "to approve the plan (`hive decisions`).")


def cmd_decisions(a):
    rows = call(admin_client().get, "/decisions", state=None if a.all else "open,queued")
    if not rows:
        print("No open decisions.")
    for d in rows:
        print(f"#{d['id']} [{d['state']}] {d['kind']}{' (freezes fleet)' if d['blocking'] else ''}"
              f" from {d['raised_by']}, {ago(d['created'])} ago")
        print("  " + d["question"].replace("\n", "\n  "))
        if d["context"]:
            print("  --- context ---\n  " + d["context"].replace("\n", "\n  "))
        if d["options"]:
            print(f"  options: {', '.join(d['options'])}")
        if d["answer"]:
            print(f"  answer: {d['answer']} {d['note'] or ''}")
        print()


def cmd_decide(a):
    d = call(admin_client().post, f"/decisions/{a.id}/answer", {"answer": a.answer,
                                                                "note": a.note or ""})
    print(f"Decision #{d['id']} answered: {d['answer']}")


def cmd_say(a):
    call(admin_client().post, "/messages", {"channel": "#user", "body": a.text,
                                            "priority": "high"})
    print("Sent to #user; the orchestrator will be woken.")


def cmd_status(a):
    c = admin_client()
    f = call(c.get, "/fleet")
    print(f"Fleet: {f['state']} (since {ago(f['since'])} ago) · orchestrator: {f['orchestrator']}"
          f" · review: {f.get('review')} · main: {(f['main_sha'] or '')[:10]}")
    if f.get("goal"):
        print(f"Goal: {f['goal'][:200]}")
    if f.get("decision"):
        print(f"!! Waiting for your decision #{f['decision']['id']}: "
              f"{f['decision']['question'][:200]}  →  hive decisions")
    print("\nAgents:")
    for ag in call(c.get, "/agents"):
        st = "out of quota" if ag["exhausted"] else ag["status"] if ag["online"] else "offline"
        print(f"  {ag['name']:<14} {ag['vendor']:<7} {','.join(ag['roles']):<24} {st}")
    tasks = call(c.get, "/tasks")
    if tasks:
        print("\nTasks:")
        for t in tasks:
            if t["state"] in ("done", "cancelled") and not a.all:
                continue
            print(f"  #{t['id']:<4} {t['state']:<12} {t['assignee'] or '-':<12} {t['title'][:70]}")
    leases = call(c.get, "/leases")
    if leases:
        print("\nLeases:")
        for l in leases:
            print(f"  #{l['id']:<4} {l['holder']:<12} {', '.join(l['globs'])[:60]} "
                  f"(expires in {int((l['expires_at'] - time.time()) / 60)}m)")
    feed = call(c.get, "/status", limit=a.n)
    if feed:
        print("\nStatus feed:")
        for s in feed:
            print(f"  [{ago(s['ts'])} ago · {s['level']} · {s['author']}] {s['summary']}")
            if a.verbose and s["body"]:
                print("    " + s["body"].replace("\n", "\n    "))


def cmd_fleet(action):
    def run(a):
        f = call(admin_client().post, f"/fleet/{action}")
        print(f"Fleet is now {f['state']}")
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
    p = argparse.ArgumentParser(prog="hive", description="Multi-agent harness. See SPEC.md.")
    p.add_argument("--version", action="version", version=__version__)
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
    s.add_argument("--vendor", choices=["claude", "gemini"], required=True)
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

    s = sub.add_parser("join", help="add an agent slot to this machine")
    s.add_argument("name")
    s.add_argument("--token", required=True)
    s.add_argument("--vendor", choices=["claude", "gemini"], required=True)
    s.add_argument("--server")
    s.add_argument("--repo", help="local clone; the slot worktree is created next to it")
    s.add_argument("--worktree", help="explicit worktree path")
    s.add_argument("--config-dir", help="CLI account config dir (default ~/.hive/accounts/NAME)")
    s.add_argument("--permission-mode", help="claude: acceptEdits|bypassPermissions|…; "
                                             "gemini: auto_edit|yolo")
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
    s.add_argument("--vendor", choices=["claude", "gemini"])
    s.set_defaults(fn=cmd_hook)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
