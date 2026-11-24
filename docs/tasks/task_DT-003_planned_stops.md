# DT-003 — Operator planned stops: entry, conflict flag, event tag, OEE exclusion

**Status:** 📝 Specced — approved by Walker 2026-10-22
**Priority:** P1 · **Effort:** M (one Dev session; no restart)
**Depends on:** DT-002 ✅, OEE-001 ✅, DT-004 ✅ and **OEE-002 verified by the PM** (this task edits `mes/downtime`, `mes/oee` and the Downtime view; OEE-002 also edits `mes/oee`, so never run them in parallel).
**Bug:** —

---

## Background

MES §5 planned downtime (Walker): **clean plates** and **roll change** are entered by an operator in the front end and stored in the database, each with a start, an end, a reason and the product code at the time. The press PLC does not show these stops. **Conflict rule:** a planned stop entered while the PLC shows Running is flagged as a conflict and kept for review — not blocked, never hidden or silently merged. Each planned stop is also published as `…/Press 1/Planned Stop/Event` (payload reason, start, end, operator; path per DEC-007). MES §6: planned stops are removed from planned production time and do not reduce Availability. Test criterion: *a planned stop entered while the PLC shows Running appears with the conflict flag.*

## Rules

1. **Start a stop.** `start_planned_stop(reason, operator)`: `reason` ∈ `clean plates`, `roll change`; `operator` required; refused (`error:`) if a stop is already open (`end_ts IS NULL`). Reads the engine's latest sample (`mes.engine.latest()`): `product_code` = its product; **conflict** if its `state == 1` → `conflict_flag = true`, `conflict_note = 'PLC Running at entry'`. `INSERT INTO mes.planned_stop (reason, start_ts, operator, product_code, conflict_flag, conflict_note)` with `start_ts` = the latest sample's time (sim clock). Result string; logged on `mes.downtime`.
2. **End a stop.** `end_planned_stop(operator)`: the open stop gets `end_ts` = the latest sample's time, guarded `WHERE planned_stop_id = ? AND end_ts IS NULL`. Refused if none is open.
3. **Event tag.** After rule 1 and after rule 2, write `[default]…/Press 1/Planned Stop/Event` (memory, UNS-004) with JSON `{"reason", "start", "end", "operator"}` (ISO 8601 UTC; `end` null on start). `published_at` stays NULL until UNS-005. `Planned Stop/Active|Reason|Since|ConflictWithPLC` are query tags and need nothing.
4. **No `downtime_event` row** for planned stops (they have their own table; `source 'operator'` stays unused, `docs/db_schema.md` §8).
5. **OEE exclusion.** Implement OEE-001's `planned_stop_overlap(start, end)` hook in `mes.oee`: seconds of `[start, end)` covered by planned stops (open stop = until now), **counting only non-running time** (state 2 or 3 intervals): a planned stop never removes Running time, and a conflicting stop logged during Running removes nothing from those Running seconds. Subtract that overlap from `planned_production_s` (never below 0). Cache open/recent stops in the store, refreshed every 10 s.
6. Parameterised SQL; writes only `planned_stop` (insert, end update). Never delete.

## What Already Exists (Do Not Rewrite)

- `mes.downtime` (DT-001 + DT-002), `mes.oee` (OEE-001, with the hook returning 0), `mes.engine.latest()`, the Downtime test page `Mes/Test/Downtime` (DT-004), `mes.ui` downtime functions.
- Schema `planned_stop` (`reason` check `clean plates` / `roll change`, `start_ts`, `end_ts`, `operator`, `product_code`, `conflict_flag`, `conflict_note`, `published_at`). No schema change.
- Query tags `Planned Stop/*` and memory tag `Planned Stop/Event` (UNS-004).

## What to Build

1. `mes/downtime/code.py`: pure `check_start(open_stop, reason, operator)`, `conflict(state)`, `event_payload(row)`; I/O `start_planned_stop`, `end_planned_stop`, `planned_stops(window_min)` (read helper for the page).
2. `mes/oee/code.py`: the hook per rule 5 (pure `overlap_non_running(intervals, stops)`), no other change.
3. View `Mes/Test/Downtime`: a **Planned stop** panel — dropdown reason, text operator, buttons **Start planned stop** / **End planned stop**, result label — and a **Planned stops** table (Start, End, Duration s, Reason, Operator, Product, Conflict, Note).
4. Tests (`ignition/tests/test_planned_stops.py`, `python3 -I`): start/end/refusals; conflict when the latest state is 1, none when 2 or 3; payload keys exactly `reason, start, end, operator`; OEE replay with a planned stop placed over a fault and a Setup overrun → `planned_production_s` drops by exactly the overlapped non-running seconds and A rises accordingly; a stop over Running time changes nothing. All earlier tests pass.

## Load and live check

1. **Stop** after tests: Dev Report; handoff: "Ready to load — waiting for Walker (Scan File System)."
2. After the scan, **with Walker's OK at that moment** (it adds a real row): start a planned stop `roll change`, operator `"DT-003 live test"`, while the press is Running → result shows the conflict; wait ~10 s; end it. Paste both results, the `planned_stop` row (`conflict_flag t`), and the `Planned Stop/*` values Walker sees in the Tag Browser during the stop. If Walker prefers to do it in class, skip and say so.

## Acceptance Criteria

- [ ] Rules 1–6 exactly; conflict from the engine's latest state; event JSON on start and end.
- [ ] OEE hook subtracts only overlapped non-running time; tests prove it; all earlier tests pass under `python3 -I`.
- [ ] Live (if Walker OKs): one conflicting test stop recorded and ended; page shows it.
- [ ] No schema change; no restart; no edits outside the listed files.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/downtime/code.py` | Planned stop entry, event tag |
| `ignition/projects/mes-2026/ignition/script-python/mes/oee/code.py` | Overlap hook |
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/views/Mes/Test/Downtime/view.json` | Panel + table |
| `ignition/tests/test_planned_stops.py` | New |
| `docs/tasks/task_DT-003_planned_stops.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

## Dev Instructions

Spec: `docs/tasks/task_DT-003_planned_stops.md`. Add operator planned stops (clean plates / roll change) to `mes.downtime`: start and end from the page, product and conflict taken from the engine's latest sample, the `Planned Stop/Event` JSON tag on start and end; implement the OEE hook so planned-stop overlap with non-running time leaves planned production time; add the panel and table to `/mes/downtime`. Test offline, **stop for Walker's Scan File System**, then one live conflicting test stop only with Walker's OK.

Hard constraints:
- **Start only after DT-002, OEE-001 and DT-004 are verified by the PM.** Edit only the listed files.
- No container restarts. No schema change. Parameterised SQL; never delete. Tag writes only to `Planned Stop/Event`. Publish nothing.
- Live: one test stop, only with Walker's OK.
- Never stop, start, update or reimport the sim; never touch UNS Studio. Never read `[MQTT Engine]`, `4.0 Printing/…`, `Probe/…`.
- Never touch `ign-mes-demo`. Never print `.env` or any password. No commit, no push; stage nothing.

## Dev Report

*(Dev fills in.)*

## PM Verification

*(PM fills in.)*
