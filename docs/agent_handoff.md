# MES Workshop 2026 — Agent Handoff

**Purpose:** Orient the incoming agent (PM or Dev) to current state. **Read second**, after `docs/claude_project_instructions.md`.
**Maintained by:** the PM (all sections except `Dev status`, which the Dev overwrites after each run).

> This is an orientation doc, not a second copy of the rules. Where it disagrees with `claude_project_instructions.md`, the instructions win and this file gets corrected.

---

## Dev status (latest Dev run — overwritten by the Dev each run)

| Field | Value |
|---|---|
| Task | DT-004 — Downtime test page: events and totals by class |
| Spec | `docs/tasks/task_DT-004_downtime_test_table.md` |
| Status | ✅ Done — ready for PM verification. Scanned by Walker; live check at ~02:52Z: page totals (Since start) match the SQL exactly — Changeover 35 / 845.8, Web break 69 / 652.7, Tunnel over-temp 29 / 351.0, overrun 14 / 211.3, RTO 14 / 173.6; 18-min window and events table match the gateway log; other pages load; no errors. |
| Date | 2026-10-22 |
| Summary | `mes.ui` gains `downtime_rows` / `class_totals` (SELECT only, parameterised window) / `downtime_now_line` (tag reads) and pure `shape_downtime` / `shape_class_totals` / `shape_now_line`; new view `Mes/Test/Downtime` at `/mes/downtime` (now-line, window dropdown 18 / 60 / since start, totals with All unplanned / All planned rows, events newest first, 2 s refresh). `test_ui_downtime.py` 25 PASS; all earlier tests pass under `python3 -I`; compose OK; Jython compile OK; no restart. Downtime gap 21:02:50Z → 02:45:54Z noted (MQTT reconnect, informational). |
| Commit | None (spec: no commit) |

The full reports are in each spec's `## Dev Report` section.


---

## 📚 Workshop reference docs — read these before writing any spec

The plant, the sim and the MES requirements are already written. **Read in this order:**

| # | Doc | What it gives you |
|---|---|---|
| 1 | `docs/about_4_0_printing.md` | The story: fictional company 4.0 Printing, Press 1, products, value stream, how the line misbehaves, manual processes, leadership questions. **Anything not written here, ask about rather than assume.** |
| 2 | `docs/sim_spec_press1.md` | The raw-layer signals: namespace, 16 tags, state and fault codes, job table, loop timing, reference values per loop, consumer rules. |
| 3 | `docs/mes_requirements.md` | What the MES must do: Work Orders, Scheduling, Downtime, OEE, data model, MES-published UNS topics, AI hooks, acceptance criteria. All items confirmed by Walker, Oct 20. Walker's word overrides. |
| — | `docs/sim_build_guide.md` | Only for building or changing a UNS Studio sim. Engine rules and probe results. Not needed for the MES build. |
| — | `docs/sim/fourzero_printing_press1.json` | The sim definition, a copy of the file UNS Studio loads. Do not hand-edit. Changes go through the spec and `create_simulator` or `update_simulator`. |

**Non-negotiables from those docs**

- The sim is **raw layer only**. No work orders, OEE, downtime reasons, planned stops or schedule data are ever simulated. The MES computes or captures them.
- The MES **records and reconciles; it does not control the press.** The sim loops jobs 101 to 105 and ignores the MES.
- **Do not stop, start or update the sim during class.** A restart resets the counters to 0 and restarts the loop.
- Cast every sim value on ingest. The same tag can arrive as integer or float. Meters are cumulative, so use deltas and treat a drop to 0 as a restart.
- The topic root is `40Printing/Dallas/Press Room/Press 1` (DEC-005). The dot in `4.0 Printing` broke Ignition. The company name in display text stays "4.0 Printing".

---

## ⏱️ Current state — 2026-10-23 (end of Day 2 session; docs audited for drift)

**Phase:** Core Four build. Walker finishes the remaining features on **Oct 23, live with the students online**. A fresh PM session starts there: read this file, then `docs/project_management.md`.

**What is built (all verified by the PM, see `docs/completed_development.md`):**
- **Stack:** INFRA-001 — CoreFlux 2.2.0 (`127.0.0.1:1886`), Ignition 8.3.10 (http://localhost:8089, user `admin`, password in `.env`), PostgreSQL 16 (5434). INFRA-002 — Ignition DB connection `mes` (`db:5432`, dev login in `.env.example`, DEC-008). INFRA-004 / DEC-005 — sim on this CoreFlux, root `40Printing/Dallas/Press Room/Press 1`. **Maestrohub, Timebase and Timebase Atlas are not deployed** (INFRA-003, not specced); Ignition does the ingest (DEC-002, DEC-007).
- **SCADA:** VIS-001 — `/press1`, `press1` library, `press1Tick`, buffer `press1.v2`.
- **Database:** DB-001 / DB-002 — schema `mes` (9 tables, 4 views, `docs/db_schema.md`), seed loaded 17:07:34Z Oct 22.
- **Tags:** UNS-002 — 16 edge expression tags + `PLC/SampleTime` (ENG-001); UNS-004 — 42 functional/definitional tags. Source: `ignition/tags/`.
- **MES logic (`mes` library, `mesTick` 2 s, `HANDLERS = [_workorder, _schedule, _downtime, _oee]`):** ENG-001 engine; WO-001 (+fix1, BUG-002) attribution and meter totals; WO-003 manual actions; SCHED-001/002/003 plan vs actual, readiness, override and reconcile; DT-001/002 fault events and Setup split; OEE-001 per-minute `oee_interval` rows.
- **Test pages (throwaway):** `/mes/work-orders` (WO-002), `/mes/schedule` (SCHED-001/002/003), `/mes/downtime` (DT-004). Base URL http://localhost:8089/data/perspective/client/mes-2026.
- **Offline tests:** 9 suites in `ignition/tests/`, all pass under `python3 -I`.

**Live data state (Oct 23 ~03:00Z):** work orders — 9 Complete, 1 Cancelled (WO-003 live test), WO-1010 Planned (Walker: keep it Planned). **No Released orders**, so new runs, downtime and OEE rows record with a NULL work order until someone creates and releases orders (on `/mes/work-orders`). 161+ downtime events.

**What is in progress:** nothing. No Dev running.

**Open bugs:** none. BUG-002 fixed (runs 2–4 corrected to 18 m reject, Walker). BUG-001 (CoreFlux SUBACK QoS order) worked around. See `docs/bug_log.md`.

**Recent decisions:** DEC-007 (`[default]` tag tree, no `MES/` level), DEC-008 (dev DB login), DEC-009 (UNS-005 publishes with MQTT Engine script publish), DEC-010 (VIS-002 = one `/mes` page, test pages kept).

**Git:** all work committed locally on `main`; **nothing pushed** (18+ commits ahead of `origin`). Push only with Walker's approval.

## ▶️ Next step

All remaining specs except AI-001 are written and **approved by Walker**. Order (file conflicts make it strict where noted):

1. **OEE-002** — OEE window tiles, `OEE/*` tags, `/mes/oee`. `claude "/dev docs/tasks/task_OEE-002_oee_window_tiles.md"`
2. **DT-003** — operator planned stops, conflict flag, `Planned Stop/Event`, OEE exclusion. **Only after OEE-002 is verified** (both edit `mes/oee/code.py`). Live test row only with Walker's OK. `claude "/dev docs/tasks/task_DT-003_planned_stops.md"`
3. **UNS-005** and **VIS-002** — in parallel, both after DT-003 and OEE-002 are verified. Walker's Scan File System after UNS-005 **starts publishing to CoreFlux**. VIS-002 runs in four steps with a scan after each.
   `claude "/dev docs/tasks/task_UNS-005_publish_functional_folders.md"` · `claude "/dev docs/tasks/task_VIS-002_mes_operator_ui.md"`
4. **AI-001** — not specced. **Blocked on a Walker decision:** it needs Timebase and Timebase Atlas (INFRA-003), which are not deployed in this stack. Ask Walker how the §9 AI queries get history and the model (deploy INFRA-003, use the Timebase video stack, or query PostgreSQL).
5. Unspecced follow-ups: WO-004 non-UI items (`docs/backlog.md`); sim spec §13 item 3 (fault precursors) is Walker's call.

## ⚠️ Traps and standing warnings

*(Things a fresh agent will get wrong without being told. Remove an entry when it stops being true.)*

- **`[default]` tag files load only at gateway start.** Changing `ignition/tags/` means re-push plus an Ignition restart (Walker's OK each time; it empties the VIS-001 buffer). Each folder's `unary-resource.json` must list `tags.json` or the tags are silently ignored. Never `docker cp` into an existing folder (silent nesting).
- **The live sim publishes every ~2.04–2.08 s, not 2.000 s.** Durations from sample timestamps run ~2–3.5 % over the nominal phase lengths (Run 50 s → ~51.7 s). Correct behaviour — do not tune. Live checks: event counts exact, each event ± 2 s, loop totals within ~4 % of the MES §5 / Sim Spec §9 nominal values. The offline replay (`ignition/tests/replay_engine.py`) checks the exact nominal values.
- **Check meters against the raw counter span, not the nominal job totals.** The live sim runs ~3 % slow and each phase is time-based, so a job sometimes gets one sample fewer (WO-1003 444 m, WO-1006 584 m). A run is right when `good_meters`/`reject_meters` equal the next run's start counter minus its own (`start_*_counter`). The offline replay checks the exact nominal totals.
- **Loading Ignition project changes = Walker's Scan File System click** (gateway web UI → Projects). No restart; `getGlobals()` stores survive it. Tag files still need a restart.
- **The sim feed can pause** (Mac sleep or UNS Studio). The engine logs one `gap` and records nothing for that time — not an MES fault. Check `docker logs mes-workshop-2026-ignition-1 | grep gap` before suspecting the code. Oct 22 21:03:51Z – Oct 23 02:45:02Z was one.
- **Parallel Devs must not share a file.** Check each spec's Files to Modify before running two at once (OEE-002 and DT-003 both edit `mes/oee`; page-config is shared by every page task).
- **The Dev cannot read live tag values** (no unauthenticated gateway API). Live checks are Walker's Tag Browser look, or a throwaway gateway script logging values.

- **The new CoreFlux (2.2.0) refuses an empty MQTT client ID** ("identifier rejected"). Every client — UNS Studio, MQTT Engine, Maestrohub, Timebase, CLI tests (`mosquitto_pub -i …`) — must send one. Anonymous connections are otherwise accepted.
- **CoreFlux 2.2.0 returns SUBACK granted QoS out of order for mixed-QoS subscribes** (BUG-001). Clients that check granted QoS (MQTT Engine does) disconnect. Keep each client's subscriptions at one QoS.
- **Topic root is `40Printing`** (DEC-005). Older docs and the stale `[MQTT Engine]4.0 Printing/…` tags show the old dotted root. Never read them.
- **Views in `mes-2026` were written by a generator kept outside the repo.** The view files are the source of truth. Never regenerate over Designer edits.
- **An open Designer runs its own copy of project library code on the gateway.** After a library change, update or restart the Designer. The versioned store key (`press1.v2`) exists because two copies once fought over one `getGlobals()` entry.
- **Perspective binding trap:** a property binding on a whole object (`view.params`) delivers `None` to its transform with no log line. Bind a leaf instead.
- **Do not reach for sim data the sim does not have.** Scheduled changeover time, ideal speed, planned stops and overrides live in MES master data or MES records, not in the UNS feed.
- **A deleted UNS Studio sim leaves its tags behind.** Stale tags do not mean a live sim.
- **Sim JSON must come from the file, never from a rendered text block.** A markdown renderer turns `*` into italics and corrupts formulas.

---

*Convention: replace the Current state block at each close-out rather than stacking dated blocks forever. If history matters, it belongs in `completed_development.md` or `decision-log.md`.*
