# Bug Log

**Project:** MES Workshop 2026
**Maintained by:** the PM
**Format:** Reverse chronological. A bug is logged when found, updated when investigated or specced, and marked Fixed with a root-cause note only after the fix is verified (Rule 7). Read this before touching a previously-buggy area.

---

## Entry template

```
## BUG-NNN — {short headline} ({P0–P3}, {Open | Specced | Fixed})

**Found:** YYYY-MM-DD by {who}, {where — build, environment}
**Spec:** `docs/tasks/task_BUG-NNN_<slug>.md` (once specced)

**Symptom:** what was observed, exactly.
**Root cause:** what was actually wrong — from logs, grep, or a query, not inference.
**Fix:** what changed (filled in at close-out).
**Trap to remember:** the pattern that will cause this again, if there is one.
```

---

## BUG-002 — Work order run loses a reject step at the end of a job (P2, Fixed)

**Found:** 2026-10-22 by the WO-002 Dev (test page), confirmed by the PM in the live database
**Spec:** `docs/tasks/task_WO-001-fix1_late_meter_delta.md`

**Symptom:** WO-1002 completed with `reject_meters` 16; MES §3 expects 18. WO-1001 (600/18) was correct.
**Root cause:** PM query on `mes.work_order_run`: the raw `RejectMeters` counter advanced **18** between run 2's `start_reject_counter` (8618) and run 3's (8636), but run 2 recorded **16** — 2 m (one web-break step) were seen on a Setup sample after `mes.workorder` had closed the run at `product_change`, so they were credited to no run. Job 102 ends on a web-break fault (reject +2 per sample); job 101 ends on an over-temp (no reject step), which is why 101 was exact. **Mechanism (inferred, not yet observed):** the counter's new value reaches the engine one sample after the state change (the 17 edge tags come from separate MQTT messages and are read in one `readBlocking`; a meter update can land one tick after `MachineState`/`SampleTime`). Jobs 103 and 104 also end on a web break and will show the same 2 m loss.
**Data correction:** Walker, 2026-10-22: "correct it to 18". PM ran one guarded update — `UPDATE mes.work_order_run SET reject_meters = 18 WHERE run_id = 2 AND work_order_id = 'WO-1002' AND reject_meters = 16 AND end_ts IS NOT NULL` → 1 row; view now shows WO-1002 375 / 18.
Walker, 2026-10-22: "yes correct WO-1003 too". PM: `UPDATE … SET reject_meters = 18 WHERE run_id = 3 AND work_order_id = 'WO-1003' AND reject_meters = 16 AND end_ts IS NOT NULL AND <run 4 start_reject_counter> − start_reject_counter = 18` → 1 row. Walker, 2026-10-22: "Yes -- correct it" (WO-1004). PM: same guarded `UPDATE` on run 4 (span to run 5's start counter = 18) → 1 row.
**Fix:** WO-001-fix1 (2026-10-22): rule 8 in `mes.workorder` — with no run open, a meter delta on a Setup sample is credited to the run just closed (absolute totals; forgotten at the next run start, a gap or after a resume). Proven by a lag-injected replay; loaded by Scan File System 12:30 CDT. Closed before the first live late-delta credit was observed (Walker: "assume its right").
**Trap to remember:** Edge tags from separate MQTT messages are not a consistent snapshot. Any counter delta that arrives on the first sample after a boundary belongs to the previous phase. Setup has meter step 0 in this sim, so a delta seen during Setup is late production of the job that just ended.

## BUG-001 — Ignition MQTT Engine connects and drops every ~3 s against CoreFlux (P1, Fixed — workaround)

**Found:** 2026-10-20 by Walker, Ignition 8.3.10 gateway log (MQTT Engine server "MES Coreflux", client ID `ignition-client`, `tcp://coreflux:1883`)
**Spec:** none — external broker defect; workaround is an Ignition gateway setting (Walker, GUI)

**Symptom:** `TahuClient` ERROR `Failed subscribe on [#, spBv1.0/#, spBv1.0/STATE/IamHost] granted QoS [0, 0, 1] != [1, 0, 0]`, then reconnect, repeating every ~3 s.
**Root cause:** CoreFlux 2.2.0 returns the SUBACK granted-QoS list in the wrong order when one SUBSCRIBE carries topics with mixed QoS. PM reproduced with paho 1.6.1 against `127.0.0.1:1886` (2026-10-20): requested `[0,0,1]` → granted `[1,0,0]`; requested `[1,0,0]` → `[1,0,0]`; `[0,0]` → `[0,0]`; `[1,1,1]` → `[1,1,1]`. MQTT Engine checks granted against requested in order, treats the mismatch as a failed subscribe, and disconnects. The QoS 1 entry is the Sparkplug primary-host STATE topic (`spBv1.0/STATE/IamHost`).
**Fix:** Workaround by Walker in the gateway UI, 2026-10-20 ~13:59: MQTT Engine server `MES Coreflux` now has `enablePrimaryHostSubscriptions: false`, so it no longer subscribes to `spBv1.0/STATE/…` at QoS 1. PM verified from the gateway config file and the log: last reconnect 13:59:01, none in the following 10+ minutes. The broker defect itself is unfixed (not reported to CoreFlux yet).
**Trap to remember:** Any client that subscribes to several topics with mixed QoS in one SUBSCRIBE will see this against CoreFlux 2.2.0 — check Maestrohub and Timebase (INFRA-003) for the same pattern. Keep subscription QoS uniform per client.

