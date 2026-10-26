"""git status parsing and the lease release transaction against a real git repository."""

import subprocess

import pytest

from hive.daemon import gitops


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    wt = tmp_path / "wt"
    git(tmp_path, "clone", str(origin), str(wt))
    git(wt, "config", "user.email", "t@example.com")
    git(wt, "config", "user.name", "t")
    for name in ("index.html", "app.js", "a b.txt", "old.txt"):
        (wt / name).write_text("v1\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-m", "init")
    git(wt, "push", "origin", "main")
    return wt, origin


def test_changed_paths_keeps_first_unstaged_entry_intact(repo):
    wt, _ = repo
    # Unstaged modification: porcelain status " M index.html" begins with a space.
    (wt / "index.html").write_text("v2\n")
    assert gitops.changed_paths(wt) == ["index.html"]


def test_changed_paths_all_status_kinds(repo):
    wt, _ = repo
    (wt / "index.html").write_text("v2\n")           # " M" unstaged
    (wt / "app.js").write_text("v2\n")
    git(wt, "add", "app.js")                           # "M " staged
    (wt / "a b.txt").write_text("v2\n")                # path with a space
    git(wt, "mv", "old.txt", "new.txt")                # "R " rename (two -z fields)
    (wt / "fresh.py").write_text("x\n")                # "??" untracked
    assert sorted(gitops.changed_paths(wt)) == ["a b.txt", "app.js", "fresh.py",
                                                "index.html", "new.txt", "old.txt"]


class FakeClient:
    """Stands in for the server: local push mode, records calls."""

    def __init__(self):
        self.calls = []
        self.bodies = {}

    def post(self, path, json=None):
        self.calls.append(path)
        self.bodies[path] = json
        if path == "/integrate":
            return {"status": "push_main", "main": "main", "grant": "g-1"}
        return {"status": "integrated"}


def test_release_integrates_unstaged_modification(repo):
    wt, origin = repo
    (wt / "index.html").write_text("v2\n")
    client = FakeClient()
    lease = {"id": 1, "fencing": 7, "globs": ["index.html"]}
    res = gitops.release_transaction(wt, client, "claude-2", [lease], "Update index", 3, [],
                                     [])
    assert res.status == "integrated", res.message
    assert res.paths == ["index.html"]
    assert git(origin, "show", "main:index.html") == "v2\n"
    assert "/integrate/confirm" in client.calls
    assert client.bodies["/integrate/confirm"]["grant"] == "g-1"  # confirms with its grant
    assert "/leases/1/release" not in client.calls  # leases are freed by the confirm step


def test_release_reports_paths_with_spaces_and_unicode(repo):
    wt, origin = repo
    (wt / "a b.txt").write_text("v2\n")
    (wt / "ünï.txt").write_text("x\n")
    lease = {"id": 1, "fencing": 7, "globs": ["a b.txt", "ünï.txt"]}
    res = gitops.release_transaction(wt, FakeClient(), "claude-2", [lease], "msg", None, [], [])
    assert res.status == "integrated", res.message
    assert sorted(res.paths) == ["a b.txt", "ünï.txt"]


def test_pre_commit_hook_reads_staged_paths_with_spaces(repo, monkeypatch):
    from hive import hooks

    wt, _ = repo
    (wt / "a b.txt").write_text("v2\n")
    git(wt, "add", "a b.txt")
    seen = {}

    class FakeDaemon:
        slot = "claude-2"

        def post(self, path, body, timeout=5):
            seen["paths"] = body["paths"]
            return {"uncovered": []}

    monkeypatch.setenv("HIVE_SLOT", "claude-2")
    monkeypatch.chdir(wt)
    monkeypatch.setattr(hooks, "LocalDaemon", lambda *a, **k: FakeDaemon())
    assert hooks.git_pre_commit() == 0
    assert seen["paths"] == ["a b.txt"]


def test_release_with_uncovered_changes_keeps_leases(repo):
    wt, _ = repo
    (wt / "app.js").write_text("v2\n")  # changed, but not covered by the lease
    client = FakeClient()
    lease = {"id": 1, "fencing": 7, "globs": ["index.html"]}
    res = gitops.release_transaction(wt, client, "claude-2", [lease], "msg", 3, [], [])
    assert res.status == "uncovered_changes"
    assert "app.js" in res.message
    assert client.calls == []  # nothing integrated, leases not released


def test_release_never_commits_staged_files_outside_leases(repo):
    """Audit H3: `git commit` used to take everything in the index."""
    wt, origin = repo
    (wt / "index.html").write_text("leased\n")
    (wt / "app.js").write_text("not leased\n")
    git(wt, "add", "app.js")                      # the agent staged it itself
    lease = {"id": 1, "fencing": 1, "globs": ["index.html"]}
    res = gitops.release_transaction(wt, FakeClient(), "s", [lease], "msg", None, [], [])
    assert res.status == "integrated", res.message
    assert git(origin, "show", "main:index.html") == "leased\n"
    assert git(origin, "show", "main:app.js") == "v1\n"          # not integrated
    assert (wt / "app.js").read_text() == "not leased\n"         # but kept locally
    assert "app.js" in res.message


def test_release_refuses_earlier_commits_touching_unleased_files(repo):
    """Audit H3: commits made outside the release (e.g. by an approved command)."""
    wt, origin = repo
    (wt / "app.js").write_text("sneaky\n")
    git(wt, "commit", "-am", "manual commit")
    (wt / "index.html").write_text("leased\n")
    client = FakeClient()
    lease = {"id": 1, "fencing": 1, "globs": ["index.html"]}
    res = gitops.release_transaction(wt, client, "s", [lease], "msg", None, [], [])
    assert res.status == "uncovered_commits" and "app.js" in res.message
    assert "/integrate" not in client.calls
    assert git(origin, "show", "main:app.js") == "v1\n"


def test_release_integrates_renames_with_both_paths_leased(repo):
    wt, origin = repo
    git(wt, "mv", "old.txt", "renamed.txt")
    lease = {"id": 1, "fencing": 1, "globs": ["old.txt", "renamed.txt"]}
    res = gitops.release_transaction(wt, FakeClient(), "s", [lease], "mv", None, [], [])
    assert res.status == "integrated", res.message
    files = git(origin, "ls-tree", "--name-only", "main").split("\n")
    assert "renamed.txt" in files and "old.txt" not in files


def test_release_pops_only_its_own_stash(repo):
    """Audit L1: an unrelated, older stash must survive."""
    wt, origin = repo
    (wt / "app.js").write_text("agent's own stashed work\n")
    git(wt, "stash", "push", "-m", "agents-own")
    (wt / "index.html").write_text("leased\n")
    (wt / "scratch.tmp").write_text("x\n")       # outside the lease -> stash push/pop
    lease = {"id": 1, "fencing": 1, "globs": ["index.html"]}
    res = gitops.release_transaction(wt, FakeClient(), "s", [lease], "msg", None, [], [])
    assert res.status == "integrated", res.message
    assert "agents-own" in git(wt, "stash", "list")
    assert (wt / "scratch.tmp").read_text() == "x\n"
    assert (wt / "app.js").read_text() == "v1\n"   # the old stash was not applied


def test_release_does_not_pop_when_nothing_was_stashed(repo, monkeypatch):
    """Audit L1: a failed `stash push` must not lead to popping someone else's stash."""
    wt, origin = repo
    (wt / "app.js").write_text("agent's own stashed work\n")
    git(wt, "stash", "push", "-m", "agents-own")
    (wt / "index.html").write_text("leased\n")
    (wt / "scratch.tmp").write_text("x\n")
    real = gitops.git

    def failing_stash_push(cwd, *args, **kw):
        if args[:2] == ("stash", "push"):
            return ""
        return real(cwd, *args, **kw)

    monkeypatch.setattr(gitops, "git", failing_stash_push)
    lease = {"id": 1, "fencing": 1, "globs": ["index.html"]}
    gitops.release_transaction(wt, FakeClient(), "s", [lease], "msg", None, [], [])
    monkeypatch.setattr(gitops, "git", real)
    assert "agents-own" in git(wt, "stash", "list")
    assert (wt / "app.js").read_text() == "v1\n"


class FlakyConfirmClient(FakeClient):
    def __init__(self, failures):
        super().__init__()
        self.failures = failures

    def post(self, path, json=None):
        if path == "/integrate/confirm" and self.failures:
            self.failures -= 1
            self.calls.append(path)
            from hive.daemon.client import HiveError
            raise HiveError(0, "unreachable", "server down")
        return super().post(path, json)


def test_confirm_is_retried_then_queued(repo):
    """Audit M11: main was pushed; the server must still be told."""
    wt, origin = repo
    (wt / "index.html").write_text("v2\n")
    lease = {"id": 1, "fencing": 1, "globs": ["index.html"]}
    client = FlakyConfirmClient(failures=2)
    res = gitops.release_transaction(wt, client, "s", [lease], "m", None, [], [],
                                     sleep=lambda s: None)
    assert res.status == "integrated" and client.calls.count("/integrate/confirm") == 3

    (wt / "index.html").write_text("v3\n")
    client = FlakyConfirmClient(failures=99)
    res = gitops.release_transaction(wt, client, "s", [lease], "m", None, [], [],
                                     sleep=lambda s: None)
    assert res.status == "integrated_unconfirmed"
    assert res.pending_confirm == {"grant": "g-1", "sha": res.sha, "paths": ["index.html"]}
    assert git(origin, "show", "main:index.html") == "v3\n"
    client.failures = 0
    assert gitops.confirm(client, res.pending_confirm, delays=())   # resent later


def test_rejoin_refuses_to_reset_a_branch_with_unintegrated_commits(repo):
    """Audit L16."""
    wt, origin = repo
    git(wt, "branch", "hive/x", "origin/main")
    git(wt, "worktree", "add", str(wt.parent / "wt-x"), "hive/x")
    (wt.parent / "wt-x" / "work.txt").write_text("unintegrated\n")
    git(wt.parent / "wt-x", "add", "work.txt")
    git(wt.parent / "wt-x", "-c", "user.email=t@e.com", "-c", "user.name=t",
        "commit", "-m", "work")
    git(wt, "worktree", "remove", "--force", str(wt.parent / "wt-x"))
    with pytest.raises(gitops.GitError, match="not in origin/main"):
        gitops.ensure_worktree(wt, wt.parent / "wt-x2", "hive/x")
    gitops.ensure_worktree(wt, wt.parent / "wt-y", "hive/y")      # a fresh branch is fine
    assert (wt.parent / "wt-y" / ".git").exists()


def test_release_integrates_a_deleted_leased_file(repo):
    wt, origin = repo
    (wt / "app.js").unlink()
    lease = {"id": 1, "fencing": 1, "globs": ["app.js"]}
    res = gitops.release_transaction(wt, FakeClient(), "s", [lease], "rm", None, [], [])
    assert res.status == "integrated", res.message
    assert "app.js" not in git(origin, "ls-tree", "--name-only", "main").split("\n")


def test_daemon_resends_queued_confirmations(tmp_path):
    """Audit M11: the heartbeat loop delivers confirmations that failed at release time."""
    from hive.config import MachineConfig, Slot
    from hive.daemon.core import Daemon
    from hive.daemon.store import Store

    daemon = Daemon(MachineConfig(slots=[Slot(name="s", vendor="claude", token="t",
                                              worktree=str(tmp_path))]),
                    Store(tmp_path / "s.db"), echo=False)
    rt = daemon.slots["s"]
    client = FlakyConfirmClient(failures=1)
    rt.client = client
    daemon.refresh = lambda rt: None
    body = {"grant": "g-1", "sha": "a" * 40, "paths": []}
    daemon.store.put("s", "pending_confirms", [body])
    daemon.resend_confirms(rt)                       # server still down: kept
    assert daemon.store.get("s", "pending_confirms") == [body]
    daemon.resend_confirms(rt)                       # delivered: removed
    assert daemon.store.get("s", "pending_confirms") == []
    assert client.bodies["/integrate/confirm"] == body


def test_patch_id_survives_a_clean_rebase_but_not_a_changed_patch(repo, tmp_path):
    wt, origin = repo
    git(wt, "checkout", "-b", "hive/dev")
    (wt / "app.js").write_text("v2\n")
    (wt / "logo.bin").write_bytes(bytes(range(256)))
    git(wt, "add", "-A")
    git(wt, "commit", "-m", "feature")
    git(wt, "fetch", "origin")
    reviewed = gitops.patch_id(wt, "origin/main")
    assert len(reviewed) == 40
    # Someone else moves main (another file); the daemon rebases onto it.
    other = tmp_path / "other"
    git(tmp_path, "clone", str(origin), str(other))
    git(other, "config", "user.email", "o@example.com")
    git(other, "config", "user.name", "o")
    (other / "index.html").write_text("v9\n")
    git(other, "commit", "-am", "other work")
    git(other, "push", "origin", "main")
    git(wt, "fetch", "origin")
    sha_before = git(wt, "rev-parse", "HEAD")
    git(wt, "rebase", "origin/main")
    assert git(wt, "rev-parse", "HEAD") != sha_before
    assert gitops.patch_id(wt, "origin/main") == reviewed
    # Any change to the content, binary files included, is a different patch.
    (wt / "logo.bin").write_bytes(bytes(range(255, -1, -1)))
    git(wt, "commit", "-am", "swap the logo")
    assert gitops.patch_id(wt, "origin/main") not in ("", reviewed)
    # No change at all: no patch id.
    git(wt, "checkout", "-q", "origin/main")
    assert gitops.patch_id(wt, "origin/main") == ""
