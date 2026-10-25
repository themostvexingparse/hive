"""Bundles generated at each launch (docs/CLI_REDESIGN.md): a changed hive.toml or prompt file
reaches the next session without `hive init` or a commit of .hive/agent/."""

import json
import subprocess

import pytest

from hive.config import MachineConfig, Slot, hive_home
from hive.daemon import core, wake
from hive.daemon.store import Store
from hive.init import init_repo


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("HIVE_HOME", str(tmp_path / "home"))
    root = tmp_path / "wt"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    init_repo(root, "http://srv")
    return root


def test_launch_generates_the_bundle_from_current_settings(repo, tmp_path):
    slot = Slot(name="c1", vendor="claude", token="t", worktree=str(repo))
    assert wake.bundle_dir(slot) == repo / ".hive" / "agent" / "claude"  # before any launch
    assert wake.prepare_vendor(slot, {}, "worker") is None
    gen = tmp_path / "home" / "bundles" / "c1" / "claude"
    assert wake.bundle_dir(slot) == gen
    cmd = wake.build_interactive_command(slot, "sid", False, None)
    assert cmd[cmd.index("--plugin-dir") + 1] == str(gen)
    assert cmd[cmd.index("--settings") + 1] == str(gen / "settings" / "worker.json")
    assert (gen / ".claude-plugin" / "plugin.json").exists()
    # Only this CLI's files, and none of the committed copy is touched.
    assert not (gen.parent / "gemini").exists()
    before = (repo / ".hive" / "agent" / "claude" / "settings" / "worker.json").read_text()

    # A permission change in hive.toml (or hive.local.toml) reaches the next launch.
    (repo / "hive.local.toml").write_text('[permissions]\nallow = ["make docs"]\n')
    wake.prepare_vendor(slot, {}, "worker")
    allowed = json.loads((gen / "settings" / "worker.json").read_text())["permissions"]["allow"]
    assert any("make docs" in a for a in allowed)
    assert (repo / ".hive" / "agent" / "claude" / "settings" / "worker.json").read_text() == before

    # So does a project rule in .hive/prompts/.
    (repo / ".hive" / "prompts" / "worker.md").write_text("Use tabs.")
    wake.prepare_vendor(slot, {}, "worker")
    assert "Use tabs." in (gen / "skills" / "hive-worker" / "SKILL.md").read_text()

    # A file the bundle no longer has is removed.
    (gen / "stale.txt").write_text("x")
    wake.prepare_vendor(slot, {}, "worker")
    assert not (gen / "stale.txt").exists()

    # A file that can't be built keeps the last good bundle and says so.
    (repo / ".hive" / "prompts" / "worker.md").write_text("x" * 300_000)  # far too big
    err = wake.prepare_vendor(slot, {}, "worker")
    assert err and "last ones that worked" in err and wake.bundle_dir(slot) == gen


def test_codex_and_agy_copy_freshly_built_files(repo, tmp_path):
    (repo / "hive.local.toml").write_text('[permissions]\nallow = ["make docs"]\n')
    codex = Slot(name="x1", vendor="codex", token="t", worktree=str(repo),
                 config_dir=str(tmp_path / "codex-home"))
    assert wake.prepare_vendor(codex, {}, "worker") is None
    rules = (tmp_path / "codex-home" / "rules" / "hive.rules").read_text()
    assert 'pattern = ["make", "docs"]' in rules


def test_gemini_relinks_an_extension_linked_to_the_old_bundle(repo, tmp_path, monkeypatch):
    home = tmp_path / "gemini-home"
    slot = Slot(name="g1", vendor="gemini", token="t", worktree=str(repo),
                config_dir=str(home))
    ext = home / ".gemini" / "extensions" / "hive"
    ext.mkdir(parents=True)
    (ext / ".gemini-extension-install.json").write_text(json.dumps(
        {"source": str(repo / ".hive" / "agent" / "gemini"), "type": "link"}))
    linked = []

    def fake_link(cmd, **kw):  # what `gemini extensions link <dir>` leaves behind
        linked.append(cmd[3])
        ext.mkdir(parents=True, exist_ok=True)
        (ext / ".gemini-extension-install.json").write_text(json.dumps(
            {"source": cmd[3], "type": "link"}))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(wake.subprocess, "run", fake_link)
    assert wake.prepare_vendor(slot, {}, "worker") is None
    assert linked == [str(tmp_path / "home" / "bundles" / "g1" / "gemini")]
    assert wake.prepare_vendor(slot, {}, "worker") is None
    assert len(linked) == 1  # linked to the generated bundle already: left alone


def test_agents_may_not_edit_hives_own_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("HIVE_HOME", str(tmp_path / "home"))
    wt = tmp_path / "wt"
    wt.mkdir()
    acct = hive_home() / "accounts" / "s1"
    slot = Slot(name="s1", vendor="claude", token="t", worktree=str(wt), config_dir=str(acct))
    d = core.Daemon(MachineConfig(server_url="http://127.0.0.1:9", slots=[slot]),
                    Store(tmp_path / "state.db"), echo=False)
    rt = d.slots["s1"]
    for target in (hive_home() / "machine.toml", hive_home() / "config.toml",
                   hive_home() / "bundles" / "s1" / "claude" / "settings" / "worker.json",
                   acct / "settings.json",  # its CLI's own settings: what it may do
                   hive_home() / "accounts" / "s2" / "projects" / "x" / "memory" / "m.md"):
        assert "Hive's own folder" in d._check_write(rt, str(target)).get("deny", "")
    assert d._check_write(rt, str(tmp_path / "elsewhere.txt")) == {}
    # Its CLI's own notes in its own account folder: Claude's memory and plans.
    for target in (acct / "projects" / "C--wt" / "memory" / "MEMORY.md",
                   acct / "plans" / "plan.md"):
        assert d._check_write(rt, str(target)) == {}
