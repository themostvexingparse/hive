"""The chat window (hive/chat.py): messages both ways, decisions answered by Hive on y / n,
slash commands that are the hive commands, and following live output."""

import io

from hive import cli, ui
from hive.chat import Chat
from hive.daemon.client import ServerClient
from hive.server.service import Actor
from test_simple_cli import fleet, isolated  # noqa: F401  (fixtures)

ADMIN = "admin-secret"
ADMIN_ACTOR = Actor("admin", "user", [])
ORCH = Actor("agent", "orch", ["orchestrator"])


def make_chat(url, answers):
    ui.set_mode("never")
    out = io.StringIO()
    asked = []

    def fake_input(prompt):
        asked.append(prompt)
        return answers.pop(0)

    chat = Chat(ServerClient(url, ADMIN), run_command=lambda argv: cli.main(argv), out=out,
                input_fn=fake_input)
    return chat, out, asked


def test_chat_end_to_end(fleet, capsys):
    clone, hv, url = fleet
    cli.main(["login", "--server", url, "--token", ADMIN])
    hv.enroll(ADMIN_ACTOR, "orch", "claude", ["orchestrator"])
    dev = hv.enroll(ADMIN_ACTOR, "dev", "claude", ["programmer"])["token"]
    chat, out, asked = make_chat(url, ["y", "add tests too"])
    chat.open()
    assert "fleet setup" in out.getvalue()

    # In setup, a plain line becomes the goal, once you say so.
    assert chat.handle("Add a README")
    assert asked == ["Start the team on this goal? [Y/n] "]
    assert hv.get_fleet()["state"] == "planning" and hv.get_fleet()["goal"] == "Add a README"

    # The orchestrator's messages and updates arrive; your own aren't echoed back.
    hv.post_message(ORCH, "Planning now.", channel="#user")
    hv.publish_status(ORCH, "Read the repo")
    chat.poll()
    assert "orch: Planning now." in out.getvalue()
    assert "• orch: Read the repo" in out.getvalue()
    chat.handle("Keep it short, please")
    assert hv.list_messages(ADMIN_ACTOR, "#user")[-1]["body"] == "Keep it short, please"
    before = out.getvalue()
    chat.poll()
    assert "Keep it short" not in out.getvalue()[len(before):]

    # A yes/no decision is a prompt: y approves, n asks what to change.
    d1 = hv.request_decision(ORCH, "question", "Use pytest?", ["approve", "reject"])
    chat.poll()
    assert "Decision #" in out.getvalue() and "y = approve" in out.getvalue()
    chat.handle("y")
    assert hv.list_decisions()[-1]["answer"] == "approve"
    d2 = hv.request_decision(ORCH, "question", "Ship it?", ["approve", "reject"])
    chat.poll()
    chat.handle("n")
    got = next(d for d in hv.list_decisions() if d["id"] == d2["id"])
    assert (got["answer"], got["note"]) == ("reject", "add tests too")
    assert d1["id"] != d2["id"]

    # Other questions take /answer; slash commands are hive commands.
    hv.request_decision(ORCH, "question", "Which DB?", ["postgres", "sqlite"])
    chat.poll()
    assert "/answer postgres | sqlite" in out.getvalue()
    chat.handle("y")  # not a yes/no question: a plain message, not an answer
    assert hv.list_decisions()[-1]["state"] == "open"
    chat.handle("/answer sqlite")
    assert hv.list_decisions()[-1]["answer"] == "sqlite"
    capsys.readouterr()
    chat.handle("/agents")
    listed = capsys.readouterr().out
    assert "orch" in listed and "dev" in listed
    chat.handle("/server")
    assert "own terminal" in out.getvalue()
    chat.handle("/nonsense")  # a usage error is shown, the chat goes on
    assert chat.handle("/status")

    # /watch follows an agent's live output.
    chat.handle("/watch dev")
    ServerClient(url, dev).post("/agents/me/stream", {"run": "r1", "lines": ["→ Edit x.py"]})
    chat.poll()
    assert "Edit x.py" in out.getvalue()
    chat.handle("/watch off")
    assert not chat.handle("/quit")


def test_hive_next_and_chat_without_a_terminal(fleet, capsys):
    clone, hv, url = fleet
    cli.main(["login", "--server", url, "--token", ADMIN])
    capsys.readouterr()
    cli.main(["--color", "never", "next"])
    assert "hive agents add claude" in capsys.readouterr().out
    cli.main(["--color", "never"])  # no terminal: the same screen, never the chat
    assert "hive agents add claude" in capsys.readouterr().out
