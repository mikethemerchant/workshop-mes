# WO-001 — Work orders from the PLC: run attribution, Running/Complete, meter tracking

**Status:** ✅ Complete — 2026-10-22, verified by the PM, approved by Walker ("assume we are good")
**Priority:** P0 · **Effort:** M (one Dev session; offline replay first; short live check after the seed)
**Depends on:** ENG-001 ✅, UNS-004 ✅, DB-001 ✅, INFRA-002 ✅. **Live check needs DB-002 (seed)**, which Walker triggers.
**Bug:** —

---

## Background

First Hour 2 function. The PLC reports a product code, never a work order. The MES links each press run to a work order, sets Running and Complete from the PLC, and tracks good and reject meters per run from the cumulative-meter deltas. Rules: `docs/mes_requirements.md` §3 (all confirmed by Walker, Oct 20). Afterwards the `Work Order/Active` query tags (UNS-004) show the running order, and `mes.work_order` / `mes.work_order_run` hold the record for WO-002's test table and the AI.

**Walker's rulings that apply:** ingest state lives in the gateway, records in PostgreSQL (2026-10-22). WO-1010 stays Planned, so the second-loop job 105 run is **unattributed** — never invent or borrow an order (2026-10-22). DB-002 PM note: all seed `released_at` are equal, so **ties break on `work_order_id` ascending**.

## Rules (MES §3, made exact)

1. **Attribute at run start.** On the engine's `run_start` event (first Running sample after Setup), pick
   `SELECT work_order_id FROM mes.work_order WHERE product_code = ? AND status = 'Released' ORDER BY released_at, work_order_id LIMIT 1`.
   - Found → `UPDATE mes.work_order SET status = 'Running', started_at = coalesce(started_at, ?) WHERE work_order_id = ? AND status = 'Released'` and `INSERT INTO mes.work_order_run (work_order_id, product_code, start_ts, attributed_by, start_good_counter, start_reject_counter) VALUES (?, ?, ?, 'auto', ?, ?) RETURNING run_id`. Times are the sample time `t`.
   - None found → **unattributed run**: no database row (`work_order_run.work_order_id` is NOT NULL). Keep it in gateway memory and log `unattributed run product=<p>`. Its meters are still counted in memory for reporting.
2. **Count while attributed.** For every accepted sample from the `run_start` sample on (its own `dg`/`dr` included), add the engine's `dg` / `dr` to the run. Write the running totals with `UPDATE mes.work_order_run SET good_meters = ?, reject_meters = ? WHERE run_id = ?` whenever they changed (absolute values, so a failed write heals on the next tick). Faulted and Setup samples carry delta 0, so the totals hold.
3. **Sim restart.** An engine `restart` event while a run is open → `counter_reset_seen = true` on that run. The engine already gives delta 0; totals never go down.
4. **Complete on product change.** On `product_change` (fires on the first Setup sample of the next job), if a run is open: `UPDATE mes.work_order_run SET end_ts = ?, good_meters = ?, reject_meters = ? WHERE run_id = ?` and `UPDATE mes.work_order SET status = 'Complete', completed_at = ? WHERE work_order_id = ? AND status = 'Running'`. Log `complete <wo> good=<g> planned=<p> diff=<g-p> reject=<r>`. Never complete on quantity. An unattributed run just closes in memory.
5. **Event order within one tick:** `restart` → `product_change` (close and complete) → `run_start` (attribute) → add this sample's deltas to the (new) open run.
6. **Gateway restart (resume).** The engine store and this module's store start empty after any gateway restart or library reload that clears them. On the first tick with an empty store: read `SELECT r.run_id, r.work_order_id, r.product_code, r.good_meters, r.reject_meters FROM mes.work_order_run r WHERE r.end_ts IS NULL ORDER BY r.start_ts DESC`.
   - Newest open run's product equals the current sample's product → **resume** it: keep its stored totals and add deltas from here on (meters made while the gateway was down are lost; log `resumed run <id> after gateway restart`).
   - Otherwise (or for any older open run) → close it at the current sample time with its stored totals and complete its order (rule 4). Log it.
   - No open run and the press is Running → stay unattributed until the next `run_start` (we did not see this run begin). Log `press running at startup, no open run: waiting for next run_start`.
7. **Never** touch an order that is On Hold, Cancelled, Closed or Planned; never change `planned_meters`, `due_date`, `priority`; never delete rows.

## What Already Exists (Do Not Rewrite)

- `mes.engine` (`ignition/projects/mes-2026/ignition/script-python/mes/engine/code.py`): events `run_start`, `product_change`, `restart` with `t` in epoch ms; the sample passed to handlers carries `dg`, `dr`, `good`, `reject`, `product`. `HANDLERS = []`. **The only engine change allowed:** register this task's handler (below). `step()` and the event logic stay as they are; `replay_engine.py` must still pass unchanged.
- Schema `mes` (DB-001): `work_order`, `work_order_run` (incl. `start_good_counter`, `start_reject_counter`, `counter_reset_seen`), view `v_work_order_table`. No schema change.
- `Work Order/Active/*` query tags (UNS-004) read the view — this task writes rows, never those tags.
- `ignition/tests/replay_engine.py` and the sim-file sample builder in it.
- `press1`, `press1Tick`, VIS-001 views — untouched.

## What to Build

### 1. Library `mes.workorder`

`ignition/projects/mes-2026/ignition/script-python/mes/workorder/code.py` + `resource.json` (same format as `mes/engine`). Same rules as ENG-001: **pure logic separated from I/O, source parses under Jython 2.7 and Python 3**, style of `mes.engine`.

- `handle(st, sample, events, db)` — all of rules 1–7. Pure except for calls on the `db` object. Returns a list of log lines (strings); it does not log.
- `db` interface (one class per backend, same method names):
  `next_released(product)` → work_order_id or None · `start_run(wo, product, t, good_counter, reject_counter)` → run_id · `update_run(run_id, good, reject, reset_seen)` · `end_run(run_id, t, good, reject)` · `complete(wo, t)` · `open_runs()` → list of dicts (newest first).
- `_SqlDb` — implements it with `system.db.runPrepQuery` / `runPrepUpdate` / `runPrepUpdate(..., getKey=…)` or `RETURNING` via `runPrepQuery`, `database='mes'`, times passed as `system.date.fromMillis(t)`. Exact SQL as in Rules.
- `on_tick(sample, events)` — the handler: `_store()` (versioned key `mes.workorder.v1` in `getGlobals()`), `handle(st, sample, events, _SqlDb())`, log each returned line at INFO on logger `mes.workorder`. A database exception is logged with the traceback and does not kill the engine (the engine's `_dispatch` also guards).
- Store: `run` (open run: `run_id` or None, `work_order_id` or None, `product`, `start`, `good`, `reject`, `reset_seen`, `attributed`), `resumed` (bool, rule 6 done), `unattributed` (last few closed unattributed runs, for WO-002).
- Read API (copies): `current()` → open run dict or None · `unattributed_runs()` → list.

### 2. Register the handler in `mes.engine`

In `mes/engine/code.py`, replace `HANDLERS = []` with:
```python
def _workorder(sample, events):
	mes.workorder.on_tick(sample, events)

# Later tasks append h(sample, events) here.
HANDLERS = [_workorder]
```
(Resolved at call time, so the offline harness, which never calls `_dispatch`, is unaffected.)

### 3. Offline replay test

`ignition/tests/replay_workorder.py` — Python 3 stdlib. Reuse the sim sample builder from `replay_engine.py` (move shared helpers into `ignition/tests/simreplay.py` if needed; `replay_engine.py` output must stay identical). Load both `code.py` files with the stub `system`; feed samples through `engine.step()` then `workorder.handle()` with a `FakeDb` that mirrors the SQL semantics on an in-memory copy of the **DB-002 seed** (10 orders, WO-1010 Planned, all `released_at` equal).

Assert over **two full loops** starting at J101 Setup:
- Runs attributed in order WO-1001 … WO-1009; the second-loop 105 run **unattributed** (WO-1010 stays Planned, no row).
- WO-1001 … WO-1009 end `Complete` (each completed by the next product change; WO-1009 by the start of the 105 Setup), `completed_at` = that product-change sample time.
- Good meters per run exactly **600, 375, 450, 525, 365, 600, 375, 450, 525**; reject **18, 18, 18, 18, 83, 18, 18, 18, 18**; unattributed 105 run: good 365, reject 83 in memory.
- `started_at` and run `start_ts` = the `run_start` sample time; WO goes Running on that same sample (within one sample).
- Tie-break: WO-1001 is chosen over WO-1006 for the first 101 run.

Edge cases, each asserted:
- **Sim restart mid-run** (meters drop to 0) → `counter_reset_seen` true, totals never decrease, no negative value anywhere.
- **Resume, same product** (FakeDb has an open run for the current product, empty store) → resumes, totals continue from the stored values.
- **Resume, different product** → old run closed and its order Complete.
- **Press Running at startup, no open run** → nothing attributed until the next `run_start`.
- **No Released order for the product** → unattributed, no DB write.
- **A failed `update_run`** (FakeDb raises once) → next tick writes the correct absolute totals.

Exit non-zero on any failure. Paste the full output. `replay_engine.py` must still pass.

### 4. Deploy — no restart

The project folder is bind-mounted, so the files are already in the gateway; 8.3 does not watch the folder.
1. Record StartedAt for `ignition`, `coreflux`, `db`.
2. **Stop. Dev Report up to here; `agent_handoff.md`: "Ready to load — waiting for Walker (Scan File System, after DB-002)."**
3. Walker runs the seed (DB-002, its own Dev command) when he is ready, then clicks **Gateway web UI → Projects → Scan File System** (the VIS-001 method, no settings change). If the scan does not load the library, stop and ask; a restart needs Walker's OK.

### 5. Live check (short)

After the scan, watch the log (`m.workorder`, `m.engine`) until the **first `run_start` after the scan** (a job is ~3.5 min; at most one job's wait) plus ~30 s. Paste the lines, then query and paste:
```sql
select work_order_id, status, started_at from mes.work_order where status in ('Running','Complete') order by work_order_id;
select run_id, work_order_id, product_code, start_ts, end_ts, good_meters, reject_meters, attributed_by, counter_reset_seen from mes.work_order_run order by run_id;
```
Check: the order picked is the earliest Released for that product (lowest ID); it went Running at the `run_start` sample time; `good_meters` rising by the job step (8, 5, 6, 7, 5 per sample) and holding through a fault. If the job running at the scan started before it, the log shows the rule-6 line and that job stays unattributed — expected. **Completion is proven by the replay; do not wait for it live.** If a later product change happens while you are still watching, paste it too. Ask Walker to glance at `Work Order/Active` in the Tag Browser.

## Acceptance Criteria

- [ ] `mes.workorder` implements rules 1–7 exactly; pure `handle()` + `_SqlDb`; Jython/Python 3 compatible; no `[MQTT Engine]`, no tag writes, no schema change.
- [ ] Engine change limited to registering `_workorder` in `HANDLERS`; `replay_engine.py` passes unchanged.
- [ ] `replay_workorder.py` passes: attribution order, unattributed 105, Complete statuses, exact meters per run, and all six edge cases.
- [ ] Every SQL statement is parameterised (`runPrepQuery` / `runPrepUpdate`), `database='mes'`; no string-built SQL.
- [ ] Live: first attribution correct, Running at the `run_start` sample, meters rising by the job step; log lines and query output pasted.
- [ ] No container restarted. `press1`, VIS-001, the tag files and `db/` unchanged.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/workorder/code.py`, `resource.json` | New |
| `ignition/projects/mes-2026/ignition/script-python/mes/engine/code.py` | Register `_workorder` in `HANDLERS` only |
| `ignition/tests/replay_workorder.py` (+ optional `ignition/tests/simreplay.py`) | New |
| `ignition/tests/replay_engine.py` | Only if helpers move to `simreplay.py`; output identical |
| `docs/tasks/task_WO-001_run_attribution.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

The PM updates `docs/architecture.md`, `docs/db_schema.md` (writers) and the backlog at close-out.

## Dev Instructions

Spec: `docs/tasks/task_WO-001_run_attribution.md`. Build the `mes.workorder` library as an `mes.engine` handler: attribute each Setup→Running run to the earliest Released work order for that product (ties on `work_order_id`), set Running and Complete from the PLC, accumulate good/reject meter deltas into `mes.work_order_run`, handle sim restarts and gateway restarts, and leave a run unattributed when no order is Released. Prove it with an offline replay of two sim loops against the DB-002 seed, then **stop and wait for Walker** to run the seed and click Scan File System; finish with the short live check.

Hard constraints:
- **No container restarts.** Loading is Walker's Scan File System click. A restart only with Walker's explicit OK.
- **Do not run `db/002_seed.sql`** — that is DB-002, Walker's call. Do not change the schema. Never delete rows; write only `work_order` (status, started_at, completed_at) and `work_order_run`.
- Never stop, start, update or reimport the sim; never touch UNS Studio.
- Read only `[default]` through the engine; never `[MQTT Engine]`, `4.0 Printing/…`, `Probe/…`. Do not write any tag. Publish nothing.
- Do not change `mes.engine` beyond registering the handler. Do not touch `press1`, `press1Tick`, VIS-001 views, the tag files or generators, `db/`.
- Never touch `ign-mes-demo`. Never print `.env` or any password. No commit, no push; stage nothing.
- Anything this spec does not cover: stop and ask in the Dev Report.

## Dev Report

**Status:** ✅ Done — ready for PM verification. Steps 1–5 are done: Walker clicked Scan File System, and the live check shows the first attribution correct. DB-002 was already applied (Walker said "run the seed" earlier in this Dev session, 17:07:34 UTC). The Tag Browser glance at `Work Order/Active` is requested from Walker; the same query tags return WO-1001 via psql (below).

**Note on approval:** the spec read "Specced — awaiting Walker's approval" when this run started; I took the lead launching `/dev` on it as the approval.

### What was built

- `ignition/projects/mes-2026/ignition/script-python/mes/workorder/code.py` (new):
  - `handle(st, sample, events, db)` holds rules 1–7, pure except for `db` calls, and returns log lines.
  - Event order inside one tick: rule 6 resume (first tick only), then `restart`, then `product_change`, then `run_start`, then this sample's `dg`/`dr` added to the open run. That is rule 5.
  - Totals are written as absolute values only when `(good, reject, reset_seen)` differs from the last successful write (`run['saved']`), so a failed write heals on the next tick.
  - An unattributed run is counted in memory and appended to `unattributed` (last 20) when it closes.
  - `_SqlDb` implements `next_released` / `start_run` / `update_run` / `end_run` / `complete` / `open_runs` with `system.db.runPrepQuery` / `runPrepUpdate` on `database='mes'`. Times go through `system.date.fromMillis(int(t))`, and `INSERT … RETURNING run_id` goes through `runPrepQuery`.
  - `on_tick(sample, events)` uses the `mes.workorder.v1` store in `getGlobals()` and logs each line at INFO on `mes.workorder`. Any exception is logged with its traceback and swallowed, so the engine keeps running.
  - Read API: `current()` and `unattributed_runs()` return copies.
- `…/mes/workorder/resource.json` (new): same format as `mes/engine/resource.json`.
- `…/mes/engine/code.py` (modified): only `_workorder()` and `HANDLERS = [_workorder]`, exactly as specified.
- `ignition/tests/simreplay.py` (new): the sim sample builder (`TOPICS`, `T0`, `loop_samples`, `load_sim`) and the stub `system` plus a `load()` helper, moved out of `replay_engine.py`.
- `ignition/tests/replay_engine.py` (modified): imports those helpers from `simreplay.py` instead of defining them. Its output is byte-identical to the pre-change baseline (`cmp`).
- `ignition/tests/replay_workorder.py` (new): two full loops from J101 Setup through `engine.step()` then `workorder.handle()` (dispatched only when the engine accepted the sample, as `tick()` does), against a `FakeDb` holding the DB-002 seed (10 orders, WO-1010 Planned, equal `released_at`). The six edge cases follow.

### Files changed

- Created: `ignition/projects/mes-2026/ignition/script-python/mes/workorder/code.py`, `…/mes/workorder/resource.json`, `ignition/tests/replay_workorder.py`, `ignition/tests/simreplay.py`
- Modified: `ignition/projects/mes-2026/ignition/script-python/mes/engine/code.py` (handler registration only), `ignition/tests/replay_engine.py` (helpers moved; output identical), `docs/tasks/task_WO-001_run_attribution.md` (Status, Dev Report), `docs/agent_handoff.md` (Dev status)
- Not touched: `press1`, `press1Tick`, VIS-001 views, tag files and generators, `db/`. No container was restarted, nothing was written to the database, and nothing was staged or committed.

### Gate results

```
$ python3 -I ignition/tests/replay_workorder.py      (exit 0)
Sim: fourzero_printing_press1, two loops, 1046 samples, first phase J101 Setup

Log:
  run 1 attributed to WO-1001 product=101
  complete WO-1001 good=600 planned=600 diff=0 reject=18
  run 2 attributed to WO-1002 product=102
  complete WO-1002 good=375 planned=375 diff=0 reject=18
  run 3 attributed to WO-1003 product=103
  complete WO-1003 good=450 planned=450 diff=0 reject=18
  run 4 attributed to WO-1004 product=104
  complete WO-1004 good=525 planned=525 diff=0 reject=18
  run 5 attributed to WO-1005 product=105
  complete WO-1005 good=365 planned=365 diff=0 reject=83
  run 6 attributed to WO-1006 product=101
  complete WO-1006 good=600 planned=600 diff=0 reject=18
  run 7 attributed to WO-1007 product=102
  complete WO-1007 good=375 planned=375 diff=0 reject=18
  run 8 attributed to WO-1008 product=103
  complete WO-1008 good=450 planned=450 diff=0 reject=18
  run 9 attributed to WO-1009 product=104
  complete WO-1009 good=525 planned=525 diff=0 reject=18
  unattributed run product=105

Runs:
  run 1 WO-1001 product 101 good 600 reject 18 start 1792000018000 end 1792000198000 reset False
  run 2 WO-1002 product 102 good 375 reject 18 start 1792000242000 end 1792000422000 reset False
  run 3 WO-1003 product 103 good 450 reject 18 start 1792000444000 end 1792000624000 reset False
  run 4 WO-1004 product 104 good 525 reject 18 start 1792000658000 end 1792000838000 reset False
  run 5 WO-1005 product 105 good 365 reject 83 start 1792000866000 end 1792001046000 reset False
  run 6 WO-1006 product 101 good 600 reject 18 start 1792001064000 end 1792001244000 reset False
  run 7 WO-1007 product 102 good 375 reject 18 start 1792001288000 end 1792001468000 reset False
  run 8 WO-1008 product 103 good 450 reject 18 start 1792001490000 end 1792001670000 reset False
  run 9 WO-1009 product 104 good 525 reject 18 start 1792001704000 end 1792001884000 reset False

Two loops:
  PASS  runs attributed in order WO-1001 .. WO-1009 (['WO-1001', 'WO-1002', 'WO-1003', 'WO-1004', 'WO-1005', 'WO-1006', 'WO-1007', 'WO-1008', 'WO-1009'])
  PASS  tie-break: WO-1001 chosen over WO-1006 for the first 101 run
  PASS  good meters per run 600, 375, 450, 525, 365, 600, 375, 450, 525
  PASS  reject meters per run 18, 18, 18, 18, 83, 18, 18, 18, 18
  PASS  all nine runs closed
  PASS  WO-1001 .. WO-1009 Complete, WO-1010 Planned
  PASS  WO-1010 untouched, no run row
  PASS  engine: 10 run_start, 9 product_change
  PASS  run start_ts = the run_start sample time
  PASS  started_at = the run_start sample time (Running on that sample)
  PASS  completed_at = the next product_change sample time (WO-1009 at the 105 Setup)
  PASS  run end_ts = the product_change sample time
  PASS  WO-1009 completed on the first loop-2 J105 Setup sample
  PASS  second-loop 105 run unattributed, in memory good 365 reject 83 ((365.0, 83.0))
  PASS  log: unattributed run product=105
  PASS  log: complete WO-1001 good=600 planned=600 diff=0 reject=18
  PASS  start_good_counter is the counter before the run: counter at close - start = 600

Edge cases:
  PASS  sim restart: counter_reset_seen true
  PASS  sim restart: totals never decrease, never negative ([(8, 0), (16, 0), (24, 0), (32, 0), (40, 0), (40, 0), (48, 0), (56, 2)])
  PASS  sim restart: totals good 56 reject 2 (56, 2)
  PASS  resume same product: resumed run 1, no new row
  PASS  resume same product: totals continue from stored 300/9 -> 316/11
  PASS  resume same product: log 'resumed run 1 ...'
  PASS  resume different product: old run closed at the current sample with stored totals
  PASS  resume different product: WO-1001 Complete
  PASS  resume different product: no run open
  PASS  running at startup: nothing attributed, no db write
  PASS  running at startup: log line
  PASS  running at startup: next run_start attributes WO-1002 (25 m); WO-1001 stays Released
  PASS  no Released order: no db write, no run row
  PASS  no Released order: unattributed run in memory, good 25
  PASS  no Released order: On Hold and Planned orders untouched
  PASS  no Released order: log 'unattributed run product=105'
  PASS  failed update_run: db still at 16 after the failed tick
  PASS  failed update_run: next tick writes the absolute total 40

PASSED: 0 failure(s)
```

```
$ python3 -I ignition/tests/replay_engine.py         (exit 0)
PASSED: 0 failure(s)
$ cmp <baseline from HEAD-era run> <after the simreplay move>   -> identical
```

Jython 2.7 parse used the same method as ENG-001: the gateway's own `jython-ia-2.7.4.0.jar`, run with `-S` through the gateway JRE, on a throwaway `/tmp/wo001_check.py` in the container that I deleted afterwards. It only compiles; nothing is loaded into the gateway:
```
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/script-python/mes/workorder/code.py
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/script-python/mes/engine/code.py
```

Greps on `mes/workorder/code.py`:
- no `MQTT Engine`, `4.0 Printing`, `Probe/`, `system.tag.` or `writeBlocking`
- 7 `runPrepQuery` / `runPrepUpdate` calls
- no string-built SQL

Compose:
```
$ docker compose config -q && echo "compose config: OK"
compose config: OK
```

Database before loading (unchanged by this task so far): `work_order` has 9 Released and 1 Planned; `work_order_run` has 0 rows.

StartedAt (step 4.1):
```
ignition 2026-10-22T17:00:29.102224928Z
coreflux 2026-10-20T18:09:09.798293762Z
db 2026-10-22T15:43:40.588308669Z
```

### Live check (after Walker's Scan File System, 2026-10-22)

Log (`m.workorder`, `m.engine`) from the scan to the first attribution and about 1 min after. Gateway log times are local (UTC−5); event `t=` values are UTC.
```
12:14:22 I [Project    ]: Restarting gateway scripts... project=mes-2026, collection=mes-2026   (x2)
12:14:25 I [m.workorder]: workorder store created mes.workorder.v1
12:14:25 I [m.workorder]: press running at startup, no open run: waiting for next run_start
12:15:07 I [m.engine   ]: segment_close seq=62 state=1 fault=0 product=105 start=2026-10-22T17:14:15.849Z end=2026-10-22T17:15:05.299Z dur_s=49.45
12:15:09 I [m.engine   ]: segment_open seq=63 state=3 fault=103 product=105 start=2026-10-22T17:15:05.299Z
12:15:19 I [m.engine   ]: product_change seq=64 lanes=4 from=105 to=101 t=2026-10-22T17:15:17.509Z
12:15:19 I [m.engine   ]: segment_close seq=65 state=3 fault=103 product=105 ... dur_s=12.21
12:15:21 I [m.engine   ]: segment_open seq=66 state=2 fault=0 product=101 start=2026-10-22T17:15:17.509Z
12:15:37 I [m.engine   ]: segment_close seq=67 state=2 fault=0 product=101 ... dur_s=18.368
12:15:37 I [m.engine   ]: run_start seq=68 product=101 lanes=4 t=2026-10-22T17:15:35.877Z
12:15:37 I [m.workorder]: run 1 attributed to WO-1001 product=101
12:15:39 I [m.engine   ]: segment_open seq=69 state=1 fault=0 product=101 start=2026-10-22T17:15:35.877Z
12:16:27 I [m.engine   ]: segment_close seq=70 state=1 fault=0 product=101 ... dur_s=50.95
12:16:29 I [m.engine   ]: segment_open seq=71 state=3 fault=101 product=101 start=2026-10-22T17:16:26.827Z
12:16:37 I [m.engine   ]: segment_close seq=72 state=3 fault=101 product=101 ... dur_s=10.28
12:16:39 I [m.engine   ]: segment_open seq=73 state=1 fault=0 product=101 start=2026-10-22T17:16:37.107Z
```
- The scan reloaded the scripts without clearing the engine store (no new `engine store created`).
- The 105 job running at the scan had started at 17:12:11, before the scan. It stayed unattributed, with the rule-6 line, as expected. Its `product_change` found no open run, so it wrote nothing.
- Warnings or errors since the scan: **0** (`| [EW] [` count).

Queries (17:17 UTC):
```
 work_order_id | status  |         started_at
---------------+---------+----------------------------
 WO-1001       | Running | 2026-10-22 17:15:35.877+00
(1 row)

 run_id | work_order_id | product_code |          start_ts          | end_ts | good_meters | reject_meters | attributed_by | counter_reset_seen | start_good_counter | start_reject_counter
--------+---------------+--------------+----------------------------+--------+-------------+---------------+---------------+--------------------+--------------------+----------------------
      1 | WO-1001       |          101 | 2026-10-22 17:15:35.877+00 |        |         296 |            10 | auto          | f                  |             127026 |                 8600
(1 row)
```
Run 1 polled every ~2 s (time, good, reject):
```
17:17:04|296|10
17:17:07|304|10
17:17:09|312|10
17:17:11|320|10
```
Order statuses: Planned 1, Released 8, Running 1.

What this shows:
- WO-1001 is the earliest Released 101 order; the tie with WO-1006 broke on ID.
- It went Running with `started_at` and `start_ts` equal to the `run_start` sample time, 17:15:35.877.
- Good meters rise +8 per sample.
- The 101 web-break fault added 10 reject meters (5 samples × 2) while good held. Good meters at 296: about 25 Run 1 samples × 8 = 200, plus about 12 Run 2 samples × 8 = 96. Nothing was added during the 5 Faulted samples.
- `Work Order/Active` query tags, run through psql: WorkOrderId `WO-1001`, ProductCode `101`, Status `Running`, QtyGood `360`, QtyReject `10`, StartedAt `2026-10-22 17:15:35.877+00`.
- Completion is proven by the replay; the live check did not wait for it.

No container restarted (StartedAt unchanged: ignition 17:00:29, coreflux 2026-10-20 18:09:09, db 15:43:40). `press1`, VIS-001, the tag files and `db/` unchanged.

### Deviations from spec

1. **`update_run` also writes `counter_reset_seen`:** `UPDATE mes.work_order_run SET good_meters = ?, reject_meters = ?, counter_reset_seen = ? WHERE run_id = ?`. The spec's interface passes `reset_seen` and rule 3 needs it in the row, but the rule 2 SQL as written does not set it.
2. **`complete(wo, t)` returns `planned_meters`** (`… AND status = 'Running' RETURNING planned_meters`, via `runPrepQuery`), or None if no row changed. Rule 4's log line needs `planned=<p>`, and the `db` interface has no other way to get it. If None comes back, the run is still closed and the log says the order was not Running.
3. **`start_good_counter` / `start_reject_counter`** hold the counter *before* the `run_start` sample, `good − dg`. That way `counter − start_counter = good_meters`, because the run_start sample's own delta is counted (rule 2). The replay asserts it: counter at close − start counter = 600.
4. **One extra store field** on the open run: `saved`, the last totals written, which rule 2's change detection needs.
5. **Resumed runs have `start = None` in memory**, because the rule 6 `open_runs` SQL as given does not select `start_ts`. Only `current()` exposes it.

### Open questions for the PM

1. **`run_start` while a run is already open** (Setup → Running with no product change, i.e. two consecutive jobs of the same product) is not covered by rules 1–7. For now `handle()` keeps the open run, logs `run_start product=<p> while run <id> is open: ignored` and attributes nothing new. The sim never does this. Should it instead close and complete the open run and attribute a new one?
2. **Rule 6, press Running at startup with no open run:** I keep no in-memory run, so meters until the next `run_start` are not counted anywhere. That matches "stay unattributed until the next run_start", but it differs from a rule-1 unattributed run, which is counted in memory. Confirm this is intended.
3. **`start_run` is two statements, not one transaction** (UPDATE order, then INSERT run, in the spec's order). If the INSERT fails, the order is left Running with no run row. This is acceptable for a teaching build; say if you want it in `system.db.beginTransaction`.

## PM Verification

**PM, 2026-10-22 — PASS** (Walker: "Fix this - assume we are good" — close on the PM's checks; the Dev's own live write-up was not waited for).

- [x] `mes/workorder/code.py` read (rules, `handle`, `_SqlDb`): rules 1–7 as specced — earliest Released with `ORDER BY released_at, work_order_id`; Running guarded `AND status = 'Released'`; run row with start counters; absolute totals written only when changed; `counter_reset_seen` on restart; complete guarded `AND status = 'Running'` with `RETURNING planned_meters`; event order restart → product_change → run_start; resume / close / wait on startup; unattributed kept in memory. All SQL parameterised on `database='mes'`; no DELETE, no tag writes, no `[MQTT Engine]`.
- [x] Engine change = `_workorder` wrapper + `HANDLERS = [_workorder]` only. `replay_engine.py` passes (0 failures) after the helper move to `simreplay.py`.
- [x] `replay_workorder.py` run by the PM: 0 failures — attribution WO-1001…1009 in order, tie-break, 600/375/450/525/365/600/375/450/525 good, 18×4/83/18×4 reject, all Complete at the product-change sample, WO-1010 untouched, second-loop 105 unattributed (365/83 in memory), six edge cases.
- [x] Live (PM, read-only, after Walker's Scan File System 12:14:25 CDT): store created; "press running at startup, no open run: waiting for next run_start" (mid-105); run_start 101 at 17:15:35.877Z → run 1 attributed to **WO-1001**, `started_at` = that sample time exactly; good 80 m after 10 samples (step 8), 408 m / reject 14 m at the last check. No handler errors.
- [x] No container restart (Ignition 17:00:29Z = UNS-004). `press1`, views, tag files, `db/` unchanged.
- Accepted extra: a `run_start` while a run is already open is logged and ignored (cannot happen in this sim — every job change has a product change).
- Not observed live yet: Complete on the next product change (proven by the replay; WO-1001 should show ~600 / 18 when 102 starts).

**Answers to the Dev's open questions (PM, 2026-10-22):**
1. `run_start` with a run already open → keep it as built (log and ignore). This sim never runs the same product twice in a row; revisit only if a later sim does.
2. Press Running at startup with no open run → yes, intended: we did not see that run begin, so nothing is counted. Different from rule 1 on purpose.
3. `start_run` as two statements → accepted for the teaching build. WO-003's actions use transactions; no change to WO-001.
Dev's live write-up (arrived after close-out) agrees: WO-1001 picked at 17:15:35.877Z, good +8 per sample, held through a web break (+10 reject), `Work Order/Active` queries return WO-1001.

User-facing impact: `mes.work_order` / `mes.work_order_run` now fill from the PLC; `Work Order/Active/*` tags show the running order. Docs: `architecture.md`, `db_schema.md` writers.
