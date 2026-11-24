#!/usr/bin/env python3
"""Generate the UNS-002 edge mirror tag tree for Ignition 8.3 (DEC-007).

Writes Ignition tag resource files for the 16 raw sim tags under
ignition/tags/default/40Printing/Dallas/Press Room/Press 1/<Folder>/.
Each tag is an expression tag that unwraps the UNS Studio JSON envelope
held by the matching [MQTT Engine] String tag and casts it to its type.
PLC/SampleTime (ENG-001) is the one exception: it reads the "timestamp"
key of the PLC/MachineState envelope.

The repo copy is the source of truth; it is pushed into the gateway's
tag-definition/default/ folder. Output is deterministic: re-running gives
byte-identical files. Standard library only.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "default"
PRESS = ("40Printing", "Dallas", "Press Room", "Press 1")
TOPIC = "/".join(PRESS)

# (folder, name, dataType, cast, engUnit or None) -- docs/sim_spec_press1.md §5
TAGS = [
    ("PLC", "Connected", "Boolean", "toBoolean", None),
    ("PLC", "MachineState", "Int4", "toInt", None),
    ("PLC", "FaultCode", "Int4", "toInt", None),
    ("PLC", "ProductCode", "Int4", "toInt", None),
    ("PLC", "WebLanes", "Int4", "toInt", None),
    ("PLC", "GoodMeters", "Int4", "toInt", "m"),
    ("PLC", "RejectMeters", "Int4", "toInt", "m"),
    ("PLC", "RTOInhibit", "Boolean", "toBoolean", None),
    ("Unwind", "WebTension", "Float8", "toFloat", "lbf"),
    ("Rewind", "WebTension", "Float8", "toFloat", "lbf"),
    ("Print Section", "LineSpeed", "Float8", "toFloat", "m/min"),
    ("Print Section", "RegistrationError", "Float8", "toFloat", "mm"),
    ("Print Section", "InkViscosity", "Float8", "toFloat", "s"),
    ("Tunnel Dryer", "Zone1Temp", "Float8", "toFloat", "°F"),
    ("Tunnel Dryer", "Zone2Temp", "Float8", "toFloat", "°F"),
    ("Tunnel Dryer", "Zone3Temp", "Float8", "toFloat", "°F"),
    ("PLC", "SampleTime", "String", "toStr", None),
]

# (folder, name) -> (source topic name, envelope key, documentation) for tags that
# do not read their own topic's "value". ENG-001 de-dups and times on SampleTime.
SOURCES = {
    ("PLC", "SampleTime"): (
        "MachineState",
        "timestamp",
        "Sim sample time (ISO 8601 UTC) from the PLC/MachineState envelope timestamp (ENG-001).",
    ),
}


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


def tag(folder, name, data_type, cast, eng_unit):
    topic = f"{TOPIC}/{folder}/{name}"
    key = "value"
    doc = f"Edge mirror of MQTT topic {topic} (UNS-002)."
    if (folder, name) in SOURCES:
        source, key, doc = SOURCES[(folder, name)]
        topic = f"{TOPIC}/{folder}/{source}"
    t = {
        "name": name,
        "tagType": "AtomicTag",
        "valueSource": "expr",
        "expressionType": "Expression",
        "dataType": data_type,
        "expression": f'{cast}(jsonGet({{[MQTT Engine]{topic}}}, "{key}"))',
        "documentation": doc,
        "readOnly": True,
    }
    if eng_unit:
        t["engUnit"] = eng_unit
    return t


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_bytes(text.encode("utf-8"))


def main():
    for depth in range(1, len(PRESS) + 1):
        write_json(ROOT.joinpath(*PRESS[:depth], "unary-resource.json"), marker([]))

    folders = {}
    for folder, *rest in TAGS:
        folders.setdefault(folder, []).append(tag(folder, *rest))

    for folder, tags in sorted(folders.items()):
        base = ROOT.joinpath(*PRESS, folder)
        write_json(base / "tags.json", sorted(tags, key=lambda t: t["name"]))
        write_json(base / "unary-resource.json", marker(["tags.json"]))
        print(f"{folder}: {len(tags)} tags")


if __name__ == "__main__":
    main()
