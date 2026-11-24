# SCHED-003 — Rush override and reconcile (MES §4.1)

**Status:** ✅ Complete — 2026-10-22, verified by the PM (spec approved by Walker)
**Priority:** P1 · **Effort:** M (one Dev session; no restart)
**Depends on:** SCHED-002 **verified by the PM** (same files). UNS-004 ✅ (`Schedule/*` memory tags).
**Bug:** —

---

## Background

MES §4.1 (confirmed by Walker, Oct 20): a rush override is a person's decision. The MES **records** it, **publishes** it, and **reconciles** it against the PLC at every Setup → Running. It never commands the press — the sim keeps running 101 → 105, and the MES shows the deviation. This task covers capture, the tag that UNS-005 will publish, and the four reconcile outcomes, plus the plan edit needed to stage the demo recipe.

## Rules

1. **Plan edit (no override record) — "MES proposes".** `move_up(wo)` swaps a **Queued** entry with the Queued entry immediately before it (by `sequence`), then `replan()`. No `schedule_override` row: this is the MES's own plan, used to stage "MES proposes 101, 102, 104, 103, 105". Result string; logged.
2. **Override — "move job to front".** `move_to_front(wo, reason, entered_by)`: the order must have a **Queued** entry that is not already the head (head = lowest-`sequence` Queued entry). `reason` ∈ `customer expedite`, `material availability`, `other`; `entered_by` required. In one transaction:
   - `from_sequence` = its sequence, `to_sequence` = head's sequence;
   - every Queued entry with `to_sequence ≤ sequence < from_sequence` gets `sequence + 1`; the order's entry gets `to_sequence`;
   - `INSERT mes.schedule_override (work_order_id, product_code, from_sequence, to_sequence, reason, entered_by)` (`entered_at` default; `published_at` stays NULL until UNS-005).
   Then `replan()`, then write tags (rule 4). Result string; logged.
3. **Reconcile at each run start.** On the engine's `run_start` event, **before** `sync_from_runs()` marks the new entry Running, take the head of the schedule (lowest-`sequence` Queued entry, i.e. after overrides) and the PLC product from the event:
   - **active override** = a `schedule_override` row whose order's entry is still `Queued` (not yet started);
   - `product == head product` and an active override moved the head's order → **Authorized override**;
   - `product == head product` and no active override for the head's order → **Normal start**;
   - `product != head product` and any active override exists → **Override not followed**;
   - `product != head product` and no active override → **Unrecorded deviation** (flag for review; never reorder history);
   - no Queued entry at all → no result (log `reconcile: schedule empty`).
   Store the result in `schedule_entry.reconcile_result` of **the entry that actually started** (the order WO-001 attributed this run to; resolved after attribution, which runs first). If the run is unattributed or its order is unscheduled, the result goes only to the log and the tag. An override is **consumed** when its order's entry leaves Queued (no extra column; rule "active" above handles it).
4. **Tags (memory tags from UNS-004, written with `system.tag.writeBlocking`, I/O layer only):**
   - `[default]40Printing/Dallas/Press Room/Press 1/Schedule/Override` ← JSON on each override: `{"work_order", "product_code", "from_sequence", "to_sequence", "reason", "operator", "timestamp"}` (MES §4.1 payload; timestamp ISO 8601 UTC).
   - `…/Schedule/OverrideActive` ← `true` while any active override exists, else `false` (refresh after override and after each reconcile).
   - `…/Schedule/ReconcileResult` ← the latest outcome string (or `""`).
   Log the write qualities; a bad quality is a logged warning, not an exception.
5. Parameterised SQL, `database='mes'`; writes only `schedule_entry` (sequence, planned times, reconcile_result) and `schedule_override` (insert). Never delete rows. No publishing (UNS-005).

## Demo recipe (Walker's class demo — for the tests, not live)

Press order is fixed 101 → 105. Vary the schedule, not the press:
- **Normal:** schedule 101…105 → every start Normal start.
- **Authorized override:** stage 101, 102, 104, 103, 105 with `move_up` (no record); after 102 starts, override **103 to front** → press runs 103 → Authorized override.
- **Override not followed:** schedule 101…105, override **105 to front** → press runs 101 → Override not followed.
- **Unrecorded deviation:** stage 101, 102, 104, 103, 105 with `move_up`, no override → press runs 103 while head is 104 → Unrecorded deviation.

## What Already Exists (Do Not Rewrite)

- `mes.schedule` after SCHED-002 (replan, sync, append, readiness, rule-2 flag). Keep all of it; reconcile hooks into `handle()` on `run_start` **before** the sync call.
- `mes.workorder` (attribution, WO-003 actions) — read only; do not edit.
- Schema: `schedule_override` (`override_id`, `work_order_id`, `product_code`, `from_sequence`, `to_sequence`, `reason` check, `entered_by`, `entered_at`, `published_at`); `schedule_entry.reconcile_result` check (`'Normal start','Authorized override','Override not followed','Unrecorded deviation'`). No schema change.
- Memory tags `Schedule/Override`, `OverrideActive`, `ReconcileResult` (UNS-004, `readOnly false`).

## What to Build

1. `mes/schedule/code.py`: pure `head(entries)`, `active_overrides(overrides, entries)`, `reconcile(product, head_entry, active)` → outcome or None, `resequence(entries, wo)` → list of (entry_id, new_sequence) + override row, `swap_up(entries, wo)`; I/O `move_up`, `move_to_front`, reconcile inside `handle()`, `_write_tags(...)`. Same conventions (pure + db interface, Jython/Python 3).
2. View `Mes/Test/Schedule`: column **Reconcile** (`reconcile_result`); an **Override** panel: dropdown order (Queued entries), dropdown reason (3 values), text entered by, buttons **Move to front (override)** and **Move up (plan edit, no record)**, result label; a small list of override records (time, order, from → to, reason, by, active yes/no).
3. Tests (`ignition/tests/test_schedule_override.py`, `python3 -I`): the four demo-recipe outcomes via replay with a FakeDb (schedule staged with `move_up` / `move_to_front` as in the recipe); resequencing correctness (no duplicate sequences, Done/Running entries untouched); validation errors (head already, not Queued, bad reason, empty entered_by); active-override consumption; tag payload JSON keys exactly as rule 4 (tag writer faked); all earlier tests pass.

## Load and live check

1. **Stop** after tests: Dev Report; handoff: "Ready to load — waiting for Walker (Scan File System)."
2. After the scan: confirm the log shows no errors on the next `run_start` (reconcile with an empty Queued schedule logs `reconcile: schedule empty`). Paste it.
3. **Do not press Move to front / Move up live** and do not schedule anything — the four outcomes are Walker's class demo. Confirm `/mes/schedule` shows the Reconcile column and Override panel; other pages load.

## Acceptance Criteria

- [ ] Rules 1–5 exactly; reconcile before sync; result on the entry that started; four outcomes per the table.
- [ ] Tags written with the exact payload keys; quality logged.
- [ ] Tests pass under `python3 -I` (new + all earlier).
- [ ] Live: clean `run_start` with `reconcile: schedule empty`; panel present; nothing pressed; no restart.
- [ ] No edits to `mes/workorder`, `mes/engine`, `mes/ui`, `press1`, VIS-001 views, tag files, `db/`.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/schedule/code.py` | Override, plan edit, reconcile, tag writes |
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/views/Mes/Test/Schedule/view.json` | Reconcile column + Override panel |
| `ignition/tests/test_schedule_override.py` | New |
| `docs/tasks/task_SCHED-003_override_reconcile.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

## Dev Instructions

Spec: `docs/tasks/task_SCHED-003_override_reconcile.md`. Add the rush override (move to front with a `schedule_override` record, resequence, replan, write the `Schedule/Override` JSON and `OverrideActive` tags), a plan edit without a record (move up), and reconcile at every run start before the sync, writing one of the four §4.1 outcomes to the started entry and `Schedule/ReconcileResult`. Prove the demo recipe offline, **stop for Walker's Scan File System**, then check one clean live run start without pressing any button.

Hard constraints:
- **Start only after SCHED-002 is verified by the PM.** Run alone (same files).
- No container restarts. No schema change. Parameterised SQL; never delete rows. Tag writes only to the three `Schedule/*` memory tags. Publish nothing.
- Live: press no button, schedule or move nothing.
- Do not edit `mes/workorder`, `mes/engine`, `mes/ui`, `press1`, `press1Tick`, `Press1/*` views, tag files, `db/`.
- Never stop, start, update or reimport the sim; never touch UNS Studio. Never read `[MQTT Engine]`, `4.0 Printing/…`, `Probe/…`.
- Never touch `ign-mes-demo`. Never print `.env` or any password. No commit, no push; stage nothing.

## Dev Report

**Status:** 🟡 Partial — code and offline tests done; **ready to load, waiting for Walker (Scan File System)**. Load-and-live steps 2–3 not run yet (they need the scan). Nothing pressed, nothing scheduled, no restart, no commit, nothing staged.

### What was built

**`mes/schedule/code.py`** (all earlier SCHED-001/002 behaviour kept):
- Header comment: SCHED-003 rules and the new writes (`schedule_override` INSERT, three memory tags).
- Constants: `OVERRIDE_REASONS` (3 values), `TAG_FOLDER = '[default]40Printing/Dallas/Press Room/Press 1/Schedule/'`, the four outcome strings.
- Pure: `head(entries)` (lowest-sequence Queued, non-Cancelled entry), `active_overrides(overrides, entries)` (override rows whose order's entry is Queued), `reconcile(product, head_entry, active)` (the rule-3 table; product cast with `int()`; `None` when no head), `resequence(entries, wo)` → `([(entry_id, new_sequence)], override row)` or `None`, `swap_up(entries, wo)` → `([(entry_id, new_sequence)], passed entry)` or `None`, `override_payload(row, ms)` (rule-4 JSON, keys `work_order, product_code, from_sequence, to_sequence, reason, operator, timestamp`; ISO 8601 UTC with ms).
- On a db: `do_move_up(db, now, wo)` (rule 1: swap in one transaction, no record, then replan); `do_move_to_front(db, now, wo, reason, entered_by, tags)` (rule 2: validate reason / entered_by / Queued / not head; in one transaction the guarded sequence UPDATEs + `INSERT … RETURNING entered_at`; then replan; then write `Override` (JSON) and `OverrideActive`); `do_reconcile(db, ev)` (rule 3: head + active overrides from the entries **before** the sync; stores on the entry of the order owning the open run that started at `ev['t']`; unattributed / unscheduled → log only).
- `handle(st, sample, events, db, tags=None)`: for each `run_start`, reconcile first, then the existing first-tick backfill or sync, then one tag write: `ReconcileResult` (outcome or `""`) and `OverrideActive` (computed after the sync). The 4-argument call of `replay_schedule.py` still works (no tags).
- `_SqlDb`: `tx` + `atomic(fn)` (same pattern as `mes.workorder`, commit only on `ok`); `entries()` now also returns `product_code` and runs on `self.tx`; new `overrides()`, `set_sequence()` (guarded `AND status = 'Queued'`, returns rows > 0), `insert_override()`, `set_reconcile()`. All parameterised, `database='mes'`.
- I/O: `move_up(wo)`, `move_to_front(wo, reason, entered_by)` (through `_run`, logged at INFO); `_write_tags(writes)` — one `system.tag.writeBlocking` on the `Schedule/*` paths, returns a `tag X write <quality>` line per tag, logs a bad quality at WARN, never raises. `on_tick` passes `_write_tags` to `handle`.
- Read API: `Reconcile` column (`reconcile_result`) in `SCHEDULE_COLUMNS` / `shape_schedule` / `VIEW_COLUMNS`; `override_targets()` (Queued entries, `3 · WO-1004 · 104`); `override_rows()` (newest first: time, order, product, from -> to, reason, by, active yes/no).

**`Mes/Test/Schedule/view.json`**: title mentions SCHED-003; new `custom.overrideOptions` / `custom.overrides` (2 s polls, same try/`log_once` pattern) and `custom.overrideResult`; new row **OverrideRow** under the Readiness row: label, dropdown order (Queued entries), dropdown reason (3 static values), text field "entered by", buttons **Move to front (override)** and **Move up (plan edit, no record)**, result label; table column **Reconcile** (16th); label + table **Overrides** under the schedule table (Time, Work order, Product, From -> to, Reason, Entered by, Active).

**`ignition/tests/test_schedule_override.py`** (new, 55 checks): the four demo-recipe outcomes by replaying one sim loop through engine → workorder → schedule with a FakeDb (schedule staged with `do_move_up` / `do_move_to_front` exactly as the recipe; the 103 override entered between ticks once WO-1002 is Running); resequencing (no duplicates, Done/Running untouched, Cancelled skipped, guarded-write rollback); validation errors; active-override consumption and `OverrideActive` sequence; payload keys and values; bad write quality; empty schedule, unattributed run, unscheduled order; `reconcile()` truth table.

### Files changed
- modified `ignition/projects/mes-2026/ignition/script-python/mes/schedule/code.py`
- modified `ignition/projects/mes-2026/com.inductiveautomation.perspective/views/Mes/Test/Schedule/view.json`
- created `ignition/tests/test_schedule_override.py`
- modified `ignition/tests/replay_schedule.py` (see Deviations)
- modified `docs/tasks/task_SCHED-003_override_reconcile.md` (Status line, this report)
- modified `docs/agent_handoff.md` (Dev status block)

### Gate results

```
$ for t in test_schedule_override replay_schedule replay_engine replay_workorder test_workorder_actions test_ui_work_orders; do python3 -I ignition/tests/$t.py; done   (summarised per file)
test_schedule_override: exit 0 · 55 PASS · 0 FAIL · PASSED: 0 failure(s)
replay_schedule: exit 0 · 68 PASS · 0 FAIL · PASSED: 0 failure(s)
replay_engine: exit 0 · 23 PASS · 0 FAIL · PASSED: 0 failure(s)
replay_workorder: exit 0 · 48 PASS · 0 FAIL · PASSED: 0 failure(s)
test_workorder_actions: exit 0 · 43 PASS · 0 FAIL · PASSED: 0 failure(s)
test_ui_work_orders: exit 0 · 23 PASS · 0 FAIL · 0 failure(s)
```
New test, non-PASS lines (the recipe as replayed):
```
Normal: schedule 101 .. 105
  reconcile: product=101 head=WO-1001 (101): Normal start; stored on WO-1001
  … 102, 103, 104, 105 likewise: Normal start
Authorized override: stage 101, 102, 104, 103, 105 with move_up; after 102 starts, 103 to front
  ok: WO-1004 moved up to 3, ahead of WO-1003 (plan edit, no override record); replanned 5 entries from anchor 2026-10-14T17:46:40Z, 5 changed
  ok: override WO-1003 (103) 4 -> 3, customer expedite, entered by SCHED-003 test; replanned 5 entries from anchor 2026-10-14T17:46:58Z, 5 changed; tag Override write Good; tag OverrideActive write Good
  reconcile: product=101 head=WO-1001 (101): Normal start; stored on WO-1001
  reconcile: product=102 head=WO-1002 (102): Normal start; stored on WO-1002
  reconcile: product=103 head=WO-1003 (103): Authorized override; stored on WO-1003
  reconcile: product=104 head=WO-1004 (104): Normal start; stored on WO-1004
  reconcile: product=105 head=WO-1005 (105): Normal start; stored on WO-1005
Override not followed: schedule 101 .. 105, override 105 to front
  ok: override WO-1005 (105) 5 -> 1, material availability, entered by SCHED-003 test; replanned 5 entries from anchor 2026-10-14T17:46:40Z, 5 changed; tag Override write Good; tag OverrideActive write Good
  reconcile: product=101 head=WO-1005 (105): Override not followed; stored on WO-1001
  reconcile: product=102 head=WO-1005 (105): Override not followed; stored on WO-1002
  reconcile: product=103 head=WO-1005 (105): Override not followed; stored on WO-1003
  reconcile: product=104 head=WO-1005 (105): Override not followed; stored on WO-1004
  reconcile: product=105 head=WO-1005 (105): Authorized override; stored on WO-1005
Unrecorded deviation: stage 101, 102, 104, 103, 105 with move_up, no override
  reconcile: product=101 head=WO-1001 (101): Normal start; stored on WO-1001
  reconcile: product=102 head=WO-1002 (102): Normal start; stored on WO-1002
  reconcile: product=103 head=WO-1004 (104): Unrecorded deviation; stored on WO-1003
  reconcile: product=104 head=WO-1004 (104): Normal start; stored on WO-1004
  reconcile: product=105 head=WO-1005 (105): Normal start; stored on WO-1005
Tags:
  {"from_sequence": 3, "operator": "Walker", "product_code": 103, "reason": "other", "timestamp": "2026-10-14T17:46:40.000Z", "to_sequence": 1, "work_order": "WO-1003"}
PASSED: 0 failure(s)
```
Compose and Jython 2.7 parse (gateway JRE + `lib/core/common/jython-ia-2.7.4.0.jar`, `-S`, throwaway `/tmp/sched003_check.py` in the container, deleted; compile only):
```
$ docker compose config -q && echo "compose config OK"
compose config OK
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/script-python/mes/schedule/code.py
rc=0
```
`code.py` is ASCII; `view.json` is valid JSON. Greps on `code.py`: no `MQTT Engine`, `4.0 Printing`, `Probe/`, `DELETE`, `press1`. Tag writes only to `Schedule/Override`, `OverrideActive`, `ReconcileResult`. No restart: `mes-workshop-2026-ignition-1` StartedAt `2026-10-22T17:00:29Z`, `mes-workshop-2026-db-1` `2026-10-22T15:43:40Z` (unchanged).

**Live (steps 2–3): not run — waiting for Walker's Scan File System.** After the scan: paste the next `run_start` log lines (expected `reconcile: schedule empty`, then `tag ReconcileResult write Good`, `tag OverrideActive write Good`), confirm `/mes/schedule` shows the Reconcile column and the Override panel, and that the other pages load. No button will be pressed.

### Deviations from spec
1. **`ignition/tests/replay_schedule.py` modified** (not in Files to Modify), test harness only: its FakeDb `entries()` now returns `product_code`, and it gains `overrides()` (empty) and `set_reconcile()`, because `handle()` now reconciles on every `run_start`; the column-count check goes 15 → 16 (Reconcile). Without this "all earlier tests pass" could not hold. No assertion was weakened.
2. **`OverrideActive` is written after the sync**, in the same tick as the reconcile, not between reconcile and sync. Reason: rule 4 says "true while any active override exists"; written before the sync, an Authorized override would leave it `true` until the next run start although the override was just consumed.
3. `ReconcileResult` is written `""` when the schedule is empty (rule 4's "or `""`").
4. `move_up` also runs in one transaction with the same Queued guard as the override (two UPDATEs); the spec only required it for `move_to_front`.

### Open questions for the PM
1. **Duplicate sequences can arise under rule 2 as written.** Rule 2 shifts only *Queued* entries in `[head, from)`. If a Done / Running / Cancelled entry sits inside that range (it happens after the press ran out of plan order, e.g. after "Override not followed", or with a Cancelled entry), the shifted Queued entry can land on its number. The test shows it: with WO-1003 Cancelled at 3, overriding WO-1005 gives WO-1002 3 and WO-1003 3. `sequence` is not unique in the schema and ties order by `entry_id`, so nothing breaks, but the "no duplicate sequences" criterion only holds when every non-Queued entry is below the head (true in all four demo recipes, tested). Keep as is, or should rule 2 renumber differently?
2. **"Override not followed" repeats** for every run start while the override stands: in the recipe, 101, 102, 103, 104 are all "not followed" and 105 then reads "Authorized override". This follows rule 3 literally; worth telling Walker before the class demo.
3. **`OverrideActive` after a gateway restart:** memory tags reset to `false`; it is refreshed only after an override and after each run start (as specced), so an override still active across a restart shows `false` until the next run start. Refresh it on the first tick too?
4. **Head choice:** Queued entries whose order is Cancelled are skipped (SCHED-001's Cancelled rule); a Queued entry whose order is **On Hold** still counts as the head. Confirm.
5. A `run_start` on the very first tick after a load is reconciled (before the backfill sync) like any other.
6. **Another Dev session (DT-001) is working in the same tree**: `mes/engine/code.py` (adds `_downtime` to `HANDLERS`), `mes/downtime/`, `replay_downtime.py`, `task_DT-001…`, `task_OEE-00*`, `project_management.md` are modified or new and are **not** from this task. When Walker scans, both tasks' code loads together.

## PM Verification

**PM, 2026-10-22 — PASS.**

- [x] PM ran all test files under `python3 -I`: `test_schedule_override.py` 56 PASS, all others 0 failures.
- [x] `reconcile()` matches the §4.1 table (head product + active override for the head's order → Authorized; head product, none → Normal; other product + any active → Not followed; else Unrecorded; no head → None). Called on `run_start` **before** the sync; result stored on the started entry; tags `Override` (JSON), `OverrideActive`, `ReconcileResult` written via `writeBlocking` with qualities logged. `move_to_front` / `move_up` in one transaction (`set_sequence … AND status = 'Queued'`), override INSERT, replan. No DELETE; `mes/workorder`, `mes/engine`, `mes/ui` untouched by this task.
- [x] `replay_schedule.py` change (fake db learns `overrides`, `set_reconcile`; 16 columns) — accepted, needed to keep the earlier test passing.
- [x] Live (after the 13:54 scan): next run start 13:56:14 → `reconcile: schedule empty`, `tag ReconcileResult write Good`, `tag OverrideActive write Good`; no errors. Buttons not pressed.
- **Dev's flags:** (1) duplicate sequences possible once the press has run out of plan order or a row is Cancelled — not in any demo; backlog WO-004 list. (2) "Override not followed" repeats on each start while the override stands — correct per §4.1; told Walker for the demo.

User-facing impact: Override panel, Reconcile column and override list on `/mes/schedule`; `Schedule/*` memory tags live.
