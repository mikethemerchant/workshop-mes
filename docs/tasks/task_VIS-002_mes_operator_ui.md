# VIS-002 — Real MES screens: one `/mes` page with tabs

**Status:** 📝 Specced — approved by Walker 2026-10-23
**Priority:** P1 · **Effort:** L (one Dev session in four steps; no restart)
**Depends on:** DT-003 and OEE-002 **verified by the PM**. Their read and action functions are used here. VIS-002 edits no file that UNS-005 edits, so the two can run in parallel.
**Bug:** —

---

## Background

MES §1 and the operating manual say: "Test visuals on Day 2 Hours 2–3 are throwaway. The real visuals come in one pass in Hour 4."

**Scope (Walker, 2026-10-22, the PM default):**
- One page **`/mes`**, in the same dark theme and project stylesheet as `/press1` (VIS-001).
- Tabs: **Overview**, **Work Orders**, **Schedule**, **Downtime**, **OEE**.
- The operator actions move over from the test pages. The UI parts of the WO-004 follow-ups are fixed along the way.
- The test pages `/mes/work-orders`, `/mes/schedule`, `/mes/downtime` and `/mes/oee` **stay as they are**. They are the fallback in class.

This is a presentation task. **All MES logic already exists.** VIS-002 calls the existing `mes.*` read and action functions. It never adds SQL that writes, and it never duplicates rules.

## Design rules

1. **Look.**
   - Copy `/press1`: the `Press1/Main` shell pattern (header, tab bar, footer), the `dark-cool` theme and the project stylesheet.
   - New classes are prefixed `mes-`. Append them to `stylesheet/stylesheet.css`, reusing the existing CSS custom properties for colour. **Do not change or remove any `psc-` class or variable.** Views carry class names only, with no hex colours.
   - The state colours are the VIS-001 ones: Running green, Setup amber, Faulted red.
2. **Header.**
   - Title "4.0 Printing — Press 1 MES", with the subtitle "Dallas · Press Room · work orders, schedule, downtime, OEE".
   - On the right:
     - a machine-state pill (Running / Setup / Faulted, from `mes.engine.latest()`);
     - a LIVE pill, red when the engine's last sample is more than 10 s old. This makes a sim pause like tonight's 21:03–02:45Z gap visible;
     - a clock.
3. **Refresh.**
   - Use the `now(2000)` polling pattern used on the test pages.
   - One binding per table or tile group calls one `mes.*` function. Do not put a poll on every cell.
4. **Actions.**
   - Every button calls the existing function and shows its result string in a result line on that panel.
   - **After a success, clear the panel's inputs.** While a call is in flight, the button is disabled, using a view custom prop `busy`. This fixes the WO-004 double-click duplicate.
   - Destructive actions (Cancel, Close, Hold) need a second click on a "Confirm" button that appears inline. **No browser dialogs.**
5. **Dates.** The Create work order date picker opens at **now + 24 h, rounded to the hour**, not 00:00. This is the WO-004 fix.
6. **Tables.** Use the VIS-001 approach: a Flex Repeater of row views, or the Perspective Table with the stylesheet classes; pick one and use it everywhere. Newest or soonest first, as on the test pages. They must not scroll the whole page: give each one a fixed height and let it scroll inside.
7. **No new logic.**
   - If a screen needs a value no existing function returns, add a **read-only** shaping function to `mes.ui`, using the same pure-plus-SELECT pattern as WO-002 and DT-004. Do not change existing functions.
   - Anything that needs a logic change goes in the Dev Report as an open question.

## Tabs

**Overview** (the default tab)
- Banner: state, product code, and the active work order with its status and good/reject m. Sources: the `Work Order/Active/*` tags and `mes.ui.open_run_line()`.
- The OEE tiles A, P, Q and OEE from the `OEE/*` tags, with the window in minutes from `OEE/WindowMinutes`.
- The downtime now-line (`mes.ui.downtime_now_line()`).
- The next three schedule entries, with their readiness flags.
- The planned stop now-line, from the `Planned Stop/*` tags. Show a conflict badge when `ConflictWithPLC` is set.
- The last reconcile result (`Schedule/ReconcileResult`).

**Work Orders**
- The work order table (`mes.ui.work_order_rows()`), and the unattributed runs (`mes.ui.unattributed_rows()`).
- These panels:
  - **Create** (product dropdown, planned m, due date, priority, notes);
  - **Release / Hold / Cancel / Close** on the selected order;
  - **Reassign run**.
- These call the WO-003 functions, the same ones used on `Mes/Test/WorkOrders`. Read that view's scripts and reuse their calls; do not copy their layout.

**Schedule**
- The schedule table (`mes.schedule.schedule_rows()`), and the override history (`override_rows()`).
- These panels:
  - **Readiness** (plates, ink, proof, entered by);
  - **Move up**;
  - **Move to front**, with a required reason and operator. This is the rush override.
- The reconcile result line.

**Downtime**
- The now-line, the window selector (Last loop 18 min / 60 min / Since start), the totals by class and the events table. These use the DT-004 functions.
- The **planned stop** panel and table from DT-003: start or end, with a reason and an operator. A conflict row is highlighted.

**OEE**
- The four tiles, the window selector and the per-product, per-work-order and per-hour tables. These use the OEE-002 functions and `set_window`.
- A note in small text: "Planned stops and changeover within schedule do not reduce Availability."

## Steps (stop for Walker's Scan File System after each step)

1. **Shell + Overview.** `Mes/Main` (header, pills, clock, tab bar, footer), `Mes/Tabs/Overview`, the page `/mes`, and the stylesheet additions.
2. **Work Orders + Schedule** tabs, with their actions.
3. **Downtime + OEE** tabs, with their actions.
4. **Pass**:
   - Every tab loads with no script errors in the gateway log.
   - `/press1` and the four test pages still load.
   - The result of one harmless action, **only with Walker's OK at that moment**: Readiness save on a Planned order, which is idempotent.

Each step ends with a Dev Report update and the handoff line "Step N ready to load — waiting for Walker (Scan File System)". Walker may tell the Dev to run several steps before a single scan.

## Acceptance Criteria

- [ ] `/mes` has five tabs in the `/press1` look. The header has the state pill, a LIVE pill that turns red when the data is stale, and the clock.
- [ ] Every operator action from the four test pages is on `/mes` and calls the same function. Inputs clear after a success, buttons are disabled while busy, destructive actions need an inline confirm, and the date picker opens at now + 24 h.
- [ ] No hex colours in the views. Only `mes-` classes are appended to the stylesheet; the `psc-` classes are unchanged.
- [ ] No new write SQL and no logic changes. Any `mes.ui` additions are read-only, have an offline test in `ignition/tests/test_ui_vis002.py` (`python3 -I`), and all earlier tests pass.
- [ ] `/press1`, `/mes/work-orders`, `/mes/schedule`, `/mes/downtime` and `/mes/oee` are unchanged and still load. No restart.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/views/Mes/Main/`, `Mes/Tabs/*`, `Mes/Parts/*` | New |
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/page-config/config.json` | Add `/mes` |
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/stylesheet/stylesheet.css` | Append `mes-` classes |
| `ignition/projects/mes-2026/ignition/script-python/mes/ui/code.py` | Read-only additions only, if needed |
| `ignition/tests/test_ui_vis002.py` | New, if `mes.ui` changes |
| `docs/tasks/task_VIS-002_mes_operator_ui.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

## Dev Instructions

Spec: `docs/tasks/task_VIS-002_mes_operator_ui.md`.

Build the real MES screens: one `/mes` page with Overview, Work Orders, Schedule, Downtime and OEE tabs, in the `/press1` dark look.
- Use the existing `mes.*` read and action functions only.
- Move the operator actions over from the test pages, with inputs that clear, busy-disabled buttons, inline confirms and the date picker at now + 24 h.
- Build it in four steps, **stopping for Walker's Scan File System after each step**.

Hard constraints:
- **Start only after the PM has verified DT-003 and OEE-002.** Edit only the listed files.
- Do not change the `press1` library, the `Press1/*` views, the `psc-` styles, the `Mes/Test/*` views, or any `mes.*` logic module. `mes.ui` gets read-only additions only.
- No container restarts. No tag files. No schema change. No new write SQL. Publish nothing. Live actions only with Walker's OK.
- Never stop, start, update or reimport the sim. Never touch UNS Studio. Never read `4.0 Printing/…` or `Probe/…`.
- Never touch `ign-mes-demo`. Never print `.env` or any password. No commit, no push; stage nothing.

## Dev Report

*(Dev fills in.)*

## PM Verification

*(PM fills in.)*
