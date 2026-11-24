-- DB-002: seed data. Run once, after 001_schema.sql, on an empty schema.
-- Source: docs/db_schema.md (MES Requirements section 3 and About section 3).
-- Due dates are relative to now(). WO-1004 and WO-1009 are tight only from the moment this runs.

insert into mes.product_master
  (product_code, product_family, substrate, lanes, colors, ideal_speed_mpm, scheduled_changeover_s)
values
  (101, 'Snack bag film',       'OPP', 4, 5, 250, 20),
  (102, 'Coffee pouch film',    'PET', 2, 8, 180, 30),
  (103, 'Frozen food bag film', 'PE',  4, 4, 200, 24),
  (104, 'Candy wrapper film',   'OPP', 2, 6, 220, 20),
  (105, 'Pet food bag film',    'PE',  2, 7, 160, 30);

-- Ten work orders: two loops of the five jobs in sim order. Planned meters equal
-- what the sim makes per job visit. Insert order sets the IDs WO-1001 to WO-1010.
-- Two are tight (WO-1004, WO-1009). Nine are Released so run attribution works.
insert into mes.work_order
  (product_code, planned_meters, due_date, priority, status, released_at)
values
  (101, 600, now() + interval '60 minutes',  'Normal', 'Released', now()),
  (102, 375, now() + interval '65 minutes',  'Normal', 'Released', now()),
  (103, 450, now() + interval '70 minutes',  'Normal', 'Released', now()),
  (104, 525, now() + interval '6 minutes',   'Normal', 'Released', now()),
  (105, 365, now() + interval '75 minutes',  'Normal', 'Released', now()),
  (101, 600, now() + interval '80 minutes',  'Normal', 'Released', now()),
  (102, 375, now() + interval '85 minutes',  'Normal', 'Released', now()),
  (103, 450, now() + interval '90 minutes',  'Normal', 'Released', now()),
  (104, 525, now() + interval '20 minutes',  'Rush',   'Released', now()),
  (105, 365, now() + interval '100 minutes', 'Normal', 'Planned',  null);

-- Readiness: first five fully ready. WO-1006 is missing proof approval (a not-ready demo).
-- The rest have no row, which means nothing has been entered yet.
insert into mes.job_readiness (work_order_id, plates_ready, ink_ready, proof_approved, entered_by)
values
  ('WO-1001', true, true, true,  'seed'),
  ('WO-1002', true, true, true,  'seed'),
  ('WO-1003', true, true, true,  'seed'),
  ('WO-1004', true, true, true,  'seed'),
  ('WO-1005', true, true, true,  'seed'),
  ('WO-1006', true, true, false, 'seed');

-- Schedule: the first five in sim order (101 to 105). Planned start and end are filled
-- in by the scheduling backend (SCHED-001). The rest stay unscheduled.
insert into mes.schedule_entry (work_order_id, sequence, scheduled_changeover_s)
select wo.work_order_id,
       row_number() over (order by wo.work_order_id),
       pm.scheduled_changeover_s
from mes.work_order wo
join mes.product_master pm on pm.product_code = wo.product_code
where wo.work_order_id in ('WO-1001','WO-1002','WO-1003','WO-1004','WO-1005');
