# Ignition Tag Namespace — Design (DEC-007)

*Draft v0.2, Oct 22, 2026, by VERA. Decided by Walker at 10:06 CDT on Oct 22. Edge folders built Oct 22 (UNS-002: `ignition/tags/`, 16 expression tags, plus `PLC/SampleTime` from ENG-001 — the sim's sample timestamp from the MachineState envelope, Walker's option A); functional folders built Oct 22 (UNS-004: `ignition/tags/generate_functional_tags.py`, 42 tags — query tags on connection `mes` where the record is in PostgreSQL, memory tags written by the MES logic where the truth is in the gateway; the spec has the full table). The functional and definitional tag names are VERA's proposal, so rename freely. Anything marked "verify" is unconfirmed until it is checked against the Ignition 8.3 documentation. **Status Oct 23:** catches 1, 2 and 4 and §7 are resolved (see the notes in each).*

## Decisions (Walker, Oct 22)

| # | Decision |
| --- | --- |
| 1 | The MES reads and writes a tag tree in Ignition's `[default]` provider. It does not read `[MQTT Engine]` directly. Recorded as **DEC-007**. |
| 2 | The `MES/` topic level is dropped. Functional namespaces sit beside the edge folders under `40Printing/Dallas/Press Room/Press 1/`. |
| 3 | Edge folders keep exactly the sim's level names: `PLC`, `Print Section`, `Unwind`, `Rewind`, `Tunnel Dryer`. |

## 1. The idea

Build a tag tree in Ignition's default tag provider that mirrors the sim's semantic hierarchy. Expression tags (UNS-002) pull the sim values in. Functional namespaces (Work Order, Schedule, Downtime, Planned Stop, OEE) sit next to them. The MES reads and writes this tree, not the raw MQTT Engine tags. The namespace becomes the interface: if the data source changes later (Maestrohub, a real PLC), the MES logic does not change.

## 2. UNS rules applied

These come from the HiveMQ UNS articles that quote the four namespace types. virtualfactory.online could not be read when this was drafted.

- **Hierarchy follows the organization:** Enterprise / Site / Area / Line, here `40Printing / Dallas / Press Room / Press 1`.
- **Edge namespace:** raw PLC data, kept separate and unprocessed. Here, the mirrored sim tags.
- **Functional namespace:** processed, contextualized data for one function (OEE, Work Order), on its own namespace apart from the raw tags.
- **Definitional namespace:** data that rarely changes (ideal speed per product, scheduled changeover time).
- **Informative namespace:** data abstracted for other software. Not needed today.
- **Document it:** the structure stays constant and is written down. This page is the first draft of that.

## 3. Tree

```
[default]40Printing/Dallas/Press Room/Press 1/
  PLC/                     EDGE  (expression tags, 8 + SampleTime)
    Connected, FaultCode, GoodMeters, MachineState,
    ProductCode, WebLanes, RejectMeters, RTOInhibit
  Print Section/           EDGE  (3)
    LineSpeed, RegistrationError, InkViscosity
  Unwind/                  EDGE  (1)   WebTension
  Rewind/                  EDGE  (1)   WebTension
  Tunnel Dryer/            EDGE  (3)   Zone1Temp, Zone2Temp, Zone3Temp

  Work Order/              FUNCTIONAL
    Active/                WorkOrderId, ProductCode, Status, QtyGood, QtyReject, StartedAt
  Schedule/                FUNCTIONAL
    HeadProductCode, QueueDepth, ReadinessOk, OverrideActive, ReconcileResult
    Override               (JSON event, published; see 3a)
  Downtime/                FUNCTIONAL
    State (Running, Planned, Unplanned), CurrentAlarm, Class, ActiveSince, DurationSec
  Planned Stop/            FUNCTIONAL
    Active, Reason (Clean Plates, Roll Change), Since, ConflictWithPLC
    Event                  (JSON event, published; see 3a)
  OEE/                     FUNCTIONAL
    Availability, Performance, Quality, OEE, WindowMinutes

  Product/                 DEFINITIONAL (optional)
    per product code: IdealSpeed, Lanes, ScheduledChangeoverSec
```

The edge folders keep exactly the sim's level names, so a student can match each tag to its MQTT topic by eye. That is 16 raw tags in the same five folders the sim publishes.

## 3a. What replaces the old `MES/` topics

The earlier docs name topics under `.../Press 1/MES/...`. With the level dropped, they become:

| Old topic under `.../Press 1/MES/` | New location under `.../Press 1/` |
| --- | --- |
| `ScheduleOverride` | `Schedule/Override` (JSON event: work order, product code, from and to sequence, reason, operator, timestamp) |
| `PlannedStop` | `Planned Stop/Event` (JSON event: reason, start, end, operator). `Planned Stop/Active` and the other tags carry current state. |
| `Downtime/CurrentClass` | `Downtime/Class` |
| `OEE/Availability`, `OEE/Performance`, `OEE/Quality`, `OEE/OEE` | Unchanged under `OEE/` |
| `WorkOrder/Active` | `Work Order/Active/...` (with a space, to match the folder name) |

The payloads Walker confirmed on Oct 20 do not change. Only the paths do. The `Event` and `Override` names are VERA's proposal.

## 4. How each part gets its value

| Part | Source | Note |
| --- | --- | --- |
| Edge tags | Mapped from the `[MQTT Engine]40Printing/...` tag. **Walker, Oct 22: the requirement is the mapping of each MQTT topic into the namespace; Reference tags are one option, not a requirement.** The mechanism is chosen in the edge-mirror spec. | Never read the old `4.0 Printing/...` tags. |
| Cast values | Expression tags or a gateway script that unwrap the envelope | See catch 1. |
| Work Order, Schedule, Downtime, Planned Stop, OEE | Query tags on the PostgreSQL views, or a gateway script | The database holds the records. The tags hold current state only. |
| Product | Query tags on the product master | Seed data from `db/002_seed.sql`. |

## 5. Catches

1. **A reference tag points at the raw string.** MQTT Engine is set to `jsonPayload: false`, so each tag holds the whole JSON envelope as a string, not the value. A plain reference gives a string like `{"value": 4, ...}`. The unwrap and cast (float to integer for meters, state and codes) still has to happen. **Verify** the Reference tag type in 8.3.9 and 8.3.10, and verify `jsonGet` for the unwrap. **Resolved (UNS-002):** expression tags `toInt|toFloat|toBoolean(jsonGet({[MQTT Engine]…}, "value"))`; no Reference tags.
2. **Never publish the edge folders back to the broker.** If MQTT Transmission publishes the whole provider, it writes onto the topics the sim publishes on, and MQTT Engine reads them again. Publish only the functional folders. **Resolved (Walker, Oct 22, UNS-005):** MQTT Engine script publish (`system.cirruslink.engine.publish`, server `MES Coreflux`) with a 13-tag allowlist and a guard that refuses non-functional paths. Transmission stays disabled.
3. **Echo.** MQTT Engine subscribes to `#`, so published functional topics come back as `[MQTT Engine]` tags. Harmless, but expect it.
4. **The `MES/` level is gone (decided).** Those docs were updated Oct 22.
5. **Effect on the Timebase video stack.** Its Collector subscribes to `.../Press 1/#` and its filter drops only `.../Press 1/MES/...`. With `MES/` gone, any functional topic the MES publishes will flow into that stack's Historian dataset `press1`. That is a separate repo this workshop's agents must not touch. Nothing publishes until Task 3. **Walker, Oct 22: accepted; he will update that stack at the end.**
6. **Names.** Ignition rejects dots in tag names (DEC-005). Spaces work in MQTT Engine. Avoid dots and special characters in every new name.
7. **Persistence.** The default provider lives in the gateway. **Verify** the gateway data persists across a container recreate, and keep the tag definitions as a JSON file in the repo. **Done:** definitions and generators are in `ignition/tags/` (tag files load only at gateway start).
8. **Existing code stays.** The `press1` project library and the SCADA view (VIS-001) keep reading `[MQTT Engine]`. New MES code goes in a separate `mes` library and reads the `[default]` tree. VIS-001's "no DB or MES terms" acceptance checks apply to the `press1` project only.
9. **No tag history.** Do not enable the Ignition historian on these tags. History lives in the database and in Timebase.

## 6. Build order

1. **Edge mirror first (Task 1, UNS-002 ✅).** Sixteen expression tags with casts, plus `PLC/SampleTime`. Students see real tags in the Tag Browser. Needs no database.
2. **Functional folders next (Task 2, UNS-004 ✅),** once the schema and seed exist (DB-001, DB-002) and the Ignition database connection is in (INFRA-002, Walker's GUI step). Create the folders and tag names early so the visuals can bind to them. Fill in values as each feature lands.
3. **Publishing last (Task 3, UNS-005, specced).** Defer it until the database logic works.

## 7. Still open

- ~~Ignition facts~~ resolved: expression tags + `jsonGet` (UNS-002); publish via MQTT Engine (UNS-005).
- ~~INFRA-002~~ done: connection `mes` to `db:5432`.
- **Names:** `Schedule/Override`, `Planned Stop/Event` and the folder tag names are VERA's proposals.
- **Hour 4 Atlas model:** whether to widen it to bind the functional topics, or leave Gage on the raw tags. Walker decides later. Nothing publishes until Task 3.
