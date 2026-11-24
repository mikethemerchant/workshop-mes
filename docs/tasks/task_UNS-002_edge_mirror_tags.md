# UNS-002 — Edge mirror: the 16 press tags in `[default]` (DEC-007)

**Status:** ✅ Complete — 2026-10-22, verified by the PM, approved by Walker
**Priority:** P0 · **Effort:** S (one Dev session; stop before the restart)
**Depends on:** INFRA-001 ✅, DEC-005 ✅, DEC-007 ✅
**Bug:** —

---

## Background

First step of the Day 2 MES build, shown live. DEC-007: the MES reads and writes a tag tree in Ignition's `[default]` provider that mirrors the sim hierarchy, never `[MQTT Engine]` directly. This task builds the edge half of that tree: the 16 raw sim tags, each mapped from its MQTT topic, envelope unwrapped, typed and cast. Afterwards students see real typed tags in the Designer Tag Browser under `[default]40Printing/Dallas/Press Room/Press 1/`. No database, no MES logic.

**Walker's rulings (2026-10-22):** the requirement is the mapping of each MQTT topic into the namespace; Reference tags are not required. The Dev generates the tags (no Designer clicking). One Ignition restart is OK, at the moment Walker says.

## Preflight findings (PM, 2026-10-22, read-only)

**a. Method — proven in `ign-mes-demo` (8.3.9), same 8.3 layout here.** Tag definitions are files under
`/usr/local/bin/ignition/data/config/resources/core/ignition/tag-definition/default/` in `mes-workshop-2026-ignition-1` (named volume `ignition-data`, owner `ignition:ignition`, uid 2003). Each folder holds:
- `tags.json` — a JSON **array** of tag objects (atomic tags of that folder only).
- `unary-resource.json` — marker that **must list `tags.json`**, or Ignition silently ignores the tags:
  `{"scope":"G","version":1,"restricted":false,"overridable":true,"files":["tags.json"],"attributes":{"config":{}}}`
  A folder with no tags of its own has `"files": []`.

Subfolders are found by directory. **Tag config is read only at gateway start** — no hot reload. A malformed file logs `Unable to decode tag resource provider=default` at startup; a clean load logs nothing.

**b. Our MQTT Engine differs from ign-mes-demo.** Here `jsonPayload: false` (DEC-002, VIS-001 depends on it): each topic is **one String tag holding the whole envelope** at `[MQTT Engine]40Printing/Dallas/Press Room/Press 1/<sub>`. There is no `/value` child. So every expression unwraps with `jsonGet(…, "value")` first. Do **not** change the MQTT Engine config.

**c. Existing `[default]` content:** `default/unary-resource.json` and a folder `default/MQTT Tags/PLC 1` (Oct 20). **Do not modify or delete either.** `default/40Printing` does not exist yet (Dev re-checks).

**d. Ignition docs (8.3):** `jsonGet(json, path)` returns the value at the path (Object). `toInt` returns a 32-bit int and rounds ("33.9" → 34). The tag system coerces the expression result into the tag's `dataType`.

**e. Docker pitfalls from ign-mes-demo:** `docker cp` into an existing directory nests it (`X/X`) silently. Write each file with `docker exec -i … sh -c 'cat > "<path>"' < file` instead. `chown -R ignition:ignition` after writing.

## What Already Exists (Do Not Rewrite)

- `[MQTT Engine]` provider, MQTT Engine server `MES Coreflux` and custom namespace `All` — unchanged.
- Project `mes-2026` (`ignition/projects/mes-2026/`): `press1` library, `press1Tick` timer, `/press1` view (VIS-001) — **unchanged**. Their in-memory trend buffer resets on the restart; that is accepted.
- `[default]MQTT Tags/…` and `default/unary-resource.json` — untouched.

## What to Build

### 1. Generator (committed, deterministic)

`ignition/tags/generate_edge_tags.py` — plain Python 3, standard library only. Writes the tree below under `ignition/tags/default/` (repo copy = source of truth). Re-running it produces byte-identical output (sorted keys, 2-space indent, trailing newline).

```
ignition/tags/default/40Printing/unary-resource.json                                  files: []
ignition/tags/default/40Printing/Dallas/unary-resource.json                           files: []
ignition/tags/default/40Printing/Dallas/Press Room/unary-resource.json                files: []
ignition/tags/default/40Printing/Dallas/Press Room/Press 1/unary-resource.json        files: []
ignition/tags/default/40Printing/Dallas/Press Room/Press 1/<Folder>/tags.json
ignition/tags/default/40Printing/Dallas/Press Room/Press 1/<Folder>/unary-resource.json   files: ["tags.json"]
```
`<Folder>` = `PLC`, `Print Section`, `Unwind`, `Rewind`, `Tunnel Dryer` — exactly the sim's level names.

### 2. The 16 tags

Each is:
```json
{ "name": "<Name>", "tagType": "AtomicTag", "valueSource": "expr", "expressionType": "Expression",
  "dataType": "<type>", "expression": "<cast>(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/<Folder>/<Name>}, \"value\"))",
  "engUnit": "<units or omit>", "documentation": "Edge mirror of MQTT topic 40Printing/Dallas/Press Room/Press 1/<Folder>/<Name> (UNS-002).",
  "readOnly": true }
```
No history, no alarms, no scale.

| Folder | Name | dataType | cast | engUnit |
|---|---|---|---|---|
| PLC | Connected | Boolean | `toBoolean` | — |
| PLC | MachineState | Int4 | `toInt` | — |
| PLC | FaultCode | Int4 | `toInt` | — |
| PLC | ProductCode | Int4 | `toInt` | — |
| PLC | WebLanes | Int4 | `toInt` | — |
| PLC | GoodMeters | Int4 | `toInt` | m |
| PLC | RejectMeters | Int4 | `toInt` | m |
| PLC | RTOInhibit | Boolean | `toBoolean` | — |
| Unwind | WebTension | Float8 | `toFloat` | lbf |
| Rewind | WebTension | Float8 | `toFloat` | lbf |
| Print Section | LineSpeed | Float8 | `toFloat` | m/min |
| Print Section | RegistrationError | Float8 | `toFloat` | mm |
| Print Section | InkViscosity | Float8 | `toFloat` | s |
| Tunnel Dryer | Zone1Temp | Float8 | `toFloat` | °F |
| Tunnel Dryer | Zone2Temp | Float8 | `toFloat` | °F |
| Tunnel Dryer | Zone3Temp | Float8 | `toFloat` | °F |

Names and units come from `docs/sim_spec_press1.md` §5 — re-check against it. Meters are cumulative counters; deltas and restart handling are ENG-001, not this task.

### 3. Push to the gateway (no restart)

1. Record `docker inspect -f '{{.State.StartedAt}}'` for `ignition`, `coreflux`, `db`.
2. Confirm `…/tag-definition/default/40Printing` does not exist. If it does, stop and report.
3. For each generated file: `docker exec -i -u ignition mes-workshop-2026-ignition-1 sh -c 'mkdir -p "<dir>" && cat > "<dir>/<file>"' < "<local file>"`. Quote paths (they contain spaces).
4. `docker exec -u root mes-workshop-2026-ignition-1 chown -R ignition:ignition "…/tag-definition/default/40Printing"`.
5. Diff every pushed file against the repo copy (`docker exec … cat` | `diff -`). All identical.
6. **Stop. Fill in the Dev Report up to here and write in `agent_handoff.md`: "Ready for Ignition restart — waiting for Walker."**

### 4. Restart and verify — only after Walker says "restart" in the Dev session

1. `docker compose restart ignition`. Wait for `healthy`.
2. Gateway log since the restart (`docker compose logs --since <restart time> ignition`): no `Unable to decode tag resource`, no new errors mentioning `40Printing`. Paste the grep output.
3. `coreflux` and `db` StartedAt unchanged.
4. Ask Walker to open the Designer Tag Browser and confirm: 16 tags, Good quality, typed values changing every ~2 s (the Dev cannot read live tag values without auth — ign-mes-demo finding). Record his answer.
5. Confirm http://localhost:8089/data/perspective/client/mes-2026/press1 loads and its buffer refills (Walker eyeball is enough).

## Acceptance Criteria

- [ ] `ignition/tags/generate_edge_tags.py` exists; running it twice gives no `git diff` in `ignition/tags/default/`.
- [ ] Exactly 16 tags in 5 `tags.json` files; names, folders, `dataType`s and units match the table and Sim Spec §5.
- [ ] Every expression reads `[MQTT Engine]40Printing/…` and wraps `jsonGet(…, "value")`. `grep -r "4.0 Printing\|Probe/" ignition/tags/` → nothing.
- [ ] Every folder that has `tags.json` has a marker listing `tags.json`; the four parent levels have `"files": []`.
- [ ] Gateway copy byte-identical to the repo copy; owned by `ignition:ignition`.
- [ ] `[default]MQTT Tags/…`, `default/unary-resource.json`, the `press1` library, `press1Tick` and the VIS-001 views unchanged (`git diff ignition/projects/` empty).
- [ ] After the restart: no tag decode errors in the log; Walker confirms 16 Good, typed, live tags in the Tag Browser.
- [ ] Only Ignition restarted; CoreFlux and db StartedAt unchanged. The sim was not touched.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/tags/generate_edge_tags.py` | New — generator |
| `ignition/tags/default/40Printing/**` | New — generated tag tree (9 marker files, 5 `tags.json`) |
| Gateway `…/tag-definition/default/40Printing/**` | Pushed copy (not in git) |
| `docs/tasks/task_UNS-002_edge_mirror_tags.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

Contract docs (`docs/ignition_tag_namespace.md` status, `architecture.md`) are updated by the PM at close-out.

## Dev Instructions

Spec: `docs/tasks/task_UNS-002_edge_mirror_tags.md`. Write a deterministic Python generator that emits Ignition 8.3 tag resource files for the 16 edge tags under `ignition/tags/default/40Printing/Dallas/Press Room/Press 1/{PLC,Print Section,Unwind,Rewind,Tunnel Dryer}/`, each an expression tag `cast(jsonGet({[MQTT Engine]…}, "value"))` with the type in the table. Push the files into the gateway's `tag-definition/default/` with `docker exec … cat >`, verify them, then **stop and wait for Walker to say "restart"**. After the restart, check the log and get Walker's Tag Browser confirmation.

Hard constraints:
- **Restart only `ignition`, only once, only after Walker says so.** Never restart CoreFlux or db. Never `docker compose down`, `down -v`, or touch any volume beyond writing the new `40Printing` folder.
- Never stop, start, update or reimport the sim; never touch UNS Studio.
- Never read or reference `[MQTT Engine]4.0 Printing/…` or `Probe/…` tags.
- Do not change the MQTT Engine config (`jsonPayload` stays false), MQTT Transmission, or the `[MQTT Engine]` provider. Publish nothing.
- Do not touch `[default]MQTT Tags/…`, `default/unary-resource.json`, the `press1` library, `press1Tick` or any VIS-001 view.
- Do not use `docker cp` for the push (silent nesting). Never touch `ign-mes-demo` or its containers.
- Never print `.env` or any password. No commit, no push; stage nothing — the PM commits after Walker approves.
- Anything this spec does not cover: stop and ask in the Dev Report.

## Dev Report

**Status:** ✅ Done — ready for PM verification. All steps 1–4 done; Walker confirmed the Tag Browser and `/press1` checks.

### What was built
- `ignition/tags/generate_edge_tags.py` — Python 3, stdlib only. One `TAGS` table (folder, name, dataType, cast, engUnit) copied from the spec table and re-checked against `docs/sim_spec_press1.md` §5 (names, folders, units all match). Writes the four parent markers (`"files": []`), then per folder a `tags.json` (tags sorted by name) and a marker listing `tags.json`. JSON: `sort_keys`, 2-space indent, UTF-8 (`°F` written literally, `ensure_ascii=False`), trailing newline. Each tag has exactly the spec's keys; `engUnit` is omitted where the table says —.
- `ignition/tags/default/40Printing/**` — generated tree: 9 `unary-resource.json` + 5 `tags.json` = 14 files, 16 tags (PLC 8, Print Section 3, Tunnel Dryer 3, Unwind 1, Rewind 1).
- Gateway copy pushed into `…/tag-definition/default/40Printing/` with `docker exec -i -u ignition … sh -c 'mkdir -p … && cat > …'` (no `docker cp`), then `chown -R ignition:ignition`.

### Files changed
- Created: `ignition/tags/generate_edge_tags.py`
- Created: 14 files under `ignition/tags/default/40Printing/` (listed below)
- Modified: `docs/tasks/task_UNS-002_edge_mirror_tags.md` (Status line, this report), `docs/agent_handoff.md` (Dev status)
- Gateway (not in git): 14 files under `/usr/local/bin/ignition/data/config/resources/core/ignition/tag-definition/default/40Printing/`

### Gate results
StartedAt before any change (unchanged after the push):
```
ignition 2026-10-20T19:08:55.765749547Z
coreflux 2026-10-20T18:09:09.798293762Z
db 2026-10-22T15:43:40.588308669Z
```
Pre-push check: `test -e …/default/40Printing` → `absent: ok`.

Generator determinism (files are untracked, so compared by checksum instead of `git diff`): ran twice, `shasum` of all 14 files → `IDENTICAL`.
```
PLC: 8 tags
Print Section: 3 tags
Rewind: 1 tags
Tunnel Dryer: 3 tags
Unwind: 1 tags
```
Tag read-back (folder, name, dataType, engUnit, expression):
```
PLC Connected Boolean — toBoolean(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/PLC/Connected}, "value"))
PLC FaultCode Int4 — toInt(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/PLC/FaultCode}, "value"))
PLC GoodMeters Int4 m toInt(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/PLC/GoodMeters}, "value"))
PLC MachineState Int4 — toInt(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/PLC/MachineState}, "value"))
PLC ProductCode Int4 — toInt(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/PLC/ProductCode}, "value"))
PLC RTOInhibit Boolean — toBoolean(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/PLC/RTOInhibit}, "value"))
PLC RejectMeters Int4 m toInt(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/PLC/RejectMeters}, "value"))
PLC WebLanes Int4 — toInt(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/PLC/WebLanes}, "value"))
Print Section InkViscosity Float8 s toFloat(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/Print Section/InkViscosity}, "value"))
Print Section LineSpeed Float8 m/min toFloat(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/Print Section/LineSpeed}, "value"))
Print Section RegistrationError Float8 mm toFloat(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/Print Section/RegistrationError}, "value"))
Rewind WebTension Float8 lbf toFloat(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/Rewind/WebTension}, "value"))
Tunnel Dryer Zone1Temp Float8 °F toFloat(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/Tunnel Dryer/Zone1Temp}, "value"))
Tunnel Dryer Zone2Temp Float8 °F toFloat(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/Tunnel Dryer/Zone2Temp}, "value"))
Tunnel Dryer Zone3Temp Float8 °F toFloat(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/Tunnel Dryer/Zone3Temp}, "value"))
Unwind WebTension Float8 lbf toFloat(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/Unwind/WebTension}, "value"))
total 16
```
`grep -r "4.0 Printing\|Probe/" ignition/tags/` → no output, exit 1.

Gateway vs repo, `docker exec … cat "<file>" | diff -q - "<file>"` for each file:
```
identical: 40Printing/Dallas/Press Room/Press 1/PLC/tags.json
identical: 40Printing/Dallas/Press Room/Press 1/PLC/unary-resource.json
identical: 40Printing/Dallas/Press Room/Press 1/Print Section/tags.json
identical: 40Printing/Dallas/Press Room/Press 1/Print Section/unary-resource.json
identical: 40Printing/Dallas/Press Room/Press 1/Rewind/tags.json
identical: 40Printing/Dallas/Press Room/Press 1/Rewind/unary-resource.json
identical: 40Printing/Dallas/Press Room/Press 1/Tunnel Dryer/tags.json
identical: 40Printing/Dallas/Press Room/Press 1/Tunnel Dryer/unary-resource.json
identical: 40Printing/Dallas/Press Room/Press 1/unary-resource.json
identical: 40Printing/Dallas/Press Room/Press 1/Unwind/tags.json
identical: 40Printing/Dallas/Press Room/Press 1/Unwind/unary-resource.json
identical: 40Printing/Dallas/Press Room/unary-resource.json
identical: 40Printing/Dallas/unary-resource.json
identical: 40Printing/unary-resource.json
```
Ownership of every file and dir under gateway `40Printing`: `23 ignition:ignition` (14 files + 9 dirs). No nesting: `…/40Printing/40Printing` → `No such file or directory`; 14 files in the gateway tree.

Untouched: gateway `default/unary-resource.json` and `MQTT Tags/PLC 1/*` still dated 2026-10-20. `git diff --stat ignition/projects/` → empty. `docker compose config -q` → `compose config OK`.

### Step 4 — restart (Walker said "restart", 2026-10-22)
`docker compose restart ignition` at `2026-10-22T16:21:48Z` → healthy within ~20 s.
```
ignition 2026-10-22T16:21:51.640542841Z healthy
coreflux 2026-10-20T18:09:09.798293762Z healthy   (unchanged)
db 2026-10-22T15:43:40.588308669Z healthy         (unchanged)
```
Log `docker compose logs --since 2026-10-22T16:21:48Z ignition` (319 lines). Note: this log tags levels as ` E [` / ` W [`, not `ERROR`/`WARN`.
- `grep -i "Unable to decode"` → no output.
- `grep -i 40Printing` → 32 lines, none from the tag system:
  - 16 × `E [c.c.m.e.g.EngineCallback] … 11:21:50 … Client shutting down - not handling message arrived on topic 40Printing/…` — the **old** JVM shutting down (shutdown began 11:21:49), one per sim topic. Expected.
  - 16 × `W [press1] … 11:22:03 … bad read Bad_NotFound("Path '[MQTT Engine]40Printing/…' not found.")` plus one `all reads bad` — the VIS-001 `press1Tick` timer firing once before MQTT Engine connected at 11:22:04 (`ignition-client: Connected to tcp://coreflux:1883`). Not repeated after that.
- E/W lines after 11:22:04, excluding the license (HASP) and Sparkplug-discard lines: **none**.
- E count by logger for the whole window: `16 c.c.m.e.g.EngineCallback` (the shutdown lines above) — nothing else.

Gateway files re-diffed against the repo after the restart: all 14 still byte-identical (Ignition did not rewrite them).

4.4 / 4.5 — Walker, 2026-10-22, in the Dev session, after being asked to check (a) the Designer Tag Browser for 16 tags in 5 folders, Good quality, typed values changing every ~2 s, and (b) that `/press1` loads and its trends refill. His answer: **"Both passed"**.

### Deviations from spec
- Determinism checked by checksum rather than `git diff`, because `ignition/tags/` is untracked until the PM commits. Re-run with `git diff` once committed.
- The existing gateway markers have no trailing newline and unsorted keys (Ignition's own writer); ours are sorted with a trailing newline, per spec. Ignition reads both; re-diffed after the restart: unchanged.

### Open questions for the PM
- None blocking. Untested until the restart: whether Ignition's tag system parses an MQTT Engine envelope String through `jsonGet` with Good quality — Walker's Tag Browser check in step 4 is the proof.

## PM Verification

**PM, 2026-10-22 — PASS.** Checked before and after the restart, against the files and the gateway, not the report:

- [x] Generator present, stdlib only; re-run in a scratch copy → `diff -r` identical. (`git diff` re-check after commit.)
- [x] 16 tags, 5 `tags.json`; names, folders, `dataType`, `engUnit` match the spec table and Sim Spec §5. Keys per tag: name, tagType, valueSource `expr`, expressionType, dataType, expression, documentation, readOnly (+engUnit).
- [x] Every expression `cast(jsonGet({[MQTT Engine]40Printing/…}, "value"))`; no `4.0 Printing` / `Probe/`.
- [x] Markers: 5 list `tags.json`, 4 parents `[]`.
- [x] Gateway copy: 14/14 files byte-identical (`cmp`), owned `ignition:ignition`, no nesting.
- [x] `[default]MQTT Tags/…` and `default/unary-resource.json` untouched; `git diff ignition/projects/` empty.
- [x] After restart (16:21:51Z): `Unable to decode` count 0; no E/W lines after MQTT Engine connected (excluding licence/Sparkplug). The 16 `press1` Bad_NotFound warnings at 11:22:03 CDT are the VIS-001 timer firing once before MQTT Engine reconnected at 11:22:04 — expected, not repeated.
- [x] Only Ignition restarted: CoreFlux StartedAt 2026-10-20, db 15:43:40Z (Step 0) unchanged.
- [x] Walker: Tag Browser 16 Good, typed, live tags and `/press1` reloads — "Both passed".
- Dev deviations accepted: determinism by checksum (untracked files); our markers are sorted with a trailing newline, Ignition's own are not — both load.

User-facing impact: students see `[default]40Printing/Dallas/Press Room/Press 1/{PLC,Print Section,Unwind,Rewind,Tunnel Dryer}` with 16 typed live tags. VIS-001 trend buffer restarted empty once (accepted).
