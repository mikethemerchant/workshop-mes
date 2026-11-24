# UNS-005 — Publish the functional folders to the broker

**Status:** 📝 Specced — approved by Walker 2026-10-23
**Priority:** P2 · **Effort:** S (one Dev session; no restart)
**Depends on:** DT-003 and OEE-002 **verified by the PM** (they write `Planned Stop/Event` and `OEE/*`). Edits no file they edit, so it may be *written* in parallel, but load it (Scan File System) only after both are verified.
**Bug:** —

---

## Background

MES §8 and DEC-007: the MES publishes its results back to the UNS in the functional folders beside the sim's edge folders under `40Printing/Dallas/Press Room/Press 1/`. There is no `MES/` level. The edge folders (`PLC`, `Print Section`, `Unwind`, `Rewind`, `Tunnel Dryer`) are **never** published. `Product` is definitional and is not published. The topics come back through MQTT Engine (it subscribes to `#`) as `[MQTT Engine]40Printing/…` tags. That echo is expected and harmless (`docs/ignition_tag_namespace.md` §5 catch 3). The Timebase video stack will also ingest these topics, which Walker accepted on Oct 22.

**Mechanism (Walker, 2026-10-22): MQTT Engine's script publish** on the existing Engine server connection **`MES Coreflux`**: `system.cirruslink.engine.publish(serverName, topic, payload, qos, retain)`. It gives plain JSON topics, needs no config change and no restart. MQTT Transmission stays disabled (it is installed but off and has no server; its default is Sparkplug B topics).

## Rules

1. **Allowlist.** Publish exactly these `[default]` tags, at the topic equal to their tag path (the `[default]` tree mirrors the topic tree, DEC-007):
   - **State:** `Downtime/Class`; `OEE/Availability`, `OEE/Performance`, `OEE/Quality`, `OEE/OEE`; `Work Order/Active/WorkOrderId`, `ProductCode`, `Status`, `QtyGood`, `QtyReject`, `StartedAt`.
   - **Events:** `Schedule/Override`, `Planned Stop/Event`.

   Base: `40Printing/Dallas/Press Room/Press 1/`. A pure `topic_for(rel_path)` must refuse (raise) any path whose first segment is not one of `Work Order`, `Schedule`, `Downtime`, `Planned Stop`, `OEE`. This guard is the code-level proof that the edge folders and `Product` are never published.
2. **Payloads.**
   - State: `{"value": <v>, "timestamp": "<ISO 8601 UTC>"}`. This mirrors the sim's envelope, so one reader unwraps both. `null` for a null or bad-quality value. Dates are ISO strings.
   - Events: the tag's JSON string **as-is**. These are the payloads Walker confirmed: Override = work order, product code, from and to sequence, reason, operator, timestamp; Event = reason, start, end, operator.
   - UTF-8 bytes.
3. **When.**
   - Publish **on change only**: compare with the last published value kept in the store `mes.publish.v1` (`system.util.getGlobals()`).
   - Floats count as changed at more than 0.05 (OEE percents).
   - **First run** (empty store): publish every state tag once. Record the current event strings **without** publishing them, so an old override or stop is not replayed.
   - Events then publish whenever the string changes and is not empty. A planned stop therefore publishes on start and again on end.
4. **QoS and retain.** State: QoS 0, **retain true**, so a new subscriber sees the current state. Events: QoS 1, retain false.
5. **`published_at`.** After an event publishes successfully, run a guarded, parameterised update:
   - Override: `UPDATE mes.schedule_override SET published_at = now() WHERE published_at IS NULL AND entered_at <= now()`.
   - Planned Stop: `UPDATE mes.planned_stop SET published_at = now() WHERE published_at IS NULL AND start_ts <= ?`, where `?` is the `start` in the payload.

   Rows from before UNS-005 that are never published stay NULL. That is truthful. Never delete.
6. **Timer and isolation.**
   - A new gateway timer `mesPublish`: 2000 ms, fixed delay, `mes.publish.tick()` in a try/except that logs and never raises (same pattern as `mesTick`).
   - It reads tags with `system.tag.readBlocking` and **writes no tags**.
   - A publish failure logs once per tag per minute and leaves the store unchanged, so the publish is retried next tick.
7. **Existence check.** If `system.cirruslink.engine.publish` is missing, or the server `MES Coreflux` is unknown, log one error per minute and publish nothing. Do not try Transmission. Report it as a blocker.
8. **Never** subscribe or write to sim topics, never publish an edge-folder or `Product` topic, and never change Engine or Transmission config.

## What Already Exists (Do Not Rewrite)

- The functional tags from UNS-004 (`ignition/tags/generate_functional_tags.py`), written by:
  - `mes.downtime` (`Downtime/*`, `Planned Stop/Event` after DT-003);
  - `mes.oee` (`OEE/*` after OEE-002);
  - `mes.schedule` (`Schedule/Override`).
- `Work Order/Active/*` are query tags.
- The MQTT Engine server `MES Coreflux` (CoreFlux at `coreflux:1883`), with Engine subscribing to `#`.
- The timer `mesTick` (`ignition/projects/mes-2026/ignition/timer/mesTick/`): copy its resource layout.
- `schedule_override.published_at` and `planned_stop.published_at` (`db/001_schema.sql`).

## What to Build

1. `ignition/projects/mes-2026/ignition/script-python/mes/publish/` (`code.py` + `resource.json`, matching the other `mes` modules). It holds:
   - **Pure:** `topic_for`, `state_payload(value, quality_good, ts)`, `changed(old, new)`, `plan(store, reads)`. `plan()` returns the publishes and the new store, applying the rule 3 first-run behaviour.
   - **I/O:** `tick()`, `info()` (last publish per topic and an error count, for the Dev and the PM).
   - The same Jython 2.7 / Python 3 style as the other modules.
2. `ignition/projects/mes-2026/ignition/timer/mesPublish/` (`handleTimerEvent.py` + `resource.json`).
3. `ignition/tests/test_publish.py` (`python3 -I`), covering:
   - `topic_for` accepts all 13 allowlisted paths and **raises** for `PLC/MachineState`, `Print Section/…`, `Product/101/IdealSpeed` and `MES/x`;
   - state payload shape (value, null on bad quality, ISO timestamp);
   - on-change only, with the float threshold;
   - first run publishes the state tags but not the events;
   - an event change publishes once;
   - a failed publish keeps the store, so the next tick retries;
   - QoS and retain per rule 4.

   All earlier tests must still pass.

## Load and live check

1. **Stop** after the tests. Fill in the Dev Report. Write in the handoff: "Ready to load — **the scan starts publishing to CoreFlux.** Waiting for Walker (Scan File System)."
2. After the scan, check it **read-only**:
   - Subscribe to `40Printing/Dallas/Press Room/Press 1/#` for about 30 s. Use `mosquitto_sub` if it is on the host. If it isn't, run Python `paho-mqtt` if it is installed, against `127.0.0.1:1886` with a unique client ID (CoreFlux refuses an empty one). If neither is available, read the `[MQTT Engine]40Printing/Dallas/Press Room/Press 1/{OEE,Downtime,Work Order}/…` echo tags from the gateway.
   - Paste the topics and payloads you saw.
   - **Confirm that no message on an edge-folder topic carries the MES payload shape**, and that nothing at all appears under `Product/`. The sim's own edge messages will appear; they are expected.
   - Do **not** create an override or planned stop just to test the events. Unit tests cover them. If one happens in class, its `published_at` should fill in.
3. Paste `mes.publish.info()` output if it can be read (or the gateway log lines).

## Acceptance Criteria

- [ ] Only the 13 allowlisted tags publish, at their own paths. The guard refuses everything else, and the tests prove it.
- [ ] The payloads, on-change behaviour, first run, QoS and retain all follow rules 2–4. `published_at` follows rule 5.
- [ ] `test_publish.py` and all earlier tests pass under `python3 -I`.
- [ ] Live: the state topics are seen on CoreFlux with the envelope shape, and nothing under the edge folders or `Product/` comes from the MES.
- [ ] No restart, no Engine or Transmission config change, no tag writes, no schema change. Only the listed files are edited.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/publish/code.py`, `resource.json` | New |
| `ignition/projects/mes-2026/ignition/timer/mesPublish/handleTimerEvent.py`, `resource.json` | New |
| `ignition/tests/test_publish.py` | New |
| `docs/tasks/task_UNS-005_publish_functional_folders.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

## Dev Instructions

Spec: `docs/tasks/task_UNS-005_publish_functional_folders.md`.

What to build: publish the 13 allowlisted MES tags (Downtime/Class, OEE/*, Work Order/Active/*, Schedule/Override, Planned Stop/Event) to CoreFlux at their own topic paths.
- Use MQTT Engine's `system.cirruslink.engine.publish` on the server `MES Coreflux`, from a new 2 s `mesPublish` gateway timer.
- State tags: on change, as `{"value","timestamp"}`, retained.
- Events: the JSON as-is, not retained. Set `published_at`.
- A hard guard refuses any topic outside the five functional folders.

Test offline, then **stop for Walker's Scan File System (it starts publishing)**. After that, check the broker read-only.

Hard constraints:
- **Start only after the PM has verified DT-003 and OEE-002**, or write the code and stop before load. Edit only the listed files.
- **Never publish the edge folders or `Product`.** Never write or subscribe to change sim topics. No Engine or Transmission config changes. No container restarts. No tag writes. No schema change. Parameterised SQL; never delete.
- Never stop, start, update or reimport the sim. Never touch UNS Studio. Never read `4.0 Printing/…` or `Probe/…`.
- Never touch `ign-mes-demo` or the Timebase stack. Never print `.env` or any password. No commit, no push; stage nothing.

## Dev Report

*(Dev fills in.)*

## PM Verification

*(PM fills in.)*
