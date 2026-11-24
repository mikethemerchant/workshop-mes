#!/usr/bin/env python3
"""Generate the UNS-004 functional and definitional tag folders for Ignition 8.3 (DEC-007).

Writes Ignition tag resource files for the 42 MES tags under
ignition/tags/default/40Printing/Dallas/Press Room/Press 1/
{Work Order,Schedule,Downtime,Planned Stop,OEE,Product}/.
A tag whose truth is a PostgreSQL record is a query tag on connection "mes";
a tag whose truth lives in gateway memory is a memory tag written only by the
MES logic. It never writes the edge folders or the Press 1 marker.

The repo copy is the source of truth; it is pushed into the gateway's
tag-definition/default/ folder. Output is deterministic: re-running gives
byte-identical files. Standard library only.

  --check  runs every query tag's query through psql in the db container.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "default"
PRESS = ("40Printing", "Dallas", "Press Room", "Press 1")
DATASOURCE = "mes"
DB_CONTAINER = "mes-workshop-2026-db-1"

PRODUCT_CODES = (101, 102, 103, 104, 105)
ACTIVE_WO = (
    "FROM mes.v_work_order_table WHERE status = 'Running' "
    "ORDER BY run_start DESC NULLS LAST LIMIT 1"
)
OPEN_STOP = "FROM mes.planned_stop WHERE end_ts IS NULL ORDER BY start_ts DESC LIMIT 1"


def marker(files):
    # Ignition ignores a folder's tags.json unless its marker lists it.
    return {
        "scope": "G",
        "version": 1,
        "restricted": False,
        "overridable": True,
        "files": files,
        "attributes": {"config": {}},
    }


def query_tag(name, data_type, eng_unit, query, rate, doc):
    t = {
        "name": name,
        "tagType": "AtomicTag",
        "valueSource": "db",
        "datasource": DATASOURCE,
        "queryType": "Select",
        "query": query,
        # Without FixedRate a db tag is EventDriven and runs once at gateway start.
        "executionMode": "FixedRate",
        "executionRate": rate,
        "dataType": data_type,
        "documentation": doc,
        "readOnly": True,
    }
    if eng_unit:
        t["engUnit"] = eng_unit
    return t


def memory_tag(name, data_type, eng_unit, default, doc):
    # defaultValue applies only when the tag is first created.
    t = {
        "name": name,
        "tagType": "AtomicTag",
        "valueSource": "memory",
        "dataType": data_type,
        "documentation": doc,
        "readOnly": False,
    }
    if default is not None:
        t["defaultValue"] = default
    if eng_unit:
        t["engUnit"] = eng_unit
    return t


def product_tags(code):
    # (name, dataType, engUnit, column) -- mes.product_master
    cols = [
        ("IdealSpeed", "Float8", "m/min", "ideal_speed_mpm"),
        ("Lanes", "Int4", None, "lanes"),
        ("ScheduledChangeoverSec", "Int4", "s", "scheduled_changeover_s"),
    ]
    return [
        query_tag(
            name, data_type, unit,
            f"SELECT (SELECT {col} FROM mes.product_master WHERE product_code = {code})",
            60000,
            "Query on mes.product_master (UNS-004).",
        )
        for name, data_type, unit, col in cols
    ]


def active_wo_tags():
    # (name, dataType, engUnit, column) -- mes.v_work_order_table
    cols = [
        ("WorkOrderId", "String", None, "work_order_id"),
        ("ProductCode", "Int4", None, "product_code"),
        ("Status", "String", None, "status"),
        ("QtyGood", "Int4", "m", "good_m"),
        ("QtyReject", "Int4", "m", "reject_m"),
        ("StartedAt", "DateTime", None, "run_start"),
    ]
    return [
        query_tag(
            name, data_type, unit,
            f"SELECT (SELECT {col} {ACTIVE_WO})",
            2000,
            "Query on mes.v_work_order_table, the Running work order (UNS-004).",
        )
        for name, data_type, unit, col in cols
    ]


def schedule_tags():
    return [
        query_tag(
            "HeadProductCode", "Int4", None,
            "SELECT (SELECT wo.product_code FROM mes.schedule_entry se "
            "JOIN mes.work_order wo USING (work_order_id) "
            "WHERE se.status = 'Queued' ORDER BY se.sequence LIMIT 1)",
            2000,
            "Query on mes.schedule_entry, the queue head (UNS-004).",
        ),
        query_tag(
            "QueueDepth", "Int4", None,
            "SELECT count(*) FROM mes.schedule_entry WHERE status = 'Queued'",
            2000,
            "Query on mes.schedule_entry, Queued entries (UNS-004).",
        ),
        query_tag(
            "ReadinessOk", "Boolean", None,
            "SELECT coalesce((SELECT jr.is_ready FROM mes.schedule_entry se "
            "LEFT JOIN mes.job_readiness jr USING (work_order_id) "
            "WHERE se.status = 'Queued' ORDER BY se.sequence LIMIT 1), false)",
            2000,
            "Query on mes.job_readiness for the queue head (UNS-004).",
        ),
        memory_tag(
            "OverrideActive", "Boolean", None, False,
            "Written by SCHED-003 from mes.engine (memory).",
        ),
        memory_tag(
            "ReconcileResult", "String", None, "",
            "Written by SCHED-003 from mes.engine (memory).",
        ),
        memory_tag(
            "Override", "String", None, "",
            "Written by SCHED-003 from mes.engine (memory): the JSON event of MES §4.1, published by UNS-005.",
        ),
    ]


def downtime_tags():
    doc = "Written by DT-001 from mes.engine (memory)."
    return [
        memory_tag("State", "String", None, "", doc),
        memory_tag("CurrentAlarm", "Int4", None, 0, doc),
        memory_tag("Class", "String", None, "", doc),
        memory_tag("ActiveSince", "DateTime", None, None, doc),
        memory_tag("DurationSec", "Float8", "s", 0.0, doc),
    ]


def planned_stop_tags():
    doc = "Query on mes.planned_stop, the open stop (UNS-004)."
    return [
        query_tag(
            "Active", "Boolean", None,
            "SELECT EXISTS (SELECT 1 FROM mes.planned_stop WHERE end_ts IS NULL)",
            2000, doc,
        ),
        query_tag("Reason", "String", None, f"SELECT (SELECT reason {OPEN_STOP})", 2000, doc),
        query_tag("Since", "DateTime", None, f"SELECT (SELECT start_ts {OPEN_STOP})", 2000, doc),
        query_tag(
            "ConflictWithPLC", "Boolean", None,
            f"SELECT coalesce((SELECT conflict_flag {OPEN_STOP}), false)",
            2000, doc,
        ),
        memory_tag(
            "Event", "String", None, "",
            "Written by DT-003 from mes.engine (memory): the JSON event of MES §5, published by UNS-005.",
        ),
    ]


def oee_tags():
    doc = "Written by OEE-002 from mes.engine (memory)."
    return [
        memory_tag("Availability", "Float8", "%", 0.0, doc),
        memory_tag("Performance", "Float8", "%", 0.0, doc),
        memory_tag("Quality", "Float8", "%", 0.0, doc),
        memory_tag("OEE", "Float8", "%", 0.0, doc),
        memory_tag("WindowMinutes", "Int4", "min", 15, doc),
    ]


def folders():
    """Path (relative to Press 1) -> list of tags; [] means a marker-only folder."""
    out = {
        ("Work Order",): [],
        ("Work Order", "Active"): active_wo_tags(),
        ("Schedule",): schedule_tags(),
        ("Downtime",): downtime_tags(),
        ("Planned Stop",): planned_stop_tags(),
        ("OEE",): oee_tags(),
        ("Product",): [],
    }
    for code in PRODUCT_CODES:
        out[("Product", str(code))] = product_tags(code)
    return out


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_bytes(text.encode("utf-8"))


def check():
    # Every query must return exactly one row and one column, even with no records.
    failed = 0
    for rel, tags in sorted(folders().items()):
        for t in tags:
            if t["valueSource"] != "db":
                continue
            r = subprocess.run(
                ["docker", "exec", "-i", DB_CONTAINER, "psql", "-U", "mes", "-d", "mes",
                 "-At", "-v", "ON_ERROR_STOP=1", "-c", t["query"]],
                capture_output=True, text=True,
            )
            rows = r.stdout.split("\n")[:-1] if r.stdout else []
            ok = r.returncode == 0 and len(rows) == 1 and "|" not in rows[0]
            failed += not ok
            label = "/".join(rel + (t["name"],))
            print(f"{'OK  ' if ok else 'FAIL'} {label} -> {rows!r} {r.stderr.strip()}")
    print(f"{'all queries OK' if not failed else f'{failed} queries FAILED'}")
    return 1 if failed else 0


def main():
    if "--check" in sys.argv[1:]:
        sys.exit(check())

    total = 0
    for rel, tags in sorted(folders().items()):
        base = ROOT.joinpath(*PRESS, *rel)
        if tags:
            write_json(base / "tags.json", sorted(tags, key=lambda t: t["name"]))
            write_json(base / "unary-resource.json", marker(["tags.json"]))
        else:
            write_json(base / "unary-resource.json", marker([]))
        total += len(tags)
        print(f"{'/'.join(rel)}: {len(tags)} tags")
    print(f"total: {total} tags")


if __name__ == "__main__":
    main()
