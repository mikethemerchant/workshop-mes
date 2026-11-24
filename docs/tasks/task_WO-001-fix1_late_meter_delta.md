# WO-001-fix1 — Credit late meter deltas to the run that just closed (BUG-002)

**Status:** ✅ Complete — 2026-10-22, verified by the PM, approved by Walker ("assume its right")
**Priority:** P2 · **Effort:** S (one Dev session; no restart)
**Depends on:** WO-001 ✅
**Bug:** BUG-002

---

## Background

BUG-002: WO-1002 completed with 16 m reject instead of 18. The raw counter advanced 18 m between run 2's start counter and run 3's, so 2 m were seen on a Setup sample after `mes.workorder` closed the run at `product_change`, and no run received them. Jobs 102, 103 and 104 end on a web-break fault (reject +2 per sample), so each can lose one step; job 101 (ends on over-temp) and 105 (ends on RTO inhibit) cannot. The likely mechanism is that a meter tag's new value is read one tick after `MachineState`/`SampleTime` (separate MQTT messages); this fix does not depend on the mechanism.

**Rule that makes the fix safe:** in this sim the meter step is 0 throughout Setup (Sim Spec §5: counters "hold" in Setup). So any non-zero `dg`/`dr` on a Setup sample is late-arriving production of the job that just ended.

## What to Build

In `ignition/projects/mes-2026/ignition/script-python/mes/workorder/code.py`:

1. **Remember the run just closed.** In `_close()` (both attributed and unattributed paths), set `st['last_closed']` to that run's dict (with `run_id`, `work_order_id`, `attributed`, `good`, `reject`, `reset_seen`, `end`). For an unattributed run, `last_closed` is the same dict object that was appended to `st['unattributed']`, so a late credit shows there too.
2. **Rule 8 — late deltas.** In `handle()`, after event processing and before the open-run accumulation: if `st['run'] is None` and `sample['state'] == 2` and `st.get('last_closed')` and (`dg` or `dr` non-zero) → add them to `last_closed`; if it is attributed, `db.update_run(run_id, good, reject, reset_seen)` (absolute totals; `end_ts` and the order's status are not touched). Append the log line `late delta good=+<dg> reject=+<dr> credited to run <id> (<wo>)` (or `… to unattributed run product=<p>`).
   - This covers the `product_change` sample itself (the run is closed first by rule 4, then its deltas land here) and any later Setup sample.
3. **Forget it at the next run.** On `run_start` (before attributing), set `st['last_closed'] = None`. Also `None` on a `gap` event and after a rule-6 resume/close.
4. **Store compatibility:** `st.setdefault('last_closed', None)` in `_store()`; add it to `_new_store()`. No store version bump (the live open run must survive the scan).
5. Update the module's header comment / rule list to include rule 8. `_SqlDb` unchanged (`update_run` already exists).

Nothing else changes: rules 1–7 stay as they are.

### Tests

Extend `ignition/tests/replay_workorder.py` (keep every existing assertion unchanged):
- **Lag injection:** run the two-loop replay with the `good` and `reject` values delayed by one sample relative to `state`/`t`/`product` (sample *n* carries the meter values of sample *n − 1*). Assert per-run totals are still exactly **600, 375, 450, 525, 365, 600, 375, 450, 525** good and **18, 18, 18, 18, 83, 18, 18, 18, 18** reject, and that the second-loop 105 unattributed run still shows 365 / 83.
- Without lag: totals as before, and **no** `late delta` lines at all.
- A late delta after a gap or after the next `run_start` is **not** credited to the old run.
- `replay_engine.py` passes unchanged.

Make sure the test also runs under `python3 -I` (add the same `sys.path` line `replay_workorder.py` uses to `test_ui_work_orders.py` — a one-line fix noticed in WO-002's verification).

### Load and live check

1. **Stop** after the tests: Dev Report; `agent_handoff.md`: "Ready to load — waiting for Walker (Scan File System)."
2. After the scan: confirm the open run survived (log shows no `workorder store created` line; the open run keeps counting).
3. Watch until the next job that ends on a web break completes (103, 104 or 102), or report the first `late delta` line if it appears earlier. Paste the `m.workorder` lines and:
   ```sql
   select run_id, work_order_id, good_meters, reject_meters,
          lead(start_reject_counter) over (order by run_id) - start_reject_counter as reject_span
   from mes.work_order_run order by run_id;
   ```
   For runs closed after the fix: `reject_meters` = `reject_span`.
4. **Do not change existing rows.** (Walker, 2026-10-22: WO-1002 was corrected to 18 m by the PM with one guarded UPDATE — logged in BUG-002. Any other run closed before the fix loads stays as recorded.)

## Acceptance Criteria

- [ ] Rule 8 implemented as above; `last_closed` set in `_close()`, cleared on `run_start`, `gap` and after resume; store compatible (no version bump).
- [ ] Lag-injected replay gives the exact per-run totals; no-lag replay unchanged with zero `late delta` lines; existing assertions pass; `replay_engine.py` passes.
- [ ] All three test files pass under `python3 -I`.
- [ ] Live: open run survived the scan; a run closed after the fix has `reject_meters` equal to its counter span (or the Dev reports that no such run completed yet, with the `late delta` lines seen).
- [ ] No container restart, no schema change, no edits to historical rows.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/workorder/code.py` | Rule 8 |
| `ignition/tests/replay_workorder.py` | Lag-injection tests |
| `ignition/tests/test_ui_work_orders.py` | `sys.path` line for `-I` |
| `docs/tasks/task_WO-001-fix1_late_meter_delta.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

## Dev Instructions

Spec: `docs/tasks/task_WO-001-fix1_late_meter_delta.md`. Fix BUG-002: in `mes.workorder`, remember the run closed at `product_change` and credit any meter delta seen on a Setup sample (before the next run start or a gap) to it, writing absolute totals. Prove it with a lag-injected two-loop replay (exact per-run totals), **stop for Walker's Scan File System**, then confirm live that a newly closed run's rejects equal its counter span.

Hard constraints:
- Rules 1–7 unchanged; no store version bump; the live open run must survive the scan.
- No container restarts. No schema change. Do not edit existing database rows (WO-1002 stays 16 unless Walker says otherwise). Parameterised SQL only.
- Do not edit `mes/engine`, `mes/ui`, the WO-002 view, `press1`, VIS-001 views, tag files or `db/`.
- Never stop, start, update or reimport the sim; never touch UNS Studio. Never read `[MQTT Engine]`, `4.0 Printing/…`, `Probe/…`.
- Never touch `ign-mes-demo`. Never print `.env` or any password. No commit, no push; stage nothing.

## Dev Report

**Status:** ✅ Done — ready for PM verification. Code and offline tests done; Walker ran Scan File System (12:30:11 local). Live step 2 confirmed. Step 3: **no web-break job has closed since the fix yet** — Walker told the Dev not to wait (12:34 local); the next one is WO-1007 (job 102), due ≈12:40 local. No `late delta` line seen so far.
**Date:** 2026-10-22

### What was built

- `ignition/projects/mes-2026/ignition/script-python/mes/workorder/code.py`
  - Header rule list: added the rule 8 line (Setup sample, BUG-002; forgotten at run_start, gap, rule 6).
  - `_new_store()`: `'last_closed': None`. `_store()`: `st.setdefault('last_closed', None)` for the live store created before rule 8. `STORE_VERSION` stays 1, so the live open run survives the scan.
  - `_close()`: sets `run['end'] = t` and `st['last_closed'] = run` on both paths (the `end` assignment moved up from the unattributed branch so the attributed dict carries it too). For an unattributed run it is the same dict object that is appended to `st['unattributed']`.
  - `_resume()`: `st['last_closed'] = None` after resuming/closing (rule 6).
  - `handle()`: `gap` added to the event order dict at `-1` (processed first) → `last_closed = None`. `run_start` with no open run → `last_closed = None` before `_attribute()`. With no run open after the events, the new `_late_delta()` runs.
  - `_late_delta()` (new): if `last_closed` is set, `state == 2` and `dg` or `dr` non-zero → adds them to `last_closed`; attributed → `db.update_run(run_id, good, reject, reset_seen)` with absolute totals (and `saved` updated); logs `late delta good=+<dg> reject=+<dr> credited to run <id> (<wo>)` or `… credited to unattributed run product=<p>`. `end_ts` and the order status are not touched. `_SqlDb` unchanged.
  - Rules 1–7 otherwise unchanged.
- `ignition/tests/replay_workorder.py` (all existing assertions unchanged)
  - No-lag two-loop replay: new check "no `late delta` lines".
  - New section "Two loops, meters one sample late": `lagged()` gives sample *n* the `good`/`reject` of sample *n − 1*; asserts attribution order, exact per-run good/reject, `end_ts`/`completed_at` unchanged, second-loop 105 unattributed run 365/83, and that `late delta` lines were logged.
  - Edge cases 7–9: late delta credited on the `product_change` sample but **not** after a 30 s gap; a delta on/after the next `run_start` goes to the new run and run 1 keeps its late +8 with `end_ts` unchanged; unattributed run gets the late delta in memory with no db write.
  - Docstring updated ("nine edge cases", lag replay).
- `ignition/tests/test_ui_work_orders.py`: `from pathlib import Path` + the same `sys.path.insert(...)` line as `replay_workorder.py`, so it runs under `python3 -I`.

### Files changed

| Path | Change |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/workorder/code.py` | modified |
| `ignition/tests/replay_workorder.py` | modified |
| `ignition/tests/test_ui_work_orders.py` | modified |
| `docs/tasks/task_WO-001-fix1_late_meter_delta.md` | modified (Status line, Dev Report) |
| `docs/agent_handoff.md` | modified (Dev status block only) |

### Gate results

```
$ python3 -I ignition/tests/replay_engine.py        -> exit 0   PASSED: 0 failure(s)
$ python3 -I ignition/tests/replay_workorder.py     -> exit 0   PASSED: 0 failure(s)
$ python3 -I ignition/tests/test_ui_work_orders.py  -> exit 0   0 failure(s)
$ docker compose config -q                          -> exit 0
```

`replay_workorder.py` new output (excerpt):
```
  PASS  no lag: no 'late delta' lines

Two loops, meters one sample late:
  PASS  lag: runs attributed in order WO-1001 .. WO-1009
  PASS  lag: good meters per run 600, 375, 450, 525, 365, 600, 375, 450, 525 ([600, 375, 450, 525, 365, 600, 375, 450, 525])
  PASS  lag: reject meters per run 18, 18, 18, 18, 83, 18, 18, 18, 18 ([18, 18, 18, 18, 83, 18, 18, 18, 18])
  PASS  lag: end_ts and completed_at unchanged by late deltas
  PASS  lag: second-loop 105 run unattributed, in memory good 365 reject 83 ((365.0, 83.0))
  PASS  lag: 'late delta' lines logged
...
  PASS  late delta: credited on the product_change sample, not after a gap (40/2)
  PASS  late delta after a gap: one 'late delta' line, last_closed cleared
  PASS  late delta: run 1 good 40 + 8 late = 48, end_ts unchanged (48)
  PASS  after run_start: deltas go to run 2 (10/2)
  PASS  after run_start: last_closed cleared; log 'late delta good=+8 reject=+0 credited to run 1 (WO-1001)'
  PASS  late delta, unattributed: credited in memory, no db write
```
Late-delta lines in the lag replay — exactly the web-break jobs, as BUG-002 predicts:
```
  late delta good=+0 reject=+2 credited to run 2 (WO-1002)
  late delta good=+0 reject=+2 credited to run 3 (WO-1003)
  late delta good=+0 reject=+2 credited to run 4 (WO-1004)
  late delta good=+0 reject=+2 credited to run 7 (WO-1007)
  late delta good=+0 reject=+2 credited to run 8 (WO-1008)
  late delta good=+0 reject=+2 credited to run 9 (WO-1009)
```
**Negative control:** the same test file run against the pre-fix `code.py` (`git show HEAD:…`, throwaway copy in the scratchpad, deleted) reproduces BUG-002 exactly:
```
  FAIL  lag: reject meters per run 18, 18, 18, 18, 83, 18, 18, 18, 18 ([18, 16, 16, 16, 83, 18, 16, 16, 16])
```

Jython 2.7 parse, same method as WO-002 (gateway's `lib/core/common/jython-ia-2.7.4.0.jar` with `-S` through the gateway JRE, throwaway `/tmp/wofix1_check.py` in the container, deleted; compile only, nothing loaded):
```
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/script-python/mes/workorder/code.py
```
The container already sees the new file on the mount (`grep -c _late_delta` → 2); it is not loaded until the scan.

No restart: `mes-workshop-2026-ignition-1` StartedAt `2026-10-22T17:00:29Z`, `mes-workshop-2026-db-1` `2026-10-22T15:43:40Z` (unchanged). No DB writes, no schema change, no commit, nothing staged.

### Live check (after the scan)

Gateway log is `docker logs` (`wrapper.log` is a symlink to `/dev/stdout`). `docker logs --since 15m mes-workshop-2026-ignition-1 | grep -E "m.workorder|Restarting gateway scripts"`, at 12:34:36 CDT:
```
12:22:03 I [Project    ] Restarting gateway scripts... project=mes-2026, collection=mes-2026
12:22:31 I [m.workorder] complete WO-1002 good=375 planned=375 diff=0 reject=16
12:22:55 I [m.workorder] run 3 attributed to WO-1003 product=103
12:25:59 I [m.workorder] complete WO-1003 good=444 planned=450 diff=-6 reject=16
12:26:33 I [m.workorder] run 4 attributed to WO-1004 product=104
12:29:40 I [m.workorder] complete WO-1004 good=525 planned=525 diff=0 reject=16
12:30:08 I [m.workorder] run 5 attributed to WO-1005 product=105
12:30:11 I [Project    ] Restarting gateway scripts... project=mes-2026, collection=mes-2026   <- Walker's scan (this fix)
12:33:11 I [m.workorder] complete WO-1005 good=365 planned=365 diff=0 reject=83
12:33:31 I [m.workorder] run 6 attributed to WO-1006 product=101
```
- **Step 2 — open run survived:** there is no `workorder store created` line after the 12:30:11 scan, run 5 (WO-1005), which was open across the scan, kept counting and closed at 365/83. No `handle failed` lines.
- **Step 3:** WO-1005 (job 105, ends on an RTO inhibit, so no late step expected) is the only run closed after the fix, and it matches its span. No web-break job (102/103/104) has closed since the fix, so the fix's live effect is **not yet observed**. WO-1007 (job 102) is the next one, ≈12:40 local. Check: its `reject_meters` = `reject_span`, and/or a `late delta … credited to run 7 (WO-1007)` line.

`docker exec mes-workshop-2026-db-1 psql -U mes -d mes` (spec query plus `good_span`), 12:3x CDT:
```
 run_id | work_order_id | good_meters | reject_meters | reject_span | good_span
--------+---------------+-------------+---------------+-------------+-----------
      1 | WO-1001       |         600 |            18 |          18 |       600
      2 | WO-1002       |         375 |            18 |          18 |       375
      3 | WO-1003       |         444 |            16 |          18 |       444
      4 | WO-1004       |         525 |            16 |          18 |       525
      5 | WO-1005       |         365 |            83 |          83 |       365
      6 | WO-1006       |         112 |             0 |             |
```
Runs 3 and 4 closed before the fix and show the BUG-002 loss (16 vs span 18), as predicted. Historical rows were not edited by this run.

No restart: `mes-workshop-2026-ignition-1` still StartedAt `2026-10-22T17:00:29Z`.

### Deviations from spec

- `run['end'] = t` is now set for attributed runs too (it was only set on unattributed runs), because the spec lists `end` among the `last_closed` fields. In-memory only; nothing reads it for attributed runs.
- `gap` is handled inside `handle()`'s event loop with sort key `-1` (before `restart`), so a gap and a `product_change` on the same sample clear first and then set `last_closed` — see open question 1.

### Open questions for the PM

1. **Gap and product_change on the same sample.** If a sampling gap ends on a sample that is also a product change, the run is closed there and that sample's `dg`/`dr` (which spans the gap) is credited to it by rule 8. Before this fix the same delta would have gone nowhere. The spec's rule ("None on a gap event") doesn't say which wins; I left it as is. If the PM wants a gap-spanning delta never credited, it is a one-line guard in `_late_delta()` (skip when the sample carries a `gap` event).
2. **WO-1002 reject is now 18 in the database, not 16.** The gateway logged `complete WO-1002 … reject=16` at 12:22:31 and BUG-002 records 16, but the row now reads `reject_meters = 18`. This Dev run did not write it (no DB writes; the fix only credits `last_closed`, which was run 5 at the scan, never run 2). Someone edited the row outside this run. The spec says WO-1002 stays 16 unless Walker says otherwise — PM to confirm with Walker who changed it.
3. **WO-1003 good 444 vs planned 450 is not an MES loss:** `good_span` (counter between run 3's and run 4's start) is also 444, so the raw counter only advanced 444 m. Out of scope; noting it in case it looks like BUG-002 in the good meters.
4. **Live confirmation of a web-break run is still outstanding** (see Live check). The PM can run the query above after ≈12:40 local, when WO-1007 closes.

## PM Verification

**PM, 2026-10-22 — PASS** (Walker: "we are on the clock, assume its right" — closed before the first live web-break close after the fix, WO-1007 ~12:41 CDT).

- [x] Diff read: `last_closed` set in `_close()` (attributed and unattributed), cleared on `run_start`, `gap` and after rule 6; `_late_delta` only when no run is open, state 2, non-zero delta; absolute `update_run`; `end_ts`/status untouched; `setdefault` in `_store()`, no version bump. Rules 1–7 unchanged.
- [x] PM ran all three tests under `python3 -I`: 0 failures each. Lag-injected replay: six `late delta` lines (+2 reject to runs 2, 3, 4, 7, 8, 9), exact 600/375/450/525/365/600/375/450/525 and 18/18/18/18/83/18/18/18/18; no-lag replay logs none.
- [x] Live: scan at 12:30:11 kept the store (WO-1005 closed 365/83; WO-1006 closed 584/18, both equal to their raw counter spans). Not yet seen live: a late delta credited (first chance WO-1007 at ~12:41).
- **Dev's flags answered:** WO-1002 at 18 was the PM's correction on Walker's instruction (BUG-002). WO-1003 444 and WO-1006 584 good are what the raw counter moved — the live sim runs ~3 % slow, so a time-based phase sometimes fits one sample fewer; MES recorded it correctly.
- Data corrections on Walker's instruction (BUG-002): runs 2, 3, 4 reject 16 → 18, each guarded on the raw counter span = 18.

User-facing impact: reject meters now match the raw counter at job ends. Docs: bug log, handoff traps.
