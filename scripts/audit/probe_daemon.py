"""Audit probes for daemon / git / parsing suspects."""
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from hive import globs as G
from hive import permissions as P
from hive.config import Slot
from hive.daemon import gitops, wake

results = []


def probe(name):
    def deco(fn):
        try:
            ok, detail = fn()
        except Exception as e:  # noqa
            ok, detail = None, "probe crashed: " + "".join(traceback.format_exception_only(e))
        results.append((name, ok, detail))
        return fn
    return deco


def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout


def repo():
    d = Path(tempfile.mkdtemp())
    origin = d / "origin.git"
    git(d, "init", "--bare", "-b", "main", str(origin))
    wt = d / "wt"
    git(d, "clone", str(origin), str(wt))
    git(wt, "config", "user.email", "t@e.com")
    git(wt, "config", "user.name", "t")
    for n in ("a.txt", "b.txt", "secret.txt"):
        (wt / n).write_text("v1\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-m", "init")
    git(wt, "push", "origin", "main")
    return wt, origin


class FakeClient:
    def __init__(self):
        self.calls = []

    def post(self, path, json=None):
        self.calls.append(path)
        return {"status": "push_main", "main": "main"} if path == "/integrate" else {}


@probe("D1 recursive deletes via find/xargs are not detected as dangerous")
def _():
    approval = P.build_rules({})["worker"]["approval"]
    cmds = ["find . -name '*.o' -delete", "find build -exec rm -rf {} +",
            "ls | xargs rm -rf", "git ls-files -z | xargs -0 rm -f"]
    missed = [c for c in cmds if not P.needs_approval(c, approval)]
    allowed_by_cli = P.matches("find . -delete", P.build_rules({})["worker"]["allow"])
    return (bool(missed), f"undetected: {missed}; CLI allow-list prefix for "
                          f"`find . -delete`: {allowed_by_cli!r}")


@probe("D2 lease_release commits files staged by the agent even if no lease covers them")
def _():
    wt, origin = repo()
    (wt / "a.txt").write_text("leased change\n")
    (wt / "secret.txt").write_text("unleased change\n")
    git(wt, "add", "secret.txt")  # agent staged it (git add is allowed)
    lease = {"id": 1, "fencing": 1, "globs": ["a.txt"]}
    res = gitops.release_transaction(wt, FakeClient(), "s", [lease], "msg", None, [], [])
    on_main = git(origin, "show", "main:secret.txt")
    return (on_main == "unleased change\n",
            f"release -> {res.status}; origin/main secret.txt = {on_main.strip()!r}")


@probe("D3 lease_release pops an unrelated older stash when nothing new was stashed")
def _():
    wt, origin = repo()
    (wt / "b.txt").write_text("agent's own stashed work\n")
    git(wt, "stash", "push", "-m", "agents-own")       # an older, unrelated stash
    (wt / "a.txt").write_text("leased\n")
    ign = wt / ".gitignore"
    # An outside file that `git stash push -- <path>` cannot stash: an ignored-but-reported
    # path is hard to fabricate, so use a path that disappears: changed then reverted race is
    # not reproducible deterministically; instead verify the code path directly:
    before = git(wt, "stash", "list").strip().splitlines()
    (wt / "b.txt").write_text("v1\n")  # unchanged content => not in `outside`
    lease = {"id": 1, "fencing": 1, "globs": ["a.txt"]}
    # Simulate a failed `stash push` (e.g. pathspec error) by monkeypatching git():
    real = gitops.git

    def fake(cwd, *args, **kw):
        if args[:2] == ("stash", "push"):
            return ""  # stash push "succeeds" without creating an entry
        return real(cwd, *args, **kw)

    (wt / "new-untracked.tmp").write_text("x")  # outside lease => triggers stash push/pop
    gitops.git = fake
    try:
        gitops.release_transaction(wt, FakeClient(), "s", [lease], "msg", None, [], [])
    finally:
        gitops.git = real
    after = git(wt, "stash", "list").strip().splitlines()
    b = (wt / "b.txt").read_text()
    return (len(after) < len(before) and "agent's own" in b,
            f"stash entries before={len(before)} after={len(after)}; b.txt now {b.strip()!r}")


@probe("D4 daemon drops wake triggers while it has no identity (server down at start)")
def _():
    from hive.daemon.core import Daemon
    from hive.config import MachineConfig
    from hive.daemon.store import Store
    d = Path(tempfile.mkdtemp())
    slot = Slot(name="prog1", vendor="claude", token="t", worktree=str(d))
    daemon = Daemon(MachineConfig(server_url="http://127.0.0.1:9", slots=[slot]),
                    Store(d / "s.db"), echo=False)
    rt = daemon.slots["prog1"]
    daemon.refresh(rt)  # fails: server unreachable
    daemon.handle_event(rt, {"type": "task.assigned", "actor": "system", "seq": 1,
                             "payload": {"id": 7, "title": "T", "assignee": "prog1"}})
    pend = daemon.store.pending_triggers("prog1")
    return (not pend, f"rt.me={rt.me}; pending triggers after task.assigned: {pend}")


@probe("D5 globs with [..] are treated as wildcards but matched literally")
def _():
    lit = G.is_literal("src/[ab].py")
    m = G.matches("src/a.py", "src/[ab].py")
    return (not lit and not m, f"is_literal={lit}, matches('src/a.py')={m}")


@probe("D6 removing a held custom role makes every request fail while tick has work")
def _():
    from hive.server.app import create_app
    from hive.server.service import ADMIN, create_hive
    d = Path(tempfile.mkdtemp())
    hive = create_hive(f"sqlite:///{d/'h.db'}", tick_interval=0)
    hive.set_config(ADMIN, {"roles": {"writer": {"task_kinds": ["prose"], "can_code": True}}})
    for n, r in (("orch", ["orchestrator"]), ("prog", ["programmer"]), ("wri", ["writer"])):
        hive.enroll(ADMIN, n, "claude", r)
    from hive.server.service import Actor
    for n, r in (("orch", ["orchestrator"]), ("prog", ["programmer"]), ("wri", ["writer"])):
        hive.heartbeat(Actor("agent", n, r))
    hive.start_fleet(ADMIN, "g")
    hive.create_task(Actor("agent", "orch", ["orchestrator"]), "t")
    from hive.server.service import ApiError
    try:
        hive.set_config(ADMIN, {})  # 'writer' removed while 'wri' holds it
    except ApiError as e:
        return (False, f'config change refused up front: {e.status} {e.code}')
    hive._last_sched = 0
    app = create_app(hive, admin_token="x")
    r = app.test_client().get("/api/v1/health")
    return (r.status_code == 500, f"GET /api/v1/health -> {r.status_code}")


@probe("D7 (Windows) wake timeout does not stop a CLI launched through a .cmd shim")
def _():
    if os.name != "nt":
        return (False, "not Windows; skipped")
    d = Path(tempfile.mkdtemp())
    shim = d / "fakecli.cmd"
    shim.write_text(f'@"{sys.executable}" -c "import time; print(1, flush=True); '
                    f'time.sleep(15)"\r\n')
    slot = Slot(name="x", vendor="claude", token="t", worktree=str(d))
    t0 = time.time()
    wake.run_cli(slot, [str(shim)], "", dict(os.environ), d / "log.txt", timeout=2)
    took = time.time() - t0
    return (took > 10, f"timeout=2s, run_cli returned after {took:.1f}s")


@probe("D8 the shared git pre-commit hook blocks the user's own commits when `hive` "
       "is not on PATH")
def _():
    from hive.init import install_git_hook
    wt, origin = repo()
    install_git_hook(wt, "hive-not-on-path")
    (wt / "a.txt").write_text("user edit\n")
    git(wt, "add", "a.txt")
    p = subprocess.run(["git", "commit", "-m", "user commit"], cwd=wt, capture_output=True,
                       text=True)
    return (p.returncode != 0, f"user commit exit={p.returncode}: "
                               f"{(p.stderr or p.stdout).strip()[:120]}")


@probe("D9 pushes_to misses `git -c k=v push origin main` and `git push origin HEAD:main` "
       "variants")
def _():
    cases = {"git -C . push origin main": P.pushes_to("git -C . push origin main", "main"),
             "git -c a=b push origin main": P.pushes_to("git -c a=b push origin main", "main"),
             "git push origin HEAD:main": P.pushes_to("git push origin HEAD:main", "main")}
    missed = [k for k, v in cases.items() if not v]
    return (bool(missed), f"missed: {missed}")


for name, ok, detail in results:
    tag = "REPRODUCED" if ok else ("PROBE ERROR" if ok is None else "not reproduced")
    print(f"[{tag}] {name}\n    {detail}")
