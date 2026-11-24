# Project Management

**Project:** MES Workshop 2026
**Status:** Canonical workflow document
**Last updated:** 2026-10-20

---

## 1. Roles

- **PM** — Claude Code session running `/pm`. Plans, writes specs, maintains living docs, verifies Dev output by reading files. Never writes production code.
- **Dev** — a separate Claude Code session running `/dev <spec>`. Implements one spec, runs the gates, reports through files.
- **Lead (Walker or VERA)** — makes all product decisions, approves specs, runs GUI-only steps, runtime-tests.

## 2. Task lifecycle

See `docs/development_workflow.md` §1. In short: **spec → Dev → Dev Report → PM verify → lead runtime-test → PM close-out.**

## 3. Task IDs

`<AREA>-NNN` (e.g. `INFRA-001`, `SIM-003`, `BUG-012`). Follow-up fixes to a verified-failing task are `<ID>-fix1`, `-fix2`. Slices of a large task are `<ID>-S1`, `-S2`.

## 4. Task Spec Format

File: `docs/tasks/task_<ID>_<slug>.md`. The worked example is `docs/tasks/_archive/task_EXAMPLE-001_example_task.md`. Sections, in order:

| Section | Owner | Purpose |
|---|---|---|
| Header (Status, Priority, Effort, Depends on, Bug) | PM (Dev updates Status) | At-a-glance state |
| Background | PM | Why the task exists; what the user experiences after |
| What Already Exists (Do Not Rewrite) | PM | Working code/infra the Dev must not touch, with paths and line refs |
| What to Build | PM | Exact implementation — files, signatures, contracts, error handling |
| Acceptance Criteria | PM | Testable checkboxes; the PM verifies each against the code |
| Files to Modify | PM | Table of path → change |
| Dev Instructions | PM | 2–4 sentence restatement + every hard constraint |
| Dev Report | **Dev** | What was built, files, gate output, deviations, open questions |
| PM Verification | PM | Rule 7 findings, added after the Dev reports |

**Rules for writing specs:**
- Read the relevant source before writing. Never spec from memory.
- Check `completed_development.md` and `bug_log.md` first.
- Never leave an architecture decision to the Dev. If the Dev would have to guess, the spec is incomplete.
- Name every contract/user doc that must change in the same commit (Rule 11).

## 5. Dev Command Format

The PM gives the lead this, to run in a **separate terminal**:

```
claude "/dev docs/tasks/task_<ID>_<slug>.md"
```

The `/dev` command loads the Dev role, reads the instructions and the spec, and binds the Dev to report through the spec's `Dev Report` and `agent_handoff.md`.

## 6. Verification Discipline (Rule 7)

After the Dev reports done, the PM:

1. Reads the Dev Report and the handoff's Dev status.
2. Runs `git status` / `git diff` to find every changed file — including any the Dev did not mention.
3. Reads each changed file and its surrounding context; checks for collateral damage and scope creep.
4. Checks each Acceptance Criterion against the code, and re-runs the gates where cheap.
5. Writes findings under `## PM Verification` in the spec — pass, or exactly what failed.

## 7. Task Index

One row per task for its whole lifecycle — edit in place, never append a second row.

**Status values:** 📝 Specced · 🔨 In progress · 🔍 Awaiting PM verify · 🧪 Awaiting lead test · ✅ Complete · ⛔ Blocked · 🗄️ Superseded

| ID | Summary | Spec | Status | Lead-verified |
|---|---|---|---|---|
| INFRA-001 | Dev stack: CoreFlux 2.2.0 (MQTT on 1886), Ignition 8.3.10 (8089), PostgreSQL 16 (5434) in docker-compose | `docs/tasks/task_INFRA-001_dev_stack_coreflux_ignition_postgres.md` | ✅ Complete | Walker 2026-10-20 |
| VIS-001 | Press 1 SCADA view in Perspective (`mes-2026`), raw PLC data, gateway in-memory trend buffer | `docs/tasks/task_VIS-001_press1_scada_perspective.md` | ✅ Complete | Walker 2026-10-21 |
| DB-001 | Apply `db/001_schema.sql`: schema `mes`, 9 tables, 4 views, empty | `docs/tasks/task_DB-001_apply_mes_schema.md` | ✅ Complete | Walker 2026-10-22 |
| DB-002 | Apply `db/002_seed.sql`: 5 products, 10 WOs, readiness, schedule. Walker triggers it before Hour 2. | `docs/tasks/task_DB-002_apply_seed.md` | ✅ Complete | Walker 2026-10-22 |
| UNS-002 | Edge mirror: 16 typed expression tags in `[default]40Printing/…/Press 1/{PLC,Print Section,Unwind,Rewind,Tunnel Dryer}`, generated as 8.3 tag files; one Ignition restart (DEC-007) | `docs/tasks/task_UNS-002_edge_mirror_tags.md` | ✅ Complete | Walker 2026-10-22 |
| ENG-001 | MES engine: `mes.engine` library + `mesTick` 2 s timer on the `[default]` edge tags; new edge tag `PLC/SampleTime`; one Ignition restart | `docs/tasks/task_ENG-001_mes_engine.md` | ✅ Complete | Walker 2026-10-22 |
| UNS-004 | Functional folders: 42 tags in `Work Order`, `Schedule`, `Downtime`, `Planned Stop`, `OEE`, `Product` — query tags where the record is in PostgreSQL, memory tags where the truth is in the gateway; one Ignition restart | `docs/tasks/task_UNS-004_functional_folders.md` | ✅ Complete | Walker 2026-10-22 |
| WO-001 | Work orders from the PLC: `mes.workorder` engine handler — attribution (earliest Released, tie on ID), Running/Complete, meter deltas into `work_order_run`, restart/resume; offline replay; no container restart | `docs/tasks/task_WO-001_run_attribution.md` | ✅ Complete | Walker 2026-10-22 |
| WO-002 | Work order test table: bare Perspective page `/mes/work-orders` (view `Mes/Test/WorkOrders`) from `mes.v_work_order_table` + `mes.workorder`, via new `mes.ui`; Scan File System, no restart | `docs/tasks/task_WO-002_work_order_test_table.md` | ✅ Complete | Walker 2026-10-22 |
| WO-003 | Work order manual actions: create, release, hold, cancel, close (status-guarded transitions) and reassign (status from runs; open-run reassign queued to the timer thread); panel on `/mes/work-orders`; no restart | `docs/tasks/task_WO-003_manual_actions.md` | ✅ Complete | Walker 2026-10-22 |
| WO-001-fix1 | BUG-002: credit late meter deltas on Setup samples to the run just closed (rule 8); lag-injected replay; no restart | `docs/tasks/task_WO-001-fix1_late_meter_delta.md` | ✅ Complete | Walker 2026-10-22 |
| SCHED-001 | Schedule: `mes.schedule` handler — replan on request (changeover + planned m at ideal speed), actuals/status synced from runs, append Released orders; `/mes/schedule` test page; no restart | `docs/tasks/task_SCHED-001_plan_vs_actual.md` | ✅ Complete | Walker 2026-10-22 |
| SCHED-002 | Readiness entry (upsert `job_readiness`, refused after start) and `started_without_readiness` set on Queued → Running; panel on `/mes/schedule`; no restart | `docs/tasks/task_SCHED-002_readiness.md` | ✅ Complete | Walker 2026-10-22 (spec) |
| SCHED-003 | Rush override (move to front + `schedule_override`, resequence, replan, `Schedule/Override` JSON tag), plan edit without record, reconcile at run start (four §4.1 outcomes); after SCHED-002 | `docs/tasks/task_SCHED-003_override_reconcile.md` | ✅ Complete | Walker 2026-10-22 (spec) |
| DT-001 | Downtime: `mes.downtime` handler — fault classes 101/102/103/Unclassified, one complete `downtime_event` row per closed fault (WO captured at start), live `Downtime/*` tags; parallel-safe with SCHED-003 | `docs/tasks/task_DT-001_downtime_events.md` | ✅ Complete | Walker 2026-10-22 (spec) |
| DT-002 | Setup split at the scheduled changeover: planned `Changeover` + derived `Changeover overrun` rows; tags Planned → Unplanned; after DT-001 | `docs/tasks/task_DT-002_setup_split.md` | ✅ Complete | Walker 2026-10-22 (spec) |
| OEE-001 | `mes.oee` handler: per-minute, per-product `oee_interval` upserts (running, planned production with Setup split, good/reject, ideal); reload-safe; after DT-001 | `docs/tasks/task_OEE-001_oee_intervals.md` | ✅ Complete | Walker 2026-10-22 (spec) |
| OEE-002 | OEE window (A/P/Q/OEE every 10 s → `OEE/*` tags), window selector, `/mes/oee` test page (tiles, per product / WO / hour); after OEE-001 | `docs/tasks/task_OEE-002_oee_window_tiles.md` | 📝 Specced — approved by Walker 2026-10-22; ready (OEE-001 ✅; DT-004 ✅) | — |
| DT-004 | Downtime test page `/mes/downtime`: events table, totals by class (planned/unplanned summary), live now-line; `mes.ui` only; parallel-safe with DT-002/OEE-001, not with OEE-002 | `docs/tasks/task_DT-004_downtime_test_table.md` | ✅ Complete 2026-10-23 | — |
| DT-003 | Operator planned stops (clean plates / roll change): start/end, conflict if PLC Running, `Planned Stop/Event` JSON, OEE exclusion of non-running overlap; after DT-002, OEE-001, DT-004 | `docs/tasks/task_DT-003_planned_stops.md` | 📝 Specced — approved by Walker 2026-10-22; after OEE-002 (both edit `mes/oee`) | — |
| UNS-005 | Publish the 13 allowlisted functional tags (Downtime/Class, OEE/*, Work Order/Active/*, Schedule/Override, Planned Stop/Event) via MQTT Engine `system.cirruslink.engine.publish` on `MES Coreflux` (Walker 2026-10-22); `mesPublish` 2 s timer; guard refuses edge folders and Product; after DT-003, OEE-002 | `docs/tasks/task_UNS-005_publish_functional_folders.md` | 📝 Specced — approved by Walker 2026-10-23; after DT-003, OEE-002 | — |
| VIS-002 | Real MES screens: one `/mes` page (Overview, Work Orders, Schedule, Downtime, OEE tabs) in the `/press1` look; existing `mes.*` functions only; operator actions moved over with WO-004 UI fixes (clear inputs, busy-disable, inline confirm, date picker now+24 h); test pages kept (Walker 2026-10-22, PM default); 4 steps, scan after each; after DT-003, OEE-002 | `docs/tasks/task_VIS-002_mes_operator_ui.md` | 📝 Specced — approved by Walker 2026-10-23; after DT-003, OEE-002 | — |
| EXAMPLE-001 | Format example from the template. | `docs/tasks/_archive/task_EXAMPLE-001_example_task.md` | 🗄️ Archived | — |

## 8. Bug Log Workflow

A bug gets a `docs/bug_log.md` entry when found. Before it is fixed it gets a spec (`task_BUG-NNN_<slug>.md`) and a task-index row. It is marked Fixed, with the root cause, only at close-out after Rule 7 verification.

## 9. Common Failure Modes

- Spec written from memory; the code had moved on.
- Acceptance Criteria that can't be checked by reading code or running a command.
- Dev report accepted without reading the diff.
- Task-index row flipped to Complete without the `completed_development.md` entry in the same turn.
- `agent_handoff.md` left describing last week.
