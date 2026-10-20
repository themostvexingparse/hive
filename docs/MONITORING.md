# Watching the fleet: live output and the dashboard

You can see everything the fleet does, from anywhere, in two ways:

- **`hive watch`** follows agents' live output in a terminal.
- **The web dashboard** on the server shows the whole fleet at once (agents, tasks, leases,
  decisions, approvals, updates, reports and live output), and lets you steer it.

Workers' live output is **visible to you only**. The server refuses it to every agent token,
including the orchestrator's. Agents learn about each other through messages and reports,
never through raw output, which keeps their context small and keeps anything secret an agent
printed away from other agents.

---

## Following live output in the terminal (`hive watch`)

```sh
hive watch                 # all agents, follows new lines (Ctrl+C to stop)
hive watch claude-2        # one agent
hive watch -n 1000         # start with the last 1000 lines (default 200)
hive watch --no-follow     # print and exit
```

`hive watch` works on any computer where you're logged in as admin. It is the easiest way to
see workers running on your orchestrator's machine, whose terminal belongs to the
orchestrator's CLI.

**Line format:** each line starts with the agent's name in its own colour, then a `│`
separator, then:

| Line | Colour | Meaning |
|---|---|---|
| `  text` | default | the agent's prose |
| `→ tool {input}` | blue | a tool call |
| `← result` | gray | a tool result |
| `× error` | red | a failed tool call or an error |
| `· …` | magenta, italic | session, wake and turn markers |

- **Retention:** the server keeps the latest 20,000 lines per agent.
- **Raw output:** every turn's complete unformatted output is also appended to
  `~/.hive/logs/<slot>.log` on the worker's machine.
- **Dashboard:** the same lines appear in the dashboard's *Live output* panel, which updates
  every 1.5 s.

---

## The web dashboard

Open the server's root URL (`https://…/`) and sign in with the admin token. For another
project, open its URL, `https://…/p/<project>/`, and sign in with that project's admin token
or the server's. Signed in with the server's token, the ⋯ menu lists every project to switch
between. If you tick *Remember on this device*, the token stays in the browser's local
storage until you sign out (⋯ menu); otherwise it's forgotten when the tab closes.

### Areas

| Area | Contents |
|---|---|
| Top bar | Fleet state, goal, orchestrator, `main` commit, and a *Live* / *Reconnecting…* indicator. *Pause* or *Resume*, whichever applies. A theme button that cycles light → dark → system. The ⋯ menu has the theme, *Archives…* (below), *Reset fleet…* (asks for confirmation) and *Sign out*. |
| Decisions | Shown across the top while one is open. Each has its context, one button per option, a free-text answer and a note. A queued decision is shown as *Up next*. In `setup`, this area holds the goal box and *Start fleet* instead. |
| Agents | One card per agent: colour, vendor, roles, state (idle, working, interactive, offline, out of quota), last seen and current task. Click a card to show only that agent's output. |
| Leases | Holder, path globs and time left (amber when under 2 minutes). *Revoke* takes the files away from the holder after a confirmation; its next merge of them is refused. |
| Live output | All agents' formatted output in one scrolling panel; see below. |
| Tasks | Filter by *Active*, *Review*, *Finished* or *All* (with counts). Click a row for its spec, notes, result, paths, dependencies, branch and reviewer. |
| Orchestrator | The `#user` conversation. Enter sends; Shift+Enter starts a new line. |
| Updates | The status feed, newest first. Alerts are tinted; long bodies fold behind *Show more*. |
| Approvals | Commands workers asked to run. You can approve (after a confirmation showing the exact command) or deny with a note, just as the orchestrator can. The tab shows how many are waiting. |
| Reports | Report requests and filed reports. |

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
  calls, errors, failure and success words, file paths, URLs, commit hashes, `#task`
  references, quoted strings, Hive tool names, and agent names.
- **Toggles:** *Results* hides tool results, *Wrap* switches between wrapping long lines and
  scrolling sideways (inside the panel only), and *Follow* keeps the newest line in view.
  Scrolling up pauses *Follow*, and a *N new lines* button takes you back down.
- **Clear:** the bin button empties the view without deleting the server's history.
- **Limit:** up to 5,000 lines stay in the page.

### Archives

**⋯ → *Archives…*** keeps a run's history without letting it pile up in the live view.
- **Save snapshot** writes everything the dashboard shows to an archive file on the server:
  fleet and goal, agents (without their tokens), tasks, decisions, all messages (including
  agent-to-agent ones), updates, reports, command approvals with their output, leases and the
  recorded agent output. Nothing is removed.
- **Archive & clear…** does the same, then removes all of that from the database and puts the
  fleet back in `setup`, ready for a new goal. Agents, their tokens, the uploaded `hive.toml`,
  the orchestrator assignment and `main`'s commit are kept, so machines keep running without
  re-joining. It's refused unless the fleet is paused (or in `setup`) and nothing is in flight:
  no leased files, no waiting or approved commands, no merge into `main` in progress. The
  dialog says which of these is in the way; revoke leases from the Leases panel.
- **Open** shows an archive in the normal layout, read-only: an *Archive* banner with its
  decisions and their answers, the agents, tasks (all of them), the recorded output (with the
  usual filters and search), every message with where it went, updates, approvals and
  reports. *Back to live* returns to the live view. Archives with more than 5,000 output lines
  show the newest 5,000; picking an agent or searching works on all of them.
- **Download** saves the archive as `hive-archive-<id>.json.gz` (gzip-compressed JSON).
  *Open a file…*, or *Open an archive file* on the sign-in page, opens such a file (or a plain
  `.json`) in the browser without signing in; nothing is uploaded.
- **Delete** removes an archive from the server for good.
- **Where the files are:** see `HIVE_ARCHIVE_DIR` in the
  [configuration reference](CONFIGURATION.md#environment-variables). Archives are history, not
  backups: they can't be loaded back into the live database. For backups, see
  [DEPLOYMENT.md §10](DEPLOYMENT.md#10-backups-and-restore).

### Other behaviour

- **Theme:** light by default; the choice is remembered in the browser.
- **Small screens:** the layout stacks into one column on phones.
- **Tab title:** shows how many decisions and approvals are waiting, e.g. `(2) Hive · Frozen`.
- **Refresh rate:** the dashboard refreshes every 4 s and live output every 1.5 s. A hidden
  tab slows to 30 s and 10 s, to spare the server. While the server is unreachable, it retries
  with back-off.
- **Typing is safe:** text you're typing in a decision or approval isn't lost when the data
  refreshes.

---

## Quick status from the terminal

For a one-shot summary instead of a live view:

```sh
hive status          # fleet state, waiting decision, agents, tasks, leases, last updates
hive status -v       # with the bodies of status updates
hive status --all    # include finished tasks
hive decisions       # what's waiting for you
```

---

**Next:** [The fleet lifecycle](LIFECYCLE.md) ·
**See also:** [Working with the orchestrator](ORCHESTRATOR.md),
[CLI.md §9](CLI.md#9-steering-the-fleet) ·
[← Back to the README](../README.md)
