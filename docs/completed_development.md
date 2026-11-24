# Completed Development

**Project:** MES Workshop 2026
**Maintained by:** the PM
**Format:** Reverse chronological. Each entry describes what was **actually built**, verified by reading the files (Rule 7) — not what was planned.

---

## How to use this log

Every completed task gets an entry, written by the PM at close-out (Rule 14). Entries are **not edited after they are written**, except to add the "Verified by" line. Corrections become new entries.

## Entry template

```
### {ID} — {short headline}

**Date:** YYYY-MM-DD
**Spec:** [`tasks/task_{ID}_{slug}.md`](tasks/task_{ID}_{slug}.md)
**Commit:** {hash}
**Verified by {Walker | VERA}:** ✅ YYYY-MM-DD — "{quote}"  (or ⏳ pending runtime test)

**What was built:** concrete deliverables.
**Files touched:** paths.
**Deviations from spec:** what the Dev did differently, and why it was acceptable — or "None."
**User-facing impact:** what a user would notice, and which docs changed — or "None."
```

---

### DT-004 — Downtime test page: events and totals by class

**Date:** 2026-10-23
**Spec:** [`tasks/task_DT-004_downtime_test_table.md`](tasks/task_DT-004_downtime_test_table.md)
**Commit:** see `git log -- ignition/tests/test_ui_downtime.py`
**Verified by Walker:** ✅ 2026-10-23 — Scan File System; close-out on PM verification

**What was built:** read-only `mes.ui` additions (`shape_downtime`, `shape_class_totals` with All unplanned / All planned rows, `shape_now_line`; `downtime_rows`, `class_totals`, `downtime_now_line`) and the bare page `/mes/downtime` (`Mes/Test/Downtime`) with a window selector (18 min / 60 min / since start). Test `ignition/tests/test_ui_downtime.py` (25 checks, one loop → 82/48/24, 118, 28 s). Live totals equal the SQL.
**Files touched:** `mes/ui/code.py`, `Mes/Test/Downtime/`, `page-config/config.json`, `ignition/tests/test_ui_downtime.py`.
**Deviations from spec:** "Since start" stored as 0; the window change refreshes at once.
**User-facing impact:** Hour 3 downtime test visual at `/mes/downtime`.

### OEE-001 — OEE interval writer

**Date:** 2026-10-22
**Spec:** [`tasks/task_OEE-001_oee_intervals.md`](tasks/task_OEE-001_oee_intervals.md)
**Commit:** see `git log -- ignition/tests/replay_oee.py`
**Verified by Walker:** ✅ 2026-10-22 — spec "both are approved"; close-out on PM verification

**What was built:** `mes.oee` (fourth engine handler): sample-and-hold time accounting to the previous sample's state and product (running; fault; Setup split at the scheduled changeover — overrun counts, planned part excluded), meter deltas to the previous product, ideal meters from product master; minute buckets split at boundaries; absolute, reload-safe upserts into `oee_interval`; planned-stop hook returning 0 (DT-003). Test `ignition/tests/replay_oee.py` reproduces MES §6. Live minutes sum to 60 s.
**Files touched:** `mes/oee/`, `mes/engine/code.py` (handler), `ignition/tests/replay_oee.py`.
**Deviations from spec:** None.
**User-facing impact:** OEE inputs per minute in the database; `v_oee_by_product` populated.

### DT-002 — Setup split: planned changeover vs overrun

**Date:** 2026-10-22
**Spec:** [`tasks/task_DT-002_setup_split.md`](tasks/task_DT-002_setup_split.md)
**Commit:** see `git log -- docs/tasks/task_DT-002_setup_split.md`
**Verified by Walker:** ✅ 2026-10-22 — spec "both are approved"; close-out on PM verification

**What was built:** In `mes.downtime`: each closed Setup is split at the product's scheduled changeover into a planned `Changeover` row and, if longer, a derived `Changeover overrun` row (one transaction); live tags switch Planned → Unplanned past the changeover. Tests extended in `replay_downtime.py` (118 s planned, 14 s overruns on 102/104). Live: 102 Setup 30.0 + 16.0 s; 103 Setup 22.7 s planned only.
**Files touched:** `mes/downtime/code.py`, `ignition/tests/replay_downtime.py`.
**Deviations from spec:** None.
**User-facing impact:** planned vs unplanned Setup time in `downtime_event`.

### DT-001 — Downtime fault events and live tags

**Date:** 2026-10-22
**Spec:** [`tasks/task_DT-001_downtime_events.md`](tasks/task_DT-001_downtime_events.md)
**Commit:** see `git log -- ignition/tests/replay_downtime.py`
**Verified by Walker:** ✅ 2026-10-22 — spec "approved and running"; close-out on PM verification

**What was built:** `mes.downtime` (third engine handler): fault class from the code (101/102/103, else Unclassified alarm); WO captured at fault start; one complete `downtime_event` row INSERTed when each fault closes (open event only in gateway memory); `Downtime/*` memory tags for Running / Setup (Planned, Changeover) / Faulted. Test `ignition/tests/replay_downtime.py` (15 faults per loop, 82/48/24 s, RTO on 102/105). Live: web breaks 10.4 s and 8.3 s recorded.
**Files touched:** `mes/downtime/`, `mes/engine/code.py` (handler), `ignition/tests/replay_downtime.py`.
**Deviations from spec:** None.
**User-facing impact:** fault events in `mes.downtime_event` and `v_downtime_by_class`; live `Downtime/*` tags.

### SCHED-003 — Rush override and reconcile

**Date:** 2026-10-22
**Spec:** [`tasks/task_SCHED-003_override_reconcile.md`](tasks/task_SCHED-003_override_reconcile.md)
**Commit:** see `git log -- ignition/tests/test_schedule_override.py`
**Verified by Walker:** ✅ 2026-10-22 — spec "Both are approved"; close-out on PM verification

**What was built:** In `mes.schedule`: `move_to_front` (override record, resequence Queued entries, replan, `Schedule/Override` JSON + `OverrideActive`), `move_up` (plan edit, no record), reconcile on every run start before the sync (Normal start / Authorized override / Override not followed / Unrecorded deviation) stored on the started entry and in `Schedule/ReconcileResult`. Override panel, Reconcile column and override list on `/mes/schedule`. Test `ignition/tests/test_schedule_override.py` (four demo-recipe outcomes). Live: `reconcile: schedule empty`, tag writes Good.
**Files touched:** `mes/schedule/code.py`, `views/Mes/Test/Schedule/view.json`, `ignition/tests/{test_schedule_override.py,replay_schedule.py}`.
**Deviations from spec:** `replay_schedule.py` fake updated (accepted). Duplicate-sequence edge case → backlog WO-004.
**User-facing impact:** the four §4.1 outcomes for the class demo.

### SCHED-002 — Job readiness and "started without readiness"

**Date:** 2026-10-22
**Spec:** [`tasks/task_SCHED-002_readiness.md`](tasks/task_SCHED-002_readiness.md)
**Commit:** see `git log -- docs/tasks/task_SCHED-002_readiness.md`
**Verified by Walker:** ✅ 2026-10-22 — spec "Both are approved"; close-out on PM verification

**What was built:** In `mes.schedule`: `save_readiness` upserts `job_readiness` (refused once an order is Running/Complete/Closed/Cancelled); the sync sets `schedule_entry.started_without_readiness` from the readiness at the moment an entry goes Queued → Running (never recomputed, not on backfill). Readiness panel and "Started w/o readiness" column on `/mes/schedule`. Tests in `replay_schedule.py`. Live: WO-1010 readiness saved with proof missing.
**Files touched:** `mes/schedule/code.py`, `views/Mes/Test/Schedule/view.json`, `ignition/tests/replay_schedule.py`.
**Deviations from spec:** None.
**User-facing impact:** readiness entry on `/mes/schedule`; the flag appears at a press start (class demo).

### SCHED-001 — Schedule: plan vs actual

**Date:** 2026-10-22
**Spec:** [`tasks/task_SCHED-001_plan_vs_actual.md`](tasks/task_SCHED-001_plan_vs_actual.md)
**Commit:** 43fa92c
**Verified by Walker:** ✅ 2026-10-22 — "Approved -- its running" (spec); close-out on PM verification

**What was built:** `mes.schedule` (second engine handler after `_workorder`): `replan()` chains planned start/end (changeover + planned m at ideal speed) from the first entry's actual start, only on first load with missing plans or on request; `sync_from_runs()` sets status and actual start/end from `work_order_run` on every run start / product change; `schedule_released()` appends Released unscheduled orders (Rush, due date, ID). Test page `/mes/schedule` (view `Mes/Test/Schedule`) with Replan and Schedule-released buttons. Test `ignition/tests/replay_schedule.py`. Live backfill: planned 164/155/159/163.2/166.9 s vs actual 185/229.9/207.8/220/213.2 s.
**Files touched:** `mes/schedule/`, `mes/engine/code.py` (handler), `views/Mes/Test/Schedule/`, `page-config/config.json`, `ignition/tests/replay_schedule.py`, `simreplay.py`.
**Deviations from spec:** None. Entry 1 excludes its Setup (previous run unattributed) — backlog WO-004.
**User-facing impact:** `/mes/schedule`; `Schedule/*` tags show live statuses.

### WO-003 — Work order manual actions

**Date:** 2026-10-22
**Spec:** [`tasks/task_WO-003_manual_actions.md`](tasks/task_WO-003_manual_actions.md)
**Commit:** 43fa92c
**Verified by Walker:** ✅ 2026-10-22 — "Approved" (spec); close-out on PM verification

**What was built:** In `mes.workorder`: create, release (keeps `released_at`), hold (reason), cancel, close — status-guarded, one transaction each, result strings; reassign of attributed or unattributed runs to a Released order of the same product with both orders' status recomputed from their runs (`status_from_runs`); reassign of the open run queued and applied on the timer thread (`apply_pending`). Read helpers `reassignable_runs`, `eligible_targets`. Action panel on `/mes/work-orders`. Test `ignition/tests/test_workorder_actions.py`. Live: WO-1011 created and walked Release → Hold → Release → Cancel; seed untouched.
**Files touched:** `mes/workorder/code.py`, `mes/ui/code.py`, `views/Mes/Test/WorkOrders/view.json`, `ignition/tests/{test_workorder_actions.py,replay_workorder.py,simreplay.py}`.
**Deviations from spec:** None. Four small follow-ups → backlog WO-004.
**User-facing impact:** manual work-order actions on the test page; WO-1011 exists (Cancelled, test order).

### WO-001-fix1 — Late meter deltas credited to the run just closed (BUG-002)

**Date:** 2026-10-22
**Spec:** [`tasks/task_WO-001-fix1_late_meter_delta.md`](tasks/task_WO-001-fix1_late_meter_delta.md)
**Commit:** 2895810
**Verified by Walker:** ✅ 2026-10-22 — "assume its right"

**What was built:** Rule 8 in `mes.workorder`: `last_closed` remembered at every close; with no run open, a non-zero meter delta on a Setup sample is added to it and written as absolute totals (`end_ts` and status untouched); forgotten at the next `run_start`, a `gap`, or after a startup resume. Lag-injected two-loop replay added (exact totals with meters one sample late); `test_ui_work_orders.py` runs under `-I`. Loaded by Scan File System 12:30 CDT; store survived. Data: runs 2, 3, 4 (WO-1002/1003/1004) reject 16 → 18 by guarded PM updates on Walker's instruction.
**Files touched:** `mes/workorder/code.py`, `ignition/tests/replay_workorder.py`, `ignition/tests/test_ui_work_orders.py`.
**Deviations from spec:** Closed before a live late-delta credit was observed (Walker).
**User-facing impact:** Job-end reject totals match the raw counter. Docs: bug log, handoff traps (check against counter span).

### WO-002 — Work order test table (Hour 2 test visual)

**Date:** 2026-10-22
**Spec:** [`tasks/task_WO-002_work_order_test_table.md`](tasks/task_WO-002_work_order_test_table.md)
**Commit:** 1042eb3
**Verified by Walker:** ✅ 2026-10-22 — "I approve"

**What was built:** Module `mes.ui` (`shape_work_orders` pure; `work_order_rows`, `open_run_line`, `unattributed_rows` I/O, SELECT-only on `mes`). Bare Perspective view `Mes/Test/WorkOrders` at `/mes/work-orders`: 13-column table from `mes.v_work_order_table` (incl. lane-m, over/under on Complete, due with `late`), open-run line and unattributed-runs table from `mes.workorder`, polling every 2 s (`now(2000)` + script transform). Offline test `ignition/tests/test_ui_work_orders.py`. Loaded by Scan File System; live page matched psql within one sample.
**Files touched:** `mes/ui/{code.py,resource.json}`, `views/Mes/Test/WorkOrders/{view.json,resource.json}`, `page-config/config.json` (one entry), `ignition/tests/test_ui_work_orders.py`.
**Deviations from spec:** None. Test doesn't run under `python3 -I` (fixed in WO-001-fix1). Exposed BUG-002 (WO-001, logged).
**User-facing impact:** test page http://localhost:8089/data/perspective/client/mes-2026/mes/work-orders.

### WO-001 — Work orders from the PLC: attribution, Running/Complete, meter totals

**Date:** 2026-10-22
**Spec:** [`tasks/task_WO-001_run_attribution.md`](tasks/task_WO-001_run_attribution.md)
**Commit:** 7734d7d
**Verified by Walker:** ✅ 2026-10-22 — "Fix this - assume we are good"

**What was built:** Library `mes.workorder` (`ignition/projects/mes-2026/ignition/script-python/mes/workorder/`), registered as the first `mes.engine` handler. On `run_start` it attributes the run to the earliest Released order of that product (tie on `work_order_id`), sets it Running and inserts a `work_order_run` with start counters; it adds the engine's deltas and writes absolute totals when they change; flags `counter_reset_seen` on a sim restart; on `product_change` closes the run and sets the order Complete (logs good vs planned). After a gateway restart or reload it resumes the open run (same product), closes it (different product) or waits for the next run start. No Released order → unattributed run in memory. Pure `handle()` + parameterised `_SqlDb`. Tests: `ignition/tests/replay_workorder.py` (two loops against the DB-002 seed + six edge cases), shared builder `simreplay.py`. Loaded by Walker's Scan File System (no restart); live: run 1 → WO-1001 at the run-start sample, meters stepping 8 m.
**Files touched:** `mes/workorder/{code.py,resource.json}`, `mes/engine/code.py` (handler registration), `ignition/tests/{replay_workorder.py,simreplay.py,replay_engine.py}`.
**Deviations from spec:** Closed on the PM's checks without waiting for the Dev's live write-up (Walker). A `run_start` with a run already open is logged and ignored.
**User-facing impact:** Work orders go Running/Complete on their own; `Work Order/Active/*` tags populate. Docs: `architecture.md`, `db_schema.md`, handoff traps (Scan File System).

### DB-002 — Seed data loaded

**Date:** 2026-10-22
**Spec:** [`tasks/task_DB-002_apply_seed.md`](tasks/task_DB-002_apply_seed.md)
**Commit:** e2b88a4 (docs; the seed file itself is in 8d7770e)
**Verified by Walker:** ✅ 2026-10-22 — "Yes"

**What was built:** `db/002_seed.sql` applied unchanged in one transaction at 17:07:34Z after confirming all nine tables empty: 5 products (MES §7 values), 10 work orders (9 Released, WO-1010 Planned; all `released_at` equal), schedule 1–5 = WO-1001…1005, 6 readiness rows (WO-1006 not ready). WO-1004 due +6 min, WO-1009 (Rush) +20 min. PM verified in the live database. UNS-004 query tags now show values.
**Files touched:** none (database only); docs.
**Deviations from spec:** None.
**User-facing impact:** Product, Schedule and readiness tags carry seed data; the due-date clock started 17:07:34Z. Docs: `db_schema.md` status line.

### UNS-004 — Functional folders and tags in `[default]`

**Date:** 2026-10-22
**Spec:** [`tasks/task_UNS-004_functional_folders.md`](tasks/task_UNS-004_functional_folders.md)
**Commit:** 5ef02e7
**Verified by Walker:** ✅ 2026-10-22 — "Confirm" (Tag Browser), "Yes" (close-out)

**What was built:** `ignition/tags/generate_functional_tags.py` (stdlib, deterministic) writes 42 tags under `[default]40Printing/Dallas/Press Room/Press 1/`: `Product/101–105` (IdealSpeed, Lanes, ScheduledChangeoverSec — query, 60 s), `Work Order/Active` (6, query on `mes.v_work_order_table` status Running, 2 s), `Schedule` (HeadProductCode, QueueDepth, ReadinessOk query; OverrideActive, ReconcileResult, Override memory), `Downtime` (5 memory), `Planned Stop` (Active, Reason, Since, ConflictWithPLC query; Event memory), `OEE` (5 memory, WindowMinutes default 15). Query tags: connection `mes`, `Select`, `FixedRate`, one-row scalar subqueries, read-only. Memory tags writable, defaults set at creation. 22 files pushed byte-identical; Ignition restarted once (17:00:29Z) with Walker's OK; no decode or SQL errors; engine running.
**Files touched:** `ignition/tags/generate_functional_tags.py`, `ignition/tags/default/40Printing/Dallas/Press Room/Press 1/{Work Order,Schedule,Downtime,Planned Stop,OEE,Product}/**`.
**Deviations from spec:** None.
**User-facing impact:** Six folders, 42 tags in the Tag Browser; query tags empty until DB-002. Docs: `architecture.md` §1, `ignition_tag_namespace.md` status.

### ENG-001 — MES engine: `mes.engine`, `mesTick`, `PLC/SampleTime`

**Date:** 2026-10-22
**Spec:** [`tasks/task_ENG-001_mes_engine.md`](tasks/task_ENG-001_mes_engine.md)
**Commit:** b815236
**Verified by Walker:** ✅ 2026-10-22 — "Yes -- close out and write UNS-004"

**What was built:** Edge tag `PLC/SampleTime` (String, `toStr(jsonGet({[MQTT Engine]…/PLC/MachineState}, "timestamp"))`) added through the UNS-002 generator. Project library `mes.engine` (`ignition/projects/mes-2026/ignition/script-python/mes/engine/`): pure `step()` with de-dup on SampleTime, 10 s gap guard, meter deltas with restart → 0, 2-sample debounced segments for every state (backdated, closed on the first differing sample, split on fault-code change), `run_start` on Setup → Running, `product_change`; `tick()` reads the 17 `[default]` edge tags, logs each event at INFO and calls `HANDLERS` (empty). Read API `latest()`, `events_since()`, `open_segment()`, `totals()`, `info()`. Gateway timer `mesTick` (2000 ms fixed delay). Offline test `ignition/tests/replay_engine.py` replays two loops of `docs/sim/fourzero_printing_press1.json` through the real `code.py`: exact nominal counts and durations plus six edge cases, all pass. Ignition restarted once (16:46:51Z) with Walker's OK; live events in order, no repeats.
**Files touched:** `ignition/tags/generate_edge_tags.py`, `ignition/tags/default/…/PLC/tags.json`, `ignition/projects/mes-2026/ignition/script-python/mes/engine/{code.py,resource.json}`, `ignition/projects/mes-2026/ignition/timer/mesTick/{handleTimerEvent.py,resource.json}`, `ignition/tests/replay_engine.py`.
**Deviations from spec:** Live check tolerance (± 0.5 s per segment) was too tight — the live sim runs ~3 % slow; durations meet the MES §5 ± 2 s requirement. Minor field-name and bookkeeping choices accepted (see PM Verification).
**User-facing impact:** `PLC/SampleTime` visible as a 17th edge tag; `m.engine` event lines in the gateway log. No views. Docs: `architecture.md` §1, `ignition_tag_namespace.md` status, handoff traps (live drift).

### UNS-002 — Edge mirror: 16 typed press tags in `[default]` (DEC-007)

**Date:** 2026-10-22
**Spec:** [`tasks/task_UNS-002_edge_mirror_tags.md`](tasks/task_UNS-002_edge_mirror_tags.md)
**Commit:** 0e4cc58
**Verified by Walker:** ✅ 2026-10-22 — "Both passed"

**What was built:** `ignition/tags/generate_edge_tags.py` (stdlib, deterministic) generates Ignition 8.3 tag resource files under `ignition/tags/default/40Printing/Dallas/Press Room/Press 1/`: folders `PLC` (8), `Print Section` (3), `Unwind` (1), `Rewind` (1), `Tunnel Dryer` (3). Each tag is a read-only expression tag `toBoolean|toInt|toFloat(jsonGet({[MQTT Engine]40Printing/…}, "value"))` — Boolean for Connected/RTOInhibit, Int4 for state, codes, product, lanes and both meters, Float8 for the rest, with `engUnit` from Sim Spec §5. Files pushed with `docker exec … cat >` into the gateway's `tag-definition/default/`, byte-identical, owned `ignition:ignition`; Ignition restarted once (16:21:51Z) with Walker's OK. No decode errors in the log; Walker saw 16 Good, live, typed tags in the Tag Browser. CoreFlux and db not restarted. Method adopted from ign-mes-demo (read-only research).
**Files touched:** `ignition/tags/generate_edge_tags.py`, `ignition/tags/default/40Printing/**` (14 files).
**Deviations from spec:** Determinism checked by checksum (files untracked at the time). Marker files sorted with trailing newline (Ignition's own are not); both load.
**User-facing impact:** 16 typed live tags visible in the Tag Browser under `[default]40Printing/…/Press 1/`. VIS-001 trend buffer restarted empty once. Docs: `architecture.md` §1, `ignition_tag_namespace.md` status line.

### DB-001 — MES schema applied (`mes`, 9 tables, 4 views, empty)

**Date:** 2026-10-22
**Spec:** [`tasks/task_DB-001_apply_mes_schema.md`](tasks/task_DB-001_apply_mes_schema.md)
**Commit:** 8d7770e
**Verified by Walker:** ✅ 2026-10-22 — "Yes -- close out"

**What was built:** VERA's `db/001_schema.sql` applied unchanged in one transaction (`psql -1 -v ON_ERROR_STOP=1` via `docker exec`). Schema `mes` (owner `mes`) holds `product_master`, `work_order`, `work_order_run`, `schedule_entry`, `schedule_override`, `job_readiness`, `downtime_event`, `planned_stop`, `oee_interval`, all empty; 17 indexes (7 explicit, 9 PK, 1 unique); views `v_work_order_table`, `v_schedule_table`, `v_downtime_by_class`, `v_oee_by_product`; `work_order_seq` unused at 1001. PM verified in the live database. Seed (DB-002) not run.
**Files touched:** `db/001_schema.sql`, `db/002_seed.sql`, `docs/db_schema.md` (VERA's, first committed here; content unchanged except status markers).
**Deviations from spec:** None.
**User-facing impact:** None yet. `docs/db_schema.md` is the reference for the schema.

### INFRA-002 — Ignition database connection to PostgreSQL (+ dev DB password)

**Date:** 2026-10-22
**Spec:** none — Walker's GUI step (backlog INFRA-002)
**Commit:** 8d7770e
**Verified by Walker:** ✅ 2026-10-22 — "I created the ignition connection"

**What was built:** Ignition gateway database connection to host `db`, port 5432, database `mes`, user `mes` (created by Walker in the gateway UI). PM saw a live PostgreSQL JDBC session from 172.25.0.4. Before that (PM, with Walker's OK): the `mes` role's password set to the dev-only value `mes` (`ALTER USER` in the container, `POSTGRES_PASSWORD` in `.env`), `db` container recreated alone (`up -d --no-deps db`, volume kept, Ignition and CoreFlux not restarted). Checked through host port 5434: right password accepted, wrong password refused.
**Files touched:** `.env.example` (POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD now carry the dev values; header line changed). `.env` changed locally, not tracked.
**Deviations from spec:** n/a.
**User-facing impact:** Students who copy `.env.example` get a working database login.

### VIS-001 — Press 1 SCADA view in Perspective with gateway trend buffer

**Date:** 2026-10-21
**Spec:** [`tasks/task_VIS-001_press1_scada_perspective.md`](tasks/task_VIS-001_press1_scada_perspective.md)
**Commit:** 72dc415
**Verified by Walker:** ✅ 2026-10-21 — "if thats ok -- then apprvoed"

**What was built:** Ignition project `mes-2026`, now in the repo at `ignition/projects/mes-2026/` and bind-mounted into the gateway (D2). Project library `press1` reads the 16 MQTT Engine string tags under `[MQTT Engine]40Printing/Dallas/Press Room/Press 1` (one `ROOT` constant), unwraps the envelope and casts once, and keeps a 1,800-row (60 min) buffer in `system.util.getGlobals()['press1.v2']`. It also tracks meter deltas with restart handling, events (2-sample debounce, backdated, split on fault-code change), jobs observed, and a 10 s sampling-gap guard. A gateway timer `press1Tick` (2000 ms fixed delay, own thread) calls `press1.tick()`. Perspective page `/press1` (`Press1/Main`) has five tabs (Overview, Tunnel Dryer, Web & Print, Production, Alarms & State) and a 5/15/30/60 min window selector, and its trends use `ia.chart.timeseries` with "gateway buffer, N samples" footers. Colours live only in the project stylesheet. URL: http://localhost:8089/data/perspective/client/mes-2026/press1
**Files touched:** `docker-compose.yml` (one bind-mount line), `ignition/projects/mes-2026/**`.
**Deviations from spec:**
- Project reloads use the gateway's "Scan File System" button.
- `alarm_summary` counts since gateway start.
- Extra red `NO DATA` pill when the newest sample is more than 10 s old.
- Fixed axis tops (state trend 0–4, zone trend 250 °F).
- MES words trimmed from the reference footnotes.
- Versioned store key `press1.v2`, so an open Designer running old library code cannot wipe the live buffer.
- Views are written by a generator kept outside the repo. The files in the repo are the source of truth.
**User-facing impact:** The new SCADA page above. `docs/architecture.md` §1, §2, §5 updated at close-out. The buffer empties on any gateway restart. It also stops sampling while the Mac sleeps, and the gap guard keeps events and jobs honest.

### INFRA-001 — Dev stack: CoreFlux, Ignition and PostgreSQL

**Date:** 2026-10-20
**Spec:** [`tasks/task_INFRA-001_dev_stack_coreflux_ignition_postgres.md`](tasks/task_INFRA-001_dev_stack_coreflux_ignition_postgres.md)
**Commit:** 87af5b0
**Verified by Walker:** ✅ 2026-10-20 — "sim is pointed at 1886, no restart — close it out"

**What was built:** `docker-compose.yml` (project `mes-workshop-2026`) with three pinned services: `coreflux` (CoreFlux 2.2.0; host 127.0.0.1 1886 MQTT, 8084 web, 5004 API; volume `coreflux-data`), `db` (PostgreSQL 16.15; host 5434; DB/user `mes`) and `ignition` (8.3.10 Standard, host 8089, MQTT Engine/Transmission modules; now `depends_on` coreflux and a healthy db). Template `broker`/`app` services removed. `.env.example` lists the five new variable names; `.env` holds the generated secrets locally. All three healthy; MQTT round trip verified from the host and in-network. Walker then repointed the sim to 1886 (INFRA-004, DEC-004); all 16 sim topics confirmed on the new broker.
**Files touched:** `docker-compose.yml`, `.env.example`.
**Deviations from spec:** AC 6 test run with explicit MQTT client IDs — CoreFlux 2.2.0 refuses an empty client ID. Accepted; recorded as a trap in `agent_handoff.md` and in `architecture.md` §2.
**User-facing impact:** Ignition at http://localhost:8089, CoreFlux web UI at http://localhost:8084, the sim's broker is now `127.0.0.1:1886`. `docs/architecture.md` §1, §2, §5, §6 updated at close-out.

