"""The simpler interface (docs/CLI_REDESIGN.md): settings layers, `hive agents`, decision
shortcuts, automatic config upload, help tiers and `hive` on its own."""

import json
import subprocess
import time

import pytest

from hive import cli, settings
from hive.config import config_layers, load_machine_config, load_repo_config, main_clone
from hive.daemon.client import ServerClient
from hive.server.app import create_app
from hive.server.db import approvals, events, integrations, leases, tasks
from hive.server.service import Actor, ApiError, create_hive
from test_e2e import ADMIN, serve


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HIVE_HOME", str(tmp_path / "home"))
    for var in ("HIVE_PROJECT", "HIVE_SERVER_URL", "HIVE_ADMIN_TOKEN"):
        monkeypatch.delenv(var, raising=False)


def make_clone(tmp_path):
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    clone = tmp_path / "app"
    git(tmp_path, "clone", str(origin), str(clone))
    git(clone, "config", "user.email", "t@example.com")
    git(clone, "config", "user.name", "t")
    (clone / "README.md").write_text("app\n")
    git(clone, "add", "-A")
    git(clone, "commit", "-m", "init")
    git(clone, "push", "origin", "main")
    return clone


# ---------------------------------------------------------------------- settings

TOML = """\
# Hive configuration
[server]
url = "http://127.0.0.1:8765"

[integration]
review = "off"            # off | orchestrator | reviewer
checks = [
    "echo # not a comment",
    "pytest",
]
shell = "native"

[sessions]
max_wakes = 40
"""


def test_settings_edit_in_place_and_keep_comments(tmp_path):
    path = tmp_path / "hive.toml"
    path.write_text(TOML)
    settings.write(path, "integration.review", "reviewer")
    text = path.read_text()
    assert 'review = "reviewer"            # off | orchestrator | reviewer' in text
    assert text.startswith("# Hive configuration\n")
    # A multi-line array is replaced whole; a "#" inside a string isn't a comment.
    settings.write(path, "integration.checks", ["npm test"])
    assert 'checks = [\n    "npm test",\n]' in path.read_text() or \
        'checks = ["npm test"]' in path.read_text()
    assert "pytest" not in path.read_text()
    assert 'shell = "native"' in path.read_text()
    # New keys go into their section; new sections at the end.
    settings.write(path, "integration.shell", "bash")
    settings.write(path, "orchestrator.debounce_seconds", 30)
    settings.write(path, "roles.docs-writer.can_code", True)
    data = load_repo_config(tmp_path)
    assert data["integration"] == {"review": "reviewer", "checks": ["npm test"],
                                   "shell": "bash"}
    assert data["orchestrator"]["debounce_seconds"] == 30
    assert data["roles"]["docs-writer"] == {"can_code": True}
    assert settings.unset(path, "integration.checks")
    assert "checks" not in load_repo_config(tmp_path)["integration"] or \
        load_repo_config(tmp_path)["integration"]["checks"] == []
    assert not settings.unset(path, "integration.checks")


def test_settings_names_and_values_are_checked():
    with pytest.raises(settings.SettingError, match="no setting"):
        settings.split_key("integration.reveiw")
    with pytest.raises(settings.SettingError, match="no settings section"):
        settings.split_key("intgration.review")
    with pytest.raises(settings.SettingError):
        settings.split_key("review")
    assert settings.parse_value("true", "sessions.per_task") is True
    assert settings.parse_value("30", "orchestrator.debounce_seconds") == 30
    assert settings.parse_value("npm test", "integration.checks") == ["npm test"]
    assert settings.parse_value('["a", "b"]', "integration.checks") == ["a", "b"]
    assert settings.parse_value("reviewer", "integration.review") == "reviewer"
    with pytest.raises(settings.SettingError, match="whole number"):
        settings.parse_value("soon", "sessions.max_wakes")
    with pytest.raises(settings.SettingError, match="true or false"):
        settings.parse_value("1", "sessions.per_task")


def test_local_layer_applies_in_worktrees_and_explains_itself(tmp_path):
    clone = make_clone(tmp_path)
    (clone / "hive.toml").write_text(TOML)
    git(clone, "add", "-A")
    git(clone, "commit", "-m", "hive")
    wt = tmp_path / "app-dev"
    git(clone, "worktree", "add", "-b", "hive/dev", str(wt))
    assert main_clone(wt) == clone.resolve() and main_clone(clone) == clone
    settings.write(clone / "hive.local.toml", "integration.shell", "powershell")
    for root in (clone, wt):  # an agent's worktree sees its clone's local settings
        assert load_repo_config(root)["integration"]["shell"] == "powershell"
        assert [name for name, _, _ in config_layers(root)] == [
            "default", "preset", "user", "hive.toml", "hive.local.toml"]
    e = settings.explain(clone, "integration.shell")
    assert e["value"] == "powershell" and e["source"] == "hive.local.toml"
    assert [v for _, _, v in e["layers"]] == ["native", None, None, "native", "powershell"]
    assert settings.explain(clone, "sessions.max_wakes")["source"] == "hive.toml"
    assert settings.explain(clone, "repo.main")["source"] == "default"


def test_config_commands(tmp_path, monkeypatch, capsys):
    clone = make_clone(tmp_path)
    (clone / "hive.toml").write_text(TOML)
    monkeypatch.chdir(clone)
    cli.main(["config", "set", "integration.checks", "npm", "test"])
    assert load_repo_config(clone)["integration"]["checks"] == ["npm test"]
    cli.main(["config", "set", "sessions.max_wakes", "10", "--local"])
    assert "hive.local.toml" in (clone / ".gitignore").read_text()
    capsys.readouterr()
    cli.main(["config", "get", "sessions.max_wakes"])
    assert capsys.readouterr().out.strip() == "10"
    cli.main(["config", "explain", "sessions.max_wakes"])
    out = capsys.readouterr().out
    assert "from hive.local.toml" in out and "this computer only" in out
    cli.main(["config", "list"])
    out = capsys.readouterr().out
    assert "integration.checks" in out and "repo.main" not in out
    cli.main(["config", "unset", "sessions.max_wakes", "--local"])
    assert "now 40" in capsys.readouterr().out
    # A value the configuration can't take is refused, and the file left as it was.
    before = (clone / "hive.toml").read_text()
    with pytest.raises(SystemExit):
        cli.main(["config", "set", "permissions.preset", "loose"])
    assert (clone / "hive.toml").read_text() == before
    with pytest.raises(SystemExit):
        cli.main(["config", "set", "integration.nope", "1"])


# ---------------------------------------------------------------------- server

def test_server_removes_an_agent_and_frees_its_work(tmp_path):
    hv = create_hive(f"sqlite:///{tmp_path / 'h.db'}")
    admin = Actor("admin", "user", [])
    hv.enroll(admin, "orch", "claude", ["orchestrator"])
    hv.enroll(admin, "dev", "claude", ["programmer"])
    hv.enroll(admin, "dev2", "claude", ["programmer"])
    t = time.time()

    def task(**kw):
        return c.execute(tasks.insert().values(
            title="x", deps=[], created_by="orch", path_hints=[], created=t, updated=t,
            **kw)).inserted_primary_key[0]
    with hv.engine.begin() as c:
        tid = task(state="in_progress", assignee="dev")
        # Its work waiting for review, and someone else's work it was picked to review.
        rev = task(state="review", assignee="dev", reviewer="orch", pending_sha="a" * 40)
        other = task(state="review", assignee="dev2", reviewer="dev", pending_sha="b" * 40)
        lid = c.execute(leases.insert().values(
            holder="dev", globs=["a/**"], fencing=1, acquired=t, expires_at=t + 600,
            state="active")).inserted_primary_key[0]
        aid = c.execute(approvals.insert().values(
            agent="dev", command="git push", shell="bash", reason="r", state="requested",
            created=t)).inserted_primary_key[0]
        c.execute(integrations.insert().values(token="g", agent="dev", sha="a" * 40,
                                               leases={}, state="granted", created=t))
    with pytest.raises(ApiError) as e:
        hv.remove_agent(Actor("agent", "orch", ["orchestrator"]), "dev")
    assert e.value.status == 403
    r = hv.remove_agent(admin, "dev")
    assert r == {"name": "dev", "requeued": [tid, rev], "revoked": [lid],
                 "reviews": {other: "orch"}, "approvals": [aid]}
    assert [a["name"] for a in hv.roster()] == ["dev2", "orch"]
    with hv.engine.begin() as c:
        states = {x.id: (x.state, x.assignee, x.reviewer) for x in c.execute(tasks.select())}
        assert states == {tid: ("open", None, None), rev: ("open", None, None),
                          other: ("review", "dev2", "orch")}
        assert c.execute(leases.select()).first().state == "revoked"
        assert c.execute(approvals.select()).first().state == "denied"
        assert c.execute(integrations.select()).first().state == "cancelled"
        removed = [e for e in c.execute(events.select()) if e.type == "agent.removed"]
    # The orchestrator is woken with what happened.
    from hive.daemon.core import summarize_event
    from hive.roles import IMMEDIATE_WAKE_EVENTS
    assert "agent.removed" in IMMEDIATE_WAKE_EVENTS
    text = summarize_event({"type": "agent.removed", "actor": "user",
                            "payload": removed[0].payload})
    assert "removed dev" in text and f"#{rev}" in text and "orch" in text
    with pytest.raises(ApiError) as e:
        hv.remove_agent(admin, "dev")
    assert e.value.code == "no_such_agent"
    # The orchestrator of a run in progress can't be removed from under it.
    hv.enroll(admin, "dev", "claude", ["programmer"])
    hv.start_fleet(admin, "goal")
    with pytest.raises(ApiError) as e:
        hv.remove_agent(admin, "orch")
    assert e.value.code == "is_orchestrator"


# ---------------------------------------------------------------------- the CLI end to end

@pytest.fixture
def fleet(tmp_path, monkeypatch, capsys):
    clone = make_clone(tmp_path)
    monkeypatch.chdir(clone)
    cli.main(["init"])
    hv = create_hive(f"sqlite:///{tmp_path / 'hive.db'}")
    srv = serve(create_app(hv, admin_token=ADMIN))
    url = f"http://127.0.0.1:{srv.server_port}"
    settings.write(clone / "hive.toml", "server.url", url)  # the server this hive.toml names
    git(clone, "add", "-A")
    git(clone, "commit", "-m", "Set up Hive")
    git(clone, "push", "origin", "main")
    capsys.readouterr()
    try:
        yield clone, hv, url
    finally:
        srv.shutdown()


def roles_of(hv):
    return {a["name"]: a["roles"] for a in hv.roster()}


def test_agents_add_list_rm_rotate(fleet, tmp_path, capsys):
    clone, hv, url = fleet
    cli.main(["login", "--server", url, "--token", ADMIN])
    cli.main(["agents", "add", "claude"])
    cli.main(["agents", "add", "2", "claude", "programmers"])
    assert roles_of(hv) == {"claude-1": ["orchestrator"], "claude-2": ["programmer"],
                            "claude-3": ["programmer"]}
    m = load_machine_config()
    assert [s.name for s in m.slots] == ["claude-1", "claude-2", "claude-3"]
    for s in m.slots:
        assert s.worktree == str(tmp_path / f"app-{s.name}") and (tmp_path / f"app-{s.name}"
                                                                  ).is_dir()
        assert git(tmp_path / f"app-{s.name}", "branch", "--show-current") == f"hive/{s.name}"
        assert m.url_for(s) == url
    out = capsys.readouterr().out
    assert "hv_" not in out  # tokens of agents set up here are never shown
    assert "hive agents login claude-1" in out  # no terminal: how to log in later

    # hive.toml was uploaded by the first command that talked to the server.
    assert json.loads((tmp_path / "home" / "config-sync.json").read_text())[url]
    cli.main(["config", "set", "integration.checks", "python -m pytest -q"])
    with hv._read() as c:  # `config set` uploaded the change
        assert hv._fleet(c, lock=False)["config"]["integration"]["checks"] == [
            "python -m pytest -q"]
    capsys.readouterr()

    cli.main(["agents"])
    out = capsys.readouterr().out
    assert "claude-3" in out and "this computer" in out

    cli.main(["agents", "edit", "claude-3", "--roles", "programmer,tester", "--model", "m1"])
    assert roles_of(hv)["claude-3"] == ["programmer", "tester"]
    assert load_machine_config().slot("claude-3").model == "m1"

    with pytest.raises(SystemExit):
        cli.main(["agents", "rm", "claude-3"])  # no terminal to ask: needs --yes
    cli.main(["agents", "rm", "claude-3", "--yes"])
    assert "claude-3" not in roles_of(hv)
    assert [s.name for s in load_machine_config().slots] == ["claude-1", "claude-2"]
    assert not (tmp_path / "app-claude-3").exists()

    old = load_machine_config().slot("claude-2").token
    cli.main(["agents", "rotate", "claude-2"])
    new = load_machine_config().slot("claude-2").token
    assert new != old and ServerClient(url, new).get("/agents/me")["name"] == "claude-2"


def test_agents_elsewhere_then_join_infers_vendor_and_clone(fleet, tmp_path, monkeypatch,
                                                             capsys):
    clone, hv, url = fleet
    cli.main(["login", "--server", url, "--token", ADMIN])
    cli.main(["agents", "add", "codex", "reviewer", "--elsewhere"])
    out = capsys.readouterr().out
    line = next(ln.strip() for ln in out.splitlines() if "hive join codex-1" in ln)
    assert roles_of(hv) == {"codex-1": ["reviewer"]}
    assert not load_machine_config().slots  # not set up here
    # The other computer runs the printed line inside its clone: no --vendor or --repo.
    monkeypatch.chdir(clone)
    cli.main(line.split()[1:])
    s = load_machine_config().slot("codex-1")
    assert s.vendor == "codex" and s.worktree == str(tmp_path / "app-codex-1")
    with pytest.raises(SystemExit):
        cli.main([*line.split()[1:], "--vendor", "claude"])  # contradicts the server


def test_decision_shortcuts(fleet, capsys):
    clone, hv, url = fleet
    cli.main(["login", "--server", url, "--token", ADMIN])
    hv.enroll(Actor("admin", "user", []), "orch", "claude", ["orchestrator"])
    orch = Actor("agent", "orch", ["orchestrator"])
    with pytest.raises(SystemExit):
        cli.main(["approve"])  # nothing waiting
    q = hv.request_decision(orch, "question", "Ship it?", ["approve", "reject"])
    capsys.readouterr()
    cli.main(["decisions"])
    out = capsys.readouterr().out
    assert "hive approve" in out and f"hive approve {q['id']}" not in out  # only one open
    cli.main(["approve", "looks", "good"])
    d = next(d for d in hv.list_decisions() if d["id"] == q["id"])
    assert d["answer"] == "approve" and d["note"] == "looks good"

    a = hv.request_decision(orch, "question", "Which database?", ["postgres", "sqlite"])
    b = hv.request_decision(orch, "question", "Deploy?", ["approve", "reject"])
    with pytest.raises(SystemExit):
        cli.main(["approve"])  # two open and no terminal: must say which
    with pytest.raises(SystemExit):
        cli.main(["approve", str(a["id"])])  # not a yes/no question
    cli.main(["reject", str(b["id"]), "not", "yet"])
    cli.main(["answer", "postgres"])
    got = {d["id"]: (d["answer"], d["note"]) for d in hv.list_decisions()}
    assert got[b["id"]] == ("reject", "not yet") and got[a["id"]] == ("postgres", "")


def test_server_makes_and_saves_its_token_and_logs_in(tmp_path, monkeypatch, capsys):
    import werkzeug.serving
    started = []
    monkeypatch.setattr(werkzeug.serving, "run_simple", lambda *a, **k: started.append(a))
    monkeypatch.setenv("HIVE_DATABASE_URL", f"sqlite:///{tmp_path / 's.db'}")
    cli.main(["server", "--port", "9999"])
    token = (tmp_path / "home" / "server.toml").read_text().split('"')[1]
    from hive.config import load_admin_config
    assert load_admin_config().target() == ("http://127.0.0.1:9999", token)
    assert "logged in to it as admin" in capsys.readouterr().out
    cli.main(["server", "--port", "9999"])  # the same token next time
    assert (tmp_path / "home" / "server.toml").read_text().split('"')[1] == token
    assert started and "logged in" not in capsys.readouterr().out


def test_help_tiers_and_home(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit):
        cli.main(["--color", "never", "--help"])
    short = capsys.readouterr().out
    assert "hive agents add" in short and "push-config" not in short
    cli.main(["--color", "never", "help", "--all"])
    full = capsys.readouterr().out
    assert "admin" in full and "decide" in full
    with pytest.raises(SystemExit):  # a command's own help, not the short list
        cli.main(["--color", "never", "agents", "add", "--help"])
    assert "--elsewhere" in capsys.readouterr().out
    monkeypatch.chdir(tmp_path)
    cli.main(["--color", "never"])
    assert "hive server" in capsys.readouterr().out  # not logged in: start a server


def test_home_walks_through_setup(fleet, capsys):
    clone, hv, url = fleet
    cli.main(["login", "--server", url, "--token", ADMIN])
    capsys.readouterr()
    cli.main(["--color", "never"])
    assert "hive agents add claude\n" in capsys.readouterr().out
    hv.enroll(Actor("admin", "user", []), "orch", "claude", ["orchestrator"])
    cli.main(["--color", "never"])
    assert "hive agents add claude programmer" in capsys.readouterr().out
    hv.enroll(Actor("admin", "user", []), "dev", "codex", ["programmer"])
    cli.main(["--color", "never"])
    assert 'hive start "' in capsys.readouterr().out


def test_init_guesses_the_test_command(tmp_path):
    assert cli.guess_test_command(tmp_path) == ""
    (tmp_path / "package.json").write_text('{"scripts": {"test": "echo \\"Error: no test '
                                           'specified\\" && exit 1"}}')
    assert cli.guess_test_command(tmp_path) == ""  # npm's placeholder isn't a test suite
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
    assert cli.guess_test_command(tmp_path) == "npm test"
    (tmp_path / "package.json").unlink()
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (tmp_path / "tests").mkdir()
    assert cli.guess_test_command(tmp_path) == "python -m pytest -q"
    (tmp_path / "go.mod").write_text("module x\n")
    assert cli.guess_test_command(tmp_path) == "go test ./..."


def test_agents_cannot_run_the_new_user_commands():
    from hive import permissions as P
    for rules in P.build_rules({}).values():
        for cmd in ("hive approve", "hive reject", "hive answer", "hive agents add claude",
                    "hive config set integration.review off", "hive login", "hive watch"):
            assert P.matches(cmd, rules["forbidden"]), cmd


# ---------------------------------------------------------------------- fixes from review

def test_automatic_upload_only_goes_to_the_server_hive_toml_names(fleet, tmp_path):
    clone, hv, url = fleet
    other = create_hive(f"sqlite:///{tmp_path / 'other.db'}")
    srv = serve(create_app(other, admin_token=ADMIN))
    other_url = f"http://127.0.0.1:{srv.server_port}"
    try:
        # A command aimed at another server from this clone leaves that server's config alone.
        cli.main(["login", "--server", other_url, "--token", ADMIN])
        cli.main(["status"])
        with other._read() as c:
            assert other._fleet(c, lock=False)["config"] == {}
        # The server hive.toml names gets it, by any name for this computer.
        cli.main(["login", "--server", url.replace("127.0.0.1", "localhost"),
                  "--token", ADMIN])
        cli.main(["status"])
        with hv._read() as c:
            assert hv._fleet(c, lock=False)["config"]["repo"]["main"] == "main"
    finally:
        srv.shutdown()
    assert cli._same_server("http://localhost:8765/", "http://127.0.0.1:8765")
    assert not cli._same_server("http://127.0.0.1:8765", "http://127.0.0.1:8765/p/acme")
    assert not cli._same_server("https://team.example", "http://127.0.0.1:8765")


def test_hive_server_keeps_your_login_to_another_server(tmp_path, monkeypatch, capsys):
    from werkzeug import serving
    from hive.config import load_admin_config
    monkeypatch.setenv("HIVE_ADMIN_TOKEN", "")  # restored afterwards: cmd_server sets it
    monkeypatch.delenv("HIVE_ADMIN_TOKEN")
    monkeypatch.setenv("HIVE_DATABASE_URL", f"sqlite:///{(tmp_path / 'h.db').as_posix()}")
    monkeypatch.setattr(serving, "run_simple", lambda *a, **kw: None)
    cli.save_login("https://team.example", "team-token")
    cli.main(["server", "--port", "8799"])
    saved = load_admin_config(env=False)
    assert saved.server_url == "https://team.example" and saved.token == "team-token"
    local = saved.token_for("http://127.0.0.1:8799")
    assert local and local != "team-token"
    assert "still go to https://team.example" in capsys.readouterr().out
    # Without another login, the local server becomes the default.
    (tmp_path / "home" / "admin.toml").unlink()
    cli.main(["server", "--port", "8799"])
    assert load_admin_config(env=False).server_url == "http://127.0.0.1:8799"


def test_the_chat_runs_its_commands_in_its_own_project(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "main", lambda argv=None: ran.append(argv))
    cli.chat_runner("acme")(["approve", "3"])
    cli.chat_runner(None)(["status"])
    assert ran == [["--project", "acme", "approve", "3"], ["status"]]


def test_moving_an_agent_keeps_its_model_and_permission_mode(fleet, capsys):
    clone, hv, url = fleet
    admin = Actor("admin", "user", [])
    cli.main(["login", "--server", url, "--token", ADMIN])
    hv.enroll(admin, "dev", "claude", ["programmer"])
    for name in ("desk", "laptop"):
        hv.pair_machine(hv.create_pairing_code(admin)["code"], name)
    hv.place_agent(admin, "dev", "desk", "opus", "acceptEdits")

    def placement():
        return next(a for a in hv.roster() if a["name"] == "dev")["placement"]
    cli.main(["agents", "edit", "dev", "--on", "desk"])
    assert placement() == {"model": "opus", "permission_mode": "acceptEdits"}
    cli.main(["agents", "edit", "dev", "--on", "laptop", "--model", "sonnet"])
    assert placement() == {"model": "sonnet", "permission_mode": "acceptEdits"}
    cli.main(["agents", "edit", "dev", "--on", "desk", "--permission-mode", "default"])
    assert placement() == {"model": "sonnet", "permission_mode": ""}
