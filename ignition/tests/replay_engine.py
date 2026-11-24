#!/usr/bin/env python3
"""Offline replay of the Press 1 sim through mes.engine.step() (ENG-001).

Builds the 2 s sample stream from docs/sim/fourzero_printing_press1.json, feeds two full
loops through step() on a fresh store, and checks the second loop's events against the
loop table (docs/sim_spec_press1.md §7-§9). Then runs six edge cases. Never calls tick().

Run from the repo root: python3 -I ignition/tests/replay_engine.py
Exits non-zero on any failed assertion. Standard library only.
"""

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from simreplay import ENGINE, T0, load, load_sim, loop_samples

failures = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        failures.append(msg)


# ---------- load the engine with a stub `system` ----------

ns = load(ENGINE, "mes.engine")


def fresh():
    ns["system"].util.globals.clear()
    return ns["_store"]()


step = ns["step"]


# ---------- sample stream from the sim definition ----------

sim = load_sim()
print("Sim: %s, %d phases, loop %d s" % (sim["name"], len(sim["phases"]),
                                          sum(p["duration_seconds"] for p in sim["phases"])))
counters = {}
loop1, t = loop_samples(sim, counters, T0)
loop2, t = loop_samples(sim, counters, t)

st = fresh()
for s in loop1:
    step(st, s)
g0, r0 = st["totals"]["good"], st["totals"]["reject"]
evs = []
for s in loop2:
    evs += step(st, s)
dgood = st["totals"]["good"] - g0
drej = st["totals"]["reject"] - r0

# phase-start t -> (phase name, duration) over both loops, to match each segment to its phase
phase_at = {s["t"]: (s["_phase"], s["_dur"]) for s in loop1 + loop2 if s["_first"]}

types = Counter(e["type"] for e in evs)
closes = [e for e in evs if e["type"] == "segment_close"]
print("\nSecond loop: %d samples, events %s" % (len(loop2), dict(sorted(types.items()))))

print("\nFaulted segments (state 3):")
by_fault = defaultdict(list)
for e in closes:
    if e["state"] == 3:
        by_fault[e["fault"]].append(e["dur_s"])
for f in sorted(by_fault):
    print("  fault %d: %d segments, %s s = %.1f s" % (f, len(by_fault[f]), by_fault[f], sum(by_fault[f])))
check(sum(len(v) for v in by_fault.values()) == 15, "15 Faulted segment_close")
for f, n, tot in ((101, 9, 82), (102, 4, 48), (103, 2, 24)):
    check(len(by_fault[f]) == n and sum(by_fault[f]) == tot, "fault %d: %d segments, %d s" % (f, n, tot))

setups = [e["dur_s"] for e in closes if e["state"] == 2]
print("\nSetup segments (state 2): %s s = %.1f s" % (setups, sum(setups)))
check(len(setups) == 5 and sum(setups) == 146, "5 Setup segment_close, 146 s")
check(sorted(setups) == sorted([18, 44, 22, 34, 28]), "Setup durations 18, 44, 22, 34, 28")

runs = [e["dur_s"] for e in closes if e["state"] == 1]
print("\nRunning segments (state 1): %d, %.1f s" % (len(runs), sum(runs)))

bad = []
for e in closes:
    ph = phase_at.get(e["start"])
    if ph is None or e["dur_s"] != ph[1]:
        bad.append((e, ph))
check(not bad, "every segment_close dur_s equals its phase duration_seconds (%d segments)" % len(closes))
for e, ph in bad:
    print("    mismatch:", e, ph)

rs = [e["product"] for e in evs if e["type"] == "run_start"]
check(rs == [101, 102, 103, 104, 105], "run_start x5, products %s" % rs)
check(types["product_change"] == 5, "product_change x5 (got %d)" % types["product_change"])
check(types["restart"] == 0, "restart x0 (got %d)" % types["restart"])
check(types["gap"] == 0, "gap x0 (got %d)" % types["gap"])
print("\nMeter deltas over the second loop: good %.0f m, reject %.0f m" % (dgood, drej))
check(dgood == 2315, "good-meter deltas 2,315 m")
check(drej == 155, "reject-meter deltas 155 m")
seqs = [e["seq"] for e in evs]
check(seqs == list(range(seqs[0], seqs[0] + len(seqs))), "event seq strictly increasing, no gaps")


# ---------- edge cases ----------

def sample(t, state=1, fault=0, product=101, good=0, reject=0):
    return {"t": T0 + t * 1000, "state": state, "fault": fault, "product": product, "lanes": 4,
            "good": good, "reject": reject, "conn": True}


def feed(st, samples):
    out = []
    for s in samples:
        out.append(step(st, s))
    return out


print("\nEdge cases:")

# 1. Duplicate sample
st = fresh()
r = feed(st, [sample(0, good=10), sample(2, good=18), sample(2, good=26)])
check(r[2] == [] and st["totals"]["good"] == 8 and st["samples"] == 2,
      "duplicate sample: no events, no delta (totals good %s)" % st["totals"]["good"])

# 2. Sim restart mid-Run
st = fresh()
seen = []
r = feed(st, [sample(0, good=100, reject=5), sample(2, good=108, reject=5), sample(4, good=0, reject=0),
              sample(6, good=8, reject=0), sample(8, good=16, reject=1)])
restarts = [e for es in r for e in es if e["type"] == "restart"]
check(len(restarts) == 1 and restarts[0]["t"] == T0 + 4000, "sim restart: one restart event, on the drop sample")
check(st["totals"]["good"] == 24 and st["totals"]["reject"] == 1,
      "sim restart: delta 0 on the drop, totals good %s reject %s" % (st["totals"]["good"], st["totals"]["reject"]))

# 3. Gap of 30 s inside a Faulted segment
st = fresh()
r = feed(st, [sample(0), sample(2), sample(4, state=3, fault=101), sample(6, state=3, fault=101),
              sample(8, state=3, fault=101), sample(38, state=3, fault=101), sample(40, state=3, fault=101)])
flat = [e for es in r for e in es]
gaps = [e for e in flat if e["type"] == "gap"]
cl = [e for e in r[5] if e["type"] == "segment_close"]
check(len(gaps) == 1 and gaps[0]["gap_s"] == 30, "gap: one gap event (30 s)")
check(len(cl) == 1 and cl[0]["end"] == T0 + 8000 and cl[0]["dur_s"] == 4,
      "gap: open segment closed at the last sample before the gap (dur 4 s)")
opens_after = [e for e in r[5] if e["type"] == "segment_open"] + [e for e in r[6] if e["type"] == "segment_open"]
check(not [e for e in r[5] if e["type"] == "segment_open"] and len(opens_after) == 1
      and opens_after[0]["start"] == T0 + 38000, "gap: debounce restarts (new segment opens on the 2nd sample after)")

# 4. 1-sample Faulted blip inside Running
st = fresh()
r = feed(st, [sample(0), sample(2), sample(4), sample(6, state=3, fault=101), sample(8), sample(10)])
flat = [e for es in r for e in es]
check(not [e for e in flat if e["type"] == "segment_open" and e["state"] == 3], "1-sample blip: no Faulted segment opens")

# 5. Fault-code change inside Faulted
st = fresh()
r = feed(st, [sample(0), sample(2), sample(4, state=3, fault=101), sample(6, state=3, fault=101),
              sample(8, state=3, fault=102), sample(10, state=3, fault=102)])
cl = [e for e in r[4] if e["type"] == "segment_close"]
op = [e for e in r[5] if e["type"] == "segment_open"]
check(len(cl) == 1 and cl[0]["fault"] == 101 and cl[0]["dur_s"] == 4 and len(op) == 1 and op[0]["fault"] == 102
      and op[0]["start"] == T0 + 8000, "fault-code change 101 -> 102: close 101 (4 s), open 102 backdated")

# 6. Faulted -> Running is not a run start; Setup -> Running is
st = fresh()
r = feed(st, [sample(0, state=2), sample(2, state=2), sample(4), sample(6), sample(8, state=3, fault=101),
              sample(10, state=3, fault=101), sample(12), sample(14)])
starts = [e for es in r for e in es if e["type"] == "run_start"]
check(len(starts) == 1 and starts[0]["t"] == T0 + 4000, "Faulted -> Running: no run_start (only the Setup -> Running one)")

print("\n%s: %d failure(s)" % ("FAILED" if failures else "PASSED", len(failures)))
sys.exit(1 if failures else 0)
