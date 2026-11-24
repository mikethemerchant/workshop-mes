# DT-002 — Setup split: planned changeover vs changeover overrun

**Status:** ✅ Complete — 2026-10-22, verified by the PM (spec approved by Walker)
**Priority:** P1 · **Effort:** S (one Dev session; no restart)
**Depends on:** DT-001 **verified by the PM** (same module).
**Bug:** —

---

## Background

MES §5 (Walker): Setup time up to the product's scheduled changeover is **planned** (`Changeover`); the time beyond it is **unplanned** (`Changeover overrun`, derived in the MES, not a PLC code). "A Setup event splits at the scheduled changeover seconds." Test criterion: five Setup events per loop totalling **146 s** — **118 s** planned changeover and **two 14 s overruns, before jobs 102 and 104**. AI hook: "Which changeovers ran over, and by how much?"

| Job (Setup for) | Setup in sim | Scheduled changeover | Planned row | Overrun row |
|---|---|---|---|---|
| 101 | 18 s | 20 s | 18 s | — |
| 102 | 44 s | 30 s | 30 s | 14 s |
| 103 | 22 s | 24 s | 22 s | — |
| 104 | 34 s | 20 s | 20 s | 14 s |
| 105 | 28 s | 30 s | 28 s | — |

## Rules

1. **Scheduled changeover** = `product_master.scheduled_changeover_s` for the Setup segment's `product` (the product code changes at the start of Setup, so the segment's product is the job being set up). Read via the db layer; cache per product in the store (refresh on a cache miss).
2. **On `segment_close` with state 2** (duration `d`, start `s`, end `e`, changeover `c`):
   - Row 1: `class 'Changeover'`, `is_planned true`, `source 'plc'`, `machine_state 2`, `fault_code 0`, `start_ts s`, `end_ts s + min(d, c)`, `duration_s min(d, c)`.
   - If `d > c`: row 2: `class 'Changeover overrun'`, `is_planned false`, `source 'derived'`, `machine_state 2`, `fault_code 0`, `start_ts s + c`, `end_ts e`, `duration_s d − c`.
   - `product_code` = segment product; `work_order_id` = `mes.workorder.current()`'s WO **at the close** (the Setup close is the `run_start` sample; `_workorder` runs first and has just attributed the new run — so this is the order the Setup was for; None if unattributed).
   - Both rows in one transaction.
3. **Live tags during Setup** (refines DT-001 rule 5): while `t − s ≤ c` → `State "Planned"`, `Class "Changeover"`; once `t − s > c` → `State "Unplanned"`, `Class "Changeover overrun"`. `ActiveSince` stays the Setup start; `DurationSec` = `t − s`.
4. Unknown product (no master row) → treat `c = 0`: the whole Setup is overrun, log a warning once per product.
5. Same restart behaviour as DT-001 (rows only at close). INSERT only; never update or delete.

## What Already Exists (Do Not Rewrite)

- `mes.downtime` after DT-001 (fault rows, live tags, store `mes.downtime.v1`). Keep DT-001 rules; state-2 segments currently write nothing — this task adds rule 2.
- Schema check `((class = 'Changeover') = is_planned)` — row 1 planned, row 2 not; satisfies it.
- `product_master` seed: 101 20 s, 102 30 s, 103 24 s, 104 20 s, 105 30 s.

## What to Build

1. In `mes/downtime/code.py`: pure `split_setup(start, end, dur_s, changeover_s)` → list of row dicts; Setup tag refinement; db `changeover_s(product)`; insert both rows in one transaction.
2. Extend `ignition/tests/replay_downtime.py` (`python3 -I`), second loop:
   - **5** `Changeover` rows totalling **118 s** (18, 30, 22, 20, 28); **2** `Changeover overrun` rows of **14 s** each, on products **102 and 104**, `start_ts` = Setup start + changeover;
   - Setup totals per job = sim Setup lengths (146 s); WO on each Setup row = the order that ran next (second-loop 105: None);
   - tags switch Planned → Unplanned exactly after `c` seconds of Setup (fake writer);
   - unknown product → all overrun + one warning; DT-001 assertions unchanged; all earlier tests pass.

## Load and live check

1. **Stop** after tests: Dev Report; handoff: "Ready to load — waiting for Walker (Scan File System)."
2. After the scan: wait for the **next Setup to close** (one every ~3.5 min; if it is a 102 or 104 Setup, the overrun row appears). Paste the `m.downtime` lines and the new rows. Check: planned row = min(setup, changeover); overrun = rest (≈ 14 s × ~1.03 on 102/104). If the first Setup after the scan is 101/103/105, that is enough — the overrun is proven offline; do not wait for another.

## Acceptance Criteria

- [ ] Rules 1–5 exactly; both rows in one transaction; WO = order of the run that starts at the Setup close.
- [ ] Replay: 118 s planned, 2 × 14 s overruns on 102/104; tag switch at `c`; DT-001 and all earlier tests pass under `python3 -I`.
- [ ] Live: one Setup recorded correctly; no errors; no restart.
- [ ] No edits outside `mes/downtime` and the test file.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/downtime/code.py` | Setup split, tag refinement |
| `ignition/tests/replay_downtime.py` | Setup tests |
| `docs/tasks/task_DT-002_setup_split.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

## Dev Instructions

Spec: `docs/tasks/task_DT-002_setup_split.md`. In `mes.downtime`, split each closed Setup segment at the product's scheduled changeover into a planned `Changeover` row and, if longer, an unplanned `Changeover overrun` row (source `derived`), and switch the live `Downtime/*` tags from Planned to Unplanned once the changeover time is exceeded. Prove it offline (118 s planned, 14 s overruns on 102 and 104), **stop for Walker's Scan File System**, then check the next live Setup.

Hard constraints:
- **Start only after DT-001 is verified by the PM.** Edit only `mes/downtime/code.py` and the test.
- No container restarts. No schema change. Parameterised SQL; INSERT only; never update or delete. Tag writes only to `Downtime/*`. Publish nothing.
- Never stop, start, update or reimport the sim; never touch UNS Studio. Never read `[MQTT Engine]`, `4.0 Printing/…`, `Probe/…`.
- Never touch `ign-mes-demo`. Never print `.env` or any password. No commit, no push; stage nothing.

## Dev Report

**Status:** ✅ Done — ready for PM verification. Offline gates pass; Walker scanned at 14:05:07 CDT; the first live Setup after the scan (job 102, the overrun case) was recorded correctly.

### What was built

`ignition/projects/mes-2026/ignition/script-python/mes/downtime/code.py`
- `split_setup(start, end, dur_s, changeover_s)` (pure, rule 2): row 1 `Changeover` (`plc`, planned, state 2, fault 0, `start` → `start + min(d, c)`; ends exactly at `end` when `d ≤ c`); if `d > c`, row 2 `Changeover overrun` (`derived`, unplanned, `start + c` → `end`, `d − c`). Caller adds `product_code` and `work_order_id`.
- `_changeover(st, product, db, out)` (rules 1, 4): store cache `st['changeover']` (product → s), DB read on a miss; no `product_master` row → 0 s, not cached (re-read next time), warned once per product via `st['unknown']`.
- `_close_setup(...)`: on `segment_close` state 2 → split, `product_code` = segment product, `work_order_id` = `wo_now` (= `mes.workorder.current()` at the close tick, after `_workorder` has attributed the new run), `db.insert_events(rows)` = both rows in one transaction. A failed lookup or insert is logged and writes nothing.
- `segment_open` state 2 looks up the changeover once and keeps it on `st['segment']['changeover']`; `tag_values` (rule 3): `t − s ≤ c` → `Planned`/`Changeover`, `t − s > c` → `Unplanned`/`Changeover overrun`; `ActiveSince` = Setup start, `DurationSec` = `t − s`, `CurrentAlarm` 0.
- `_SqlDb`: `insert_event(r, tx=None)` (unchanged SQL, optional tx), `insert_events(rows)` (`beginTransaction` / commit / rollback on error / close — same pattern as `mes.schedule.atomic`), `changeover_s(product)` (parameterised `SELECT scheduled_changeover_s FROM mes.product_master WHERE product_code = ?`).
- Store: new keys `changeover`, `unknown`. `_store()` adds missing keys to an existing store with `setdefault` — the live `mes.downtime.v1` store was created by DT-001 and survives Scan File System; bumping the version would have dropped an open fault. `STORE_VERSION` stays 1.
- `on_tick`: lines prefixed `warning: ` are logged with `log.warn` (the unknown-product warning); everything else stays `log.info`.
- Header comment updated for DT-002.

`ignition/tests/replay_downtime.py`
- `EventDb`: fake `product_master` (DB-002 seed: 101 20, 102 30, 103 24, 104 20, 105 30), `changeover_s` (records lookups), `insert_events` (all-or-nothing, injectable failure), `faults()` / `setups()` filters.
- New "Setup split (loop 2)" section: 5 `Changeover` rows 18/30/22/20/28 = 118 s; 2 overruns 14 s on 102 and 104 with `start_ts` = Setup start + c; per-job Setup total = sim Setup length (146 s); rows tile each Setup; WO per Setup = WO-1006…WO-1009, 105 None (loop 1: WO-1001…WO-1005); overrun row has its planned row's WO; source/is_planned/state/fault per class; `product_master` read once per product; tag switch to `Unplanned`/`Changeover overrun` on the first sample past c (102 at 32 s, 104 at 22 s; 101/103/105 never).
- Tag check per state (`tags_ok`) now expects the Setup Planned → Unplanned switch at c.
- New edge cases 8–11: unknown product (all overrun + one warning + Unplanned tags), failed Setup transaction (no rows, logged, tags still follow), `split_setup` d < c / d = c / d > c, DT-001 store without the new keys upgraded in place with its open fault kept.

### Live check (after Walker's Scan File System, 14:05:07 CDT)

`docker logs --since 2026-10-22T19:05:00Z mes-workshop-2026-ignition-1 | grep -E "m\.downtime|m\.workorder|ERROR|Traceback"`:
```
14:05:07 Restarting gateway scripts... project=mes-2026 (x2)
14:05:19 [m.downtime] event 7 Web break fault=101 start=2026-10-22T19:05:09Z end=2026-10-22T19:05:18Z dur=8.3s product=101 wo=None
14:06:21 [m.downtime] event 8 Tunnel over-temp fault=102 start=2026-10-22T19:06:07Z end=2026-10-22T19:06:20Z dur=12.4s product=101 wo=None
14:07:07 [m.workorder] unattributed run product=102
14:07:07 [m.downtime] event 9 Changeover start=2026-10-22T19:06:20Z end=2026-10-22T19:06:50Z dur=30.0s changeover=30s product=102 wo=None
14:07:07 [m.downtime] event 10 Changeover overrun start=2026-10-22T19:06:50Z end=2026-10-22T19:07:06Z dur=16.0s changeover=30s product=102 wo=None
```
`grep -ciE "error|exception|traceback"` on the same log window → `0`.

`SELECT … FROM mes.downtime_event WHERE machine_state = 2 ORDER BY event_id`:
```
 event_id |          start_ts          |           end_ts           | duration_s | source  | machine_state | fault_code |       class        | is_planned | product_code | work_order_id
        9 | 2026-10-22 19:06:20.315+00 | 2026-10-22 19:06:50.315+00 |       30.0 | plc     |             2 |          0 | Changeover         | t          |          102 |
       10 | 2026-10-22 19:06:50.315+00 | 2026-10-22 19:07:06.333+00 |       16.0 | derived |             2 |          0 | Changeover overrun | f          |          102 |
```
Check: Setup 46.0 s (nominal 44 s, +4.5 %, ±2 s per event); planned = min(46, 30) = 30.0 s, ends at start + 30 s; overrun = 16.0 s (nominal 14 s) from start + 30 s to the Setup end; row 10 starts where row 9 ends; one insert tick (both rows logged at 14:07:07.778). `work_order_id` NULL because `mes.workorder` left this 102 run unattributed (its own log line at the same tick) — rule 2 "None if unattributed". Tags not read (the Dev cannot read live tags); this Setup opened after the scan, so its tags should have switched to Unplanned at ~30 s — Walker's Tag Browser glance if wanted.

### Files changed

- modified `ignition/projects/mes-2026/ignition/script-python/mes/downtime/code.py`
- modified `ignition/tests/replay_downtime.py`
- modified `docs/tasks/task_DT-002_setup_split.md` (Status line, this report)
- modified `docs/agent_handoff.md` (Dev status block)

### Gate results

All tests, `python3 -I`, from the repo root (PASS counted from output lines):
```
replay_downtime 53 PASS / 0 FAIL lines / All checks passed. / exit 0
replay_engine 23 PASS / 0 FAIL lines / PASSED: 0 failure(s) / exit 0
replay_schedule 68 PASS / 0 FAIL lines / PASSED: 0 failure(s) / exit 0
replay_workorder 48 PASS / 0 FAIL lines / PASSED: 0 failure(s) / exit 0
test_schedule_override 55 PASS / 0 FAIL lines / PASSED: 0 failure(s) / exit 0
test_ui_work_orders 23 PASS / 0 failure(s) / exit 0
test_workorder_actions 43 PASS / 0 FAIL lines / PASSED: 0 failure(s) / exit 0
```
(`test_workorder_actions` 43 / `test_schedule_override` 55 count only `  PASS` lines; `grep -c PASS` gives 44 / 56 because the summary line `PASSED: 0 failure(s)` matches too — the same totals as DT-001's report.)

`replay_downtime` row dump, loop 2 Setups (from the run above):
```
  23 Changeover         fault 0 start 1792001046000 dur  18.0 product 101 wo WO-1006
  27 Changeover         fault 0 start 1792001244000 dur  30.0 product 102 wo WO-1007
  28 Changeover overrun fault 0 start 1792001274000 dur  14.0 product 102 wo WO-1007
  32 Changeover         fault 0 start 1792001468000 dur  22.0 product 103 wo WO-1008
  36 Changeover         fault 0 start 1792001670000 dur  20.0 product 104 wo WO-1009
  37 Changeover overrun fault 0 start 1792001690000 dur  14.0 product 104 wo WO-1009
  41 Changeover         fault 0 start 1792001884000 dur  28.0 product 105 wo None
```

`docker compose config -q` → no output, `compose rc=0`.

Jython 2.7 compile (gateway JRE + `jython-ia-2.7.4.0.jar`, `-S`, script on stdin, compile only):
```
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/script-python/mes/downtime/code.py
rc=0
```
`code.py` is ASCII (no non-printable/non-ASCII bytes). No restart: `mes-workshop-2026-ignition-1` StartedAt `2026-10-22T17:00:29Z`, `mes-workshop-2026-db-1` `2026-10-22T15:43:40Z`. No DB writes, no tag writes, nothing staged, no commit.

### Deviations from spec

1. **DT-001 test assertions filtered to fault rows.** Several DT-001 checks counted *all* rows (`len(rows) == 30`, "every row: source plc, machine_state 3", edge cases 1–3, 5–7 `len(r) == 1`). Setup rows now exist, so these checks read `edb.faults()` instead of `edb.rows`; their expected values are unchanged. DT-001 edge case 4 ("Setup and Running segments: no rows") contradicts DT-002 and now asserts "Running: no rows; each Setup: one Changeover row".
2. **Failure handling not named in the spec:** a failed product_master lookup at the Setup *open* leaves the tags at Planned/Changeover (DT-001 behaviour) for that Setup; a failed lookup or a failed transaction at the *close* logs and writes no rows (never a wrong split). An unknown product is not cached, so adding its master row later takes effect on the next Setup without a reload.

### Open questions for the PM

Questions 1 and 2 answered by Walker (2026-10-22): the 0 s planned row is fine (cannot happen with the five seed products); Planned tags for a Setup already open at the scan are accepted.

1. **Unknown product → 0 s `Changeover` row.** Rule 2 makes row 1 unconditional, so with `c = 0` (rule 4) a zero-length planned row (`start = end`, 0.0 s) is written next to the full-length overrun. I followed the rule literally. If Walker prefers no 0 s row, it is a one-line change in `split_setup`.
2. **First Setup after the scan.** The live store has no `segment['changeover']` for a Setup already open at load time (the segment was opened by DT-001 code), so that one Setup's tags stay Planned to its end; its rows at close are still split correctly (the close does its own lookup). From the next Setup on, tags switch at c.
3. **Parallel OEE-001 run** in the same tree (`docs/tasks/task_OEE-001_oee_intervals.md` modified, `mes/oee/` untracked) — not touched by this task.

## PM Verification

**PM, 2026-10-22 — PASS.**

- [x] All eight test files pass under `python3 -I` (PM run before the scan); `replay_downtime.py` second loop 118 s planned + 2 × 14 s overruns on 102/104. DT-001 test edits (fault-only counts) accepted — Setups now write rows by design.
- [x] SQL: INSERT only on `downtime_event`; rows in one transaction.
- [x] Live (DB): Setup for 102 → event 9 `Changeover` 30.0 s (planned, plc) 19:06:20.315–19:06:50.315 + event 10 `Changeover overrun` 16.0 s (derived) 19:06:50.315–19:07:06.333; Setup for 103 → event 14 `Changeover` 22.7 s (< 24 s, no overrun). WO NULL — no Released orders left (correct).
- Not seen live: the tag switch Planned → Unplanned at `c` (offline only; Tag Browser glance during the next 102/104 Setup).
- Answers recorded: 0 s planned row for an unknown product kept; Setup already running at the scan keeps Planned tags.

User-facing impact: Setup split into planned changeover and overrun in `downtime_event`.
