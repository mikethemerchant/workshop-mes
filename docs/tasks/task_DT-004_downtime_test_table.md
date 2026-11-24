# DT-004 — Downtime test page: events and totals by class

**Status:** ✅ Complete — verified by PM 2026-10-23
**Priority:** P1 · **Effort:** S (one Dev session; no restart)
**Depends on:** DT-001 ✅. Can run **in parallel with DT-002 and OEE-001** (edits only `mes/ui`, a new view and `page-config`). **Not at the same time as OEE-002** (both add a page to `page-config/config.json`).
**Bug:** —

---

## Background

The Hour 3 downtime test visual (MES §5): a bare table of events (start, end, duration, class, planned flag, product code, work order) and a total by class. Test criteria: over one 17.4-minute loop, 15 alarm events — 9 web break, 4 tunnel over-temp, 2 RTO inhibit, about 82 s, 48 s and 24 s; five Setup events totalling 146 s — 118 s planned changeover and two 14 s overruns (after DT-002). Live values run ~3 % long (sim drift); check counts exactly and totals within ~4 %.

## What to Build

1. **`mes.ui` additions** (do not change existing functions; pure shaping + SELECT-only I/O, as in WO-002):
   - `downtime_rows(window_min)` — `SELECT event_id, start_ts, end_ts, duration_s, class, is_planned, source, fault_code, product_code, work_order_id FROM mes.downtime_event WHERE start_ts >= now() - (? * interval '1 minute') ORDER BY start_ts DESC` (`window_min` None → no filter), shaped by pure `shape_downtime(rows, fmt)`: Start, End (`HH:mm:ss` local), Duration s (1 decimal), Class, Planned (`yes`/blank), Source, Code (blank when 0), Product, Work order (blank when NULL).
   - `class_totals(window_min)` — same window, `GROUP BY class, is_planned` → Class, Planned, Events, Total s (1 decimal), sorted planned first then by total desc; plus a final row **All unplanned** (sum of unplanned) and **All planned**. Pure `shape_class_totals(rows)`.
   - `downtime_now_line()` — reads the five `Downtime/*` tags (`system.tag.readBlocking`) → `"Unplanned · Web break (101) · since 14:02:11 · 6.2 s"`, `"Planned · Changeover · since …"`, or `"Running"`.
2. **View `Mes/Test/Downtime`** at page `/mes/downtime` (add one entry to `page-config/config.json`): label "Downtime — DT-004 test (throwaway)"; the now-line; a window dropdown **Last loop (18 min)** (default) / 60 min / Since start; the class-totals table; the events table (newest first). Bare style, `now(2000)` pattern, view custom prop for the window.
3. **Offline test** `ignition/tests/test_ui_downtime.py` (`python3 -I`): `shape_downtime` (blanks for NULL WO and code 0, planned flag, formatting) and `shape_class_totals` (ordering, the two summary rows, sums) on hand-built rows — including a loop's worth (9/4/2 faults, 5 Changeover, 2 overrun) giving 82/48/24, 118 and 28 s. All earlier tests pass.

## Load and live check

1. **Stop** after tests: Dev Report; handoff: "Ready to load — waiting for Walker (Scan File System)." If DT-002 / OEE-001 are also waiting, one scan loads all.
2. After the scan: open `/mes/downtime` (or ask Walker); compare the class totals with
   `select class, is_planned, count(*), round(sum(duration_s),1) from mes.downtime_event group by 1,2 order by 2 desc, 4 desc;` — same numbers. Paste both. Do not wait for a full loop.

## Acceptance Criteria

- [ ] Functions added to `mes.ui` only (existing ones untouched); SELECT-only SQL, parameterised window; tag reads only.
- [ ] `/mes/downtime` shows the now-line, window dropdown, class totals with the two summary rows, and the events table; refreshes every 2 s.
- [ ] `test_ui_downtime.py` and all earlier tests pass under `python3 -I`.
- [ ] Live totals match the SQL; no restart; `/press1` and other test pages load.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/projects/mes-2026/ignition/script-python/mes/ui/code.py` | Add the downtime functions |
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/views/Mes/Test/Downtime/view.json`, `resource.json` | New |
| `ignition/projects/mes-2026/com.inductiveautomation.perspective/page-config/config.json` | Add `/mes/downtime` |
| `ignition/tests/test_ui_downtime.py` | New |
| `docs/tasks/task_DT-004_downtime_test_table.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

## Dev Instructions

Spec: `docs/tasks/task_DT-004_downtime_test_table.md`. Add read-only downtime shaping to `mes.ui` (events table, totals by class with planned/unplanned summary rows, a live now-line from the `Downtime/*` tags) and the bare `/mes/downtime` test page with a window selector. Test the shaping offline, **stop for Walker's Scan File System**, then compare the page's totals with SQL.

Hard constraints:
- Edit only the files listed. Do not edit `mes/downtime`, `mes/oee`, `mes/engine`, `mes/workorder`, `mes/schedule`, other views, `press1`, tag files, `db/`. Do not run in parallel with OEE-002 (page-config).
- No container restarts. SELECT-only SQL; tag **reads** only; publish nothing.
- Never stop, start, update or reimport the sim; never touch UNS Studio. Never read `[MQTT Engine]`, `4.0 Printing/…`, `Probe/…`.
- Never touch `ign-mes-demo`. Never print `.env` or any password. No commit, no push; stage nothing.

## Dev Report

**Status:** ✅ Done — ready for PM verification. Walker's Scan File System done; live check below matches the SQL.

### What was built

- **`mes/ui/code.py`** — new section `DT-004 downtime test page` appended; no existing function changed (`git diff` shows 0 removed lines).
  - Pure: `shape_downtime(rows, fmt)` (Start/End `HH:mm:ss`, Duration s 1 decimal, Class, Planned `yes`/blank, Source, Code blank when 0, Product and Work order blank when NULL); `shape_class_totals(rows)` (sorted planned first then total desc, then **All unplanned** and **All planned**; sums taken from unrounded totals, then rounded to 1 decimal); `shape_now_line(values, fmt)` (`Unplanned · Web break (101) · since HH:mm:ss · 6.2 s`, `Planned · Changeover · since … · 12.0 s`, `Unplanned · Changeover overrun · since … · s` — the code is left out when `CurrentAlarm` is 0; anything other than Planned/Unplanned → `Running`).
  - I/O: `downtime_rows(window_min)`, `class_totals(window_min)` — `system.db.runPrepQuery` on connection `mes`, SELECT only, window passed as a `?` parameter (`start_ts >= now() - (? * interval '1 minute')`), no filter when the window is None or 0; durations cast `::float8` in SQL so the shaping gets floats, not BigDecimal. `downtime_now_line()` — `system.tag.readBlocking` of the five `[default]40Printing/…/Press 1/Downtime/*` tags (`mes.engine.ROOT`); any tag not Good → `Downtime tags not good: <names>`.
- **View `Mes/Test/Downtime`** (`view.json`, `resource.json` copied from `Mes/Test/Schedule`): label "Downtime — DT-004 test (throwaway)"; the now-line; a **Window** dropdown (Last loop (18 min) = 18, default; 60 min = 60; Since start = 0) bidirectionally bound to view custom prop `window`; the class-totals table; the events table (newest first). Column template copied from the WorkOrders table. Bindings are view custom props: `nowLine` uses `now(2000)`; `totals` and `events` use the expression `toMillis(now(2000)) + '|' + coalesce({view.custom.window}, 0)` so they refresh every 2 s **and** at once on a window change; the transform takes the window after the `|`. Errors go to `mes.ui.log_once`, as on the other test pages.
- **`page-config/config.json`** — one entry `/mes/downtime` → `Mes/Test/Downtime`, title "MES test — Downtime".
- **`ignition/tests/test_ui_downtime.py`** — 25 checks: events-table columns and blanks, planned flag, rounding, NULL end/duration/product; class totals for a loop's worth of events (9/4/2 faults → 82/48/24 s, 5 Changeover → 118 s, 2 overruns → 28 s; All unplanned 17 / 182 s, All planned 5 / 118 s), input order not mattering, rounding of sums, empty input; the now-line in fault / changeover / overrun / running / never-written states.

### Files changed

- Modified: `ignition/projects/mes-2026/ignition/script-python/mes/ui/code.py`
- Modified: `ignition/projects/mes-2026/com.inductiveautomation.perspective/page-config/config.json`
- Created: `ignition/projects/mes-2026/com.inductiveautomation.perspective/views/Mes/Test/Downtime/view.json`
- Created: `ignition/projects/mes-2026/com.inductiveautomation.perspective/views/Mes/Test/Downtime/resource.json`
- Created: `ignition/tests/test_ui_downtime.py`
- Modified: `docs/tasks/task_DT-004_downtime_test_table.md` (Status line, Dev Report), `docs/agent_handoff.md` (Dev status block)

### Gate results

`python3 -I ignition/tests/test_ui_downtime.py` → 25 PASS, `0 failure(s)`, exit 0.

Earlier tests, each `python3 -I`:
```
ignition/tests/test_schedule_override.py exit=0 56 PASS 0 FAIL
ignition/tests/test_ui_work_orders.py exit=0 23 PASS 0 FAIL
ignition/tests/test_workorder_actions.py exit=0 44 PASS 0 FAIL
ignition/tests/replay_downtime.py exit=0 53 PASS 0 FAIL
ignition/tests/replay_engine.py exit=0 24 PASS 0 FAIL
ignition/tests/replay_oee.py exit=0 38 PASS 0 FAIL
ignition/tests/replay_schedule.py exit=0 69 PASS 0 FAIL
ignition/tests/replay_workorder.py exit=0 49 PASS 0 FAIL
```
`docker compose config -q` → no output, `compose exit=0`.

Jython 2.7 compile (gateway JRE + `jython-ia-2.7.4.0.jar`, `-S`, compile only; the project folder is bind-mounted and the gateway copy is byte-identical to the repo file, `cmp` → identical):
```
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/script-python/mes/ui/code.py
rc=0
```
`code.py` is ASCII. `view.json` parses as JSON.

The page's two SELECTs run read-only in psql (PREPARE/EXECUTE with window 18, and the GROUP BY with no window) — both valid. Current DB numbers (no window), page query vs the spec's comparison query, same numbers:
```
 Changeover         | t |  33 | 798.6
 Web break          | f |  65 | 615.6
 Tunnel over-temp   | f |  27 | 336.3
 Changeover overrun | f |  14 | 211.3
 RTO inhibit        | f |  13 | 161.4
```
Expected on the page with **Since start**: those five rows, then All unplanned 119 / 1324.6 s, All planned 33 / 798.6 s.

No restart: `mes-workshop-2026-ignition-1` StartedAt `2026-10-22T17:00:29Z`, `mes-workshop-2026-db-1` `2026-10-22T15:43:40Z`. No DB writes, no tag writes, nothing staged, no commit.

### Live check (after Walker's Scan File System, 2026-10-23 ~02:52Z)

Opened http://localhost:8089/data/perspective/client/mes-2026/mes/downtime in Chrome. Now-line `Running`; window dropdown shows **Last loop (18 min)** by default.

**Since start** — page totals table (read from the screen):
```
Changeover          yes   35   845.8
Web break                 69   652.7
Tunnel over-temp          29   351
Changeover overrun        14   211.3
RTO inhibit               14   173.6
All unplanned            126   1,388.6
```
(All planned is the next row below the fold.) SQL at 02:52:07Z, 161 rows:
```
select class, is_planned, count(*), round(sum(duration_s),1) from mes.downtime_event group by 1,2 order by 2 desc, 4 desc;
       class        | is_planned | count | round
--------------------+------------+-------+-------
 Changeover         | t          |    35 | 845.8
 Web break          | f          |    69 | 652.7
 Tunnel over-temp   | f          |    29 | 351.0
 Changeover overrun | f          |    14 | 211.3
 RTO inhibit        | f          |    14 | 173.6
```
Same numbers; All unplanned 69 + 29 + 14 + 14 = 126, 652.7 + 351.0 + 211.3 + 173.6 = 1388.6.

**Last loop (18 min)**, at ~02:51Z (before event 161, a Web break of 8.2 s at 02:51:5x, was written): totals Changeover yes 2 / 47.2, Web break 3 / 28.9, Tunnel over-temp 1 / 12.5, RTO inhibit 1 / 12.2, All unplanned 5 / 53.6, All planned 2 / 47.2. Events table, newest first: 21:50:44 Web break 10.2 s (101, product 101), 21:49:35 Changeover 18.3 s yes, 21:49:23 RTO inhibit 12.2 s (103), 21:48:23 Web break 10.5 s, 21:47:19 Tunnel over-temp 12.5 s (102), 21:46:02 Changeover 28.9 s yes, 21:45:54 Web break 8.2 s — code blank on Changeover, Work order blank (runs unattributed), matching the gateway's `m.downtime` log lines for events 154–160. The 18-minute SQL a minute later (`… where start_ts >= now() - interval '18 minutes' …`) gave Changeover 2 / 47.2, Web break 4 / 37.1, Tunnel over-temp 1 / 12.5, RTO inhibit 1 / 12.2 — the page plus event 161.

Switching the dropdown to **Since start** refreshed the tables at once. `/press1`, `/mes/work-orders` and `/mes/schedule` load and show live data. Gateway log since the scan: no `mes.ui` lines, no new warnings or errors. No restart (Ignition StartedAt `2026-10-22T17:00:29Z`). No full loop waited for, per the spec.

### Deviations from spec

1. **"Since start" is window 0, not None.** A Perspective dropdown value of `null` reads as "nothing selected", so the option value is 0 and the functions treat None **or** 0 as no filter.
2. **The totals/events bindings use `toMillis(now(2000)) + '|' + window`** instead of bare `now(2000)`, so a window change refreshes the tables at once instead of on the next tick. Still the 2 s poll.
3. Page title (browser tab) is "MES test — Downtime", matching the other test pages; the on-page label is the spec's "Downtime — DT-004 test (throwaway)".

### Open questions for the PM

1. **Downtime gap 21:02:50Z → 02:45:54Z (informational).** No `downtime_event` rows were written for about 5 h 40 min. Events resumed at 02:45:54Z (event 154). Around then the gateway log shows MQTT module warnings at 21:44:58–21:45:00 CDT (`MQTT Transmission is not enabled`, a discarded Sparkplug STATE message), which points to the MQTT feed reconnecting rather than to the MES. I did not investigate further — out of scope. Event 153 (Tunnel over-temp, start 21:03:49Z) closed with 2.2 s at 21:45 CDT, so it looks right.
2. **Cosmetic:** the table's auto-render drops a trailing `.0` (`351`) and adds a thousands separator (`1,388.6`). The values are right; setting each column's number format would fix it if the Hour 4 page needs it.

## PM Verification

**Verified 2026-10-23 (~03:00Z). Passed.**
- Code: `git diff mes/ui/code.py` shows additions only (DT-004 section); SQL is SELECT-only with the window as a `?` parameter; tag reads only (`readBlocking` on the five `Downtime/*` tags). `page-config` gains one entry `/mes/downtime`.
- Tests rerun by PM under `python3 -I`: `test_ui_downtime.py` and all 8 earlier suites pass.
- Live: Dev's screen totals (Since start, 02:52Z) equal the comparison SQL row for row. PM reran the SQL at ~03:00Z: Changeover t 36 / 875.8, Web break 70 / 662.9, Tunnel over-temp 30 / 363.4, Changeover overrun 15 / 227.0, RTO inhibit 15 / 185.9 — consistent growth since the Dev's read.
- The 21:03–02:45Z quiet period is a sim feed pause, not an MES fault: `mes.engine` logged `gap seq=1076 gap_s=20470.728` at 02:45:02Z with no counter reset; containers were not restarted.
- Accepted deviations: "Since start" stored as 0 (dropdown treats empty as unselected); the window change refreshes at once. Number display (`351` for 351.0, thousands commas) is the Table's default formatting — fine for a throwaway page; VIS-002 tables should format explicitly.
