# MES Database Schema — PostgreSQL 16 (DB-001, DB-002)

*Draft v0.2, Oct 22, 2026, by VERA. Built from `mes_requirements.md` §3 to §7 (table list confirmed by Walker, Oct 20). Committed Oct 22. The SQL lives in `db/001_schema.sql` and `db/002_seed.sql`. This page explains it and does not repeat it, so the two cannot drift.*

**Dry run (Oct 22):** the full schema and seed ran inside one transaction on the live `mes` database and were rolled back. 9 tables, 4 views, 5 products, 10 work orders (9 Released, 1 Planned), 5 schedule entries and 6 readiness rows, all as expected. Nothing was left behind. **Applied for real Oct 22:** schema (DB-001) and seed (DB-002, ran 17:07:34 UTC, so WO-1004 was due 17:13:34 and WO-1009 17:27:34).

## 1. Rules this schema follows

- **Nine tables, as confirmed:** `product_master`, `work_order`, `work_order_run`, `schedule_entry`, `schedule_override`, `job_readiness`, `downtime_event`, `planned_stop`, `oee_interval`. All live in a schema called `mes`, not `public`.
- **Raw samples are not stored here.** Timebase holds the history. This database holds only what the MES computes or a person enters.
- **Nothing here comes from the sim.** Product master, work orders, readiness, overrides and planned stops are MES data.
- **All timestamps are `timestamptz`.** The sim stamps UTC. Display in CDT in the front end.
- **Meters are integers.** The sim publishes floats such as `54.0`. Cast on ingest. Meters are cumulative, so rows store deltas, and a counter that drops to 0 is a restart, not negative production.
- **Work order IDs are text** (`WO-1001`), generated from a sequence that starts at 1001. Easier to read in a bare table.
- **This script assumes an empty `mes` schema.** Run it once. To reset a dev database, `drop schema mes cascade;` deletes everything in it, so only run that with Walker's go-ahead.
- **Ingest state is not in the database.** The last sample, the open downtime event and the counter baselines live in the Ignition gateway. A gateway restart loses only the open event. (Walker confirmed, Oct 22.)

## 2. Entity to table

| Table | Written by | Spec section |
| --- | --- | --- |
| `product_master` | Seed, then admin edits | MES §7 |
| `work_order` | Front end (people), MES logic (Running, Complete — `mes.workorder`, WO-001) | MES §3 |
| `work_order_run` | MES logic from the PLC feed (`mes.workorder`, WO-001). Operator can reassign (WO-003). | MES §3 |
| `schedule_entry` | Front end and scheduling logic | MES §4 |
| `schedule_override` | Front end ("move to front") | MES §4.1 |
| `job_readiness` | Front end (people) | MES §4 |
| `downtime_event` | MES logic from state and fault code | MES §5 |
| `planned_stop` | Front end (operator) | MES §5 |
| `oee_interval` | MES logic, one row per minute per product code | MES §6 |

## 3. Views for the Day 2 test visuals

Each Hour 2 and Hour 3 test visual reads one view.

| View | Test visual | Hour |
| --- | --- | --- |
| `mes.v_work_order_table` | Work orders | 2 |
| `mes.v_schedule_table` | Scheduling | 2 |
| `mes.v_downtime_by_class` | Downtime totals by class (closed events only) | 3 |
| `mes.v_oee_by_product` | A, P, Q and OEE by product code. Performance capped at 100 for display. | 3 |

## 4. Columns added beyond the entity lists in MES Requirements

| Column | Table | Why |
| --- | --- | --- |
| `start_good_counter`, `start_reject_counter`, `counter_reset_seen` | `work_order_run` | Lets the MES prove a delta and flag a counter drop to 0 as a restart. |
| `actual_start_ts`, `actual_end_ts`, `reconcile_result`, `started_without_readiness` | `schedule_entry` | The scheduling test visual needs plan against actual and the reconcile result. |
| `published_at` | `schedule_override`, `planned_stop` | Shows whether the MES has published the record to the UNS yet. |
| `is_ready` (generated) | `job_readiness` | One flag for "all three entered". |
| `Changeover` value in `class` | `downtime_event` | The planned part of a Setup event (MES §5 splits Setup at the scheduled seconds). |

## 5. Checks after applying

Run these after DB-001 and DB-002. Expected values are in the comments.

```sql
select count(*) from mes.product_master;                      -- 5
select count(*) from mes.work_order;                          -- 10
select status, count(*) from mes.work_order group by status;  -- Released 9, Planned 1
select count(*) from mes.schedule_entry;                      -- 5
select count(*) from mes.job_readiness;                       -- 6
select * from mes.v_work_order_table order by work_order_id;  -- 10 rows, good_m and reject_m all 0
select * from mes.v_schedule_table;                           -- 5 rows, sequence 1 to 5
```

## 6. Seed notes

- **Product master** matches About §3: ideal speed and scheduled changeover per product code.
- **Work orders:** two loops of the five jobs in sim order (101 to 105, then 101 to 105). Planned meters equal what the sim makes per job visit: 600, 375, 450, 525 and 365. Nine are Released so run attribution works, and WO-1010 is Planned.
- **Tight due dates:** WO-1004 (6 minutes) and WO-1009 (20 minutes, Rush). Due dates are relative to `now()`, so they are tight only from the moment the seed runs. **Run the seed right before the Hour 2 test**, and test with whole-loop deltas, because the loop position is unknown.
- **Readiness:** WO-1001 to WO-1005 are fully ready. WO-1006 is missing proof approval, as a not-ready demo. The rest have no row, which means nothing has been entered.
- **Schedule:** the first five work orders in sim order. Planned start and end are filled in by the scheduling backend (SCHED-001).

## 7. Connection

Inside Docker the database is host `db`, port 5432, database `mes`, user `mes`. From the host it is `127.0.0.1:5434`. The password is the dev-only value in `.env.example` (Walker, Oct 22; this stack binds to localhost only). Ignition reaches the database through its own connection (INFRA-002, a GUI step).

## 8. Notes

- `downtime_event.source = 'operator'` is allowed by the entity list but unused here, because planned stops have their own table.
- A stray `Changeover` check ties `is_planned` to the class: only Changeover is planned inside `downtime_event`. Planned stops (clean plates, roll change) live in `planned_stop`.
