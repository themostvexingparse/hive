"""Git operations for slot worktrees and the lease release transaction (SPEC §13)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .. import globs as G
from .client import HiveError, ServerClient


class GitError(Exception):
    pass


def git(cwd: str | Path, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {(p.stderr or p.stdout).strip()[:2000]}")
    return p.stdout.strip()


def ensure_worktree(repo: str | Path, worktree: str | Path, branch: str, main: str = "main") -> None:
    worktree = Path(worktree)
    if (worktree / ".git").exists():
        return
    git(repo, "fetch", "origin", check=False)
    base = f"origin/{main}" if git(repo, "rev-parse", "--verify", "-q", f"origin/{main}",
                                   check=False) else main
    git(repo, "worktree", "add", "-B", branch, str(worktree), base)


def changed_paths(wt: str | Path) -> list[str]:
    out = git(wt, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    paths, parts, i = [], out.split("\0"), 0
    while i < len(parts):
        entry = parts[i]
        if len(entry) > 3:
            paths.append(entry[3:])
            if entry[0] in "RC":  # renames carry the source path as the next field
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
    status: str  # integrated | review_pending | nothing | conflict | checks_failed | error
    message: str
    sha: str = ""
    paths: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"status": self.status, "message": self.message, "sha": self.sha,
                "paths": self.paths[:200]}


def release_transaction(wt: str | Path, client: ServerClient, slot: str, leases: list[dict],
                        commit_message: str, task_id: int | None, write_globs: list[str],
                        checks: list[str], main: str = "main", retries: int = 3) -> ReleaseResult:
    allowed = [g for l in leases for g in l["globs"]] + list(write_globs)
    changed = changed_paths(wt)
    to_add = [p for p in changed if G.covered(p, allowed)]
    outside = [p for p in changed if p not in to_add]
    if to_add:
        git(wt, "add", "-A", "--", *to_add)
        trailers = [f"Hive-Agent: {slot}"]
        if task_id:
            trailers.append(f"Hive-Task: {task_id}")
        trailers += [f"Hive-Fence: {l['id']}:{l['fencing']}" for l in leases]
        git(wt, "commit", "-m", commit_message.strip() or "hive: update",
            "-m", "\n".join(trailers))
    fences = {str(l["id"]): l["fencing"] for l in leases}
    git(wt, "fetch", "origin", check=False)
    ahead = git(wt, "rev-list", "--count", f"origin/{main}..HEAD", check=False) or "0"
    if int(ahead) == 0:
        for l in leases:
            try:
                client.post(f"/leases/{l['id']}/release")
            except HiveError:
                pass
        return ReleaseResult("nothing", "No changes to integrate; leases released." +
                             (f" Uncommitted files outside your leases: {outside}" if outside
                              else ""))
    note = f" Left uncommitted (outside leases): {outside[:20]}" if outside else ""
    if outside:
        git(wt, "stash", "push", "--include-untracked", "-m", "hive-outside-lease", "--",
            *outside, check=False)
    try:
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
            for cmd in checks:
                p = subprocess.run(cmd, shell=True, cwd=str(wt), capture_output=True, text=True,
                                   encoding="utf-8", errors="replace")
                if p.returncode != 0:
                    tail = (p.stdout + p.stderr)[-3000:]
                    return ReleaseResult("checks_failed", f"Check `{cmd}` failed; leases still "
                                         f"held. Fix and call lease_release again.\n{tail}")
            sha = head(wt)
            paths = git(wt, "diff", "--name-only", f"origin/{main}..HEAD").splitlines()
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
                                     "automatically. End your turn now." + note, sha, paths)
            if r["status"] == "push_main":
                try:
                    git(wt, "push", "origin", f"HEAD:refs/heads/{r.get('main', main)}")
                except GitError:
                    git(wt, "fetch", "origin", check=False)
                    continue
                client.post("/integrate/confirm", body)
            return ReleaseResult("integrated", f"Integrated {sha[:10]} into {main}; leases "
                                 "released." + note, sha, paths)
        return ReleaseResult("error", "main kept moving; gave up after retries. Try again.")
    finally:
        if outside:
            git(wt, "stash", "pop", check=False)
