# Deploying the Hive server

The Hive server (`hive-server`) is the program that holds everything a Hive **fleet** (a team
of AI coding agents working on one repository) shares: the agents, their tasks and messages,
their locks on files (*leases*), the questions waiting for you (*decisions*) and their live
output. It also serves the web dashboard. The agents' computers and your own `hive` commands
all talk to it over HTTPS, so for a fleet spread over several computers it has to be
reachable from all of them. See [How Hive works](ARCHITECTURE.md) for the big picture and the
[glossary](GLOSSARY.md) for terms.

This guide covers everything about running the server:
- what it needs;
- how much data and load to expect, with measured numbers;
- step-by-step deployment on [PythonAnywhere](GLOSSARY.md#pythonanywhere) (a Python web
  host), on a [VPS](GLOSSARY.md#vps) (a rented Linux server), and locally;
- upgrades, backups, data retention, monitoring and hardening.

**In the examples,** `USER` is your PythonAnywhere username, `hive.example.com` your own
domain on a VPS, and `acme` an example [project](PROJECTS.md) (a second repository on the same
server). For a first local trial you don't need this guide: see [Running the server](SERVER.md).

For setting up the agents' computers afterwards, see [Preparing the repository](REPOSITORY.md) and [Enrolling agents and setting up machines](MACHINES.md).

---

## Contents

1. [What the server is and what it needs](#1-what-the-server-is-and-what-it-needs)
2. [Choosing a host](#2-choosing-a-host)
3. [Capacity planning: how much data and load to expect](#3-capacity-planning-how-much-data-and-load-to-expect)
4. [Deploying on PythonAnywhere](#4-deploying-on-pythonanywhere)
5. [Deploying on a VPS (gunicorn + Caddy + systemd)](#5-deploying-on-a-vps-gunicorn--caddy--systemd)
6. [Running locally or on a local network](#6-running-locally-or-on-a-local-network)
7. [Configuration reference](#7-configuration-reference)
8. [After deploying: first-time checklist](#8-after-deploying-first-time-checklist)
9. [Upgrading](#9-upgrading)
10. [Backups and restore](#10-backups-and-restore)
11. [Data retention and pruning](#11-data-retention-and-pruning)
12. [Monitoring](#12-monitoring)
13. [Security hardening](#13-security-hardening)
14. [Troubleshooting](#14-troubleshooting)
15. [Hosting several projects](#15-hosting-several-projects)

---

## 1. What the server is and what it needs

`hive-server` is a plain Python web application, written with Flask and following the
**WSGI** standard that Python web hosts use to run apps ([WSGI](GLOSSARY.md#wsgi)). Its entry
point is `hive.server.wsgi:application`. It stores its data in one SQL database.

| Property | Value |
|---|---|
| Protocol | HTTPS + JSON. Every request is short; there are **no requests held open waiting for news (long-polls) and no permanent connections (WebSockets)**, which many cheap hosts don't support. |
| Background work | **None.** Periodic duties (expiring old leases, marking agents offline, letting a standby take over from an orchestrator that went offline, assigning tasks) run at the start of whichever request comes next, at most once per second per server process. No cron jobs, scheduled tasks or always-on tasks are needed. |
| Database | SQLite (a single local file; on network drives such as PythonAnywhere's, in its safer "rollback journal" writing mode), MySQL, or Postgres, through the SQLAlchemy library. Tables and the SQLite file's folder are created automatically on first start. |
| Outbound internet | Only for the optional [GitHub mode](INTEGRATION.md#github-mode-optional), in which the server updates the repository's `main` branch through `api.github.com`. Otherwise none. |
| Inbound | The [daemons](GLOSSARY.md#daemon) (the background program `hive up` runs on each computer with agents), your `hive` admin commands, and your browser (dashboard). |
| Secrets | `HIVE_ADMIN_TOKEN` (required) and optionally `HIVE_GITHUB_TOKEN`. |

**Who connects, and how often:**

| Client | Requests |
|---|---|
| Each agent's daemon (one per agent, or *slot*, on its computer) | asks for news (`GET /events`) every 3 s; reports that it's alive (a *heartbeat*) every 20 s; uploads what the agent prints, up to once a second **while that agent is running**. |
| Agents using Hive's tools | One request per tool call (messages, tasks, leases, …). |
| Dashboard (while open) | About 2 requests per second. |
| `hive watch` (while running) | One request every 1.5 s. |

---

## 2. Choosing a host

| Host | Suits | Database | Notes |
|---|---|---|---|
| **PythonAnywhere – Developer** ($10/mo at the time of writing) | Recommended easy option | MySQL (included) | 3 web workers (server processes handling requests in parallel), 5 GB disk, unrestricted outbound internet, HTTPS included. See §3.5 for the verdict. |
| PythonAnywhere – free (Beginner) | Trials and small fleets | **SQLite** (a `.db` file in your home directory) | Works with Hive's SQLite mode for network drives (§4.8). Limits: 512 MB disk, one web worker, and PythonAnywhere itself advises against SQLite for production. |
| VPS (Hetzner, DigitalOcean, …) | Full control, lowest cost at scale | SQLite, Postgres or MySQL | You manage HTTPS certificates (TLS), updates and backups (§5). |
| Fly.io / Railway / Render | Container platforms (they run your app in a container) | Managed Postgres | Run the same gunicorn command as §5. Use a managed database, not SQLite inside the container. |
| Your own computer / local network | Trials, fleets in one place | SQLite | §6. Not reachable from outside without a tunnel service. |

Plan details change; check [PythonAnywhere's pricing page](https://www.pythonanywhere.com/pricing/)
before buying.

---

## 3. Capacity planning: how much data and load to expect

### 3.1 How the numbers were obtained

`scripts/simulate_load.py` drives the **real server code** with a made-up workload and
measures the resulting database. One simulated **busy day** for a 7-agent fleet (1
orchestrator, 4 programmers, 1 reviewer, 1 tester, with [reviews](INTEGRATION.md#reviews)
turned on) contains:

- **Tasks:** 30, each with a 1.5 KB description, 3 progress notes, a lease, a review, a merge
  into `main` and a 2 KB report.
- **Messages:** 150, about 400 characters each.
- **Status updates:** 40 from the orchestrator, each with a 1.5 KB body.
- **Other:** 5 report requests with answers, and one question to you from a stuck agent that
  paused the fleet ([Stuck agents and freezing](LIFECYCLE.md#stuck-agents-and-freezing)).

Thirty integrated tasks a day is a **heavy** pace for subscription accounts with usage limits.
Treat it as an upper estimate for everyday use. To model your own fleet:

```sh
uv run python scripts/simulate_load.py /tmp/hive-sim 30     # simulate 30 busy days
```

The measurements below are SQLite file sizes after `VACUUM` (SQLite's command to compact a
file). **MySQL (with its usual InnoDB storage engine) typically stores the same data in
1.5–3× the space** because of how it lays out rows and indexes, so MySQL figures are given as
a range.

### 3.2 What grows, and what doesn't

| Table | Growth | Bounded? |
|---|---|---|
| `events` | ≈ 540 rows per busy day; a log of everything that happens (every message, task change, lease and decision), which daemons read to learn what's new | **no**: grows forever unless pruned (§11) |
| `messages` | one row per message | **no** |
| `tasks`, `reports`, `status_feed`, `decisions`, `leases` | per task / report / update | **no**, but small |
| `streams` (live output) | up to 1 line per output line of a running agent | **yes**: the newest 20,000 lines per agent are kept; older lines are deleted on each upload |
| `agents`, `receipts` (which agent has read which message), `fleet` | tiny | effectively constant |

**Single-row size caps:**

| Item | Cap |
|---|---|
| Message | 8 KB |
| Status body | 50 KB |
| Report body | 50 KB |
| Task notes | 20,000 chars |
| Task result | 20,000 chars |
| Live-output line | 4000 chars |

### 3.3 Measured results

| Measurement | SQLite (measured) | MySQL (estimated) |
|---|---|---|
| Coordination data, **1 busy day** | 0.67 MB | 1.0 – 2.0 MB |
| Coordination data, **30 busy days** | 19.6 MB (16,102 events, 4,501 messages, 900 tasks, 1,200 reports) | 30 – 60 MB |
| Live output, **one agent at the 20,000-line cap** (avg. line 95 chars) | 2.8 MB | 4 – 8 MB |

Projected totals for a 7-agent fleet. Live output is about 20 MB (SQLite) or 30–55 MB
(MySQL), regardless of age.

| Activity level | per month | per year (SQLite) | per year (MySQL, estimated) |
|---|---|---|---|
| Light: ~10 tasks/day, ~50 messages | ≈ 7 MB | ≈ 0.1 GB | 0.15 – 0.3 GB |
| **Busy: ~30 tasks/day, ~150 messages** (measured) | ≈ 20 MB | ≈ 0.25 GB | 0.35 – 0.75 GB |
| Extreme: ~100 tasks/day, ~500 messages, every day | ≈ 65 MB | ≈ 0.8 GB | 1.2 – 2.4 GB |

**Rule of thumb:** each finished task (merged into `main`), with its share of messages,
reports and status updates, costs about **22 KB in SQLite**, or **35–65 KB in MySQL**.

### 3.4 Request load

For a 7-agent fleet whose daemons run 24 hours a day, with 4 worker agents active about 4
hours a day each:

| Source | Requests per day |
|---|---|
| Event polling (7 slots × every 3 s) | ≈ 201,600 |
| Heartbeats (7 slots × every 20 s) | ≈ 30,200 |
| Live-output uploads (4 agents × 4 h × ≤ 1/s) | ≤ 57,600 |
| Tool calls, dashboard, `hive watch` | a few thousand to tens of thousands |
| **Total** | **≈ 0.3 million per day, ≈ 3–4 per second on average** |

**Measured cost** (on a desktop computer, calling the server code directly, so excluding the
network and the web server, which typically add a few ms):

| Request | With 1 day of history | With 30 days of history |
|---|---|---|
| Agent poll (`GET /events`, nothing new) | 0.81 ms | 0.61 ms |
| Maintenance tick: the periodic duties from §1 (≤ once per second per server process) | 0.9 ms | 9.9 ms |

**What this means:**
- **Poll cost doesn't grow with history**: new events are found directly by their number.
- **The maintenance tick does grow.** The part that assigns tasks currently reads all tasks,
  including finished ones, so it scales with the total number of tasks. Projected: about 120 ms after a
  busy year (≈ 11,000 tasks), i.e. up to ~12 % of one worker's time. Pruning finished tasks
  (§11) keeps it flat. Making the scheduler read only unfinished tasks is a planned
  improvement.
- **Server processes and time limits:** even at 5–10 ms per request on shared hosting, 0.3
  million requests a day is only 25–50 minutes of processing per day: 2–3 % of one web
  worker. Three workers leave plenty of headroom. No request waits for news: when the
  orchestrator waits (with its `await_events` tool), it waits inside the daemon on its own
  computer, never on the server, so hosts' per-request time limits don't matter.
- **Access logs:** most hosts log every request. At ~150 bytes per line, 0.3 million requests
  are ≈ 40–50 MB of access log per day. Hosts rotate these logs, but check whether rotated
  archives count against your disk quota, and delete old ones if they do.

### 3.5 Verdict for PythonAnywhere

**Developer plan: sufficient, with a wide margin.**
- **Disk:** 5 GB. Even with InnoDB overhead, a busy fleet uses well under 1 GB per year. With
  the pruning in §11 the database stays at a few hundred MB indefinitely. According to
  PythonAnywhere staff on their forums, MySQL data counts against the disk quota even though
  the dashboard may not show it.
- **Workers:** 3 web workers handle ~3–4 requests per second of 1–10 ms requests easily.
- **CPU:** the plan's daily CPU-seconds allowance doesn't apply to web apps, according to the
  pricing page.
- **Database:** MySQL is included. Its idle-connection timeout is handled: Hive recycles
  connections every 280 s.

**Free plan: workable for trials and small fleets, with SQLite (§4.8).** New free accounts have
no MySQL, so Hive stores its data in a SQLite file. PythonAnywhere keeps your files on network
storage, so Hive runs SQLite in a mode built for that:
- SQLite's "rollback journal" way of writing, instead of its faster default (WAL), which
  doesn't work reliably on network storage;
- reads that never take the write lock;
- one writer at a time inside the single web worker;
- an idle server that does no writes at all;
- automatic retries when the database is briefly busy;
- backups taken inside the server process.

The limits that remain:

| Limit | Impact |
|---|---|
| **512 MB disk** | A busy fleet's database (§3.3) fits for roughly 1–1½ years without pruning; with the pruning in §11, indefinitely. Old access logs and backup files also count. |
| **One web worker** | Every request is served one at a time. Fine for ~3–4 requests per second of short requests, but each write must also sync to network storage, so expect slower responses than on a paid plan, especially with several agents streaming output at once. |
| **SQLite on a network filesystem** | PythonAnywhere staff [advise against SQLite in production](https://pythonanywhere.com/forums/topic/35474) because locks on their storage can occasionally get stuck until they intervene. Hive keeps all access inside one process, which avoids the usual causes, but this risk can't be fully removed. Take regular backups (`hive admin backup`). |

**Recommendation:** the free plan is a good way to try Hive or run a small fleet (a few
agents). For daily use with a full fleet, the Developer plan with MySQL is the reliable choice.

**Scale limits:** roughly, beyond ~15 agents or several always-open dashboards, consider
adding workers (PythonAnywhere custom plan) or moving to a VPS with Postgres.

---

## 4. Deploying on PythonAnywhere

These steps use the **Developer** plan, or any paid plan with MySQL. Replace `USER` with your
PythonAnywhere username.

### 4.1 Get the code onto PythonAnywhere

Open a **Bash console** (a terminal in your browser, from PythonAnywhere's Consoles tab) and
clone the repository containing Hive's source code:

```bash
git clone https://github.com/<you>/<hive-repo>.git ~/hive
```

Or upload a zip in the **Files** tab and unzip it into `~/hive`.

### 4.2 Create a virtualenv and install Hive

A *virtualenv* is a private Python environment, so Hive's packages don't mix with others.
`mkvirtualenv` (provided on PythonAnywhere) creates one and switches to it. Use Python 3.12 or
newer:

```bash
mkvirtualenv --python=python3.12 hive
pip install "$HOME/hive[mysql]"
python -c "import hive, pymysql; print('ok', hive.__version__)"
```

The `[mysql]` extra also installs PyMySQL, the library Hive uses to talk to MySQL. The virtualenv lives at
`/home/USER/.virtualenvs/hive`.

### 4.3 Create the database

1. **Databases** tab → **MySQL**: set a MySQL password if you haven't yet. This is separate
   from your login password.
2. Create a database named `hive`. PythonAnywhere names it **`USER$hive`**.
3. Note the host: **`USER.mysql.pythonanywhere-services.com`**.

The resulting URL:

```
mysql+pymysql://USER:MYSQL_PASSWORD@USER.mysql.pythonanywhere-services.com/USER$hive?charset=utf8mb4
```

If the password contains special characters (`@ : / ? #` …), they must be written in the
encoded form web addresses use (`@` becomes `%40`, and so on). This prints the encoded form: `python -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1], safe=''))" 'p@ss'`.

### 4.4 Generate the admin token

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

This is the **admin token**, your password for the server. Store it in your password manager.
This token is full control over the fleet.

### 4.5 Create the web app

1. **Web** tab → **Add a new web app** → your domain (`USER.pythonanywhere.com` or a custom
   one) → **Manual configuration** (not "Flask") → the same Python version as the virtualenv.
2. In the web app's settings, set **Virtualenv** to `/home/USER/.virtualenvs/hive`.
3. Open the **WSGI configuration file** link (`/var/www/USER_pythonanywhere_com_wsgi.py`):
   the small Python file PythonAnywhere runs to start your app. Replace its entire contents
   with:

   ```python
   import os

   os.environ["HIVE_ADMIN_TOKEN"] = "<the token from 4.4>"
   os.environ["HIVE_DATABASE_URL"] = (
       "mysql+pymysql://USER:MYSQL_PASSWORD@USER.mysql.pythonanywhere-services.com/"
       "USER$hive?charset=utf8mb4"
   )
   # Optional: let the server advance main via the GitHub API (docs/INTEGRATION.md, GitHub mode)
   # os.environ["HIVE_GITHUB_TOKEN"] = "github_pat_..."

   from hive.server.wsgi import application  # noqa: E402
   ```

   To keep secrets out of the WSGI file, put them in `/home/USER/.hive-env` (readable only by
   you: `chmod 600`). Then load that file at the top of the WSGI file with the python-dotenv
   library (`pip install python-dotenv`, then `from dotenv import load_dotenv`), or by reading
   it manually.
4. Under **Security**, turn on **Force HTTPS**.
5. Click **Reload**.

The tables are created automatically on the first request.

### 4.6 Verify

```bash
curl https://USER.pythonanywhere.com/api/v1/health          # → {"ok": true}
```

- Open `https://USER.pythonanywhere.com/` and enter the admin token: the dashboard should load
  with fleet state `setup` (no goal given yet).
- On your computer: `hive admin login --server https://USER.pythonanywhere.com --token <token>`.

**If anything fails:** read the **error log**, linked on the Web tab
(`/var/log/USER.pythonanywhere.com.error.log`). §14 lists common causes.

### 4.7 Updating the code later

Get the new code, then run the server installer from the project folder in a Bash console:

```bash
cd ~/hive && git pull          # or upload and unzip the new version over ~/hive
./install-pa-server.sh         # or: bash install-pa-server.sh
```

It removes the previous installation and installs this folder fresh, without touching your
credentials or data:
- **Never modified:** the WSGI file (admin token, database URL and password), secret files it
  loads (such as `~/.hive-env`), the database (SQLite file or MySQL), and the dashboard
  archives. The WSGI file is copied to `~/hive-install-backups/<time>/` (readable only by
  you) and touched to reload the web app.
- **Reads the web app's settings from the WSGI file:**
  - which driver to install: `[mysql]` for MySQL, and `python-dotenv` if the file uses it;
  - where the database and archives are, to make sure neither is inside the virtualenv it is
    about to replace;
  - whether the admin token is set.

  It shows a summary first, with the database password masked and the token never shown,
  and asks before changing anything.
- **Removes stray copies** of Hive installed with `pip install` outside the virtualenv (in
  `~/.local`), which can be picked up by mistake. It asks first.
- **Swaps safely:**
  1. moves the old virtualenv aside;
  2. builds a new one with the same Python version;
  3. installs and self-tests it on a throwaway database (health, dashboard, admin API);
  4. reloads the web app;
  5. checks `https://<your site>/api/v1/health`.

  If any step fails, or the site answers with an error, the old virtualenv is put back and
  the web app reloaded with it. If the console can't reach the site to check it, the new
  install stays, the old one is kept as `<venv>.previous-<time>` until you've checked in a
  browser, and the script says so.

| Option | Effect |
|---|---|
| `--yes`, `-y` | Answer every question with yes (unattended). |
| `--venv PATH` | The web app's virtualenv, if not `~/.virtualenvs/hive` (Web tab → Virtualenv). |
| `--python 3.13` | Python for the new virtualenv (default: the old one's version). Change the Web tab's Python version to match first. |
| `--wsgi FILE` | The WSGI file, if several web apps import Hive. |
| `--url URL` | Site to health-check, if not derived from the WSGI file name. |
| `--extra "PKGS"` | Extra pip packages your WSGI file needs. |
| `--no-reload` | Install only; reload on the Web tab yourself. Keeps the old virtualenv. |
| `--keep-old` | Keep the previous virtualenv after a successful install. |

Back up the database first (`hive admin backup` from your computer, §10). See §9 for
database changes between versions.

**By hand instead:** `workon hive && pip install --upgrade "$HOME/hive[mysql]"`, then click
**Reload** on the Web tab.

### 4.8 Free (Beginner) plan with SQLite

The free plan has no MySQL for new accounts, so store Hive's data in a SQLite file in your home
directory. Steps 4.1, 4.2, 4.4, 4.6 and 4.7 are the same, with these differences:

1. **Install without the MySQL extra.** Get the code as in 4.1. Free accounts' restricted
   internet access still allows GitHub and PyPI (the Python package index `pip` installs
   from), so cloning and installing the dependencies work:
   ```bash
   mkvirtualenv --python=python3.12 hive
   pip install "$HOME/hive"
   ```
2. **Skip the database step (4.3).** The SQLite file and its folder are created on the first
   request.
3. **Use this WSGI file (4.5):**
   ```python
   import os

   os.environ["HIVE_ADMIN_TOKEN"] = "<the token from 4.4>"
   # An absolute path: four slashes after "sqlite:" (three + the leading "/" of the path)
   os.environ["HIVE_DATABASE_URL"] = "sqlite:////home/USER/hive-data/hive.db"
   # Rollback journal: required on PythonAnywhere's network filesystem.
   # Hive selects it automatically there; setting it explicitly makes sure.
   os.environ["HIVE_SQLITE_JOURNAL"] = "delete"

   from hive.server.wsgi import application  # noqa: E402
   ```
   Keep the database **outside** the code folder, so updating the code never touches it.
4. **Verify:** `curl https://USER.pythonanywhere.com/api/v1/health` should return
   `{"ok": true, "sqlite_journal": "delete"}`. If it says `"wal"`, the environment variable
   isn't set before the import line.

**Rules for SQLite on PythonAnywhere:**
- **Don't open the live `.db` file from another process** while the web app runs: no
  `sqlite3` in a console, no scheduled task reading it. Every access goes through the web app.
- **Back up through the server** instead. On your computer, run `hive admin backup` (or
  `-o file.db`). It asks the web app for a consistent copy made inside its own process and
  downloads it (§10). Run it daily, for example from your computer's scheduler.
- **Prune with the server stopped.** To run the SQL from §11:
  1. Disable the web app on the Web tab, which stops its worker.
  2. Run `sqlite3 /home/USER/hive-data/hive.db` in a console and execute the statements, then
     `VACUUM;`.
  3. Enable the web app again.
- **Renew the web app.** Free web apps must be renewed from the Web tab every few months, or
  they're disabled. The data stays.
- **Watch the disk quota** on the Files and Account pages. Delete old access-log archives and
  downloaded backups you no longer need.

**Moving to a paid plan later:** upgrade, create the MySQL database (4.3), and switch
`HIVE_DATABASE_URL` to it. Hive doesn't migrate data between databases automatically.
Transfer the data with a tool such as `sqlite3-to-mysql`, or start fresh: create the agents
again (`hive admin enroll`) and re-run `hive join` on each computer that runs them.

---

## 5. Deploying on a VPS (gunicorn + Caddy + systemd)

These steps are for a Debian/Ubuntu server with a domain name, here `hive.example.com`,
pointing at it. Three standard tools do the work:
- **gunicorn** runs the Python app (several processes, *workers*, in parallel);
- **Caddy** is the web server facing the internet: it handles HTTPS, getting and renewing the
  certificate (TLS) automatically, and passes requests on to gunicorn;
- **systemd**, Linux's service manager, starts gunicorn at boot and restarts it if it stops.

### 5.1 Install

```bash
sudo apt update && sudo apt install -y python3 python3-venv git caddy
sudo useradd --system --create-home --home-dir /srv/hive hive
sudo -u hive git clone https://github.com/<you>/<hive-repo>.git /srv/hive/app
sudo -u hive python3 -m venv /srv/hive/venv
sudo -u hive /srv/hive/venv/bin/pip install /srv/hive/app gunicorn
# For Postgres: sudo -u hive /srv/hive/venv/bin/pip install psycopg[binary]
```

### 5.2 Choose a database

| Option | `HIVE_DATABASE_URL` | Notes |
|---|---|---|
| SQLite (simplest) | `sqlite:////srv/hive/data/hive.db` | Fine on a single VPS with local disk. WAL mode (the default) works with several gunicorn workers. The folder is created automatically; it must be writable by `hive`. |
| Postgres | `postgresql+psycopg://hive:PASSWORD@localhost/hive` | Best for larger fleets or managed databases. |
| MySQL / MariaDB | `mysql+pymysql://hive:PASSWORD@localhost/hive?charset=utf8mb4` | Needs `pip install pymysql`. |

### 5.3 Secrets

`/etc/hive.env`, owned by root with mode `600`:

```bash
HIVE_ADMIN_TOKEN=<long random token>
HIVE_DATABASE_URL=sqlite:////srv/hive/data/hive.db
# HIVE_GITHUB_TOKEN=github_pat_...
```

### 5.4 systemd service

`/etc/systemd/system/hive.service`:

```ini
[Unit]
Description=Hive server
After=network.target

[Service]
User=hive
Group=hive
EnvironmentFile=/etc/hive.env
WorkingDirectory=/srv/hive
ExecStart=/srv/hive/venv/bin/gunicorn --workers 3 --bind 127.0.0.1:8000 \
          --access-logfile - hive.server.wsgi:application
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now hive
curl http://127.0.0.1:8000/api/v1/health
```

### 5.5 TLS with Caddy

Put this in `/etc/caddy/Caddyfile`, so Caddy passes (*reverse-proxies*) requests for your
domain to gunicorn:

```
hive.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Then reload Caddy:

```bash
sudo systemctl reload caddy
```

Caddy obtains and renews the certificate automatically. Verify with
`curl https://hive.example.com/api/v1/health`.

### 5.6 Container platforms

Fly.io, Railway, Render and similar: use a Python image, run `pip install . gunicorn`, and
start with:

```
gunicorn --workers 3 --bind 0.0.0.0:$PORT hive.server.wsgi:application
```

Set `HIVE_ADMIN_TOKEN` and `HIVE_DATABASE_URL` as platform secrets, and use the platform's
managed Postgres. Container disks are usually wiped on every restart, so don't rely on SQLite
there.

---

## 6. Running locally or on a local network

```bash
hive server --admin-token "<token>" --db sqlite:///hive.db --host 127.0.0.1 --port 8765
```

- **Agents on the same computer:** use `--server http://127.0.0.1:8765` with `hive join`.
- **Other computers on your local network:** start the server with `--host 0.0.0.0` and point
  them at `http://<your computer's IP address>:8765`.

This is Flask's simple built-in server and plain HTTP, so tokens travel unencrypted. Use it
only on networks you trust. For remote access without a public server, a tunnel service (such
as Tailscale or Cloudflare Tunnel, which give other computers a private, encrypted route to
yours) in front of it works.

---

## 7. Configuration reference

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `HIVE_ADMIN_TOKEN` | **yes** | – | The admin token. The app refuses to start without it. |
| `HIVE_DATABASE_URL` | no | `sqlite:///hive.db` (relative to the working directory) | Where to store data ([database URL](GLOSSARY.md#sqlite-mysql-postgres-database-url)). |
| `HIVE_GITHUB_TOKEN` | no | – | Enables [GitHub mode](INTEGRATION.md#github-mode-optional): the server moves `main` forward through GitHub's API. Also requires `github = "owner/repo"` in the `[repo]` section of the project's `hive.toml`, uploaded with `hive admin push-config`. Needs a fine-grained token with *Contents: read and write* on that repository. |
| `HIVE_ARCHIVE_DIR` | no | `hive-archives/` next to a SQLite database file, else `~/hive-archives` | Where the dashboard's archives are written ([Archives](MONITORING.md#archives)). The server process must be able to write there. On PythonAnywhere the files count toward your disk quota. |
| `HIVE_PROJECT_DATABASE_URL` | no | – | Database URL for new [projects](PROJECTS.md); `{slug}` in it is replaced by the project's name, e.g. `mysql+pymysql://USER:PW@HOST/USER$hive_{slug}` (§15). Without it, projects on a SQLite server get their own SQLite file, and on MySQL/Postgres each `project create` needs `--db-url`. |
| `HIVE_DATA_DIR` | no | next to a SQLite database file, else `~/hive-data` | Where project SQLite databases and project archives go: `projects/<project>/` (§15). |
| `HIVE_SQLITE_JOURNAL` | no | `wal`; `delete` when PythonAnywhere is detected | SQLite only. `wal` for local disks (fastest; readers and the writer don't block each other). `delete` (or `truncate`) for network filesystems such as PythonAnywhere's, where WAL doesn't work. `GET /api/v1/health` reports the active mode. |

**How Hive uses SQLite:**
- **Reads** use deferred transactions, so they never take the write lock.
- **Writes** take it up front (`BEGIN IMMEDIATE`) and are serialized inside each process.
- **Busy timeout:** 30 s. A request that still finds the database locked gets `503` with
  `Retry-After`, and Hive's clients retry it automatically (3 attempts with back-off).
- **Idle maintenance writes nothing:** the tick first checks, read-only, whether anything
  needs doing.

**Fleet behaviour** (whether work is reviewed, which tests must pass, [roles](ROLES.md), which
agent takes over from the orchestrator) is not server configuration. It comes from the repository's `hive.toml`, uploaded with
`hive admin push-config` or automatically by `hive start`.

**Built-in constants** (not configurable at present):

| Constant | Value |
|---|---|
| Agent considered offline | after 90 s without a heartbeat from its daemon |
| A standby takes over from an unreachable orchestrator | after 5 minutes |
| Maintenance tick (the periodic duties) | at most once per second per process |
| Message limits | 8 KB per message; 20 messages per minute per agent |
| Live output kept | 20,000 lines per agent |

---

## 8. After deploying: first-time checklist

1. `curl https://<server>/api/v1/health` returns `{"ok": true}`.
2. `hive admin login --server https://<server> --token <token>` on your computer.
3. `hive status` shows fleet `setup` and no agents.
4. In your repository: `hive init`, commit and push, then `hive admin push-config`
   ([Preparing the repository](REPOSITORY.md)).
5. Create the agents (`hive admin enroll …`) and add each to the computer that runs it
   (`hive join …`), as in [Enrolling agents and setting up machines](MACHINES.md).
6. `hive up` on every one of those computers. `hive status` should show every agent `online`
   within 20 s.
7. Give the fleet its goal: `hive start "<goal>"`.

---

## 9. Upgrading

1. **Update the code:** `git pull`, then reinstall:
   - **PythonAnywhere:** `./install-pa-server.sh` (§4.7);
   - **VPS:** `pip install --upgrade` as the `hive` user into `/srv/hive/venv`, then
     `sudo systemctl restart hive`.
2. **Update the agents' computers too.** They and the server should run the same Hive
   version: `uv tool install --force /path/to/hive` on each, then re-run `hive init` in the
   repository and commit the regenerated files in `.hive/agent/` (the *bundles*:
   [Preparing the repository](REPOSITORY.md)).
   - **This release requires it.** When an agent's work is merged into `main`, the daemon now
     needs a one-time permission from the server
     ([What `lease_release` does](INTEGRATION.md#what-lease_releasecommit_message-task_id-does)).
     A daemon from before this change still gets its work into `main`, but its leases stay
     held until it's upgraded.
   - **Git hooks refresh themselves.** `hive up` refreshes the git `pre-commit` hook (installed
     by `hive join`) in each agent's repository, so your own commits no longer need `hive` on
     PATH.
3. **Several projects (this release):** the server adds a `projects` table on start-up. An
   existing deployment becomes the `default` project, unchanged, so agents and admin logins
   keep working. Agents already set up on a computer (in its `~/.hive/machine.toml`) keep
   using the server address recorded there. Nothing to
   do unless you add projects (§15).
4. **Database changes:** on start-up the server creates any **missing tables**, but it does
   **not** alter existing ones.
   - Every version so far only adds tables, so upgrades need no manual steps.
   - Release notes will state when a version changes existing columns and give the SQL to run.
   - Take a backup before upgrading (§10).

---

## 10. Backups and restore

**What a backup preserves:** the database holds the fleet's coordination state:
- agents and their token hashes;
- tasks, messages, decisions, reports and status history;
- active leases;
- the uploaded `hive.toml`.

Code lives in git and is not in the database. Dashboard archives ([Archives](MONITORING.md#archives)) are separate
files in `HIVE_ARCHIVE_DIR`. Copy that folder too if you want to keep them, but they are
read-only history, not a way to restore the database.

**If the database is lost:**
1. Create the agents again (`hive admin enroll`); this gives new tokens.
2. Re-run `hive join` on each computer that runs agents.
3. Upload the settings (`hive admin push-config`) and give the fleet its goal again
   (`hive start`).

| Database | Backup | Restore |
|---|---|---|
| MySQL (PythonAnywhere) | `mysqldump -u USER -h USER.mysql.pythonanywhere-services.com --set-gtid-purged=OFF --no-tablespaces 'USER$hive' \| gzip > ~/backups/hive-$(date +%F).sql.gz` | `gunzip -c file.sql.gz \| mysql -u USER -h … 'USER$hive'` |
| SQLite (any host) | `hive admin backup -o hive-$(date +%F).db` from your computer. The server makes a consistent copy inside its own process and streams it to you. This is the **only** safe way on PythonAnywhere. | Stop the server, copy the file over the live database, start it again. |
| SQLite (local disk, VPS) | Alternatively `sqlite3 /srv/hive/data/hive.db ".backup '/srv/hive/backups/hive-$(date +%F).db'"` on the server. Safe while running on a local disk. | Same as above. |
| Postgres | `pg_dump -Fc hive > hive-$(date +%F).dump` | `pg_restore -d hive --clean hive-….dump` |

**Several projects (§15):** each project has its own database, so back each one up. For SQLite,
`hive admin backup --project NAME -o NAME-$(date +%F).db` copies that project's database only.
For MySQL/Postgres, dump each project's database the same way as the default one.

**Scheduling backups:**
- **PythonAnywhere:** a daily **Scheduled task** on the Tasks tab running the `mysqldump`
  line. The password goes in `~/.my.cnf` (`[client] password=…`, mode `600`). Delete old dumps
  periodically so they don't fill the disk quota.
- **VPS:** a cron entry or systemd timer.

---

## 11. Data retention and pruning

Hive doesn't delete coordination history on its own; only live output is capped. With the
growth rates in §3 that's fine for years on a 5 GB plan. Pruning keeps the database small and
the maintenance tick fast.

**Safe to delete:**
- old **events**: each daemon only reads events newer than the last one it saw;
- old **messages** and their receipts;
- **finished tasks** (`done`, `cancelled`) and their released, expired or revoked leases;
- old **status updates**, **reports** and **answered decisions**.

**Never delete** open tasks, active leases, or open or queued decisions.

Example in MySQL syntax. For SQLite replace `UNIX_TIMESTAMP()` with
`strftime('%s','now')`; for Postgres with `EXTRACT(EPOCH FROM now())`. It keeps the last 30
days. Back up first.

```sql
-- 30 days in seconds; timestamps are Unix epoch seconds
DELETE FROM events      WHERE ts < UNIX_TIMESTAMP() - 30*86400;
DELETE FROM receipts    WHERE message_id IN (SELECT id FROM (SELECT id FROM messages
                          WHERE ts < UNIX_TIMESTAMP() - 30*86400) AS old);
DELETE FROM messages    WHERE ts < UNIX_TIMESTAMP() - 30*86400;
DELETE FROM status_feed WHERE ts < UNIX_TIMESTAMP() - 90*86400;
DELETE FROM reports     WHERE created < UNIX_TIMESTAMP() - 90*86400;
DELETE FROM decisions   WHERE state = 'answered' AND answered_at < UNIX_TIMESTAMP() - 90*86400;
DELETE FROM leases      WHERE state <> 'active' AND acquired < UNIX_TIMESTAMP() - 30*86400;
DELETE FROM tasks       WHERE state IN ('done','cancelled') AND updated < UNIX_TIMESTAMP() - 30*86400;
```

Run it in the MySQL console (PythonAnywhere: Databases tab → open a console on `USER$hive`),
or as a monthly scheduled task.

**The easier way, between runs** (a *run* being everything from one goal to the next): the
dashboard's ⋯ menu → *Archives…* → *Archive & clear…* saves
a finished run's history to a compressed file in `HIVE_ARCHIVE_DIR` and empties those tables
in one step, without touching agents or config ([Archives](MONITORING.md#archives)). Archives are gzip-compressed
JSON; the dialog shows each one's size. Delete old archives from the same dialog, or download
them first.

**Ids keep counting after a clear.** Daemons remember the number of the last event and message
they saw, so the server must never hand out an old number again:
- **Postgres and MySQL 8:** their sequences never go back.
- **SQLite:** the cleared tables use `AUTOINCREMENT`. Databases created by older Hive versions
  are converted on their first clear.
- **MySQL 5.7 and older:** the counter resets to the highest remaining id when the database
  server restarts, so restart MySQL only after the next run has created some rows. MySQL 8
  stores the counter and doesn't have this problem.

**Reclaiming disk space:**

| Database | Command |
|---|---|
| MySQL InnoDB | `OPTIMIZE TABLE events, messages;` (otherwise freed space is reused, not returned) |
| SQLite | `VACUUM;` |
| Postgres | autovacuum handles it |

Pruning finished tasks also removes them from what the orchestrator (the agent that
coordinates the fleet) sees when it lists tasks. Keep the window longer than any plan the
orchestrator might still be referring to.

---

## 12. Monitoring

| What | How |
|---|---|
| Server alive | `GET /api/v1/health` → `{"ok": true}`. Point any uptime monitor at it. |
| Errors | PythonAnywhere: the error log on the Web tab. VPS: `journalctl -u hive -f`. |
| Fleet health | `hive status` and the dashboard: offline agents, agents whose account has used up its allowance (*out of quota*), a paused fleet waiting for your answer. |
| Database size | MySQL: `SELECT table_name, ROUND((data_length+index_length)/1e6,1) AS mb FROM information_schema.tables WHERE table_schema = 'USER$hive' ORDER BY mb DESC;` SQLite: the file size. |
| Request volume | Access log line counts (≈ 0.3 million/day for 7 agents is normal; see §3.4). |

---

## 13. Security hardening

- **HTTPS only.** Turn on PythonAnywhere's *Force HTTPS*, or use Caddy. Tokens work like
  passwords sent with every request: anyone who sees one can use it.
- **Admin token:**
  - long and random (§4.4);
  - kept in a password manager and in `~/.hive/admin.toml` only on machines where you run
    admin commands;
  - never on computers that only run agents, unless you also work there.
- **Rotating tokens:**
  - **Admin:** change `HIVE_ADMIN_TOKEN` and reload; then `hive admin login` again.
  - **Agent:** have the server issue a new token by sending `POST /api/v1/agents/<name>/token`
    with the admin token ([CLI.md §13](CLI.md#13-common-recipes) has the `curl` command), then
    re-run `hive join` with it, or update that agent's `token` in `~/.hive/machine.toml` on
    the computer that runs it.
- **Live output contains whatever agents print**, including file contents they read. It's
  readable only with the admin token, but it sits in your database: keep real credentials out
  of the repository and prune streams if needed (`DELETE FROM streams WHERE agent = '…';`).
- **Database credentials** live only in the WSGI file or `/etc/hive.env`, with restrictive
  permissions.
- **GitHub mode:** give `HIVE_GITHUB_TOKEN` access to that single repository only. Protect
  `main` so only this token, or you, can push to it.

---

## 14. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Web app shows "Something went wrong" / 500 | Read the error log. `RuntimeError: HIVE_ADMIN_TOKEN must be set` means the environment variable isn't set before the `from hive.server.wsgi import application` line. |
| `ModuleNotFoundError: hive` or `pymysql` | The web app's virtualenv setting doesn't point at the env where you installed `hive[mysql]` (§4.2, §4.5). |
| `Access denied for user` / `Unknown database` | Check the MySQL password (separate from your login), the `USER$hive` database name (the `$` is literal), and the host `USER.mysql.pythonanywhere-services.com`. URL-encode special characters in the password. |
| `MySQL server has gone away` | Idle connections are recycled after 280 s automatically. If it persists, reload the web app. |
| Agents log `503 db_busy`, or requests are slow (SQLite) | The database is briefly locked. Clients retry automatically. If it's frequent: make sure nothing else opens the `.db` file (consoles, scheduled tasks), and check `/api/v1/health` shows `"sqlite_journal": "delete"` on PythonAnywhere. |
| SQLite stays locked permanently on PythonAnywhere | A stale lock on the network filesystem. Reload the web app. If that doesn't help, contact PythonAnywhere support, or restore the latest `hive admin backup` to a new file path and point `HIVE_DATABASE_URL` at it. |
| `disk I/O error` / `database disk image is malformed` (SQLite) | Usually WAL on a network filesystem: set `HIVE_SQLITE_JOURNAL=delete`. Restore from a backup if the file is damaged. |
| Agents get `401 bad_token` | Wrong agent token in `~/.hive/machine.toml`, or one that was since replaced, or the server's database was replaced. |
| `hive admin login` fails with 403 | The token isn't the admin token configured on the server. |
| Dashboard loads but stays empty / asks for the token again | The admin token was mistyped; clear it via the browser's site data and re-enter it. |
| Requests slow down over months | The maintenance tick reads all tasks (§3.4); prune finished tasks (§11). |
| Disk quota warnings on PythonAnywhere | Database growth (§3), old `mysqldump` files, or rotated access logs. Prune (§11) and delete old archives. |

---

## 15. Hosting several projects

One server can run several projects. Each one is a separate repository and fleet with its own
database, served at `https://<server>/p/<project>/`. [PROJECTS.md](PROJECTS.md) covers using them; this
section covers the server side.

**What changes on the server:**
- **Nothing to deploy.** The same WSGI app (`hive.server.wsgi:application`) serves every
  project. The existing database is the `default` project and holds the list of projects.
- **No background processes.** Each project's periodic duties (§1) run on its own requests,
  as before.
- **Connections:** each project that has had a request in the last 10 minutes keeps a small
  set (*pool*) of open database connections, at most 6. An idle project's pool is closed and reopens on
  demand. If your host limits connections per user, budget about 6 per active project, plus
  the default project's.

**Adding a project on SQLite** (local, VPS, or the PythonAnywhere free plan): nothing to
prepare. The project's database is created at `projects/<project>/hive.db` next to the
server's database, or under `HIVE_DATA_DIR`. It uses the same journal mode as the server's.

```sh
hive admin project create acme --name "Acme Corp"
```

**Adding a project on MySQL (PythonAnywhere):**
1. **Create the database** on the *Databases* tab, named for example `hive_acme`.
   PythonAnywhere names it `USER$hive_acme`.
2. **Tell the server** where projects' databases are. Do this once, in the WSGI file next
   to `HIVE_DATABASE_URL`:
   ```python
   os.environ["HIVE_PROJECT_DATABASE_URL"] = \
       "mysql+pymysql://USER:PASSWORD@USER.mysql.pythonanywhere-services.com/USER$hive_{slug}"
   ```
   Then reload the web app. Hive fills in `{slug}` with the project's name.
3. **Create the project** from your computer:
   `hive admin project create acme`. Hive opens the database and creates its tables. If the
   database doesn't exist or can't be reached, the project is not created and the error says
   why.

Without the template, pass the URL each time:
`hive admin project create acme --db-url "mysql+pymysql://…/USER$hive_acme"`. Postgres works
the same way, with one database per project.

**GitHub mode per project:** `HIVE_GITHUB_TOKEN` is for the default project only. Give each
other project a fine-grained token limited to its own repository:
1. Add it to the server's environment (the WSGI file, or the systemd `EnvironmentFile`), e.g.
   `HIVE_GITHUB_TOKEN_ACME`.
2. Reload the server.
3. Run `hive admin project set acme --github-token-env HIVE_GITHUB_TOKEN_ACME`.

**Admin tokens:** `project create` prints the project's own admin token once. Give it to
whoever runs that project; it can't reach other projects or manage projects.
`hive admin project rotate acme` replaces it. Your `HIVE_ADMIN_TOKEN` keeps working in every
project.

**Archiving:** `hive admin project set acme --archive` stops serving the project (its URLs
answer `404`) but keeps its database and archives. `--unarchive` brings it back.
