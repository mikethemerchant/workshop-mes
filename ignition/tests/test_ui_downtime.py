"""Offline check for DT-004: mes.ui.shape_downtime, shape_class_totals and shape_now_line on hand-built rows.

Loads ignition/projects/mes-2026/ignition/script-python/mes/ui/code.py with the stub `system`
(simreplay.load) and checks the events table (blank code 0 and NULL work order, planned flag,
formatting), the class totals (ordering, the two summary rows, sums) on one loop's worth of events
(9 web break, 4 tunnel over-temp, 2 RTO inhibit, 5 Changeover, 2 Changeover overrun -> 82 / 48 / 24,
118 and 28 s), and the now-line. Standard library only.

    python3 -I ignition/tests/test_ui_downtime.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from simreplay import SCRIPTS, load

UI = SCRIPTS / "mes/ui/code.py"
NOW = 1_792_000_000_000
SEC = 1_000

failures = 0


def check(name, ok, detail=""):
    global failures
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  " + detail) if detail and not ok else ""))
    if not ok:
        failures += 1


def fmt(ms, pattern):
    """Fake formatter: seconds relative to NOW, tagged with the pattern."""
    return "%s@%+d" % (pattern, (ms - NOW) // SEC)


def ev(cls, dur, planned=False, source="plc", code=0, product=101, wo="WO-1001", start=0):
    return {"event_id": 1, "start_ts": NOW + start * SEC, "end_ts": NOW + int((start + dur) * SEC),
            "duration_s": dur, "class": cls, "is_planned": planned, "source": source,
            "fault_code": code, "product_code": product, "work_order_id": wo}


def group(events):
    """What class_totals' GROUP BY class, is_planned returns (in no particular order)."""
    g = {}
    for e in events:
        k = (e["class"], e["is_planned"])
        n, s = g.get(k, (0, 0.0))
        g[k] = (n + 1, s + e["duration_s"])
    return [{"class": c, "is_planned": p, "events": n, "total_s": s} for (c, p), (n, s) in g.items()]


ui = load(UI, "mes.ui")

# ---------- events table ----------
spec_order = ["start", "end", "duration", "class", "planned", "source", "code", "product", "wo"]
check("DOWNTIME_COLUMNS order is the spec's 9 columns",
      [k for k, _ in ui["DOWNTIME_COLUMNS"]] == spec_order, str(ui["DOWNTIME_COLUMNS"]))
rows = [
    ev("Web break", 9.04, code=101, start=-60),
    ev("Changeover", 30.0, planned=True, code=0, wo=None, start=-200),
    ev("Changeover overrun", 16.0, source="derived", code=0, wo=None, start=-170),
    {**ev("RTO inhibit", 0, code=103), "end_ts": None, "duration_s": None, "product_code": None},
]
out = ui["shape_downtime"](rows, fmt)
check("every row has keys in column order", all(list(r) == spec_order for r in out), str(list(out[0])))
check("input order kept (SQL sorts newest first)", [r["class"] for r in out] == [r["class"] for r in rows])
check("start / end HH:mm:ss", out[0]["start"] == "HH:mm:ss@-60" and out[0]["end"] == "HH:mm:ss@-51",
      "%s %s" % (out[0]["start"], out[0]["end"]))
check("duration rounded to 1 decimal", out[0]["duration"] == 9.0, repr(out[0]["duration"]))
check("fault code shown", out[0]["code"] == 101, repr(out[0]["code"]))
check("unplanned -> planned blank", out[0]["planned"] == "", repr(out[0]["planned"]))
check("planned -> 'yes'", out[1]["planned"] == "yes", repr(out[1]["planned"]))
check("code 0 -> blank", out[1]["code"] == "" and out[2]["code"] == "", "%r %r" % (out[1]["code"], out[2]["code"]))
check("NULL work order -> blank", out[1]["wo"] == "" and out[2]["wo"] == "", "%r %r" % (out[1]["wo"], out[2]["wo"]))
check("work order shown", out[0]["wo"] == "WO-1001", repr(out[0]["wo"]))
check("source shown", out[2]["source"] == "derived", repr(out[2]["source"]))
check("NULL end / duration / product -> blank",
      out[3]["end"] == "" and out[3]["duration"] == "" and out[3]["product"] == "", str(out[3]))

# ---------- class totals, one loop ----------
loop = (
    [ev("Web break", d, code=101) for d in (8, 10, 8, 10, 8, 10, 8, 10, 10)]           # 9 -> 82 s
    + [ev("Tunnel over-temp", 12, code=102) for _ in range(4)]                       # 4 -> 48 s
    + [ev("RTO inhibit", 12, code=103) for _ in range(2)]                            # 2 -> 24 s
    + [ev("Changeover", d, planned=True, wo=None) for d in (20, 30, 24, 20, 24)]     # 5 -> 118 s
    + [ev("Changeover overrun", 14, source="derived", wo=None) for _ in range(2)]    # 2 -> 28 s
)
grouped = group(loop)
grouped.reverse()  # input order must not matter
tot = ui["shape_class_totals"](grouped)
check("CLASS_TOTAL_COLUMNS order", [k for k, _ in ui["CLASS_TOTAL_COLUMNS"]] == ["class", "planned", "events", "total_s"])
check("every row has keys in column order", all(list(r) == ["class", "planned", "events", "total_s"] for r in tot))
want = [
    ("Changeover", "yes", 5, 118.0),
    ("Web break", "", 9, 82.0),
    ("Tunnel over-temp", "", 4, 48.0),
    ("Changeover overrun", "", 2, 28.0),
    ("RTO inhibit", "", 2, 24.0),
    ("All unplanned", "", 17, 182.0),
    ("All planned", "yes", 5, 118.0),
]
got = [(r["class"], r["planned"], r["events"], r["total_s"]) for r in tot]
check("loop totals: planned first, then total desc, then the two summary rows", got == want, str(got))
check("summary rows are last", [r["class"] for r in tot[-2:]] == ["All unplanned", "All planned"])
check("events and totals are int / float", all(isinstance(r["events"], int) and isinstance(r["total_s"], float) for r in tot))

odd = ui["shape_class_totals"]([{"class": "Web break", "is_planned": False, "events": 3, "total_s": 30.06},
                                {"class": "RTO inhibit", "is_planned": False, "events": 1, "total_s": 12.04}])
check("totals rounded to 1 decimal, sums from unrounded values",
      [(r["class"], r["events"], r["total_s"]) for r in odd]
      == [("Web break", 3, 30.1), ("RTO inhibit", 1, 12.0), ("All unplanned", 4, 42.1), ("All planned", 0, 0.0)],
      str(odd))
empty = ui["shape_class_totals"]([])
check("no events -> only the two summary rows at 0",
      [(r["class"], r["events"], r["total_s"]) for r in empty] == [("All unplanned", 0, 0.0), ("All planned", 0, 0.0)],
      str(empty))

# ---------- now-line ----------
line = ui["shape_now_line"]({"State": "Unplanned", "CurrentAlarm": 101, "Class": "Web break",
                             "ActiveSince": NOW - 6 * SEC, "DurationSec": 6.2}, fmt)
check("now-line, fault", line == "Unplanned · Web break (101) · since HH:mm:ss@-6 · 6.2 s", line)
line = ui["shape_now_line"]({"State": "Planned", "CurrentAlarm": 0, "Class": "Changeover",
                             "ActiveSince": NOW - 12 * SEC, "DurationSec": 12.0}, fmt)
check("now-line, changeover", line == "Planned · Changeover · since HH:mm:ss@-12 · 12.0 s", line)
line = ui["shape_now_line"]({"State": "Unplanned", "CurrentAlarm": 0, "Class": "Changeover overrun",
                             "ActiveSince": NOW - 32 * SEC, "DurationSec": 32.4}, fmt)
check("now-line, overrun (no code)", line == "Unplanned · Changeover overrun · since HH:mm:ss@-32 · 32.4 s", line)
line = ui["shape_now_line"]({"State": "Running", "CurrentAlarm": 0, "Class": "", "ActiveSince": None,
                             "DurationSec": 0.0}, fmt)
check("now-line, running", line == "Running", line)
line = ui["shape_now_line"]({"State": "", "CurrentAlarm": 0, "Class": "", "ActiveSince": None, "DurationSec": 0.0}, fmt)
check("now-line, tags never written -> Running", line == "Running", line)

print("\n%d failure(s)" % failures)
sys.exit(1 if failures else 0)
