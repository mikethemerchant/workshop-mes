# DT-001 — Downtime events from the PLC: fault classes, records and live tags

**Status:** ✅ Complete — 2026-10-22, verified by the PM (spec approved by Walker)
**Priority:** P0 · **Effort:** M (one Dev session; no restart)
**Depends on:** ENG-001 ✅, WO-001 ✅, UNS-004 ✅. Can run in parallel with SCHED-003 (no shared files). **DT-002 runs after this** (same module).
**Bug:** —

---

## Background

First Hour 3 function (MES §5). Every stop on Press 1 becomes an event with start, end, duration, class, product code and work order, and planned and unplanned time stay apart. DT-001 covers the **unplanned fault events** (state 3) and the live `Downtime/*` tags; DT-002 adds the Setup split (planned changeover vs overrun). Detection already exists: `mes.engine` emits debounced, backdated `segment_open` / `segment_close` events with durations from sample timestamps (MES §5 timing rules, ENG-001).

**Walker's ruling (2026-10-22):** the open event lives in the gateway, not the database — a gateway restart loses only the open event. So **a `downtime_event` row is written once, when the event closes**, complete. No open rows, no orphans after a restart.

## Rules

1. **Class from the fault code** (MES §5, state 3): `101` → `Web break`, `102` → `Tunnel over-temp`, `103` → `RTO inhibit`, any other code → `Unclassified alarm` (never dropped). `source = 'plc'`, `machine_state = 3`, `is_planned = false`.
2. **On `segment_open` with state 3:** remember the open event in the module store: `start`, `fault`, `class`, `product` (from the segment), and `work_order_id` = the WO of `mes.workorder.current()` **at this moment** (None if no run or unattributed). Write the live tags (rule 5).
3. **On `segment_close` with state 3:** `INSERT INTO mes.downtime_event (start_ts, end_ts, duration_s, source, machine_state, fault_code, class, is_planned, product_code, work_order_id)` with the segment's `start`, `end`, `dur_s` and the remembered fields. A fault-code change inside Faulted is a close + open in the engine, so it becomes two rows. Clear the open event.
4. **RTO cross-check (log only):** on a 103 event, if the sample's `rto` is not `true` at open, log `RTO inhibit without PLC/RTOInhibit` (MES §5 says it can be cross-checked; no extra column).
5. **Live tags** (UNS-004 memory tags, `system.tag.writeBlocking`, I/O layer only; write a tag only when its value changed, except `DurationSec`):
   - state 1 → `Downtime/State` `"Running"`, `CurrentAlarm` `0`, `Class` `""`, `ActiveSince` null, `DurationSec` `0.0`.
   - state 3 (open fault) → `State` `"Unplanned"`, `CurrentAlarm` = fault code, `Class` = rule-1 class, `ActiveSince` = start, `DurationSec` = sample `t` − start (s), every tick.
   - state 2 (Setup) → `State` `"Planned"`, `CurrentAlarm` `0`, `Class` `"Changeover"`, `ActiveSince` = Setup start, `DurationSec` every tick. (DT-002 refines this to the overrun.)
   - Before the first debounced segment (2 samples), leave the tags as they are.
6. **State 2 segments are ignored for rows in DT-001** (DT-002 writes them). State 1 segments write nothing.
7. **After a gateway restart / library reload:** the module store starts empty; the engine re-opens its current segment at the first samples it sees; DT-001 follows the engine (the event that was open is recorded from the new backdated start — the documented loss). Log `downtime store created`.
8. Parameterised SQL, `database='mes'`; `INSERT` into `mes.downtime_event` only. Never update or delete rows.

## What Already Exists (Do Not Rewrite)

- `mes.engine` events: `segment_open` / `segment_close` with `state`, `fault`, `product`, `start`, `end`, `dur_s`; sample carries `rto`, `t`. `HANDLERS = [_workorder, _schedule]`. **Only change:** append `_downtime` (after `_schedule`).
- `mes.workorder.current()` → open run dict (`work_order_id` or None). Read only.
- Schema: `downtime_event` (`source` plc/derived/operator, `machine_state` 1–3, `class` check list, `check ((class = 'Changeover') = is_planned)`); view `v_downtime_by_class`. No schema change.
- Memory tags `Downtime/State|CurrentAlarm|Class|ActiveSince|DurationSec` (UNS-004).
- Tests: `simreplay.py` and the existing replays. SCHED-003 may be editing `mes/schedule` and the Schedule view in parallel — **do not touch those**.

## What to Build

1. Library `ignition/projects/mes-2026/ignition/script-python/mes/downtime/code.py` + `resource.json`, same conventions as `mes.workorder` / `mes.schedule` (pure `handle(st, sample, events, db, wo_now)` returning `(log_lines, tag_values)`; `_SqlDb`; tag writer in I/O only; Jython/Python 3). Store `mes.downtime.v1`.
2. Register in `mes/engine/code.py`: `def _downtime(sample, events): mes.downtime.on_tick(sample, events)` and `HANDLERS = [_workorder, _schedule, _downtime]`.
3. Test `ignition/tests/replay_downtime.py` (`python3 -I`): two-loop nominal replay through engine → workorder → downtime with a FakeDb; assert for the **second loop**:
   - fault rows: **15** — Web break × 9 (total **82 s**), Tunnel over-temp × 4 (**48 s**), RTO inhibit × 2 (**24 s**); each `duration_s` equals its phase length; `product_code` and `work_order_id` match the job (second-loop 105 events: `work_order_id` None — unattributed);
   - RTO inhibit events on jobs **102 and 105** (MES §5 AI hook);
   - injected fault code `999` → `Unclassified alarm`; a 101 → 102 change inside Faulted → two rows;
   - tag values per state as rule 5 (fake writer); no rows for state 1 or 2;
   - empty store mid-fault (restart) → no orphan, event recorded from the new start;
   - all earlier tests pass.

## Load and live check

1. **Stop** after tests: Dev Report; handoff: "Ready to load — waiting for Walker (Scan File System)."
2. After the scan: watch the log until the **first fault closes** (faults come every ~1 min). Paste the `m.downtime` lines and
   `select event_id, start_ts, end_ts, duration_s, class, fault_code, is_planned, product_code, work_order_id from mes.downtime_event order by event_id;`
   Check: class matches the code, duration ≈ phase length × ~1.03 (± 2 s), WO = the running order. **Do not wait for a full loop.**
3. Ask Walker to glance at `Downtime/*` in the Tag Browser during a fault (State Unplanned, DurationSec climbing).

## Acceptance Criteria

- [ ] Rules 1–8 exactly; rows only at close; WO captured at open; tags per rule 5.
- [ ] Handler appended after `_schedule`; engine otherwise unchanged.
- [ ] `replay_downtime.py` passes (15 events, 82/48/24 s, RTO on 102/105, edge cases) and all earlier tests pass under `python3 -I`.
- [ ] Live: first closed fault recorded correctly; no errors; no restart.
- [ ] No edits to `mes/workorder`, `mes/schedule`, `mes/ui`, views, `press1`, tag files, `db/`.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/downtime/code.py`, `resource.json` | New |
| `ignition/projects/mes-2026/ignition/script-python/mes/engine/code.py` | Append `_downtime` |
| `ignition/tests/replay_downtime.py` | New |
| `docs/tasks/task_DT-001_downtime_events.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block (DT-001 rows added beside SCHED-003's; see open question 3) |

## Dev Instructions

Spec: `docs/tasks/task_DT-001_downtime_events.md`. Build `mes.downtime` as the third engine handler: classify fault segments (101/102/103/other), write one complete `downtime_event` row when each fault closes (WO captured at the start), and keep the `Downtime/*` memory tags live for Running, Setup and Faulted. Prove it with a two-loop replay (15 faults, 82/48/24 s), **stop for Walker's Scan File System**, then check the first live fault.

Hard constraints:
- May run in parallel with SCHED-003: do not edit `mes/schedule`, the Schedule view, or `mes/workorder`. Engine: append the handler only.
- No container restarts. No schema change. Parameterised SQL; INSERT only into `downtime_event`; never update or delete. Tag writes only to the five `Downtime/*` tags. Publish nothing.
- Never stop, start, update or reimport the sim; never touch UNS Studio. Never read `[MQTT Engine]`, `4.0 Printing/…`, `Probe/…`.
- Never touch `ign-mes-demo`. Never print `.env` or any password. No commit, no push; stage nothing.
- Anything this spec does not cover: stop and ask in the Dev Report.

## Dev Report

**Status:** ✅ Done — ready for PM verification. Loaded by Walker's Scan File System (13:54:06 CDT); the first live fault was recorded correctly (see *Live check*). Still to do: Walker's `Downtime/*` glance in the Tag Browser (step 3).
**Date:** 2026-10-22

Note: the spec's status line read "📝 Specced — awaiting Walker's approval" when this run started. Walker launched `/dev` on it himself, so I took that as approval.

### What was built

- **`mes/downtime/code.py`** (new), same shape as `mes.workorder` / `mes.schedule`:
  - Store `mes.downtime.v1` in `getGlobals()`: `segment` (the engine's open segment, any state; drives the tags), `fault` (the open fault event: `start`, `fault`, `class`, `product`, `work_order_id`), `written` (last value written per tag), `warned`. `_store()` logs `downtime store created mes.downtime.v1` (rule 7).
  - `classify(fault)`: 101 → Web break, 102 → Tunnel over-temp, 103 → RTO inhibit, anything else → Unclassified alarm (rule 1).
  - `handle(st, sample, events, db, wo_now)` → `(log_lines, tag_values)`, pure except `db.insert_event`. Events are processed in engine order. `segment_open` state 3 remembers the fault, with `wo_now` as the work order (rule 2), and logs `fault open …`. For 103 it logs `RTO inhibit without PLC/RTOInhibit (rto=…)` when the sample's `rto` is not `True` (rule 4). `segment_close` state 3 inserts one complete row (`source 'plc'`, `machine_state 3`, `is_planned false`, the segment's `start`/`end`/`dur_s`, and the remembered fault, class, product and WO), clears the open fault and logs `event <id> <class> fault=… start=… end=… dur=…s product=… wo=…` (rule 3). State 1/2 segments write no rows (rule 6). An insert failure is caught and logged (`insert failed …`); the tags still update.
  - `tag_values(seg, fault, t)` is rule 5: Running / Unplanned / Planned, with ActiveSince in epoch ms and DurationSec = (t − segment start)/1000. When no segment is open (before the first debounce, and on the first sample after each close), it returns None and the tags are left as they are. `handle` returns only the tags whose value changed, plus DurationSec on every tick while a segment is open.
  - `_SqlDb.insert_event`: one parameterised `INSERT … RETURNING event_id` on `mes.downtime_event`, connection `mes`, with `duration_s` rounded to 0.1. No UPDATE or DELETE anywhere.
  - `_write_tags` (I/O only): `system.tag.writeBlocking` on `mes.engine.ROOT + '/Downtime/<name>'`, only for the five tags; ActiveSince is converted with `system.date.fromMillis`. If any write quality is bad, it empties `written` so everything is rewritten next tick, and logs one warning until a write succeeds.
  - `on_tick(sample, events)`: `wo_now` = `mes.workorder.current()['work_order_id']` (None if no run or unattributed), then `handle`, log the lines, then write the tags (in a separate try, so a tag error can't swallow the log lines).
  - Read API: `open_fault()` returns a copy.
- **`mes/downtime/resource.json`** (new): same as the other libraries.
- **`mes/engine/code.py`**: added `_downtime` and `HANDLERS = [_workorder, _schedule, _downtime]`. Nothing else changed:
  ```
  +def _downtime(sample, events):
  +	mes.downtime.on_tick(sample, events)
  +
  -HANDLERS = [_workorder, _schedule]
  +HANDLERS = [_workorder, _schedule, _downtime]
  ```
- **`ignition/tests/replay_downtime.py`** (new, `python3 -I`): engine → workorder → downtime per sample against the WO-001 `FakeDb` (`simreplay.py`), a fake `downtime_event` table (enforces the schema checks: class list, `(class = 'Changeover') = is_planned`, `end ≥ start`, fault_code not null), and a fake tag table. It runs two nominal loops plus the first two loop-3 samples, then 7 edge cases.

### Files changed

| Path | |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/downtime/code.py` | created |
| `ignition/projects/mes-2026/ignition/script-python/mes/downtime/resource.json` | created |
| `ignition/projects/mes-2026/ignition/script-python/mes/engine/code.py` | modified (handler appended) |
| `ignition/tests/replay_downtime.py` | created |
| `docs/tasks/task_DT-001_downtime_events.md` | Status line + Dev Report |
| `docs/agent_handoff.md` | Dev status block |

Not touched: `mes/workorder`, `mes/schedule`, `mes/ui`, views, `press1`, tag files, `db/`, `simreplay.py`.

### Gate results

`python3 -I ignition/tests/replay_downtime.py` → exit 0, 33 PASS:
```
Two loops:
  PASS  15 fault rows per loop (loop 1 15, loop 2 15)
  PASS  loop 2: Web break x 9, total 82 s (9, 82 s)
  PASS  loop 2: Tunnel over-temp x 4, total 48 s (4, 48 s)
  PASS  loop 2: RTO inhibit x 2, total 24 s (2, 24 s)
  PASS  loop 2: each row's start, class, fault code, product, duration = phase length, work order
  PASS  loop 2: job 105 faults unattributed (work_order_id None)
  PASS  loop 1: job 105 faults on WO-1005
  PASS  RTO inhibit on jobs 102 and 105
  PASS  every row: source plc, machine_state 3, is_planned false
  PASS  no RTO cross-check warning on the nominal loops
  PASS  rows written at close: end_ts = the first sample after the fault
  PASS  tags per state (Running / Unplanned / Planned) on every sample after the debounce []
  PASS  first sample of each phase (segment closed, next not open): no tag write
  PASS  DurationSec written every tick while a segment is open
  PASS  Running: State etc. written once per Running segment (30), then only DurationSec
  PASS  loop boundary: first sample of the next phase leaves the tags as they were

Edge cases:
  PASS  fault 999 -> one Unclassified alarm row, 10 s ([('Unclassified alarm', 999, 10.0)])
  PASS  fault 999: work order of the open run (WO-1001)
  PASS  back to Running: tags reset
  PASS  101 -> 102 inside Faulted: two rows, Web break 8 s then Tunnel over-temp 12 s ([('Web break', 8.0), ('Tunnel over-temp', 12.0)])
  PASS  the second row starts where the first ends
  PASS  tags follow the new fault code ({'State': 'Unplanned', 'CurrentAlarm': 102, 'Class': 'Tunnel over-temp', 'ActiveSince': 1792000024000, 'DurationSec': 2.0})
  PASS  103 with rto false: 'RTO inhibit without PLC/RTOInhibit' logged
  PASS  103 with rto false: RTO inhibit row written
  PASS  Setup and Running segments: no rows
  PASS  restart: fault open, no row yet
  PASS  restart: one row for the fault, no orphan (1)
  PASS  restart: row from the new backdated start (first sample after the restart), 12 s
  PASS  restart: class Web break
  PASS  store created mid-fault: row from the engine's start, work order unknown (None)
  PASS  store created mid-fault: logged
  PASS  insert failure: logged
  PASS  insert failure: tags still follow the press

All checks passed.
```
Loop-2 rows in the replay: WO-1006 (101): Web break 10, Web break 8, Tunnel 12 · WO-1007 (102): Web break 10, RTO 12, Web break 8 · WO-1008 (103): Tunnel 12, Web break 10, Web break 8 · WO-1009 (104): Web break 10, Tunnel 12, Web break 8 · None (105): Tunnel 12, Web break 10, RTO 12.

Earlier tests, `python3 -I` (final run, after SCHED-003's session finished its edits in the same tree):
```
replay_engine 24 PASS / PASSED: 0 failure(s) / exit 0
replay_workorder 49 PASS / PASSED: 0 failure(s) / exit 0
replay_schedule 69 PASS / PASSED: 0 failure(s) / exit 0
test_ui_work_orders 23 PASS / 0 failure(s) / exit 0
test_workorder_actions 44 PASS / PASSED: 0 failure(s) / exit 0
test_schedule_override 56 PASS / PASSED: 0 failure(s) / exit 0
replay_downtime 33 PASS / All checks passed. / exit 0
```
An earlier run, while SCHED-003 was mid-edit, gave `replay_schedule` 1 FAIL (`shape_schedule: 15 columns in order`). On the committed HEAD tree with this engine change (`git archive HEAD` into the scratchpad) it passed, and it passes on the final run above. DT-001 had no part in it.

`docker compose config -q` → exit 0, no output.

Jython 2.7 parse (gateway JRE + `jython-ia-2.7.4.0.jar`, `-S`, script piped on stdin, no temp file; compile only):
```
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/script-python/mes/downtime/code.py
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/script-python/mes/engine/code.py
rc=0
```
Both `.py` files are ASCII (`grep -P '[^\x00-\x7F]'` finds nothing). No restart: `mes-workshop-2026-ignition-1` StartedAt `2026-10-22T17:00:29Z`, `mes-workshop-2026-db-1` `2026-10-22T15:43:40Z`. No DB writes, no tag writes, nothing staged, no commit.

### Live check (after Walker's Scan File System, 13:54:06 CDT)

`docker logs --since 15m mes-workshop-2026-ignition-1 | grep -E "m\.downtime|Restarting gateway scripts"`:
```
2026/10/22 13:54:06 | I [Project ] Restarting gateway scripts... project=mes-2026, collection=mes-2026
2026/10/22 13:54:06 | I [Project ] Restarting gateway scripts... project=mes-2026, collection=mes-2026
2026/10/22 13:54:08 | I [m.downtime] downtime store created mes.downtime.v1
2026/10/22 13:54:30 | I [m.downtime] fault open Web break fault=101 start=2026-10-22T18:54:26Z product=103 wo=None
2026/10/22 13:54:38 | I [m.downtime] event 1 Web break fault=101 start=2026-10-22T18:54:26Z end=2026-10-22T18:54:36Z dur=10.4s product=103 wo=None
```
`select event_id, start_ts, end_ts, duration_s, class, fault_code, is_planned, product_code, work_order_id from mes.downtime_event order by event_id;`
```
 event_id |          start_ts          |           end_ts           | duration_s |   class   | fault_code | is_planned | product_code | work_order_id
----------+----------------------------+----------------------------+------------+-----------+------------+------------+--------------+---------------
        1 | 2026-10-22 18:54:26.417+00 | 2026-10-22 18:54:36.811+00 |       10.4 | Web break |        101 | f          |          103 |
```
Checks:
- **Class matches the code:** 101 → Web break ✓. This is J103 Fault 2 (101 web break, 10 s).
- **Duration:** 10.4 s against 10 s × ~1.03 = 10.3 s ± 2 ✓.
- **Work order:** NULL is correct. The 103 run that is open now is unattributed (`m.workorder` 13:52:34: `unattributed run product=103`), because both seeded 103 orders (WO-1003, WO-1008) are already Complete. The last attributed run is run 9 (WO-1009, closed 17:47:28Z). This is the open fault's own start (opened after the store was created, logged `fault open`), not the open-question-1 case.
- **Errors:** no ERROR/WARN lines and no `failed` / `Traceback` in the gateway log since 18:54:00Z.
- **No restart:** `mes-workshop-2026-ignition-1` StartedAt `2026-10-22T17:00:29Z`, `mes-workshop-2026-db-1` `2026-10-22T15:43:40Z` (unchanged).

I can't read live tag values (no unauthenticated gateway API), so the `Downtime/*` check (State Unplanned, DurationSec climbing during a fault) is Walker's Tag Browser glance, which is still pending.

### Deviations from spec

1. **Two loops plus two samples.** The last loop-2 fault (J105 RTO inhibit) only closes on the next sample, so the replay also feeds the first two loop-3 samples. Without them, loop 2 would show 14 rows and RTO × 1.
2. **DurationSec in state 1** is written every tick (`0.0`), reading rule 5's "except DurationSec" literally. If unchanged writes are unwanted, it is a one-line change (`ALWAYS_WRITTEN`).
3. **The tags are left as they are on the first sample after every close**, not only before the very first segment. On that sample the engine has closed the old segment, and the new one isn't open until the debounce sample, so for one tick (~2 s) the tags still show the previous state. I read rule 5's last bullet as applying whenever no segment is open.

### Open questions for the PM

1. **The first load comes mid-segment (not covered by the spec).** After Scan File System, the engine store survives but `mes.downtime.v1` is new. If the press is Faulted at that moment, the fault's `segment_open` already went by, so the store has no remembered fault. What I did: on its close the row is still written, from the segment's own start/end/fault/product (class from the code), with **`work_order_id` None**, and the log line `fault closed with no open event in the store: work order unknown`. A fault is never dropped. Until the next segment opens, the tags stay as they are. This only happens if the scan lands during a fault or Setup. Alternative if wanted: when the store is created, adopt `mes.engine.open_segment()` and `mes.workorder.current()`. That would be a spec change, so I didn't do it.
2. **Live check done** (see *Live check*). Only Walker's `Downtime/*` Tag Browser glance is outstanding. Also: every seeded order is now consumed, so live rows will carry `work_order_id` NULL until new orders are Released. That is correct behaviour, but the PM may want to note it for the Hour 3 demo.
3. **The handoff's Dev status block was shared.** SCHED-003's Dev, running at the same time, wrote its status there while I worked. I added a DT-001 table under it rather than overwriting it, so the PM sees both runs.

## PM Verification

**PM, 2026-10-22 — PASS.**

- [x] `replay_downtime.py` (PM, `-I`): all checks passed (33 PASS); every earlier test passes.
- [x] Code: `CLASSES = {101 Web break, 102 Tunnel over-temp, 103 RTO inhibit}`, else `Unclassified alarm`; WO captured at `segment_open` via `mes.workorder.current()`; one INSERT per closed fault, nothing else written; tags via `writeBlocking`. Engine change = `_downtime` appended only.
- [x] Live (scan 13:54:06): `downtime store created`; event 1 Web break 101, 10.4 s, product 103; event 2 Web break 8.3 s — phase lengths 10 s / 8 s × ~1.03. `work_order_id` NULL: the 103 run is unattributed (no Released 103 order left) — correct. No errors; no restart.
- Not yet seen live: a 102/103 fault and the Tag Browser view — proven offline.

User-facing impact: `mes.downtime_event` fills with faults; `Downtime/*` tags live.
