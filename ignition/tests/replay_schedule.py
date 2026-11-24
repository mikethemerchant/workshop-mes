#!/usr/bin/env python3
"""Offline replay of the Press 1 sim through mes.engine, mes.workorder and mes.schedule (SCHED-001).

Builds the 2 s sample stream from docs/sim/fourzero_printing_press1.json and feeds two full loops
(from J101 Setup) through engine.step() -> workorder.handle() -> schedule.handle(), as engine.tick()
dispatches them, against a FakeDb holding the DB-002 seed (orders, product master, schedule 1-5).

1. Backfill, as on the live gateway: loop 1 runs with the work-order handler only, then mes.schedule
   is loaded; its first tick syncs and replans; loop 2 runs with both. Checks the plan against actual.
2. Schedule released orders on the seed, then two loops with both handlers from the first sample.
3. Edge cases: Cancelled entries and orders, a Setup gap > 300 s, an open run, idempotence, Rush order.
4. Readiness (SCHED-002): edit allowed / refused per status, the upsert, and started_without_readiness
   set on the Queued -> Running transition from the readiness at that moment.
Never calls tick() or on_tick().

Run from the repo root: python3 -I ignition/tests/replay_schedule.py
Exits non-zero on any failed assertion. Standard library only.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from simreplay import ENGINE, SCRIPTS, T0, WORKORDER, load, load_sim, loop_samples

SCHEDULE = SCRIPTS / "mes/schedule/code.py"
MIN = 60_000

failures = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        failures.append(msg)


eng = load(ENGINE, "mes.engine")
wo = load(WORKORDER, "mes.workorder")
sch = load(SCHEDULE, "mes.schedule")
step = eng["step"]


def engine_store():
    eng["system"].util.globals.clear()
    return eng["_store"]()


# ---------- FakeDb: the SQL of mes.workorder._SqlDb and mes.schedule._SqlDb on the DB-002 seed ----------

PRODUCTS = {101: (250.0, 20), 102: (180.0, 30), 103: (200.0, 24), 104: (220.0, 20), 105: (160.0, 30)}
SEED_RELEASED = T0 - 3_600_000
SEED = [  # (work_order_id, product_code, planned_meters, due in minutes from the seed, priority, status)
    ("WO-1001", 101, 600, 60, "Normal", "Released"), ("WO-1002", 102, 375, 65, "Normal", "Released"),
    ("WO-1003", 103, 450, 70, "Normal", "Released"), ("WO-1004", 104, 525, 6, "Normal", "Released"),
    ("WO-1005", 105, 365, 75, "Normal", "Released"), ("WO-1006", 101, 600, 80, "Normal", "Released"),
    ("WO-1007", 102, 375, 85, "Normal", "Released"), ("WO-1008", 103, 450, 90, "Normal", "Released"),
    ("WO-1009", 104, 525, 20, "Rush", "Released"), ("WO-1010", 105, 365, 100, "Normal", "Planned"),
]


class FakeDb:
    def __init__(self):
        self.orders = {}
        for wid, product, planned, due, prio, status in SEED:
            self.orders[wid] = {"work_order_id": wid, "product_code": product, "planned_meters": planned,
                                "due_date": SEED_RELEASED + due * MIN, "priority": prio, "status": status,
                                "released_at": SEED_RELEASED if status == "Released" else None,
                                "started_at": None, "completed_at": None}
        self.runs = []
        self.entries_ = []      # rows of mes.schedule_entry
        self.plan_writes = 0
        self.actual_writes = 0
        self.readiness_ = {}    # rows of mes.job_readiness (seed: WO-1001 .. WO-1006, WO-1006 proof missing)
        for i in range(1, 7):
            self.upsert_readiness("WO-100%d" % i, True, True, i != 6, "seed")
        for i in range(1, 6):   # seed schedule: WO-1001 .. WO-1005, sequence 1-5
            self.insert_entry("WO-100%d" % i, i, PRODUCTS[100 + i][1])

    # mes.workorder interface
    def run(self, run_id):
        return next(r for r in self.runs if r["run_id"] == run_id)

    def next_released(self, product):
        c = [o for o in self.orders.values() if o["product_code"] == product and o["status"] == "Released"]
        c.sort(key=lambda o: (o["released_at"], o["work_order_id"]))
        return c[0]["work_order_id"] if c else None

    def start_run(self, wid, product, t, good_counter, reject_counter):
        o = self.orders[wid]
        if o["status"] == "Released":
            o["status"], o["started_at"] = "Running", o["started_at"] or t
        run_id = len(self.runs) + 1
        self.runs.append({"run_id": run_id, "work_order_id": wid, "product_code": product, "start_ts": t,
                          "end_ts": None, "good_meters": 0, "reject_meters": 0, "counter_reset_seen": False})
        return run_id

    def update_run(self, run_id, good, reject, reset_seen):
        r = self.run(run_id)
        r["good_meters"], r["reject_meters"], r["counter_reset_seen"] = int(round(good)), int(round(reject)), bool(reset_seen)

    def end_run(self, run_id, t, good, reject):
        r = self.run(run_id)
        r["end_ts"], r["good_meters"], r["reject_meters"] = t, int(round(good)), int(round(reject))

    def complete(self, wid, t):
        o = self.orders[wid]
        if o["status"] != "Running":
            return None
        o["status"], o["completed_at"] = "Complete", t
        return o["planned_meters"]

    def open_runs(self):
        rs = sorted([r for r in self.runs if r["end_ts"] is None], key=lambda r: r["start_ts"], reverse=True)
        return [{k: r[k] for k in ("run_id", "work_order_id", "product_code", "good_meters", "reject_meters")}
                for r in rs]

    # mes.schedule interface
    def entry(self, wid):
        return next(e for e in self.entries_ if e["work_order_id"] == wid)

    def entries(self):
        out = []
        for e in sorted(self.entries_, key=lambda e: (e["sequence"], e["entry_id"])):
            o = self.orders[e["work_order_id"]]
            out.append({"entry_id": e["entry_id"], "work_order_id": e["work_order_id"], "sequence": e["sequence"],
                        "status": e["status"], "scheduled_changeover_s": e["scheduled_changeover_s"],
                        "planned_start": e["planned_start"], "planned_end": e["planned_end"],
                        "actual_start": e["actual_start_ts"], "actual_end": e["actual_end_ts"],
                        "wo_status": o["status"], "product_code": o["product_code"],
                        "planned_meters": o["planned_meters"],
                        "ideal_speed_mpm": PRODUCTS[o["product_code"]][0]})
        return out

    def runs_(self):
        return [{"work_order_id": r["work_order_id"], "start": r["start_ts"], "end": r["end_ts"]}
                for r in sorted(self.runs, key=lambda r: (r["start_ts"], r["run_id"]))]

    def set_plan(self, entry_id, start, end):
        e = next(e for e in self.entries_ if e["entry_id"] == entry_id)
        e["planned_start"], e["planned_end"] = start, end
        self.plan_writes += 1

    def set_actual(self, entry_id, status, start, end):
        e = next(e for e in self.entries_ if e["entry_id"] == entry_id)
        if e["status"] == "Cancelled":  # the SQL's AND status <> 'Cancelled'
            return
        e["status"], e["actual_start_ts"], e["actual_end_ts"] = status, start, end
        self.actual_writes += 1

    def unscheduled_released(self):
        have = set(e["work_order_id"] for e in self.entries_)
        return [{"work_order_id": o["work_order_id"], "priority": o["priority"], "due_date": o["due_date"],
                 "scheduled_changeover_s": PRODUCTS[o["product_code"]][1]}
                for o in self.orders.values() if o["status"] == "Released" and o["work_order_id"] not in have]

    def max_sequence(self):
        return max([e["sequence"] for e in self.entries_]) if self.entries_ else None

    def insert_entry(self, wid, seq, changeover):
        assert all(e["work_order_id"] != wid for e in self.entries_), "schedule_entry.work_order_id is UNIQUE"
        self.entries_.append({"entry_id": len(self.entries_) + 1, "work_order_id": wid, "sequence": seq,
                              "planned_start": None, "planned_end": None, "scheduled_changeover_s": changeover,
                              "status": "Queued", "actual_start_ts": None, "actual_end_ts": None,
                              "started_without_readiness": False})

    # SCHED-002
    def readiness(self, wid):
        r = self.readiness_.get(wid)
        return None if r is None else dict(r, is_ready=r["plates_ready"] and r["ink_ready"] and r["proof_approved"])

    def set_started_without_readiness(self, entry_id, flag):
        next(e for e in self.entries_ if e["entry_id"] == entry_id)["started_without_readiness"] = bool(flag)

    def order_status(self, wid):
        o = self.orders.get(wid)
        return None if o is None else o["status"]

    def upsert_readiness(self, wid, plates, ink, proof, entered_by):
        if wid not in self.orders or self.orders[wid]["status"] not in ("Planned", "Released", "On Hold"):
            return False  # the SQL's status guard
        self.readiness_[wid] = {"plates_ready": plates, "ink_ready": ink, "proof_approved": proof,
                                "entered_by": entered_by}
        return True

    # SCHED-003: handle() reconciles on run_start (covered by test_schedule_override.py)
    def overrides(self):
        return []

    def set_reconcile(self, entry_id, result):
        next(e for e in self.entries_ if e["entry_id"] == entry_id)["reconcile_result"] = result


class ScheduleDb:
    """FakeDb as mes.schedule sees it: runs() is a method there, the list of run rows here."""

    def __init__(self, db):
        self.db = db

    def runs(self):
        return self.db.runs_()

    def __getattr__(self, name):
        return getattr(self.db, name)


def drive(est, wst, sst, db, samples, logs, schedule=True):
    """engine.step(), then workorder.handle(), then schedule.handle() per sample, as tick() dispatches."""
    for s in samples:
        n = est["samples"]
        evs = step(est, s)
        if est["samples"] == n:
            continue
        logs.extend(wo["handle"](wst, dict(est["last"]), [dict(e) for e in evs], db))
        if schedule:
            logs.extend(sch["handle"](sst, dict(est["last"]), [dict(e) for e in evs], ScheduleDb(db)))


def secs(a, b):
    return None if a is None or b is None else (b - a) / 1000.0


def near(vals, want, tol=1.0):
    return len(vals) == len(want) and all(v is not None and abs(v - w) <= tol for v, w in zip(vals, want))


# ---------- 1. backfill: two loops, mes.schedule loaded after loop 1 ----------

sim = load_sim()
counters = {}
loop1, t = loop_samples(sim, counters, T0)
loop2, t = loop_samples(sim, counters, t)
print("Sim: %s, two loops, %d samples" % (sim["name"], len(loop1) + len(loop2)))

PLANNED = [164, 155, 159, 163, 167]
SETUP_101 = 18  # s, J101 Setup: the first entry's Setup is before its run and has no previous run

db = FakeDb()
est, wst, sst, logs = engine_store(), wo["_new_store"](), {"loaded": False}, []
# Loop 1 and the first loop-2 sample (product change 105 -> 101 closes run 5) before mes.schedule loads.
drive(est, wst, sst, db, loop1 + loop2[:1], logs, schedule=False)
check(len(db.runs) == 5 and all(r["end_ts"] is not None for r in db.runs) and db.plan_writes == 0,
      "before load: runs 1-5 closed, schedule untouched")
load_logs = []
drive(est, wst, sst, db, loop2[1:2], load_logs)
plan_after_load = [(e["planned_start"], e["planned_end"]) for e in db.entries_]
writes_after_load = db.plan_writes
drive(est, wst, sst, db, loop2[2:], logs)

print("\nFirst tick after load:")
for line in load_logs:
    print("  " + line)
print("\nSchedule after loop 2:")
for e in db.entries_:
    print("  %(sequence)d %(work_order_id)s %(status)s" % e +
          " planned %.3f s actual %s s" % (secs(e["planned_start"], e["planned_end"]),
                                           secs(e["actual_start_ts"], e["actual_end_ts"])))

print("\nBackfill:")
E = sorted(db.entries_, key=lambda e: e["sequence"])
runs = db.runs
check(any(l.startswith("first tick: sync ok: synced, 5 changed") for l in load_logs)
      and any(l.startswith("first tick: planned_start missing, replan ok: replanned 5 entries") for l in load_logs),
      "first tick: sync (5 changed) then replan (5 entries)")
check([e["status"] for e in E] == ["Done"] * 5, "entries 1-5 Done")
planned = [secs(e["planned_start"], e["planned_end"]) for e in E]
check(near(planned, PLANNED), "planned totals %s ~ 164, 155, 159, 163, 167 s" % planned)
actual = [secs(e["actual_start_ts"], e["actual_end_ts"]) for e in E]
check(near(actual, [198 - SETUP_101, 224, 202, 214, 208], 0),
      "actual totals %s = 180 (198 less its unobserved 18 s Setup), 224, 202, 214, 208 s" % actual)
check(E[0]["actual_start_ts"] == runs[0]["start_ts"], "entry 1 actual start = run 1 start (no previous run)")
check([e["actual_start_ts"] for e in E[1:]] == [r["end_ts"] for r in runs[:4]],
      "entries 2-5 actual start = previous run's end (the product change)")
check([e["actual_end_ts"] for e in E] == [r["end_ts"] for r in runs[:5]], "actual end = the run's end")
check(E[0]["planned_start"] == E[0]["actual_start_ts"], "planned chain anchored at entry 1's actual start")
check(all(a["planned_end"] == b["planned_start"] for a, b in zip(E, E[1:])), "planned_start(k+1) = planned_end(k)")
loop_plan = secs(E[0]["planned_start"], E[-1]["planned_end"])
loop_actual = secs(E[0]["actual_start_ts"], E[-1]["actual_end_ts"])
check(abs(loop_plan - 808) <= 1, "loop plan %.3f s ~ 808 s" % loop_plan)
check(loop_actual == 1046 - SETUP_101 and loop_actual + SETUP_101 == 1046,
      "loop actual %.0f s = 1,046 s less entry 1's unobserved Setup (18 s)" % loop_actual)
check([(e["planned_start"], e["planned_end"]) for e in db.entries_] == plan_after_load
      and db.plan_writes == writes_after_load == 5,
      "loop 2 (run_start / product_change): planned times unchanged, no replan (%d plan writes)" % db.plan_writes)
check(sum(l.startswith("sync ok") for l in logs) == 9,
      "loop 2: one sync per run_start / product_change sample (9)")
check(not any("replan" in l for l in logs), "loop 2: no replan line")


# ---------- 2. schedule released orders, then two loops from the start ----------

print("\nSchedule released orders:")
db = FakeDb()
res = sch["do_schedule_released"](ScheduleDb(db), T0)
print("  " + res)
E = sorted(db.entries_, key=lambda e: e["sequence"])
check([(e["sequence"], e["work_order_id"]) for e in E[5:]] ==
      [(6, "WO-1009"), (7, "WO-1006"), (8, "WO-1007"), (9, "WO-1008")],
      "appended 6 WO-1009 (Rush), 7 WO-1006, 8 WO-1007, 9 WO-1008 (due date)")
check(all(e["work_order_id"] != "WO-1010" for e in E), "WO-1010 (Planned) not added")
check([e["scheduled_changeover_s"] for e in E[5:]] == [20, 20, 30, 24], "changeover from product master")
check(all(e["planned_start"] is not None for e in E) and E[0]["planned_start"] == T0
      and all(a["planned_end"] == b["planned_start"] for a, b in zip(E, E[1:])),
      "then replan: 9 entries chained from now")
check(res.startswith("ok: scheduled 4 released orders (6 WO-1009, 7 WO-1006, 8 WO-1007, 9 WO-1008)"), "result string")
res2 = sch["do_schedule_released"](ScheduleDb(db), T0)
check(res2.startswith("ok: scheduled 0 released orders (none)") and len(db.entries_) == 9, "second press adds nothing")

est, wst, sst, logs = engine_store(), wo["_new_store"](), {"loaded": False}, []
plan_before = [(e["planned_start"], e["planned_end"]) for e in db.entries_]
drive(est, wst, sst, db, loop1 + loop2, logs)
E = {e["work_order_id"]: e for e in db.entries_}
check(logs and not any("replan" in l for l in logs), "planned_start set: first tick syncs, no replan")
check([(e["planned_start"], e["planned_end"]) for e in db.entries_] == plan_before, "planned times unchanged over two loops")
check([E["WO-100%d" % i]["status"] for i in range(1, 10)] == ["Done"] * 9, "WO-1001 .. WO-1009 entries Done")
a2 = [secs(E[w]["actual_start_ts"], E[w]["actual_end_ts"]) for w in ("WO-1006", "WO-1007", "WO-1008", "WO-1009")]
check(near(a2, [198, 224, 202, 214], 0), "loop 2 (Setup observed): WO-1006 .. WO-1009 actual %s = 198, 224, 202, 214 s" % a2)


# ---------- 3. edge cases ----------

print("\nEdge cases:")
S = 1000


def edge_db():
    db = FakeDb()
    for i, (start, end) in enumerate([(0, 200), (244, 420), (442, None)]):  # WO-1001 .. WO-1003
        rid = db.start_run("WO-100%d" % (i + 1), 101 + i, T0 + start * S, 0, 0)
        if end is not None:
            db.end_run(rid, T0 + end * S, 0, 0)
    return db


# Open run: Running, no end; previous end 22 s before
db = edge_db()
print("  " + sch["do_sync"](ScheduleDb(db)))
e3 = db.entry("WO-1003")
check(e3["status"] == "Running" and e3["actual_start_ts"] == T0 + 420 * S and e3["actual_end_ts"] is None,
      "open run: Running, actual start = previous end, end NULL")
check(db.entry("WO-1004")["status"] == "Queued" and db.entry("WO-1004")["actual_start_ts"] is None,
      "no run: entry stays Queued")
w = db.actual_writes
check(sch["do_sync"](ScheduleDb(db)) == "ok: synced, 0 changed" and db.actual_writes == w, "idempotent: second sync writes nothing")

# Gap > 300 s: actual start = run start; exactly 300 s: previous end
db = FakeDb()
r1 = db.start_run("WO-1001", 101, T0, 0, 0)
db.end_run(r1, T0 + 100 * S, 0, 0)
db.start_run("WO-1002", 102, T0 + 401 * S, 0, 0)        # 301 s after run 1 ended
sch["do_sync"](ScheduleDb(db))
check(db.entry("WO-1002")["actual_start_ts"] == T0 + 401 * S, "gap 301 s: actual start = run start")
db = FakeDb()
r1 = db.start_run("WO-1001", 101, T0, 0, 0)
db.end_run(r1, T0 + 100 * S, 0, 0)
db.start_run("WO-1002", 102, T0 + 400 * S, 0, 0)        # 300 s
sch["do_sync"](ScheduleDb(db))
check(db.entry("WO-1002")["actual_start_ts"] == T0 + 100 * S, "gap 300 s: actual start = previous run's end")

# Cancelled order and Cancelled entry: untouched by sync and replan
db = edge_db()
db.orders["WO-1002"]["status"] = "Cancelled"
db.entry("WO-1003")["status"] = "Cancelled"
before = {w: dict(db.entry(w)) for w in ("WO-1002", "WO-1003")}
sch["do_sync"](ScheduleDb(db))
sch["do_replan"](ScheduleDb(db), T0)
check(all(db.entry(w) == before[w] for w in before), "Cancelled order / Cancelled entry: row untouched by sync and replan")
check(db.entry("WO-1004")["planned_start"] == db.entry("WO-1001")["planned_end"],
      "replan skips them: WO-1004 planned right after WO-1001")

# Replan anchor: entry 1's actual start, else now; explicit anchor_ms wins
db = FakeDb()
sch["do_replan"](ScheduleDb(db), T0 + 5 * S)
check(db.entry("WO-1001")["planned_start"] == T0 + 5 * S, "replan with no actual start: anchored at now")
sch["do_replan"](ScheduleDb(db), T0 + 5 * S, T0 + 9 * S)
check(db.entry("WO-1001")["planned_start"] == T0 + 9 * S, "replan(anchor_ms): explicit anchor used")

# append_order: Rush first even with a later due date, then due date, then work_order_id
rel = [{"work_order_id": "WO-2", "priority": "Normal", "due_date": 5, "scheduled_changeover_s": 1},
       {"work_order_id": "WO-1", "priority": "Normal", "due_date": 5, "scheduled_changeover_s": 1},
       {"work_order_id": "WO-3", "priority": "Rush", "due_date": 9, "scheduled_changeover_s": 1}]
check([w for w, _, _ in sch["append_order"](rel, None)] == ["WO-3", "WO-1", "WO-2"]
      and [s for _, s, _ in sch["append_order"](rel, 7)] == [8, 9, 10],
      "append_order: Rush, then due date, then work_order_id; sequence from max + 1")

# shape_schedule: columns, seconds, gap only once Done
rows = [{"sequence": 1, "work_order_id": "WO-1001", "product_code": 101, "status": "Done",
         "plates_ready": True, "ink_ready": False, "proof_approved": None,
         "planned_start": T0, "planned_end": T0 + 164 * S, "actual_start_ts": T0, "actual_end_ts": T0 + 198 * S},
        {"sequence": 2, "work_order_id": "WO-1002", "product_code": 102, "status": "Running",
         "plates_ready": False, "ink_ready": False, "proof_approved": False,
         "planned_start": T0 + 164 * S, "planned_end": T0 + 319 * S, "actual_start_ts": T0 + 198 * S, "actual_end_ts": None}]
out = sch["shape_schedule"](rows, lambda ms, p: "%s@%d" % (p, (ms - T0) // S))
keys = [k for k, _ in sch["SCHEDULE_COLUMNS"]]
check(all(list(r) == keys for r in out) and len(keys) == 16, "shape_schedule: 16 columns in order (SCHED-003: Reconcile)")
check((out[0]["planned_s"], out[0]["actual_s"], out[0]["gap_s"]) == (164, 198, 34), "Done: planned 164, actual 198, gap +34")
check((out[1]["actual_s"], out[1]["gap_s"], out[1]["actual_end"]) == ("", "", ""), "Running: actual and gap blank")
check(out[0]["planned_end"] == "HH:mm:ss@164" and (out[0]["plates"], out[0]["ink"], out[0]["proof"]) == ("yes", "no", "no"),
      "times HH:mm:ss, readiness flags")

rows[0]["started_without_readiness"] = True
out = sch["shape_schedule"](rows, lambda ms, p: "")
check((out[0]["swr"], out[1]["swr"]) == ("YES", ""), "Started w/o readiness: YES / blank")


# ---------- 4. readiness (SCHED-002) ----------

print("\nReadiness:")
check_edit = sch["check_readiness_edit"]
for status in ("Planned", "Released", "On Hold"):
    check(check_edit(status) is None, "edit allowed on %s" % status)
for status in ("Running", "Complete", "Closed", "Cancelled"):
    check((check_edit(status) or "").startswith("error:"), "edit refused on %s" % status)
check(check_edit(None) == "error: no such work order", "edit refused on an unknown order")

save = sch["do_save_readiness"]
db = FakeDb()
res = save(ScheduleDb(db), "WO-1010", True, True, False, " SCHED-002 test ")
print("  " + res)
check(res == "ok: readiness WO-1010: plates yes, ink yes, proof no, entered by SCHED-002 test (not ready)"
      and db.readiness("WO-1010")["is_ready"] is False, "save on WO-1010 (Planned): ok, not ready")
res = save(ScheduleDb(db), "WO-1010", 1, 1, 1, "x")
check(res.endswith("(ready)") and db.readiness("WO-1010")["is_ready"] is True and db.readiness("WO-1010")["entered_by"] == "x",
      "second save upserts the same row: ready")
check(save(ScheduleDb(db), "WO-1010", True, True, True, "  ") == "error: entered by is required", "entered by required")
check(save(ScheduleDb(db), None, True, True, True, "x") == "error: pick a work order", "no order picked")
check(save(ScheduleDb(db), "WO-9999", True, True, True, "x") == "error: no such work order", "unknown order")
db.orders["WO-1001"]["status"] = "Running"
before = dict(db.readiness_["WO-1001"])
res = save(ScheduleDb(db), "WO-1001", False, False, False, "x")
check(res.startswith("error:") and db.readiness_["WO-1001"] == before, "save refused on a Running order, row untouched")


def ready_db(readiness):
    """Seed, schedule cut to WO-1006 then WO-1010 at sequence 1-2 (WO-1010 Released), readiness as given."""
    db = FakeDb()
    db.entries_ = []
    db.orders["WO-1010"]["status"] = "Released"
    db.insert_entry("WO-1006", 1, 20)
    db.insert_entry("WO-1010", 2, 30)
    db.readiness_.pop("WO-1010", None)
    if readiness is not None:
        db.readiness_["WO-1010"] = dict(readiness, entered_by="test")
    return db


def start(db, wid, product, t):
    rid = db.start_run(wid, product, t, 0, 0)
    return rid, sch["do_sync"](ScheduleDb(db))


ALL = {"plates_ready": True, "ink_ready": True, "proof_approved": True}
NO_PROOF = {"plates_ready": True, "ink_ready": True, "proof_approved": False}
for name, readiness, want in (("no readiness row", None, True), ("all three", ALL, False),
                              ("proof missing", NO_PROOF, True)):
    db = ready_db(readiness)
    r1, _ = start(db, "WO-1006", 101, T0)               # seed WO-1006: proof missing
    db.end_run(r1, T0 + 200 * S, 0, 0)
    sch["do_sync"](ScheduleDb(db))
    _, res = start(db, "WO-1010", 105, T0 + 230 * S)
    check(db.entry("WO-1006")["started_without_readiness"] is True, "WO-1006 (seed, proof missing): flag true on its start")
    check(db.entry("WO-1010")["started_without_readiness"] is want and
          (("started without readiness: WO-1010" in res) == want),
          "WO-1010 with %s: flag %s on its start (%s)" % (name, want, res))

# Readiness saved after the start does not clear the flag
db = ready_db(None)
rid, _ = start(db, "WO-1010", 105, T0)
db.readiness_["WO-1010"] = dict(ALL, entered_by="late")   # the page refuses this once Running; force it here
sch["do_sync"](ScheduleDb(db))
db.end_run(rid, T0 + 200 * S, 0, 0)
sch["do_sync"](ScheduleDb(db))
check(db.entry("WO-1010")["status"] == "Done" and db.entry("WO-1010")["started_without_readiness"] is True,
      "readiness entered after the start: flag stays true through Running -> Done")
# And the reverse: ready at the start, readiness removed later, flag stays false
db = ready_db(ALL)
rid, _ = start(db, "WO-1010", 105, T0)
db.readiness_.pop("WO-1010")
db.end_run(rid, T0 + 200 * S, 0, 0)
sch["do_sync"](ScheduleDb(db))
check(db.entry("WO-1010")["started_without_readiness"] is False, "ready at the start: flag stays false afterwards")

# First-tick backfill: entries that already ran stay false, even with no readiness
db = ready_db(None)
r1 = db.start_run("WO-1006", 101, T0, 0, 0)
db.end_run(r1, T0 + 200 * S, 0, 0)
db.start_run("WO-1010", 105, T0 + 230 * S, 0, 0)        # open at load
lines = sch["handle"]({"loaded": False}, {"t": T0 + 240 * S}, [], ScheduleDb(db))
check([db.entry(w)["status"] for w in ("WO-1006", "WO-1010")] == ["Done", "Running"]
      and not any(db.entry(w)["started_without_readiness"] for w in ("WO-1006", "WO-1010"))
      and not any("readiness" in l for l in lines),
      "first-tick backfill: Done and Running entries left false, nothing logged")

# Unscheduled order: no entry, no flag, log only
db = ready_db(None)
db.orders["WO-1007"]["status"] = "Released"               # no entry, no readiness row
db.start_run("WO-1007", 102, T0, 0, 0)
res = sch["do_sync"](ScheduleDb(db))
check("started without readiness: WO-1007 (unscheduled)" in res and
      not any(e["started_without_readiness"] for e in db.entries_), "unscheduled order: logged, no entry flagged (%s)" % res)
db = ready_db(None)
db.readiness_["WO-1007"] = dict(ALL, entered_by="test")
db.start_run("WO-1007", 102, T0, 0, 0)
check("readiness" not in sch["do_sync"](ScheduleDb(db)), "unscheduled order, ready: nothing logged")

# Replay: the backfill and loop-2 scenario of section 1 leaves every seed entry false (all ran before load
# or were Queued -> Running with seed readiness all three)
db = FakeDb()
est, wst, sst, logs = engine_store(), wo["_new_store"](), {"loaded": False}, []
drive(est, wst, sst, db, loop1, logs, schedule=False)
drive(est, wst, sst, db, loop2, logs)
check(all(e["started_without_readiness"] is False for e in db.entries_), "replay backfill: seed entries 1-5 false")
db = FakeDb()
sch["do_schedule_released"](ScheduleDb(db), T0)
est, wst, sst, logs = engine_store(), wo["_new_store"](), {"loaded": False}, []
drive(est, wst, sst, db, loop1[:1], logs)                 # first tick before any run: nothing ran yet
drive(est, wst, sst, db, loop1[1:] + loop2, logs)
F = {e["work_order_id"]: e["started_without_readiness"] for e in db.entries_}
check(F == {"WO-1001": False, "WO-1002": False, "WO-1003": False, "WO-1004": False, "WO-1005": False,
            "WO-1006": True, "WO-1007": True, "WO-1008": True, "WO-1009": True},
      "replay from the start: WO-1001..1005 ready -> false; WO-1006 (proof missing), 1007-1009 (no row) -> true")
check(sum("started without readiness" in l for l in logs) == 4, "replay: four 'started without readiness' log notes")

print("\n%s: %d failure(s)" % ("FAILED" if failures else "PASSED", len(failures)))
sys.exit(1 if failures else 0)
