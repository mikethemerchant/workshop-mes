# WO-002 — Work order test table (Day 2 Hour 2 test visual)

**Status:** ✅ Complete — 2026-10-22, verified by the PM, approved by Walker
**Priority:** P1 · **Effort:** S (one Dev session; no restart)
**Depends on:** WO-001 (its `mes.workorder` read API and rows), DB-002 ✅, UNS-004 ✅
**Bug:** —

---

## Background

The Hour 2 work-order test visual: a **bare** Perspective page that shows, live, what WO-001 records, so the class can check MES §3's test criteria by eye (Running within one sample, good meters stepping 8/5/6/7/5 and holding in a fault, Complete with 600/375/450/525/365 ± one step and rejects 18/18/18/18/83, never negative after a sim restart). Test visuals on Hours 2–3 are throwaway (`claude_project_instructions.md`); the real operator interface is VIS-002 in Hour 4. No styling work.

## What Already Exists (Do Not Rewrite)

- View `mes.v_work_order_table` (DB-001): `work_order_id, product_code, product_family, status, priority, planned_meters, good_m, reject_m, lanes, run_start, run_end, due_date`.
- `mes.workorder` (WO-001): `current()` → open run dict or None (`work_order_id` or None, `product`, `start`, `good`, `reject`, `attributed`), `unattributed_runs()` → list. **Read only — this task does not edit `mes/workorder/code.py` or `mes/engine/code.py`.** If WO-001's final API names differ, use WO-001's and note it.
- Binding pattern to copy (VIS-001, `views/Press1/Tabs/Production/view.json`): a view custom property with an expression binding `now(2000)` and a script transform that returns a library call. Polling every 2 s.
- `page-config/config.json` has one page, `/press1`. Gateway TZ is America/Chicago; database times are `timestamptz` (UTC).
- Loading project changes: Walker's **Scan File System** click (gateway web UI → Projects). No restart.

## What to Build

### 1. Data module `mes.ui`

`ignition/projects/mes-2026/ignition/script-python/mes/ui/code.py` + `resource.json` (format of `mes/engine`). Holds view-data shaping for the MES test visuals (later test tables add functions here). Same rules as ENG-001/WO-001: pure shaping separated from I/O, Jython 2.7 / Python 3 compatible.

- `work_order_rows()` — `system.db.runQuery("SELECT * FROM mes.v_work_order_table ORDER BY work_order_id", "mes")` (no parameters needed), then `shape_work_orders(rows, now_ms)`.
- `shape_work_orders(rows, now_ms)` — pure. Returns a list of dicts, one per order, keys in this column order:

| Key | Header | Value |
|---|---|---|
| `wo` | Work order | `work_order_id` |
| `product` | Product | `product_code` + `" "` + `product_family` |
| `status` | Status | `status` |
| `priority` | Priority | `priority` |
| `planned_m` | Planned m | int |
| `good_m` | Good m | int |
| `reject_m` | Reject m | int |
| `lanes` | Lanes | int |
| `lane_m` | Lane-m | `good_m × lanes` (MES §3 FR4) |
| `diff_m` | Over/under m | `good_m − planned_m` when status is `Complete`, else `""` |
| `run_start` | Run start | `HH:mm:ss` local (CDT), `""` if none |
| `run_end` | Run end | same |
| `due` | Due | `HH:mm` local; append `" late"` when `due_date < now` and status not `Complete`/`Closed`/`Cancelled` |

- `open_run_line()` — from `mes.workorder.current()`: `"Running WO-1003 · 103 · good 312 m · reject 18 m · since 12:41:07"`, or `"Running UNATTRIBUTED · 105 · good … · since …"`, or `"No open run"`.
- `unattributed_rows()` — from `mes.workorder.unattributed_runs()`: `product`, `start`, `end`, `good_m`, `reject_m`, times `HH:mm:ss` local.

Formatting of times happens in the I/O wrappers (`system.date.format`); `shape_work_orders` receives epoch ms or `None` so it stays pure (convert `java.util.Date`/`Timestamp` with `.getTime()` before calling).

### 2. View `Mes/Test/WorkOrders` and page `/mes/work-orders`

- View files under `com.inductiveautomation.perspective/views/Mes/Test/WorkOrders/` (`view.json` + `resource.json` like `Press1/Main`).
- Root flex column. Top: label `"Work orders — WO-001 test (throwaway)"`; label bound to `open_run_line()`. Middle: `ia.display.table` with `props.data` = `work_order_rows()`, `props.columns` defined in the table order above (headers as listed, no sorting by the user needed), height to fit 10 rows. Bottom: label `"Unattributed runs (product made with no Released order)"` and a second table bound to `unattributed_rows()`.
- Bindings: one view custom property per data function, each `now(2000)` + script transform `return mes.ui.<fn>()` (the VIS-001 pattern). On exception the transform returns `[]` / `"error: <msg>"` and logs once on logger `mes.ui`.
- No styles beyond Perspective defaults. Do not reference the `press1` stylesheet classes or the `press1` library.
- Add one entry to `page-config/config.json`: `"/mes/work-orders": {"viewPath": "Mes/Test/WorkOrders", "title": "MES test — Work orders", "docks": {}}`. Leave `/press1` exactly as it is.

### 3. Offline check

`ignition/tests/test_ui_work_orders.py` — Python 3 stdlib: load `mes/ui/code.py` with the stub `system` (as the replay tests do) and assert `shape_work_orders` on hand-built rows: lane-m, diff only on Complete, `late` flag (due past + Running → late; due past + Complete → not late), `None` times → `""`, column order. Paste the output.

### 4. Load and check live

1. **Stop** after the offline check: Dev Report up to here; `agent_handoff.md`: "Ready to load — waiting for Walker (Scan File System)."
2. After Walker's scan, open http://localhost:8089/data/perspective/client/mes-2026/mes/work-orders if the Dev can (browser tools); otherwise ask Walker to open it.
3. Compare the page with `docker exec mes-workshop-2026-db-1 psql -U mes -d mes -c "select work_order_id, status, good_m, reject_m, run_start, run_end from mes.v_work_order_table order by 1"` at the same moment: same statuses and meters (± one sample of lag). Paste the psql output and Walker's (or the Dev's) observation.
4. Confirm `/press1` still loads.

## Acceptance Criteria

- [ ] `mes.ui` with `work_order_rows`, `shape_work_orders` (pure), `open_run_line`, `unattributed_rows`; Jython/Python 3 compatible; SELECT-only on connection `mes`; no tag writes; no `[MQTT Engine]`.
- [ ] `/mes/work-orders` shows the 13 columns above for all 10 orders, the open-run line and the unattributed table, refreshing every 2 s.
- [ ] Offline test passes.
- [ ] Live page matches the psql query (± one sample).
- [ ] `/press1` entry, the `press1` library, VIS-001 views, `mes.engine`, `mes.workorder` unchanged. No container restarted; loaded by Scan File System.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/ui/code.py`, `resource.json` | New |
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/views/Mes/Test/WorkOrders/view.json`, `resource.json` | New |
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/page-config/config.json` | Add `/mes/work-orders` only |
| `ignition/tests/test_ui_work_orders.py` | New |
| `docs/tasks/task_WO-002_work_order_test_table.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

## Dev Instructions

Spec: `docs/tasks/task_WO-002_work_order_test_table.md`. Build a bare Perspective test page `/mes/work-orders` (view `Mes/Test/WorkOrders`) backed by a new `mes.ui` module: a 13-column work-order table from `mes.v_work_order_table`, an open-run line and an unattributed-runs table from `mes.workorder`, all polling every 2 s. Test the pure row shaping offline, **stop for Walker's Scan File System click**, then compare the live page with a psql query.

Hard constraints:
- **Start only after WO-001 is verified by the PM** (this task reads its API). If WO-001 is still open, stop and say so.
- No container restarts. Read-only database access (SELECT). No schema change, no tag writes, publish nothing.
- Do not edit `mes/engine`, `mes/workorder`, `press1`, `press1Tick`, any `Press1/*` view, the stylesheet, the tag files or `db/`. In `page-config/config.json` add one entry only.
- Never stop, start, update or reimport the sim; never touch UNS Studio. Never read `[MQTT Engine]`, `4.0 Printing/…`, `Probe/…`.
- Never touch `ign-mes-demo`. Never print `.env` or any password. No commit, no push; stage nothing.
- Anything this spec does not cover: stop and ask in the Dev Report.

## Dev Report

**Status:** ✅ Done — ready for PM verification (Dev, 2026-10-22, third run). Steps 1–4 done: built, offline check passes, Walker ran Scan File System (gateway log `12:22:03 Restarting gateway scripts... project=mes-2026`), and the live page matches psql.

*(Two earlier runs today stopped ⛔ Blocked because WO-001 wasn't PM-verified yet. It is now ✅ (commit `7734d7d`, PM PASS), and Walker approved this spec, so this run started.)*

**What was built**
- `mes/ui/code.py` (new): module `mes.ui`.
  - Pure: `shape_work_orders(rows, now_ms, fmt)` returns the 13 columns in the spec's order (`WORK_ORDER_COLUMNS` holds keys and headers). `lane_m = good × lanes`. `diff_m = good − planned` only when the status is `Complete`, else `""`. `None` times become `""`. A due date gets `" late"` when it is before `now_ms` and the status is not Complete/Closed/Cancelled. Meters are `int` (`None` → 0). Also pure: `shape_open_run(run, fmt)` and `shape_unattributed(runs, fmt)`.
  - I/O: `work_order_rows()` runs `system.db.runQuery("SELECT * FROM mes.v_work_order_table ORDER BY work_order_id", 'mes')`, converts Timestamps with `.getTime()`, and calls `shape_work_orders` with `system.date.format` as `fmt` (gateway TZ). `open_run_line()` reads `mes.workorder.current()`. `unattributed_rows()` reads `mes.workorder.unattributed_runs()`. `log_once(where, err)` logs a transform failure on logger `mes.ui` (warn), once per distinct message, so a repeating failure doesn't log every 2 s.
  - The middle dot in the open-run line is written as the escape `u'\u00b7'`, so the file stays ASCII (Jython 2.7 rejects a non-ASCII literal without a coding line).
- `mes/ui/resource.json` (new): copy of `mes/engine/resource.json`.
- `views/Mes/Test/WorkOrders/view.json` (new). Root flex column, top to bottom:
  - label `Work orders — WO-001 test (throwaway)`
  - label bound to `view.custom.openRun`
  - `ia.display.table` `WorkOrders`: `props.data` ← `view.custom.workOrders`, 13 `props.columns` in spec order with the spec headers, `sortable: false`, basis 400px
  - label `Unattributed runs (product made with no Released order)`
  - table `Unattributed`: `props.data` ← `view.custom.unattributed`, columns `product, start, end, good_m, reject_m`, basis 300px

  Three view custom properties (`workOrders`, `openRun`, `unattributed`), each `now(2000)` + script transform `return mes.ui.<fn>()`, wrapped in `try/except:`. On failure the transform calls `mes.ui.log_once(...)` and returns `[]`, or `'error: <msg>'` for the line. No style classes, no `press1` references.
- `views/Mes/Test/WorkOrders/resource.json` (new): copy of `Press1/Main/resource.json`.
- `page-config/config.json` (modified): one entry added, `"/mes/work-orders": {"viewPath": "Mes/Test/WorkOrders", "title": "MES test — Work orders", "docks": {}}`. The `/press1` entry is unchanged (diff shows additions only).
- `ignition/tests/test_ui_work_orders.py` (new): loads `mes/ui/code.py` through `simreplay.load` (the stub `system`) with a fake formatter. 23 checks.

**Files changed**
- Created: `ignition/projects/mes-2026/ignition/script-python/mes/ui/code.py`, `…/mes/ui/resource.json`, `ignition/projects/mes-2026/com.inductiveautomation.perspective/views/Mes/Test/WorkOrders/view.json`, `…/WorkOrders/resource.json`, `ignition/tests/test_ui_work_orders.py`
- Modified: `ignition/projects/mes-2026/com.inductiveautomation.perspective/page-config/config.json` (one entry), `docs/tasks/task_WO-002_work_order_test_table.md` (Status, Dev Report), `docs/agent_handoff.md` (Dev status)
- Not touched: `mes/engine`, `mes/workorder`, `press1`, `Press1/*` views, stylesheet, tag files, `db/`

**Gate results**
```
$ python3 ignition/tests/test_ui_work_orders.py      (exit 0)
PASS  WORK_ORDER_COLUMNS order is the spec's 13 columns
PASS  every row has keys in column order
PASS  one row per input row, input order kept
PASS  product = code + family
PASS  lane-m = good x lanes (603 x 4)
PASS  lane-m = good x lanes (120 x 2)
PASS  diff on Complete = good - planned
PASS  diff blank when Running
PASS  diff blank when Released
PASS  meters are int
PASS  due past + Running -> late
PASS  due past + Complete -> not late
PASS  due past + Cancelled -> not late
PASS  due future + Released -> not late
PASS  run_start formatted HH:mm:ss
PASS  run_end formatted HH:mm:ss
PASS  None run_end -> ''
PASS  None run_start/run_end -> ''
PASS  None due -> ''
PASS  open-run line, attributed
PASS  open-run line, unattributed
PASS  open-run line, none
PASS  unattributed row

0 failure(s)
```
```
$ docker compose config -q && echo "compose config OK"
compose config OK
```
Jython 2.7 parse, same method as ENG-001 and WO-001: the gateway's own `jython-ia-2.7.4.0.jar`, run with `-S` through the gateway JRE, on a throwaway `/tmp/wo002_check.py` that was deleted afterwards. It only compiles; nothing is loaded:
```
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/script-python/mes/ui/code.py
```
Greps on `mes/ui/code.py` and `WorkOrders/view.json`: no `MQTT Engine`, `4.0 Printing`, `Probe/`, `system.tag.`, `writeBlocking`, `runUpdate`/`runPrepUpdate`, INSERT/UPDATE/DELETE or `press1`. Exactly one DB call, the SELECT above.

Container StartedAt (no restart by this run): ignition `2026-10-22T17:00:29Z` (the UNS-004 restart), db `2026-10-22T15:43:40Z`, coreflux `2026-10-20T18:09:09Z`.

**Live check (step 4.2–4.4)**

The Dev opened http://localhost:8089/data/perspective/client/mes-2026/mes/work-orders (Chrome, browser tools) and ran psql at the same moment.

psql, 17:23:04Z:
```
 work_order_id |  status  | good_m | reject_m |         run_start          |          run_end           
---------------+----------+--------+----------+----------------------------+----------------------------
 WO-1001       | Complete |    600 |       18 | 2026-10-22 17:15:35.877+00 | 2026-10-22 17:18:40.865+00
 WO-1002       | Complete |    375 |       16 | 2026-10-22 17:19:26.388+00 | 2026-10-22 17:22:30.749+00
 WO-1003       | Running  |     24 |        0 | 2026-10-22 17:22:53.444+00 | 
 WO-1004       | Released |      0 |        0 |                            | 
 WO-1005       | Released |      0 |        0 |                            | 
 WO-1006       | Released |      0 |        0 |                            | 
 WO-1007       | Released |      0 |        0 |                            | 
 WO-1008       | Released |      0 |        0 |                            | 
 WO-1009       | Released |      0 |        0 |                            | 
 WO-1010       | Planned  |      0 |        0 |                            | 
(10 rows)
```

Page at the same moment (Dev's observation from the screenshot):
- Open-run line: `Running WO-1003 · 103 · good 18 m · reject 0 m · since 12:22:53`
- WO-1001: Complete 600 / 18, lanes 4, lane-m 2,400, over/under 0, 12:15:35 → 12:18:40, due 13:07
- WO-1002: Complete 375 / 16, lane-m 750, over/under 0, 12:19:26 → 12:22:30, due 13:12
- WO-1003: Running 18 / 0, lane-m 72, start 12:22:53, run end blank, over/under blank
- WO-1004: due `12:13 late` (Released, due 17:13Z already passed)
- WO-1009 Rush: due 12:27, not late yet at 12:23
- WO-1010: Planned
- All statuses match psql. All times are psql UTC − 5 h (CDT).

The page's 18 vs psql's 24 is one 6 m sample step (job 103), within ± one sample.

Second sample, 17:23:12Z: psql `WO-1003|Running|48|0`; page good 42 m, lane-m 168. Again one step behind, so the page refreshes on its 2 s poll.

The layout fits all 10 rows without scrolling. Perspective adds its default pager bar ("25 rows") under each table.

The unattributed table is empty: no unattributed run has closed since the scan, so WO-001 holds none in memory. Its header row also renders blank with no data. The headers should show once the first row arrives (loop-2 job 105 per WO-001), but I haven't seen that yet.

`/press1` still loads and shows live data (RUNNING, job 103, 12:23:23).

No `mes.ui` warnings and no tracebacks in the gateway log after the scan. Container StartedAt unchanged after the check (ignition `17:00:29Z`).

**Deviations from spec**
- `shape_work_orders` takes a third argument, `fmt(ms, pattern)`, the time formatter. The spec asks for it to be pure on epoch ms *and* to return `HH:mm:ss` / `HH:mm` strings, while formatting happens with `system.date.format` in the I/O wrapper. Passing the formatter in satisfies all three: the wrapper supplies `system.date.format`, and the test supplies a fake.
- The open-run line leaves out `· since …` when `start` is `None`. That is the case for a run resumed after a gateway restart: WO-001 `_resume` sets `start=None`.
- The transforms log at warn level through `mes.ui.log_once`, once per distinct message. The spec only says "logs once".

**Open questions for the PM**
- **WO-1002 reject is 16, not the 18 the Background expects.** That figure comes from WO-001, which recorded it; this page displays it faithfully. It may come from where WO-1002's run started relative to the reject counter, but I haven't investigated, because it is outside this task. Worth a look against `mes.work_order_run` (start counters) when WO-001 is next checked.
- The unattributed table's headers haven't been seen live: the table is empty, and an empty table renders a blank header row. Confirm at the loop-2 job 105 run, or tell me if headers must show while it is empty.

## PM Verification` is still empty. Still nothing built.

**First run:**

**Why:** two gates in this spec are not met.
1. Dev Instructions → Hard constraints: *"Start only after WO-001 is verified by the PM … If WO-001 is still open, stop and say so."* WO-001 is still open: its spec Status is `🟡 Partial — built and replayed; waiting for Walker's Scan File System`, its `## PM Verification` section is empty, and its task-index row in `project_management.md` reads `📝 Specced`.
2. This spec's own Status line read `📝 Specced — awaiting Walker's approval`; no approval is recorded.

**What was built:** nothing.

**Files changed:** `docs/tasks/task_WO-002_work_order_test_table.md` (Status line, Dev Report), `docs/agent_handoff.md` (Dev status block).

**Gate results:** none run (no code written).

**Deviations from spec:** none.

**Observations for when it unblocks (read-only check, nothing changed):** `mes/workorder/code.py` exposes `current()` and `unattributed_runs()` as the spec expects, so no API rename is anticipated — confirm against WO-001's final state after its verification.

**Open questions for the PM:**
- Re-run `/dev` on this spec once WO-001 is loaded by Walker's Scan File System, live-checked and PM-verified (its `## PM Verification` filled in). Approval is already given.

## PM Verification

**PM, 2026-10-22 — PASS.**

- [x] `mes/ui/code.py`: `shape_work_orders` pure (formatter injected), 13 keys in spec order, `lane_m = good × lanes`, `diff_m` only on Complete, `late` only when due passed and status not Complete/Closed/Cancelled; `work_order_rows` SELECT-only on `mes`; no writes, no `[MQTT Engine]`, no `press1`.
- [x] `page-config/config.json`: one entry added (`/mes/work-orders`), `/press1` untouched (`git diff`).
- [x] `test_ui_work_orders.py` passes (0 failures) under `python3`; fails to import `simreplay` under `python3 -I` (missing the `sys.path` line `replay_workorder.py` has) — trivial, folded into WO-001-fix1. `replay_engine.py`, `replay_workorder.py` pass.
- [x] Live (Dev): page matches the psql query, meters within one sample; WO-1004 shows `late`; `/press1` loads; no restart.
- **WO-1002 reject 16 vs 18 (Dev's flag): a WO-001 defect, not this page.** PM query: raw reject counter span for run 2 = 18, recorded 16 — a step seen on a Setup sample after the run closed. Logged as **BUG-002**, fix specced as WO-001-fix1. The page shows the database faithfully.
- Unattributed table headers blank while empty — accepted for a throwaway test visual.

User-facing impact: new test page http://localhost:8089/data/perspective/client/mes-2026/mes/work-orders.
