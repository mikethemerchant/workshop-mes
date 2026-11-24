#!/usr/bin/env python3
"""Offline test of the WO-003 manual actions in mes.workorder: create, release, hold, cancel, close, reassign.

Runs the action functions (do_*, _reassign, apply_pending) against the FakeDb of simreplay.py (DB-002 seed),
and replays the sim through mes.engine.step(), apply_pending() and handle() the way on_tick() does, to check
the attribution scenarios of the spec. Never calls tick() or on_tick().

Run from the repo root: python3 -I ignition/tests/test_workorder_actions.py
Exits non-zero on any failed assertion. Standard library only.
"""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from simreplay import ENGINE, SEED_RELEASED, T0, WORKORDER, FakeDb, load, load_sim, loop_samples

failures = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        failures.append(msg)


eng = load(ENGINE, "mes.engine")
wo = load(WORKORDER, "mes.workorder")
step = eng["step"]
handle, apply_pending = wo["handle"], wo["apply_pending"]
STATUSES = ["Planned", "Released", "Running", "Complete", "Closed", "On Hold", "Cancelled"]


def engine_store():
    eng["system"].util.globals.clear()
    return eng["_store"]()


def act(db, action, wid, reason=None):
    return db.atomic(lambda d: wo["do_transition"](d, action, wid, reason))


def create(db, product=101, planned=600, due=T0 + 7_200_000, priority="Normal", notes=None):
    return db.atomic(lambda d: wo["do_create"](d, product, planned, due, priority, notes))


def reassign(st, db, ref, target, by="test"):
    return wo["_reassign"](st, db, dict(ref), target, by)


def snapshot(db):
    return copy.deepcopy((db.orders, db.runs))


def drive(est, wst, db, samples, logs, until=None):
    """As on_tick: apply_pending() then handle() per accepted sample. Stops before the sample until(s) is true."""
    for i, s in enumerate(samples):
        if until is not None and until(s):
            return samples[i:]
        n = est["samples"]
        evs = step(est, s)
        if est["samples"] == n:
            continue
        logs += apply_pending(wst, db)
        logs += handle(wst, dict(est["last"]), [dict(e) for e in evs], db)
    return []


def first(phase, loop):
    return lambda s: s["_phase"] == phase and s["_first"] and s["t"] >= loop[0]["t"]


sim = load_sim()
counters = {}
loop1, t = loop_samples(sim, counters, T0)
loop2, t = loop_samples(sim, counters, t)
loop3, t = loop_samples(sim, counters, t)
print("Sim: %s, %d phases per loop" % (sim["name"], len(sim["phases"])))


# ---------- transition table ----------

print("\nTransition table:")
allowed = {"release": ("Planned", "On Hold"), "hold": ("Planned", "Released"),
           "cancel": ("Planned", "Released", "On Hold"), "close": ("Complete",)}
new = {"release": "Released", "hold": "On Hold", "cancel": "Cancelled", "close": "Closed"}
bad = []
for action in ("release", "hold", "cancel", "close"):
    for status in STATUSES:
        db = FakeDb()
        db.orders["WO-1001"]["status"] = status
        before, writes = snapshot(db), db.writes
        res = act(db, action, "WO-1001", "proof not approved")
        if status in allowed[action]:
            ok = res.startswith("ok:") and db.orders["WO-1001"]["status"] == new[action]
        else:
            ok = res == "error: WO-1001 is %s; %s is not allowed" % (status, action) \
                and snapshot(db) == before and db.writes == writes
        if not ok:
            bad.append((action, status, res))
check(not bad, "28 action x status pairs: allowed ones succeed, the rest return error and change nothing %s" % bad)
check(wo["check_transition"]("hold", "Running") == "Running; hold is not allowed"
      and wo["check_transition"]("close", "Released") == "Released; close is not allowed"
      and wo["check_transition"]("release", "Cancelled") == "Cancelled; release is not allowed"
      and wo["check_transition"]("release", "On Hold") is None,
      "check_transition: hold on Running, close on Released, release on Cancelled refused; release on On Hold allowed")
db = FakeDb()
db.orders["WO-1003"]["status"] = "Running"
check(act(db, "hold", "WO-1003", "x") == "error: WO-1003 is Running; hold is not allowed",
      "result string: 'error: WO-1003 is Running; hold is not allowed'")

db = FakeDb()
before = snapshot(db)
r1, r2 = act(db, "hold", "WO-1001", ""), act(db, "release", "WO-9999")
check(r1 == "error: hold needs a reason" and r2 == "error: no work order WO-9999" and snapshot(db) == before,
      "hold without a reason, unknown order: error, nothing changed")
db.orders["WO-1001"]["status"] = "Complete"  # someone else changed it between read and write
res = db.atomic(lambda d: (d.transition("WO-1001", "Released", "hold", "x") and "ok: x") or wo["STALE"])
check(res == "error: status changed since you looked; refresh", "status guard misses -> 'status changed since you looked'")

db = FakeDb()
db.orders["WO-1001"]["hold_reason"] = None
act(db, "hold", "WO-1001", "proof not approved")
check(db.orders["WO-1001"]["hold_reason"] == "proof not approved", "hold stores hold_reason")
db.now = T0 + 999_000
act(db, "release", "WO-1001")
check(db.orders["WO-1001"]["released_at"] == SEED_RELEASED and db.orders["WO-1001"]["hold_reason"] is None,
      "re-release keeps released_at, clears hold_reason")
check(db.next_released(101) == "WO-1001", "attribution order unchanged after Hold -> Release (WO-1001 before WO-1006)")
act(db, "close", "WO-1001")
db.orders["WO-1004"]["status"] = "Complete"
act(db, "close", "WO-1004")
check(db.orders["WO-1004"]["status"] == "Closed" and db.orders["WO-1004"]["closed_at"] == db.now,
      "close: Complete -> Closed, closed_at = now()")

print("\nCreate:")
db = FakeDb()
res = create(db, 105, 100, T0 + 7_200_000, "Normal", "WO-003 test")
o = db.orders.get("WO-1011")
check(res == "ok: created WO-1011 (product 105, 100 m, Normal), Planned" and o and o["status"] == "Planned"
      and o["notes"] == "WO-003 test" and o["due_date"] == T0 + 7_200_000,
      "create: WO-1011 Planned from the sequence (%s)" % res)
check(create(db, 102, 50.0)[:16] == "ok: created WO-1", "create: a second order takes the next id")
before = snapshot(db)
errs = [create(db, 106), create(db, 101, 0), create(db, 101, 100, None), create(db, 101, 100, T0, "Urgent"),
        create(db, "x")]
check(all(e.startswith("error:") for e in errs) and snapshot(db) == before,
      "create: unknown product, planned 0, no due date, bad priority, non-number -> error, nothing inserted %s" % errs)


# ---------- status_from_runs ----------

print("\nstatus_from_runs:")
sfr = wo["status_from_runs"]
check(sfr([]) == ("Released", None, None), "no runs -> Released, NULL times")
check(sfr([(10, 20), (30, None)]) == ("Running", 10, None), "an open run -> Running, earliest start")
check(sfr([(30, 40), (10, 20)]) == ("Complete", 10, 40), "closed runs -> Complete, earliest start, latest end")


# ---------- replay scenarios ----------

print("\nRelease WO-1010 before the second-loop 105 run:")
est, wst, db, logs = engine_store(), wo["_new_store"](), FakeDb(), []
drive(est, wst, db, loop1, logs)
check(act(db, "release", "WO-1010").startswith("ok:"), "release WO-1010 at the end of loop 1")
drive(est, wst, db, loop2, logs)
check([r["work_order_id"] for r in db.runs] == ["WO-%d" % i for i in range(1001, 1011)],
      "runs attributed WO-1001 .. WO-1010")
check(wst["run"]["work_order_id"] == "WO-1010" and wst["run"]["attributed"] and not wst["unattributed"]
      and db.orders["WO-1010"]["status"] == "Running" and db.runs[-1]["good_meters"] == 365,
      "second-loop 105 run on WO-1010 (Running, 365 m), nothing unattributed")

print("\nHold WO-1006 before the second-loop 101 run, release it later:")
est, wst, db, logs = engine_store(), wo["_new_store"](), FakeDb(), []
drive(est, wst, db, loop1, logs)
act(db, "hold", "WO-1006", "proof not approved")
rest = drive(est, wst, db, loop2, logs, until=first("J102 Run 1", loop2))  # loop-2 101 run closed
check(db.next_released(101) is None and [r["product_code"] for r in db.runs].count(101) == 1
      and len(wst["unattributed"]) == 1 and wst["unattributed"][0]["product"] == 101
      and wst["unattributed"][0]["good"] == 600,
      "no Released 101 order: the loop-2 101 run is unattributed (600 m)")
check(db.orders["WO-1006"]["status"] == "On Hold", "WO-1006 stays On Hold")
act(db, "release", "WO-1006")
drive(est, wst, db, rest, logs)
drive(est, wst, db, loop3, logs, until=first("J101 Run 2", loop3))
r101 = [r for r in db.runs if r["product_code"] == 101]
check([r["work_order_id"] for r in r101] == ["WO-1001", "WO-1006"] and r101[-1]["start_ts"] >= loop3[0]["t"],
      "released later: the loop-3 101 run attributes to WO-1006")

print("\nReassign a closed run WO-1001 -> a new Released 101 order:")
est, wst, db, logs = engine_store(), wo["_new_store"](), FakeDb(), []
drive(est, wst, db, loop1, logs, until=first("J103 Run 1", loop1))  # runs 1, 2 closed, 3 not started
create(db, 101, 600)
act(db, "release", "WO-1011")
run1 = dict(db.runs[0])
res = reassign(wst, db, {"run_id": 1}, "WO-1011")
o11, o01 = db.orders["WO-1011"], db.orders["WO-1001"]
check(res == "ok: run 1 WO-1001 -> WO-1011; WO-1001 Released, WO-1011 Complete", "result: %s" % res)
check(o11["status"] == "Complete" and o11["started_at"] == run1["start_ts"] and o11["completed_at"] == run1["end_ts"],
      "target WO-1011 Complete with the run's start and end")
check(o01["status"] == "Released" and o01["started_at"] is None and o01["completed_at"] is None,
      "WO-1001 back to Released with NULL started_at / completed_at")
check(db.runs[0]["work_order_id"] == "WO-1011" and db.runs[0]["attributed_by"] == "manual"
      and db.runs[0]["good_meters"] == 600, "run row moved, attributed_by manual, totals kept")

print("\nReassign the open run mid-run:")
est, wst, db, logs = engine_store(), wo["_new_store"](), FakeDb(), []
mid = [s for s in loop1 if s["_phase"].startswith("J103 Run")]  # the 103 run
rest = drive(est, wst, db, loop1, logs, until=lambda s: s["t"] == mid[len(mid) // 2]["t"])
create(db, 103, 450)
act(db, "release", "WO-1011")
good_then = db.runs[2]["good_meters"]
before = snapshot(db)
res = reassign(wst, db, {"run_id": 3}, "WO-1011")
check(res.startswith("ok: queued") and snapshot(db) == before and wst["run"]["work_order_id"] == "WO-1003"
      and len(wst["pending"]) == 1, "queued, nothing changed before the tick (%s)" % res)
drive(est, wst, db, rest[:1], logs)
check(wst["run"]["work_order_id"] == "WO-1011" and db.runs[2]["work_order_id"] == "WO-1011"
      and not wst["pending"], "applied on the next tick: open run and its row on WO-1011")
check(db.orders["WO-1003"]["status"] == "Released" and db.orders["WO-1003"]["started_at"] is None
      and db.orders["WO-1011"]["status"] == "Running" and db.orders["WO-1011"]["started_at"] == db.runs[2]["start_ts"],
      "WO-1003 Released (NULL times), WO-1011 Running from the run start")
check(any(line.startswith("action reassign WO-1011 by test: ok: run 3 WO-1003 -> WO-1011") for line in logs),
      "log: action reassign WO-1011 by test: ok: ...")
drive(est, wst, db, rest[1:], logs, until=first("J104 Run 1", loop1))
check(db.runs[2]["good_meters"] > good_then, "later deltas count into the same run row (%d -> %d)" % (
    good_then, db.runs[2]["good_meters"]))
check(db.orders["WO-1011"]["status"] == "Complete" and db.runs[2]["good_meters"] == 450 and db.runs[2]["reject_meters"] == 18
      and db.orders["WO-1003"]["status"] == "Released",
      "at product change WO-1011 Complete with the full run total 450 / 18; WO-1003 stays Released")
check(any(line.startswith("complete WO-1011 good=450 planned=450") for line in logs), "log: complete WO-1011 good=450 planned=450")

print("\nOpen run closes before the queue is applied:")
est, wst, db, logs = engine_store(), wo["_new_store"](), FakeDb(), []
rest = drive(est, wst, db, loop1, logs, until=first("J103 Run 1", loop1))
create(db, 102, 375)
act(db, "release", "WO-1011")
wst["pending"].append({"type": "reassign", "run_id": 2, "unattributed_start": None, "target": "WO-1011", "by": "test"})
before = snapshot(db)
lines = apply_pending(wst, db)
check(snapshot(db) == before and lines and "no longer open" in lines[0] and not wst["pending"],
      "queued reassign of a run that closed meanwhile: error logged, nothing changed")

print("\nAssign an unattributed run, closed:")
est, wst, db, logs = engine_store(), wo["_new_store"](), FakeDb(), []
db.orders["WO-1005"]["status"] = "On Hold"
drive(est, wst, db, loop1 + loop2, logs, until=first("J101 Run 2", loop2))  # loop-1 105 run closed
ua = wst["unattributed"][0]
create(db, 105, 365)
act(db, "release", "WO-1011")
res = reassign(wst, db, {"unattributed_start": ua["start"]}, "WO-1011")
row = db.runs[-1]
check(res.startswith("ok: unattributed run of 105 -> WO-1011") and row["work_order_id"] == "WO-1011"
      and row["start_ts"] == ua["start"] and row["end_ts"] == ua["end"] and row["good_meters"] == 365
      and row["reject_meters"] == 83 and row["attributed_by"] == "manual", "run row inserted for WO-1011 (%s)" % res)
o = db.orders["WO-1011"]
check(o["status"] == "Complete" and o["started_at"] == ua["start"] and o["completed_at"] == ua["end"],
      "WO-1011 Complete with the run's start and end")
create(db, 105, 365)
act(db, "release", "WO-1012")
before = snapshot(db)
res = reassign(wst, db, {"unattributed_start": ua["start"]}, "WO-1012")
check(res.startswith("error:") and snapshot(db) == before, "assigning it twice -> error, nothing changed (%s)" % res)

print("\nAssign an unattributed run, open:")
est, wst, db, logs = engine_store(), wo["_new_store"](), FakeDb(), []
mid = [s for s in loop2 if s["_phase"].startswith("J105 Run")]  # the loop-2 105 run
rest = drive(est, wst, db, loop1 + loop2, logs, until=lambda s: s["t"] == mid[len(mid) // 2]["t"])
check(not wst["run"]["attributed"] and wst["run"]["product"] == 105, "loop-2 105 run open, unattributed")
act(db, "release", "WO-1010")
start = wst["run"]["start"]
res = reassign(wst, db, {"unattributed_start": start}, "WO-1010")
n_runs = len(db.runs)
drive(est, wst, db, rest[:1], logs)
check(res.startswith("ok: queued") and len(db.runs) == n_runs + 1 and db.runs[-1]["work_order_id"] == "WO-1010"
      and db.runs[-1]["end_ts"] is None and db.orders["WO-1010"]["status"] == "Running",
      "applied on the next tick: run row inserted (open), WO-1010 Running")
check(wst["run"]["attributed"] and wst["run"]["run_id"] == db.runs[-1]["run_id"] and wst["run"]["work_order_id"] == "WO-1010",
      "in-memory run now attributed to WO-1010 with the new run_id")
drive(est, wst, db, rest[1:] + loop3, logs, until=first("J101 Run 1", loop3))
check(db.orders["WO-1010"]["status"] == "Complete" and db.runs[-1]["good_meters"] == 365
      and db.runs[-1]["reject_meters"] == 83 and not wst["unattributed"],
      "at product change WO-1010 Complete with the full run 365 / 83, nothing unattributed")

print("\nBad targets:")
est, wst, db, logs = engine_store(), wo["_new_store"](), FakeDb(), []
drive(est, wst, db, loop1, logs, until=first("J103 Run 1", loop1))
before = snapshot(db)
errs = [reassign(wst, db, {"run_id": 1}, "WO-1002"),       # different product
        reassign(wst, db, {"run_id": 1}, "WO-1006", by=""),  # no entered by
        reassign(wst, db, {"run_id": 1}, "WO-1001"),       # same order
        reassign(wst, db, {"run_id": 99}, "WO-1006")]      # no such run
unchanged = snapshot(db) == before
db.orders["WO-1006"]["status"] = "Planned"
before = snapshot(db)
errs.append(reassign(wst, db, {"run_id": 1}, "WO-1006"))   # not Released
check(all(e.startswith("error:") for e in errs) and unchanged and snapshot(db) == before and not wst["pending"],
      "different product, no entered by, same order, no run, target Planned -> error, nothing changed %s" % errs)
check(errs[0] == "error: WO-1002 is product 102, the run is 101" and errs[4] == "error: WO-1006 is Planned; the target must be Released",
      "error strings name the product / status")


# ---------- store compatibility ----------

print("\nStore:")
g = wo["system"].util.globals
old = {"version": 1, "run": {"run_id": 5}, "resumed": True, "unattributed": [], "last_closed": None}  # WO-001-fix1 store
g[wo["STORE_KEY"]] = old
st = wo["_store"]()
check(st is old and st["pending"] == [] and st["run"] == {"run_id": 5} and wo["STORE_KEY"] == "mes.workorder.v1",
      "a WO-001 store gets 'pending' by setdefault; same key, open run kept")

print("\n%s: %d failure(s)" % ("FAILED" if failures else "PASSED", len(failures)))
sys.exit(1 if failures else 0)
