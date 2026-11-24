# WO-003 — Work order manual actions: create, release, hold, cancel, close, reassign

**Status:** ✅ Complete — 2026-10-22, verified by the PM, approved by Walker
**Priority:** P1 · **Effort:** M (one Dev session; no restart)
**Depends on:** WO-001 and WO-002 **verified by the PM** (this task extends both).
**Bug:** —

---

## Background

MES §3: people set every status except Running and Complete, and an operator can reassign a run by hand. This task adds those actions to the WO-002 test page (still a bare Hour 2 test visual; VIS-002 builds the real screens). Teaching moments it enables: release WO-1010 so the second-loop 105 run attributes; hold an order and watch attribution skip it; reassign a run the MES attributed to the "wrong" order; assign an unattributed run after the fact.

## Rules (MES §3 status model, made exact)

Statuses: `Planned`, `Released`, `Running`, `Complete`, `Closed`, side states `On Hold`, `Cancelled`. **Running and Complete are never set by hand** except as the consequence of a reassign (rule R).

| Action | Allowed from | Effect (one transaction, `WHERE status = <expected>` guard) |
|---|---|---|
| **Create** | — | `INSERT` `product_code` (must exist in `product_master`), `planned_meters > 0`, `due_date` (required, any time), `priority` `Normal`/`Rush`, `notes` (optional) → status `Planned`, ID from the sequence (`WO-1011`, …). |
| **Release** | `Planned`, `On Hold` | → `Released`; `released_at = coalesce(released_at, now())` (a re-release keeps its place in the attribution order); `hold_reason = NULL`. |
| **Hold** | `Planned`, `Released` | → `On Hold`; `hold_reason` required (free text, e.g. "proof not approved"). Not allowed on `Running` — the press does not wait for the MES. |
| **Cancel** | `Planned`, `Released`, `On Hold` | → `Cancelled`. |
| **Close** | `Complete` | → `Closed`; `closed_at = now()`. |
| **Reassign (R)** | see below | see below |

**R — Reassign a run.** Inputs: a run (an attributed run from `mes.work_order_run` whose order is `Running` or `Complete`, or an unattributed run from `mes.workorder`), a target order, and "entered by" (required free text). Target must have the **same `product_code`** as the run and status **`Released`**.
- Attributed run: `UPDATE mes.work_order_run SET work_order_id = <target>, attributed_by = 'manual'`. Then recompute **both** orders' status from their runs with `status_from_runs()`: an open run → `Running` (`started_at` = earliest run start); only closed runs → `Complete` (`started_at` = earliest start, `completed_at` = latest end); no runs → `Released` with `started_at` and `completed_at` set to NULL.
- Unattributed run: `INSERT` a `work_order_run` for the target with the run's `start_ts`, `end_ts` (NULL if still open), totals, `attributed_by = 'manual'`, then recompute the target's status the same way.
- **If the run is the open run in `mes.workorder`'s store**, the in-memory run must switch to the target (`work_order_id`, `run_id`, `attributed = True`) so meter counting continues into the right order. This change goes through a **queue applied on the timer thread** (next tick, ≤ 2 s): the action appends `{'type': 'reassign', …}` to `st['pending']`; `on_tick` applies pending items before `handle()`. Every other action (including reassigning a closed run) runs directly in the session and touches only the database.
- Log every action at INFO on `mes.workorder`: `action <name> <wo> by <entered_by>: <result>`. No audit table (none in the schema); the log is the audit trail for the workshop.

Every action returns a result string: `"ok: <what changed>"` or `"error: <why>"` (e.g. `"error: WO-1003 is Running; hold is not allowed"`, `"error: status changed since you looked; refresh"` when the guard updates 0 rows). Never raise into the view.

## What Already Exists (Do Not Rewrite)

- `mes.workorder` (WO-001): store `mes.workorder.v1` with `run` (`run_id`, `work_order_id`, `product`, `start`, `good`, `reject`, `reset_seen`, `attributed`, `saved`), `unattributed` (closed runs: `product`, `start`, `end`, `good`, `reject`); `handle()`, `_SqlDb`, `on_tick()`, `current()`, `unattributed_runs()`. Keep rules 1–7 exactly; this task **adds** to the file.
- `mes.ui` and the view `Mes/Test/WorkOrders` at `/mes/work-orders` (WO-002). This task **adds** a panel to that view.
- `ignition/tests/replay_workorder.py`, `simreplay.py` (FakeDb with the DB-002 seed). Must still pass.
- Schema (DB-001): `work_order.hold_reason`, `notes`, `released_at`, `started_at`, `completed_at`, `closed_at`; `work_order_run.attributed_by` (`auto`/`manual`). No schema change.

## What to Build

### 1. Actions in `mes.workorder`

Add to `mes/workorder/code.py` (same style; pure decision logic separate from I/O; Jython/Python 3 compatible):
- Pure: `check_transition(action, current_status)` → `None` or an error string (the table above); `status_from_runs(runs)` → `(status, started_at, completed_at)`; `apply_pending(st, db)` → list of log lines (queued reassigns of the open run).
- I/O (session-callable, each returns the result string): `create(product, planned_m, due_ms, priority, notes)`, `release(wo, by)`, `hold(wo, reason, by)`, `cancel(wo, by)`, `close(wo, by)`, `reassign(run_ref, target_wo, by)` where `run_ref` is `{'run_id': n}` or `{'unattributed_start': ms}`.
- Database writes: parameterised `runPrepUpdate` / `runPrepQuery` on `database='mes'`, one transaction per action (`system.db.beginTransaction` / `commitTransaction` / `rollbackTransaction` / `closeTransaction`), status guard in every `WHERE`.
- `on_tick`: call `apply_pending(st, _SqlDb())` before `handle()`. Add `st['pending'] = []` to `_new_store()` **and** tolerate a store created by WO-001 without it (`st.setdefault('pending', [])`) — no store version bump, so the live open run survives the scan.
- Read helpers for the view: `reassignable_runs()` (attributed runs whose order is Running/Complete, plus open and closed unattributed runs, each with a label like `"run 7 · WO-1003 · 103 · 12:41:07"` / `"unattributed · 105 · 13:02:10"`) and `eligible_targets(product)` (Released orders of that product).

### 2. Panel on `Mes/Test/WorkOrders`

Below the existing tables, bare Perspective components, no styling:
- **Create:** dropdown product (101–105 from `mes.product_master`), numeric planned m, datetime due, dropdown priority, text notes, button **Create**.
- **Status:** dropdown work order (all orders, label `"WO-1004 · 104 · Released"`), text reason (for Hold), text entered by, buttons **Release**, **Hold**, **Cancel**, **Close**.
- **Reassign:** dropdown run (`reassignable_runs()`), dropdown target (`eligible_targets()` of the selected run's product), text entered by, button **Reassign**.
- One result label showing the last action's result string. Button scripts call the `mes.workorder` functions and write the result to a view custom property. Dropdown options poll with the existing `now(2000)` pattern through `mes.ui` (add small wrapper functions there if needed).

### 3. Offline tests

Extend `ignition/tests/replay_workorder.py` (or add `test_workorder_actions.py`) with FakeDb support for the new writes, and assert:
- The full transition table: every allowed action succeeds, every disallowed one returns `error:` and changes nothing (incl. Hold on Running, Close on Released, Release on Cancelled).
- Re-release keeps `released_at`; attribution order unchanged after Hold → Release.
- **Release WO-1010 before the second-loop 105 run** (replay) → that run attributes to WO-1010, nothing unattributed.
- **Hold WO-1006 before the second-loop 101 run** → no Released 101 order, run unattributed; release it later → next 101 run attributes.
- **Reassign a closed run** WO-1001 → a new Released 101 order: target Complete with the run's start/end; WO-1001 back to Released with NULL times.
- **Reassign the open run** mid-run → applied on the next tick; later deltas count into the target; at product change the target completes with the full run total.
- **Assign an unattributed run** (closed, and open) → run row inserted for the target; target Complete / Running.
- Target with a different product or not Released → `error:`, nothing changed.
WO-001's original assertions still pass unchanged.

### 4. Load and live check

1. **Stop** after the offline tests: Dev Report; `agent_handoff.md`: "Ready to load — waiting for Walker (Scan File System)."
2. After the scan (the open run must survive: check `current()` in the log or the open-run line before/after):
   - Create a test order: product 105, 100 m, due +2 h, Normal, notes "WO-003 live test" → note its ID.
   - On it: Release → Hold ("live test") → Release → Cancel. Paste each result string and the order's final row (`Cancelled`, `released_at` set once).
   - **Do not reassign live runs and do not touch seed orders** — reassign is proven offline; Walker demonstrates it in class.
3. Confirm `/mes/work-orders` and `/press1` still load.

## Acceptance Criteria

- [ ] Transition table enforced in code with a status guard in every `WHERE`; result strings, never exceptions, to the view.
- [ ] Reassign per rule R, incl. the timer-thread queue for the open run; `status_from_runs` used for both orders.
- [ ] Store compatible with WO-001's live store (no version bump; `setdefault('pending')`).
- [ ] Offline tests pass (all listed cases) and WO-001's replay still passes unchanged; `replay_engine.py` passes.
- [ ] Panel on `/mes/work-orders` with Create, Status and Reassign sections and a result label.
- [ ] Live: test order created and walked through Release → Hold → Release → Cancel; seed orders untouched; open run survived the scan.
- [ ] Parameterised SQL only; no schema change; no tag writes; no container restart.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/workorder/code.py` | Add actions, queue, read helpers |
| `ignition/projects/mes-2026/ignition/script-python/mes/ui/code.py` | Small wrappers for dropdown options, if needed |
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/views/Mes/Test/WorkOrders/view.json` | Add the action panel |
| `ignition/tests/replay_workorder.py` and/or `ignition/tests/test_workorder_actions.py`, `simreplay.py` | Tests |
| `docs/tasks/task_WO-003_manual_actions.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

## Dev Instructions

Spec: `docs/tasks/task_WO-003_manual_actions.md`. Add the manual work-order actions to `mes.workorder` — create, release, hold, cancel, close per the transition table, and reassign per rule R (status recomputed from runs, open-run reassign queued onto the timer thread) — and an action panel on the WO-002 test page. Prove it offline (transition table and the replay scenarios), **stop for Walker's Scan File System click**, then do the live walk-through on a new test order only.

Hard constraints:
- **Start only after WO-001 and WO-002 are verified by the PM.** If either is open, stop and say so.
- Keep WO-001 rules 1–7 and its live store intact (no store version bump). The open run must survive the scan.
- Live: only the new test order. Never change or reassign seed orders or live runs.
- No container restarts. No schema change. Parameterised SQL only. Never delete rows. No tag writes; publish nothing.
- Do not edit `mes/engine`, `press1`, `press1Tick`, `Press1/*` views, the stylesheet, the tag files or `db/`.
- Never stop, start, update or reimport the sim; never touch UNS Studio. Never read `[MQTT Engine]`, `4.0 Printing/…`, `Probe/…`.
- Never touch `ign-mes-demo`. Never print `.env` or any password. No commit, no push; stage nothing.
- Anything this spec does not cover: stop and ask in the Dev Report.

## Dev Report

**Status:** ✅ Done — ready for PM verification. Offline sections 1–3 pass. Walker scanned at 12:47:39 CDT; the live walk-through (4.2–4.3) ran on a new test order, WO-1011, only.

### What was built

**`mes/workorder/code.py`** (added to; WO-001 rules 1–8 and `handle()` unchanged)
- Store: `'pending': []` in `_new_store()`; `st.setdefault('pending', [])` in `_store()`. `STORE_VERSION` stays 1, key `mes.workorder.v1`.
- `on_tick`: `lines = apply_pending(st, _SqlDb())` then `lines += handle(...)`, both inside the existing try.
- `_SqlDb` gains the action methods (the WO-001 methods are untouched): `atomic(fn)` (begin/commit on an `ok` result, otherwise rollback, then close), `product_exists`, `insert_order`, `order`, `transition` (release/hold/cancel/close SQL, `WHERE work_order_id = ? AND status = ?`), `run_row`, `move_run` (`WHERE run_id = ? AND work_order_id = ?`), `insert_run` (`attributed_by = 'manual'`), `run_at`, `run_times`, `set_from_runs` (`WHERE work_order_id = ? AND status = ?`). All `runPrepQuery`/`runPrepUpdate` on `'mes'` with the transaction; returns rows changed.
- Pure: `TRANSITIONS`, `check_transition(action, status)`, `status_from_runs(runs)`, `do_create`, `do_transition`, `do_move_run`, `do_insert_run` (each takes a db and returns `ok: …`/`error: …`), `apply_pending(st, db)`.
- Session-callable, never raise: `create(product, planned_m, due_ms, priority, notes)` (accepts a Date or epoch ms for due), `release(wo, by)`, `hold(wo, reason, by)`, `cancel(wo, by)`, `close(wo, by)`, `reassign(run_ref, target_wo, by)`. Each logs `action <name> <wo> by <by>: <result>` at INFO; queued reassigns log again from `on_tick` when applied.
- Rule R: an attributed closed run → `do_move_run` in one transaction (move the row, recompute the source with the guard on its read status, recompute the target with the guard `Released`). Closed unattributed run → `do_insert_run`, with a duplicate check (`run_at(product, start)`). If the run is the store's open run, the target is checked at once, then `{'type': 'reassign', …}` goes on `st['pending']`; `apply_pending` checks again on the timer thread, runs the same `do_*` in one transaction and, on `ok`, switches the in-memory run (`work_order_id`; for an unattributed run also `run_id`, `attributed = True`, `saved`). If the run closed before the tick, nothing changes and an error line is logged.
- Read helpers: `reassignable_runs()` (attributed runs whose order is Running/Complete, newest first, then the open unattributed run and the closed ones not yet assigned; each with `label`, e.g. `run 7 · WO-1003 · 103 · 12:41:07`, `unattributed · 105 · 13:02:10 · open`), `eligible_targets(product)` (Released orders, attribution order), `run_label()`.

**`mes/ui/code.py`**: pure `run_option()` / `parse_run_option()` (value `run:<id>:<product>` or `ua:<start ms>:<product>`, so the target dropdown knows the product); I/O `product_options()`, `order_options()` (label `WO-1004 · 104 · Released`), `run_options()`, `target_options(run_value)`. Header comment updated.

**`Mes/Test/WorkOrders/view.json`**: below the two tables, title "Actions (WO-003 test)", three flex rows and a result label. Create: product dropdown, numeric planned m, datetime due, priority dropdown (Normal/Rush), notes, **Create**. Status: order dropdown, reason, entered by, **Release / Hold / Cancel / Close**. Reassign: run dropdown, target dropdown, entered by, **Reassign**. Result label bound to `view.custom.result`. New view custom props `productOptions`, `orderOptions`, `runOptions` poll with `now(2000)` + script transform + `mes.ui.log_once` (WO-002 pattern). The target dropdown's options are an expression `if(now(2000) > 0, {../ReassignRun.props.value}, '')` → `mes.ui.target_options(value)`. Each button script wraps its call in try/except and writes the result string to `view.custom.result`. No styling.

**Tests**
- `simreplay.py`: `FakeDb`, `SEED`, `SEED_RELEASED` moved here from `replay_workorder.py` (unchanged), plus the action methods above, `atomic()` with snapshot/rollback, `now`, `seq` (1011).
- `replay_workorder.py`: imports `FakeDb` from `simreplay`; its assertions are unchanged.
- `test_workorder_actions.py` (new): every case listed in section 3, plus a queued reassign whose run closed first, assigning an unattributed run twice, and the old-store `setdefault`.

### Files changed
- modified `ignition/projects/mes-2026/ignition/script-python/mes/workorder/code.py`
- modified `ignition/projects/mes-2026/ignition/script-python/mes/ui/code.py`
- modified `ignition/projects/mes-2026/com.inductiveautomation.perspective/views/Mes/Test/WorkOrders/view.json`
- modified `ignition/tests/simreplay.py`, `ignition/tests/replay_workorder.py`
- created `ignition/tests/test_workorder_actions.py`
- modified `docs/tasks/task_WO-003_manual_actions.md` (Status line, this report), `docs/agent_handoff.md` (Dev status)

### Gate results

`python3 -I ignition/tests/test_workorder_actions.py` → exit 0, 44 PASS:
```
Transition table:
  PASS  28 action x status pairs: allowed ones succeed, the rest return error and change nothing []
  PASS  check_transition: hold on Running, close on Released, release on Cancelled refused; release on On Hold allowed
  PASS  result string: 'error: WO-1003 is Running; hold is not allowed'
  PASS  hold without a reason, unknown order: error, nothing changed
  PASS  status guard misses -> 'status changed since you looked'
  PASS  hold stores hold_reason
  PASS  re-release keeps released_at, clears hold_reason
  PASS  attribution order unchanged after Hold -> Release (WO-1001 before WO-1006)
  PASS  close: Complete -> Closed, closed_at = now()
Create:
  PASS  create: WO-1011 Planned from the sequence (ok: created WO-1011 (product 105, 100 m, Normal), Planned)
  PASS  create: a second order takes the next id
  PASS  create: unknown product, planned 0, no due date, bad priority, non-number -> error, nothing inserted [...]
status_from_runs:
  PASS  no runs -> Released, NULL times
  PASS  an open run -> Running, earliest start
  PASS  closed runs -> Complete, earliest start, latest end
Release WO-1010 before the second-loop 105 run:
  PASS  release WO-1010 at the end of loop 1
  PASS  runs attributed WO-1001 .. WO-1010
  PASS  second-loop 105 run on WO-1010 (Running, 365 m), nothing unattributed
Hold WO-1006 before the second-loop 101 run, release it later:
  PASS  no Released 101 order: the loop-2 101 run is unattributed (600 m)
  PASS  WO-1006 stays On Hold
  PASS  released later: the loop-3 101 run attributes to WO-1006
Reassign a closed run WO-1001 -> a new Released 101 order:
  PASS  result: ok: run 1 WO-1001 -> WO-1011; WO-1001 Released, WO-1011 Complete
  PASS  target WO-1011 Complete with the run's start and end
  PASS  WO-1001 back to Released with NULL started_at / completed_at
  PASS  run row moved, attributed_by manual, totals kept
Reassign the open run mid-run:
  PASS  queued, nothing changed before the tick (ok: queued open run -> WO-1011; applies on the next tick (<= 2 s))
  PASS  applied on the next tick: open run and its row on WO-1011
  PASS  WO-1003 Released (NULL times), WO-1011 Running from the run start
  PASS  log: action reassign WO-1011 by test: ok: ...
  PASS  later deltas count into the same run row (222 -> 450)
  PASS  at product change WO-1011 Complete with the full run total 450 / 18; WO-1003 stays Released
  PASS  log: complete WO-1011 good=450 planned=450
Open run closes before the queue is applied:
  PASS  queued reassign of a run that closed meanwhile: error logged, nothing changed
Assign an unattributed run, closed:
  PASS  run row inserted for WO-1011 (ok: unattributed run of 105 -> WO-1011 (good 365, reject 83); WO-1011 Complete)
  PASS  WO-1011 Complete with the run's start and end
  PASS  assigning it twice -> error, nothing changed (error: the run of 105 at that start is already assigned; refresh)
Assign an unattributed run, open:
  PASS  loop-2 105 run open, unattributed
  PASS  applied on the next tick: run row inserted (open), WO-1010 Running
  PASS  in-memory run now attributed to WO-1010 with the new run_id
  PASS  at product change WO-1010 Complete with the full run 365 / 83, nothing unattributed
Bad targets:
  PASS  different product, no entered by, same order, no run, target Planned -> error, nothing changed ['error: WO-1002 is product 102, the run is 101', 'error: entered by is required', 'error: run 1 is already on WO-1001', 'error: no run 99', 'error: WO-1006 is Planned; the target must be Released']
  PASS  error strings name the product / status
Store:
  PASS  a WO-001 store gets 'pending' by setdefault; same key, open run kept
PASSED: 0 failure(s)
```
Other gates:
```
python3 -I ignition/tests/replay_workorder.py   -> PASSED: 0 failure(s)  (48 PASS, assertions unchanged)
python3 -I ignition/tests/replay_engine.py      -> PASSED: 0 failure(s)
python3 -I ignition/tests/test_ui_work_orders.py -> 0 failure(s)
python3 -I ignition/tests/replay_schedule.py    -> PASSED: 0 failure(s)  (SCHED-001's test, run read-only to confirm the simreplay.py change does not break it)
docker compose config -q                        -> compose config OK
```
Jython 2.7 compile, same method as WO-001-fix1 (the gateway's `jython-ia-2.7.4.0.jar` with `-S` through the gateway JRE, throwaway `/tmp/wo003_check.py` in the container, deleted; compile only):
```
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/script-python/mes/workorder/code.py
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/script-python/mes/ui/code.py
```
The 13 view scripts (button scripts and transforms) compile as function bodies under Python 3. Grep of the three changed Ignition files: no `MQTT Engine`, `4.0 Printing`, `Probe/`, `system.tag.`, `writeBlocking` or `DELETE`. No restart: ignition StartedAt `2026-10-22T17:00:29Z`, db `2026-10-22T15:43:40Z` (unchanged). No DB write, no schema change, no commit, nothing staged.

### Deviations from spec
- **Test seam.** The session I/O functions are thin wrappers. The logic is in `do_*(db, …)` and `_reassign(st, db, ref, target, by)`, so the offline test runs the exact reassign path, including the queue, against FakeDb. `apply_pending(st, db)` runs each item through `db.atomic()`, the same transaction wrapper the session actions use.
- **FakeDb moved** from `replay_workorder.py` to `simreplay.py` so both tests share it. The class and its WO-001 methods are unchanged; `replay_workorder.py`'s checks are unchanged.
- **Create log line** reads `action create new by -: ok: created WO-1011 …`. The order has no id before the insert, and the spec's Create inputs have no "entered by".
- **"Entered by"** is required only for reassign, as the spec says. Release, Hold, Cancel and Close accept an empty value and log `by -`.

### Open questions for the PM
1. **⚠️ Shared working tree: the scan will also load SCHED-001.** While I worked, another session changed `mes/engine/code.py` (adds a `_schedule` handler) and `page-config/config.json`, and added `mes/schedule/`, `Mes/Test/Schedule/` and `replay_schedule.py`. None of them are mine. Walker's Scan File System loads the whole project, so those uncommitted SCHED-001 changes go live in the same scan as WO-003. Please confirm with Walker that this is intended (or sequence the two) before the scan.
2. **Closed unattributed run, once assigned, still shows in the WO-002 "Unattributed runs" table**, because that table reads the in-memory list and this task's session actions touch only the database. It drops out of the Reassign dropdown (filtered against `manual` run rows), and assigning it twice returns an error. Should the WO-002 table filter it the same way? I did not change it because it is out of scope.
3. **Rule 8 edge.** If a run that has just closed is reassigned (attributed run) or assigned (unattributed run) within the one Setup sample where a late delta can still arrive, the delta goes to the attributed run row by `run_id` (correct). For an assigned unattributed run, the delta only reaches memory, not the new row. The window is one sample, so I left it; flagging it in case you want it handled.

### Live check (step 4)

**Scan.** Walker clicked Scan File System at 12:47:39 CDT (`Restarting gateway scripts… project=mes-2026`, logged twice, 12:47:39.936 and 12:47:40.012). The SCHED-001 changes loaded in the same scan (`schedule store created mes.schedule.v1` at 12:47:42), as flagged in open question 1.

**Store survived.** There is no `workorder store created` line after the scan, and no `handle failed` line. No run was open at the scan: WO-1009 had closed at 12:47:29 and the press was in 105 Setup. The store kept counting through it: `late delta … credited to run 9 (WO-1009)` came before the scan, then `unattributed run product=105` at 12:47:58 (WO-1005 Complete, WO-1010 Planned, as expected), and the open-run line on the page counted it up (`Running UNATTRIBUTED · 105 · good 90 m … since 12:47:57`). It closed at 12:51:04 with `good=365 reject=83`.

**Walk-through on the panel** (Chrome, http://localhost:8089/data/perspective/client/mes-2026/mes/work-orders). Inputs: product 105, 100 m, due Oct 22 3:00 PM CDT (≈ +2 h 10 min; the picker steps in whole hours), Normal, notes "WO-003 live test", entered by "Dev WO-003". `docker logs mes-workshop-2026-ignition-1 | grep "action "`:
```
12:50:19 I [m.workorder] action create new by -: ok: created WO-1011 (product 105, 100 m, Normal), Planned
12:50:49 I [m.workorder] action release WO-1011 by Dev WO-003: ok: WO-1011 Planned -> Released
12:51:00 I [m.workorder] action hold WO-1011 by Dev WO-003: ok: WO-1011 Released -> On Hold
12:51:06 I [m.workorder] action release WO-1011 by Dev WO-003: ok: WO-1011 On Hold -> Released
12:51:13 I [m.workorder] action cancel WO-1011 by Dev WO-003: ok: WO-1011 Released -> Cancelled
```
(Each line also carries `component=root/StatusRow/<button>, view=Mes/Test/WorkOrders@C, project-name=mes-2026`, Ignition's MDC.) The page's result label showed the same strings. After the Hold, the row read `On Hold | live test | released_at 17:50:49.227837+00`. After the re-release it read `Released | (null) | 17:50:49.227837+00`, so `released_at` was kept and `hold_reason` cleared.

Final row (psql, 12:51:15 CDT):
```
 work_order_id | product_code | planned_meters |        due_date        | priority |  status   | hold_reason |      notes       |          created_at           |          released_at          | started_at | completed_at | closed_at
 WO-1011       |          105 |            100 | 2026-10-22 20:00:00+00 | Normal   | Cancelled |             | WO-003 live test | 2026-10-22 17:50:19.197684+00 | 2026-10-22 17:50:49.227837+00 |            |              |
```
Seed orders untouched: 9 Complete + WO-1010 Planned, all seed `released_at` still `17:07:34.783423+00`. `mes.work_order_run`: 9 rows, 0 `manual`. No reassign was run live. No `run_start` for 105 happened while WO-1011 was Released (the live 105 run started at 12:47:58, before the order existed, and closed at 12:51:04 while it was On Hold), so no live run touched it.

**Pages.** `/mes/work-orders` loads with the panel, all three option dropdowns populated (orders labelled `WO-1011 · 105 · Cancelled`). `/press1` loads and is live (RUNNING, job 101, 12:51:23).

No restart: ignition StartedAt `2026-10-22T17:00:29Z`, db `2026-10-22T15:43:40Z` (unchanged). The only DB writes were WO-1011's create and its four transitions, all made through the panel.

**Observations for the PM (not blocking):**
- The DateTime Input does not take typed text. The due time has to be set with the picker's hour arrows and AM/PM select. The default when the picker opens is 12:00 AM on the picked day, so a quick Create gives a due time that is already late. Worth knowing for class.
- After a Create, the panel keeps its inputs (no reset), so a second click creates a second, identical order.

## PM Verification

**PM, 2026-10-22 — PASS.**

- [x] Tests run by the PM under `python3 -I`: `test_workorder_actions.py` 44 PASS, `replay_workorder.py` 49 PASS, `replay_engine.py`, `test_ui_work_orders.py`, `replay_schedule.py` — 0 failures each.
- [x] Code: `check_transition`, `status_from_runs`, `apply_pending` pure; every transition `UPDATE … WHERE work_order_id = ? AND status = ?`; one transaction per action (`beginTransaction` / commit / rollback / close); no DELETE, no tag writes; store `setdefault('pending')`, version still 1. Rules 1–8 untouched (only `on_tick` gained `apply_pending`).
- [x] Live (DB, PM): WO-1011 (105, notes "WO-003 live test") Cancelled, `released_at` set once, `hold_reason` cleared; 0 manual runs; WO-1001…1009 Complete, WO-1010 Planned — seed untouched. No restart (Ignition 17:00:29Z).
- Not directly tested live: an open run surviving the scan (none open at 12:47:39) — the store survived (no `store created` line).
- **Open questions → backlog (not blocking):** (2) an assigned closed unattributed run still listed in the page's table; (3) a late delta on a just-assigned unattributed run doesn't reach its new row; date picker opens at 00:00 (instant "late" order) and the panel keeps inputs (double Create = duplicate). All four logged in `backlog.md` for VIS-002 / a follow-up.

User-facing impact: action panel on `/mes/work-orders`.
