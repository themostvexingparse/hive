"""Simulate Hive server storage growth and per-request cost using the real service layer.

Each simulated "busy day" (7 agents, review on) contains 30 tasks with 1.5 KB specs, 3 progress
notes each, a lease, review and integration per task, a 2 KB report per task, 150 messages of
400 chars, 40 status updates with 1.5 KB bodies, 5 report requests and one freeze decision.
Afterwards one agent's live output is filled to the 20,000-line cap. Edit the constants to
model your own fleet. See docs/DEPLOYMENT.md §3.

Usage: uv run python scripts/simulate_load.py <workdir> [days]
"""
import random
import sqlite3
import string
import sys
import time
from pathlib import Path

from hive.server.service import ADMIN, Actor, create_hive

work = Path(sys.argv[1])
days = int(sys.argv[2]) if len(sys.argv) > 2 else 1
work.mkdir(parents=True, exist_ok=True)
dbfile = work / "sim.db"
if dbfile.exists():
    dbfile.unlink()
rnd = random.Random(1)


def text(n):
    words = []
    while sum(len(w) + 1 for w in words) < n:
        words.append("".join(rnd.choices(string.ascii_lowercase, k=rnd.randint(2, 9))))
    return " ".join(words)[:n]


hive = create_hive(f"sqlite:///{dbfile}", tick_interval=0)
hive.set_config(ADMIN, {"integration": {"review": "reviewer"}})
names = {"orch": ["orchestrator"], "rev": ["reviewer"], "tst": ["tester"],
         **{f"p{i}": ["programmer"] for i in range(4)}}
for n, r in names.items():
    hive.enroll(ADMIN, n, "claude", r)
A = {n: Actor("agent", n, r) for n, r in names.items()}
for a in A.values():
    hive.heartbeat(a)
hive.start_fleet(ADMIN, text(400))

# --- one "busy day" of coordination (per day) ---
TASKS, MESSAGES, STATUS, REPORT_REQ = 30, 150, 40, 5
seq_sha = 0
for day in range(days):
    for a in A.values():
        hive.heartbeat(a)
    created = [hive.create_task(A["orch"], text(60), spec=text(1500), kind="code",
                                path_hints=[f"src/m{day}_{i}/**"]) for i in range(TASKS)]
    if day == 0:
        d = hive.request_decision(A["orch"], "start_approval", "Approve?", context=text(3000))
        hive.answer_decision(ADMIN, d["id"], "approve")
    hive.tick(force=True)
    for t in created:
        t = hive.get_task(t["id"])
        who = A[t["assignee"]] if t["assignee"] else A["p0"]
        if not t["assignee"]:
            hive.assign_task(A["orch"], t["id"], who.name)
        for _ in range(3):
            hive.update_task(who, t["id"], "in_progress", text(200))
        lease = hive.acquire_lease(who, t["path_hints"])
        seq_sha += 1
        sha = f"{seq_sha:040x}"
        fences = {str(lease["id"]): lease["fencing"]}
        hive.integrate(who, sha, "0" * 40, fences, t["id"], [f"src/f{i}.py" for i in range(5)])
        hive.review_verdict(A["rev"], t["id"], True, text(300))
        hive.integrate(who, sha, "0" * 40, fences, t["id"], [f"src/f{i}.py" for i in range(5)])
        hive.confirm_integration(who, sha, fences, t["id"], [f"src/f{i}.py" for i in range(5)])
        hive.submit_report(who, text(120), text(2000), t["id"])
    for i in range(MESSAGES):
        # Posted as admin to bypass the per-agent rate limit; row sizes are identical.
        hive.post_message(ADMIN, text(400),
                          channel=rnd.choice(["#general", "#user", f"#task-{created[0]['id']}"]))
    for _ in range(STATUS):
        hive.publish_status(A["orch"], text(150), text(1500))
    for _ in range(REPORT_REQ):
        r = hive.request_report(A["orch"], "p1", text(300))
        hive.submit_report(A["p1"], text(120), text(2000), request_id=r["id"])
    d = hive.request_decision(A["orch"], "stuck", text(300), context=text(1500))
    hive.answer_decision(ADMIN, d["id"], text(100))

coord_bytes = dbfile.stat().st_size


# --- live output: fill one agent to the 20,000-line cap with realistic line lengths ---
def stream_line():
    k = rnd.random()
    if k < 0.45:
        return "  " + text(rnd.randint(30, 140))           # prose
    if k < 0.75:
        return "→ Edit " + text(rnd.randint(40, 153))       # tool call (capped ~160)
    if k < 0.97:
        return "← " + text(rnd.randint(20, 198))            # tool result (capped ~200)
    return "· turn done (12 turns)"


lines = [stream_line() for _ in range(25000)]
avg_line = sum(len(l) for l in lines) / len(lines)
for i in range(0, len(lines), 500):
    hive.append_stream(A["p0"], "run", lines[i:i + 500])
con = sqlite3.connect(dbfile)
con.execute("VACUUM")
counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
          for t in ("events", "messages", "tasks", "reports", "status_feed", "streams",
                    "leases", "decisions")}
try:
    per_table = dict(con.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name").fetchall())
except sqlite3.OperationalError:
    per_table = {}
con.close()
total = dbfile.stat().st_size

# --- per-request cost of the hot path (agent poll) with this much history ---
from hive.server.app import create_app
app = create_app(hive, admin_token="x")
tok = hive.rotate_token(ADMIN, "p2")["token"]
c = app.test_client()
h = {"Authorization": f"Bearer {tok}"}
head = c.get("/api/v1/events?since=0&limit=1", headers=h).get_json()["head"]
hive.tick_interval = 1.0
t0 = time.perf_counter()
N = 300
for _ in range(N):
    c.get(f"/api/v1/events?since={head}", headers=h)
poll_ms = (time.perf_counter() - t0) / N * 1000
t0 = time.perf_counter()
for _ in range(50):
    hive.tick(force=True)
tick_ms = (time.perf_counter() - t0) / 50 * 1000

print(f"days simulated: {days}")
print(f"row counts: {counts}")
print(f"avg stream line: {avg_line:.0f} chars")
print(f"db size after coordination only: {coord_bytes/1e6:.2f} MB")
print(f"db size total (vacuumed, incl. 1 agent at stream cap): {total/1e6:.2f} MB")
for name, size in sorted(per_table.items(), key=lambda x: -x[1])[:12]:
    print(f"  {name:<40} {size/1e6:8.2f} MB")
print(f"poll request: {poll_ms:.2f} ms  |  forced tick: {tick_ms:.2f} ms")
