# Decision Log

**Project:** MES Workshop 2026
**Maintained by:** the PM
**Format:** Reverse chronological. Superseded decisions are ~~struck through~~ with a pointer to the decision that replaced them — never deleted.

---

## How to use this log

Every significant architectural, tooling, process or product decision is recorded with: the decision, the options considered, the rationale, who made the call, and the cost of reversing it. **🔴 High reversal cost** decisions warrant extra deliberation before changing.

## Entry template

```
### DEC-NNN — {decision headline}

**Date:** YYYY-MM-DD
**Status:** Proposed | ✅ Accepted — {who}, YYYY-MM-DD | ~~Superseded by DEC-MMM~~
**Reversal cost:** 🟢 Low | 🟡 Moderate | 🔴 High
**Affects:** files, components, docs

**Context:** what forced the decision, verified by reading (not recalled).
**Options:**
1. {option} — pros / cons
2. {option} — pros / cons
**Decision:** what was chosen.
**Rationale:** why.
**Consequences:** what this commits us to, and what it rules out.
```

---

## Log

### DEC-010 — VIS-002 is one `/mes` page with five tabs; test pages kept

**Date:** 2026-10-23
**Status:** ✅ Accepted — Walker, 2026-10-23 (PM default)
**Reversal cost:** 🟢 Low
**Affects:** `docs/tasks/task_VIS-002_mes_operator_ui.md`, Perspective views `Mes/*`, `page-config`, project stylesheet

**Context:** MES §1 says only "the real visuals come in one pass in Hour 4". No prompt or reference dashboard exists for the MES screens.
**Options:**
1. PM default: one `/mes` page (Overview, Work Orders, Schedule, Downtime, OEE) in the `/press1` look, existing `mes.*` functions only.
2. Spec from a prompt or reference dashboard (as VIS-001 was).
3. Data API and page shell only; students build the screens live.
**Decision:** Option 1. The throwaway test pages `/mes/*` stay as the fallback. UI follow-ups from WO-004 (clear inputs, busy-disable, inline confirm, date picker at now + 24 h) are folded in.
**Rationale:** Fastest path to a coherent operator UI with no new logic.
**Consequences:** No new write SQL in views; read-only `mes.ui` additions only. `psc-` styles untouched, `mes-` classes appended.

### DEC-009 — MES results published with MQTT Engine's script publish (UNS-005)

**Date:** 2026-10-23
**Status:** ✅ Accepted — Walker, 2026-10-23
**Reversal cost:** 🟢 Low
**Affects:** `docs/tasks/task_UNS-005_publish_functional_folders.md`, `mes.publish`, gateway timer `mesPublish`

**Context:** The functional folders must be published to CoreFlux (MES §8, DEC-007). MQTT Transmission is installed but disabled with no server, and its default is Sparkplug B topics. MQTT Engine is already connected to CoreFlux as `MES Coreflux`.
**Options:**
1. `system.cirruslink.engine.publish` from a gateway timer — plain JSON topics, no config change, no restart.
2. MQTT Transmission — needs enabling, a server, and custom-topic setup.
3. 8.3 Event Streams MQTT handler — GUI setup, unproven here.
**Decision:** Option 1, with a 13-tag allowlist and a guard that refuses any path outside `Work Order`, `Schedule`, `Downtime`, `Planned Stop`, `OEE`. State tags retained, events not.
**Rationale:** Least setup and no restart during class; the guard makes "never publish the edge folders" provable in code.
**Consequences:** The Engine echo (`[MQTT Engine]40Printing/…/{functional}/…`) appears in Ignition, harmless. The Timebase video stack ingests the topics (accepted by Walker, DEC-007).

### DEC-008 — Dev database login is `mes` / `mes` / `mes`, committed in `.env.example`

**Date:** 2026-10-22
**Status:** ✅ Accepted — Walker, 2026-10-22
**Reversal cost:** 🟢 Low
**Affects:** `.env.example`, `.env` (local), PostgreSQL role `mes`, Ignition DB connection (INFRA-002), `docs/db_schema.md` §7

**Context:** Walker did not want a long random password on the workshop database. Students who clone the repo need a stack that works without asking for credentials. Rule 15 forbids secrets in tracked files.
**Options:**
1. Keep a random password in `.env` only — Rule 15 clean / students cannot log in without being told it.
2. Dev value `mes` committed in `.env.example` — works on clone / a password sits in a tracked file.
**Decision:** Option 2. Database `mes`, user `mes`, password `mes`, in `.env.example`. The live role was changed with `ALTER USER` and `db` recreated alone (PM, 2026-10-22).
**Rationale:** Dev-only stack, every port bound to 127.0.0.1 (DEC-003), synthetic data only. Not a secret in any meaningful sense.
**Consequences:** A documented exception to Rule 15 for this one value. Every other credential (`IGNITION_ADMIN_PASSWORD`, `COREFLUX_SECRET_KEY`) stays out of tracked files. Never reuse this value outside the workshop stack.

### DEC-007 — The MES reads and writes an Ignition `[default]` tag tree; the `MES/` topic level is dropped

**Date:** 2026-10-22
**Status:** ✅ Accepted — Walker, 2026-10-22 10:06 CDT
**Reversal cost:** 🟡 Moderate (every MES script, binding and published path is written against this tree)
**Affects:** Ignition `[default]` tag provider, all Core Four tasks, `docs/ignition_tag_namespace.md`, the MES-published topics in `docs/mes_requirements.md` §4.1, §5, §8, §10, `docs/architecture.md`, `docs/sim_spec_press1.md` §2. Supersedes the open question in DEC-002.

**Context:** DEC-002 deferred how the Core Four get press data. The candidates were the VIS-001 `press1` gateway buffer, typed Ignition tags (UNS-002) and Maestrohub (UNS-001, INFRA-003). MQTT Engine (`jsonPayload: false`) holds each raw topic as a String tag with the whole JSON envelope. The docs had the MES publishing under `…/Press 1/MES/…`.
**Options:**
1. MES reads `[MQTT Engine]` tags or the `press1` buffer directly — no new layer / every MES function parses envelopes and is tied to MQTT Engine.
2. A `[default]` tag tree mirroring the sim hierarchy, with functional folders beside it — one cast point, the namespace becomes the interface, a later source change (Maestrohub, real PLC) does not touch MES logic / one more layer to build.
3. Maestrohub ingest first — the target architecture / not stood up, too slow for the workshop.
**Decision:** Option 2. Under `[default]40Printing/Dallas/Press Room/Press 1/`:
- **Edge folders** keep exactly the sim's level names: `PLC`, `Print Section`, `Unwind`, `Rewind`, `Tunnel Dryer`. They hold the 16 raw tags, brought in from `[MQTT Engine]40Printing/…` and typed/cast.
- **Functional folders** sit beside them: `Work Order`, `Schedule`, `Downtime`, `Planned Stop`, `OEE`.
- **The `MES/` topic level is dropped.** MES-published paths move per `docs/ignition_tag_namespace.md` §3a; the confirmed payloads do not change.
- The MES reads and writes this tree, never `[MQTT Engine]` directly.
**Rationale:** The namespace is the interface (UNS edge/functional/definitional split). Students see real, typed tags in the Tag Browser before any MES logic exists.
**Consequences:** Ingest state (last sample, open downtime event, counter baselines) lives in the Ignition gateway, not the database (Walker, 2026-10-22). MES code goes in a separate `mes` project library; the `press1` library and the VIS-001 view stay unchanged. The edge folders are never published to the broker. The VIS-001 acceptance checks that forbid DB and MES terms now apply to the `press1` project library and its view only. Tag names in the functional folders are VERA's proposal and may be renamed. Mechanism (reference vs expression tags, `jsonGet`, MQTT Transmission) is checked against the Ignition 8.3 docs before it is specced. Published functional topics will also flow into the separate Timebase video stack's `press1` dataset (its filter drops only `…/MES/…`); Walker accepts this and will update that stack at the end (2026-10-22). Workshop agents still do not touch it.

### DEC-006 — Repo moved to `iiot-university`, private

**Date:** 2026-10-21
**Status:** ✅ Accepted — Walker, 2026-10-21
**Reversal cost:** 🟢 Low
**Affects:** git remote `origin`, `docs/claude_project_instructions.md`

**Context:** The repo was created as `40solutions/mes-workshop-2026`. Walker: "its supposed to be private in iiot-university not 40solutions".
**Decision:** Transferred it with GitHub's repo transfer (history kept, still private) to `iiot-university/mes-workshop-2026`. Local `origin` repointed.
**Consequences:** The old URL redirects. New clones use https://github.com/iiot-university/mes-workshop-2026.

### DEC-005 — Topic root renamed `4.0 Printing` → `40Printing`

**Date:** 2026-10-20
**Status:** ✅ Accepted — Walker, 2026-10-20
**Reversal cost:** 🔴 High (every consumer path, and another sim reimport resets the counters)
**Affects:** the sim definition, every topic path in `docs/`, MQTT Engine namespace, all future Ignition/Maestrohub/Timebase paths

**Context:** The project rules allowed renaming the first level only with Walker's approval and only if a consumer misbehaved with it. The dot broke Ignition (Ignition does not allow `.` in tag names). The documented fallback `4.0Printing` keeps the dot, so it would not have helped.
**Decision:** Root is `40Printing`. Walker had VERA rename it and reimported the sim at ~13:45 CDT (this reset the counters and restarted the loop, before students connected). Display text still says "4.0 Printing" — it is the company name.
**Consequences:** PM verified all 16 topics live under `40Printing/Dallas/Press Room/Press 1/` on `coreflux:1883` (~14:10 CDT). Stale `4.0 Printing/…` copies remain in UNS Studio's namespace, Notus and probably the MQTT Engine tag provider; never read them. Repo docs and `docs/sim/fourzero_printing_press1.json` need syncing (PM; sim JSON only as a byte-for-byte copy from UNS Studio). Spaces in lower levels are unchanged.

### DEC-004 — Sim repointed to this stack's CoreFlux (INFRA-004)

**Date:** 2026-10-20
**Status:** ✅ Accepted — Walker, 2026-10-20
**Reversal cost:** 🟡 Moderate (another repoint risks a sim restart and counter reset)
**Affects:** UNS Studio broker connection, `docs/architecture.md` §1, §5

**Context:** The sim published to `coreflux-mqtt` (project `notus-dev`, host 1884), outside this stack. INFRA-001 deployed CoreFlux 2.2.0 on host `127.0.0.1:1886`.
**Decision:** Walker repointed the UNS Studio sim to `127.0.0.1:1886`. Walker: "sim is pointed at 1886, no restart".
**Rationale:** The MES stack owns its broker; one cut-over before students pull data.
**Consequences:** The old broker is out of the path. PM verified all 16 topics under `4.0 Printing/Dallas/Press Room/Press 1/` arriving on `coreflux:1883` (2026-10-20). Do not repoint again during class.

### DEC-003 — Dev stack host ports bind to localhost only; students use their own broker

**Date:** 2026-10-20
**Status:** ✅ Accepted — Walker, 2026-10-20
**Reversal cost:** 🟢 Low
**Affects:** `docker-compose.yml` port bindings, `docs/development_philosophy.md` §Security

**Context:** INFRA-001 binds every host port to `127.0.0.1`. The PM asked whether students would connect to Walker's broker or gateway from their own machines.
**Options:**
1. Keep `127.0.0.1` — nothing on Walker's Mac is exposed to the room.
2. Bind `0.0.0.0` so students can reach Walker's CoreFlux/Ignition — needs auth thought.
**Decision:** Option 1. Students connect to their own broker, or Walker republishes to a public broker for them.
**Rationale:** Walker, 2026-10-20: "students will connect to their own broker (or I will publish to a public broker for them)".
**Consequences:** No LAN exposure of this stack. If Walker publishes to a public broker, that is a separate bridge/republish task, specced when he asks.

### DEC-002 — Day 1: Ignition MQTT Engine subscribes to CoreFlux directly

**Date:** 2026-10-20
**Status:** ✅ Accepted — Walker, 2026-10-20
**Reversal cost:** 🟢 Low
**Affects:** UNS-002, VIS-001; deferred: INFRA-003 (Maestrohub), UNS-001; `docs/architecture.md` §1

**Context:** The approved data flow (`docs/mes_requirements.md` §8) routes sim → CoreFlux → Maestrohub → Ignition. Maestrohub is not in this stack yet, and the Day 1 goal is process visuals in Perspective.
**Options:**
1. Build Maestrohub ingest first (INFRA-003, UNS-001), then Ignition.
2. Ignition's MQTT Engine subscribes to the sim topics on CoreFlux directly; visuals bind to MQTT Engine tags.
**Decision:** Option 2, for testing. Walker, 2026-10-20: "ignition will sub to coreflux and visuals will publish from mqtt engine just for testing".
**Rationale:** Fastest path to live tags for today's visuals.
**Consequences:** UNS-002 configures MQTT Engine against `coreflux:1883`. Envelope unwrap and casting happen in Ignition for now; the Maestrohub path (INFRA-003, UNS-001) is deferred, not dropped — revisit before the Core Four build.

### DEC-001 — Two-session PM/Dev workflow, files as the only channel

**Date:** 2026-10-20
**Status:** ✅ Accepted — inherited from `mes-dev-template`
**Reversal cost:** 🟡 Moderate
**Affects:** `.claude/skills/pm/SKILL.md`, `.claude/skills/dev/SKILL.md`, all of `docs/`

**Decision:** One Claude Code session runs `/pm` (specs, docs, verification; no production code); a second runs `/dev` (implements one spec). They never share a chat; the Dev reports through the spec's `Dev Report` and `agent_handoff.md`. No root `CLAUDE.md` — the operating manual is `docs/claude_project_instructions.md`.

**Rationale:** Separating author from verifier stops an agent grading its own work. Files survive `/clear` and session restarts; chat does not.
