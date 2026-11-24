#!/usr/bin/env python3
"""Offline replay of the Press 1 sim through mes.engine, mes.workorder and mes.downtime (DT-001, DT-002).

Builds the 2 s sample stream from docs/sim/fourzero_printing_press1.json and feeds two full loops
(from J101 Setup, plus the first two loop-3 samples so the last loop-2 fault closes) through
engine.step() -> workorder.handle() -> downtime.handle(), as engine.tick() dispatches them, against
the WO-001 FakeDb (DB-002 seed) and a fake mes.downtime_event table and Downtime/* tag table.
Checks the second loop's fault rows, their classes, durations, products and work orders, and the
tags per state. DT-002: the second loop's Setup split (Changeover / Changeover overrun rows, work
orders, the Planned -> Unplanned tag switch at the scheduled changeover). Then edge cases: an unknown
fault code, a fault-code change inside Faulted, an RTO fault without PLC/RTOInhibit, a gateway restart
mid-fault, an unknown product, a failed Setup insert, a DT-001 store without the DT-002 keys.
Never calls tick() or on_tick().

Run from the repo root: python3 -I ignition/tests/replay_downtime.py
Exits non-zero on any failed assertion. Standard library only.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from simreplay import ENGINE, SCRIPTS, T0, WORKORDER, FakeDb, load, load_sim, loop_samples

DOWNTIME = SCRIPTS / "mes/downtime/code.py"

failures = []
SETUP_CLASSES = ("Changeover", "Changeover overrun")
CHANGEOVER_S = {101: 20, 102: 30, 103: 24, 104: 20, 105: 30}  # DB-002 seed product_master


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        failures.append(msg)


eng = load(ENGINE, "mes.engine")
wo = load(WORKORDER, "mes.workorder")
dt = load(DOWNTIME, "mes.downtime")
step = eng["step"]


def engine_store():
    eng["system"].util.globals.clear()
    return eng["_store"]()


class EventDb:
    """mes.downtime._SqlDb on a fake mes.downtime_event table, with the table's checks."""

    CLASSES = ('Web break', 'Tunnel over-temp', 'RTO inhibit', 'Changeover', 'Changeover overrun',
               'Unclassified alarm')

    def __init__(self):
        self.rows = []
        self.products = dict(CHANGEOVER_S)
        self.lookups = []       # products looked up in product_master
        self.fail_inserts = 0   # insert_events raises this many more times

    def changeover_s(self, product):
        self.lookups.append(product)
        return self.products.get(product)

    def insert_events(self, rows):
        """One transaction: every row or none."""
        if self.fail_inserts:
            self.fail_inserts -= 1
            raise RuntimeError("EventDb: transaction failed (injected)")
        before = list(self.rows)
        try:
            return [self.insert_event(r) for r in rows]
        except Exception:
            self.rows = before
            raise

    def faults(self):
        return [r for r in self.rows if r["class"] not in SETUP_CLASSES]

    def setups(self):
        return [r for r in self.rows if r["class"] in SETUP_CLASSES]

    def insert_event(self, r):
        assert r["source"] in ("plc", "derived", "operator") and r["machine_state"] in (1, 2, 3)
        assert r["class"] in self.CLASSES and r["fault_code"] is not None
        assert (r["class"] == "Changeover") == r["is_planned"]
        assert r["end"] >= r["start"], "end_ts >= start_ts"
        row = dict(r, event_id=len(self.rows) + 1)
        self.rows.append(row)
        return row["event_id"]


class Rig:
    """One gateway: engine, work-order and downtime stores, the databases and the Downtime/* tags."""

    def __init__(self):
        self.est, self.wst, self.dst = engine_store(), wo["_new_store"](), dt["_new_store"]()
        self.db, self.edb = FakeDb(), EventDb()
        self.tags = {}      # Downtime/<name> -> value
        self.writes = []    # (t, {name: value}) per sample
        self.logs = []

    def restart(self):
        """Gateway restart: every getGlobals() store is empty again; the databases stay."""
        self.est, self.wst, self.dst = engine_store(), wo["_new_store"](), dt["_new_store"]()

    def feed(self, samples):
        for s in samples:
            n = self.est["samples"]
            evs = step(self.est, s)
            if self.est["samples"] == n:
                continue
            last = dict(self.est["last"])
            self.logs += wo["handle"](self.wst, last, [dict(e) for e in evs], self.db)
            run = self.wst["run"]
            lines, tags = dt["handle"](self.dst, last, [dict(e) for e in evs], self.edb,
                                       run["work_order_id"] if run else None)
            self.logs += lines
            self.tags.update(tags)
            self.writes.append((s["t"], dict(tags)))


def sample(ts, state=1, fault=0, product=101, rto=False):
    return {"t": T0 + ts * 1000, "state": state, "fault": fault, "product": product, "lanes": 4,
            "good": 0, "reject": 0, "rto": rto, "conn": True}


def series(ts, n, **kw):
    out = [sample(ts + 2 * i, **kw) for i in range(n)]
    return out, ts + 2 * n


# ---------- two full loops ----------

sim = load_sim()
counters = {}
loop1, t = loop_samples(sim, counters, T0)
loop2, t = loop_samples(sim, counters, t)
loop3, t = loop_samples(sim, counters, t)
samples = loop1 + loop2 + loop3[:2]
print("Sim: %s, two loops + 2 samples, %d samples" % (sim["name"], len(samples)))

rig = Rig()
tag_at = {}
for s in samples:
    rig.feed([s])
    tag_at[s["t"]] = dict(rig.tags)

print("\nRows:")
for r in rig.edb.rows:
    print("  %(event_id)2d %(class)-18s fault %(fault_code)d start %(start)d dur %(dur_s)5.1f "
          "product %(product_code)s wo %(work_order_id)s" % r)

print("\nTwo loops:")
L2 = loop2[0]["t"]
rows = [r for r in rig.edb.faults() if r["start"] >= L2]
check(len(rig.edb.faults()) == 30 and len(rows) == 15, "15 fault rows per loop (loop 1 %d, loop 2 %d)" % (
    len(rig.edb.faults()) - len(rows), len(rows)))


def by_class(cls):
    return [r for r in rows if r["class"] == cls]


for cls, n, total in (("Web break", 9, 82), ("Tunnel over-temp", 4, 48), ("RTO inhibit", 2, 24)):
    rs = by_class(cls)
    check(len(rs) == n and sum(r["dur_s"] for r in rs) == total,
          "loop 2: %s x %d, total %g s (%d, %g s)" % (cls, n, total, len(rs), sum(r["dur_s"] for r in rs)))

# Expected per fault phase of loop 2: (class, fault, product, duration, work order)
JOB_WO = {101: "WO-1006", 102: "WO-1007", 103: "WO-1008", 104: "WO-1009", 105: None}
CODE = {"101": ("Web break", 101), "102": ("Tunnel over-temp", 102), "103": ("RTO inhibit", 103)}
want = []
for s in loop2:
    if s["_first"] and s["state"] == 3:
        cls, code = CODE[s["_phase"].split("(")[1][:3]]
        want.append((s["t"], cls, code, s["product"], float(s["_dur"]), JOB_WO[s["product"]]))
got = [(r["start"], r["class"], r["fault_code"], r["product_code"], r["dur_s"], r["work_order_id"]) for r in rows]
check(got == want, "loop 2: each row's start, class, fault code, product, duration = phase length, work order")
check(all(r["work_order_id"] is None for r in rows if r["product_code"] == 105),
      "loop 2: job 105 faults unattributed (work_order_id None)")
check([r["work_order_id"] for r in rig.edb.faults() if r["start"] < L2 and r["product_code"] == 105] == ["WO-1005"] * 3,
      "loop 1: job 105 faults on WO-1005")
check(sorted(r["product_code"] for r in by_class("RTO inhibit")) == [102, 105], "RTO inhibit on jobs 102 and 105")
check(all(r["source"] == "plc" and r["machine_state"] == 3 and r["is_planned"] is False for r in rig.edb.faults()),
      "every fault row: source plc, machine_state 3, is_planned false")
check(not any("RTO inhibit without" in line for line in rig.logs), "no RTO cross-check warning on the nominal loops")
closes = [r["end"] for r in rig.edb.faults()]
check(all(any(s["t"] == e and s["state"] != 3 or s["t"] == e and s["_first"] for s in samples) for e in closes),
      "rows written at close: end_ts = the first sample after the fault")


# Tags per state, from the third sample of each phase (the debounce opens on the second).
def tags_ok():
    bad = []
    for s in samples:
        if s["_first"]:
            phase_start, k = s["t"], 0
        k += 1
        if k < 2:
            continue  # first sample of a phase: the previous segment closed, the new one not open yet
        tg = tag_at[s["t"]]
        dur = (s["t"] - phase_start) / 1000.0
        if s["state"] == 1:
            exp = {"State": "Running", "CurrentAlarm": 0, "Class": "", "ActiveSince": None, "DurationSec": 0.0}
        elif s["state"] == 3:
            exp = {"State": "Unplanned", "CurrentAlarm": s["fault"], "Class": dt["classify"](s["fault"]),
                   "ActiveSince": phase_start, "DurationSec": dur}
        elif dur <= CHANGEOVER_S[s["product"]]:
            exp = {"State": "Planned", "CurrentAlarm": 0, "Class": "Changeover", "ActiveSince": phase_start,
                   "DurationSec": dur}
        else:  # DT-002 rule 3: past the scheduled changeover
            exp = {"State": "Unplanned", "CurrentAlarm": 0, "Class": "Changeover overrun", "ActiveSince": phase_start,
                   "DurationSec": dur}
        if tg != exp:
            bad.append((s["_phase"], k, tg, exp))
    return bad


bad = tags_ok()
check(not bad, "tags per state (Running / Unplanned / Planned, Setup Unplanned past c) on every sample after the "
               "debounce %s" % (bad[:1],))
first_t = set(s["t"] for s in samples if s["_first"])
check(all(w == {} for t_, w in rig.writes if t_ in first_t),
      "first sample of each phase (segment closed, next not open): no tag write")
check(all("DurationSec" in w for t_, w in rig.writes if t_ not in first_t),
      "DurationSec written every tick while a segment is open")
running = [w for t_, w in rig.writes if t_ not in first_t and tag_at[t_]["State"] == "Running"]
check(sum("State" in w for w in running) == 30 and all(set(w) == {"DurationSec"} for w in running if "State" not in w),
      "Running: State etc. written once per Running segment (30), then only DurationSec")
check(tag_at[loop2[0]["t"]]["State"] == "Unplanned",
      "loop boundary: first sample of the next phase leaves the tags as they were")


# ---------- DT-002: Setup split, second loop ----------

print("\nSetup split (loop 2):")
setups = [r for r in rig.edb.setups() if r["start"] >= L2]
planned = [r for r in setups if r["class"] == "Changeover"]
over = [r for r in setups if r["class"] == "Changeover overrun"]
check([r["dur_s"] for r in planned] == [18.0, 30.0, 22.0, 20.0, 28.0] and sum(r["dur_s"] for r in planned) == 118,
      "5 Changeover rows 18/30/22/20/28 = 118 s (%s)" % [r["dur_s"] for r in planned])
check([(r["product_code"], r["dur_s"]) for r in over] == [(102, 14.0), (104, 14.0)],
      "2 Changeover overrun rows of 14 s, on products 102 and 104 (%s)" % [(r["product_code"], r["dur_s"]) for r in over])
setup_ph = [(s["t"], s["product"], s["_dur"]) for s in loop2 if s["_first"] and s["state"] == 2]
check(all(any(r["start"] == st_ + CHANGEOVER_S[p] * 1000 and r["product_code"] == p for r in over)
          for st_, p, d in setup_ph if d > CHANGEOVER_S[p]),
      "overrun start_ts = Setup start + scheduled changeover; ends at the Setup end")
per_job = {}
for r in setups:
    per_job[r["product_code"]] = per_job.get(r["product_code"], 0) + r["dur_s"]
check(per_job == {p: float(d) for _, p, d in setup_ph} and sum(per_job.values()) == 146,
      "Setup total per job = sim Setup length, 146 s (%s)" % per_job)
ok_span = True
for st_, p, d in setup_ph:
    rs = [r for r in setups if r["product_code"] == p]
    ok_span &= rs[0]["start"] == st_ and rs[-1]["end"] == st_ + d * 1000 and \
        all(a["end"] == b["start"] for a, b in zip(rs, rs[1:]))
check(ok_span, "each Setup's rows tile the Setup segment (start, contiguous, end)")
check([(r["product_code"], r["work_order_id"]) for r in planned] ==
      [(101, "WO-1006"), (102, "WO-1007"), (103, "WO-1008"), (104, "WO-1009"), (105, None)],
      "work order on each Setup = the order that ran next (105: None)")
check(all(a["work_order_id"] == b["work_order_id"] for a in planned for b in over if a["product_code"] == b["product_code"]),
      "overrun row carries the same work order as its planned row")
check(all(r["source"] == "plc" and r["is_planned"] is True and r["machine_state"] == 2 and r["fault_code"] == 0
          for r in planned) and
      all(r["source"] == "derived" and r["is_planned"] is False and r["machine_state"] == 2 and r["fault_code"] == 0
          for r in over), "Changeover: plc, planned; overrun: derived, unplanned; machine_state 2, fault_code 0")
check([r["work_order_id"] for r in rig.edb.setups() if r["start"] < L2 and r["class"] == "Changeover"] ==
      ["WO-1001", "WO-1002", "WO-1003", "WO-1004", "WO-1005"], "loop 1: Setups on WO-1001..WO-1005")
check(sorted(set(rig.edb.lookups)) == [101, 102, 103, 104, 105] and len(rig.edb.lookups) == 5,
      "product_master read once per product (cached) (%s)" % rig.edb.lookups)
switch = []
for st_, p, d in setup_ph:
    ts_ = [s["t"] for s in loop2 if st_ <= s["t"] < st_ + d * 1000]
    first_over = next((t_ for t_ in ts_ if tag_at[t_].get("Class") == "Changeover overrun"), None)
    switch.append((p, (first_over - st_) / 1000.0 if first_over else None))
check(switch == [(101, None), (102, 32.0), (103, None), (104, 22.0), (105, None)],
      "tags switch to Unplanned / Changeover overrun on the first sample past c (102 at 32 s, 104 at 22 s) %s" % switch)


# ---------- edge cases ----------

print("\nEdge cases:")

# 1. Unknown fault code 999 -> Unclassified alarm, never dropped
rig = Rig()
a, ts = series(0, 3, state=2)
b, ts = series(ts, 5)
c, ts = series(ts, 5, state=3, fault=999)
d, ts = series(ts, 3)
rig.feed(a + b + c + d)
r = rig.edb.faults()
check(len(r) == 1 and r[0]["class"] == "Unclassified alarm" and r[0]["fault_code"] == 999 and r[0]["dur_s"] == 10.0,
      "fault 999 -> one Unclassified alarm row, 10 s (%s)" % [(x["class"], x["fault_code"], x["dur_s"]) for x in r])
check(r and r[0]["work_order_id"] == "WO-1001", "fault 999: work order of the open run (WO-1001)")
check(rig.tags.get("State") == "Running" and rig.tags.get("CurrentAlarm") == 0, "back to Running: tags reset")

# 2. 101 -> 102 inside Faulted -> two rows
rig = Rig()
a, ts = series(0, 3, state=2)
b, ts = series(ts, 5)
c, ts = series(ts, 4, state=3, fault=101)
mid = [sample(ts + 4, state=3, fault=102)]
d, ts2 = series(ts, 6, state=3, fault=102)
e, ts2 = series(ts2, 3)
rig.feed(a + b + c)
rig.feed(d[:2])
during = dict(rig.tags)
rig.feed(d[2:] + e)
r = rig.edb.faults()
check([(x["class"], x["dur_s"]) for x in r] == [("Web break", 8.0), ("Tunnel over-temp", 12.0)],
      "101 -> 102 inside Faulted: two rows, Web break 8 s then Tunnel over-temp 12 s (%s)" % [
          (x["class"], x["dur_s"]) for x in r])
check(len(r) == 2 and r[0]["end"] == r[1]["start"], "the second row starts where the first ends")
check(during.get("Class") == "Tunnel over-temp" and during.get("CurrentAlarm") == 102,
      "tags follow the new fault code (%s)" % during)

# 3. RTO inhibit without PLC/RTOInhibit -> logged, row still written
rig = Rig()
a, ts = series(0, 3, state=2)
b, ts = series(ts, 5)
c, ts = series(ts, 6, state=3, fault=103, rto=False)
d, ts = series(ts, 3)
rig.feed(a + b + c + d)
check(any(line.startswith("RTO inhibit without PLC/RTOInhibit") for line in rig.logs),
      "103 with rto false: 'RTO inhibit without PLC/RTOInhibit' logged")
check([x["class"] for x in rig.edb.faults()] == ["RTO inhibit"], "103 with rto false: RTO inhibit row written")

# 4. Running segments write no rows; Setup segments write only Setup rows (DT-002)
rig = Rig()
a, ts = series(0, 10, state=2)
b, ts = series(ts, 10)
c, ts = series(ts, 10, state=2, product=102)
d, ts = series(ts, 3, product=102)
rig.feed(a + b + c + d)
check(rig.edb.faults() == [] and [(x["class"], x["dur_s"]) for x in rig.edb.rows] ==
      [("Changeover", 20.0), ("Changeover", 20.0)], "Running segments: no rows; Setups: one Changeover row each")

# 5. Gateway restart mid-fault: stores empty, no orphan, event recorded from the new backdated start
rig = Rig()
a, ts = series(0, 3, state=2)
b, ts = series(ts, 5)
c, ts = series(ts, 10, state=3, fault=101)
d, ts = series(ts, 3)
rig.feed(a + b + c[:4])
check(rig.edb.faults() == [] and rig.dst["fault"] is not None, "restart: fault open, no row yet")
rig.restart()
rig.feed(c[4:] + d)
r = rig.edb.faults()
check(len(r) == 1, "restart: one row for the fault, no orphan (%d)" % len(r))
check(r and r[0]["start"] == c[4]["t"] and r[0]["end"] == d[0]["t"] and r[0]["dur_s"] == 12.0,
      "restart: row from the new backdated start (first sample after the restart), 12 s")
check(r and r[0]["class"] == "Web break", "restart: class Web break")

# 6. Downtime store created mid-fault while the engine keeps its segment (first load)
rig = Rig()
rig.feed(a + b + c[:4])
rig.dst = dt["_new_store"]()
rig.feed(c[4:] + d)
r = rig.edb.faults()
check(len(r) == 1 and r[0]["start"] == c[0]["t"] and r[0]["dur_s"] == 20.0 and r[0]["work_order_id"] is None,
      "store created mid-fault: row from the engine's start, work order unknown (None)")
check(any(line.startswith("fault closed with no open event") for line in rig.logs),
      "store created mid-fault: logged")

# 7. A failed insert is logged and does not stop the tags
rig = Rig()
rig.edb.insert_event = lambda r: (_ for _ in ()).throw(RuntimeError("FakeDb: insert failed (injected)"))
rig.feed(a + b + c + d)
check(any(line.startswith("insert failed Web break") for line in rig.logs), "insert failure: logged")
check(rig.tags.get("State") == "Running", "insert failure: tags still follow the press")

# 8. Unknown product (no product_master row): changeover 0 s, the whole Setup is overrun, one warning
rig = Rig()
del rig.edb.products[104]
a, ts = series(0, 6, state=2, product=104)
b, ts = series(ts, 3, product=104)
a2, ts = series(ts, 4, state=2, product=104)
b2, ts = series(ts, 3, product=104)
rig.feed(a[:3])
during = dict(rig.tags)
rig.feed(a[3:] + b + a2 + b2)
r = rig.edb.setups()
check([(x["class"], x["dur_s"]) for x in r] == [("Changeover", 0.0), ("Changeover overrun", 12.0),
                                                ("Changeover", 0.0), ("Changeover overrun", 8.0)],
      "unknown product: overrun = the whole Setup (12 s, 8 s), planned row 0 s (%s)" % [(x["class"], x["dur_s"]) for x in r])
check(sum(line.startswith(dt["WARN"] + "no product_master row for product 104") for line in rig.logs) == 1,
      "unknown product: warned once")
check(during.get("State") == "Unplanned" and during.get("Class") == "Changeover overrun",
      "unknown product: tags Unplanned / Changeover overrun during the Setup (%s)" % during)

# 9. A failed Setup transaction writes neither row and is logged
rig = Rig()
rig.edb.fail_inserts = 1
a, ts = series(0, 20, state=2, product=102)
b, ts = series(ts, 3, product=102)
rig.feed(a + b)
check(rig.edb.rows == [] and any(line.startswith("insert failed Setup product=102") for line in rig.logs),
      "Setup insert failure: no rows (one transaction), logged")
check(rig.tags.get("State") == "Running", "Setup insert failure: tags still follow the press")

# 10. Short Setup (d <= c): one Changeover row, no overrun; split_setup is pure
rows_ = dt["split_setup"](T0, T0 + 18000, 18.0, 20)
check(len(rows_) == 1 and rows_[0]["end"] == T0 + 18000 and rows_[0]["dur_s"] == 18.0, "split_setup 18 s / 20 s: one row")
rows_ = dt["split_setup"](T0, T0 + 30000, 30.0, 30)
check(len(rows_) == 1, "split_setup 30 s / 30 s (d == c): no overrun")
rows_ = dt["split_setup"](T0, T0 + 44000, 44.0, 30)
check([(x["start"], x["end"], x["dur_s"]) for x in rows_] == [(T0, T0 + 30000, 30.0), (T0 + 30000, T0 + 44000, 14.0)],
      "split_setup 44 s / 30 s: 30 s planned + 14 s overrun at start + 30 s")

# 11. A DT-001 store (no changeover/unknown keys) survives the load: keys added, open fault kept
old = dt["_new_store"]()
del old["changeover"], old["unknown"]
old["fault"] = {"start": T0}
dt["system"].util.globals[dt["STORE_KEY"]] = old
st11 = dt["_store"]()
check(st11 is old and st11["changeover"] == {} and st11["unknown"] == [] and st11["fault"] == {"start": T0},
      "DT-001 store upgraded in place (open fault kept)")

print()
if failures:
    print("%d FAILED" % len(failures))
    sys.exit(1)
print("All checks passed.")
