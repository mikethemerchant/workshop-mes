# DB-002 — Apply the seed data (`db/002_seed.sql`)

**Status:** ✅ Complete — 2026-10-22 (seed ran 17:07:34Z), verified by the PM, approved by Walker
**Priority:** P1 · **Effort:** S (one command plus checks)
**Depends on:** DB-001
**Bug:** —

---

## Background

The Hour 2 test visuals need products, work orders, readiness and a schedule to show. VERA wrote the seed. Due dates are `now()` plus an offset, so WO-1004 (6 min) and WO-1009 (20 min, Rush) are tight only from the moment the seed runs. That is why Walker triggers it, not the Dev.

## What Already Exists (Do Not Rewrite)

- `db/002_seed.sql` — VERA's seed, source of truth. **Apply as written. Do not edit it.**
- `docs/db_schema.md` §5 (checks) and §6 (seed notes).
- The `mes` schema from DB-001, empty.

**PM review notes (2026-10-22), for later tasks, not this one:**
- All nine Released orders get the same `released_at` (`now()` is fixed within a transaction). "Earliest Released work order for that product code" (MES §3) therefore ties between e.g. WO-1001 and WO-1006. WO-001 must break the tie on `work_order_id` ascending. No seed change.
- WO-1010 (product 105) is Planned, so the second loop's job 105 has no Released order to attribute to. **Kept on purpose (Walker, 2026-10-22):** a teaching moment — the press made product nobody released. WO-001 must show that run as unattributed, never invent an order.

## What to Build

1. Confirm DB-001 is applied and every `mes` table is empty. If any table has rows, stop and report.
2. Apply the seed in one transaction:
   ```
   docker exec -i mes-workshop-2026-db-1 psql -U mes -d mes -1 -v ON_ERROR_STOP=1 -f - < db/002_seed.sql
   ```
3. Run the checks in `docs/db_schema.md` §5 and paste the full output, plus:
   ```sql
   select now(), work_order_id, due_date, priority from mes.work_order where work_order_id in ('WO-1004','WO-1009');
   ```

## Acceptance Criteria

- [ ] 5 products, 10 work orders (Released 9, Planned 1), 5 schedule entries (sequence 1–5, WO-1001..WO-1005), 6 readiness rows.
- [ ] `v_work_order_table` returns 10 rows with `good_m` and `reject_m` all 0.
- [ ] WO-1004 is due about 6 min and WO-1009 about 20 min after the seed's `now()`.
- [ ] `db/002_seed.sql` unchanged: `shasum` is `4f6eccf7d537ffb55e287a666d21ffa1b44f4d80` (the PM-reviewed file) before and after.
- [ ] No container restarted.

## Files to Modify

| Path | Change |
|---|---|
| — | No file changes. Database only. |
| `docs/tasks/task_DB-002_apply_seed.md` | Dev Report |
| `docs/agent_handoff.md` | Dev status block |

## Dev Instructions

Spec: `docs/tasks/task_DB-002_apply_seed.md`. **Run only when Walker has said "run the seed" in this session.** Check that the `mes` tables are empty, apply `db/002_seed.sql` in one transaction with `psql -1 -v ON_ERROR_STOP=1` through `docker exec`, then run the `docs/db_schema.md` §5 checks and paste the output.

Hard constraints:
- Run the seed exactly once. Never re-run it on a non-empty schema; never truncate or drop to "reset" without Walker's go-ahead.
- Do not edit either SQL file. On any error, stop and report it.
- Never `docker compose down -v` or touch the `db-data` volume. Do not restart any container.
- Never stop, start, update or reimport the sim; never touch UNS Studio.
- Never print `.env` or any password.
- No commit, no push. Stage nothing.

## Dev Report

**Status:** ✅ Done — ready for PM verification.

**Trigger:** Walker said "run the seed" in this Dev session on 2026-10-22, after UNS-004 closed on the Dev side. The seed's `now()` is **2026-10-22 17:07:34.78 UTC**: WO-1004 is due 17:13:34 and WO-1009 (Rush) 17:27:34.

### What was built

No file changes; database only. I applied `db/002_seed.sql` once, in one transaction.

### Files changed

- Modified: `docs/tasks/task_DB-002_apply_seed.md` (Status line, this report), `docs/agent_handoff.md` (Dev status block)
- Not touched: `db/001_schema.sql`, `db/002_seed.sql` (hash unchanged). No container was restarted.

### Gate results

Preflight:
```
$ shasum db/002_seed.sql
4f6eccf7d537ffb55e287a666d21ffa1b44f4d80  db/002_seed.sql
```
All 9 `mes` tables had 0 rows (`count(*)`): product_master, work_order, work_order_run, schedule_entry, schedule_override, job_readiness, downtime_event, planned_stop, oee_interval.

Apply:
```
$ docker exec -i mes-workshop-2026-db-1 psql -U mes -d mes -1 -v ON_ERROR_STOP=1 -f - < db/002_seed.sql
INSERT 0 5
INSERT 0 10
INSERT 0 6
INSERT 0 5
exit: 0
```

`docs/db_schema.md` §5 checks, plus the due-date query:
```
select count(*) from mes.product_master;            -> 5
select count(*) from mes.work_order;                -> 10
select status, count(*) ... group by status;        -> Released 9, Planned 1
select count(*) from mes.schedule_entry;            -> 5
select count(*) from mes.job_readiness;             -> 6

select * from mes.v_work_order_table order by work_order_id;
 work_order_id | product_code |    product_family    |  status  | priority | planned_meters | good_m | reject_m | lanes | run_start | run_end |           due_date
 WO-1001       |          101 | Snack bag film       | Released | Normal   |            600 |      0 |        0 |     4 |           |         | 2026-10-22 18:07:34.783423+00
 WO-1002       |          102 | Coffee pouch film    | Released | Normal   |            375 |      0 |        0 |     2 |           |         | 2026-10-22 18:12:34.783423+00
 WO-1003       |          103 | Frozen food bag film | Released | Normal   |            450 |      0 |        0 |     4 |           |         | 2026-10-22 18:17:34.783423+00
 WO-1004       |          104 | Candy wrapper film   | Released | Normal   |            525 |      0 |        0 |     2 |           |         | 2026-10-22 17:13:34.783423+00
 WO-1005       |          105 | Pet food bag film    | Released | Normal   |            365 |      0 |        0 |     2 |           |         | 2026-10-22 18:22:34.783423+00
 WO-1006       |          101 | Snack bag film       | Released | Normal   |            600 |      0 |        0 |     4 |           |         | 2026-10-22 18:27:34.783423+00
 WO-1007       |          102 | Coffee pouch film    | Released | Normal   |            375 |      0 |        0 |     2 |           |         | 2026-10-22 18:32:34.783423+00
 WO-1008       |          103 | Frozen food bag film | Released | Normal   |            450 |      0 |        0 |     4 |           |         | 2026-10-22 18:37:34.783423+00
 WO-1009       |          104 | Candy wrapper film   | Released | Rush     |            525 |      0 |        0 |     2 |           |         | 2026-10-22 17:27:34.783423+00
 WO-1010       |          105 | Pet food bag film    | Planned  | Normal   |            365 |      0 |        0 |     2 |           |         | 2026-10-22 18:47:34.783423+00
(10 rows)

select * from mes.v_schedule_table;
 sequence | work_order_id | product_code | plates_ready | ink_ready | proof_approved | planned_start | planned_end | actual_start_ts | actual_end_ts | reconcile_result | started_without_readiness
        1 | WO-1001       |          101 | t            | t         | t              |               |             |                 |               |                  | f
        2 | WO-1002       |          102 | t            | t         | t              |               |             |                 |               |                  | f
        3 | WO-1003       |          103 | t            | t         | t              |               |             |                 |               |                  | f
        4 | WO-1004       |          104 | t            | t         | t              |               |             |                 |               |                  | f
        5 | WO-1005       |          105 | t            | t         | t              |               |             |                 |               |                  | f
(5 rows)

select now(), work_order_id, due_date, priority, due_date - now() as due_in ...
              now              | work_order_id |           due_date            | priority |     due_in
 2026-10-22 17:07:38.894542+00 | WO-1004       | 2026-10-22 17:13:34.783423+00 | Normal   | 00:05:55.888881
 2026-10-22 17:07:38.894542+00 | WO-1009       | 2026-10-22 17:27:34.783423+00 | Rush     | 00:19:55.888881
```
(`now()` in the last query is about 4 s after the seed transaction; due_in relative to the seed's `now()` is exactly 6 min and 20 min.)

After:
```
$ shasum db/002_seed.sql
4f6eccf7d537ffb55e287a666d21ffa1b44f4d80  db/002_seed.sql
ignition 2026-10-22T17:00:29.102224928Z   (UNS-004 restart; not restarted by DB-002)
coreflux 2026-10-20T18:09:09.798293762Z
db 2026-10-22T15:43:40.588308669Z
```

UNS-004 query tags against the seeded data (`generate_functional_tags.py --check`, all 28 queries OK, read-only):
- Product 101–105: IdealSpeed / Lanes / ScheduledChangeoverSec are 250.0/4/20, 180.0/2/30, 200.0/4/24, 220.0/2/20, 160.0/2/30.
- Schedule: HeadProductCode 101, QueueDepth 5, ReadinessOk t.
- Work Order/Active: all empty. No order is Running, because WO-001 is not built yet.
- Planned Stop: Active f, ConflictWithPLC f.

### Deviations from spec

None.

### Open questions for the PM

None. The two PM review notes (the `released_at` tie on `work_order_id`, and WO-1010 Planned and unattributed) are carried to WO-001 as written.

## PM Verification

**PM, 2026-10-22 — PASS.** Checked in the live database:

- [x] 5 products with the MES §7 values (101 250/4/20 … 105 160/2/30); 10 orders: Released 9, Planned 1 (WO-1010); `work_order_seq` at 1010.
- [x] Schedule 1–5 = WO-1001 … WO-1005; readiness 6 rows, WO-1001–1005 ready, WO-1006 not ready (proof missing).
- [x] Due offsets from `created_at`: 60, 65, 70, **6**, 75, 80, 85, 90, **20 (Rush)**, 100 min — WO-1004 and WO-1009 tight as designed. Seed ran 17:07:34Z.
- [x] All `released_at` equal `created_at` (the tie WO-001 breaks on `work_order_id`). `v_work_order_table` 10 rows, 0 m good / 0 m reject; `work_order_run` empty.
- [x] `db/002_seed.sql` shasum `4f6eccf7…f80`, unchanged. No container restarted (Ignition 17:00:29Z is UNS-004's restart).
- UNS-004 query tags now carry values (Dev report; Walker can see them in the Tag Browser).

User-facing impact: the Product, Schedule and readiness tags show seed data; the Hour 2 due-date clock started 17:07:34Z.
