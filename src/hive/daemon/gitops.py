"""Git operations for slot worktrees and the lease release transaction (SPEC §13)."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import globs as G
from .client import HiveError, ServerClient
from .shells import run_shell

CHECK_TIMEOUT = 1800
CONFIRM_RETRY_DELAYS = (2, 4, 8, 16)  # then the daemon keeps retrying in the background
STASH_MESSAGE = "hive-outside-lease"


def native_shell(shell: str | None) -> str:
    """Shell for [integration] checks. "native" (default): cmd.exe on Windows, /bin/sh
    elsewhere, so simple portable commands like `python -m pytest -q` work on every machine."""
    if not shell or shell == "native":
        return "cmd" if os.name == "nt" else "sh"
    return shell


class GitError(Exception):
    pass


def git(cwd: str | Path, *args: str, check: bool = True, strip: bool = True) -> str:
    """Run git; returns stdout. Pass strip=False for machine-readable output whose leading
    whitespace is significant (e.g. `status --porcelain`, where " M" means unstaged)."""
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {(p.stderr or p.stdout).strip()[:2000]}")
    return p.stdout.strip() if strip else p.stdout


def ensure_worktree(repo: str | Path, worktree: str | Path, branch: str, main: str = "main") -> None:
    worktree = Path(worktree)
    if (worktree / ".git").exists():
        return
    git(repo, "fetch", "origin", check=False)
    base = f"origin/{main}" if git(repo, "rev-parse", "--verify", "-q", f"origin/{main}",
                                   check=False) else main
    # `worktree add -B` resets an existing branch: never silently drop unintegrated work.
    if git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False):
        ahead = git(repo, "rev-list", "--count", f"{base}..{branch}", check=False) or "0"
        if int(ahead) > 0:
            raise GitError(f"branch {branch} has {ahead} commit(s) that are not in {base}. "
                           f"Integrate them, or delete the branch (`git branch -D {branch}`) "
                           "if they are no longer needed, then join again.")
    git(repo, "worktree", "add", "-B", branch, str(worktree), base)


def changed_paths(wt: str | Path) -> list[str]:
    """Changed paths, including both sides of a rename (deleting the old path is a change)."""
    # Entries are "XY PATH\0"; X or Y may be a space, so the output must not be stripped.
    out = git(wt, "status", "--porcelain=v1", "-z", "--untracked-files=all", strip=False)
    paths, parts, i = [], out.split("\0"), 0
    while i < len(parts):
        entry = parts[i]
        if len(entry) > 3:
            paths.append(entry[3:])
            if entry[0] in "RC":  # the source path follows as the next field
                if entry[0] == "R" and i + 1 < len(parts) and parts[i + 1]:
                    paths.append(parts[i + 1])
                i += 1
        i += 1
    return paths


def is_clean(wt: str | Path) -> bool:
    return not changed_paths(wt)


def head(wt: str | Path) -> str:
    return git(wt, "rev-parse", "HEAD")


def sync_to_main(wt: str | Path, main: str = "main") -> str:
    """Fetch and rebase a clean worktree onto origin/main. Returns a short status."""
    git(wt, "fetch", "origin", check=False)
    if not is_clean(wt):
        return "dirty"
    try:
        git(wt, "rebase", f"origin/{main}")
        return "rebased"
    except GitError:
        git(wt, "rebase", "--abort", check=False)
        return "conflict"


@dataclass
class ReleaseResult:
    # integrated | integrated_unconfirmed | review_pending | nothing | uncovered_changes |
    # uncovered_commits | conflict | checks_failed | error
    status: str
    message: str
    sha: str = ""
    paths: list[str] = field(default_factory=list)
    # When main was pushed but the server could not be told: the confirmation to resend.
    pending_confirm: dict | None = None

    def as_dict(self) -> dict:
        return {"status": self.status, "message": self.message, "sha": self.sha,
                "paths": self.paths[:200]}


def confirm(client: ServerClient, body: dict, sleep: Callable[[float], None] = time.sleep,
            delays=CONFIRM_RETRY_DELAYS) -> bool:
    """Tell the server main advanced. Retries transient failures; False if still failing.
    Errors that can never succeed (grant used, lease revoked) count as done."""
    for delay in (0, *delays):
        if delay:
            sleep(delay)
        try:
            client.post("/integrate/confirm", body)
            return True
        except HiveError as e:
            if e.status in (400, 403, 404, 409, 410):
                return True  # nothing left to confirm with this grant
    return False


def _stash_ref(wt) -> str:
    return git(wt, "rev-parse", "-q", "--verify", "refs/stash", check=False)


def release_transaction(wt: str | Path, client: ServerClient, slot: str, leases: list[dict],
                        commit_message: str, task_id: int | None, write_globs: list[str],
                        checks: list[str], main: str = "main", retries: int = 3,
                        checks_shell: str = "native",
                        sleep: Callable[[float], None] = time.sleep) -> ReleaseResult:
    allowed = [g for l in leases for g in l["globs"]] + list(write_globs)
    changed = changed_paths(wt)
    to_add = [p for p in changed if G.covered(p, allowed)]
    outside = [p for p in changed if p not in to_add]
    if to_add:
        # Stage what still exists on disk or in the index; a path already staged as deleted
        # (e.g. the source of `git mv`) can't be added again but is committed below.
        index = set(git(wt, "ls-files", "-z", strip=False).split("\0"))
        stage = [p for p in to_add if (Path(wt) / p).exists() or p in index]
        if stage:
            git(wt, "add", "-A", "--", *stage)
        trailers = [f"Hive-Agent: {slot}"]
        if task_id:
            trailers.append(f"Hive-Task: {task_id}")
        trailers += [f"Hive-Fence: {l['id']}:{l['fencing']}" for l in leases]
        # Commit exactly the covered paths (`--only`), whatever else the agent has staged.
        git(wt, "commit", "--only", "-m", commit_message.strip() or "hive: update",
            "-m", "\n".join(trailers), "--", *to_add)
    git(wt, "fetch", "origin", check=False)
    ahead = git(wt, "rev-list", "--count", f"origin/{main}..HEAD", check=False) or "0"
    if int(ahead) == 0 and outside:
        # Work exists but none of it is covered: never silently drop the leases over it.
        return ReleaseResult("uncovered_changes",
                             f"Nothing was integrated: the changed files {outside[:20]} are "
                             "not covered by your leases. Your leases are still held. Lease "
                             "those paths with lease_acquire (or revert them), then call "
                             "lease_release again. Use lease_release(abandon=true) only if you "
                             "really mean to drop the work.")
    if int(ahead) == 0:
        for l in leases:
            try:
                client.post(f"/leases/{l['id']}/release")
            except HiveError:
                pass
        return ReleaseResult("nothing", "No changes to integrate; leases released.")
    stashed = False
    if outside:
        before = _stash_ref(wt)
        git(wt, "stash", "push", "--include-untracked", "-m", STASH_MESSAGE, "--", *outside,
            check=False)
        after = _stash_ref(wt)
        stashed = bool(after) and after != before
    try:
        res = _integrate(wt, client, slot, leases, task_id, allowed, checks, main, retries,
                         checks_shell, sleep)
    finally:
        restored = True
        if stashed:  # only ever pop the entry created above
            try:
                git(wt, "stash", "pop")
            except GitError:
                restored = False
    if outside:
        res.message += (f" Left uncommitted (outside your leases): {outside[:20]}."
                        if restored else
                        f" Could not restore the files outside your leases ({outside[:20]}): "
                        f"they are kept in `git stash` as '{STASH_MESSAGE}'.")
    return res


def _integrate(wt, client, slot, leases, task_id, allowed, checks, main, retries,
               checks_shell, sleep) -> ReleaseResult:
    fences = {str(l["id"]): l["fencing"] for l in leases}
    for attempt in range(retries):
        try:
            git(wt, "rebase", f"origin/{main}")
        except GitError as e:
            git(wt, "rebase", "--abort", check=False)
            try:
                client.post("/integrate/conflict", {"task_id": task_id, "detail": str(e)})
            except HiveError:
                pass
            return ReleaseResult("conflict", "Rebase onto main conflicted; your commit is "
                                 "kept and your leases are still held. Resolve by merging "
                                 f"origin/{main} manually, then call lease_release again. "
                                 f"{e}")
        paths = [p for p in git(wt, "diff", "--name-only", "-z", f"origin/{main}..HEAD",
                                strip=False).split("\0") if p]
        uncovered = [p for p in paths if not G.covered(p, allowed)]
        if uncovered:
            # Earlier commits (e.g. made by an approved command) touch unleased files.
            return ReleaseResult("uncovered_commits",
                                 f"Your branch changes files your leases don't cover: "
                                 f"{uncovered[:20]}. Lease them too (lease_acquire), then call "
                                 "lease_release again. Your leases are still held.")
        for cmd in checks:
            code, output = run_shell(str(wt), cmd, native_shell(checks_shell),
                                     timeout=CHECK_TIMEOUT)
            if code != 0:
                tail = output[-3000:]
                return ReleaseResult("checks_failed", f"Check `{cmd}` failed; leases still "
                                     f"held. Fix and call lease_release again.\n{tail}")
        sha = head(wt)
        git(wt, "push", "--force-with-lease", "origin", f"HEAD:refs/heads/hive/{slot}",
            check=False)
        body = {"sha": sha, "base_sha": git(wt, "rev-parse", f"origin/{main}"),
                "leases": fences, "task_id": task_id, "paths": paths}
        try:
            r = client.post("/integrate", body)
        except HiveError as e:
            if e.code == "not_fast_forward":
                git(wt, "fetch", "origin", check=False)
                continue
            return ReleaseResult("error", f"Integration refused: {e.message}")
        if r["status"] == "review_pending":
            return ReleaseResult("review_pending",
                                 f"Submitted for review by {r.get('reviewer')}. Your leases "
                                 "stay held; when approved the daemon integrates "
                                 "automatically. End your turn now.", sha, paths)
        if r["status"] == "push_main":
            try:
                git(wt, "push", "origin", f"HEAD:refs/heads/{r.get('main', main)}")
            except GitError:
                git(wt, "fetch", "origin", check=False)
                continue
            body = {"grant": r.get("grant"), "sha": sha, "paths": paths}
            if not confirm(client, body, sleep):
                return ReleaseResult(
                    "integrated_unconfirmed",
                    f"Integrated {sha[:10]} into {main}, but the Hive server could not be "
                    "told yet; the daemon keeps retrying in the background. Your leases are "
                    "released once it gets through.", sha, paths, pending_confirm=body)
        return ReleaseResult("integrated", f"Integrated {sha[:10]} into {main}; leases "
                             "released.", sha, paths)
    return ReleaseResult("error", "main kept moving; gave up after retries. Try again.")
