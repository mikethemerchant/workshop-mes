# OEE-001 — OEE interval writer: one row per minute per product

**Status:** ✅ Complete — 2026-10-22, verified by the PM (spec approved by Walker)
**Priority:** P0 · **Effort:** M (one Dev session; no restart)
**Depends on:** DT-001 **verified by the PM** (both register a handler in `mes/engine/code.py`). WO-001 ✅. Can run in parallel with DT-002 and SCHED-003 (no shared files).
**Bug:** —

---

## Background

MES §6 time basis (Walker, Oct 20): write **one `oee_interval` row per minute per product code**, then aggregate for any window (live 15 min, per work order, per hour, since start). Each row holds the inputs, not the ratios: `running_s`, `planned_production_s`, `good_m`, `reject_m`, `ideal_m`. OEE-002 computes and shows the ratios. Definitions (§6, Walker):
- Running time = time in state 1.
- Planned production time = window time − planned downtime (Setup up to the scheduled changeover; operator-logged planned stops). With this sim: Running + alarm time + changeover overrun.
- Performance basis = good meters only; ideal = running minutes × the product's ideal speed (m/min, product master).
- Quality = good / (good + reject).

## Rules

1. **Time accounting (sample-and-hold).** For each accepted sample at `t` with previous sample at `t_prev` (same engine store; skip if no previous sample, or if the engine emitted `gap` on this sample): the interval `[t_prev, t)` belongs to the **previous sample's** `state` and `product`.
   - state 1 → `running_s += dt`, `planned_production_s += dt`.
   - state 3 → `planned_production_s += dt`.
   - state 2 → split by how long the current Setup has lasted: track `setup_start` = `t` of the first sample with state 2 after a non-2 sample (raw samples, not debounced); the part of the interval with `elapsed ≤ c` (`c` = `scheduled_changeover_s` of that product) is planned (excluded from everything); the part beyond `c` adds to `planned_production_s` (overrun).
   - Operator planned stops (DT-003, not built): leave a hook `planned_stop_overlap(start, end) → seconds` returning 0 for now; subtract it from `planned_production_s` when DT-003 lands.
2. **Meters.** The sample's engine deltas `dg`, `dr` belong to the **previous sample's** product (production happened during `[t_prev, t)`; this also credits a late meter step at a product change to the old product — the BUG-002 lesson). Add to `good_m`, `reject_m`.
3. **Ideal.** `ideal_m += running part of dt / 60 × ideal_speed_mpm(product)` (product master, cached per product).
4. **Minute buckets.** Key `(interval_start = floor(minute UTC), product_code)`. Split an interval that crosses a minute boundary proportionally (time and ideal by time; meters go to the minute containing `t`). `work_order_id` = the WO of `mes.workorder.current()` when the bucket last received running time (None if none / unattributed).
5. **Write.** After each tick, upsert every bucket that changed: `INSERT INTO mes.oee_interval (…) VALUES (…) ON CONFLICT (interval_start, product_code) DO UPDATE SET running_s = EXCLUDED.running_s, …` with **absolute** values from memory (a failed write heals next tick). Keep only buckets of the last 3 minutes in memory.
6. **Restart / reload.** When a bucket is first created in memory, load the existing row for that key from the database (if any) and continue from its values, so a reload never lowers a minute. Log `oee store created`.
7. Values: `running_s`, `planned_production_s` numeric(6,1) → round to 0.1 on write; `ideal_m` 0.1; meters integers. Never delete rows.

## What Already Exists (Do Not Rewrite)

- `mes.engine`: sample with `t`, `state`, `product`, `dg`, `dr`; events incl. `gap`. `HANDLERS = [_workorder, _schedule, _downtime]` after DT-001. **Only change:** append `_oee`.
- `mes.workorder.current()` — read only.
- Schema: `oee_interval` (PK `interval_start, product_code`; checks `running_s ≥ 0`, `planned_production_s ≥ 0`); view `v_oee_by_product` (A, P capped at 100, Q, OEE per product). No schema change.
- Do not edit `mes/downtime`, `mes/schedule`, `mes/workorder`, `mes/ui` or any view (other Dev sessions may be in them).

## What to Build

1. Library `ignition/projects/mes-2026/ignition/script-python/mes/oee/code.py` + `resource.json` (same conventions: pure accumulation `accumulate(st, prev, sample, events, changeover, ideal_speed, wo_now)` → changed buckets; `_SqlDb` with `upsert`, `load`, `product_master`; Jython/Python 3). Store `mes.oee.v1`.
2. Register: `def _oee(sample, events): mes.oee.on_tick(sample, events)`; `HANDLERS = [_workorder, _schedule, _downtime, _oee]`.
3. Test `ignition/tests/replay_oee.py` (`python3 -I`): two-loop nominal replay through engine → workorder → oee with a FakeDb (upsert semantics), then aggregate the **second loop's** rows per product with the `v_oee_by_product` formulas and assert (± 0.1 point) MES §6:

| Product | A | P | Q | OEE |
|---|---|---|---|---|
| 101 | 83.3 | 96.0 | 97.1 | 77.7 |
| 102 | 77.3 | 83.3 | 95.4 | 61.5 |
| 103 | 83.3 | 90.0 | 96.2 | 72.1 |
| 104 | 77.3 | 95.5 | 96.7 | 71.4 |
| 105 | 81.1 | 93.8 | 81.5 | 62.0 |
| Loop | 80.4 | 92.1 | 93.7 | 69.4 |

   Also: per loop `running_s` 746 s, `planned_production_s` 928 s (746 + 154 alarm + 28 overrun), good 2,315, reject 155; minute-boundary split conserves totals exactly; a reload mid-minute (empty store, row in FakeDb) continues and never lowers a value; a `gap` adds no time; a sim restart adds no negative meters. All earlier tests pass.

## Load and live check

1. **Stop** after tests: Dev Report; handoff: "Ready to load — waiting for Walker (Scan File System)."
2. After the scan, wait **~3 minutes** (three closed minutes). Paste the `m.oee` lines and
   `select interval_start, product_code, work_order_id, running_s, planned_production_s, good_m, reject_m, ideal_m from mes.oee_interval order by interval_start, product_code;`
   Check: each full minute has `running_s + planned-excluded + fault time ≈ 60 s`, `ideal_m ≈ running_s/60 × ideal speed`, meters plausible for the job step. **Do not wait for a full loop.**

## Acceptance Criteria

- [ ] Rules 1–7 exactly; absolute upserts; reload-safe; handler appended after `_downtime`.
- [ ] `replay_oee.py` reproduces the §6 table (± 0.1) and the loop totals; edge cases pass; all earlier tests pass under `python3 -I`.
- [ ] Live: three minutes of plausible rows; no errors; no restart.
- [ ] No edits outside `mes/oee`, the engine registration and the test.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/oee/code.py`, `resource.json` | New |
| `ignition/projects/mes-2026/ignition/script-python/mes/engine/code.py` | Append `_oee` |
| `ignition/tests/replay_oee.py` | New |
| `docs/tasks/task_OEE-001_oee_intervals.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

## Dev Instructions

Spec: `docs/tasks/task_OEE-001_oee_intervals.md`. Build `mes.oee` as the fourth engine handler: account each 2 s interval to the previous sample's state and product (running, alarm, Setup split at the scheduled changeover), add meter deltas and ideal meters, and upsert one `oee_interval` row per minute per product with absolute, reload-safe values. Prove it reproduces the MES §6 OEE table offline, **stop for Walker's Scan File System**, then check three live minutes.

Hard constraints:
- **Start only after DT-001 is verified by the PM** (engine registration). Edit only `mes/oee`, the engine `HANDLERS`, and the test.
- No container restarts. No schema change. Parameterised SQL; upsert into `oee_interval` only; never delete. No tag writes (OEE-002). Publish nothing.
- Never stop, start, update or reimport the sim; never touch UNS Studio. Never read `[MQTT Engine]`, `4.0 Printing/…`, `Probe/…`.
- Never touch `ign-mes-demo`. Never print `.env` or any password. No commit, no push; stage nothing.

## Dev Report

**Status:** ✅ Done — ready for PM verification. Offline gates pass; loaded by Walker's Scan File System (14:05:07 CDT); three closed live minutes check out (see *Live check*).

### What was built

- **`mes/oee/code.py` (new)** — the `mes.oee` library, store `mes.oee.v1` (`oee store created mes.oee.v1` logged on creation).
  - `accumulate(st, prev, sample, events, changeover, ideal_speed, wo_now)` — pure, rules 1–4. `changeover(p)` / `ideal_speed(p)` read the cached product master. Sample-and-hold: `[t_prev, t)` goes to the previous sample's state and product. State 1 → running + planned production + ideal (`ms / 60000 × ideal_speed_mpm`) and the bucket's `work_order_id = wo_now`. State 3 → planned production. State 2 → everything after `setup_start + scheduled_changeover_s` is planned production (overrun); before that nothing. `setup_start` = `t` of the first state-2 sample after a non-2 sample (raw samples). No time on a `gap` sample or with no previous sample. Meter deltas `dg` / `dr` go to the previous sample's product, in the minute containing `t`. Intervals are split at minute boundaries in integer milliseconds, so splits conserve totals exactly. Seconds are rounded to 0.1 only in `row_values()`.
  - `planned_stop_overlap(start, end)` — DT-003 hook, returns 0. It is called per minute piece and subtracted from planned production.
  - `handle(st, sample, events, db, wo_now)` — pure except for db calls. It loads the product master once per store, accumulates, and stores `prev`. When a minute closes it logs one `minute … product=… wo=… running=… planned_production=… good=… reject=… ideal=…` line per bucket of that minute. Then for every dirty bucket: if the bucket is new in memory, load its existing row and add it (rule 6, logged `continued …`), then upsert absolute values (rule 5). A failed load or upsert leaves the bucket dirty for the next tick (logged once per failure streak: `write failed (retried next tick) …`). Clean buckets older than the current minute and the two before it are dropped.
  - `_SqlDb`: `product_master()`, `load(interval_start, product)`, `upsert(row)` (`INSERT … ON CONFLICT (interval_start, product_code) DO UPDATE SET … = EXCLUDED.…`). All SQL is parameterised. Never deletes. `_num()` reads `BigDecimal` columns.
  - `on_tick(sample, events)` — the engine handler; `wo_now` = `mes.workorder.current()['work_order_id']` or None. Read API: `buckets()`.
- **`mes/oee/resource.json` (new)** — same as the other `mes/*` libraries.
- **`mes/engine/code.py`** — `_oee` added; `HANDLERS = [_workorder, _schedule, _downtime, _oee]`. No other change.
- **`ignition/tests/replay_oee.py` (new)** — two loops + 2 loop-3 samples through engine → workorder → oee with the WO-001 `FakeDb` and an `OeeDb` (upsert on the PK, the table's checks, plus a record of any upsert that lowered a value). Loop 2 is started on a minute boundary, so its rows are those with `interval_start ≥` loop-2 start. Those rows are aggregated with the `v_oee_by_product` formulas. Edge cases: off-grid replay (split conservation), exact millisecond split, reload mid-minute, gap, sim restart, failed upsert, failed load.

### Files changed

| Path | |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/oee/code.py` | created |
| `ignition/projects/mes-2026/ignition/script-python/mes/oee/resource.json` | created |
| `ignition/projects/mes-2026/ignition/script-python/mes/engine/code.py` | modified (`_oee`, `HANDLERS`) |
| `ignition/tests/replay_oee.py` | created |
| `docs/tasks/task_OEE-001_oee_intervals.md` | Status line + Dev Report |
| `docs/agent_handoff.md` | Dev status block |

Not touched: `mes/downtime`, `replay_downtime.py` and `task_DT-002` (the parallel DT-002 session has uncommitted edits there), `mes/schedule`, `mes/workorder`, `mes/ui`, views, schema.

### Gate results

`python3 -I ignition/tests/replay_oee.py` → exit 0:
```
Sim: two loops + 2 samples, loop 2 starts 1792001100000 (a minute)

Loop 2, v_oee_by_product formulas:
  101  A  83.3 P  96.0 Q  97.1 OEE  77.7   running  150.0 planned  180.0 good  600 reject  18 ideal   625.0
  102  A  77.3 P  83.3 Q  95.4 OEE  61.5   running  150.0 planned  194.0 good  375 reject  18 ideal   450.0
  103  A  83.3 P  90.0 Q  96.2 OEE  72.1   running  150.0 planned  180.0 good  450 reject  18 ideal   500.0
  104  A  77.3 P  95.5 Q  96.7 OEE  71.4   running  150.0 planned  194.0 good  525 reject  18 ideal   549.9
  105  A  81.1 P  93.8 Q  81.5 OEE  62.0   running  146.0 planned  180.0 good  365 reject  83 ideal   389.3
  Loop A  80.4 P  92.1 Q  93.7 OEE  69.4   running  746.0 planned  928.0 good 2315 reject 155 ideal  2514.2

MES section 6 table:
  PASS  101 A/P/Q/OEE (83.3, 96.0, 97.1, 77.7) = (83.3, 96.0, 97.1, 77.7) (+- 0.1)
  PASS  102 A/P/Q/OEE (77.3, 83.3, 95.4, 61.5) = (77.3, 83.3, 95.4, 61.5) (+- 0.1)
  PASS  103 A/P/Q/OEE (83.3, 90.0, 96.2, 72.1) = (83.3, 90.0, 96.2, 72.1) (+- 0.1)
  PASS  104 A/P/Q/OEE (77.3, 95.5, 96.7, 71.4) = (77.3, 95.5, 96.7, 71.4) (+- 0.1)
  PASS  105 A/P/Q/OEE (81.1, 93.8, 81.5, 62.0) = (81.1, 93.8, 81.5, 62.0) (+- 0.1)
  PASS  Loop A/P/Q/OEE (80.4, 92.1, 93.7, 69.4) = (80.4, 92.1, 93.7, 69.4) (+- 0.1)

Loop totals:
  PASS  running_s 746 s (746)
  PASS  planned_production_s 928 s = 746 + 154 alarm + 28 overrun (928)
  PASS  good 2315, reject 155 (2315, 155)
  PASS  planned - running per product = alarm + overrun: 102 and 104 carry 14 s overrun ({101: 30.0, 102: 44.0, 103: 30.0, 104: 44.0, 105: 34.0})
  PASS  ideal_m = running_s / 60 x ideal speed per product
  PASS  each row: running <= planned <= 60 s
  PASS  loop 2: rows in 18 minutes (18)
  PASS  per minute, all products: planned production <= 60 s
  PASS  loop 2: work_order_id of every row with running time = the run's order (105 None)
  PASS  loop 1: job 105 rows on WO-1005
  PASS  no upsert lowered a value (0)
  PASS  memory: at most 3 minutes of buckets (max 4)
  PASS  one 'minute ...' log line per closed bucket

Minute splits:
  PASS  two loops off the grid: rows sum to 2 x 746 / 928 s, 2 x 2315 / 155 m ({'running_s': 1492.0, 'planned_production_s': 1856.0, 'good_m': 4630, 'reject_m': 310, 'ideal_m': 5028.5})
  PASS  off the grid: some rows hold a split, not whole 2 s intervals
  PASS  2 s across a boundary: 1234 ms + 766 ms, exact (1234, 766)
  PASS  ideal split by time, sum = 2 s of ideal
  PASS  meters go to the minute containing t
  PASS  both buckets changed, with the run's order

Reload:
  PASS  the bucket open at the reload is continued from the table
  PASS  reload: no upsert lowered a value (0)
  PASS  reload: that minute loses only the first sample after the reload (2 s, 6 m) (2 s, 6 m)
  PASS  reload: every other row equal to the uninterrupted replay

Gap:
  PASS  engine emitted gap
  PASS  a gap adds no time: 4 + 4 intervals of 2 s, not the 30 s across the gap (16)

Sim restart:
  PASS  engine emitted restart
  PASS  no negative meters
  PASS  good = 5 x 8 before + 4 x 8 after the restart (72)

Failures:
  PASS  failed upsert: logged
  PASS  next tick heals it with absolute values (10 s, 48 m)
  PASS  failed load: nothing written until the load succeeds, then continued ({'interval_start': 1791999960000, 'product_code': 101, 'work_order_id': 'WO-1001', 'running_s': 22.0, 'planned_production_s': 32.0, 'good_m': 96, 'reject_m': 2, 'ideal_m': 91.6})
  PASS  failed load: planned 32 s, order WO-1001

All checks passed.
```

Earlier tests, `python3 -I` (counted with `grep -c '  PASS'`):
```
replay_engine 23 PASS / PASSED: 0 failure(s) / exit 0
replay_workorder 48 PASS / PASSED: 0 failure(s) / exit 0
replay_schedule 68 PASS / PASSED: 0 failure(s) / exit 0
test_ui_work_orders / 0 failure(s) / exit 0
test_workorder_actions 43 PASS / PASSED: 0 failure(s) / exit 0
test_schedule_override 55 PASS / PASSED: 0 failure(s) / exit 0
replay_downtime 53 PASS / All checks passed. / exit 0   (includes the parallel DT-002 session's in-progress additions)
replay_oee 38 PASS / All checks passed. / exit 0
```
(`test_ui_work_orders` prints its checks in a different format; its last line is `0 failure(s)`, exit 0.)

`docker compose config -q` → exit 0, no output.

Jython 2.7 parse (gateway JRE + `lib/core/common/jython-ia-2.7.4.0.jar`, `-S`, script piped on stdin, compile only):
```
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/script-python/mes/oee/code.py
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/script-python/mes/engine/code.py
rc=0
```
Both files are ASCII. SQL checked against the live schema with `EXPLAIN` only, which writes nothing. The upsert plans with `Conflict Resolution: UPDATE`, `Conflict Arbiter Indexes: oee_interval_pkey`, and the load is an index scan on the PK. `product_master` reads 250.0/20, 180.0/30, 200.0/24, 220.0/20, 160.0/30. `select count(*) from mes.oee_interval` → 0.

No restart: `mes-workshop-2026-ignition-1` StartedAt `2026-10-22T17:00:29Z`, `mes-workshop-2026-db-1` `2026-10-22T15:43:40Z`. No DB writes, no tag writes, nothing staged, no commit.

### Deviations from spec

None in the rules. Interpretations where the spec is silent are listed below for the PM:
1. **Meters on a `gap` sample are counted.** Rule 1's "skip" covers time only, and rule 2 has no gap exception. The production across a gap is real and `mes.workorder` counts it too. With **no previous sample** (first sample after a store is created) nothing is counted, because there is no previous product to credit.
2. **Unknown product code** (not in `product_master`): its time and meters are not counted, because the FK would reject the row. This is logged once per code. The product master is cached once per store, so an edit to it takes effect at the next Scan File System.
3. **Failed load:** a new bucket whose load fails is not written until a load succeeds. Writing it first could lower an existing row.
4. **Dirty buckets** older than 3 minutes stay in memory until their write succeeds. Clean ones are dropped. The replay peaked at 4 buckets: the current minute and the two before it, with two products in one minute.
5. **Log lines** (logger `mes.oee`): `oee store created`, one `minute …` line per bucket when its minute closes, `continued … from the database`, `write failed …`, `product … not in product_master`.
6. **Test layout:** loop 2 is started on a minute boundary so its rows separate cleanly. The off-grid replay (`T0 + 1234 ms`) shows the minute splits conserve the two-loop totals.

### Open questions for the PM

1. **Reload mid-Setup:** after a Scan File System during a Setup, `setup_start` becomes the first sample the new store sees, so up to `scheduled_changeover_s` more of that Setup counts as planned. The engine's open segment has the true backdated start (`mes.engine.open_segment()`), but rule 1 says raw samples. Use the engine's start in that case?
2. **Gap meters** (deviation 1): count them (current behaviour), or drop them like the time?

Answers relayed by the lead (2026-10-22):
1. Backlog. It only matters after a full gateway restart (or the store's first creation), not an ordinary scan, because the `getGlobals()` store survives a Scan File System. My wording "after a Scan File System" above was too broad.
2. Accepted: gap meters are counted. They are real production, as in `mes.workorder` and the engine totals. No code change.

### Live check

Walker ran Scan File System at 14:05:07 CDT (19:05:07Z). Checked at 19:09:26Z. No restart: `mes-workshop-2026-ignition-1` StartedAt `2026-10-22T17:00:29Z`, `mes-workshop-2026-db-1` `2026-10-22T15:43:40Z`.

`docker logs --since 15m mes-workshop-2026-ignition-1 | grep -E "m\.oee|handler .* failed|Restarting gateway scripts"`:
```
2026/10/22 14:05:07 | I [Project ] Restarting gateway scripts... project=mes-2026, collection=mes-2026
2026/10/22 14:05:07 | I [Project ] Restarting gateway scripts... project=mes-2026, collection=mes-2026
2026/10/22 14:05:09 | I [m.oee ] oee store created mes.oee.v1
2026/10/22 14:06:03 | I [m.oee ] minute 2026-10-22T19:05Z product=101 wo=None running=44.0 planned_production=52.2 good=168 reject=8 ideal=183.2
2026/10/22 14:07:01 | I [m.oee ] minute 2026-10-22T19:06Z product=101 wo=None running=7.9 planned_production=20.3 good=32 reject=0 ideal=33.1
2026/10/22 14:07:01 | I [m.oee ] minute 2026-10-22T19:06Z product=102 wo=None running=0.0 planned_production=9.7 good=0 reject=0 ideal=0.0
2026/10/22 14:08:01 | I [m.oee ] minute 2026-10-22T19:07Z product=102 wo=None running=51.8 planned_production=60.0 good=125 reject=0 ideal=155.3
2026/10/22 14:09:02 | I [m.oee ] minute 2026-10-22T19:08Z product=102 wo=None running=49.4 planned_production=60.0 good=120 reject=10 ideal=148.3
```
No `handler … failed`, no `write failed`, no errors.

`select interval_start, product_code, work_order_id, running_s, planned_production_s, good_m, reject_m, ideal_m from mes.oee_interval order by interval_start, product_code;`
```
     interval_start     | product_code | work_order_id | running_s | planned_production_s | good_m | reject_m | ideal_m
------------------------+--------------+---------------+-----------+----------------------+--------+----------+---------
 2026-10-22 19:05:00+00 |          101 |               |      44.0 |                 52.2 |    168 |        8 |   183.2
 2026-10-22 19:06:00+00 |          101 |               |       7.9 |                 20.3 |     32 |        0 |    33.1
 2026-10-22 19:06:00+00 |          102 |               |       0.0 |                  9.7 |      0 |        0 |     0.0
 2026-10-22 19:07:00+00 |          102 |               |      51.8 |                 60.0 |    125 |        0 |   155.3
 2026-10-22 19:08:00+00 |          102 |               |      49.4 |                 60.0 |    120 |       10 |   148.3
 2026-10-22 19:09:00+00 |          102 |               |      14.4 |                 24.5 |     35 |        0 |    43.3
(6 rows)
```
19:05 is a partial minute, because the store was created at 19:05:09. 19:09 is the open minute.

Checks, against the engine and downtime log lines for the same span:
- **19:06 (job 101 end, job 102 Setup):** 101 ran 7.9 s, then Tunnel over-temp 19:06:07 → 19:06:20 (DT event 8, 12.4 s), so 7.9 + 12.4 = 20.3 = planned production ✓. Setup 102 started 19:06:20.3. The first 30 s up to 19:06:50.3 (`scheduled_changeover_s` 30) is excluded. The overrun from 19:06:50.3 to 19:07:00 is 9.7 s, on 102 ✓. 20.3 + 30.0 + 9.7 = **60.0 s** ✓.
- **19:07 (job 102):** the overrun continues to the `run_start` at 19:07:06.3 (6.3 s), then 51.8 s running to the Web break at 19:07:58. That leaves ~1.9 s of fault time: 6.3 + 51.8 + 1.9 = **60.0** ✓. The overrun total is 9.7 + 6.3 = **16.0 s**, the same as DT-002's event 10 (`Changeover overrun` 16.0 s) ✓. Ideal 51.8 / 60 × 180 = 155.4 against 155.3 (rounding of the split pieces) ✓. Good 125 = 25 samples × 5 m ✓.
- **19:08 (job 102):** Web break to 19:08:08 (8.2 s), then 49.4 s running, then RTO inhibit from 19:08:57 (2.4 s). Running + fault time = **60.0** ✓. Ideal 49.4 × 3 = 148.2 against 148.3 ✓. Good 120 = 24 × 5 m ✓. Reject 10 = 5 web-break samples × 2 m ✓.
- **19:05 (job 101, partial):** good 168 = 21 samples × 8 m. Reject 8 = 4 web-break samples × 2 m (Web break 19:05:09 → 19:05:18). Ideal 44.0 / 60 × 250 = 183.3 against 183.2 ✓.
- **`work_order_id` NULL:** correct. Every live run in this span is unattributed (`m.workorder: unattributed run product=102`), so `mes.workorder.current()` gives no order.

Live check passed: three full closed minutes (19:06, 19:07, 19:08), plausible rows, no errors, no restart.

## PM Verification

**PM, 2026-10-22 — PASS.**

- [x] `replay_oee.py` (PM, `-I`): all checks passed — MES §6 table per product and loop (80.4 / 92.1 / 93.7 / 69.4) and loop totals; all earlier tests pass.
- [x] SQL: one `INSERT … ON CONFLICT (interval_start, product_code) DO UPDATE` with absolute values; no other writes. Engine change = `_oee` appended.
- [x] Live (DB, PM): 9 rows 19:05–19:11. Full minutes sum to 60 s (e.g. 19:06 = 101 running 7.9 + fault 12.4 + 102 Setup 30 s excluded + 9.7 s overrun); `ideal_m` = running_s/60 × ideal speed within 0.2 m; the 16 s overrun matches DT-002's event 10 (9.7 s in 19:06 + 6.3 s in 19:07). Ratios over the 6 minutes A 77.8 / P 84.6 / Q 96.2 — consistent with jobs 101/102 (§6: 102 is 77.3 / 83.3 / 95.4).
- Answers: changeover-start-after-restart → backlog (OEE-004 note); meters across a gap counted, time not — accepted.

User-facing impact: `mes.oee_interval` fills every minute; `v_oee_by_product` now has data.
