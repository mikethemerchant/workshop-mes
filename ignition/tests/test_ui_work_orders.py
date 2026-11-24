"""Offline check for WO-002: mes.ui.shape_work_orders on hand-built rows.

Loads ignition/projects/mes-2026/ignition/script-python/mes/ui/code.py with the stub `system`
(simreplay.load) and checks lane-m, diff only on Complete, the late flag, None times and
column order. Standard library only.

    python3 ignition/tests/test_ui_work_orders.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from simreplay import SCRIPTS, load

UI = SCRIPTS / "mes/ui/code.py"
NOW = 1_792_000_000_000
MIN = 60_000

failures = 0


def check(name, ok, detail=""):
    global failures
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  " + detail) if detail and not ok else ""))
    if not ok:
        failures += 1


def fmt(ms, pattern):
    """Fake formatter: minutes relative to NOW, tagged with the pattern."""
    return "%s@%+d" % (pattern, (ms - NOW) // MIN)


def row(wo, status, good, planned, lanes, run_start=None, run_end=None, due=None, reject=0):
    return {"work_order_id": wo, "product_code": 101, "product_family": "Snack bag film",
            "status": status, "priority": "Normal", "planned_meters": planned, "good_m": good,
            "reject_m": reject, "lanes": lanes, "run_start": run_start, "run_end": run_end,
            "due_date": due}


ui = load(UI, "mes.ui")
rows = [
    row("WO-1001", "Complete", 603, 600, 4, NOW - 10 * MIN, NOW - 7 * MIN, NOW - 5 * MIN, reject=18),
    row("WO-1002", "Running", 120, 375, 2, NOW - 2 * MIN, None, NOW - 1 * MIN),
    row("WO-1003", "Released", 0, 450, 4, None, None, NOW + 30 * MIN),
    row("WO-1004", "Released", 0, 525, 2, None, None, None),
    row("WO-1005", "Cancelled", 0, 365, 2, None, None, NOW - 60 * MIN),
]
out = ui["shape_work_orders"](rows, NOW, fmt)
by = {r["wo"]: r for r in out}

# column order
spec_order = ["wo", "product", "status", "priority", "planned_m", "good_m", "reject_m", "lanes",
              "lane_m", "diff_m", "run_start", "run_end", "due"]
check("WORK_ORDER_COLUMNS order is the spec's 13 columns",
      [k for k, _ in ui["WORK_ORDER_COLUMNS"]] == spec_order, str(ui["WORK_ORDER_COLUMNS"]))
check("every row has keys in column order", all(list(r) == spec_order for r in out), str(list(out[0])))
check("one row per input row, input order kept", [r["wo"] for r in out] == [r["work_order_id"] for r in rows])

# values
check("product = code + family", by["WO-1001"]["product"] == "101 Snack bag film", by["WO-1001"]["product"])
check("lane-m = good x lanes (603 x 4)", by["WO-1001"]["lane_m"] == 2412, str(by["WO-1001"]["lane_m"]))
check("lane-m = good x lanes (120 x 2)", by["WO-1002"]["lane_m"] == 240, str(by["WO-1002"]["lane_m"]))
check("diff on Complete = good - planned", by["WO-1001"]["diff_m"] == 3, str(by["WO-1001"]["diff_m"]))
check("diff blank when Running", by["WO-1002"]["diff_m"] == "", repr(by["WO-1002"]["diff_m"]))
check("diff blank when Released", by["WO-1003"]["diff_m"] == "", repr(by["WO-1003"]["diff_m"]))
check("meters are int", all(isinstance(r[k], int) for r in out for k in ("planned_m", "good_m", "reject_m", "lanes", "lane_m")))

# late flag
check("due past + Running -> late", by["WO-1002"]["due"] == "HH:mm@-1 late", by["WO-1002"]["due"])
check("due past + Complete -> not late", by["WO-1001"]["due"] == "HH:mm@-5", by["WO-1001"]["due"])
check("due past + Cancelled -> not late", by["WO-1005"]["due"] == "HH:mm@-60", by["WO-1005"]["due"])
check("due future + Released -> not late", by["WO-1003"]["due"] == "HH:mm@+30", by["WO-1003"]["due"])

# times
check("run_start formatted HH:mm:ss", by["WO-1001"]["run_start"] == "HH:mm:ss@-10", by["WO-1001"]["run_start"])
check("run_end formatted HH:mm:ss", by["WO-1001"]["run_end"] == "HH:mm:ss@-7", by["WO-1001"]["run_end"])
check("None run_end -> ''", by["WO-1002"]["run_end"] == "", repr(by["WO-1002"]["run_end"]))
check("None run_start/run_end -> ''", by["WO-1003"]["run_start"] == "" and by["WO-1003"]["run_end"] == "")
check("None due -> ''", by["WO-1004"]["due"] == "", repr(by["WO-1004"]["due"]))

# open-run line and unattributed rows (also pure)
line = ui["shape_open_run"]({"work_order_id": "WO-1003", "product": 103, "start": NOW - MIN,
                             "good": 312.0, "reject": 18.0, "attributed": True}, fmt)
check("open-run line, attributed", line == "Running WO-1003 · 103 · good 312 m · reject 18 m · since HH:mm:ss@-1", line)
line = ui["shape_open_run"]({"work_order_id": None, "product": 105, "start": NOW, "good": 0.0,
                             "reject": 0.0, "attributed": False}, fmt)
check("open-run line, unattributed", line.startswith("Running UNATTRIBUTED · 105 · good 0 m"), line)
check("open-run line, none", ui["shape_open_run"](None, fmt) == "No open run")
un = ui["shape_unattributed"]([{"product": 105, "start": NOW - 3 * MIN, "end": NOW, "good": 365.0, "reject": 83.0}], fmt)
check("unattributed row", un == [{"product": 105, "start": "HH:mm:ss@-3", "end": "HH:mm:ss@+0", "good_m": 365, "reject_m": 83}], str(un))

print("\n%d failure(s)" % failures)
sys.exit(1 if failures else 0)
