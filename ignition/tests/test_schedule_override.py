#!/usr/bin/env python3
"""Offline test of the rush override, the plan edit and the run-start reconcile in mes.schedule (SCHED-003).

The MES section 4.1 demo recipe, replayed: one sim loop (J101 Setup .. J105) through engine.step() ->
workorder.handle() -> schedule.handle(), as engine.tick() dispatches them, against a FakeDb holding the
DB-002 seed (orders, schedule 1-5), with the schedule staged by do_move_up / do_move_to_front as in the
recipe and the tag writer faked.

1. The four outcomes: Normal start, Authorized override, Override not followed, Unrecorded deviation.
2. Resequencing: no duplicate sequences, Done / Running entries untouched, override row, rollback.
3. Validation: already the head, not Queued, not scheduled, bad reason, empty entered_by.
4. Active-override consumption, OverrideActive, and the Schedule/Override JSON keys.
5. Reconcile edge cases: empty schedule, unattributed run, unscheduled order.
Never calls tick(), on_tick() or a page action.

Run from the repo root: python3 -I ignition/tests/test_schedule_override.py
Exits non-zero on any failed assertion. Standard library only.
"""

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
import simreplay
from simreplay import ENGINE, SCRIPTS, T0, WORKORDER, load, load_sim, loop_samples

SCHEDULE = SCRIPTS / "mes/schedule/code.py"
OUTCOMES = ("Normal start", "Authorized override", "Override not followed", "Unrecorded deviation")

failures = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        failures.append(msg)


eng = load(ENGINE, "mes.engine")
wo = load(WORKORDER, "mes.workorder")
sch = load(SCHEDULE, "mes.schedule")
step = eng["step"]

PRODUCTS = {101: (250.0, 20), 102: (180.0, 30), 103: (200.0, 24), 104: (220.0, 20), 105: (160.0, 30)}


# ---------- FakeDb: mes.workorder's (simreplay) plus the SQL of mes.schedule._SqlDb ----------

class FakeDb(simreplay.FakeDb):
    def __init__(self, schedule=True):
        super().__init__()
        self.entries_ = []      # rows of mes.schedule_entry
        self.overrides_ = []    # rows of mes.schedule_override
        self.readiness_ = {}
        self.fail_sequence = None  # entry_id whose guarded set_sequence returns 0 rows (left Queued)
        if schedule:            # seed schedule: WO-1001 .. WO-1005, sequence 1-5
            for i in range(1, 6):
                self.entries_.append({"entry_id": i, "work_order_id": "WO-100%d" % i, "sequence": i,
                                      "planned_start": None, "planned_end": None,
                                      "scheduled_changeover_s": PRODUCTS[100 + i][1], "status": "Queued",
                                      "actual_start_ts": None, "actual_end_ts": None,
                                      "started_without_readiness": False, "reconcile_result": None})

    def atomic(self, fn):
        saved = copy.deepcopy((self.entries_, self.overrides_))
        try:
            res = fn(self)
        except Exception:
            self.entries_, self.overrides_ = saved
            raise
        if not res.startswith("ok"):
            self.entries_, self.overrides_ = saved
        return res

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
                        "planned_meters": o["planned_meters"], "ideal_speed_mpm": PRODUCTS[o["product_code"]][0]})
        return out

    def runs_(self):
        return [{"work_order_id": r["work_order_id"], "start": r["start_ts"], "end": r["end_ts"]}
                for r in sorted(self.runs, key=lambda r: (r["start_ts"], r["run_id"]))]

    def set_plan(self, entry_id, start, end):
        e = next(e for e in self.entries_ if e["entry_id"] == entry_id)
        e["planned_start"], e["planned_end"] = start, end

    def set_actual(self, entry_id, status, start, end):
        e = next(e for e in self.entries_ if e["entry_id"] == entry_id)
        if e["status"] != "Cancelled":
            e["status"], e["actual_start_ts"], e["actual_end_ts"] = status, start, end

    def readiness(self, wid):
        r = self.readiness_.get(wid)
        return None if r is None else dict(r, is_ready=True)

    def set_started_without_readiness(self, entry_id, flag):
        next(e for e in self.entries_ if e["entry_id"] == entry_id)["started_without_readiness"] = bool(flag)

    # SCHED-003
    def overrides(self):
        return [dict(o) for o in self.overrides_]

    def set_sequence(self, entry_id, seq):
        e = next(e for e in self.entries_ if e["entry_id"] == entry_id)
        if e["status"] != "Queued" or entry_id == self.fail_sequence:  # the SQL's AND status = 'Queued'
            return False
        e["sequence"] = seq
        return True

    def insert_override(self, row):
        assert row["reason"] in ("customer expedite", "material availability", "other"), "reason check"
        assert row["entered_by"], "entered_by NOT NULL"
        self.overrides_.append(dict(row, override_id=len(self.overrides_) + 1, entered_at=self.now,
                                    published_at=None))
        return self.now

    def set_reconcile(self, entry_id, result):
        assert result in OUTCOMES, "reconcile_result check"
        next(e for e in self.entries_ if e["entry_id"] == entry_id)["reconcile_result"] = result


class ScheduleDb:
    """FakeDb as mes.schedule sees it: runs() is a method there, the list of run rows here."""

    def __init__(self, db):
        self.db = db

    def runs(self):
        return self.db.runs_()

    def atomic(self, fn):
        return self.db.atomic(lambda _: fn(self))

    def __getattr__(self, name):
        return getattr(self.db, name)


class FakeTags:
    """The tag writer: records each write, every quality Good unless told otherwise."""

    def __init__(self, bad=()):
        self.writes = []
        self.bad = bad

    def __call__(self, writes):
        self.writes.extend(writes)
        return ["tag %s write %s" % (n, "Bad_Failure" if n in self.bad else "Good") for n, _ in writes]

    def last(self, name):
        vals = [v for n, v in self.writes if n == name]
        return vals[-1] if vals else None


def engine_store():
    eng["system"].util.globals.clear()
    return eng["_store"]()


def drive(db, samples, tags, logs, hook=None):
    """engine.step(), workorder.handle(), schedule.handle() per sample, as tick() dispatches.
    hook(db) runs after each sample (the operator acting between ticks)."""
    est, wst, sst = engine_store(), wo["_new_store"](), {"loaded": False}
    for s in samples:
        n = est["samples"]
        evs = step(est, s)
        if est["samples"] == n:
            continue
        logs.extend(wo["handle"](wst, dict(est["last"]), [dict(e) for e in evs], db))
        logs.extend(sch["handle"](sst, dict(est["last"]), [dict(e) for e in evs], ScheduleDb(db), tags))
        if hook:
            hook(db)


def results(db):
    return dict((e["work_order_id"], e["reconcile_result"]) for e in db.entries_)


def seqs(db):
    return dict((e["work_order_id"], e["sequence"]) for e in db.entries_)


def front(db, wid, reason="customer expedite", by="SCHED-003 test", tags=None):
    return sch["do_move_to_front"](ScheduleDb(db), T0, wid, reason, by, tags or FakeTags())


def up(db, wid):
    return sch["do_move_up"](ScheduleDb(db), T0, wid)


sim = load_sim()
loop, _ = loop_samples(sim, {}, T0)
print("Sim: %s, one loop, %d samples" % (sim["name"], len(loop)))


# ---------- 1. the demo recipe ----------

print("\nNormal: schedule 101 .. 105")
db, tags, logs = FakeDb(), FakeTags(), []
drive(db, loop, tags, logs)
for l in logs:
    if l.startswith("reconcile"):
        print("  " + l)
check(results(db) == dict(("WO-100%d" % i, "Normal start") for i in range(1, 6)), "every start Normal start")
check([e["status"] for e in db.entries_] == ["Done"] * 4 + ["Running"],
      "entries 1-4 Done, 5 Running (its run closes on the next loop's product change)")
check([v for n, v in tags.writes if n == "ReconcileResult"] == ["Normal start"] * 5
      and [v for n, v in tags.writes if n == "OverrideActive"] == [False] * 5,
      "five run starts: ReconcileResult 'Normal start', OverrideActive false each time")
check(not db.overrides_, "no override rows")

print("\nAuthorized override: stage 101, 102, 104, 103, 105 with move_up; after 102 starts, 103 to front")
db, tags, logs = FakeDb(), FakeTags(), []
res = up(db, "WO-1004")
print("  " + res)
check(res.startswith("ok: WO-1004 moved up to 3, ahead of WO-1003 (plan edit, no override record); replanned 5"),
      "move_up result string, then replan")
check(seqs(db) == {"WO-1001": 1, "WO-1002": 2, "WO-1004": 3, "WO-1003": 4, "WO-1005": 5} and not db.overrides_,
      "MES proposes 101, 102, 104, 103, 105; no override row")
check(all(e["planned_start"] is not None for e in db.entries_), "replanned")
seen = {}


def override_after_102(db):
    if "res" not in seen and db.entry("WO-1002")["status"] == "Running":
        seen["before"] = copy.deepcopy(db.entries_)
        seen["res"] = front(db, "WO-1003", tags=tags)
        seen["active"] = len(sch["active_overrides"](db.overrides(), ScheduleDb(db).entries()))
        seen["after"] = copy.deepcopy(db.entries_)
    if "res" in seen and "consumed" not in seen and db.entry("WO-1003")["status"] == "Running":
        seen["consumed"] = len(sch["active_overrides"](db.overrides(), ScheduleDb(db).entries()))


drive(db, loop, tags, logs, override_after_102)
print("  " + seen["res"])
for l in logs:
    if l.startswith("reconcile"):
        print("  " + l)
check(seen["res"].startswith("ok: override WO-1003 (103) 4 -> 3, customer expedite, entered by SCHED-003 test; "
                             "replanned 5 entries"), "move_to_front result string, then replan")
check(results(db) == {"WO-1001": "Normal start", "WO-1002": "Normal start", "WO-1003": "Authorized override",
                      "WO-1004": "Normal start", "WO-1005": "Normal start"},
      "press runs 103 -> Authorized override on WO-1003; the rest Normal start")
check(seen["active"] == 1 and seen["consumed"] == 0, "override active until WO-1003 starts, then consumed")
check([v for n, v in tags.writes if n == "OverrideActive"] == [False, False, True, False, False, False],
      "OverrideActive: false, false (runs 1-2), true (override), false after 103 starts, false, false")
check(tags.writes[-2:] == [("ReconcileResult", "Normal start"), ("OverrideActive", False)],
      "last run start (105): ReconcileResult then OverrideActive")
o = db.overrides_[0]
check((o["work_order_id"], o["product_code"], o["from_sequence"], o["to_sequence"], o["reason"], o["entered_by"],
       o["published_at"]) == ("WO-1003", 103, 4, 3, "customer expedite", "SCHED-003 test", None),
      "schedule_override row: WO-1003 103 4 -> 3, reason, entered_by, published_at NULL")

print("\nOverride not followed: schedule 101 .. 105, override 105 to front")
db, tags, logs = FakeDb(), FakeTags(), []
res = front(db, "WO-1005", "material availability", "SCHED-003 test", tags)
print("  " + res)
check(seqs(db) == {"WO-1005": 1, "WO-1001": 2, "WO-1002": 3, "WO-1003": 4, "WO-1004": 5},
      "105 to the front: 1 WO-1005, 2-5 WO-1001 .. WO-1004")
drive(db, loop, tags, logs)
for l in logs:
    if l.startswith("reconcile"):
        print("  " + l)
check(results(db)["WO-1001"] == "Override not followed", "press runs 101 -> Override not followed on WO-1001")
check(results(db) == {"WO-1001": "Override not followed", "WO-1002": "Override not followed",
                      "WO-1003": "Override not followed", "WO-1004": "Override not followed",
                      "WO-1005": "Authorized override"},
      "102-104 not followed while the override stands; 105 then matches its head -> Authorized override")
check([v for n, v in tags.writes if n == "OverrideActive"] == [True] * 5 + [False],
      "OverrideActive true from the override until 105 starts")

print("\nUnrecorded deviation: stage 101, 102, 104, 103, 105 with move_up, no override")
db, tags, logs = FakeDb(), FakeTags(), []
up(db, "WO-1004")
drive(db, loop, tags, logs)
for l in logs:
    if l.startswith("reconcile"):
        print("  " + l)
check(results(db) == {"WO-1001": "Normal start", "WO-1002": "Normal start", "WO-1003": "Unrecorded deviation",
                      "WO-1004": "Normal start", "WO-1005": "Normal start"},
      "press runs 103 while the head is 104 -> Unrecorded deviation on WO-1003")
check(any(l == "reconcile: product=103 head=WO-1004 (104): Unrecorded deviation; stored on WO-1003" for l in logs),
      "log line names the product, the head and the entry stored on")
check(seqs(db) == {"WO-1001": 1, "WO-1002": 2, "WO-1004": 3, "WO-1003": 4, "WO-1005": 5},
      "history not reordered (sequences as planned)")


# ---------- 2. resequencing ----------

print("\nResequencing:")
before, after = seen["before"], seen["after"]
b = dict((e["work_order_id"], e) for e in before)
a = dict((e["work_order_id"], e) for e in after)
check(b["WO-1001"]["status"] == "Done" and b["WO-1002"]["status"] == "Running", "at the override: 1 Done, 2 Running")
check(all(a[w]["sequence"] == b[w]["sequence"] and a[w]["status"] == b[w]["status"] for w in ("WO-1001", "WO-1002")),
      "Done / Running entries untouched")
check(sorted(e["sequence"] for e in after) == [1, 2, 3, 4, 5], "no duplicate sequences after the override")
check({w: a[w]["sequence"] for w in ("WO-1003", "WO-1004", "WO-1005")} == {"WO-1003": 3, "WO-1004": 4, "WO-1005": 5},
      "WO-1003 3, WO-1004 4 (moved down one), WO-1005 5 (outside the range, unchanged)")

E = FakeDb()
pure = sch["resequence"](ScheduleDb(E).entries(), "WO-1004")
check(pure == ([(1, 2), (2, 3), (3, 4), (4, 1)],
               {"work_order_id": "WO-1004", "product_code": 104, "from_sequence": 4, "to_sequence": 1}),
      "resequence(WO-1004): entries 1-3 +1, WO-1004 -> 1; override row")
check(sch["resequence"](ScheduleDb(E).entries(), "WO-1001") is None, "resequence of the head: None")
check(sch["swap_up"](ScheduleDb(E).entries(), "WO-1001") is None, "swap_up of the head: None")

E = FakeDb()
E.entries_[0]["status"], E.entries_[0]["actual_start_ts"] = "Done", T0  # WO-1001 Done
E.entries_[2]["status"] = "Cancelled"                                    # WO-1003 Cancelled
res = front(E, "WO-1005")
check(res.startswith("ok") and seqs(E) == {"WO-1001": 1, "WO-1002": 3, "WO-1003": 3, "WO-1004": 5, "WO-1005": 2},
      "head is WO-1002 (2): WO-1005 -> 2, WO-1002 3, WO-1004 5; Done WO-1001 and Cancelled WO-1003 untouched")
res = up(E, "WO-1004")
check(res.startswith("ok: WO-1004 moved up to 3, ahead of WO-1002") and seqs(E)["WO-1004"] == 3
      and seqs(E)["WO-1002"] == 5, "move_up skips the Cancelled entry: swaps with the Queued entry before it")

E = FakeDb()
E.fail_sequence = 4  # WO-1004 started between the read and the write
res = front(E, "WO-1004")
check(res == "error: the schedule changed, nothing moved" and seqs(E) == {"WO-100%d" % i: i for i in range(1, 6)}
      and not E.overrides_, "guarded write misses: rolled back, no sequence changed, no override row")


# ---------- 3. validation ----------

print("\nValidation:")
E = FakeDb()
E.entries_[1]["status"] = "Done"  # WO-1002
cases = [
    (front(E, "WO-1001"), "error: WO-1001 is already the head of the schedule", "move_to_front: already the head"),
    (front(E, "WO-1002"), "error: WO-1002 is not Queued", "move_to_front: not Queued"),
    (front(E, "WO-1009"), "error: WO-1009 is not on the schedule", "move_to_front: not scheduled"),
    (front(E, None), "error: pick a work order", "move_to_front: no order"),
    (front(E, "WO-1004", "rush"), "error: reason must be one of customer expedite, material availability, other",
     "move_to_front: bad reason"),
    (front(E, "WO-1004", by="  "), "error: entered by is required", "move_to_front: empty entered_by"),
    (up(E, "WO-1001"), "error: WO-1001 is already the head of the schedule", "move_up: the head"),
    (up(E, "WO-1002"), "error: WO-1002 is not Queued", "move_up: not Queued"),
]
for got, want, msg in cases:
    check(got == want, "%s -> %s" % (msg, got))
check(seqs(E) == {"WO-100%d" % i: i for i in range(1, 6)} and not E.overrides_,
      "no refused action changed a sequence or wrote an override")


# ---------- 4. tags ----------

print("\nTags:")
E, t = FakeDb(), FakeTags()
res = front(E, "WO-1003", "other", " Walker ", t)
check([n for n, _ in t.writes] == ["Override", "OverrideActive"] and t.last("OverrideActive") is True,
      "override writes Schedule/Override then OverrideActive = true")
payload = json.loads(t.last("Override"))
print("  " + t.last("Override"))
check(sorted(payload) == sorted(["work_order", "product_code", "from_sequence", "to_sequence", "reason", "operator",
                                 "timestamp"]), "payload keys exactly as rule 4")
check(payload == {"work_order": "WO-1003", "product_code": 103, "from_sequence": 3, "to_sequence": 1,
                  "reason": "other", "operator": "Walker", "timestamp": sch["_iso_ms"](T0)},
      "payload values (operator trimmed, timestamp = entered_at)")
check(payload["timestamp"] == "2026-10-14T17:46:40.000Z", "timestamp ISO 8601 UTC with ms")
check(res.endswith("; tag Override write Good; tag OverrideActive write Good"), "write qualities in the result")
E, t = FakeDb(), FakeTags(bad=("Override",))
res = front(E, "WO-1003", tags=t)
check(res.startswith("ok") and "tag Override write Bad_Failure" in res and E.overrides_,
      "bad write quality: reported, the override stands")


# ---------- 5. reconcile edge cases ----------

print("\nReconcile edge cases:")
db, tags, logs = FakeDb(schedule=False), FakeTags(), []
drive(db, loop, tags, logs)
check(sum(l == "reconcile: schedule empty" for l in logs) == 5, "empty schedule: 'reconcile: schedule empty' x5")
check([v for n, v in tags.writes if n == "ReconcileResult"] == [""] * 5, "empty schedule: ReconcileResult ''")

db, tags, logs = FakeDb(), FakeTags(), []
for w in ("WO-1001", "WO-1006"):
    db.orders[w]["status"] = "Planned"  # no Released 101: the run is unattributed
drive(db, loop, tags, logs)
check(any(l == "reconcile: product=101 head=WO-1001 (101): Normal start; run unattributed, not stored"
          for l in logs) and results(db)["WO-1001"] is None, "unattributed run: log and tag only")

db, tags, logs = FakeDb(), FakeTags(), []
db.orders["WO-1001"]["status"] = "Planned"  # 101 attributed to WO-1006, which has no entry
drive(db, loop, tags, logs)
check(any(l == "reconcile: product=101 head=WO-1001 (101): Normal start; WO-1006 unscheduled, not stored"
          for l in logs) and tags.writes[0] == ("ReconcileResult", "Normal start"), "unscheduled order: log and tag only")

ents = ScheduleDb(FakeDb()).entries()
r = sch["reconcile"]
check(r(101, None, []) is None, "no head: None")
check(r(101.0, ents[0], []) == "Normal start", "product cast (101.0 == 101)")
check(r(102, ents[0], [{"work_order_id": "WO-1001"}]) == "Override not followed", "mismatch with an override")
check(r(102, ents[0], []) == "Unrecorded deviation", "mismatch, no override")
check(r(101, ents[0], [{"work_order_id": "WO-1001"}]) == "Authorized override", "match, override moved the head")
check(r(101, ents[0], [{"work_order_id": "WO-1004"}]) == "Normal start", "match, override for another order")

print("\n%s: %d failure(s)" % ("FAILED" if failures else "PASSED", len(failures)))
sys.exit(1 if failures else 0)
