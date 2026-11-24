# ENG-001 — MES engine: `mes.engine` library, 2 s gateway timer, `PLC/SampleTime` tag

**Status:** ✅ Complete — 2026-10-22, verified by the PM, approved by Walker
**Priority:** P0 · **Effort:** M (one Dev session; stop before the restart; verified by an offline replay, not a live 17-minute loop)
**Depends on:** UNS-002 ✅, DEC-007 ✅
**Bug:** —

---

## Background

Every Core Four function (Work Orders, Scheduling, Downtime, OEE) needs the same thing from the press: one clean sample every 2 s, state changes with the 2-sample debounce, durations from the sim's own sample times, and meter deltas that survive a sim restart. ENG-001 builds that once, as the shared core, in a new `mes` project library. It holds the ingest state in gateway memory (Walker, 2026-10-22: not in the database). It writes no database rows and no functional tags yet. Those come in UNS-004, WO-001, DT-001 and OEE-001, which plug into this engine.

**Walker's rulings (2026-10-22):** ENG-001 is its own task. Sample time comes from a new edge tag, `PLC/SampleTime` (option A), which costs one more Ignition restart at the moment Walker says. **No 17-minute live wait:** the detection logic is proven by replaying the sim definition offline; the live check after the restart is a few minutes.

## Preflight findings (PM, 2026-10-22, read-only)

- **Why `SampleTime`:** the UNS-002 edge tags carry only `value`. An Ignition tag's timestamp moves only when its value changes, so it cannot de-duplicate samples or time a stop. The sim's sample time is the `timestamp` field of each envelope (ISO 8601 UTC with microseconds, e.g. `2026-10-20T19:00:51.377788Z`). VIS-001 de-duplicates on the **MachineState** envelope timestamp; ENG-001 does the same, through the new tag.
- **Tag files load only at gateway start; projects are not watched.** 8.3 does not watch the project folder (VIS-001 Dev Report). The one restart in this task loads both the new tag and the new library and timer.
- **Patterns to copy (read only, do not import or edit):** `ignition/projects/mes-2026/ignition/script-python/press1/code.py` — `_store()` with a versioned `getGlobals()` key, `_warn_once`, `_epoch_ms` via `java.time.Instant`, `_meter_delta`, `_events` (debounce, backdate, close on first differing sample), the `GAP_S` guard in `tick()`. Timer resource format: `ignition/projects/mes-2026/ignition/timer/press1Tick/`. Match that file's style (tabs, comment density, naming).
- **Live values are not readable by the Dev** (no unauthenticated gateway API). The engine therefore logs every event; the Dev checks the live run from `docker compose logs ignition`.
- **Offline replay source:** `docs/sim/fourzero_printing_press1.json` (byte-for-byte copy of the running sim, commit `11225ed`). 35 phases, `interval_seconds` 2, `duration_seconds` per phase, one entry per topic with a `generator`: `static` (`value`), `counter` (`start`, `step`), `random_walk` (`start`, `step`, `min`, `max`). Phase names show job and fault (`J102 Fault 2 (103 RTO inhibit)`). Counter semantics across phases (cumulative, holds at step 0): Sim Spec §6–§8. Read the file; never edit it.

## What Already Exists (Do Not Rewrite)

- `ignition/tags/generate_edge_tags.py` and its 16 tags (UNS-002). Add one entry; change nothing else.
- `press1` library, `press1Tick` timer, all `Press1/*` views (VIS-001) — **unchanged**. `press1` keeps reading `[MQTT Engine]`.
- `[default]MQTT Tags/…`, `default/unary-resource.json` in the gateway — untouched.

## What to Build

### 1. Edge tag `PLC/SampleTime` (generator change)

Add to `TAGS` in `generate_edge_tags.py` and regenerate:

| Folder | Name | dataType | expression | engUnit |
|---|---|---|---|---|
| PLC | SampleTime | String | `toStr(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/PLC/MachineState}, "timestamp"))` | — |

`documentation`: `"Sim sample time (ISO 8601 UTC) from the PLC/MachineState envelope timestamp (ENG-001)."` The generator needs a small change so this one tag reads the MachineState topic and the `timestamp` key; keep the other 16 byte-identical (`git diff` on their lines must show nothing).

### 2. Library `mes.engine`

File `ignition/projects/mes-2026/ignition/script-python/mes/engine/code.py` plus its `resource.json` (same format as `press1/resource.json`, `files: ["code.py"]`). Package path `mes.engine`, so later tasks add `mes.workorder`, `mes.downtime`, … beside it. No `resource.json` in the `mes/` folder itself.

**Split pure logic from Ignition I/O, so the same file runs in the offline replay:**
- `step(st, sample)` — **all** detection logic (steps 3–8 below). Takes the store dict and one sample dict (`t` already in epoch ms, typed values), mutates `st`, and **returns the list of new events**. No `system.*`, no Java, no logging inside `step` or anything it calls.
- `tick()` — the only I/O: read the tags, parse `t` (`_epoch_ms`, which imports `java.time.Instant` **inside** the function), call `step`, log the returned events, call `_dispatch`.
- Module top level: no Java imports. `log = system.util.getLogger(...)` is allowed (the harness stubs `system`).
- **Source must parse under both Jython 2.7 and Python 3:** `except Exception as e`, no `print` statements, no `basestring`/`long`/`unicode`, no `dict.iteritems`.

Constants:
```
ROOT = '[default]40Printing/Dallas/Press Room/Press 1'   # the only place a tag path starts
TAGS = [(key, sub-path)] for the 17 edge tags: conn, state, fault, product, lanes, good, reject, rto,
        tenU, tenR, speed, reg, visc, z1, z2, z3, t (= PLC/SampleTime)
DEBOUNCE = 2; GAP_S = 10; MAX_EVENTS = 500
STORE_VERSION = 1; STORE_KEY = 'mes.engine.v%d' % STORE_VERSION
log = system.util.getLogger('mes.engine')
```

**`tick()` and `step()`** — the timer calls `tick()`; steps 3–8 live in `step()`:
1. `system.tag.readBlocking` all 17 paths. A bad-quality or `None` value → that key is `None`, `_warn_once`. Values are already typed (UNS-002); do not re-parse them. Parse only `t` with `Instant.parse(...).toEpochMilli()`.
2. If `t` or `state` is `None` → return.
3. **De-dup:** if `t <= last['t']` → return (same sample read twice).
4. **Gap guard:** if `t - last['t'] > GAP_S*1000` → close any open segment at `last['t']`, reset the debounce run, emit `gap`.
5. **Meter deltas:** `dg`, `dr` = current − previous when current ≥ previous; when current < previous it is a sim restart: delta 0 and emit `restart` (once per drop). First sample: deltas 0. Keep `totals = {good, reject}` since engine start (sum of deltas).
6. **Product change:** product differs from the previous sample's product → emit `product_change` (`from`, `to`, `lanes`, `t`).
7. **Segments (debounce):** key = `(state, fault if state == 3 else 0)`. A sample whose key differs from the current run closes the open segment at this sample's `t` (emit `segment_close` with `dur_s` from sample timestamps) and starts a new run. When the run reaches `DEBOUNCE` samples, open a segment backdated to the run's first sample (emit `segment_open`). **Segments open for every state, Running included** (OEE needs running time). A fault-code change inside Faulted is a key change.
8. **Run start:** the first sample with `state == 1` whose previous sample had `state == 2` → emit `run_start` (`product`, `lanes`, `t`). Not debounced: WO-001 needs it "within one sample". Faulted → Running is not a run start.
9. Save the sample as `last`; call `_dispatch(sample, new_events)`.

**Events** — each a dict with `seq` (increasing int), `type`, `t` (epoch ms) and fields above. `segment_open/close` carry `state`, `fault`, `product`, `start`, and on close `end`, `dur_s`. Keep the newest `MAX_EVENTS` in the store. **Log every event at INFO** in one line, e.g.
`segment_close state=3 fault=101 product=102 start=… end=… dur_s=10.0`. Do not log per sample.

**`_dispatch(sample, events)`** — loops over module-level `HANDLERS = []` (empty in this task) and calls each `h(sample, events)` inside its own try/except that logs the traceback. Later tasks append their handler here.

**Read API** (for later tasks and test views; return copies, never the live store):
`latest()` → last sample dict · `events_since(seq)` → list · `open_segment()` → dict or `None` · `totals()` → `{good, reject, since}` · `info()` → `{started, samples, last_t, store_key}`.

Store (`getGlobals()[STORE_KEY]`): `version`, `started`, `samples`, `last`, `run`, `open_segment`, `events`, `seq`, `totals`, `warned`. On first creation log `engine store created <STORE_KEY>`.

### 3. Timer `mesTick`

`ignition/projects/mes-2026/ignition/timer/mesTick/` — `resource.json` identical in shape to `press1Tick` (delay 2000, fixedDelay true, sharedThread false, enabled true) and `handleTimerEvent.py`:
```python
def handleTimerEvent():
	# ENG-001: one Press 1 sample into the MES engine. Never let an exception kill the timer.
	try:
		mes.engine.tick()
	except:
		import traceback
		system.util.getLogger("mes.engine").error("tick failed: " + traceback.format_exc())
```

### 4. Offline replay test (before any push)

`ignition/tests/replay_engine.py` — Python 3, stdlib only, run from the repo root: `python3 -I ignition/tests/replay_engine.py`. It:
1. Loads `code.py` with `exec` into a namespace that has a stub `system` (only `system.util.getLogger` returning a no-op logger and `system.util.getGlobals` returning a dict). It never calls `tick()`.
2. Builds the sample stream from `docs/sim/fourzero_printing_press1.json`: for each phase, `duration_seconds / interval_seconds` samples, `t` advancing 2,000 ms per sample; `static` → value; `counter` → cumulative per Sim Spec §6–§8; `random_walk` → `start` (values are irrelevant to detection). Cast like UNS-002 (int / bool / float).
3. Feeds **two full loops** (70 phases) through `step()` on a fresh store and tallies the events of the **second** loop (the first warms up the debounce and deltas).
4. Asserts and prints, for the second loop:
   - `segment_close` state 3: **15** — fault 101 × 9, total **82 s**; 102 × 4, **48 s**; 103 × 2, **24 s**.
   - `segment_close` state 2: **5**, total **146 s** (18 + 44 + 22 + 34 + 28).
   - Each `segment_close` `dur_s` equals its phase's `duration_seconds` exactly.
   - `run_start`: **5**, products 101 → 102 → 103 → 104 → 105. `product_change`: **5**. `restart`: 0. `gap`: 0.
   - Good-meter deltas over the loop **2,315 m**, reject **155 m** (Sim Spec §6/§9; if the file gives different totals, report both numbers — do not edit the expectation silently).
5. Edge cases, each on a fresh store, each asserted:
   - **Duplicate sample** (same `t` twice) → second one produces no events and no delta.
   - **Sim restart** (meters drop to 0 mid-Run) → one `restart` event, delta 0 on that sample, totals never decrease.
   - **Gap** (next `t` 30 s later) → open segment closed at the last sample before the gap, one `gap` event, debounce restarts.
   - **1-sample blip** (one Faulted sample inside Running) → no Faulted segment opens.
   - **Fault-code change inside Faulted** (101 → 102 without leaving state 3) → close + new segment.
   - **Faulted → Running** → no `run_start`.
6. Exits non-zero on any failed assertion. Paste the full output in the Dev Report.

The test file is committed with the task (`ignition/tests/`), so WO-001, DT-001 and OEE-001 can extend it.

### 5. Push, stop, restart, verify

1. Record StartedAt for `ignition`, `coreflux`, `db`.
2. Push the regenerated `PLC/tags.json` (only file that changes) with `docker exec -i -u ignition … sh -c 'cat > "<path>"'`, `chown`, and `cmp` against the repo. The project files are already in the gateway through the bind mount.
3. **Stop. Dev Report up to here; `agent_handoff.md`: "Ready for Ignition restart — waiting for Walker."**
4. After Walker says "restart": `docker compose restart ignition`; wait for healthy.
5. Logs since the restart: no `Unable to decode tag resource`; `engine store created mes.engine.v1` present; no `tick failed`; no `mes.engine` warnings after MQTT Engine connects (a burst of bad reads in the first seconds before it connects is expected, as with `press1`).
6. **Short live check (about 3 minutes, no full loop).** Watch the log for about 180 s after MQTT Engine connects (extend only until at least one `segment_open` and one `segment_close` have appeared). Paste every `mes.engine` line. Check: events are in `seq` order with no gaps; each `segment_close` `dur_s` is a multiple of 2 s (±0.5 s for sim jitter) and matches its phase in the sim file; no event repeats (de-dup works).
7. Ask Walker to confirm in the Tag Browser that `PLC/SampleTime` is Good and changes every ~2 s, and that `/press1` reloads.

If a tally is off, report the raw lines; do not tune the rules.

## Acceptance Criteria

- [ ] `PLC/SampleTime` generated (String, MachineState envelope `timestamp`); the other 16 tags byte-identical; generator still deterministic (`git diff` clean after a re-run).
- [ ] `mes/engine/code.py`, its `resource.json`, and `timer/mesTick/` exist in the formats above; single `ROOT` on `[default]40Printing/…`; no `[MQTT Engine]`, `4.0 Printing`, `Probe/` or SQL in `mes/` (`grep`).
- [ ] De-dup on SampleTime, gap guard, meter deltas with restart → 0, debounce 2 with backdated start, close on first differing sample, fault-code split, segments for all states, `run_start` on Setup → Running only.
- [ ] Every event logged once at INFO; nothing logged per sample; `HANDLERS` empty; read API returns copies.
- [ ] After the restart: no decode errors, store-created line present, no `tick failed`.
- [ ] `python3 -I ignition/tests/replay_engine.py` passes: second-loop tally exactly as in §4.4, all six edge cases pass. `code.py` parses under Python 3 (the harness proves it) and uses no Jython-only syntax.
- [ ] Short live check: store-created line, ordered events, durations consistent with the sim file, no repeats.
- [ ] `press1` library, `press1Tick`, `Press1/*` views unchanged (`git diff ignition/projects/mes-2026/ignition/script-python/press1 ignition/projects/mes-2026/ignition/timer/press1Tick ignition/projects/mes-2026/com.inductiveautomation.perspective` empty).
- [ ] Only Ignition restarted, once, after Walker said so; CoreFlux and db StartedAt unchanged.

## Files to Modify

| Path | Change |
|---|---|
| `ignition/tags/generate_edge_tags.py` | Add `PLC/SampleTime` |
| `ignition/tags/default/40Printing/Dallas/Press Room/Press 1/PLC/tags.json` | Regenerated (+1 tag) |
| `ignition/projects/mes-2026/ignition/script-python/mes/engine/code.py`, `resource.json` | New |
| `ignition/projects/mes-2026/ignition/timer/mesTick/handleTimerEvent.py`, `resource.json` | New |
| `ignition/tests/replay_engine.py` | New — offline replay test |
| Gateway `…/tag-definition/default/…/PLC/tags.json` | Pushed copy |
| `docs/tasks/task_ENG-001_mes_engine.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

The PM updates `docs/ignition_tag_namespace.md`, `docs/architecture.md` and DEC-007's tag count at close-out.

## Dev Instructions

Spec: `docs/tasks/task_ENG-001_mes_engine.md`. Add the `PLC/SampleTime` edge tag to the UNS-002 generator, and build the `mes.engine` project library and the `mesTick` 2 s gateway timer: read the 17 `[default]` edge tags, de-dup on SampleTime, compute meter deltas with restart handling, debounce state segments, and emit and log events, holding all state in gateway memory. Keep the detection logic in a pure `step()` and prove it with the offline replay test (two loops from the sim file plus six edge cases) **before** pushing. Push the one changed tag file, **stop and wait for Walker to say "restart"**, then do the short live check (about 3 minutes). **Do not wait for a full 17-minute loop.**

Hard constraints:
- **Restart only `ignition`, once, only after Walker says so.** Never restart CoreFlux or db; never `docker compose down` / `down -v`; touch no volume beyond the one pushed tag file.
- Never stop, start, update or reimport the sim; never touch UNS Studio.
- `mes.engine` reads only `[default]40Printing/…`. Never `[MQTT Engine]`, `4.0 Printing/…` or `Probe/…`.
- No database reads or writes. No functional-folder tags. Publish nothing. No Perspective views.
- Do not edit the `press1` library, `press1Tick`, any VIS-001 view, `[default]MQTT Tags/…` or the gateway's `default/unary-resource.json`. Do not change MQTT Engine or Transmission config.
- Do not use `docker cp` for the push. Never touch `ign-mes-demo` or its containers.
- Never print `.env` or any password. No commit, no push; stage nothing.
- Anything this spec does not cover: stop and ask in the Dev Report. Do not tune detection rules to make a tally match.

## Dev Report

**Status:** ✅ Done — ready for PM verification (one finding for the PM: live durations vs the ±0.5 s tolerance, see Open questions)

### What was built

- `ignition/tags/generate_edge_tags.py` — one `TAGS` entry `("PLC", "SampleTime", "String", "toStr", None)` and a `SOURCES` map `(folder, name) → (source topic name, envelope key, documentation)`. `tag()` uses the default `"value"` key and UNS-002 documentation unless the tag is in `SOURCES`, so the other 16 tags are generated exactly as before.
- `…/PLC/tags.json` — regenerated: one tag inserted (alphabetical, between `RTOInhibit` and `WebLanes`). The git diff is a 10-line pure insert; no existing line changed. Expression: `toStr(jsonGet({[MQTT Engine]40Printing/Dallas/Press Room/Press 1/PLC/MachineState}, "timestamp"))`.
- `ignition/projects/mes-2026/ignition/script-python/mes/engine/code.py` (+ `resource.json`, copied from `press1/resource.json`; no `resource.json` in `mes/`):
  - Constants as specced: single `ROOT = '[default]40Printing/Dallas/Press Room/Press 1'`, 17 `TAGS` (key, sub-path), `DEBOUNCE = 2`, `GAP_S = 10`, `MAX_EVENTS = 500`, `STORE_KEY = 'mes.engine.v1'`, `HANDLERS = []`.
  - `step(st, sample)` — pure: de-dup (`t <= last.t`), gap guard (close at `last.t`, reset run, `gap` event), meter deltas with drop → 0 and one `restart` event per drop sample, running `totals`, `product_change`, debounced segments for every state keyed `(state, fault if state == 3 else 0)` with backdated start and close on the first differing sample, `run_start` on Setup → Running only. Returns the new events. Stored sample gets `dg` / `dr`.
  - `tick()` — the only I/O: `readBlocking` the 17 paths, bad quality / `None` → `None` + `_warn_once`, parse only `t` (`java.time.Instant` imported inside `_epoch_ms`), `step()`, log each new event once at INFO, `_dispatch` (each handler in its own try/except with traceback). Nothing logged per sample.
  - Log line format: `segment_close seq=12 state=3 fault=101 product=102 start=<ISO> end=<ISO> dur_s=10.0 t=<ISO>` (times rendered ISO with `Instant.ofEpochMilli` in the I/O layer).
  - Read API, all copies (`copy.deepcopy`): `latest()`, `events_since(seq)`, `open_segment()`, `totals()` → `{good, reject, since}`, `info()` → `{started, samples, last_t, store_key}`.
  - Store keys exactly as specced; `engine store created mes.engine.v1` logged on creation.
- `ignition/projects/mes-2026/ignition/timer/mesTick/` — `resource.json` copied byte-for-byte from `press1Tick` (2000 ms, fixedDelay, own thread, enabled); `handleTimerEvent.py` exactly as in the spec (tabs).
- `ignition/tests/replay_engine.py` — Python 3 stdlib harness: execs `code.py` with a stub `system`, builds the sample stream from the sim JSON (cumulative counters across phases and loops), feeds two loops through `step()`, tallies the second loop, runs six edge cases, exits non-zero on failure.

### Files changed

| Path | Change |
|---|---|
| `ignition/tags/generate_edge_tags.py` | Modified |
| `ignition/tags/default/40Printing/Dallas/Press Room/Press 1/PLC/tags.json` | Modified (regenerated, +1 tag) |
| `ignition/projects/mes-2026/ignition/script-python/mes/engine/code.py` | Created |
| `ignition/projects/mes-2026/ignition/script-python/mes/engine/resource.json` | Created |
| `ignition/projects/mes-2026/ignition/timer/mesTick/handleTimerEvent.py` | Created |
| `ignition/projects/mes-2026/ignition/timer/mesTick/resource.json` | Created |
| `ignition/tests/replay_engine.py` | Created |
| Gateway `/usr/local/bin/ignition/data/config/resources/core/ignition/tag-definition/default/40Printing/Dallas/Press Room/Press 1/PLC/tags.json` | Pushed copy (not in git) |
| `docs/tasks/task_ENG-001_mes_engine.md` | Status line + Dev Report |
| `docs/agent_handoff.md` | Dev status block |

### Gate results

Generator deterministic, other 16 tags untouched:
```
$ python3 ignition/tags/generate_edge_tags.py
PLC: 9 tags
Print Section: 3 tags
Rewind: 1 tags
Tunnel Dryer: 3 tags
Unwind: 1 tags
$ git diff --stat ignition/tags/default
 .../default/40Printing/Dallas/Press Room/Press 1/PLC/tags.json | 10 ++++++++++
 1 file changed, 10 insertions(+)
$ python3 ignition/tags/generate_edge_tags.py >/dev/null && git diff --stat ignition/tags/default   # second run: same
 .../default/40Printing/Dallas/Press Room/Press 1/PLC/tags.json | 10 ++++++++++
 1 file changed, 10 insertions(+)
```

Offline replay — `python3 -I ignition/tests/replay_engine.py` (exit 0):
```
Sim: fourzero_printing_press1, 35 phases, loop 1046 s

Second loop: 523 samples, events {'product_change': 5, 'run_start': 5, 'segment_close': 35, 'segment_open': 35}

Faulted segments (state 3):
  fault 101: 9 segments, [10.0, 8.0, 10.0, 8.0, 10.0, 8.0, 10.0, 8.0, 10.0] s = 82.0 s
  fault 102: 4 segments, [12.0, 12.0, 12.0, 12.0] s = 48.0 s
  fault 103: 2 segments, [12.0, 12.0] s = 24.0 s
  PASS  15 Faulted segment_close
  PASS  fault 101: 9 segments, 82 s
  PASS  fault 102: 4 segments, 48 s
  PASS  fault 103: 2 segments, 24 s

Setup segments (state 2): [18.0, 44.0, 22.0, 34.0, 28.0] s = 146.0 s
  PASS  5 Setup segment_close, 146 s
  PASS  Setup durations 18, 44, 22, 34, 28

Running segments (state 1): 15, 746.0 s
  PASS  every segment_close dur_s equals its phase duration_seconds (35 segments)
  PASS  run_start x5, products [101, 102, 103, 104, 105]
  PASS  product_change x5 (got 5)
  PASS  restart x0 (got 0)
  PASS  gap x0 (got 0)

Meter deltas over the second loop: good 2315 m, reject 155 m
  PASS  good-meter deltas 2,315 m
  PASS  reject-meter deltas 155 m
  PASS  event seq strictly increasing, no gaps

Edge cases:
  PASS  duplicate sample: no events, no delta (totals good 8.0)
  PASS  sim restart: one restart event, on the drop sample
  PASS  sim restart: delta 0 on the drop, totals good 24.0 reject 1.0
  PASS  gap: one gap event (30 s)
  PASS  gap: open segment closed at the last sample before the gap (dur 4 s)
  PASS  gap: debounce restarts (new segment opens on the 2nd sample after)
  PASS  1-sample blip: no Faulted segment opens
  PASS  fault-code change 101 -> 102: close 101 (4 s), open 102 backdated
  PASS  Faulted -> Running: no run_start (only the Setup -> Running one)

PASSED: 0 failure(s)
```

Jython 2.7 parse (the gateway's own `jython-ia-2.7.4.0.jar`, run with `-S` via the gateway JRE on a throwaway `/tmp/eng001_check.py` in the container, deleted afterwards; compile only, nothing loaded into the gateway):
```
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/script-python/mes/engine/code.py
jython compile OK: /usr/local/bin/ignition/data/projects/mes-2026/ignition/timer/mesTick/handleTimerEvent.py
```

Greps on `ignition/projects/mes-2026/ignition/script-python/mes`:
```
$ grep -rn -E "MQTT Engine|4\.0 Printing|Probe/|SELECT|INSERT|UPDATE |runPrepQuery|runQuery|db\." …/mes   → (no output)
$ grep -rn -E "basestring|unicode|long\(|iteritems|print " …/mes                                         → (no output)
$ grep -c "^ROOT" …/mes/engine/code.py                                                                     → 1
```

VIS-001 untouched:
```
$ git diff --stat …/script-python/press1 …/timer/press1Tick …/com.inductiveautomation.perspective   → (no output)
```

`docker compose config -q` → OK (no output, exit 0).

StartedAt before the push (record for the "only Ignition restarted" check):
```
ignition 2026-10-22T16:21:51.640542841Z healthy
coreflux 2026-10-20T18:09:09.798293762Z healthy
db       2026-10-22T15:43:40.588308669Z healthy
```

Push (`docker exec -i -u ignition … sh -c 'cat > "<path>"' < file`, then `chown ignition:ignition`, then `cmp`):
```
cmp: identical
-rw-r--r-- 1 ignition ignition 3657 Oct 22 11:44 tags.json      (was 3252 bytes)
-rw-r--r-- 1 ignition ignition  155 Oct 22 11:19 unary-resource.json
grep -c '"name"' tags.json → 9
```
Gateway log in the 15 min before the stop: no `mes.engine`, `mesTick` or tag-decode lines (confirms nothing loads before the restart).

### Restart and live check (2026-10-22, after Walker said "restart")

Restart: `docker compose restart ignition` at 16:46:47 UTC; healthy by 16:47:06 UTC.
```
ignition 2026-10-22T16:46:51.179564341Z healthy   (was 16:21:51 — restarted once)
coreflux 2026-10-20T18:09:09.798293762Z healthy   (unchanged)
db       2026-10-22T15:43:40.588308669Z healthy   (unchanged)
```

Logs since the restart (`docker compose logs --since 2026-10-22T16:46:47Z ignition`, 296 lines; watched ~220 s):
```
grep -c "Unable to decode tag resource"  → 0
grep -c "tick failed"                     → 0
MQTT Engine: 11:47:03.988 TahuClient  ignition-client: Connected to tcp://coreflux:1883
```
Every `mes.engine` line (the gateway log abbreviates the logger to `m.engine`; times CDT in brackets, event times UTC):
```
I [11:47:03.230]: engine store created mes.engine.v1 
W [11:47:03.232]: conn: bad read Error_ExpressionEval("Cannot evaluate path 'value' against a null object") at [default]40Printing/Dallas/Press Room/Press 1/PLC/Connected 
W [11:47:03.232]: state: bad read Error_ExpressionEval("Cannot evaluate path 'value' against a null object") at [default]40Printing/Dallas/Press Room/Press 1/PLC/MachineState 
W [11:47:03.232]: fault: bad read Error_ExpressionEval("Cannot evaluate path 'value' against a null object") at [default]40Printing/Dallas/Press Room/Press 1/PLC/FaultCode 
W [11:47:03.232]: product: bad read Error_ExpressionEval("Cannot evaluate path 'value' against a null object") at [default]40Printing/Dallas/Press Room/Press 1/PLC/ProductCode 
W [11:47:03.232]: lanes: bad read Error_ExpressionEval("Cannot evaluate path 'value' against a null object") at [default]40Printing/Dallas/Press Room/Press 1/PLC/WebLanes 
W [11:47:03.232]: good: bad read Error_ExpressionEval("Cannot evaluate path 'value' against a null object") at [default]40Printing/Dallas/Press Room/Press 1/PLC/GoodMeters 
W [11:47:03.232]: reject: bad read Error_ExpressionEval("Cannot evaluate path 'value' against a null object") at [default]40Printing/Dallas/Press Room/Press 1/PLC/RejectMeters 
W [11:47:03.233]: rto: bad read Error_ExpressionEval("Cannot evaluate path 'value' against a null object") at [default]40Printing/Dallas/Press Room/Press 1/PLC/RTOInhibit 
W [11:47:03.233]: tenU: bad read Error_ExpressionEval("Cannot evaluate path 'value' against a null object") at [default]40Printing/Dallas/Press Room/Press 1/Unwind/WebTension 
W [11:47:03.233]: tenR: bad read Error_ExpressionEval("Cannot evaluate path 'value' against a null object") at [default]40Printing/Dallas/Press Room/Press 1/Rewind/WebTension 
W [11:47:03.233]: speed: bad read Error_ExpressionEval("Cannot evaluate path 'value' against a null object") at [default]40Printing/Dallas/Press Room/Press 1/Print Section/LineSpeed 
W [11:47:03.233]: reg: bad read Error_ExpressionEval("Cannot evaluate path 'value' against a null object") at [default]40Printing/Dallas/Press Room/Press 1/Print Section/RegistrationError 
W [11:47:03.233]: visc: bad read Error_ExpressionEval("Cannot evaluate path 'value' against a null object") at [default]40Printing/Dallas/Press Room/Press 1/Print Section/InkViscosity 
W [11:47:03.233]: z1: bad read Error_ExpressionEval("Cannot evaluate path 'value' against a null object") at [default]40Printing/Dallas/Press Room/Press 1/Tunnel Dryer/Zone1Temp 
W [11:47:03.233]: z2: bad read Error_ExpressionEval("Cannot evaluate path 'value' against a null object") at [default]40Printing/Dallas/Press Room/Press 1/Tunnel Dryer/Zone2Temp 
W [11:47:03.233]: z3: bad read Error_ExpressionEval("Cannot evaluate path 'value' against a null object") at [default]40Printing/Dallas/Press Room/Press 1/Tunnel Dryer/Zone3Temp 
W [11:47:03.233]: t: bad read Error_ExpressionEval("Cannot evaluate path 'timestamp' against a null object") at [default]40Printing/Dallas/Press Room/Press 1/PLC/SampleTime 
I [11:47:07.250]: segment_open seq=1 state=1 fault=0 product=103 start=2026-10-22T16:47:04.387Z t=2026-10-22T16:47:06.478Z 
I [11:47:53.338]: segment_close seq=2 state=1 fault=0 product=103 start=2026-10-22T16:47:04.387Z end=2026-10-22T16:47:51.991Z dur_s=47.604 t=2026-10-22T16:47:51.991Z 
I [11:47:55.342]: segment_open seq=3 state=3 fault=102 product=103 start=2026-10-22T16:47:51.991Z t=2026-10-22T16:47:54.086Z 
I [11:48:05.361]: segment_close seq=4 state=3 fault=102 product=103 start=2026-10-22T16:47:51.991Z end=2026-10-22T16:48:04.398Z dur_s=12.407 t=2026-10-22T16:48:04.398Z 
I [11:48:07.363]: segment_open seq=5 state=1 fault=0 product=103 start=2026-10-22T16:48:04.398Z t=2026-10-22T16:48:06.498Z 
I [11:48:57.437]: segment_close seq=6 state=1 fault=0 product=103 start=2026-10-22T16:48:04.398Z end=2026-10-22T16:48:56.111Z dur_s=51.713 t=2026-10-22T16:48:56.111Z 
I [11:48:59.440]: segment_open seq=7 state=3 fault=101 product=103 start=2026-10-22T16:48:56.111Z t=2026-10-22T16:48:58.217Z 
I [11:49:07.460]: segment_close seq=8 state=3 fault=101 product=103 start=2026-10-22T16:48:56.111Z end=2026-10-22T16:49:06.516Z dur_s=10.405 t=2026-10-22T16:49:06.516Z 
I [11:49:09.465]: segment_open seq=9 state=1 fault=0 product=103 start=2026-10-22T16:49:06.516Z t=2026-10-22T16:49:08.569Z 
I [11:50:01.558]: segment_close seq=10 state=1 fault=0 product=103 start=2026-10-22T16:49:06.516Z end=2026-10-22T16:49:59.718Z dur_s=53.202 t=2026-10-22T16:49:59.718Z 
I [11:50:03.562]: segment_open seq=11 state=3 fault=101 product=103 start=2026-10-22T16:49:59.718Z t=2026-10-22T16:50:01.756Z 
I [11:50:09.572]: product_change seq=12 lanes=2 from=103 to=104 t=2026-10-22T16:50:07.891Z 
I [11:50:09.573]: segment_close seq=13 state=3 fault=101 product=103 start=2026-10-22T16:49:59.718Z end=2026-10-22T16:50:07.891Z dur_s=8.173 t=2026-10-22T16:50:07.891Z 
I [11:50:11.576]: segment_open seq=14 state=2 fault=0 product=104 start=2026-10-22T16:50:07.891Z t=2026-10-22T16:50:09.908Z 
I [11:50:43.619]: segment_close seq=15 state=2 fault=0 product=104 start=2026-10-22T16:50:07.891Z end=2026-10-22T16:50:42.492Z dur_s=34.601 t=2026-10-22T16:50:42.492Z 
I [11:50:43.619]: run_start seq=16 product=104 lanes=2 t=2026-10-22T16:50:42.492Z 
I [11:50:45.624]: segment_open seq=17 state=1 fault=0 product=104 start=2026-10-22T16:50:42.492Z t=2026-10-22T16:50:44.516Z
```
The 17 bad-read warnings are a single burst at 11:47:03.232, before MQTT Engine connected at 11:47:03.988 (expected, same as `press1`). Because of `_warn_once` they are logged once only, so "none after connect" rests on the events flowing from 11:47:07 onwards and on there being no error lines.

Checks:
- `engine store created mes.engine.v1` present ✅
- seq 1–17 in order, no gaps, no repeats ✅ (de-dup works: 2 s timer against a ~2.07 s sim, no event twice)
- Sequence matches the sim file: J103 Run 1 → Fault 1 (102) → Run 2 → Fault 2 (101) → Run 3 → Fault 3 (101) → `product_change` 103→104 (lanes 2) → J104 Setup → `run_start` 104 → Running ✅
- Segment durations vs the sim file:

| Phase | dur_s | Phase s | Samples | Avg sample interval | dur_s − phase |
|---|---|---|---|---|---|
| J103 Run 1 (partial: engine started mid-phase) | 47.604 | 48 | — | — | — |
| J103 Fault 1, 102 | 12.407 | 12 | 6 | 2.068 s | +0.407 |
| J103 Run 2 | 51.713 | 50 | 25 | 2.069 s | **+1.713** |
| J103 Fault 2, 101 | 10.405 | 10 | 5 | 2.081 s | +0.405 |
| J103 Run 3 | 53.202 | 52 | 26 | 2.046 s | **+1.202** |
| J103 Fault 3, 101 | 8.173 | 8 | 4 | 2.043 s | +0.173 |
| J104 Setup | 34.601 | 34 | 17 | 2.035 s | **+0.601** |

  🟡 **The "multiple of 2 s ±0.5 s" check fails for the two complete Run segments and the Setup.** Each duration equals the phase's sample count times the measured interval. The cause is the live sim: it publishes every ~2.04–2.08 s, not 2.000 s (e.g. sample times 16:47:51.991 → 16:47:54.086 = 2.095 s), so the error builds up over a phase. The engine computes durations from sample timestamps, as the spec requires. Rules not tuned.

**Step 7 — Walker confirmed (2026-10-22):** `PLC/SampleTime` Good and changing every ~2 s in the Tag Browser, and `/press1` reloads ("yes -- good to go").

### Deviations from spec

- **Store `started`** uses `int(time.time() * 1000)` rather than `system.date`, so `_store()` runs unchanged in the harness (the stub only provides `system.util`). Same value, epoch ms.
- **Duplicate reads in `tick()`** are detected by `st['samples']` not changing across `step()`, so a duplicate neither logs nor dispatches.
- **Gap events** carry `from` (last sample `t`) and `gap_s`; **restart events** carry the new `good` / `reject` readings. The spec named no fields for these two.
- **1-sample blip** inside Running: no Faulted segment opens (as specced), but the blip sample still ends the Running run, so the Running segment closes at the blip and a new Running segment opens two samples later. That is the specced "close on first differing sample" rule applied to state 1; flagging it because OEE-001 will see a Running segment split around a blip.
- Meter deltas across a gap are still counted (the spec didn't say to drop them; the meters are cumulative, so the delta is real production).

### Open questions for the PM

- **Live durations run 2–3.5 % longer than the sim's nominal phase lengths.** The live sim publishes every ~2.04–2.08 s, so a 50 s Run phase measures 51.7 s from sample timestamps. The spec's live-check tolerance ("multiple of 2 s ±0.5 s") doesn't hold for phases longer than ~10 s. Two decisions for the PM: should the tolerance be per sample (≈ samples × 2 s × 1.05)? And should the §9 reference values (Run 746 s, Fault 154 s, Setup 146 s, loop 1,046 s) be read as sample counts × 2 s rather than wall-clock seconds when DT-001 / OEE-001 check against live data? Percentages (Availability, Quality) should be unaffected, since every state stretches by about the same factor. Not tuned in the engine.
- See the 1-sample blip note under Deviations (OEE-001: a Running segment splits around a blip).


## PM Verification

**PM, 2026-10-22 — PASS.** Checked against the files, the test and the live gateway log, not the report:

- [x] `PLC/SampleTime`: only addition to `PLC/tags.json` (`git diff`), String, `toStr(jsonGet({…/PLC/MachineState}, "timestamp"))`; other 16 unchanged; generator re-run → no diff; gateway copy identical (`cmp`).
- [x] `mes/engine/code.py` read in full: pure `step()` (no `system.*`, Java or logging); `tick()` the only I/O; single `ROOT` on `[default]40Printing/…`; no `[MQTT Engine]`, old root, `Probe/` or SQL; de-dup, gap guard, deltas with restart → 0, debounce 2 backdated, close on first differing sample, fault-code split, segments for all states, `run_start` only on 2 → 1; `HANDLERS` empty; read API returns deep copies. Style matches `press1`.
- [x] `resource.json` (scope A) and `timer/mesTick` (2000 ms, fixed delay, own thread, scope G) match the `press1` formats.
- [x] Replay test run by the PM: `python3 -I ignition/tests/replay_engine.py` → 0 failures; it `exec`s the real `code.py`. Second loop: faults 9/4/2 = 82/48/24 s, Setup 5 = 146 s, all 35 segments equal their phase `duration_seconds`, run starts 101→105, good 2,315 m, reject 155 m; six edge cases pass.
- [x] After the restart (16:46:51Z): 0 `Unable to decode`; `engine store created mes.engine.v1`; no `tick failed`; the 17 bad reads are one burst at 11:47:03.232, before MQTT Engine connected. Events through seq 24 in order, no repeats, sequence matches the sim file (J103 → product_change 103→104 → J104 Setup → run_start 104 → …).
- [x] Only Ignition restarted; CoreFlux (2026-10-20) and db (15:43:40Z) unchanged. VIS-001 files unchanged (`git diff` empty). Walker: SampleTime Good and live, `/press1` reloads.

**Live drift finding — accepted, engine correct.** The live sim publishes every ~2.04–2.08 s, so durations from sample timestamps run ~2–3.5 % over nominal (Run 50 s → 51.7 s). The spec's "multiple of 2 s ± 0.5 s" live check was the PM's error, too tight for long phases. The MES §5 requirement ("10 s ± one sample, 2 s") holds for every segment seen (faults +0.2 to +0.4 s, Setup +0.6 s, Runs ≤ +1.7 s). Ratios (A, P, Q) are unaffected. **For DT-001 / OEE-001 live checks:** compare event counts exactly, per-event durations ± 2 s, and loop totals within ~4 % of the §5/§9 nominal values (e.g. web break ≈ 82–85 s, loop ≈ 1,046–1,085 s). The engine was not tuned. Recorded in `agent_handoff.md` traps.

**Deviations accepted:** `started` from `time.time()`; duplicate detection via `samples`; `gap`/`restart` field names; deltas across a gap counted (cumulative meters, real production); 1-sample blip splits a Running segment (impossible in this sim — every fault ≥ 8 s; noted for OEE-001).

User-facing impact: none visible (no tags, no views). The gateway log now has one `m.engine` INFO line per event (~70 per loop). Students see `PLC/SampleTime` as a 17th edge tag. VIS-001 trend buffer restarted empty once (accepted).
