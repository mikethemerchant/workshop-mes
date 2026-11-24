"""Shared helpers for the offline replays: the sim sample builder and a stub `system`.

Used by replay_engine.py (ENG-001), replay_workorder.py (WO-001) and test_workorder_actions.py (WO-003).
Standard library only.
"""

import copy
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "ignition/projects/mes-2026/ignition/script-python"
ENGINE = SCRIPTS / "mes/engine/code.py"
WORKORDER = SCRIPTS / "mes/workorder/code.py"
SIM = REPO / "docs/sim/fourzero_printing_press1.json"

# topic -> (engine key, cast), as UNS-002 types them
TOPICS = {
    "PLC/Connected": ("conn", bool),
    "PLC/MachineState": ("state", int),
    "PLC/FaultCode": ("fault", int),
    "PLC/ProductCode": ("product", int),
    "PLC/WebLanes": ("lanes", int),
    "PLC/GoodMeters": ("good", int),
    "PLC/RejectMeters": ("reject", int),
    "PLC/RTOInhibit": ("rto", bool),
    "Unwind/WebTension": ("tenU", float),
    "Rewind/WebTension": ("tenR", float),
    "Print Section/LineSpeed": ("speed", float),
    "Print Section/RegistrationError": ("reg", float),
    "Print Section/InkViscosity": ("visc", float),
    "Tunnel Dryer/Zone1Temp": ("z1", float),
    "Tunnel Dryer/Zone2Temp": ("z2", float),
    "Tunnel Dryer/Zone3Temp": ("z3", float),
}
T0 = 1_792_000_000_000  # arbitrary epoch ms


# ---------- stub `system` ----------

class _Log:
    def info(self, *a): pass
    def warn(self, *a): pass
    def error(self, *a): pass


class _Util:
    def __init__(self):
        self.globals = {}

    def getLogger(self, name):
        return _Log()

    def getGlobals(self):
        return self.globals


class _System:
    def __init__(self):
        self.util = _Util()


def load(path, name):
    """Exec an Ignition script module with a fresh stub `system`; returns its namespace."""
    ns = {"system": _System(), "__name__": name}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
    return ns


# ---------- sample stream from the sim definition ----------

def load_sim():
    return json.loads(SIM.read_text(encoding="utf-8"))


def loop_samples(sim, counters, t):
    """One loop of samples. counters carries cumulative counter values across phases and loops."""
    out = []
    for ph in sim["phases"]:
        n = ph["duration_seconds"] // ph["interval_seconds"]
        topics = ph["groups"][0]["topics"]
        for i in range(n):
            s = {"t": t, "_phase": ph["name"], "_first": i == 0, "_dur": ph["duration_seconds"]}
            for tp in topics:
                key, cast = TOPICS[tp["topic"]]
                g = tp["generator"]
                if g["type"] == "static":
                    v = g["value"]
                elif g["type"] == "counter":
                    counters.setdefault(key, g["start"])
                    counters[key] += g["step"]
                    v = counters[key]
                else:
                    v = g["start"]
                s[key] = cast(v)
            out.append(s)
            t += ph["interval_seconds"] * 1000
    return out, t


# ---------- FakeDb: the SQL of mes.workorder._SqlDb on an in-memory DB-002 seed ----------

SEED_RELEASED = T0 - 3_600_000  # every seed released_at is the same now()
SEED = [  # (work_order_id, product_code, planned_meters, status)
    ("WO-1001", 101, 600, "Released"), ("WO-1002", 102, 375, "Released"),
    ("WO-1003", 103, 450, "Released"), ("WO-1004", 104, 525, "Released"),
    ("WO-1005", 105, 365, "Released"), ("WO-1006", 101, 600, "Released"),
    ("WO-1007", 102, 375, "Released"), ("WO-1008", 103, 450, "Released"),
    ("WO-1009", 104, 525, "Released"), ("WO-1010", 105, 365, "Planned"),
]


class FakeDb:
    def __init__(self):
        self.orders = {}
        for wid, product, planned, status in SEED:
            self.orders[wid] = {"work_order_id": wid, "product_code": product, "planned_meters": planned,
                                "status": status, "released_at": SEED_RELEASED if status == "Released" else None,
                                "started_at": None, "completed_at": None}
        self.runs = []          # rows of mes.work_order_run, in run_id order
        self.writes = 0         # INSERT / UPDATE statements that changed a row
        self.fail_updates = 0   # update_run raises this many more times
        self.now = T0           # now() of the action SQL (WO-003)
        self.seq = 1011         # mes.work_order_seq after the seed

    def run(self, run_id):
        return next(r for r in self.runs if r["run_id"] == run_id)

    def next_released(self, product):
        c = [o for o in self.orders.values() if o["product_code"] == product and o["status"] == "Released"]
        c.sort(key=lambda o: (o["released_at"], o["work_order_id"]))
        return c[0]["work_order_id"] if c else None

    def start_run(self, wid, product, t, good_counter, reject_counter):
        o = self.orders[wid]
        if o["status"] == "Released":
            o["status"] = "Running"
            o["started_at"] = o["started_at"] or t
            self.writes += 1
        assert wid is not None, "work_order_run.work_order_id is NOT NULL"
        run_id = len(self.runs) + 1
        self.runs.append({"run_id": run_id, "work_order_id": wid, "product_code": product, "start_ts": t,
                          "end_ts": None, "good_meters": 0, "reject_meters": 0, "attributed_by": "auto",
                          "start_good_counter": good_counter, "start_reject_counter": reject_counter,
                          "counter_reset_seen": False})
        self.writes += 1
        return run_id

    def update_run(self, run_id, good, reject, reset_seen):
        if self.fail_updates:
            self.fail_updates -= 1
            raise RuntimeError("FakeDb: update_run failed (injected)")
        r = self.run(run_id)
        r["good_meters"], r["reject_meters"], r["counter_reset_seen"] = int(round(good)), int(round(reject)), bool(reset_seen)
        self.writes += 1

    def end_run(self, run_id, t, good, reject):
        r = self.run(run_id)
        assert t >= r["start_ts"], "end_ts >= start_ts"
        r["end_ts"], r["good_meters"], r["reject_meters"] = t, int(round(good)), int(round(reject))
        self.writes += 1

    def complete(self, wid, t):
        o = self.orders[wid]
        if o["status"] != "Running":
            return None
        o["status"], o["completed_at"] = "Complete", t
        self.writes += 1
        return o["planned_meters"]

    def open_runs(self):
        rs = sorted([r for r in self.runs if r["end_ts"] is None], key=lambda r: r["start_ts"], reverse=True)
        return [{k: r[k] for k in ("run_id", "work_order_id", "product_code", "good_meters", "reject_meters")}
                for r in rs]

    # ----- WO-003 manual actions: the SQL of the _SqlDb action methods -----

    def atomic(self, fn):
        """One transaction: keep the changes on an 'ok' result, roll back otherwise."""
        saved = copy.deepcopy((self.orders, self.runs, self.seq, self.writes))
        try:
            res = fn(self)
        except Exception:
            self.orders, self.runs, self.seq, self.writes = saved
            raise
        if not res.startswith("ok"):
            self.orders, self.runs, self.seq, self.writes = saved
        return res

    def product_exists(self, product):
        return product in (101, 102, 103, 104, 105)

    def insert_order(self, product, planned, due, priority, notes):
        wid = "WO-%d" % self.seq
        self.seq += 1
        self.orders[wid] = {"work_order_id": wid, "product_code": product, "planned_meters": planned,
                            "status": "Planned", "released_at": None, "started_at": None, "completed_at": None,
                            "due_date": due, "priority": priority, "notes": notes, "hold_reason": None,
                            "closed_at": None}
        self.writes += 1
        return wid

    def order(self, wid):
        o = self.orders.get(wid)
        return None if o is None else {k: o[k] for k in ("work_order_id", "product_code", "status")}

    def transition(self, wid, expected, action, reason):
        o = self.orders[wid]
        if o["status"] != expected:
            return 0
        if action == "release":
            o["status"], o["hold_reason"] = "Released", None
            o["released_at"] = o["released_at"] or self.now
        elif action == "hold":
            o["status"], o["hold_reason"] = "On Hold", reason
        elif action == "cancel":
            o["status"] = "Cancelled"
        elif action == "close":
            o["status"], o["closed_at"] = "Closed", self.now
        self.writes += 1
        return 1

    def run_row(self, run_id):
        r = next((r for r in self.runs if r["run_id"] == run_id), None)
        return None if r is None else {"run_id": r["run_id"], "work_order_id": r["work_order_id"],
                                       "product_code": r["product_code"], "start_ts": r["start_ts"], "end_ts": r["end_ts"]}

    def move_run(self, run_id, from_wid, to_wid):
        r = self.run(run_id)
        if r["work_order_id"] != from_wid:
            return 0
        r["work_order_id"], r["attributed_by"] = to_wid, "manual"
        self.writes += 1
        return 1

    def insert_run(self, wid, product, start, end, good, reject, reset_seen):
        assert end is None or end >= start, "end_ts >= start_ts"
        self.runs.append({"run_id": len(self.runs) + 1, "work_order_id": wid, "product_code": product,
                          "start_ts": start, "end_ts": end, "good_meters": int(round(good)),
                          "reject_meters": int(round(reject)), "attributed_by": "manual",
                          "start_good_counter": None, "start_reject_counter": None,
                          "counter_reset_seen": bool(reset_seen)})
        self.writes += 1

    def run_at(self, product, start):
        return next((r["run_id"] for r in self.runs if r["product_code"] == product and r["start_ts"] == start), None)

    def run_times(self, wid):
        return [(r["start_ts"], r["end_ts"]) for r in self.runs if r["work_order_id"] == wid]

    def set_from_runs(self, wid, expected, status, started, completed):
        o = self.orders[wid]
        if o["status"] != expected:
            return 0
        o["status"], o["started_at"], o["completed_at"] = status, started, completed
        self.writes += 1
        return 1

