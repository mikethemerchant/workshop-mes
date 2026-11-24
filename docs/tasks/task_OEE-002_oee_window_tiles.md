# OEE-002 — OEE window, live tags and the OEE test page

**Status:** 📝 Specced — approved by Walker 2026-10-22
**Priority:** P1 · **Effort:** S–M (one Dev session; no restart)
**Depends on:** OEE-001 **verified by the PM** (same module).
**Bug:** —

---

## Background

MES §6: report Availability, Performance, Quality and OEE for Press 1 **live, per work order and per time window**. Test criteria (Day 2 Hr 3): **four tiles (A, P, Q, OEE) and a per-job table**; after one full loop the values match the §6 table within about 1–2 points. AI hook: "How does OEE compare between 2-wide and 4-wide jobs, and what drives the gap?"

## Rules

1. **Ratios from interval sums** (same formulas as `mes.v_oee_by_product`): over the selected rows, `A = Σrunning_s / Σplanned_production_s`, `P = min(1, Σgood_m / Σideal_m)` (display cap at 100 %), `Q = Σgood_m / (Σgood_m + Σreject_m)`, `OEE = A × P × Q`. Any zero denominator → that ratio is `None` (shown blank), OEE `None`.
2. **Live window.** Every **10 s** (not every tick), compute rule 1 over `oee_interval` rows with `interval_start ≥ now − WindowMinutes` (all products) and write the memory tags `OEE/Availability`, `OEE/Performance`, `OEE/Quality`, `OEE/OEE` as **percent, 1 decimal** (`None` → leave the tag unchanged and log once). `OEE/WindowMinutes` (memory, default 15) is the window; read it each time.
3. **Window selector.** `set_window(minutes)` accepts 5, 15, 30, 60 and writes `OEE/WindowMinutes`; anything else → `error:`. This is the only other writer of that tag (the page calls it).
4. **Per work order and per product** (test page, read-only queries): per WO = rule 1 grouped by `work_order_id` (rows with NULL WO shown as "unattributed"); per product = `mes.v_oee_by_product` (since start). Per hour = rule 1 grouped by `date_trunc('hour', interval_start)`.
5. Parameterised SQL, SELECT only on `oee_interval` / the view. Tag writes only to the five `OEE/*` tags.

## What Already Exists (Do Not Rewrite)

- `mes.oee` (OEE-001): interval writer, store `mes.oee.v1`, handler `on_tick`. Add the 10 s window step inside `on_tick` (time-gated from the store), not a new timer.
- View `mes.v_oee_by_product`; memory tags `OEE/*` (UNS-004).
- Page pattern: `Mes/Test/WorkOrders`, `Mes/Test/Schedule` (bare, `now(2000)` + script transform).

## What to Build

1. In `mes/oee/code.py`: pure `ratios(rows)` → dict of A/P/Q/OEE (fractions or None); `window_rows`, `by_work_order`, `by_product`, `by_hour` I/O helpers returning shaped rows (percent, 1 decimal, `""` for None, Lane info from product master: lanes column so 2-wide vs 4-wide is visible); `set_window`; the time-gated tag update in `on_tick`.
2. View `Mes/Test/Oee` at page `/mes/oee` (add to `page-config/config.json` only):
   - Four tiles (labels) A, P, Q, OEE bound to the `OEE/*` tags, plus a window dropdown (5/15/30/60) calling `set_window`.
   - Table **Per product (since start)**: Product, Lanes, A, P, Q, OEE, Running s, Planned prod. s, Good m, Reject m.
   - Table **Per work order**: Work order, Product, A, P, Q, OEE, Good m, Reject m.
   - Table **Per hour**: Hour (local), A, P, Q, OEE.
   - Bare style; label "OEE — OEE-002 test (throwaway)".
3. Tests: extend `ignition/tests/replay_oee.py` (`python3 -I`): `ratios` on the second-loop rows → §6 loop and per-product values (± 0.1); zero-denominator cases; cap at 100; `set_window` validation; the 10 s gate (two ticks 2 s apart → one tag write); window filter excludes older minutes. All earlier tests pass.

## Load and live check

1. **Stop** after tests: Dev Report; handoff: "Ready to load — waiting for Walker (Scan File System)."
2. After the scan: within ~30 s the log shows the first window computation. Paste it and compare with
   `select round(100*sum(running_s)/nullif(sum(planned_production_s),0),1) a, round(100*least(1,sum(good_m)/nullif(sum(ideal_m),0)),1) p, round(100*sum(good_m)::numeric/nullif(sum(good_m+reject_m),0),1) q from mes.oee_interval where interval_start >= now() - interval '15 minutes';`
   (± 0.1). Open `/mes/oee` (or ask Walker) and confirm tiles and tables. Do not change the window live except one round trip 15 → 5 → 15 to prove `set_window`.
3. **Full-loop match to §6 (1–2 points) is the class check**, not the Dev's — do not wait 17 minutes.

## Acceptance Criteria

- [ ] Rules 1–5 exactly; tags updated every 10 s, percent 1 decimal; `set_window` validated.
- [ ] Tests reproduce §6 (± 0.1), edge cases pass; all earlier tests pass under `python3 -I`.
- [ ] Live: tag values match the SQL; page shows tiles and three tables; window round trip works; no restart.
- [ ] Edits only in `mes/oee`, the new view, `page-config/config.json` (one entry) and the test.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/oee/code.py` | Ratios, window, helpers, tag update |
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/views/Mes/Test/Oee/view.json`, `resource.json` | New |
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/page-config/config.json` | Add `/mes/oee` |
| `ignition/tests/replay_oee.py` | Tests |
| `docs/tasks/task_OEE-002_oee_window_tiles.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

## Dev Instructions

Spec: `docs/tasks/task_OEE-002_oee_window_tiles.md`. In `mes.oee`, compute A/P/Q/OEE from `oee_interval` sums over the live window every 10 s and write the four `OEE/*` tags (percent), add a validated window selector, and build the bare `/mes/oee` test page with tiles and per-product, per-work-order and per-hour tables. Prove the §6 values offline, **stop for Walker's Scan File System**, then compare the tags with a SQL check.

Hard constraints:
- **Start only after OEE-001 is verified by the PM.** Edit only the files listed.
- No container restarts. No schema change. SELECT-only SQL. Tag writes only to the five `OEE/*` tags. Publish nothing.
- Never stop, start, update or reimport the sim; never touch UNS Studio. Never read `[MQTT Engine]`, `4.0 Printing/…`, `Probe/…`.
- Never touch `ign-mes-demo`. Never print `.env` or any password. No commit, no push; stage nothing.

## Dev Report

*(Dev fills in.)*

## PM Verification

*(PM fills in.)*
