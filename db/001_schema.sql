-- DB-001: MES schema (PostgreSQL 16). Run once on an empty mes schema.
-- Source: docs/db_schema.md. Raw samples are not stored here. Timebase holds history.

create schema if not exists mes;

create sequence mes.work_order_seq start 1001;

-- Master data: ideal speed and scheduled changeover live here, not in the sim.
create table mes.product_master (
  product_code           integer      primary key,
  product_family         text         not null,
  substrate              text         not null,
  lanes                  smallint     not null check (lanes in (2, 4)),
  colors                 smallint     not null check (colors between 1 and 8),
  ideal_speed_mpm        numeric(6,1) not null check (ideal_speed_mpm > 0),
  scheduled_changeover_s integer      not null check (scheduled_changeover_s >= 0)
);

create table mes.work_order (
  work_order_id  text        primary key default ('WO-' || nextval('mes.work_order_seq')),
  product_code   integer     not null references mes.product_master,
  planned_meters integer     not null check (planned_meters > 0),
  due_date       timestamptz not null,
  priority       text        not null default 'Normal' check (priority in ('Normal', 'Rush')),
  status         text        not null default 'Planned'
                 check (status in ('Planned','Released','Running','Complete','Closed','On Hold','Cancelled')),
  hold_reason    text,
  notes          text,
  created_at     timestamptz not null default now(),
  released_at    timestamptz,
  started_at     timestamptz,
  completed_at   timestamptz,
  closed_at      timestamptz
);

-- One press run attributed to one work order.
create table mes.work_order_run (
  run_id             bigint      generated always as identity primary key,
  work_order_id      text        not null references mes.work_order,
  product_code       integer     not null references mes.product_master,
  start_ts           timestamptz not null,
  end_ts             timestamptz,
  good_meters        integer     not null default 0,
  reject_meters      integer     not null default 0,
  attributed_by      text        not null default 'auto' check (attributed_by in ('auto', 'manual')),
  -- added: counter baselines so deltas and restarts can be checked later
  start_good_counter   integer,
  start_reject_counter integer,
  counter_reset_seen   boolean   not null default false,
  check (end_ts is null or end_ts >= start_ts)
);

create table mes.schedule_entry (
  entry_id               bigint  generated always as identity primary key,
  work_order_id          text    not null unique references mes.work_order,
  sequence               integer not null,
  planned_start          timestamptz,
  planned_end            timestamptz,
  scheduled_changeover_s integer not null,
  status                 text    not null default 'Queued'
                         check (status in ('Queued','Running','Done','Cancelled')),
  -- added: plan against actual and the reconcile result (MES section 4 test visual)
  actual_start_ts          timestamptz,
  actual_end_ts            timestamptz,
  reconcile_result         text check (reconcile_result in
                           ('Normal start','Authorized override','Override not followed','Unrecorded deviation')),
  started_without_readiness boolean not null default false
);

create table mes.schedule_override (
  override_id   bigint      generated always as identity primary key,
  work_order_id text        not null references mes.work_order,
  product_code  integer     not null references mes.product_master,
  from_sequence integer     not null,
  to_sequence   integer     not null,
  reason        text        not null check (reason in ('customer expedite','material availability','other')),
  entered_by    text        not null,
  entered_at    timestamptz not null default now(),
  published_at  timestamptz            -- added: when it was published to the UNS
);

create table mes.job_readiness (
  work_order_id  text        primary key references mes.work_order,
  plates_ready   boolean     not null default false,
  ink_ready      boolean     not null default false,
  proof_approved boolean     not null default false,
  is_ready       boolean     generated always as (plates_ready and ink_ready and proof_approved) stored,
  entered_by     text        not null,
  entered_at     timestamptz not null default now()
);

-- Open event = end_ts is null. Duration is the difference of sample timestamps.
create table mes.downtime_event (
  event_id      bigint       generated always as identity primary key,
  start_ts      timestamptz  not null,
  end_ts        timestamptz,
  duration_s    numeric(10,1),
  source        text         not null check (source in ('plc', 'derived', 'operator')),
  machine_state smallint     not null check (machine_state in (1, 2, 3)),
  fault_code    integer      not null default 0,
  class         text         not null check (class in
                ('Web break','Tunnel over-temp','RTO inhibit','Changeover','Changeover overrun','Unclassified alarm')),
  is_planned    boolean      not null,
  product_code  integer      references mes.product_master,
  work_order_id text         references mes.work_order,
  check (end_ts is null or end_ts >= start_ts),
  check ((class = 'Changeover') = is_planned)
);

create table mes.planned_stop (
  planned_stop_id bigint      generated always as identity primary key,
  reason          text        not null check (reason in ('clean plates', 'roll change')),
  start_ts        timestamptz not null,
  end_ts          timestamptz,
  operator        text        not null,
  product_code    integer     references mes.product_master,
  conflict_flag   boolean     not null default false,
  conflict_note   text,
  published_at    timestamptz,            -- added: when it was published to the UNS
  check (end_ts is null or end_ts >= start_ts)
);

-- One row per minute per product code (MES section 6 time basis).
create table mes.oee_interval (
  interval_start       timestamptz   not null,
  product_code         integer       not null references mes.product_master,
  work_order_id        text          references mes.work_order,
  running_s            numeric(6,1)  not null default 0 check (running_s >= 0),
  planned_production_s numeric(6,1)  not null default 0 check (planned_production_s >= 0),
  good_m               integer       not null default 0,
  reject_m             integer       not null default 0,
  ideal_m              numeric(10,1) not null default 0,
  primary key (interval_start, product_code)
);

create index on mes.work_order (product_code, status, released_at);  -- run attribution lookup
create index on mes.work_order (status, due_date);
create index on mes.work_order_run (work_order_id);
create index on mes.work_order_run (start_ts);
create index on mes.downtime_event (start_ts);
create index on mes.downtime_event (class);
create index on mes.oee_interval (work_order_id);

-- Views for the Day 2 test visuals. Each test visual reads one view.

-- Hour 2: work orders
create view mes.v_work_order_table as
select wo.work_order_id, wo.product_code, pm.product_family, wo.status, wo.priority,
       wo.planned_meters,
       coalesce(sum(r.good_meters), 0)   as good_m,
       coalesce(sum(r.reject_meters), 0) as reject_m,
       pm.lanes,
       min(r.start_ts) as run_start,
       max(r.end_ts)   as run_end,
       wo.due_date
from mes.work_order wo
join mes.product_master pm on pm.product_code = wo.product_code
left join mes.work_order_run r on r.work_order_id = wo.work_order_id
group by wo.work_order_id, pm.product_family, pm.lanes;

-- Hour 2: scheduling
create view mes.v_schedule_table as
select se.sequence, se.work_order_id, wo.product_code,
       coalesce(jr.plates_ready, false)   as plates_ready,
       coalesce(jr.ink_ready, false)      as ink_ready,
       coalesce(jr.proof_approved, false) as proof_approved,
       se.planned_start, se.planned_end, se.actual_start_ts, se.actual_end_ts,
       se.reconcile_result, se.started_without_readiness
from mes.schedule_entry se
join mes.work_order wo on wo.work_order_id = se.work_order_id
left join mes.job_readiness jr on jr.work_order_id = se.work_order_id
order by se.sequence;

-- Hour 3: downtime totals by class (closed events only)
create view mes.v_downtime_by_class as
select class, is_planned, count(*) as events, sum(duration_s) as total_s
from mes.downtime_event
where end_ts is not null
group by class, is_planned;

-- Hour 3: OEE by product code. Performance is capped at 100 for display (MES section 6).
create view mes.v_oee_by_product as
with s as (
  select product_code,
         sum(running_s)             as running_s,
         sum(planned_production_s)  as planned_s,
         sum(good_m)                as good_m,
         sum(reject_m)              as reject_m,
         sum(ideal_m)               as ideal_m
  from mes.oee_interval
  group by product_code
), r as (
  select product_code, running_s, planned_s, good_m, reject_m,
         running_s / nullif(planned_s, 0)                      as a,
         least(1, good_m / nullif(ideal_m, 0))                 as p,
         good_m::numeric / nullif(good_m + reject_m, 0)        as q
  from s
)
select product_code,
       round(100 * a, 1)           as availability_pct,
       round(100 * p, 1)           as performance_pct,
       round(100 * q, 1)           as quality_pct,
       round(100 * a * p * q, 1)   as oee_pct
from r;
