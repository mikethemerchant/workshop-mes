#!/usr/bin/env python3
"""Offline replay of the Press 1 sim through mes.engine, mes.workorder and mes.oee (OEE-001).

Builds the 2 s sample stream from docs/sim/fourzero_printing_press1.json and feeds two full loops
(plus the first two loop-3 samples, so the last loop-2 interval closes) through engine.step() ->
workorder.handle() -> oee.handle(), as engine.tick() dispatches them, against the WO-001 FakeDb
(DB-002 seed) and a fake mes.oee_interval table with upsert semantics. The second loop starts on a
minute boundary, so its rows are the rows from that minute on; they are aggregated per product with
the v_oee_by_product formulas and checked against the MES section 6 table (+- 0.1 point) and the loop
totals. Then: the same replay off the minute grid (minute splits conserve the totals), a reload
mid-minute, a gap, a sim restart, a failed write and a failed load.
Never calls tick() or on_tick().

Run from the repo root: python3 -I ignition/tests/replay_oee.py
Exits non-zero on any failed assertion. Standard library only.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from simreplay import ENGINE, SCRIPTS, T0, WORKORDER, FakeDb, load, load_sim, loop_samples

OEE = SCRIPTS / "mes/oee/code.py"
MIN = 60000
LOOP_MS = 1046000

failures = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        failures.append(msg)


eng = load(ENGINE, "mes.engine")
wo = load(WORKORDER, "mes.workorder")
oee = load(OEE, "mes.oee")
step = eng["step"]

# DB-002 seed: product_code -> (ideal_speed_mpm, scheduled_changeover_s)
MASTER = {101: (250, 20), 102: (180, 30), 103: (200, 24), 104: (220, 20), 105: (160, 30)}
NUMS = ("running_s", "planned_production_s", "good_m", "reject_m", "ideal_m")


def engine_store():
    eng["system"].util.globals.clear()
    return eng["_store"]()


class OeeDb:
    """mes.oee._SqlDb on a fake mes.oee_interval table: upsert on the primary key, with the table's checks."""

    def __init__(self):
        self.rows = {}          # (interval_start, product_code) -> row
        self.lowered = []       # upserts that lowered a value already in the table
        self.upserts = 0
        self.fail_upserts = 0   # upsert raises this many more times
        self.fail_loads = 0     # load raises this many more times

    def product_master(self):
        return [(p, float(v[0]), v[1]) for p, v in MASTER.items()]

    def load(self, interval_start, product):
        if self.fail_loads:
            self.fail_loads -= 1
            raise RuntimeError("OeeDb: load failed (injected)")
        r = self.rows.get((interval_start, product))
        return None if r is None else dict(r)

    def upsert(self, r):
        if self.fail_upserts:
            self.fail_upserts -= 1
            raise RuntimeError("OeeDb: upsert failed (injected)")
        assert r["product_code"] in MASTER, "product_code references product_master"
        assert r["running_s"] >= 0 and r["planned_production_s"] >= 0, "running_s, planned_production_s >= 0"
        assert r["interval_start"] % MIN == 0, "interval_start on a minute"
        assert r["running_s"] <= 60 and r["planned_production_s"] <= 60, "at most 60 s per minute"
        assert r["running_s"] <= r["planned_production_s"], "running is part of planned production"
        k = (r["interval_start"], r["product_code"])
        old = self.rows.get(k)
        if old is not None and any(r[f] < old[f] for f in NUMS):
            self.lowered.append((old, dict(r)))
        self.rows[k] = dict(r)
        self.upserts += 1


class Rig:
    """One gateway: engine, work-order and OEE stores and their databases."""

    def __init__(self):
        self.est, self.wst, self.ost = engine_store(), wo["_new_store"](), oee["_new_store"]()
        self.db, self.odb = FakeDb(), OeeDb()
        self.logs = []
        self.max_buckets = 0

    def feed(self, samples):
        for s in samples:
            n = self.est["samples"]
            evs = step(self.est, s)
            if self.est["samples"] == n:
                continue
            last = dict(self.est["last"])
            self.logs += wo["handle"](self.wst, last, [dict(e) for e in evs], self.db)
            run = self.wst["run"]
            self.logs += oee["handle"](self.ost, last, [dict(e) for e in evs], self.odb,
                                       run["work_order_id"] if run else None)
            self.max_buckets = max(self.max_buckets, len(self.ost["buckets"]))


def aggregate(rows):
    """v_oee_by_product over rows: product -> (A, P, Q, OEE) in percent, plus the sums; and the loop."""
    sums = {}
    for r in rows:
        s = sums.setdefault(r["product_code"], dict.fromkeys(NUMS, 0))
        for f in NUMS:
            s[f] += r[f]
    tot = dict.fromkeys(NUMS, 0)
    for s in sums.values():
        for f in NUMS:
            tot[f] += s[f]
    sums["Loop"] = tot

    def pct(s):
        a = s["running_s"] / s["planned_production_s"]
        p = min(1, s["good_m"] / s["ideal_m"])
        q = s["good_m"] / (s["good_m"] + s["reject_m"])
        return 100 * a, 100 * p, 100 * q, 100 * a * p * q

    return {k: pct(s) for k, s in sums.items()}, sums


def replay(t0, reload_at=None):
    sim = load_sim()
    counters = {}
    l1, t = loop_samples(sim, counters, t0)
    l2, t = loop_samples(sim, counters, t)
    l3, t = loop_samples(sim, counters, t)
    rig = Rig()
    for i, s in enumerate(l1 + l2 + l3[:2]):
        if i == reload_at:
            rig.ost = oee["_new_store"]()  # Scan File System / gateway restart: the OEE store is empty
        rig.feed([s])
    return rig, l1, l2


# ---------- two full loops, loop 2 on a minute boundary ----------

L2 = ((T0 + LOOP_MS) // MIN + 1) * MIN
t0 = L2 - LOOP_MS
rig, loop1, loop2 = replay(t0)
assert loop2[0]["t"] == L2
print("Sim: two loops + 2 samples, loop 2 starts %d (a minute)" % L2)

rows2 = [r for k, r in sorted(rig.odb.rows.items()) if k[0] >= L2]
got, sums = aggregate(rows2)
print("\nLoop 2, v_oee_by_product formulas:")
for k in (101, 102, 103, 104, 105, "Loop"):
    s = sums[k]
    print("  %-4s A %5.1f P %5.1f Q %5.1f OEE %5.1f   running %6.1f planned %6.1f good %4d reject %3d ideal %7.1f"
          % ((k,) + got[k] + tuple(s[f] for f in NUMS)))

MES6 = {101: (83.3, 96.0, 97.1, 77.7), 102: (77.3, 83.3, 95.4, 61.5), 103: (83.3, 90.0, 96.2, 72.1),
        104: (77.3, 95.5, 96.7, 71.4), 105: (81.1, 93.8, 81.5, 62.0), "Loop": (80.4, 92.1, 93.7, 69.4)}
print("\nMES section 6 table:")
for k, want in MES6.items():
    have = tuple(round(v, 1) for v in got[k])
    check(all(abs(h - w) <= 0.1 for h, w in zip(have, want)), "%s A/P/Q/OEE %s = %s (+- 0.1)" % (k, have, want))

print("\nLoop totals:")
tot = sums["Loop"]
check(tot["running_s"] == 746.0, "running_s 746 s (%g)" % tot["running_s"])
check(tot["planned_production_s"] == 928.0, "planned_production_s 928 s = 746 + 154 alarm + 28 overrun (%g)"
      % tot["planned_production_s"])
check(tot["good_m"] == 2315 and tot["reject_m"] == 155, "good 2315, reject 155 (%d, %d)" % (tot["good_m"], tot["reject_m"]))
over = {p: sums[p]["planned_production_s"] - sums[p]["running_s"] for p in MASTER}
check(over == {101: 30.0, 102: 44.0, 103: 30.0, 104: 44.0, 105: 34.0},
      "planned - running per product = alarm + overrun: 102 and 104 carry 14 s overrun (%s)" % over)
check(all(abs(sums[p]["ideal_m"] - sums[p]["running_s"] / 60 * MASTER[p][0]) <= 0.05 * len(rows2) for p in MASTER),
      "ideal_m = running_s / 60 x ideal speed per product")
check(all(r["running_s"] <= r["planned_production_s"] <= 60 for r in rows2), "each row: running <= planned <= 60 s")
starts = sorted(set(r["interval_start"] for r in rows2))
check(len(starts) == 18 and starts[-1] < L2 + LOOP_MS, "loop 2: rows in 18 minutes (%d)" % len(starts))
check(all(sum(r["planned_production_s"] for r in rows2 if r["interval_start"] == m) <= 60 for m in starts),
      "per minute, all products: planned production <= 60 s")

# Work orders: running buckets carry the open run's order; job 105 in loop 2 is unattributed
JOB_WO = {101: "WO-1006", 102: "WO-1007", 103: "WO-1008", 104: "WO-1009", 105: None}
check(all(r["work_order_id"] == JOB_WO[r["product_code"]] for r in rows2 if r["running_s"] > 0),
      "loop 2: work_order_id of every row with running time = the run's order (105 None)")
rows1 = [r for k, r in rig.odb.rows.items() if k[0] < L2 - MIN]
check(set(r["work_order_id"] for r in rows1 if r["product_code"] == 105 and r["running_s"] > 0) == {"WO-1005"},
      "loop 1: job 105 rows on WO-1005")
check(not rig.odb.lowered, "no upsert lowered a value (%d)" % len(rig.odb.lowered))
check(rig.max_buckets <= 6, "memory: at most 3 minutes of buckets (max %d)" % rig.max_buckets)
check(sum(line.startswith("minute ") for line in rig.logs) >= 2 * 17, "one 'minute ...' log line per closed bucket")


# ---------- minute splits conserve the totals ----------

print("\nMinute splits:")
off, l1, l2 = replay(T0 + 1234)  # off the minute grid by 41.234 s
all_aligned = aggregate(rig.odb.rows.values())[1]["Loop"]
all_off = aggregate(off.odb.rows.values())[1]["Loop"]
check(all_off["running_s"] == all_aligned["running_s"] == 2 * 746.0
      and all_off["planned_production_s"] == all_aligned["planned_production_s"] == 2 * 928.0
      and all_off["good_m"] == all_aligned["good_m"] == 2 * 2315 and all_off["reject_m"] == 2 * 155,
      "two loops off the grid: rows sum to 2 x 746 / 928 s, 2 x 2315 / 155 m (%s)" % all_off)
check(any(0 < r["running_s"] < 60 and r["running_s"] * 10 % 20 for r in off.odb.rows.values()),
      "off the grid: some rows hold a split, not whole 2 s intervals")

# In memory the split is exact to the millisecond: one 2 s interval with an odd offset across a boundary
st = oee["_new_store"]()
m0 = 30 * MIN * 1000
prev = {"t": m0 - 1234, "state": 1, "product": 101}
cur = {"t": m0 + 766, "state": 1, "product": 101, "dg": 8.0, "dr": 0.0}
ch = oee["accumulate"](st, prev, cur, [], lambda p: 20, lambda p: 250.0, "WO-X")
b0, b1 = st["buckets"][(m0 - MIN, 101)], st["buckets"][(m0, 101)]
check(b0["run_ms"] == 1234 and b1["run_ms"] == 766 and b0["pp_ms"] + b1["pp_ms"] == 2000,
      "2 s across a boundary: 1234 ms + 766 ms, exact (%d, %d)" % (b0["run_ms"], b1["run_ms"]))
check(abs(b0["ideal"] + b1["ideal"] - 2 / 60 * 250) < 1e-9, "ideal split by time, sum = 2 s of ideal")
check(b0["good"] == 0 and b1["good"] == 8, "meters go to the minute containing t")
check(len(ch) == 2 and b0["work_order_id"] == b1["work_order_id"] == "WO-X", "both buckets changed, with the run's order")


# ---------- reload mid-minute ----------

print("\nReload:")
mid = next(i for i, s in enumerate(loop2) if s["_phase"] == "J103 Run 2" and (s["t"] - L2) % MIN == 30000)
rl, _, _ = replay(t0, reload_at=len(loop1) + mid)
k = ((loop2[mid]["t"]) // MIN * MIN, 103)
check(any(line.startswith("continued") for line in rl.logs), "the bucket open at the reload is continued from the table")
check(not rl.odb.lowered, "reload: no upsert lowered a value (%d)" % len(rl.odb.lowered))
a, b = rig.odb.rows[k], rl.odb.rows[k]
check(b["running_s"] == a["running_s"] - 2 and b["good_m"] == a["good_m"] - 6,
      "reload: that minute loses only the first sample after the reload (2 s, 6 m) (%g s, %d m)"
      % (a["running_s"] - b["running_s"], a["good_m"] - b["good_m"]))
others = [kk for kk in rig.odb.rows if kk != k]
check(all(rl.odb.rows[kk] == rig.odb.rows[kk] for kk in others), "reload: every other row equal to the uninterrupted replay")


# ---------- gap, sim restart ----------

def sample(ts, state=1, product=101, good=0, reject=0, fault=0):
    return {"t": T0 + ts * 1000, "state": state, "fault": fault, "product": product, "lanes": 4,
            "good": good, "reject": reject, "rto": False, "conn": True}


print("\nGap:")
g = Rig()
ss = [sample(2 * i, state=2) for i in range(3)] + [sample(6 + 2 * i, good=8 * (i + 1)) for i in range(5)]
ss += [sample(46 + 2 * i, good=8 * (i + 7)) for i in range(5)]  # 30 s without samples
g.feed(ss)
tot = aggregate(g.odb.rows.values())[1]["Loop"]
check(any(e["type"] == "gap" for e in g.est["events"]), "engine emitted gap")
check(tot["running_s"] == 16.0 and tot["planned_production_s"] == 16.0,
      "a gap adds no time: 4 + 4 intervals of 2 s, not the 30 s across the gap (%g)" % tot["running_s"])

print("\nSim restart:")
r = Rig()
ss = [sample(2 * i, state=2, good=900, reject=50) for i in range(3)]
ss += [sample(6 + 2 * i, good=900 + 8 * (i + 1), reject=50) for i in range(5)]
ss += [sample(16 + 2 * i, good=8 * (i + 1), reject=0) for i in range(5)]  # counters back to 0
r.feed(ss)
tot = aggregate(r.odb.rows.values())[1]["Loop"]
check(any(e["type"] == "restart" for e in r.est["events"]), "engine emitted restart")
check(all(x["good_m"] >= 0 and x["reject_m"] >= 0 for x in r.odb.rows.values()) and tot["reject_m"] == 0,
      "no negative meters")
check(tot["good_m"] == 40 + 32, "good = 5 x 8 before + 4 x 8 after the restart (%d)" % tot["good_m"])


# ---------- failed write, failed load ----------

print("\nFailures:")
f = Rig()
ss = [sample(2 * i, state=2) for i in range(3)] + [sample(6 + 2 * i, good=8 * (i + 1)) for i in range(6)]
f.feed(ss[:6])
f.odb.fail_upserts = 1
f.feed(ss[6:7])
check(any(line.startswith("write failed") for line in f.logs), "failed upsert: logged")
f.feed(ss[7:])
tot = aggregate(f.odb.rows.values())[1]["Loop"]
check(tot["running_s"] == 10.0 and tot["good_m"] == 48, "next tick heals it with absolute values (%g s, %d m)"
      % (tot["running_s"], tot["good_m"]))

f = Rig()
f.odb.rows[(oee["_minute"](T0 + 8000), 101)] = {"interval_start": oee["_minute"](T0 + 8000), "product_code": 101,
                                                "work_order_id": "WO-1001", "running_s": 20.0,
                                                "planned_production_s": 30.0, "good_m": 80, "reject_m": 2,
                                                "ideal_m": 83.3}
f.odb.fail_loads = 1
f.feed(ss[:5])
row = f.odb.rows[(oee["_minute"](T0 + 8000), 101)]
check(row["running_s"] == 20.0 + 2 and row["good_m"] == 80 + 8 + 8 and not f.odb.lowered,
      "failed load: nothing written until the load succeeds, then continued (%s)" % row)
check(row["planned_production_s"] == 32.0 and row["work_order_id"] == "WO-1001", "failed load: planned 32 s, order WO-1001")

print()
if failures:
    print("%d FAILED" % len(failures))
    sys.exit(1)
print("All checks passed.")
