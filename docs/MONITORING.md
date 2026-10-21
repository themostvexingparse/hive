# Watching the fleet: live output and the dashboard

You can see everything your Hive fleet (the team of AI agents working on your repository)
does, from anywhere, in two ways:

- **`hive watch`** follows the agents' **live output** in a terminal: everything each agent
  writes and every tool it calls, as it works.
- **The web dashboard**, a web page the Hive server shows at its address, gives the whole
  fleet at once and lets you steer it. It shows the agents, their [tasks](COORDINATION.md#tasks),
  their [leases](INTEGRATION.md#leases) (locks on files), [decisions](GLOSSARY.md#decision)
  waiting for you, [approvals](PERMISSIONS.md) of risky commands, the orchestrator's status
  updates, the agents' [reports](COORDINATION.md#reports), and the live output.

Both need your admin login ([Log in as admin](SERVER.md#log-in-as-admin)).

Live output is **visible to you only**. The server refuses it to every agent, including the
orchestrator (the agent that coordinates the others). Agents learn about each other through
messages and reports, never through raw output. That keeps what each agent has to read
short, and keeps anything secret an agent printed away from other agents.

---

## Following live output in the terminal (`hive watch`)

```sh
hive watch                 # all agents, follows new lines (Ctrl+C to stop)
hive watch claude-2        # one agent (claude-2 is an example agent name)
hive watch -n 1000         # start with the last 1000 lines (default 200)
hive watch --no-follow     # print and exit
```

`hive watch` works on any computer where you're logged in as admin. It is the easiest way to
see agents running on the same computer as the orchestrator: there, `hive up` gives its
terminal to your chat with the orchestrator, so the others' output isn't shown.

**Line format:** each line starts with the agent's name in its own colour, then a `│`
separator, then:

| Line | Colour | Meaning |
|---|---|---|
| `  text` | default | the agent's prose |
| `→ tool {input}` | blue | a tool call |
| `← result` | gray | a tool result |
| `× error` | red | a failed tool call or an error |
| `· …` | magenta, italic | Hive's notes: a new conversation (*session*) started, the agent was started for some work (a *wake*) and why, a run (*turn*) ended |

- **Retention:** the server keeps the latest 20,000 lines per agent.
- **Raw output:** every run's complete unformatted output is also appended to
  `~/.hive/logs/<agent name>.log` on the computer that runs the agent.
- **Dashboard:** the same lines appear in the dashboard's *Live output* panel, which updates
  every 1.5 s.

---

## The web dashboard

Open the server's address in a browser (for example `http://127.0.0.1:8765/` for a local
trial) and sign in with the admin token. If the server hosts [several projects](PROJECTS.md),
each has its own address, `https://…/p/<project>/`; sign in there with that project's admin
token or the server's. Signed in with the server's token, the ⋯ (three dots) menu at the top
right lists every project to switch between. If you tick *Remember on this device*, the token
stays saved in this browser until you sign out (⋯ menu); otherwise it's forgotten when the
tab closes.

### Areas

| Area | Contents |
|---|---|
| Top bar | The [fleet state](LIFECYCLE.md) (the phase the team is in), the goal, which agent is the orchestrator, the latest commit on `main`, and a *Live* / *Reconnecting…* indicator. *Pause* or *Resume*, whichever applies ([Pausing](LIFECYCLE.md#pausing-resuming-resetting)). A theme button that cycles light → dark → system. The ⋯ menu has the theme, *Archives…* (below), *Reset fleet…* (asks for confirmation) and *Sign out*. |
| Decisions | Shown across the top while one is open. Each has its context, one button per option, a free-text answer and a note. A decision waiting behind the current one is shown as *Up next*. Before the fleet has a goal (state `setup`), this area holds the goal box and *Start fleet* instead. |
| Agents | One card per agent: colour, CLI (Claude Code, Codex, agy or Gemini CLI), [roles](ROLES.md), state (idle, working, interactive, offline, or out of quota: its account has used up its allowance for now), last seen and current task. Click a card to show only that agent's output. |
| Leases | Who holds each file lock, which paths it covers (as patterns such as `src/api/**`), and time left (amber when under 2 minutes). *Revoke* takes the files away from the holder after a confirmation; its next attempt to merge them into `main` is refused. |
| Live output | All agents' formatted output in one scrolling panel; see below. |
| Tasks | Filter by *Active*, *Review*, *Finished* or *All* (with counts). Click a row for its spec, notes, result, paths, dependencies, branch and reviewer. |
| Orchestrator | Your conversation with the orchestrator (the `#user` channel). Enter sends; Shift+Enter starts a new line. |
| Updates | The orchestrator's status updates, newest first. Alerts are tinted; long ones fold behind *Show more*. |
| Approvals | Dangerous shell commands agents asked to run ([how approvals work](PERMISSIONS.md#how-approval-works-for-workers)). You can approve (after a confirmation showing the exact command) or deny with a note, just as the orchestrator can. The tab shows how many are waiting. |
| Reports | The orchestrator's requests for reports, and the reports agents filed. |

Everything you can do from the CLI as admin (start, pause, resume, reset, answer decisions,
message the orchestrator) you can also do from the dashboard. That makes it the easiest way
to steer the fleet from a phone.

### Live output

- **Agent colours:** every agent name has its own colour, the same one `hive watch` uses in
  the terminal.
- **Filtering:** chips filter by agent (click one to show only it; Ctrl/Shift+click to
  combine several). The filter box (shortcut `/`) narrows to matching lines and marks the
  matches.
- **Highlighting:** done in the browser, with no extra requests or prompting. It marks tool
  calls, errors, failure and success words, file paths, URLs, commit hashes, task numbers
  (`#4`), quoted strings, the names of Hive's tools, and agent names.
- **Toggles:** *Results* hides tool results, *Wrap* switches between wrapping long lines and
  scrolling sideways (inside the panel only), and *Follow* keeps the newest line in view.
  Scrolling up pauses *Follow*, and a *N new lines* button takes you back down.
- **Clear:** the bin button empties the view without deleting the server's history.
- **Limit:** up to 5,000 lines stay in the page.

### Archives

**⋯ → *Archives…*** keeps a run's history without letting it pile up in the live view. A
*run* is everything from giving the fleet a goal until you clear it.
- **Save snapshot** writes everything the dashboard shows to an archive file on the server:
  fleet and goal, agents (without their passwords, the agent tokens), tasks, decisions, all
  messages (including agent-to-agent ones), updates, reports, command approvals with their
  output, leases and the recorded agent output. Nothing is removed.
- **Archive & clear…** does the same, then removes all of that from the database and puts the
  fleet back in `setup`, ready for a new goal. Agents, their tokens, the project settings
  uploaded from `hive.toml`, which agent is orchestrator, and the latest `main` commit are
  kept, so computers running agents carry on without being set up again (`hive join`). It's
  refused unless the fleet is paused (or in `setup`) and nothing is in progress: no leased
  files, no waiting or approved commands, no merge into `main` under way. The dialog says
  which of these is in the way; revoke leases from the Leases panel.
- **Open** shows an archive in the normal layout, read-only: an *Archive* banner with its
  decisions and their answers, the agents, tasks (all of them), the recorded output (with the
  usual filters and search), every message with where it went, updates, approvals and
  reports. *Back to live* returns to the live view. Archives with more than 5,000 output lines
  show the newest 5,000; picking an agent or searching works on all of them.
- **Download** saves the archive as `hive-archive-<id>.json.gz` (JSON data, compressed with
  gzip).
  *Open a file…*, or *Open an archive file* on the sign-in page, opens such a file (or a plain
  `.json`) in the browser without signing in; nothing is uploaded.
- **Delete** removes an archive from the server for good.
- **Where the files are:** on the server, in the folder set by the `HIVE_ARCHIVE_DIR`
  environment variable ([configuration reference](CONFIGURATION.md#environment-variables)).
  Archives are history, not backups: they can't be loaded back into the live database. For backups, see
  [DEPLOYMENT.md §10](DEPLOYMENT.md#10-backups-and-restore).

### Other behaviour

- **Theme:** light by default; the choice is remembered in the browser.
- **Small screens:** the layout stacks into one column on phones.
- **Tab title:** shows how many decisions and approvals are waiting, e.g. `(2) Hive · Frozen`.
- **Refresh rate:** the dashboard refreshes every 4 s and live output every 1.5 s. A tab
  you're not looking at slows to 30 s and 10 s, to spare the server. While the server is
  unreachable, it retries, waiting longer between attempts.
- **Typing is safe:** text you're typing in a decision or approval isn't lost when the data
  refreshes.

---

## Quick status from the terminal

For a one-shot summary instead of a live view:

```sh
hive status          # fleet state, waiting decision, agents, tasks, leases, last updates
hive status -v       # with the full text of status updates
hive status --all    # include finished tasks
hive decisions       # what's waiting for you
```

---

**Next:** [The fleet lifecycle](LIFECYCLE.md) ·
**See also:** [Working with the orchestrator](ORCHESTRATOR.md),
[CLI.md §9](CLI.md#9-steering-the-fleet) ·
[← Back to the README](../README.md)
