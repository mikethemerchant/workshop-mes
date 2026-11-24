# UNS-004 — Functional folders and tags in `[default]` (DEC-007)

**Status:** ✅ Complete — 2026-10-22, verified by the PM, approved by Walker
**Priority:** P1 · **Effort:** S–M (one Dev session; stop before the restart)
**Depends on:** UNS-002 ✅, ENG-001 ✅, DB-001 ✅, INFRA-002 ✅. DB-002 (seed) is **not** required: the queries return empty values until it runs.
**Bug:** —

---

## Background

DEC-007's second half: the functional folders `Work Order`, `Schedule`, `Downtime`, `Planned Stop`, `OEE`, plus the definitional `Product` folder, beside the edge folders under `[default]40Printing/Dallas/Press Room/Press 1/`. This task creates the folders and every tag in `docs/ignition_tag_namespace.md` §3, so the Hour 2–4 visuals have names to bind to. It writes no MES logic: WO-001, SCHED-00x, DT-00x and OEE-00x fill the values.

## Design decision (PM; approved by Walker with the spec, 2026-10-22)

**One rule decides each tag's value source: where its truth lives.**
- **The record is in PostgreSQL → query tag** (`valueSource: "db"`, connection `mes`). The tag reflects the database; the logic tasks write rows, never these tags.
- **The truth is in gateway memory → memory tag** (`valueSource: "memory"`), written only by the MES logic through `system.tag.writeBlocking`. Walker, 2026-10-22: ingest state, the open downtime event and live OEE live in the gateway, not the database.

So: `Product` and `Work Order/Active` and the record-backed parts of `Schedule` and `Planned Stop` are query tags; `Downtime`, `OEE` and the event/reconcile tags are memory tags. Every tag has exactly one writer.

## Preflight findings (PM, 2026-10-22, read-only)

- **Database connection name in the gateway: `mes`** (`jdbc:postgresql://db:5432/mes`, PostgreSQL translator). Tag group: `Default` only.
- **Query tag format** (from ign-mes-demo, 8.3.9): `"valueSource": "db", "datasource": "<conn>", "queryType": "Select", "query": "…", "executionMode": "FixedRate", "executionRate": <ms>`. **Without `executionMode: FixedRate` a db tag defaults to EventDriven and runs once at gateway start** (ign-mes-demo Task 31).
- **Memory tags:** `"valueSource": "memory", "defaultValue": …`. **`defaultValue` applies only when the tag is first created**; changing it later needs delete → restart → re-add → restart. Get defaults right the first time.
- Tag files: same layout, markers and push method as UNS-002 (`tags.json` + `unary-resource.json` listing it; parents `files: []`; `docker exec … cat >`, no `docker cp`; read only at gateway start).
- The `Press 1/unary-resource.json` marker already exists (`files: []`) from UNS-002. Do not rewrite it differently.
- Schema: `db/001_schema.sql`, `docs/db_schema.md`. The schema is applied; the seed (DB-002) is not, so every query returns NULL / 0 / false today.

## What Already Exists (Do Not Rewrite)

- `ignition/tags/generate_edge_tags.py` and the 17 edge tags (UNS-002, ENG-001) — unchanged.
- `mes.engine`, `mesTick` (ENG-001) — unchanged. No handler is added in this task.
- `press1`, `press1Tick`, `Press1/*` views (VIS-001) — unchanged.
- `db/*.sql` — unchanged. No schema change.

## What to Build

### 1. Generator

`ignition/tags/generate_functional_tags.py` — Python 3 stdlib, deterministic (same JSON style as `generate_edge_tags.py`: sorted keys, 2-space indent, trailing newline). Writes only under `ignition/tags/default/40Printing/Dallas/Press Room/Press 1/{Work Order,Schedule,Downtime,Planned Stop,OEE,Product}/`. It must not write or touch any edge folder or the `Press 1` marker. A folder with no tags of its own (`Work Order`, `Product`) gets a marker with `files: []`.

### 2. Tags

Common fields: `tagType: "AtomicTag"`, `documentation` = one sentence naming the source and the task that writes it (e.g. `"Written by DT-001 from mes.engine (memory)."`, `"Query on mes.work_order (UNS-004)."`).
**Query tags:** `datasource: "mes"`, `queryType: "Select"`, `executionMode: "FixedRate"`, `readOnly: true`. **Every query returns exactly one row and one column, even when no record matches** — wrap it as a scalar subquery: `SELECT (SELECT … LIMIT 1)`. Fully qualify tables as `mes.<table>`.
**Memory tags:** `readOnly: false` (the gateway logic writes them), `defaultValue` as listed.

**`Product/<code>/` — definitional, query, `executionRate` 60000.** One folder per code `101`–`105`:

| Tag | dataType | engUnit | query |
|---|---|---|---|
| IdealSpeed | Float8 | m/min | `SELECT (SELECT ideal_speed_mpm FROM mes.product_master WHERE product_code = <code>)` |
| Lanes | Int4 | — | `… lanes …` |
| ScheduledChangeoverSec | Int4 | s | `… scheduled_changeover_s …` |

**`Work Order/Active/` — query, `executionRate` 2000.** The Running work order, newest run first: subquery base `FROM mes.v_work_order_table WHERE status = 'Running' ORDER BY run_start DESC NULLS LAST LIMIT 1`.

| Tag | dataType | engUnit | column |
|---|---|---|---|
| WorkOrderId | String | — | `work_order_id` |
| ProductCode | Int4 | — | `product_code` |
| Status | String | — | `status` |
| QtyGood | Int4 | m | `good_m` |
| QtyReject | Int4 | m | `reject_m` |
| StartedAt | DateTime | — | `run_start` |

**`Schedule/`**

| Tag | Source | dataType | Default / query (`executionRate` 2000) | Writer |
|---|---|---|---|---|
| HeadProductCode | query | Int4 | `SELECT (SELECT wo.product_code FROM mes.schedule_entry se JOIN mes.work_order wo USING (work_order_id) WHERE se.status = 'Queued' ORDER BY se.sequence LIMIT 1)` | SCHED logic via rows |
| QueueDepth | query | Int4 | `SELECT count(*) FROM mes.schedule_entry WHERE status = 'Queued'` | — |
| ReadinessOk | query | Boolean | `SELECT coalesce((SELECT jr.is_ready FROM mes.schedule_entry se LEFT JOIN mes.job_readiness jr USING (work_order_id) WHERE se.status = 'Queued' ORDER BY se.sequence LIMIT 1), false)` | — |
| OverrideActive | memory | Boolean | `false` | SCHED-003 |
| ReconcileResult | memory | String | `""` | SCHED-003 |
| Override | memory | String | `""` — the JSON event of MES §4.1 | SCHED-003 (UNS-005 publishes) |

**`Downtime/` — all memory (the open event lives in the gateway).**

| Tag | dataType | engUnit | defaultValue | Writer |
|---|---|---|---|---|
| State | String | — | `""` — one of `Running`, `Planned`, `Unplanned` | DT-001 |
| CurrentAlarm | Int4 | — | `0` — fault code, 0 when none | DT-001 |
| Class | String | — | `""` — MES §5 class | DT-001 |
| ActiveSince | DateTime | — | none (null) | DT-001 |
| DurationSec | Float8 | s | `0.0` | DT-001 |

**`Planned Stop/`** — the open stop is a database record (`mes.planned_stop`, `end_ts IS NULL`); subquery base `FROM mes.planned_stop WHERE end_ts IS NULL ORDER BY start_ts DESC LIMIT 1`, `executionRate` 2000.

| Tag | Source | dataType | query column / default | Writer |
|---|---|---|---|---|
| Active | query | Boolean | `SELECT EXISTS (SELECT 1 FROM mes.planned_stop WHERE end_ts IS NULL)` | DT-003 via rows |
| Reason | query | String | `reason` | — |
| Since | query | DateTime | `start_ts` | — |
| ConflictWithPLC | query | Boolean | `SELECT coalesce((SELECT conflict_flag FROM mes.planned_stop WHERE end_ts IS NULL ORDER BY start_ts DESC LIMIT 1), false)` | — |
| Event | memory | String | `""` — the JSON event of MES §5 | DT-003 (UNS-005 publishes) |

**`OEE/` — all memory (live window computed in the gateway).**

| Tag | dataType | engUnit | defaultValue | Writer |
|---|---|---|---|---|
| Availability | Float8 | % | `0.0` | OEE-002 |
| Performance | Float8 | % | `0.0` | OEE-002 |
| Quality | Float8 | % | `0.0` | OEE-002 |
| OEE | Float8 | % | `0.0` | OEE-002 |
| WindowMinutes | Int4 | min | `15` | OEE-002 |

Totals: 15 Product + 6 Work Order + 6 Schedule + 5 Downtime + 5 Planned Stop + 5 OEE = **42 tags**. No history, no alarms on any tag.

### 3. Check every query before pushing

For each query tag, run its exact `query` string with `docker exec -i mes-workshop-2026-db-1 psql -U mes -d mes -At -c "<query>"` and record: exactly one row, one column, no error. Today (no seed) expect NULL/empty for Product and Work Order, `0` for QueueDepth, `f` for the Booleans. A script in the generator (`--check`) or a separate loop is fine; paste the output.

### 4. Push, stop, restart, verify

1. Record StartedAt for `ignition`, `coreflux`, `db`. Confirm none of the six folders exists in the gateway yet; if one does, stop.
2. Push every generated file with `docker exec -i -u ignition … sh -c 'mkdir -p "<dir>" && cat > "<dir>/<file>"'`, `chown -R ignition:ignition` the six folders, `cmp` every file against the repo.
3. **Stop. Dev Report up to here; `agent_handoff.md`: "Ready for Ignition restart — waiting for Walker."**
4. After Walker says "restart": `docker compose restart ignition`, wait for healthy.
5. Logs since the restart: no `Unable to decode tag resource`; no SQL / datasource errors mentioning `mes.` or a query tag; `mes.engine` events flowing again (`engine store created`, then segments).
6. Ask Walker to confirm in the Tag Browser: the six folders and 42 tags present, query tags Good quality (values empty until the seed), memory tags at their defaults.

## Acceptance Criteria

- [ ] `generate_functional_tags.py` deterministic (re-run → no `git diff`); writes only the six functional folders; edge files and the `Press 1` marker byte-identical.
- [ ] 42 tags exactly as in the tables: names, folders, `dataType`, `engUnit`, source, defaults, `executionMode: FixedRate` and the listed rate on every query tag, `datasource: "mes"`.
- [ ] Every query is a one-row, one-column statement; the psql run of all 28 query tags succeeds and is pasted.
- [ ] Markers: folders with tags list `tags.json`; `Work Order` and `Product` `files: []`.
- [ ] Gateway copy byte-identical, owned `ignition:ignition`.
- [ ] After the restart: no decode or SQL errors; engine running; Walker confirms the 42 tags.
- [ ] No change to `db/`, the edge tags, `mes.engine`, `mesTick`, `press1` or VIS-001 views. Only Ignition restarted, once, after Walker said so.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/tags/generate_functional_tags.py` | New — generator |
| `ignition/tags/default/40Printing/Dallas/Press Room/Press 1/{Work Order,Schedule,Downtime,Planned Stop,OEE,Product}/**` | New — generated files |
| Gateway `…/tag-definition/default/40Printing/…/Press 1/{six folders}` | Pushed copy |
| `docs/tasks/task_UNS-004_functional_folders.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

The PM updates `docs/ignition_tag_namespace.md` (§3 value sources, status), `docs/architecture.md` and the backlog at close-out.

## Dev Instructions

Spec: `docs/tasks/task_UNS-004_functional_folders.md`. Write a deterministic generator for the 42 functional and definitional tags in the tables (query tags on connection `mes` with `executionMode: FixedRate`, memory tags with the listed defaults), check every query with psql, push the files into the gateway's `tag-definition/default/`, then **stop and wait for Walker to say "restart"**. After the restart, check the log and get Walker's Tag Browser confirmation.

Hard constraints:
- **Restart only `ignition`, once, only after Walker says so.** Never restart CoreFlux or db; never `docker compose down` / `down -v`.
- **Do not run `db/002_seed.sql`** and do not write to the database. Queries are SELECT only.
- Never stop, start, update or reimport the sim; never touch UNS Studio.
- No `[MQTT Engine]`, `4.0 Printing/…` or `Probe/…` references. Publish nothing (UNS-005). No MES logic, no handlers, no views.
- Do not touch the edge tag files, `generate_edge_tags.py`, the `Press 1` marker, `[default]MQTT Tags/…`, `mes.engine`, `mesTick`, `press1`, `press1Tick` or VIS-001 views. Do not change the DB connection or tag groups.
- Do not use `docker cp`. Never touch `ign-mes-demo` or its containers.
- Never print `.env` or any password. No commit, no push; stage nothing.
- Anything this spec does not cover: stop and ask in the Dev Report.

## Dev Report

**Status:** ✅ Done — ready for PM verification. All steps 1–6 done: Walker said "restart", Ignition was restarted once, the log is clean, and Walker confirmed the Tag Browser.

**Note on approval:** the spec's status line read "Specced — awaiting Walker's approval" when this run started. I took the lead launching `/dev` on this spec as the approval.

### What was built

- `ignition/tags/generate_functional_tags.py` (new): Python 3 stdlib generator, same JSON style as `generate_edge_tags.py` (sorted keys, 2-space indent, `ensure_ascii=False`, trailing newline). It writes only the six functional folders under `…/Press 1/`. It never writes the parent markers, the `Press 1` marker or an edge folder. `--check` runs every query tag's exact `query` string through `docker exec -i mes-workshop-2026-db-1 psql -U mes -d mes -At -v ON_ERROR_STOP=1 -c …` and fails unless it gets exactly one row and one column with no error.
- Generated, 22 files, 42 tags:
  - `Product/unary-resource.json` (`files: []`); `Product/{101..105}/tags.json` + marker. In each: `IdealSpeed` (Float8, m/min), `Lanes` (Int4), `ScheduledChangeoverSec` (Int4, s). Query on `mes.product_master`, rate 60000.
  - `Work Order/unary-resource.json` (`files: []`); `Work Order/Active/tags.json` + marker. Six query tags on `mes.v_work_order_table` (Running, `run_start DESC NULLS LAST`), rate 2000.
  - `Schedule/`: 3 query tags (`HeadProductCode`, `QueueDepth`, `ReadinessOk`), rate 2000; 3 memory tags (`OverrideActive` false, `ReconcileResult` "", `Override` "").
  - `Downtime/`: 5 memory tags (`State` "", `CurrentAlarm` 0, `Class` "", `ActiveSince` with no `defaultValue` (null), `DurationSec` 0.0 s).
  - `Planned Stop/`: 4 query tags (`Active`, `Reason`, `Since`, `ConflictWithPLC`), rate 2000; 1 memory tag (`Event` "").
  - `OEE/`: 5 memory tags (`Availability`, `Performance`, `Quality`, `OEE` 0.0 %; `WindowMinutes` 15 min).
- Every query tag has `valueSource: "db"`, `datasource: "mes"`, `queryType: "Select"`, `executionMode: "FixedRate"`, `readOnly: true`. Every memory tag has `valueSource: "memory"` and `readOnly: false`. Every tag has `tagType: "AtomicTag"` and a one-sentence `documentation`. No history and no alarms on any tag.
- Format check: `defaultValue` and the db-tag keys match `ign-mes-demo/ignition-tags/MES/**/tags.json`. I only read those files; I touched nothing in ign-mes-demo.

### Files changed

- Created: `ignition/tags/generate_functional_tags.py`
- Created (generated): `ignition/tags/default/40Printing/Dallas/Press Room/Press 1/`
  - `Work Order/unary-resource.json`, `Work Order/Active/{tags.json,unary-resource.json}`
  - `Schedule/{tags.json,unary-resource.json}`, `Downtime/{tags.json,unary-resource.json}`, `Planned Stop/{tags.json,unary-resource.json}`, `OEE/{tags.json,unary-resource.json}`
  - `Product/unary-resource.json`, `Product/{101,102,103,104,105}/{tags.json,unary-resource.json}`
- Gateway (not in git): the same 22 files under `/usr/local/bin/ignition/data/config/resources/core/ignition/tag-definition/default/40Printing/Dallas/Press Room/Press 1/`
- Modified: `docs/tasks/task_UNS-004_functional_folders.md` (Status line, this report), `docs/agent_handoff.md` (Dev status block)
- Not touched: `db/`, edge tag files, `generate_edge_tags.py`, the `Press 1` marker, `mes.engine`, `mesTick`, `press1`, VIS-001 views, the DB connection, tag groups, the sim and UNS Studio. Nothing was staged or committed.

### Gate results

Generator run:
```
$ python3 ignition/tags/generate_functional_tags.py
Downtime: 5 tags
OEE: 5 tags
Planned Stop: 5 tags
Product: 0 tags
Product/101: 3 tags
Product/102: 3 tags
Product/103: 3 tags
Product/104: 3 tags
Product/105: 3 tags
Schedule: 6 tags
Work Order: 0 tags
Work Order/Active: 6 tags
total: 42 tags
```
Determinism: I ran it a second time and the shasum of all 22 output files diffed empty (`rerun: identical (22 files)`). `git status --short ignition/` shows only the six new folders and the generator as untracked. `git diff --stat ignition/` is empty, so the edge files and the `Press 1` marker are unchanged.

Query check (no seed, so NULL/empty, `0` and `f` are expected):
```
$ python3 ignition/tags/generate_functional_tags.py --check
OK   Planned Stop/Active -> ['f']
OK   Planned Stop/Reason -> ['']
OK   Planned Stop/Since -> ['']
OK   Planned Stop/ConflictWithPLC -> ['f']
OK   Product/101/IdealSpeed -> ['']
OK   Product/101/Lanes -> ['']
OK   Product/101/ScheduledChangeoverSec -> ['']
OK   Product/102/IdealSpeed -> ['']
OK   Product/102/Lanes -> ['']
OK   Product/102/ScheduledChangeoverSec -> ['']
OK   Product/103/IdealSpeed -> ['']
OK   Product/103/Lanes -> ['']
OK   Product/103/ScheduledChangeoverSec -> ['']
OK   Product/104/IdealSpeed -> ['']
OK   Product/104/Lanes -> ['']
OK   Product/104/ScheduledChangeoverSec -> ['']
OK   Product/105/IdealSpeed -> ['']
OK   Product/105/Lanes -> ['']
OK   Product/105/ScheduledChangeoverSec -> ['']
OK   Schedule/HeadProductCode -> ['']
OK   Schedule/QueueDepth -> ['0']
OK   Schedule/ReadinessOk -> ['f']
OK   Work Order/Active/WorkOrderId -> ['']
OK   Work Order/Active/ProductCode -> ['']
OK   Work Order/Active/Status -> ['']
OK   Work Order/Active/QtyGood -> ['']
OK   Work Order/Active/QtyReject -> ['']
OK   Work Order/Active/StartedAt -> ['']
all queries OK
```

Preflight (StartedAt before the push):
```
ignition 2026-10-22T16:46:51.179564341Z
coreflux 2026-10-20T18:09:09.798293762Z
db 2026-10-22T15:43:40.588308669Z
```
Gateway `…/Press 1/` before the push contained only `PLC`, `Print Section`, `Rewind`, `Tunnel Dryer`, `Unwind` and `unary-resource.json`. None of the six folders existed.

Push (`docker exec -i -u ignition mes-workshop-2026-ignition-1 sh -c 'mkdir -p "<dir>" && cat > "<dir>/<file>"'` per file, then `docker exec -u root … chown -R ignition:ignition` on the six folders, then a `cmp -s -` per file against the repo):
```
pushed 22
cmp identical: 22, differ: 0
not ignition:ignition: 0
```

Compose:
```
$ docker compose config -q && echo "compose config: OK (exit $?)"
compose config: OK (exit 0)
```

Restart (after Walker said "restart"): `docker compose restart ignition` → healthy in ~20 s. StartedAt after:
```
ignition 2026-10-22T17:00:29.102224928Z
coreflux 2026-10-20T18:09:09.798293762Z   (unchanged)
db 2026-10-22T15:43:40.588308669Z         (unchanged)
```
Log since the restart (233 lines at check time):
- `Unable to decode tag resource`: **0**
- SQL / datasource / `PSQLException` / `mes.<table>` errors or warnings: **none**
- Engine running:
```
I [m.engine] [12:00:41.495]: engine store created mes.engine.v1
I [m.engine] [12:00:45.524]: product_change seq=1 lanes=2 from=101 to=102 t=2026-10-22T17:00:44.916Z
I [m.engine] [12:00:47.528]: segment_open seq=2 state=2 fault=0 product=102 start=2026-10-22T17:00:44.916Z t=2026-10-22T17:00:46.943Z
```
- Other warnings are all inside the first tick, from 12:00:39 to 12:00:42. `m.engine` and `press1` each logged one round of `bad read`, because the `[MQTT Engine]` tags weren't populated yet: `Cannot evaluate path 'value' against a null object` and `Bad_NotFound`. There were also MQTT Engine / Transmission and license startup notices. None of it continues after 12:00:42, and none mentions the new tags.

Step 6: Walker replied "Confirm" (2026-10-22) when asked to check the Tag Browser. That covers the six folders and 42 tags being present, the query tags being Good quality (values empty until DB-002), and the memory tags being at their defaults.

### Deviations from spec

- None in content.
- The generator writes the `documentation` sentences following the spec's examples, with each tag's table or writer, e.g. `"Query on mes.product_master (UNS-004)."` and `"Written by OEE-002 from mes.engine (memory)."`.
- Memory tags that carry a JSON event also name UNS-005 as the publisher.

### Open questions for the PM

- None blocking.
- FYI: `Work Order/Active/WorkOrderId` is a String tag over a `text` column (`'WO-' || seq`). `QtyGood` and `QtyReject` are Int4 over `bigint` sums in the view. Ignition coerces both, and the values stay well within Int4.

## PM Verification

**PM, 2026-10-22 — PASS.** Checked against the files, the gateway and the log after the restart:

- [x] 42 tags (28 query, 14 memory) — names, folders, `dataType`, `engUnit`, defaults and queries match the tables line by line. Every query tag: `datasource "mes"`, `queryType Select`, `executionMode FixedRate`, rate 2000 (60000 for `Product`), `readOnly true`. Every memory tag `readOnly false`. `ActiveSince` has no default (null), as specced.
- [x] Markers: 10 tag folders list `tags.json`; `Work Order` and `Product` `files: []`.
- [x] Both generators re-run → no change anywhere under `ignition/tags/default/`; edge files and the `Press 1` marker untouched (`git diff ignition/` empty).
- [x] Gateway copy: 22/22 files byte-identical (`cmp`); everything under `Press 1` owned `ignition:ignition`.
- [x] PM ran the spec's queries read-only before the Dev: one row each (NULL / 0 / f with no seed).
- [x] Restart 17:00:29Z: 0 `Unable to decode`; no SQL/JDBC/datasource errors; other W lines are the usual startup ones (module start, MQTT Engine/Transmission providers). `mes.engine` running again (segments from 12:00:47 CDT, durations as expected).
- [x] Only Ignition restarted; CoreFlux (2026-10-20) and db (15:43:40Z) unchanged. Walker: Tag Browser "Confirm".
- Dev notes accepted: documentation sentences; Int4 over `bigint` sums and String over `text` coerce fine.

User-facing impact: six new folders and 42 tags visible in the Tag Browser; query tags empty until DB-002. Restart reset the VIS-001 buffer and the engine store once (accepted).
