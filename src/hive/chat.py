"""`hive` (or `hive chat`): talk to the team in one window (docs/CLI_REDESIGN.md, rung 0).

What you type goes to the orchestrator in #user; its replies, status updates and the fleet's
state come back as they happen. Decisions show up as prompts that Hive itself answers on your
keypress (`y` / `n`), never the AI, so "nobody codes until you approve" still holds. Slash
commands are the `hive` commands: `/agents add codex` runs `hive agents add codex`.

It works with the orchestrator wherever it runs: headless under `hive up --headless`, or in
its own terminal under `hive up` (#user messages wake it either way).
"""

from __future__ import annotations

import shlex
import sys
import threading
import time
from typing import Callable

from . import ui
from .daemon.client import HiveError, ServerClient

POLL_SECONDS = 2.0
HISTORY = 8  # earlier #user messages shown when the chat opens
# `hive` commands that make no sense inside the chat (they take over the terminal).
NOT_IN_CHAT = {"server", "up", "chat", "mcp", "hook", "join"}

HELP = """\
  Type a message for the orchestrator. When a decision waits: y approves, n sends it back.
  /approve [N] [note]   /reject [N] [note]   /answer [N] TEXT   /decisions
  /status   /agents [add …]   /machines   /config …   /pause   /resume   /next
  /watch [AGENT]        follow an agent's live output here (/watch off stops)
  /help   /quit         (any other /command runs `hive command`, e.g. /plan)
"""


class Chat:
    def __init__(self, client: ServerClient, run_command: Callable[[list[str]], None],
                 out=None, input_fn: Callable[[str], str] = input):
        self.c, self.run_command, self.input = client, run_command, input_fn
        self.out = out or sys.stdout
        self.lock = threading.Lock()
        self.seen_msg = self.seen_status = self.seen_stream = 0
        self.shown_decisions: set[int] = set()
        self.sent: set[int] = set()  # messages this window posted (not echoed back)
        self.state: str | None = None
        self.pending: dict | None = None  # an approve/reject decision a bare y / n answers
        self.watching: str | None = None
        self.stop = threading.Event()

    # ------------------------------------------------------------------ output

    def write(self, lines: list[str]) -> None:
        if not lines:
            return
        with self.lock:
            tty = getattr(self.out, "isatty", lambda: False)()
            # Above whatever is being typed: clear the prompt line, print, show it again.
            self.out.write(("\r\x1b[K" if tty and ui.enabled(self.out) else "")
                           + "\n".join(lines) + "\n" + ("> " if tty else ""))
            self.out.flush()

    def _decision_lines(self, d: dict) -> list[str]:
        opts = [o.lower() for o in d.get("options") or []]
        tag = " · freezes the fleet" if d.get("blocking") else ""
        lines = ["", ui.paint(f"┌ Decision #{d['id']} · {d['kind']}{tag}", "bright_yellow",
                              "bold", stream=self.out)]
        lines += ["│ " + ui.bold(q, self.out) for q in d["question"].splitlines()]
        ctx = (d.get("context") or "").splitlines()
        lines += ["│ " + ln for ln in ctx[:25]]
        if len(ctx) > 25:
            lines.append("│ " + ui.dim(f"… {len(ctx) - 25} more lines: /decisions", self.out))
        if "approve" in opts:
            hint = "y = approve · n = send back with a note"
        elif opts:
            hint = "/answer " + " | ".join(d["options"])
        else:
            hint = '/answer "your answer"'
        lines.append(ui.paint("└ " + hint, "bright_yellow", stream=self.out))
        return lines

    # ------------------------------------------------------------------ polling

    def open(self) -> None:
        """Show where things stand and the recent conversation; later polls show what's new."""
        fleet = self.c.get("/fleet")
        self.state = fleet["state"]
        msgs = self.c.get("/messages", channel="#user", limit=HISTORY)
        status = self.c.get("/status", limit=1)
        self.seen_status = status[-1]["id"] if status else 0
        lines = [ui.dim(f"  fleet {self.state}"
                        + (f" · goal: {fleet['goal'][:120]}" if fleet.get("goal") else ""),
                        self.out),
                 ui.dim("  /help lists the commands; /quit leaves (the team keeps working)",
                        self.out), ""]
        for m in msgs:
            lines.append(self._message_line(m))
            self.seen_msg = max(self.seen_msg, m["id"])
        self.write(lines)
        self.poll()

    def _message_line(self, m: dict) -> str:
        who = "you" if m["author"] == "user" else m["author"]
        colour = "gray" if who == "you" else "bright_cyan"
        body = m["body"].replace("\n", "\n    ")
        return ui.paint(f"{who}:", colour, "bold", stream=self.out) + " " + body

    def poll(self) -> None:
        lines: list[str] = []
        fleet = self.c.get("/fleet")
        if fleet["state"] != self.state:
            self.state = fleet["state"]
            lines.append(ui.dim(f"• the fleet is {self.state}", self.out))
        for m in self.c.get("/messages", channel="#user", since_id=self.seen_msg, limit=100):
            self.seen_msg = max(self.seen_msg, m["id"])
            if m["id"] not in self.sent:
                lines.append(self._message_line(m))
        for s in self.c.get("/status", since_id=self.seen_status, limit=50):
            self.seen_status = max(self.seen_status, s["id"])
            if s["level"] != "decision-needed":  # the decision itself is shown below
                lines.append(ui.dim(f"• {s['author']}: {s['summary']}", self.out))
        open_ = [d for d in self.c.get("/decisions", state="open") if d["state"] == "open"]
        for d in open_:
            if d["id"] not in self.shown_decisions:
                self.shown_decisions.add(d["id"])
                lines += self._decision_lines(d)
        yes_no = [d for d in open_ if "approve" in [o.lower() for o in d.get("options") or []]]
        self.pending = yes_no[0] if len(yes_no) == 1 else None
        if self.watching:
            r = self.c.get("/streams", agent=self.watching, since_id=self.seen_stream,
                           limit=200)
            self.seen_stream = r.get("last_id", self.seen_stream)
            lines += [ui.stream_line(None, l["text"], stream=self.out) for l in r["lines"]]
        self.write(lines)

    def _poll_loop(self) -> None:
        while not self.stop.wait(POLL_SECONDS):
            try:
                self.poll()
            except HiveError as e:
                if e.code != "unreachable":
                    self.write([ui.dim(f"• {e.message}", self.out)])
            except Exception as e:  # never kill the window over one bad poll
                self.write([ui.dim(f"• {e}", self.out)])

    # ------------------------------------------------------------------ input

    def handle(self, line: str) -> bool:
        """Act on one line; False ends the chat."""
        line = line.strip()
        if not line:
            return True
        if self.pending and line.lower() in ("y", "yes", "n", "no"):
            d = self.pending
            if line.lower().startswith("y"):
                self._run(["approve", str(d["id"])])
            else:
                note = self.input("What should change? ").strip()
                self._run(["reject", str(d["id"]), *([note] if note else [])])
            self.poll()
            return True
        if line.startswith("/"):
            try:
                parts = shlex.split(line[1:], posix=True)
            except ValueError as e:
                self.write([ui.dim(f"• {e}", self.out)])
                return True
            name, rest = (parts[0].lower(), parts[1:]) if parts else ("help", [])
            if name in ("quit", "exit", "q"):
                return False
            if name in ("help", "?"):
                tbl = ui.format_table(
                    ["command", "action"],
                    [
                        [ui.cmd("/approve [N] [note]"), "approve a waiting decision (or press y)"],
                        [ui.cmd("/reject [N] [note]"), "send a decision back with a note (or press n)"],
                        [ui.cmd("/answer [N] TEXT"), "answer a multi-choice or free-text decision"],
                        [ui.cmd("/decisions"), "list open decisions waiting for you"],
                        [ui.cmd("/status"), "show fleet, agents, tasks and status feed"],
                        [ui.cmd("/agents [add …]"), "list or manage agents"],
                        [ui.cmd("/machines"), "list paired computers"],
                        [ui.cmd("/config …"), "read or update configuration"],
                        [ui.cmd("/pause · /resume"), "pause or resume all agents"],
                        [ui.cmd("/next"), "show where things stand and the next step"],
                        [ui.cmd("/watch [AGENT|off]"), "follow an agent's live output here"],
                        [ui.cmd("/help · /quit"), "show this table or leave the chat"],
                    ],
                    stream=self.out,
                    boxed=True,
                )
                self.write([
                    ui.dim("  Type a message for the orchestrator. Any /command runs `hive command`.", self.out),
                    *tbl,
                ])
            elif name == "watch":
                self.watching = None if (rest and rest[0] == "off") else (
                    rest[0] if rest else self.c.get("/fleet").get("orchestrator"))
                self.seen_stream = 0 if not self.watching else self.c.get(
                    "/streams", agent=self.watching, limit=1).get("last_id", 0)
                self.write([ui.dim(f"• following {self.watching}'s live output" if
                                   self.watching else "• stopped following", self.out)])
            elif name in NOT_IN_CHAT:
                self.write([ui.dim(f"• run `hive {name}` in its own terminal", self.out)])
            else:
                self._run([name, *rest])
            self.poll()
            return True
        if self.state == "setup":
            ans = self.input("Start the team on this goal? [Y/n] ").strip().lower()
            if ans in ("", "y", "yes"):
                self._run(["start", line])
            self.poll()
            return True
        try:
            m = self.c.post("/messages", {"channel": "#user", "body": line,
                                          "priority": "high"})
            self.sent.add(m["id"])
        except HiveError as e:
            self.write([ui.dim(f"• not sent: {e.message}", self.out)])
        return True

    def _run(self, argv: list[str]) -> None:
        try:
            self.run_command(argv)
        except SystemExit:
            pass  # the command already said what went wrong

    def run(self) -> None:
        self.open()
        threading.Thread(target=self._poll_loop, daemon=True, name="chat-poll").start()
        try:
            while True:
                try:
                    line = self.input("> ")
                except EOFError:
                    break
                if not self.handle(line):
                    break
        except KeyboardInterrupt:
            pass
        finally:
            self.stop.set()
            self.out.write("\n")
