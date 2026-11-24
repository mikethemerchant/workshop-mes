# Backlog

**Project:** MES Workshop 2026
**Maintained by:** the PM
**Format:** Work not yet specced, grouped by priority. When an item is specced it gets a task ID, a row in the task index (`project_management.md`), and a pointer from here to its spec. Done items are struck through, not deleted, until the next tidy.

**Priority:** P0 blocking · P1 needed for the next milestone · P2 degrades experience, workaround exists · P3 polish / later
**Effort:** S (one Dev run) · M (2–3 runs) · L (4+ runs — split it)

Build sequence approved by Walker 2026-10-20. Each item is verified against `docs/mes_requirements.md` (§ named per item). Effort is the PM's estimate.

---

## Current milestone — Day 1: dev stack up, live tags in Ignition, process visuals

Walker, 2026-10-20: "Today's only task after standing up the dev environment is to build process visuals in perspective based on a prompt I will get from VERA."

### ~~INFRA-001 — CoreFlux, Ignition and PostgreSQL in docker-compose~~

**Status:** ✅ Complete 2026-10-20 → `docs/tasks/task_INFRA-001_dev_stack_coreflux_ignition_postgres.md`
**Priority:** P0 · **Effort:** S
**Origin:** Walker, 2026-10-20 (build sequence; "stand up Ignition and CoreFlux first")

Replace the template skeleton with pinned CoreFlux, Ignition and PostgreSQL services on non-colliding host ports. Verify all healthy and a test message round-trips through the broker.

### INFRA-002 — Ignition database connection to PostgreSQL

**Status:** ✅ Complete 2026-10-22 — Walker's GUI step, no spec (see `completed_development.md`)
**Priority:** P1 · **Effort:** S
**Origin:** Walker, 2026-10-20 (build sequence)

The Ignition service itself moved into INFRA-001. What remains: the gateway's database connection to `db`. Not needed for VIS-001, so it can follow today's visuals. Licensing is a GUI-only step for Walker.

### INFRA-003 — Maestrohub, Timebase, Timebase Atlas

**Status:** Not specced
**Priority:** P1 · **Effort:** S
**Origin:** Walker, 2026-10-20 (build sequence)

Add the three services, pinned. Verify containers up and UIs reachable.

### ~~INFRA-004 — Broker cut-over (Walker-run)~~

**Status:** ✅ Done by Walker 2026-10-20, no sim restart (DEC-004)
**Priority:** P0 · **Effort:** S
**Origin:** Walker, 2026-10-20

Walker repoints the UNS Studio sim from the existing CoreFlux container (`coreflux-mqtt`, host port 1884) to this stack's CoreFlux (`127.0.0.1:1886`, from INFRA-001), once, before students pull data. May restart the sim and reset its counters. Log as a DEC in `decision-log.md`. Verify all 16 tags arrive on the new broker and the `4.0 Printing` level works.

### UNS-001 — Maestrohub ingest of the 16 raw tags

**Status:** Not specced
**Priority:** P0 · **Effort:** M
**Origin:** Walker, 2026-10-20 (build sequence)

Subscribe to the 16 tags, unwrap the envelope, cast every value, route to Ignition (`docs/sim_spec_press1.md` §11, `docs/mes_requirements.md` §2). Verify types stay correct through a Setup, Run and Fault cycle.

### UNS-002 — 16 live tags in Ignition (first visible)

**Status:** ✅ Complete 2026-10-22 → `docs/tasks/task_UNS-002_edge_mirror_tags.md` (re-scoped by DEC-007)
**Priority:** P0 · **Effort:** S
**Origin:** Walker, 2026-10-20 (build sequence); re-scoped 2026-10-22

Edge mirror: the 16 raw tags in `[default]40Printing/Dallas/Press Room/Press 1/` under `PLC`, `Print Section`, `Unwind`, `Rewind`, `Tunnel Dryer`, each mapped from its `[MQTT Engine]` topic, envelope unwrapped, typed and cast. The requirement is the topic-to-namespace mapping; Reference tags are optional (Walker). Tag definitions kept as JSON in the repo. No database. First thing students can see.

### ENG-001 — MES engine: `mes` library and 2 s gateway timer

**Status:** ✅ Complete 2026-10-22 → `docs/tasks/task_ENG-001_mes_engine.md`
**Priority:** P0 · **Effort:** M
**Origin:** PM proposal, approved by Walker 2026-10-22

New `mes` project library and gateway timer that read the UNS-002 edge tags every 2 s and hold the ingest state in gateway memory: last sample, state/fault transitions with the 2-sample debounce (backdated start, sample-timestamp durations), meter deltas with restart detection, product-code changes. Shared by WO, SCHED, DT and OEE. `press1` library and VIS-001 untouched.

### UNS-004 — Functional folders and tags

**Status:** ✅ Complete 2026-10-22 → `docs/tasks/task_UNS-004_functional_folders.md` (ran before DB-002; query tags fill once the seed runs)
**Priority:** P1 · **Effort:** S
**Origin:** DEC-007, Walker 2026-10-22

`Work Order`, `Schedule`, `Downtime`, `Planned Stop`, `OEE` (and `Product`, definitional) folders and tags per `docs/ignition_tag_namespace.md` §3, bound to the DB views where they read the database. Needs DB-002 (seed) and INFRA-002 ✅.

### UNS-005 — Publish the functional folders to the broker

**Status:** 📝 Specced, approved by Walker 2026-10-23 → `docs/tasks/task_UNS-005_publish_functional_folders.md` (after DT-003, OEE-002).
**Priority:** P2 · **Effort:** S
**Origin:** DEC-007

Publish `Schedule/Override`, `Planned Stop/Event`, `Downtime/Class`, `OEE/*`, `Work Order/Active/…` (MES §8) with MQTT Engine's script publish on `MES Coreflux` (Walker, Oct 22; DEC-009). Transmission and Event Streams not used. Never the edge folders. Expect the MQTT Engine echo. Timebase video stack will ingest them (accepted by Walker).

### ~~VIS-001 — Press 1 process visuals in Perspective~~

**Status:** ✅ Complete 2026-10-21 → `docs/tasks/task_VIS-001_press1_scada_perspective.md` (from VERA's prompt, 2026-10-20)
**Priority:** P1 · **Effort:** M (to confirm once the prompt arrives)
**Origin:** Walker, 2026-10-20 (quoted above)

Process visuals of Press 1 in Perspective, built on the UNS-002 tags. Scope comes from VERA's prompt; the PM writes the spec from it. Open question: anything in the prompt not covered by the workshop docs goes to Walker.

---

## Later

**Build order (Walker, 2026-10-22):** UNS-002 ✅ → ENG-001 ✅ → UNS-004 ✅ → DB-002 ✅ → WO → SCHED (Hour 2) → DT → OEE (Hour 3) → UNS-005 → VIS-002 → AI-001 (Hour 4). Publishing in WO/SCHED/DT/OEE moved to UNS-005.

Day 1 / before Day 2:

- UNS-003 — Timebase records the 16 tags; trend over one loop
- DB-001 — The nine tables (`docs/mes_requirements.md` §7) — ✅ Complete 2026-10-22 → `docs/tasks/task_DB-001_apply_mes_schema.md`
- DB-002 — Seed: 5 products, 10 work orders (101–105 twice, two tight due dates), schedule in sim order (§3, §7) — ✅ Complete 2026-10-22 (ran 17:07:34Z) → `docs/tasks/task_DB-002_apply_seed.md`

Work Orders (test visual Day 2 Hr 2):

- WO-001 — Run attribution, Running and Complete from the PLC, meter deltas with restart handling (§3) — ✅ Complete 2026-10-22 → `docs/tasks/task_WO-001_run_attribution.md`. `Work Order/Active/*` are query tags (UNS-004); publishing is UNS-005.
- WO-002 — ✅ Complete 2026-10-22 → `docs/tasks/task_WO-002_work_order_test_table.md` (`/mes/work-orders`) · WO-001-fix1 (BUG-002) — ✅ Complete 2026-10-22 · WO-003 — ✅ Complete 2026-10-22 → `docs/tasks/task_WO-003_manual_actions.md`
- WO-004 (follow-ups from WO-003/SCHED-001, not specced): assigned unattributed run still listed as unattributed; late delta on a just-assigned unattributed run not written; Create date picker opens at 00:00 and the panel keeps inputs (duplicate on double click); schedule resequencing can duplicate sequence numbers once the press is out of plan order or a row is Cancelled (SCHED-003); OEE Setup start after a full gateway restart could come from the engine's open segment (OEE-001); SCHED entry 1 actual start could come from the engine's `product_change` when the previous run is unattributed. Fold into VIS-002 where they are UI.

Scheduling (test visual Day 2 Hr 2):

- SCHED-001 — Planned start and end, plan-vs-actual test table (§4) — ✅ Complete 2026-10-22 → `docs/tasks/task_SCHED-001_plan_vs_actual.md` (`/mes/schedule`)
- SCHED-002 — Readiness entry and "started without readiness" flag (§4) — ✅ Complete 2026-10-22 → `docs/tasks/task_SCHED-002_readiness.md`
- SCHED-003 — Override capture (`Schedule/Override` tag), reconcile; four outcomes via the demo recipe (§4.1) — ✅ Complete 2026-10-22

Downtime (test visual Day 2 Hr 3):

- DT-001 — Event detection (debounce, 101/102/103/Unclassified), `downtime_event`, `Downtime/Class` tag (§5) — ✅ Complete 2026-10-22
- DT-002 — Setup split into planned changeover and overrun; two 14 s overruns (§5) — ✅ Complete 2026-10-22
- DT-003 — Planned stop entry, `Planned Stop/Event` tag, conflict flag (§5) — 📝 Specced, approved → `docs/tasks/task_DT-003_planned_stops.md` (after OEE-002)
- DT-004 — Downtime test table and totals by class; 15 alarms per loop (§5) — ✅ Complete 2026-10-23 → `docs/tasks/task_DT-004_downtime_test_table.md` (`/mes/downtime`)

OEE (test visual Day 2 Hr 3):

- OEE-001 — `oee_interval` writer, one row per minute per product (§6) — ✅ Complete 2026-10-22
- OEE-002 — Window aggregation, `OEE/*` tags, four tiles and per-job table; within 1–2 points of §6 — 📝 Specced, approved → `docs/tasks/task_OEE-002_oee_window_tiles.md` (next)

Day 2 Hr 4:

- AI-001 — Timebase Atlas model and the §9 AI queries — not specced. **Needs INFRA-003 (Timebase, Atlas), which is not deployed**; ask Walker how to source history and the model.
- VIS-002 — Real MES visuals in one pass — 📝 Specced, approved → `docs/tasks/task_VIS-002_mes_operator_ui.md` (one `/mes` page; after DT-003, OEE-002)

## Parked / won't do

- None yet.
