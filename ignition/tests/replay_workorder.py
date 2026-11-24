#!/usr/bin/env python3
"""Offline replay of the Press 1 sim through mes.engine.step() and mes.workorder.handle() (WO-001).

Builds the 2 s sample stream from docs/sim/fourzero_printing_press1.json, feeds two full loops
(from J101 Setup) through the engine and the work-order rules against a FakeDb holding the
DB-002 seed, and checks attribution, statuses and meters per run, then again with the meters one sample late
(BUG-002, rule 8). Then runs nine edge cases.
Never calls tick() or on_tick().

Run from the repo root: python3 -I ignition/tests/replay_workorder.py
Exits non-zero on any failed assertion. Standard library only.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from simreplay import ENGINE, T0, WORKORDER, FakeDb, load, load_sim, loop_samples

failures = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        failures.append(msg)


eng = load(ENGINE, "mes.engine")
wo = load(WORKORDER, "mes.workorder")
step = eng["step"]
handle = wo["handle"]


def engine_store():
    eng["system"].util.globals.clear()
    return eng["_store"]()


def wo_store():
    return wo["_new_store"]()


def drive(est, wst, db, samples, logs=None, catch=False):
    """engine.step() then workorder.handle() per sample, as engine.tick() dispatches."""
    for s in samples:
        n = est["samples"]
        evs = step(est, s)
        if est["samples"] == n:
            continue
        try:
            lines = handle(wst, dict(est["last"]), [dict(e) for e in evs], db)
        except Exception as e:
            if not catch:
                raise
            lines = ["handle failed: %s" % e]
        if logs is not None:
            logs.extend(lines)


# ---------- two full loops ----------

sim = load_sim()
counters = {}
loop1, t = loop_samples(sim, counters, T0)
loop2, t = loop_samples(sim, counters, t)
samples = loop1 + loop2
print("Sim: %s, two loops, %d samples, first phase %s" % (sim["name"], len(samples), samples[0]["_phase"]))

est, wst, db = engine_store(), wo_store(), FakeDb()
logs = []
# Record the engine events per sample time to check the timestamps against.
events_at = {}
for s in samples:
    n = est["samples"]
    evs = step(est, s)
    if est["samples"] == n:
        continue
    for e in evs:
        events_at.setdefault(e["type"], []).append(e["t"])
    logs += handle(wst, dict(est["last"]), [dict(e) for e in evs], db)

print("\nLog:")
for line in logs:
    print("  " + line)

print("\nRuns:")
for r in db.runs:
    print("  run %(run_id)d %(work_order_id)s product %(product_code)d good %(good_meters)d reject %(reject_meters)d "
          "start %(start_ts)d end %(end_ts)s reset %(counter_reset_seen)s" % r)

print("\nTwo loops:")
ids = [r["work_order_id"] for r in db.runs]
check(ids == ["WO-%d" % i for i in range(1001, 1010)], "runs attributed in order WO-1001 .. WO-1009 (%s)" % ids)
check(ids[0] == "WO-1001" and "WO-1006" in ids and ids.index("WO-1006") == 5,
      "tie-break: WO-1001 chosen over WO-1006 for the first 101 run")
check([r["good_meters"] for r in db.runs] == [600, 375, 450, 525, 365, 600, 375, 450, 525],
      "good meters per run 600, 375, 450, 525, 365, 600, 375, 450, 525")
check([r["reject_meters"] for r in db.runs] == [18, 18, 18, 18, 83, 18, 18, 18, 18],
      "reject meters per run 18, 18, 18, 18, 83, 18, 18, 18, 18")
check(all(r["end_ts"] is not None for r in db.runs), "all nine runs closed")
check([o["status"] for o in db.orders.values()] == ["Complete"] * 9 + ["Planned"],
      "WO-1001 .. WO-1009 Complete, WO-1010 Planned")
check(db.orders["WO-1010"]["started_at"] is None and "WO-1010" not in ids, "WO-1010 untouched, no run row")
rs, pc = events_at["run_start"], events_at["product_change"]
check(len(rs) == 10 and len(pc) == 9, "engine: 10 run_start, 9 product_change")
check([r["start_ts"] for r in db.runs] == rs[:9], "run start_ts = the run_start sample time")
check([db.orders["WO-%d" % i]["started_at"] for i in range(1001, 1010)] == rs[:9],
      "started_at = the run_start sample time (Running on that sample)")
check([db.orders["WO-%d" % i]["completed_at"] for i in range(1001, 1010)] == pc,
      "completed_at = the next product_change sample time (WO-1009 at the 105 Setup)")
check([r["end_ts"] for r in db.runs] == pc, "run end_ts = the product_change sample time")
first_setup = [s["t"] for s in loop2 if s["_phase"] == "J105 Setup" and s["_first"]]
check(db.orders["WO-1009"]["completed_at"] == first_setup[0], "WO-1009 completed on the first loop-2 J105 Setup sample")
open_run = wst["run"]
check(open_run is not None and not open_run["attributed"] and open_run["product"] == 105
      and open_run["good"] == 365 and open_run["reject"] == 83,
      "second-loop 105 run unattributed, in memory good 365 reject 83 (%s)" % (
          open_run and (open_run["good"], open_run["reject"]),))
check(any(line == "unattributed run product=105" for line in logs), "log: unattributed run product=105")
check(any(line.startswith("complete WO-1001 good=600 planned=600 diff=0 reject=18") for line in logs),
      "log: complete WO-1001 good=600 planned=600 diff=0 reject=18")
r1_end = [s for s in loop1 if s["t"] < db.runs[0]["end_ts"]][-1]
check(r1_end["good"] - db.runs[0]["start_good_counter"] == 600,
      "start_good_counter is the counter before the run: counter at close - start = 600")
check(not any(line.startswith("late delta") for line in logs), "no lag: no 'late delta' lines")


# ---------- two full loops, meters one sample late (BUG-002, rule 8) ----------

def lagged(samples):
    """Sample n carries the good / reject values of sample n - 1 (state, t, product on time)."""
    out = [dict(samples[0])]
    for prev, s in zip(samples, samples[1:]):
        s = dict(s)
        s["good"], s["reject"] = prev["good"], prev["reject"]
        out.append(s)
    return out


print("\nTwo loops, meters one sample late:")
est, wst, db, lag_logs = engine_store(), wo_store(), FakeDb(), []
drive(est, wst, db, lagged(samples), lag_logs)
for line in lag_logs:
    if line.startswith("late delta"):
        print("  " + line)
check([r["work_order_id"] for r in db.runs] == ["WO-%d" % i for i in range(1001, 1010)],
      "lag: runs attributed in order WO-1001 .. WO-1009")
check([r["good_meters"] for r in db.runs] == [600, 375, 450, 525, 365, 600, 375, 450, 525],
      "lag: good meters per run 600, 375, 450, 525, 365, 600, 375, 450, 525 (%s)" % [r["good_meters"] for r in db.runs])
check([r["reject_meters"] for r in db.runs] == [18, 18, 18, 18, 83, 18, 18, 18, 18],
      "lag: reject meters per run 18, 18, 18, 18, 83, 18, 18, 18, 18 (%s)" % [r["reject_meters"] for r in db.runs])
check([r["end_ts"] for r in db.runs] == pc and [db.orders["WO-%d" % i]["completed_at"] for i in range(1001, 1010)] == pc,
      "lag: end_ts and completed_at unchanged by late deltas")
open_run = wst["run"]
check(open_run is not None and not open_run["attributed"] and open_run["good"] == 365 and open_run["reject"] == 83,
      "lag: second-loop 105 run unattributed, in memory good 365 reject 83 (%s)" % (
          open_run and (open_run["good"], open_run["reject"]),))
check(any(line.startswith("late delta") for line in lag_logs), "lag: 'late delta' lines logged")


# ---------- edge cases ----------

def sample(ts, state=1, fault=0, product=101, good=0, reject=0):
    return {"t": T0 + ts * 1000, "state": state, "fault": fault, "product": product, "lanes": 4,
            "good": good, "reject": reject, "conn": True}


def job(ts, product, good, reject, step_g=8, n_setup=2, n_run=5):
    """Setup then Running samples from ts (s); returns samples, next ts, counters."""
    out = []
    for _ in range(n_setup):
        out.append(sample(ts, state=2, product=product, good=good, reject=reject))
        ts += 2
    for _ in range(n_run):
        good += step_g
        out.append(sample(ts, product=product, good=good, reject=reject))
        ts += 2
    return out, ts, good, reject


print("\nEdge cases:")

# 1. Sim restart mid-run: meters drop to 0
est, wst, db = engine_store(), wo_store(), FakeDb()
s1, ts, g, r = job(0, 101, 1000, 50)
s1 += [sample(ts, good=0, reject=0), sample(ts + 2, good=8, reject=0), sample(ts + 4, good=16, reject=2)]
seen = []
for s in s1:
    drive(est, wst, db, [s])
    if db.runs:
        seen.append((db.runs[0]["good_meters"], db.runs[0]["reject_meters"]))
run = db.runs[0]
check(run["counter_reset_seen"] is True, "sim restart: counter_reset_seen true")
check(all(b[0] >= a[0] and b[1] >= a[1] for a, b in zip(seen, seen[1:])) and min(min(x) for x in seen) >= 0,
      "sim restart: totals never decrease, never negative (%s)" % seen)
check(run["good_meters"] == 5 * 8 + 16 and run["reject_meters"] == 2,
      "sim restart: totals good %d reject %d (56, 2)" % (run["good_meters"], run["reject_meters"]))

# 2. Resume, same product: open run in the db, empty store
db = FakeDb()
rid = db.start_run("WO-1001", 101, T0 - 60_000, 0, 0)
db.update_run(rid, 300, 9, False)
est, wst, logs = engine_store(), wo_store(), []
drive(est, wst, db, [sample(0, good=300), sample(2, good=308), sample(4, good=316, reject=2)], logs)
check(len(db.runs) == 1 and wst["run"]["run_id"] == rid and wst["run"]["work_order_id"] == "WO-1001",
      "resume same product: resumed run %d, no new row" % rid)
check(db.runs[0]["good_meters"] == 316 and db.runs[0]["reject_meters"] == 11,
      "resume same product: totals continue from stored 300/9 -> %d/%d" % (db.runs[0]["good_meters"], db.runs[0]["reject_meters"]))
check(any(line.startswith("resumed run %d" % rid) for line in logs), "resume same product: log 'resumed run %d ...'" % rid)

# 3. Resume, different product: old run closed, its order Complete
db = FakeDb()
rid = db.start_run("WO-1001", 101, T0 - 60_000, 0, 0)
db.update_run(rid, 300, 9, False)
est, wst, logs = engine_store(), wo_store(), []
drive(est, wst, db, [sample(0, state=2, product=102), sample(2, state=2, product=102)], logs)
check(db.runs[0]["end_ts"] == T0 and db.runs[0]["good_meters"] == 300 and db.runs[0]["reject_meters"] == 9,
      "resume different product: old run closed at the current sample with stored totals")
check(db.orders["WO-1001"]["status"] == "Complete" and db.orders["WO-1001"]["completed_at"] == T0,
      "resume different product: WO-1001 Complete")
check(wst["run"] is None and len(db.runs) == 1, "resume different product: no run open")

# 4. Press Running at startup, no open run: nothing until the next run_start
db = FakeDb()
est, wst, logs = engine_store(), wo_store(), []
drive(est, wst, db, [sample(i * 2, good=100 + 8 * i) for i in range(5)], logs)
check(db.writes == 0 and wst["run"] is None, "running at startup: nothing attributed, no db write")
check("press running at startup, no open run: waiting for next run_start" in logs, "running at startup: log line")
s4, _, _, _ = job(10, 102, 140, 0, step_g=5)
drive(est, wst, db, s4, logs)
check([r["work_order_id"] for r in db.runs] == ["WO-1002"] and db.runs[0]["good_meters"] == 25
      and db.orders["WO-1001"]["status"] == "Released",
      "running at startup: next run_start attributes WO-1002 (25 m); WO-1001 stays Released")

# 5. No Released order for the product: unattributed, no db write, On Hold untouched
db = FakeDb()
db.orders["WO-1005"]["status"] = "On Hold"
est, wst, logs = engine_store(), wo_store(), []
s5, _, _, _ = job(0, 105, 0, 0, step_g=5)
drive(est, wst, db, s5, logs)
check(db.writes == 0 and not db.runs, "no Released order: no db write, no run row")
check(wst["run"] is not None and not wst["run"]["attributed"] and wst["run"]["good"] == 25,
      "no Released order: unattributed run in memory, good 25")
check(db.orders["WO-1005"]["status"] == "On Hold" and db.orders["WO-1010"]["status"] == "Planned",
      "no Released order: On Hold and Planned orders untouched")
check("unattributed run product=105" in logs, "no Released order: log 'unattributed run product=105'")

# 6. A failed update_run heals on the next tick
db = FakeDb()
est, wst, logs = engine_store(), wo_store(), []
s6, ts, g, r = job(0, 101, 0, 0)
drive(est, wst, db, s6[:4], logs)          # setup x2, running x2 -> 16
db.fail_updates = 1
drive(est, wst, db, s6[4:5], logs, catch=True)  # -> 24 in memory, write fails
stale = db.runs[0]["good_meters"]
drive(est, wst, db, s6[5:], logs, catch=True)   # -> 40, written
check(stale == 16 and any(line.startswith("handle failed") for line in logs),
      "failed update_run: db still at 16 after the failed tick")
check(db.runs[0]["good_meters"] == 40 == wst["run"]["good"],
      "failed update_run: next tick writes the absolute total 40")

# 7. Rule 8: a late delta after a gap is not credited to the closed run
db = FakeDb()
est, wst, logs = engine_store(), wo_store(), []
s7, ts, g, r = job(0, 101, 0, 0)                        # run 1: 40 m
s7 += [sample(ts, state=2, product=102, good=g, reject=r + 2),           # late +2 -> run 1
       sample(ts + 30, state=2, product=102, good=g, reject=r + 4)]      # after a 30 s gap: not credited
drive(est, wst, db, s7, logs)
check(db.runs[0]["reject_meters"] == 2 and db.runs[0]["good_meters"] == 40,
      "late delta: credited on the product_change sample, not after a gap (%d/%d)" % (
          db.runs[0]["good_meters"], db.runs[0]["reject_meters"]))
check(sum(line.startswith("late delta") for line in logs) == 1 and wst["last_closed"] is None,
      "late delta after a gap: one 'late delta' line, last_closed cleared")

# 8. Rule 8: a delta on or after the next run_start goes to the new run, never the old one
db = FakeDb()
est, wst, logs = engine_store(), wo_store(), []
s8, ts, g, r = job(0, 101, 0, 0)                        # run 1: 40 m
s8 += [sample(ts, state=2, product=102, good=g + 8, reject=r),           # late +8 good -> run 1
       sample(ts + 2, state=2, product=102, good=g + 8, reject=r),
       sample(ts + 4, product=102, good=g + 13, reject=r + 2),           # run_start, delta -> run 2
       sample(ts + 6, product=102, good=g + 18, reject=r + 2)]
drive(est, wst, db, s8, logs)
check(db.runs[0]["good_meters"] == 48 and db.runs[0]["reject_meters"] == 0 and db.runs[0]["end_ts"] == T0 + ts * 1000,
      "late delta: run 1 good 40 + 8 late = 48, end_ts unchanged (%d)" % db.runs[0]["good_meters"])
check(db.runs[1]["good_meters"] == 10 and db.runs[1]["reject_meters"] == 2,
      "after run_start: deltas go to run 2 (%d/%d)" % (db.runs[1]["good_meters"], db.runs[1]["reject_meters"]))
check(wst["last_closed"] is None and "late delta good=+8 reject=+0 credited to run 1 (WO-1001)" in logs,
      "after run_start: last_closed cleared; log 'late delta good=+8 reject=+0 credited to run 1 (WO-1001)'")

# 9. Rule 8, unattributed: the late delta shows on the run in st['unattributed']
db = FakeDb()
db.orders["WO-1005"]["status"] = "On Hold"
est, wst, logs = engine_store(), wo_store(), []
s9, ts, g, r = job(0, 105, 0, 0, step_g=5)
s9 += [sample(ts, state=2, product=101, good=g, reject=r + 3)]
drive(est, wst, db, s9, logs)
check(wst["unattributed"] and wst["unattributed"][-1]["reject"] == 3 and db.writes == 0
      and "late delta good=+0 reject=+3 credited to unattributed run product=105" in logs,
      "late delta, unattributed: credited in memory, no db write")

print("\n%s: %d failure(s)" % ("FAILED" if failures else "PASSED", len(failures)))
sys.exit(1 if failures else 0)
