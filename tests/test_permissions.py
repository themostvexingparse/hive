import json
import subprocess
import tomllib

import pytest

from hive import permissions as P
from hive.config import Slot
from hive.daemon import wake
from hive.init import init_repo


def test_three_tiers_per_role_and_preset():
    cfg = {"integration": {"checks": ["python -m pytest -q"]},
           "permissions": {"preset": "standard", "allow": ["docker compose ps *"],
                           "approval": ["npm publish"], "deny": ["terraform apply"]}}
    r = P.build_rules(cfg)
    orch, worker = r["orchestrator"], r["worker"]
    for rules in (orch, worker):
        assert "git diff" in rules["allow"] and "pytest" in rules["allow"]
        assert "python -m pytest -q" in rules["allow"]          # integration checks
        assert "docker compose ps" in rules["allow"]            # trailing " *" normalized
        assert {"git push", "git reset --hard", "rm -rf", "npm publish"} <= set(rules["approval"])
        assert {"hive decide", "hive admin", "terraform apply"} <= set(rules["forbidden"])
    # Ordinary git history work and building are for workers, not the orchestrator.
    assert {"git commit", "git rebase", "git merge", "python", "rm"} <= set(worker["allow"])
    assert "git commit" not in orch["allow"] and "python" not in orch["allow"]
    strict = P.build_rules({"permissions": {"preset": "strict"}})["worker"]
    assert "git commit" in strict["allow"] and "python" not in strict["allow"]
    none = P.build_rules({"permissions": {"preset": "none", "allow": ["make lint"]}})
    assert none["worker"]["allow"] == ["make lint"]
    assert "git push" in none["worker"]["approval"]  # dangerous commands are never just allowed


def test_stricter_tier_wins_and_bad_entries_rejected():
    r = P.build_rules({"permissions": {"allow": ["git push", "hive decide"],
                                       "approval": ["hive admin"]}})["worker"]
    assert "git push" not in r["allow"] and "git push" in r["approval"]
    assert "hive decide" not in r["allow"] and "hive admin" not in r["approval"]
    with pytest.raises(ValueError):
        P.build_rules({"permissions": {"allow": ["git * main"]}})
    with pytest.raises(ValueError):
        P.build_rules({"permissions": {"preset": "everything"}})


@pytest.mark.parametrize("command", [
    # POSIX
    "rm -rf build", "rm -fr build", "rm -r -f build", "rm --recursive build", "rm -f x",
    "/bin/rm -r build", "sudo make install", "chmod -R 777 .",
    # cmd.exe
    "del /s /q build", "rd /s /q build", "rmdir /S build", r'cmd /c "del /q C:\tmp\x"',
    "cmd /crd /s build",
    # PowerShell, including flags after the path, abbreviations and aliases
    "Remove-Item build -Recurse", "Remove-Item -Path build -Recurse -Force",
    "remove-item build -rec", "ri build -r", "rm build -Recurse", "del build -Force",
    "Remove-Item build -Recurse:$true", "Get-ChildItem *.log | Remove-Item -Force",
    "Get-ChildItem | ForEach-Object { Remove-Item $_ -Recurse }",
    "Start-Process git -Verb RunAs", 'icacls . /grant "Everyone:F" /t',
    # nested shells
    'powershell -NoProfile -Command "Remove-Item build -Recurse"',
    'pwsh -c "rm build -Recurse"', 'bash -c "git status && rm -rf build"',
    "git status; $(rm -rf build)",
])
def test_dangerous_commands_detected_in_every_shell(command):
    approval = P.build_rules({})["worker"]["approval"]
    assert P.needs_approval(command, approval), command


@pytest.mark.parametrize("command", [
    "rm notes.txt", "del notes.txt", "Remove-Item notes.txt", "ri notes.txt", "rmdir empty",
    'git commit -m "fix; rm -rf mistake"',            # separators inside quotes are literal
    'git commit -m "Remove-Item x -Recurse"',
    "Get-ChildItem -Recurse", "dir /s", "findstr /s TODO *.py", "git rm -r --cached x",
    "icacls notes.txt",
])
def test_ordinary_commands_not_flagged(command):
    approval = P.build_rules({})["worker"]["approval"]
    assert P.needs_approval(command, approval) is None, command


@pytest.mark.parametrize("command", [
    "find . -name '*.o' -delete", "find build -exec rm -rf {} +", "find . -execdir rm {} ;",
    "ls | xargs rm -rf", "git ls-files -z | xargs -0 rm -f", "xargs -n 1 -P 4 rm -r < list",
])
def test_find_and_xargs_deletes_need_approval(command):
    """Audit M4."""
    approval = P.build_rules({})["worker"]["approval"]
    assert P.needs_approval(command, approval), command


@pytest.mark.parametrize("command", ["find . -name '*.py'", "find . -exec grep TODO {} +",
                                     "ls | xargs wc -l", "git ls-files | xargs grep x"])
def test_harmless_find_and_xargs_not_flagged(command):
    approval = P.build_rules({})["worker"]["approval"]
    assert P.needs_approval(command, approval) is None, command


@pytest.mark.parametrize("command", [
    "git -C . push origin main", "git -c user.name=x push origin main",
    "git --git-dir .git --work-tree . push origin main", "git --no-pager push --all",
    "git -C sub -c a=b push -f origin +main",
])
def test_git_global_options_do_not_hide_pushes_to_main(command):
    """Audit M3."""
    assert P.pushes_to(command, "main"), command
    assert P.matches(command, ["git push"]) == "git push"


def test_git_global_options_keep_other_subcommands_matchable():
    assert P.subcommands("git -C repo --no-pager log -1") == ["git log -1"]
    assert not P.pushes_to("git -C repo push origin hive/x", "main")
    assert P.matches("git -c core.x=1 reset --hard HEAD~1", ["git reset --hard"])


def test_globs_case_and_brackets():
    """Audit L4 and L8."""
    from hive import globs as G
    assert G.overlaps("src/App.py", "src/app.py")
    assert G.overlaps("SRC/**", "src/a.py")
    assert not G.overlaps("src/a.py", "src/b.py")
    assert G.is_literal("src/[ab].py")
    assert G.matches("src/[ab].py", "src/[ab].py")
    assert not G.matches("src/a.py", "src/[ab].py")


def test_encoded_powershell_is_unwrapped():
    import base64
    enc = base64.b64encode("Remove-Item build -Recurse".encode("utf-16-le")).decode()
    approval = P.build_rules({})["worker"]["approval"]
    assert P.needs_approval(f"powershell -EncodedCommand {enc}", approval)
    assert P.needs_approval(f"pwsh -e {enc}", approval)


def test_program_names_are_normalised():
    assert P.program(r'"C:\Program Files\Git\cmd\git.exe"') == "git"
    assert P.program("./gradlew.bat") == "gradlew"
    assert P.program("Remove-Item") == "remove-item"
    forbidden = P.build_rules({})["worker"]["forbidden"]
    assert P.matches(r"C:\Users\me\.local\bin\hive.exe decide 1 approve", forbidden)
    assert P.matches("& hive decide 1 approve", forbidden)
    assert P.pushes_to(r'& "C:\Program Files\Git\cmd\git.exe" push origin main', "main")


def test_command_matching():
    assert P.subcommands("cd src && FOO=1 timeout 30 rm -rf build | tee x; git status") == [
        "cd src", "rm -rf build", "tee x", "git status"]
    approval = P.build_rules({})["worker"]["approval"]
    assert P.matches("git status && git push origin hive/x", approval) == "git push"
    assert P.matches("rm -r build", approval) == "rm -r"
    assert P.matches("rm notes.txt", approval) is None           # plain rm is allowed
    assert P.matches("git pushx", approval) is None               # whole words only
    assert P.matches("Remove-Item -Recurse dist", approval) == "Remove-Item -Recurse"
    assert P.pushes_to("git push origin main", "main")
    assert P.pushes_to("git push origin HEAD:refs/heads/main", "main")
    assert P.pushes_to("git push -f origin +main", "main")
    assert P.pushes_to("git push --all origin", "main")
    assert not P.pushes_to("git push origin hive/claude-2", "main")
    assert not P.pushes_to("git push origin maintenance", "main")


def test_vendor_translation():
    rules = {"allow": ["git diff"], "approval": ["git push"], "forbidden": ["hive decide"]}
    worker = P.claude_settings(rules, "worker")["permissions"]
    assert "Bash(git diff *)" in worker["allow"] and "PowerShell(git diff *)" in worker["allow"]
    assert "mcp__plugin_hive_hive" in worker["allow"]
    assert {"Bash(git push *)", "Bash(hive decide *)"} <= set(worker["deny"])
    assert "ask" not in worker  # headless: approval goes through Hive, not a prompt
    orch = P.claude_settings(rules, "orchestrator")["permissions"]
    assert orch["ask"] == ["Bash(git push *)", "PowerShell(git push *)"]
    assert orch["deny"] == ["Bash(hive decide *)", "PowerShell(hive decide *)"]

    g = P.gemini_policy(rules, "worker")["rule"]
    assert all("toolName" in x for x in g)  # required by Gemini CLI's validator
    by_decision = {x["decision"]: x for x in g if x.get("commandPrefix")}
    assert by_decision["deny"]["priority"] > by_decision["allow"]["priority"]
    approval = [x for x in g if x.get("commandPrefix") == ["git push"]][0]
    assert approval["decision"] == "deny" and "request_approval" in approval["denyMessage"]
    orch_rule = [x for x in P.gemini_policy(rules, "orchestrator")["rule"]
                 if x.get("commandPrefix") == ["git push"]][0]
    assert orch_rule["decision"] == "ask_user"


def test_init_writes_permission_files_and_launch_uses_them(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    init_repo(tmp_path, "http://srv")
    claude_dir = tmp_path / ".hive" / "agent" / "claude" / "settings"
    gemini_dir = tmp_path / ".hive" / "agent" / "gemini" / "policies"
    for cls in P.ROLE_CLASSES:
        settings = json.loads((claude_dir / f"{cls}.json").read_text())
        assert "Bash(hive decide *)" in settings["permissions"]["deny"]
        policy = tomllib.loads((gemini_dir / f"{cls}.toml").read_text())
        assert any(r.get("mcpName") == "hive" for r in policy["rule"])

    c = Slot(name="c1", vendor="claude", token="t", worktree=str(tmp_path))
    cmd = wake.build_command(c, "sid", False, "orchestrator")
    assert cmd[cmd.index("--settings") + 1] == str(claude_dir / "orchestrator.json")
    cmd = wake.build_interactive_command(c, "sid", False, None, "worker")
    assert cmd[cmd.index("--settings") + 1] == str(claude_dir / "worker.json")
    g = Slot(name="g1", vendor="gemini", token="t", worktree=str(tmp_path))
    cmd = wake.build_command(g, "sid", False, "worker")
    assert cmd[cmd.index("--policy") + 1] == str(gemini_dir / "worker.toml")

    # Repos initialised by an older Hive (no permission files) still launch.
    old = Slot(name="o1", vendor="claude", token="t", worktree=str(tmp_path / "nowhere"))
    cmd = wake.build_command(old, "sid", False)
    assert cmd[cmd.index("--settings") + 1] == wake.CLAUDE_SESSION_SETTINGS
    assert "--policy" not in wake.build_command(
        Slot(name="o2", vendor="gemini", token="t", worktree=str(tmp_path / "nowhere")),
        "sid", False)


def test_web_access_browsers_and_read_only_gh_are_granted_by_default():
    # Workers were refused WebFetch, a headless Edge and `gh repo list` (all of them asked
    # about, which a headless turn can't answer).
    rules = P.build_rules({})
    for cls in ("orchestrator", "worker"):
        assert {"WebFetch", "WebSearch"} <= set(P.claude_settings(rules[cls], cls)["permissions"]["allow"])
        assert {r["toolName"] for r in P.gemini_policy(rules[cls], cls)["rule"]
                if r["decision"] == "allow"} >= {"web_fetch", "google_web_search"}
        assert "read_url(*)" in P.agy_settings(rules[cls], cls)["permissions"]["allow"]
    allow = rules["worker"]["allow"]
    assert P.allowed(r'"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" '
                     "--headless --screenshot=out.png site/index.html", allow)
    assert P.allowed("gh repo list 0shen6 --limit 50 --json name,url", allow)
    assert not P.allowed("gh repo delete x", allow) and not P.allowed("gh api -X DELETE x", allow)
    assert P.allowed("git status", rules["orchestrator"]["allow"])
    assert not P.allowed("chrome", rules["orchestrator"]["allow"])  # builds and runs: coders only


def test_redirections_with_ampersand_are_not_command_separators():
    assert P.subcommands("ls .hive/plans 2>&1; git log -3") == ["ls .hive/plans 2>&1", "git log -3"]
    assert P.subcommands("npm test >out.txt 2>&1") == ["npm test >out.txt 2>&1"]
    assert P.subcommands("make &> build.log") == ["make &> build.log"]
    assert P.subcommands("a && b & c") == ["a", "b", "c"]  # real separators still split
    assert P.needs_approval("git status 2>&1 && git push --force", P.APPROVAL)
