# SCHED-001 — Schedule: planned start/end, actuals from the PLC, plan-vs-actual test page

**Status:** ✅ Complete — 2026-10-22, verified by the PM, approved by Walker
**Priority:** P1 · **Effort:** M (one Dev session; no restart)
**Depends on:** WO-001 + fix1 ✅, WO-002 ✅. Runs alongside WO-003 only if the Dev sessions do not edit the same files (this task does not touch `mes/workorder`).
**Bug:** —

---

## Background

MES §4 FR1–3 and FR7: sequence the Press 1 queue, compute planned start and end per entry (scheduled changeover + planned meters at ideal speed), and show plan against actual. The §4 table expects the press to run ~30 % behind plan (101: plan 164 s, actual 198 s, … loop 808 s vs 1,046 s). Readiness (SCHED-002) and overrides / reconcile (SCHED-003) come later; this task leaves `reconcile_result` and `started_without_readiness` alone.

**State now (PM, 12:45 CDT):** the seed schedule holds WO-1001…1005 (sequence 1–5), and all five have already run and completed (runs 1–5). WO-1006…1008 ran unscheduled; WO-1009 is Released; WO-1010 Planned. So the first deploy must **backfill** planned and actual times for entries that already ran.

## Rules

1. **Planned times (replan).** `replan()` recomputes `planned_start` / `planned_end` for **every** non-Cancelled entry in `sequence` order:
   - anchor = `actual_start_ts` of the lowest-sequence entry if it has one, else now;
   - `planned_start(1)` = anchor; `planned_end(k) = planned_start(k) + scheduled_changeover_s + planned_meters / ideal_speed_mpm × 60 s` (product master values; changeover from `schedule_entry.scheduled_changeover_s`); `planned_start(k+1) = planned_end(k)`.
   - Planned start = start of the changeover (Setup), matching the §4 table ("planned total = changeover + ideal run").
   - **Replan only when asked:** on the first tick after load if any entry has `planned_start` NULL, from the page's **Replan** button, and (later) by SCHED-003. Never on every tick — the plan must stay fixed so drift is visible.
2. **Actuals from the runs (sync).** `sync_from_runs()` sets, for every entry whose work order has a `work_order_run`:
   - `actual_start_ts` = the **previous run's `end_ts`** (the product change that began this job's Setup) when that end is ≤ 300 s before this run's `start_ts`; otherwise this run's `start_ts` (no Setup observed, e.g. after a gateway restart);
   - `actual_end_ts` = this run's `end_ts` (NULL while open);
   - `status` = `Running` while the run is open, `Done` once closed; entries without a run stay `Queued`; `Cancelled` is never touched.
   - Idempotent (recomputes from the database every time). Called on the first tick after load and on every `run_start` and `product_change` event.
3. **Schedule released orders (FR1).** `schedule_released()` appends every `Released` order that has no `schedule_entry` to the end of the sequence (`max(sequence) + 1, …`), ordered by `priority = 'Rush'` first, then `due_date`, then `work_order_id`; `scheduled_changeover_s` from product master. Called only from the page's button. Then `replan()`.
4. All SQL parameterised, `database='mes'`, `UPDATE`/`INSERT` on `mes.schedule_entry` only. Never delete rows. No tag writes (the `Schedule/*` query tags already read `schedule_entry`).

## What Already Exists (Do Not Rewrite)

- `mes.engine` (`HANDLERS = [_workorder]`), `mes.workorder` (rules 1–8). **This task adds a second handler after `_workorder`** (attribution must run first) and does not edit `mes/workorder`.
- Schema: `schedule_entry` (`sequence`, `planned_start`, `planned_end`, `scheduled_changeover_s`, `status` Queued/Running/Done/Cancelled, `actual_start_ts`, `actual_end_ts`, `reconcile_result`, `started_without_readiness`), view `v_schedule_table`, `product_master.ideal_speed_mpm`. No schema change.
- `mes.ui`, `/mes/work-orders` (WO-002). WO-003 may be editing the WorkOrders view and `mes/workorder` in parallel — **do not touch those files**.
- Tests: `ignition/tests/simreplay.py`, `replay_workorder.py` (FakeDb with the seed). Must still pass.

## What to Build

### 1. Library `mes.schedule`

`ignition/projects/mes-2026/ignition/script-python/mes/schedule/code.py` + `resource.json`. Same conventions as `mes.workorder`: pure planning functions + a `db` interface + `_SqlDb`; Jython 2.7 / Python 3 compatible.
- Pure: `plan(entries, anchor_ms)` → list of `(entry_id, planned_start_ms, planned_end_ms)`; `actuals(entries, runs)` → list of `(entry_id, status, actual_start_ms, actual_end_ms)`; `append_order(released, max_seq)` → new entries.
- I/O: `replan(anchor_ms=None)`, `sync_from_runs()`, `schedule_released()` — each returns a result string (`"ok: …"` / `"error: …"`) and logs it at INFO on `mes.schedule`.
- Handler `on_tick(sample, events)`: first tick after load (store `mes.schedule.v1` with `loaded: True`) → `sync_from_runs()`, then `replan()` if any `planned_start` is NULL; afterwards `sync_from_runs()` on any `run_start` or `product_change` event.

Register in `mes/engine/code.py`:
```python
def _schedule(sample, events):
	mes.schedule.on_tick(sample, events)

HANDLERS = [_workorder, _schedule]
```

### 2. Test page `/mes/schedule`

View `Mes/Test/Schedule` (same bare style and `now(2000)` polling pattern as `Mes/Test/WorkOrders`), page entry `"/mes/schedule"` in `page-config/config.json` (add only).
- Table from a new `mes.schedule.schedule_rows()` over `mes.v_schedule_table` (+ `status`, `scheduled_changeover_s` from `schedule_entry`): Seq, Work order, Product, Status, Planned start, Planned end, Planned s, Actual start, Actual end, Actual s, Gap s (actual − planned, once Done), Plates, Ink, Proof. Times `HH:mm:ss` local.
- Buttons **Replan** and **Schedule released orders**, result label.
- Put `schedule_rows()` and a pure `shape_schedule()` in `mes.schedule` (**not** `mes.ui` — WO-003 may be editing `mes/ui/code.py` in parallel).

### 3. Offline test

`ignition/tests/replay_schedule.py` (runs under `python3 -I`): two-loop nominal replay through engine → workorder → schedule with a FakeDb holding the seed schedule.
- After loop 1: entries 1–5 `Done`; planned totals **164, 155, 159, 163, 167 s** (±1 s rounding); actual totals (actual_end − actual_start) **198, 224, 202, 214, 208 s**; entry 1's actual start = its run start (no previous run), entries 2–5 = previous run's end; planned chain anchored at entry 1's actual start; loop plan **808 s** vs actual **1,046 s**.
- `replan()` is not called by `run_start` / `product_change` (planned times unchanged after loop 2).
- `schedule_released()` with WO-1006…1009 Released and unscheduled → appended as 6–9 with WO-1009 (Rush) first, then by due date; WO-1010 (Planned) not added.
- Entry whose order is Cancelled → untouched. Gap > 300 s → actual start = run start.
- Existing tests still pass.

### 4. Load and live check

1. **Stop** after the tests: Dev Report; `agent_handoff.md`: "Ready to load — waiting for Walker (Scan File System)."
2. After the scan: log shows the first-tick sync and replan. Paste:
   ```sql
   select sequence, work_order_id, status, planned_start, planned_end, actual_start_ts, actual_end_ts,
          extract(epoch from planned_end - planned_start) as planned_s,
          extract(epoch from actual_end_ts - actual_start_ts) as actual_s
   from mes.schedule_entry order by sequence;
   ```
   Expect entries 1–5 Done with planned 164/155/159/163/167 s and actual ≈ 198/224/202/214/208 s × ~1.03 (live sim ~3 % slow; entry 1 may be shorter if its Setup was not observed).
3. Open `/mes/schedule` (or ask Walker) and confirm it matches. **Do not press the buttons live** — Walker demonstrates "Schedule released orders" in class.

## Acceptance Criteria

- [ ] Rules 1–4 exactly; pure `plan`/`actuals`/`append_order`; parameterised SQL; `schedule_entry` writes only.
- [ ] Handler registered after `_workorder`; `replay_engine.py`, `replay_workorder.py`, `test_ui_work_orders.py`, `replay_schedule.py` all pass under `python3 -I`.
- [ ] Live: entries 1–5 backfilled (Done, planned and actual times) as expected; page shows them.
- [ ] No edits to `mes/workorder`, `mes/ui`, the WorkOrders view, `press1`, VIS-001 views, tag files, `db/`. No container restart.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/schedule/code.py`, `resource.json` | New |
| `ignition/projects/mes-2026/ignition/script-python/mes/engine/code.py` | Register `_schedule` only |
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/views/Mes/Test/Schedule/view.json`, `resource.json` | New |
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/page-config/config.json` | Add `/mes/schedule` |
| `ignition/tests/replay_schedule.py` | New |
| `docs/tasks/task_SCHED-001_plan_vs_actual.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

## Dev Instructions

Spec: `docs/tasks/task_SCHED-001_plan_vs_actual.md`. Build `mes.schedule` as a second engine handler: replan planned start/end from changeover + planned meters at ideal speed (only when asked), sync actual start/end and status from the work-order runs on every run start and product change, and append Released unscheduled orders on request; add the bare `/mes/schedule` test page. Prove it with a two-loop replay (plan 164/155/159/163/167 s vs actual 198/224/202/214/208 s), **stop for Walker's Scan File System**, then check the backfilled rows live.

Hard constraints:
- Do not edit `mes/workorder/code.py`, `mes/ui/code.py` or the `Mes/Test/WorkOrders` view (WO-003 may be editing them). Engine: register the handler only.
- No container restarts. No schema change. Parameterised SQL; write only `mes.schedule_entry`; never delete rows. No tag writes; publish nothing.
- Do not press Replan / Schedule released orders live.
- Never stop, start, update or reimport the sim; never touch UNS Studio. Never read `[MQTT Engine]`, `4.0 Printing/…`, `Probe/…`.
- Never touch `ign-mes-demo`. Never print `.env` or any password. No commit, no push; stage nothing.
- Anything this spec does not cover: stop and ask in the Dev Report.

## Dev Report

**Status:** ✅ Done — ready for PM verification. Loaded by Walker's Scan File System 12:47:39 CDT; backfill checked in the database. The page itself is not yet eyeballed (Walker to open `/mes/schedule`).
**Date:** 2026-10-22

### What was built

- **`mes/schedule/code.py`** (new), Jython 2.7 / Python 3, same layout as `mes.workorder`:
  - Pure: `plan(entries, anchor_ms)` → `[(entry_id, planned_start_ms, planned_end_ms)]` (rule 1: `scheduled_changeover_s + planned_meters / ideal_speed_mpm × 60`, ms-rounded, chained in `sequence` order); `anchor(entries, now_ms)`; `actuals(entries, runs)` → `[(entry_id, status, actual_start_ms, actual_end_ms)]` (rule 2, 300 s Setup window `SETUP_MAX_S`); `append_order(released, max_seq)` → `[(work_order_id, sequence, scheduled_changeover_s)]` (rule 3: Rush, due date, work_order_id); `shape_schedule(rows, fmt)` (14 columns, `SCHEDULE_COLUMNS`).
  - On a db argument: `do_replan(db, now_ms, anchor_ms=None)`, `do_sync(db)`, `do_schedule_released(db, now_ms)` (appends, then replans). Each writes only the rows whose values changed and returns `"ok: …"`.
  - `handle(st, sample, events, db)`: first tick (`st['loaded']` False) → sync, then replan if any non-Cancelled entry has `planned_start` NULL (anchor fallback "now" = the sample time); afterwards sync when the tick carries a `run_start` or `product_change`. Never replans from events.
  - Gateway wrappers `replan(anchor_ms=None)`, `sync_from_runs()`, `schedule_released()` → result string, logged INFO on `mes.schedule` (an exception returns `"error: …"` and logs ERROR with the traceback). `on_tick(sample, events)` with store `mes.schedule.v1` (`loaded`). `schedule_rows()` reads `mes.v_schedule_table` joined to `schedule_entry` (status, scheduled_changeover_s) and calls `shape_schedule` with `system.date.format` (gateway TZ, `HH:mm:ss`).
  - `_SqlDb`: parameterised SQL on connection `mes`. Writes are `UPDATE mes.schedule_entry` (plan; status/actuals with `AND status <> 'Cancelled'`) and `INSERT INTO mes.schedule_entry` only. No deletes, no tag writes. `ideal_speed_mpm::float8` cast in SQL (numeric → BigDecimal in Jython).
- **`mes/schedule/resource.json`** (new): copy of `mes/workorder/resource.json`.
- **`mes/engine/code.py`**: `_schedule` handler added, `HANDLERS = [_workorder, _schedule]` (comment notes the order). Nothing else changed.
- **View `Mes/Test/Schedule`** (`view.json`, `resource.json` new): title label; a row with buttons **Replan** (`self.view.custom.result = mes.schedule.replan()`) and **Schedule released orders** (`… = mes.schedule.schedule_released()`) and a result label bound to `view.custom.result`; table `Schedule` bound to `view.custom.schedule`, which polls `mes.schedule.schedule_rows()` with `now(2000)` + script transform (failure → `mes.ui.log_once('schedule_rows', …)`, read-only call into `mes.ui`). 14 columns: Seq, Work order, Product, Status, Planned start, Planned end, Planned s, Actual start, Actual end, Actual s, Gap s, Plates, Ink, Proof.
- **`page-config/config.json`**: `/mes/schedule` → `Mes/Test/Schedule`, title "MES test — Schedule" (added only).
- **`ignition/tests/replay_schedule.py`** (new): FakeDb with the DB-002 seed (orders with due dates and priority, product master, schedule 1–5) serving both the workorder and schedule db interfaces. (1) Backfill, as live: loop 1 + first loop-2 sample through engine → workorder, then the schedule handler loads; its first tick syncs and replans; loop 2 runs through all three. (2) `do_schedule_released` on the seed, then two loops with all handlers from the first sample. (3) Edge cases: open run, no run, idempotence, gap 301 s / 300 s, Cancelled order and Cancelled entry, replan anchors, `append_order` order, `shape_schedule`.

### Files changed

| Path | Change |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/schedule/code.py` | Created |
| `ignition/projects/mes-2026/ignition/script-python/mes/schedule/resource.json` | Created |
| `ignition/projects/mes-2026/ignition/script-python/mes/engine/code.py` | Modified (`_schedule` + `HANDLERS` only) |
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/views/Mes/Test/Schedule/view.json` | Created |
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/views/Mes/Test/Schedule/resource.json` | Created |
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/page-config/config.json` | Modified (`/mes/schedule` added) |
| `ignition/tests/replay_schedule.py` | Created |
| `docs/tasks/task_SCHED-001_plan_vs_actual.md` | Status line, Dev Report |
| `docs/agent_handoff.md` | Dev status block |

Not touched: `mes/workorder`, `mes/ui`, the WorkOrders view, `press1`, VIS-001 views, tag files, `db/`, `simreplay.py`, `replay_workorder.py` (the working tree shows WO-003's in-progress edits to several of these — not mine).

### Gate results

```
$ for t in replay_engine replay_workorder test_ui_work_orders replay_schedule; do python3 -I ignition/tests/$t.py …; done
replay_engine exit=0 PASSED: 0 failure(s)
replay_workorder exit=0 PASSED: 0 failure(s)
test_ui_work_orders exit=0 0 failure(s)
replay_schedule exit=0 PASSED: 0 failure(s)
$ docker compose config -q && echo "compose config OK"
compose config OK
```
(The tests ran against the working tree, which includes WO-003's uncommitted edits to `mes/workorder`, `simreplay.py` and `replay_workorder.py`.)

`python3 -I ignition/tests/replay_schedule.py`:
```
Sim: fourzero_printing_press1, two loops, 1046 samples
  PASS  before load: runs 1-5 closed, schedule untouched

First tick after load:
  first tick: sync ok: synced, 5 changed (WO-1001 Done, WO-1002 Done, WO-1003 Done, WO-1004 Done, WO-1005 Done)
  first tick: planned_start missing, replan ok: replanned 5 entries from anchor 2026-10-14T17:46:58Z, 5 changed

Schedule after loop 2:
  1 WO-1001 Done planned 164.000 s actual 180.0 s
  2 WO-1002 Done planned 155.000 s actual 224.0 s
  3 WO-1003 Done planned 159.000 s actual 202.0 s
  4 WO-1004 Done planned 163.182 s actual 214.0 s
  5 WO-1005 Done planned 166.875 s actual 208.0 s

Backfill:
  PASS  first tick: sync (5 changed) then replan (5 entries)
  PASS  entries 1-5 Done
  PASS  planned totals [164.0, 155.0, 159.0, 163.182, 166.875] ~ 164, 155, 159, 163, 167 s
  PASS  actual totals [180.0, 224.0, 202.0, 214.0, 208.0] = 180 (198 less its unobserved 18 s Setup), 224, 202, 214, 208 s
  PASS  entry 1 actual start = run 1 start (no previous run)
  PASS  entries 2-5 actual start = previous run's end (the product change)
  PASS  actual end = the run's end
  PASS  planned chain anchored at entry 1's actual start
  PASS  planned_start(k+1) = planned_end(k)
  PASS  loop plan 808.057 s ~ 808 s
  PASS  loop actual 1028 s = 1,046 s less entry 1's unobserved Setup (18 s)
  PASS  loop 2 (run_start / product_change): planned times unchanged, no replan (5 plan writes)
  PASS  loop 2: one sync per run_start / product_change sample (9)
  PASS  loop 2: no replan line

Schedule released orders:
  ok: scheduled 4 released orders (6 WO-1009, 7 WO-1006, 8 WO-1007, 9 WO-1008); replanned 9 entries from anchor 2026-10-14T17:46:40Z, 9 changed
  PASS  appended 6 WO-1009 (Rush), 7 WO-1006, 8 WO-1007, 9 WO-1008 (due date)
  PASS  WO-1010 (Planned) not added
  PASS  changeover from product master
  PASS  then replan: 9 entries chained from now
  PASS  result string
  PASS  second press adds nothing
  PASS  planned_start set: first tick syncs, no replan
  PASS  planned times unchanged over two loops
  PASS  WO-1001 .. WO-1009 entries Done
  PASS  loop 2 (Setup observed): WO-1006 .. WO-1009 actual [198.0, 224.0, 202.0, 214.0] = 198, 224, 202, 214 s

Edge cases:
  ok: synced, 3 changed (WO-1001 Done, WO-1002 Done, WO-1003 Running)
  PASS  open run: Running, actual start = previous end, end NULL
  PASS  no run: entry stays Queued
  PASS  idempotent: second sync writes nothing
  PASS  gap 301 s: actual start = run start
  PASS  gap 300 s: actual start = previous run's end
  PASS  Cancelled order / Cancelled entry: row untouched by sync and replan
  PASS  replan skips them: WO-1004 planned right after WO-1001
  PASS  replan with no actual start: anchored at now
  PASS  replan(anchor_ms): explicit anchor used
  PASS  append_order: Rush, then due date, then work_order_id; sequence from max + 1
  PASS  shape_schedule: 14 columns in order
  PASS  Done: planned 164, actual 198, gap +34
  PASS  Running: actual and gap blank
  PASS  times HH:mm:ss, readiness flags

PASSED: 0 failure(s)
```

Jython 2.7 parse (WO-002 method: gateway JRE + `lib/core/common/jython-ia-2.7.4.0.jar`, `-S`, throwaway `/tmp/sched001_check.py` in the container, deleted; compile only, nothing loaded). Both files are ASCII.
```
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/script-python/mes/schedule/code.py
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/script-python/mes/engine/code.py
rc=0
```

No restart: `mes-workshop-2026-ignition-1` StartedAt `2026-10-22T17:00:29Z`, `mes-workshop-2026-db-1` `2026-10-22T15:43:40Z` (unchanged). No DB writes, no schema change, no tag writes, no commit, nothing staged.

### Live check

Walker's Scan File System at 12:47:39 CDT. No container restart (`mes-workshop-2026-ignition-1` StartedAt still `2026-10-22T17:00:29Z`). `docker logs --since 15m mes-workshop-2026-ignition-1 | grep -E "schedule|Restarting gateway scripts"`:
```
12:47:39 | I [Project   ] Restarting gateway scripts... project=mes-2026, collection=mes-2026
12:47:40 | I [Project   ] Restarting gateway scripts... project=mes-2026, collection=mes-2026
12:47:42 | I [m.schedule] schedule store created mes.schedule.v1
12:47:42 | I [m.schedule] first tick: sync ok: synced, 5 changed (WO-1001 Done, WO-1002 Done, WO-1003 Done, WO-1004 Done, WO-1005 Done)
12:47:42 | I [m.schedule] first tick: planned_start missing, replan ok: replanned 5 entries from anchor 2026-10-22T17:15:35Z, 5 changed
```
No ERROR / `handler … failed` lines. `m.workorder` kept logging normally across the scan (run 9 / WO-1009 complete 12:47:29, unattributed 105 run 12:47:58).

The §4 query (`docker exec mes-workshop-2026-db-1 psql -U mes -d mes`):
```
 sequence | work_order_id | status |       planned_start        |        planned_end         |      actual_start_ts       |       actual_end_ts        | planned_s  |  actual_s
----------+---------------+--------+----------------------------+----------------------------+----------------------------+----------------------------+------------+------------
        1 | WO-1001       | Done   | 2026-10-22 17:15:35.877+00 | 2026-10-22 17:18:19.877+00 | 2026-10-22 17:15:35.877+00 | 2026-10-22 17:18:40.865+00 | 164.000000 | 184.988000
        2 | WO-1002       | Done   | 2026-10-22 17:18:19.877+00 | 2026-10-22 17:20:54.877+00 | 2026-10-22 17:18:40.865+00 | 2026-10-22 17:22:30.749+00 | 155.000000 | 229.884000
        3 | WO-1003       | Done   | 2026-10-22 17:20:54.877+00 | 2026-10-22 17:23:33.877+00 | 2026-10-22 17:22:30.749+00 | 2026-10-22 17:25:58.504+00 | 159.000000 | 207.755000
        4 | WO-1004       | Done   | 2026-10-22 17:23:33.877+00 | 2026-10-22 17:26:17.059+00 | 2026-10-22 17:25:58.504+00 | 2026-10-22 17:29:38.464+00 | 163.182000 | 219.960000
        5 | WO-1005       | Done   | 2026-10-22 17:26:17.059+00 | 2026-10-22 17:29:03.934+00 | 2026-10-22 17:29:38.464+00 | 2026-10-22 17:33:11.663+00 | 166.875000 | 213.199000
(5 rows)
```
Against `mes.work_order_run` (runs 1–5 start 17:15:35.877 / 17:19:26.388 / 17:22:53.444 / 17:26:33.128 / 17:30:07.499, end 17:18:40.865 / 17:22:30.749 / 17:25:58.504 / 17:29:38.464 / 17:33:11.663):
- Entry 1 actual start = run 1 start (no previous run); entries 2–5 actual start = previous run's end (Setups 45.5 s, 22.7 s, 34.6 s, 28.8 s, all < 300 s); actual end = own run end. Planned chain anchored at entry 1's actual start and contiguous.
- Planned 164 / 155 / 159 / 163.182 / 166.875 s — exact. Actual 185.0 / 229.9 / 207.8 / 220.0 / 213.2 s vs nominal 180 / 224 / 202 / 214 / 208 × ~1.03 = 185.4 / 230.7 / 208.1 / 220.4 / 214.2 — within the live ~3 % slow sim. Loop: plan 808.057 s, actual 17:15:35.877 → 17:33:11.663 = 1,055.8 s (1,028 nominal × 1.027).
- Entries 6+ not present (WO-1006…1009 ran unscheduled); nothing appended. Buttons not pressed.

**Page:** not opened by the Dev (no gateway login from this session). Walker to open http://localhost:8089/data/perspective/client/mes-2026/mes/schedule and confirm the five rows match the query (times in CDT: planned 12:15:35 → 12:29:03, actual 12:15:35 → 12:33:11; Gap s +21 / +75 / +49 / +57 / +46).

### Deviations from spec

1. **Entry 1 actual is 180 s, not 198 s, in the replay (and loop actual 1,028 s, not 1,046 s).** Rule 2 and §3 both say entry 1's actual start = its run start (no previous run), and the run starts after J101's 18 s Setup, so its actual total is 198 − 18 = 180 s. The test asserts the rule (180, and loop actual + 18 = 1,046). Scenario 2 shows the §4 values when the Setup *is* observed: the loop-2 jobs WO-1006…1009 come out exactly 198 / 224 / 202 / 214 s. See open question 1.
2. **The backfill replay loads `mes.schedule` after loop 1** (engine + workorder only for loop 1, schedule from the second loop-2 sample), mirroring the live first deploy. With the handler running from the first sample, the first-tick replan finds no actual start yet and anchors at "now" (the first sample), which can't satisfy "planned chain anchored at entry 1's actual start". Scenario 2 covers the from-the-start case.
3. **"Cancelled" covers the order's status too.** Rule 2 says Cancelled entries are never touched; §3 tests "entry whose order is Cancelled → untouched". Both `plan` and `actuals` skip an entry whose own status or whose work order's status is `Cancelled` (so replan also leaves it out of the chain). The SQL guard on `set_actual` also refuses `status = 'Cancelled'` rows.
4. **`replan(anchor_ms)`**: an explicit `anchor_ms` replaces the rule-1 anchor; `None` applies rule 1. In the handler, rule 1's "now" is the sample time (keeps `handle()` deterministic offline; live it is within ~2 s of wall time); the page button uses wall time.
5. **Several runs for one order** (possible after WO-003 reassign/re-release): actual start from the order's first run (and the run before it), actual end and Running/Done from its last run. The spec assumes one run per entry.
6. Readiness flags display as `yes` / `no`. Product column shows the product code only (the view has no family).

### Open questions for the PM

1. **Entry 1's expected 198 s / loop 1,046 s vs rule 2.** With the rule as written the first entry of a schedule never includes its own Setup (no previous run), so live entry 1 (WO-1001, run 1) will show ≈ 180 s × 1.03 and a gap of ~+16 s rather than +34 s. Is that intended (spec §4 step 2 hints yes: "entry 1 may be shorter if its Setup was not observed"), or should entry 1 use another Setup start (e.g. the engine's Setup segment start)? I implemented the rule.
2. **Scan File System will load WO-003's in-progress edits too.** The shared tree holds uncommitted changes to `mes/workorder/code.py`, `mes/ui/code.py` and the WorkOrders view from the WO-003 Dev session. A scan loads the whole project, so Walker's scan for SCHED-001 also deploys whatever state WO-003 is in at that moment. Coordinate the scan with the WO-003 Dev (or scan after WO-003 reports ready).
3. Rule 1 says replan on the first tick after load. The store survives Scan File System (`getGlobals()`), so a later code-only scan will not re-run the first-tick sync/replan; only a gateway restart or the Replan button does. Fine for this spec; noting it for SCHED-003.

## PM Verification

**PM, 2026-10-22 — PASS.**

- [x] `replay_schedule.py` (PM, `-I`): 40 PASS, 0 failures; all other tests pass.
- [x] Engine change = `_schedule` + `HANDLERS = [_workorder, _schedule]` only. `mes.schedule` writes only `schedule_entry` (UPDATE planned / status+actuals, INSERT for append), parameterised; pure `plan`/`actuals`/`append_order`/`shape_schedule`. No `mes/ui`, `mes/workorder` or WorkOrders-view edits by this task; page-config adds `/mes/schedule` only.
- [x] Live (DB, PM): entries 1–5 Done; planned 164.0 / 155.0 / 159.0 / 163.2 / 166.9 s chained from 12:15:35; actual 185.0 / 229.9 / 207.8 / 220.0 / 213.2 s (≈ nominal × 1.03, live drift). Log: first-tick sync (5 changed) and replan; no errors.
- Buttons were pressed from the page after load (log, `component=root/Actions/…`): both no-ops (0 released orders, 0 changed) — harmless.
- **Dev's open question:** entry 1 actual (185 s) excludes its 18 s Setup because the previous run (an unattributed 105) has no database row. Accepted as the rule is written; if wanted later, take the Setup start from the engine's `product_change` instead of the previous run row (backlog).

User-facing impact: test page `/mes/schedule`; `Schedule/*` query tags reflect statuses.
