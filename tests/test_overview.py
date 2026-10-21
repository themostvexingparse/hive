"""`hive overview`: what it finds about a project's setup, offline and against a live server."""

import subprocess
import threading

import pytest
from werkzeug.serving import make_server

from hive import overview
from hive.config import MachineConfig, Slot, save_admin_config, AdminConfig, save_machine_config
from hive.init import init_repo
from hive.server.app import create_app
from hive.server.service import create_hive

ADMIN = "adm"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HIVE_HOME", str(tmp_path / "home"))
    for var in ("HIVE_ADMIN_TOKEN", "HIVE_SERVER_URL"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def project(tmp_path):
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    root = tmp_path / "proj"
    git(tmp_path, "clone", "-q", str(origin), str(root))
    git(root, "-c", "user.email=t@e.com", "-c", "user.name=t", "commit", "-q", "--allow-empty",
        "-m", "init")
    git(root, "push", "-q", "origin", "main")
    init_repo(root, "http://127.0.0.1:9")          # a port nothing listens on
    return root


def msgs(d):
    return [p["msg"] for p in d["problems"]]


def test_outside_a_hive_project(tmp_path):
    git(tmp_path, "init", "-q", str(tmp_path / "plain"))
    d = overview.collect(tmp_path / "plain", offline=True)
    assert d["root"] is None and d["git_root"]


def test_fresh_project_offline(project):
    (project / "docs").mkdir()
    d = overview.collect(project / "docs", offline=True)     # run from a subfolder
    assert d["root"] == str(project)
    repo = d["repo"]
    assert repo["branch"] == "main" and repo["origin"] and repo["main_on_origin"]
    assert repo["bundles"] and repo["drift"] == [] and repo["hook"] == "missing"
    assert repo["orchestrator_memory_ignored"]
    assert {r["name"] for r in d["roles"]["list"]} == {"orchestrator", "programmer", "planner",
                                                       "reviewer", "tester"}
    assert {r["status"] for r in d["prompts"]} == {"comments only"}
    assert d["machine"]["daemon"] == "no slots here" and d["server"]["reachable"] is None
    assert d["problems"] == []


def test_finds_stale_bundles_and_explains_why(project):
    (project / ".hive" / "prompts" / "worker.md").write_text("Use tabs.")
    d = overview.collect(project, offline=True)
    assert d["repo"]["drift"] == ["claude/skills/hive-worker/SKILL.md",
                                  "gemini/skills/hive-worker/SKILL.md",
                                  "codex/skills/hive-worker/SKILL.md",
                                  "agy/skills/hive-worker/SKILL.md"]
    assert any("out of date" in m and ".hive/prompts/" in m for m in msgs(d))
    assert [r["status"] for r in d["prompts"] if r["file"] == "worker.md"] == ["active"]
    # Permission changes make the permission files stale too.
    (project / "hive.toml").write_text((project / "hive.toml").read_text()
                                       .replace('allow = []', 'allow = ["make docs"]'))
    drift = overview.collect(project, offline=True)["repo"]["drift"]
    assert "claude/settings/worker.json" in drift and "gemini/policies/worker.toml" in drift
    assert "codex/rules/worker.rules" in drift and "agy/settings/worker.json" in drift


def test_roles_show_their_origin_and_prompt_sources(project):
    toml = project / "hive.toml"
    toml.write_text(toml.read_text() + '\n[roles.reviewer]\nprompt = "Be strict."\n'
                    '[roles.docs-writer]\ncan_code = true\ntask_kinds = ["docs"]\n')
    (project / ".hive" / "prompts" / "roles" / "reviewer.md").write_text("Check tests first.")
    roles = {r["name"]: r for r in overview.collect(project, offline=True)["roles"]["list"]}
    assert roles["reviewer"]["origin"] == "built-in, changed"
    assert roles["reviewer"]["prompt_from"] == ["hive.toml", "roles/reviewer.md"]
    assert roles["docs-writer"]["origin"] == "custom" and roles["docs-writer"]["can_code"]
    assert roles["programmer"]["prompt_from"] == ["built-in"]


def test_reports_config_errors_without_crashing(project):
    toml = project / "hive.toml"
    toml.write_text(toml.read_text().replace('preset = "standard"', 'preset = "loose"')
                    + '\n[roles.bad]\nnot_a_field = 1\n')
    d = overview.collect(project, offline=True)
    assert any("[permissions]" in m for m in msgs(d))
    assert any("[roles]" in m for m in msgs(d))
    overview.render(d)                                   # still prints


def test_machine_slots_of_this_repo(project, tmp_path):
    wt = tmp_path / "proj-p1"
    git(project, "worktree", "add", "-q", "-b", "hive/p1", str(wt))
    other = tmp_path / "other"
    git(tmp_path, "init", "-q", str(other))
    account = tmp_path / "home" / "accounts" / "p1"
    account.mkdir(parents=True)
    save_machine_config(MachineConfig(server_url="http://elsewhere:1", slots=[
        Slot(name="p1", vendor="claude", token="t1", worktree=str(wt), config_dir=str(account)),
        Slot(name="p2", vendor="gemini", token="t2", worktree=str(wt)),       # shares p1's
        Slot(name="x", vendor="claude", token="t3", worktree=str(other))]))
    d = overview.collect(project, offline=True)
    slots = {s["name"]: s for s in d["machine"]["slots"]}
    assert set(slots) == {"p1", "p2"} and d["machine"]["other_slots"] == 1
    assert slots["p1"]["branch"] == "hive/p1" and not slots["p1"]["account_used"]
    m = msgs(d)
    assert any("share the worktree" in x for x in m)
    assert any("p2's worktree is on hive/p1" in x for x in m)
    assert any("p1's account folder is empty" in x for x in m)
    # The slots' server is the one in use; hive.toml's differing value is only stale.
    assert d["server"]["url"] == "http://elsewhere:1"
    assert any("[server] url (http://127.0.0.1:9) is out of date" in x for x in m)


@pytest.fixture
def server(tmp_path):
    hive = create_hive(f"sqlite:///{tmp_path / 'srv.db'}", tick_interval=0)
    srv = make_server("127.0.0.1", 0, create_app(hive, admin_token=ADMIN), threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def point_at(project, url):
    toml = project / "hive.toml"
    text = toml.read_text().replace("http://127.0.0.1:9", url)
    toml.write_text(text.replace('review = "off"', 'review = "orchestrator"'))


def test_live_fleet_with_the_admin_login(project, server):
    import requests
    point_at(project, server)
    h = {"Authorization": f"Bearer {ADMIN}"}
    requests.post(server + "/api/v1/agents", headers=h,
                  json={"name": "prog1", "vendor": "claude", "roles": ["programmer"]})
    save_admin_config(AdminConfig(server, ADMIN))
    d = overview.collect(project)
    srv = d["server"]
    assert srv["reachable"] and srv["auth"] == "admin login"
    assert srv["fleet"]["state"] == "setup" and [a["name"] for a in srv["agents"]] == ["prog1"]
    m = msgs(d)
    assert any("review policy (off) differs from hive.toml (orchestrator)" in x for x in m)
    assert any("Role orchestrator needs at least 1" in x for x in m)
    assert not any("Role programmer" in x for x in m)
    overview.render(d)


def test_live_fleet_with_a_slot_token_and_unenrolled_slot(project, server, tmp_path):
    import requests
    point_at(project, server)
    tok = requests.post(server + "/api/v1/agents", headers={"Authorization": f"Bearer {ADMIN}"},
                        json={"name": "prog1", "vendor": "claude",
                              "roles": ["programmer"]}).json()["token"]
    wt1, wt2 = tmp_path / "proj-prog1", tmp_path / "proj-ghost"
    git(project, "worktree", "add", "-q", "-b", "hive/prog1", str(wt1))
    git(project, "worktree", "add", "-q", "-b", "hive/ghost", str(wt2))
    save_machine_config(MachineConfig(server_url=server, slots=[
        Slot(name="prog1", vendor="claude", token=tok, worktree=str(wt1)),
        Slot(name="ghost", vendor="claude", token="bad", worktree=str(wt2))]))
    d = overview.collect(project)
    assert d["server"]["auth"] == "slot prog1" and d["server"]["fleet"]["state"] == "setup"
    assert d["machine"]["daemon"] == "not running"
    assert any("ghost is set up on this machine but isn't enrolled" in x for x in msgs(d))


def test_stale_hive_toml_server_is_not_used_to_connect(project, server):
    """Regression: hive.toml still had `hive init`'s localhost default while the admin login
    pointed at the real server; the overview tried localhost and reported it unreachable."""
    save_admin_config(AdminConfig(server, ADMIN))           # hive.toml keeps 127.0.0.1:9
    d = overview.collect(project)
    srv = d["server"]
    assert srv["url"] == server and "admin login" in srv["source"]
    assert srv["reachable"] and srv["fleet"]["state"] == "setup"
    m = msgs(d)
    assert not any("can't be reached" in x for x in m)
    assert any("[server] url (http://127.0.0.1:9) is out of date" in x and server in x
               for x in m)
    overview.render(d)


def test_slots_server_wins_and_differing_admin_server_is_reported(project, server, tmp_path):
    import requests
    tok = requests.post(server + "/api/v1/agents", headers={"Authorization": f"Bearer {ADMIN}"},
                        json={"name": "prog1", "vendor": "claude",
                              "roles": ["programmer"]}).json()["token"]
    wt = tmp_path / "proj-prog1"
    git(project, "worktree", "add", "-q", "-b", "hive/prog1", str(wt))
    save_machine_config(MachineConfig(server_url=server, slots=[
        Slot(name="prog1", vendor="claude", token=tok, worktree=str(wt))]))
    save_admin_config(AdminConfig("https://other.example", "x"))
    d = overview.collect(project)
    srv = d["server"]
    assert srv["url"] == server and "slots" in srv["source"]
    assert srv["auth"] == "slot prog1" and srv["fleet"]["state"] == "setup"
    assert any("admin commands use https://other.example" in x for x in msgs(d))


def test_init_records_this_machines_server(tmp_path):
    save_machine_config(MachineConfig(server_url="https://hive.example.com"))
    root = tmp_path / "fresh"
    git(tmp_path, "init", "-q", str(root))
    from hive.cli import main as cli_main
    cli_main(["--color", "never", "init", str(root)])
    assert 'url = "https://hive.example.com"' in (root / "hive.toml").read_text()


def test_unreachable_server_is_a_problem_not_a_crash(project):
    d = overview.collect(project)
    assert d["server"]["reachable"] is False
    assert any("can't be reached" in x for x in msgs(d))
    overview.render(d)
