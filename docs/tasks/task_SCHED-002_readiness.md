# SCHED-002 — Job readiness entry and the "started without readiness" flag

**Status:** ✅ Complete — 2026-10-22, verified by the PM (spec approved by Walker)
**Priority:** P1 · **Effort:** S (one Dev session; no restart)
**Depends on:** SCHED-001 ✅, WO-003 ✅. **Run before SCHED-003** (both edit `mes/schedule/code.py` and the Schedule view — never in parallel).
**Bug:** —

---

## Background

MES §4 FR4 and rules: readiness is captured by hand — plates and cylinders ready, ink ready, proof approved, entered by a person. A job is Ready only when all three are entered. The press does not wait for the MES, so a job that starts without readiness is flagged "started without readiness". Test criterion (§4): *a job with readiness not entered shows "started without readiness" when the press starts it.*

## Rules

1. **Save readiness.** `save_readiness(wo, plates, ink, proof, entered_by)` upserts `mes.job_readiness` (`INSERT … ON CONFLICT (work_order_id) DO UPDATE SET plates_ready, ink_ready, proof_approved, entered_by, entered_at = now()`). `is_ready` is the generated column — never written. `entered_by` required. Allowed for orders in `Planned`, `Released`, `On Hold`; refused (`error:`) for `Running`, `Complete`, `Closed`, `Cancelled` (readiness after the start would hide the deviation). Result string `ok: …` / `error: …`, logged at INFO on `mes.schedule`.
2. **Flag at the start.** When `sync_from_runs()` moves an entry **from Queued to Running** (its `actual_start_ts` was NULL before this sync), read that order's readiness **at that moment**: no `job_readiness` row, or `is_ready` false → `started_without_readiness = true`; otherwise `false`. Only on that transition — never recomputed afterwards (entering readiness later must not clear it), never on the first-tick backfill of entries that already ran (leave those `false`).
3. Unscheduled orders and unattributed runs: no entry, no flag (log `started without readiness: <wo> (unscheduled)` only, if attributed and not ready).
4. Parameterised SQL, `database='mes'`; writes only `job_readiness` (rule 1) and `schedule_entry.started_without_readiness` (rule 2). No tag writes.

## What Already Exists (Do Not Rewrite)

- `mes.schedule` (SCHED-001): `do_sync`, `actuals`, `handle`, `_SqlDb`, `shape_schedule`, columns incl. Plates/Ink/Proof. Keep SCHED-001 rules exactly; this task adds rule 2 inside the sync step and the readiness writer.
- `Schedule/ReadinessOk` query tag (UNS-004) reads `job_readiness.is_ready` for the head — unchanged.
- View `Mes/Test/Schedule` at `/mes/schedule`; `mes.workorder` (WO-003) — do not edit `mes/workorder`.
- Seed: readiness rows for WO-1001…1006 (WO-1006 proof missing). All seed entries already ran.

## What to Build

1. In `mes/schedule/code.py`: pure `check_readiness_edit(status)`; `do_save_readiness(db, …)`; rule 2 in the sync path (pure decision `flag_on_start(readiness_row)` + the db write); session function `save_readiness(...)`; read helper `readiness_targets()` (orders in Planned/Released/On Hold, label `"WO-1012 · 101 · Released · ready"` / `"… · not ready"`).
2. View `Mes/Test/Schedule`: add column **Started w/o readiness** (from `v_schedule_table.started_without_readiness`, shown as `YES` / blank); add a **Readiness** panel: dropdown order (`readiness_targets()`), three checkboxes (Plates & cylinders, Ink, Proof approved) pre-filled from the current row when an order is picked, text entered by, button **Save readiness**, result label. Bare style, `now(2000)` pattern.
3. Tests — extend `ignition/tests/replay_schedule.py` (or `test_schedule_readiness.py`, runs under `python3 -I`):
   - Edit allowed/refused per status (rule 1).
   - Replay: an order scheduled with **no readiness row** → flag `true` on its start; with all three → `false`; with proof missing (WO-1006 style) → `true`.
   - Readiness saved **after** the start does not clear the flag; first-tick backfill leaves Done entries `false`.
   - All existing tests still pass.

## Load and live check

1. **Stop** after tests: Dev Report; handoff: "Ready to load — waiting for Walker (Scan File System)."
2. After the scan: create **one test order** with the WO-003 panel only if needed — prefer an existing **Planned** order: save readiness on **WO-1010** (Planned, product 105) with plates ✓, ink ✓, proof ✗, entered by `"SCHED-002 live test"`; paste the result string and the `job_readiness` row (`is_ready = f`). Do **not** release or schedule anything (that would change what the press attributes).
3. Confirm `/mes/schedule` shows the new column and panel; `/mes/work-orders` and `/press1` load.

The live "started without readiness" moment is Walker's class demo (release + schedule an order with readiness missing, wait for its run).

## Acceptance Criteria

- [ ] Rules 1–4 exactly; flag set only on the Queued → Running transition, from readiness at that moment.
- [ ] Tests pass under `python3 -I`, including all earlier test files.
- [ ] Live: WO-1010 readiness saved (`is_ready` false); nothing released or scheduled; pages load; no restart.
- [ ] No edits to `mes/workorder`, `mes/engine`, `press1`, VIS-001 views, tag files, `db/`.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/schedule/code.py` | Readiness writer, rule 2, helpers |
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/views/Mes/Test/Schedule/view.json` | Column + Readiness panel |
| `ignition/tests/replay_schedule.py` / `test_schedule_readiness.py` | Tests |
| `docs/tasks/task_SCHED-002_readiness.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

## Dev Instructions

Spec: `docs/tasks/task_SCHED-002_readiness.md`. Add manual readiness entry (upsert `job_readiness`, refused once an order has started) and set `schedule_entry.started_without_readiness` from the readiness at the moment an entry goes Queued → Running; add the column and a Readiness panel to `/mes/schedule`. Test offline, **stop for Walker's Scan File System**, then save readiness on WO-1010 only.

Hard constraints:
- **Run alone** — not in parallel with SCHED-003 (same files).
- No container restarts. No schema change. Parameterised SQL; never delete rows. No tag writes; publish nothing.
- Live: do not release, schedule or move any order; only the WO-1010 readiness save.
- Do not edit `mes/workorder`, `mes/engine`, `mes/ui`, `press1`, `press1Tick`, `Press1/*` views, tag files, `db/`.
- Never stop, start, update or reimport the sim; never touch UNS Studio. Never read `[MQTT Engine]`, `4.0 Printing/…`, `Probe/…`.
- Never touch `ign-mes-demo`. Never print `.env` or any password. No commit, no push; stage nothing.

## Dev Report

**Status:** ✅ Done — ready for PM verification (2026-10-22). Offline tests pass; Walker scanned at 13:40:19 CDT; live steps 2–3 done (WO-1010 readiness only).

### What was built

- **`mes/schedule/code.py`** (modified; SCHED-001 rules unchanged):
  - Header comment: SCHED-002 rules and the new write (`job_readiness` upsert).
  - `READINESS_EDITABLE = ('Planned', 'Released', 'On Hold')`.
  - Pure `flag_on_start(readiness)` → `True` when the row is `None` or `is_ready` is false.
  - `do_sync(db, flag=True)`: unchanged SCHED-001 logic, plus rule 2 — inside the "changed" branch, when the entry was `Queued` with `actual_start` NULL and `actuals()` now says `Running`, it reads `db.readiness(wo)` **at that moment** and writes `db.set_started_without_readiness(entry_id, flag)` (true or false, once). Never written on any other transition, so later readiness edits cannot clear it. Rule 3: open runs of orders with no schedule entry and not ready add the note `started without readiness: <wo> (unscheduled)` (no write). Notes are appended to the sync result string after `; `, which `handle()` / `sync_from_runs()` log at INFO.
  - `handle()`: first tick calls `do_sync(db, flag=False)` — the backfill never flags (rule 2).
  - Pure `check_readiness_edit(status)` → `None`, or `error: no such work order` / `error: readiness cannot be changed on a <status> order (only Planned, Released, On Hold)`.
  - `do_save_readiness(db, wo, plates, ink, proof, entered_by)`: order picked, `entered_by` required (trimmed), status check, then `db.upsert_readiness`; result `ok: readiness WO-1010: plates yes, ink yes, proof no, entered by X (not ready)`.
  - `_SqlDb`: `readiness(wo)` (SELECT incl. generated `is_ready`), `set_started_without_readiness(entry_id, flag)` (UPDATE that one column), `order_status(wo)`, `upsert_readiness(...)` — `INSERT … SELECT … FROM mes.work_order WHERE work_order_id = ? AND status IN ('Planned','Released','On Hold') ON CONFLICT (work_order_id) DO UPDATE SET plates_ready, ink_ready, proof_approved, entered_by, entered_at = now()`; `is_ready` never written. Returns rowcount > 0.
  - Session functions `save_readiness(...)` (via `_run`, logged at INFO on `mes.schedule`), `readiness_targets()` (Planned/Released/On Hold orders, label `WO-1012 · 101 · Released · ready` / `… · not ready`, `SEP` middle dot as in `mes.ui`), `readiness_of(wo)` (row for pre-filling; all false when none).
  - `SCHEDULE_COLUMNS` + `shape_schedule`: new column `swr` "Started w/o readiness" (`YES` / blank); `VIEW_COLUMNS` reads `started_without_readiness` from `v_schedule_table`.
- **`Mes/Test/Schedule/view.json`** (modified): column **Started w/o readiness** (field `swr`); new row `ReadinessRow` above the table — dropdown `ReadinessOrder` (options bound to `view.custom.readinessOptions`, `now(2000)` + `readiness_targets()` with `mes.ui.log_once`), `onChange` on its value pre-fills the three checkboxes from `readiness_of()`; checkboxes Plates & cylinders / Ink / Proof approved; text field entered by; button **Save readiness** → `mes.schedule.save_readiness(...)`; label bound to `view.custom.readinessResult`. Title now says SCHED-001/002. Bare style.
- **`ignition/tests/replay_schedule.py`** (modified): FakeDb gains the seed readiness rows (WO-1001…1006, WO-1006 proof missing), `readiness`, `set_started_without_readiness`, `order_status`, `upsert_readiness` (with the SQL's status guard), and `started_without_readiness` on entries. New section 4 (below); column-count check 14 → 15.

### Files changed

- modified `ignition/projects/mes-2026/ignition/script-python/mes/schedule/code.py`
- modified `ignition/projects/mes-2026/com.inductiveautomation.perspective/views/Mes/Test/Schedule/view.json`
- modified `ignition/tests/replay_schedule.py`
- modified `docs/tasks/task_SCHED-002_readiness.md` (Status line, this report)
- modified `docs/agent_handoff.md` (Dev status block)

### Gate results

```
$ docker compose config -q; echo compose rc=$?
compose rc=0
$ for t in replay_engine replay_workorder replay_schedule test_workorder_actions test_ui_work_orders; do python3 -I ignition/tests/$t.py; done
replay_engine rc=0 23 PASS, 0 FAIL; PASSED: 0 failure(s)
replay_workorder rc=0 48 PASS, 0 FAIL; PASSED: 0 failure(s)
replay_schedule rc=0 68 PASS, 0 FAIL; PASSED: 0 failure(s)
test_workorder_actions rc=0 43 PASS, 0 FAIL; PASSED: 0 failure(s)
test_ui_work_orders rc=0 0 PASS, 0 FAIL; 0 failure(s)
view.json valid
```

`replay_schedule.py` section 4 output:
```
Readiness:
  PASS  edit allowed on Planned
  PASS  edit allowed on Released
  PASS  edit allowed on On Hold
  PASS  edit refused on Running
  PASS  edit refused on Complete
  PASS  edit refused on Closed
  PASS  edit refused on Cancelled
  PASS  edit refused on an unknown order
  ok: readiness WO-1010: plates yes, ink yes, proof no, entered by SCHED-002 test (not ready)
  PASS  save on WO-1010 (Planned): ok, not ready
  PASS  second save upserts the same row: ready
  PASS  entered by required
  PASS  no order picked
  PASS  unknown order
  PASS  save refused on a Running order, row untouched
  PASS  WO-1006 (seed, proof missing): flag true on its start
  PASS  WO-1010 with no readiness row: flag True on its start (ok: synced, 1 changed (WO-1010 Running); started without readiness: WO-1010)
  PASS  WO-1006 (seed, proof missing): flag true on its start
  PASS  WO-1010 with all three: flag False on its start (ok: synced, 1 changed (WO-1010 Running))
  PASS  WO-1006 (seed, proof missing): flag true on its start
  PASS  WO-1010 with proof missing: flag True on its start (ok: synced, 1 changed (WO-1010 Running); started without readiness: WO-1010)
  PASS  readiness entered after the start: flag stays true through Running -> Done
  PASS  ready at the start: flag stays false afterwards
  PASS  first-tick backfill: Done and Running entries left false, nothing logged
  PASS  unscheduled order: logged, no entry flagged (ok: synced, 0 changed; started without readiness: WO-1007 (unscheduled))
  PASS  unscheduled order, ready: nothing logged
  PASS  replay backfill: seed entries 1-5 false
  PASS  replay from the start: WO-1001..1005 ready -> false; WO-1006 (proof missing), 1007-1009 (no row) -> true
  PASS  replay: four 'started without readiness' log notes

PASSED: 0 failure(s)
```

Jython 2.7 parse (SCHED-001 method: gateway JRE + `jython-ia-2.7.4.0.jar`, `-S`, throwaway `/tmp/sched002_check.py` in the container, deleted; compile only):
```
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/script-python/mes/schedule/code.py
rc=0
```
Both changed `.py` files are ASCII. No restart: `mes-workshop-2026-ignition-1` StartedAt `2026-10-22T17:00:29Z`, `mes-workshop-2026-db-1` `2026-10-22T15:43:40Z` (unchanged). No DB writes, no tag writes, no commit, nothing staged. The project folder is bind-mounted, so the gateway sees the new files but runs them only after the scan.

### Deviations from spec

1. **Upsert has the status guard in SQL.** `INSERT … SELECT … FROM mes.work_order WHERE … status IN (…) ON CONFLICT … DO UPDATE` instead of `INSERT … VALUES`, so an order that starts between the check and the write is not written (same idea as WO-003's status guard in every `WHERE`). A 0-row result returns `error: <wo> changed status, readiness not saved`.
2. **The "started without readiness" notes are appended to the sync result line**, e.g. `sync ok: synced, 1 changed (WO-1010 Running); started without readiness: WO-1010` and `…; started without readiness: WO-1007 (unscheduled)`, rather than a separate log line. Same INFO logger, same text.
3. **Separate result label** for the Readiness panel (`view.custom.readinessResult`), so the Replan/Schedule result stays where it was.
4. Added `readiness_of(wo)` (not named in the spec) for the checkbox pre-fill.

### Open questions for the PM

1. **Queued → Done in one sync is not flagged** (strict reading of rule 2). It can only happen if a sync missed the run_start; in practice the first-tick backfill is the only case, and that is excluded by rule anyway.
2. **WO-003 reassign:** if a run is reassigned onto a scheduled Queued order, that entry goes Queued → Running at the next sync and is flagged from the readiness at *that* moment, not at the physical start. Acceptable?
3. **Unscheduled note repeats** on every sync while that order's run is open. Today syncs run only on run_start / product_change (the run is closed by the product change first) and the page has no Sync button, so it logs once in practice.
4. The spec's Status line read "awaiting Walker's approval" while the task index says "approved by Walker 2026-10-22"; I went by the index and the lead's `/dev` run.

### Live check

Walker's Scan File System at 13:40:19 CDT (`Restarting gateway scripts... project=mes-2026`). No container restart: `mes-workshop-2026-ignition-1` StartedAt still `2026-10-22T17:00:29Z`. No `store created` line and no errors or warnings in the log after the scan. The `mes.schedule.v1` store survived, so there was no new first tick.

Before: WO-1010 `Planned`, no `job_readiness` row; schedule 1–5 all `Done`, `started_without_readiness` all `f`.

**Save on the panel** (Chrome, `/mes/schedule`). The dropdown listed one order, `WO-1010 · 105 · Planned · not ready` (the others are Complete or Cancelled). Picking it left all three checkboxes clear (no row yet). Inputs: plates ✓, ink ✓, proof ✗, entered by `SCHED-002 live test` → **Save readiness**. Page result label and the gateway log:
```
I [m.schedule] [13:41:29.894]: save_readiness ok: readiness WO-1010: plates yes, ink yes, proof no, entered by SCHED-002 live test (not ready) component=root/ReadinessRow/SaveReadiness, view=Mes/Test/Schedule@C, project-name=mes-2026
```
`select * from mes.job_readiness where work_order_id='WO-1010'`:
```
 work_order_id | plates_ready | ink_ready | proof_approved | is_ready |     entered_by      |          entered_at
---------------+--------------+-----------+----------------+----------+---------------------+-------------------------------
 WO-1010       | t            | t         | f              | f        | SCHED-002 live test | 2026-10-22 18:41:29.891316+00
```
After: WO-1010 still `Planned`, `released_at` NULL; `schedule_entry` 5 rows, max sequence 5, 0 flagged; `work_order_run` 9 rows. **Nothing was released, scheduled or moved.** Replan and Schedule released orders were not pressed.

**Pages.** `/mes/schedule` shows the **Started w/o readiness** column (blank for entries 1–5) and the Readiness panel. `/mes/work-orders` loads with its table, unattributed runs and action panel. `/press1` loads and is live (SETUP, job 105, 13:41:46).

Not tested live: the "started without readiness" flag at a real run start. Per the spec, that is Walker's class demo (release and schedule an order with readiness missing, then wait for its run). It is proven offline in section 4.


## PM Verification

**PM, 2026-10-22 — PASS.**

- [x] PM ran all six test files under `python3 -I`: 0 failures (`replay_schedule.py` now 69 PASS).
- [x] Diff: `check_readiness_edit`, `flag_on_start` pure; upsert `INSERT … ON CONFLICT (work_order_id) DO UPDATE`, `is_ready` never written; flag written only when an entry goes Queued (no `actual_start`) → Running; first-tick backfill excluded. `mes/workorder`, `mes/engine`, `mes/ui`, tag files, `db/` untouched.
- [x] Live (DB): WO-1010 readiness t/t/f, `is_ready` f, entered by "SCHED-002 live test"; WO-1010 still Planned; 5 entries, none flagged. Scan 13:40:19, no restart.
- Not live yet: the flag at a real press start — Walker's class demo; proven offline.
- **Answers:** (1) Queued → Done in one sync not flagged — accepted (only the excluded backfill can do it). (2) After a WO-003 reassign onto a Queued order, the flag reflects readiness at the reassign — accepted; a reassign is a manual correction made after the fact. (3) Repeating unscheduled note — accepted. (4) Approval: Walker approved both SCHED specs; the spec status line was stale.

User-facing impact: Readiness panel and "Started w/o readiness" column on `/mes/schedule`.
