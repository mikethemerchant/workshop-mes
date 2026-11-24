# mes.schedule -- the Press 1 schedule: plan against actual (SCHED-001). An mes.engine handler.
#
# Rules of docs/tasks/task_SCHED-001_plan_vs_actual.md (MES section 4 FR1-3, FR7):
#   replan            -> planned_start / planned_end for every non-Cancelled entry in sequence order:
#                        changeover + planned meters at ideal speed, chained from the anchor
#                        (entry 1's actual start, else now). Only when asked: first tick after load
#                        if a planned_start is NULL, the page's Replan button, later SCHED-003.
#   sync_from_runs    -> actual start / end and status from mes.work_order_run. Actual start is the
#                        previous run's end (the product change that began this job's Setup) when that
#                        is <= 300 s before this run's start, else this run's start. Idempotent.
#                        First tick after load, then on every run_start and product_change.
#   schedule_released -> append Released orders with no entry (Rush first, then due date), then replan.
#                        Page button only.
# An entry is Cancelled when its own status is Cancelled or its work order's is: never touched.
#
# Readiness (docs/tasks/task_SCHED-002_readiness.md, MES section 4 FR4):
#   save_readiness    -> upsert mes.job_readiness (plates, ink, proof, entered_by) for an order in Planned,
#                        Released or On Hold; refused once it has started. is_ready is generated, never written.
#   sync_from_runs    -> when an entry goes Queued -> Running, started_without_readiness = readiness at that
#                        moment is missing or not ready. Set once, never recomputed; not on the first-tick
#                        backfill. An unscheduled order that starts not ready is only logged.
#
# Override and reconcile (docs/tasks/task_SCHED-003_override_reconcile.md, MES section 4.1):
#   move_up           -> plan edit, no record: swap a Queued entry with the Queued entry just before it, replan.
#   move_to_front     -> rush override: the order's Queued entry takes the head's sequence, the Queued entries
#                        from the head up to it move down one, a mes.schedule_override row is inserted (one
#                        transaction); then replan and write Schedule/Override (JSON) and OverrideActive.
#   reconcile         -> on run_start, before the sync: PLC product against the head (lowest-sequence Queued
#                        entry) and the active overrides (their order's entry still Queued). The outcome goes
#                        to reconcile_result of the entry of the order WO-001 attributed the run to, and to
#                        Schedule/ReconcileResult; OverrideActive is refreshed after the sync.
#
# plan(), actuals(), append_order() and handle() are pure except for calls on a db argument (and a tags
# writer for handle), so the offline replays (ignition/tests/replay_schedule.py, test_schedule_override.py)
# run this file under Python 3 with a fake db. Times are epoch ms everywhere except inside _SqlDb.
#
# Writes: UPDATE / INSERT on mes.schedule_entry, upsert on mes.job_readiness, INSERT on mes.schedule_override
# (connection 'mes'); memory tags Schedule/Override, OverrideActive, ReconcileResult. Never deletes.

import json
import time

DATABASE = 'mes'
SETUP_MAX_S = 300       # previous run end further back than this before a run start: no Setup observed
STORE_VERSION = 1
STORE_KEY = 'mes.schedule.v%d' % STORE_VERSION
SYNC_EVENTS = ('run_start', 'product_change')
READINESS_EDITABLE = ('Planned', 'Released', 'On Hold')
OVERRIDE_REASONS = ('customer expedite', 'material availability', 'other')
TAG_FOLDER = '[default]40Printing/Dallas/Press Room/Press 1/Schedule/'
NORMAL, AUTHORIZED, NOT_FOLLOWED, UNRECORDED = (
	'Normal start', 'Authorized override', 'Override not followed', 'Unrecorded deviation')

log = system.util.getLogger('mes.schedule')


# ---------- store ----------

def _store():
	g = system.util.getGlobals()
	st = g.get(STORE_KEY)
	if st is None:
		st = g[STORE_KEY] = {'version': STORE_VERSION, 'loaded': False}
		log.info('schedule store created %s' % STORE_KEY)
	return st


# ---------- planning (pure) ----------

def _cancelled(e):
	return e['status'] == 'Cancelled' or e.get('wo_status') == 'Cancelled'


def _live(entries):
	return sorted([e for e in entries if not _cancelled(e)], key=lambda e: e['sequence'])


def planned_ms(e):
	"""Scheduled changeover + planned meters at ideal speed, in ms."""
	s = e['scheduled_changeover_s'] + float(e['planned_meters']) / float(e['ideal_speed_mpm']) * 60.0
	return int(round(s * 1000))


def anchor(entries, now_ms):
	"""Rule 1: actual start of the lowest-sequence entry if it has one, else now."""
	live = _live(entries)
	if live and live[0].get('actual_start') is not None:
		return live[0]['actual_start']
	return now_ms


def plan(entries, anchor_ms):
	"""Rule 1. entries: dicts with entry_id, sequence, status, wo_status, scheduled_changeover_s,
	planned_meters, ideal_speed_mpm. Returns [(entry_id, planned_start_ms, planned_end_ms)]."""
	out = []
	t = anchor_ms
	for e in _live(entries):
		end = t + planned_ms(e)
		out.append((e['entry_id'], t, end))
		t = end
	return out


def actuals(entries, runs):
	"""Rule 2. runs: every mes.work_order_run row (work_order_id, start, end), any order.
	Returns [(entry_id, status, actual_start_ms, actual_end_ms)] for each non-Cancelled entry with a run.
	An order with several runs: first run's start, last run's end, Running while the last is open."""
	runs = sorted(runs, key=lambda r: r['start'])
	out = []
	for e in _live(entries):
		mine = [r for r in runs if r['work_order_id'] == e['work_order_id']]
		if not mine:
			continue  # stays as it is (Queued)
		first, last = mine[0], mine[-1]
		start = first['start']
		before = [r for r in runs if r['start'] < first['start'] and r['end'] is not None]
		if before:
			prev_end = before[-1]['end']
			if 0 <= first['start'] - prev_end <= SETUP_MAX_S * 1000:
				start = prev_end
		status = 'Running' if last['end'] is None else 'Done'
		out.append((e['entry_id'], status, start, last['end']))
	return out


def append_order(released, max_seq):
	"""Rule 3. released: Released orders with no entry (work_order_id, priority, due_date ms,
	scheduled_changeover_s). Returns [(work_order_id, sequence, scheduled_changeover_s)]."""
	order = sorted(released, key=lambda o: (o['priority'] != 'Rush', o['due_date'], o['work_order_id']))
	seq = max_seq or 0
	out = []
	for o in order:
		seq += 1
		out.append((o['work_order_id'], seq, o['scheduled_changeover_s']))
	return out


# ---------- actions on a db (pure except for db calls) ----------

def _iso_s(ms):
	return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ms // 1000))


def do_replan(db, now_ms, anchor_ms=None):
	"""Rule 1. anchor_ms, when given, replaces the rule-1 anchor. Returns 'ok: ...'."""
	entries = db.entries()
	a = anchor_ms if anchor_ms is not None else anchor(entries, now_ms)
	by_id = dict((e['entry_id'], e) for e in entries)
	changed = 0
	for entry_id, start, end in plan(entries, a):
		e = by_id[entry_id]
		if (e['planned_start'], e['planned_end']) != (start, end):
			db.set_plan(entry_id, start, end)
			changed += 1
	n = len(_live(entries))
	return 'ok: replanned %d entries from anchor %s, %d changed' % (n, _iso_s(a), changed)


def flag_on_start(readiness):
	"""SCHED-002 rule 2. readiness: the order's mes.job_readiness row (dict with is_ready) or None.
	Returns started_without_readiness."""
	return readiness is None or not readiness['is_ready']


def do_sync(db, flag=True):
	"""Rule 2. Writes only the entries whose status or actual times changed. Returns 'ok: ...'.
	flag (SCHED-002): set started_without_readiness on each Queued -> Running entry, from the readiness
	read now, and note unscheduled orders that start not ready. False on the first-tick backfill."""
	entries = db.entries()
	by_id = dict((e['entry_id'], e) for e in entries)
	runs = db.runs()
	changed = []
	notes = []
	for entry_id, status, start, end in actuals(entries, runs):
		e = by_id[entry_id]
		if (e['status'], e['actual_start'], e['actual_end']) != (status, start, end):
			db.set_actual(entry_id, status, start, end)
			changed.append('%s %s' % (e['work_order_id'], status))
			if flag and e['status'] == 'Queued' and e['actual_start'] is None and status == 'Running':
				swr = flag_on_start(db.readiness(e['work_order_id']))
				db.set_started_without_readiness(entry_id, swr)
				if swr:
					notes.append('started without readiness: %s' % e['work_order_id'])
	if flag:
		scheduled = set(e['work_order_id'] for e in entries)
		for wo in sorted(set(r['work_order_id'] for r in runs if r['end'] is None and r['work_order_id'] is not None)):
			if wo not in scheduled and flag_on_start(db.readiness(wo)):
				notes.append('started without readiness: %s (unscheduled)' % wo)
	return 'ok: synced, %d changed%s%s' % (len(changed), (' (%s)' % ', '.join(changed)) if changed else '',
										   ''.join('; ' + n for n in notes))


def check_readiness_edit(status):
	"""SCHED-002 rule 1. status: the work order's status. Returns None, or the 'error: ...' refusal."""
	if status is None:
		return 'error: no such work order'
	if status not in READINESS_EDITABLE:
		return 'error: readiness cannot be changed on a %s order (only %s)' % (status, ', '.join(READINESS_EDITABLE))
	return None


def do_save_readiness(db, wo, plates, ink, proof, entered_by):
	"""SCHED-002 rule 1: upsert mes.job_readiness. Returns 'ok: ...' or 'error: ...'."""
	if not wo:
		return 'error: pick a work order'
	entered_by = (entered_by or '').strip()
	if not entered_by:
		return 'error: entered by is required'
	err = check_readiness_edit(db.order_status(wo))
	if err:
		return err
	plates, ink, proof = bool(plates), bool(ink), bool(proof)
	if not db.upsert_readiness(wo, plates, ink, proof, entered_by):
		return 'error: %s changed status, readiness not saved' % wo
	return 'ok: readiness %s: plates %s, ink %s, proof %s, entered by %s (%s)' % (
		wo, _flag(plates), _flag(ink), _flag(proof), entered_by, 'ready' if plates and ink and proof else 'not ready')


def do_schedule_released(db, now_ms):
	"""Rule 3, then replan. Returns 'ok: ...'."""
	new = append_order(db.unscheduled_released(), db.max_sequence())
	for wo, seq, changeover in new:
		db.insert_entry(wo, seq, changeover)
	added = ', '.join('%d %s' % (seq, wo) for wo, seq, _ in new) or 'none'
	return 'ok: scheduled %d released orders (%s); %s' % (len(new), added, do_replan(db, now_ms)[4:])


# ---------- override and reconcile (pure, SCHED-003) ----------

def _queued(entries):
	return [e for e in _live(entries) if e['status'] == 'Queued']


def head(entries):
	"""The head of the schedule after overrides: the lowest-sequence Queued entry, or None."""
	q = _queued(entries)
	return q[0] if q else None


def active_overrides(overrides, entries):
	"""Override rows whose order's entry is still Queued (not yet started)."""
	queued = set(e['work_order_id'] for e in _queued(entries))
	return [o for o in overrides if o['work_order_id'] in queued]


def reconcile(product, head_entry, active):
	"""Rule 3: the section 4.1 outcome for a run start of product against the head, or None (no head)."""
	if head_entry is None:
		return None
	if product is not None and int(product) == int(head_entry['product_code']):
		if any(o['work_order_id'] == head_entry['work_order_id'] for o in active):
			return AUTHORIZED
		return NORMAL
	return NOT_FOLLOWED if active else UNRECORDED


def _queued_entry(entries, wo):
	return next((e for e in _queued(entries) if e['work_order_id'] == wo), None)


def resequence(entries, wo):
	"""Rule 2: wo's Queued entry to the head's sequence; the Queued entries with head <= sequence < its
	own move down one. Returns ([(entry_id, new_sequence)], override row), or None when wo has no Queued
	entry or is already the head."""
	e, h = _queued_entry(entries, wo), head(entries)
	if e is None or e['entry_id'] == h['entry_id']:
		return None
	frm, to = e['sequence'], h['sequence']
	changes = [(q['entry_id'], q['sequence'] + 1) for q in _queued(entries)
			   if q['entry_id'] != e['entry_id'] and to <= q['sequence'] < frm]
	changes.append((e['entry_id'], to))
	row = {'work_order_id': wo, 'product_code': e['product_code'], 'from_sequence': frm, 'to_sequence': to}
	return changes, row


def swap_up(entries, wo):
	"""Rule 1: swap wo's Queued entry with the Queued entry just before it. Returns
	[(entry_id, new_sequence)] and the entry it passed, or None when there is none to pass."""
	q = _queued(entries)
	i = next((i for i, e in enumerate(q) if e['work_order_id'] == wo), None)
	if not i:
		return None
	a, b = q[i - 1], q[i]
	return [(b['entry_id'], a['sequence']), (a['entry_id'], b['sequence'])], a


def _iso_ms(ms):
	return '%s.%03dZ' % (time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(ms // 1000)), ms % 1000)


def override_payload(row, override_ms):
	"""Rule 4: the Schedule/Override JSON (MES section 4.1 payload)."""
	return json.dumps({'work_order': row['work_order_id'], 'product_code': row['product_code'],
					   'from_sequence': row['from_sequence'], 'to_sequence': row['to_sequence'],
					   'reason': row['reason'], 'operator': row['entered_by'], 'timestamp': _iso_ms(override_ms)},
					  sort_keys=True)


# ---------- override and reconcile (on a db) ----------

def _check_move(entries, wo):
	if not wo:
		return 'error: pick a work order'
	if not any(e['work_order_id'] == wo for e in entries):
		return 'error: %s is not on the schedule' % wo
	if _queued_entry(entries, wo) is None:
		return 'error: %s is not Queued' % wo
	return None


def _apply(db, changes):
	"""Write new sequences; False when an entry left Queued since it was read (the action rolls back)."""
	return all(db.set_sequence(entry_id, seq) for entry_id, seq in changes)


def do_move_up(db, now_ms, wo):
	"""Rule 1: plan edit, no override record. Returns 'ok: ...' or 'error: ...'."""
	def fn(tx):
		entries = tx.entries()
		err = _check_move(entries, wo)
		if err:
			return err
		r = swap_up(entries, wo)
		if r is None:
			return 'error: %s is already the head of the schedule' % wo
		changes, passed = r
		if not _apply(tx, changes):
			return 'error: the schedule changed, nothing moved'
		return 'ok: %s moved up to %d, ahead of %s (plan edit, no override record)' % (
			wo, changes[0][1], passed['work_order_id'])
	res = db.atomic(fn)
	if res.startswith('ok'):
		res += '; ' + do_replan(db, now_ms)[4:]
	return res


def do_move_to_front(db, now_ms, wo, reason, entered_by, tags):
	"""Rule 2: rush override. tags(writes) writes memory tags and returns log lines.
	Returns 'ok: ...' or 'error: ...'."""
	entered_by = (entered_by or '').strip()
	if reason not in OVERRIDE_REASONS:
		return 'error: reason must be one of %s' % ', '.join(OVERRIDE_REASONS)
	if not entered_by:
		return 'error: entered by is required'
	done = {}

	def fn(tx):
		entries = tx.entries()
		err = _check_move(entries, wo)
		if err:
			return err
		r = resequence(entries, wo)
		if r is None:
			return 'error: %s is already the head of the schedule' % wo
		changes, row = r
		if not _apply(tx, changes):
			return 'error: the schedule changed, nothing moved'
		row = dict(row, reason=reason, entered_by=entered_by)
		done['row'], done['at'] = row, tx.insert_override(row)
		return 'ok: override %s (%s) %d -> %d, %s, entered by %s' % (
			wo, row['product_code'], row['from_sequence'], row['to_sequence'], reason, entered_by)
	res = db.atomic(fn)
	if not res.startswith('ok'):
		return res
	res += '; ' + do_replan(db, now_ms)[4:]
	active = bool(active_overrides(db.overrides(), db.entries()))
	lines = tags([('Override', override_payload(done['row'], done['at'])), ('OverrideActive', active)])
	return res + ''.join('; ' + l for l in lines)


def do_reconcile(db, ev):
	"""Rule 3 for one run_start event, before the sync. Returns (outcome or None, log line)."""
	entries = db.entries()
	h = head(entries)
	product = ev.get('product')
	if h is None:
		return None, 'reconcile: schedule empty'
	res = reconcile(product, h, active_overrides(db.overrides(), entries))
	line = 'reconcile: product=%s head=%s (%s): %s' % (product, h['work_order_id'], h['product_code'], res)
	run = next((r for r in db.runs() if r['start'] == ev['t'] and r['end'] is None), None)
	if run is None or run['work_order_id'] is None:
		return res, line + '; run unattributed, not stored'
	e = next((e for e in entries if e['work_order_id'] == run['work_order_id']), None)
	if e is None:
		return res, line + '; %s unscheduled, not stored' % run['work_order_id']
	db.set_reconcile(e['entry_id'], res)
	return res, line + '; stored on %s' % run['work_order_id']


def handle(st, sample, events, db, tags=None):
	"""One accepted engine sample and its events. Returns log lines; does not log.
	tags(writes), when given, writes memory tags and returns log lines (SCHED-003 rule 4)."""
	out = []
	writes = []
	for ev in [e for e in events if e['type'] == 'run_start']:
		res, line = do_reconcile(db, ev)  # before the sync marks the entry Running
		out.append(line)
		writes = [('ReconcileResult', res or '')]
	if not st['loaded']:
		out.append('first tick: sync ' + do_sync(db, flag=False))
		if any(e['planned_start'] is None for e in _live(db.entries())):
			out.append('first tick: planned_start missing, replan ' + do_replan(db, sample['t']))
		st['loaded'] = True
	elif any(e['type'] in SYNC_EVENTS for e in events):
		out.append('sync ' + do_sync(db))
	if writes:
		writes.append(('OverrideActive', bool(active_overrides(db.overrides(), db.entries()))))
		if tags is not None:
			out.extend(tags(writes))
	return out


# ---------- table shaping (pure) ----------

# Table column order and headers. The view's props.columns use the same order.
SCHEDULE_COLUMNS = [
	('seq',          'Seq'),
	('wo',           'Work order'),
	('product',      'Product'),
	('status',       'Status'),
	('planned_start','Planned start'),
	('planned_end',  'Planned end'),
	('planned_s',    'Planned s'),
	('actual_start', 'Actual start'),
	('actual_end',   'Actual end'),
	('actual_s',     'Actual s'),
	('gap_s',        'Gap s'),
	('plates',       'Plates'),
	('ink',          'Ink'),
	('proof',        'Proof'),
	('swr',          'Started w/o readiness'),
	('reconcile',    'Reconcile'),
]


def _secs(a, b):
	return '' if a is None or b is None else int(round((b - a) / 1000.0))


def _time(fmt, ms):
	return '' if ms is None else fmt(ms, 'HH:mm:ss')


def _flag(v):
	return 'yes' if v else 'no'


def shape_schedule(rows, fmt):
	"""Rows of mes.v_schedule_table + status (dicts, times as epoch ms or None) -> table rows.
	fmt(ms, pattern) formats an epoch-ms time. Keys in SCHEDULE_COLUMNS order."""
	out = []
	for r in rows:
		planned = _secs(r['planned_start'], r['planned_end'])
		actual = _secs(r['actual_start_ts'], r['actual_end_ts'])
		gap = actual - planned if r['status'] == 'Done' and actual != '' and planned != '' else ''
		out.append({
			'seq': r['sequence'],
			'wo': r['work_order_id'],
			'product': r['product_code'],
			'status': r['status'],
			'planned_start': _time(fmt, r['planned_start']),
			'planned_end': _time(fmt, r['planned_end']),
			'planned_s': planned,
			'actual_start': _time(fmt, r['actual_start_ts']),
			'actual_end': _time(fmt, r['actual_end_ts']),
			'actual_s': actual,
			'gap_s': gap,
			'plates': _flag(r['plates_ready']),
			'ink': _flag(r['ink_ready']),
			'proof': _flag(r['proof_approved']),
			'swr': 'YES' if r.get('started_without_readiness') else '',
			'reconcile': r.get('reconcile_result') or '',
		})
	return out


# ---------- database ----------

def _ms(v):
	return None if v is None else v.getTime()


def _ts(ms):
	return None if ms is None else system.date.fromMillis(int(ms))


class _SqlDb(object):
	"""The db interface of the actions above on PostgreSQL, connection 'mes'. Parameterised SQL only.
	SCHED-003: atomic() runs one action in one transaction (self.tx), committed only on an 'ok' result."""

	tx = None  # set by atomic()

	def atomic(self, fn):
		"""fn(db) in one transaction: commit on an 'ok' result, roll back otherwise."""
		tx = system.db.beginTransaction(DATABASE)
		try:
			db = _SqlDb()
			db.tx = tx
			res = fn(db)
			if res.startswith('ok'):
				system.db.commitTransaction(tx)
			else:
				system.db.rollbackTransaction(tx)
			return res
		except:
			system.db.rollbackTransaction(tx)
			raise
		finally:
			system.db.closeTransaction(tx)

	def entries(self):
		ds = system.db.runPrepQuery(
			"SELECT se.entry_id, se.work_order_id, se.sequence, se.status, se.scheduled_changeover_s, "
			"se.planned_start, se.planned_end, se.actual_start_ts, se.actual_end_ts, "
			"wo.status AS wo_status, wo.product_code, wo.planned_meters, "
			"pm.ideal_speed_mpm::float8 AS ideal_speed_mpm "
			"FROM mes.schedule_entry se "
			"JOIN mes.work_order wo ON wo.work_order_id = se.work_order_id "
			"JOIN mes.product_master pm ON pm.product_code = wo.product_code "
			"ORDER BY se.sequence, se.entry_id", [], DATABASE, self.tx)
		return [{'entry_id': r['entry_id'], 'work_order_id': r['work_order_id'], 'sequence': r['sequence'],
				 'status': r['status'], 'scheduled_changeover_s': r['scheduled_changeover_s'],
				 'planned_start': _ms(r['planned_start']), 'planned_end': _ms(r['planned_end']),
				 'actual_start': _ms(r['actual_start_ts']), 'actual_end': _ms(r['actual_end_ts']),
				 'wo_status': r['wo_status'], 'product_code': r['product_code'], 'planned_meters': r['planned_meters'],
				 'ideal_speed_mpm': r['ideal_speed_mpm']} for r in ds]

	def runs(self):
		ds = system.db.runPrepQuery(
			"SELECT work_order_id, start_ts, end_ts FROM mes.work_order_run ORDER BY start_ts, run_id", [], DATABASE)
		return [{'work_order_id': r['work_order_id'], 'start': _ms(r['start_ts']), 'end': _ms(r['end_ts'])}
				for r in ds]

	def set_plan(self, entry_id, start, end):
		system.db.runPrepUpdate(
			"UPDATE mes.schedule_entry SET planned_start = ?, planned_end = ? WHERE entry_id = ?",
			[_ts(start), _ts(end), entry_id], DATABASE)

	def set_actual(self, entry_id, status, start, end):
		system.db.runPrepUpdate(
			"UPDATE mes.schedule_entry SET status = ?, actual_start_ts = ?, actual_end_ts = ? "
			"WHERE entry_id = ? AND status <> 'Cancelled'",
			[status, _ts(start), _ts(end), entry_id], DATABASE)

	def unscheduled_released(self):
		ds = system.db.runPrepQuery(
			"SELECT wo.work_order_id, wo.priority, wo.due_date, pm.scheduled_changeover_s "
			"FROM mes.work_order wo JOIN mes.product_master pm ON pm.product_code = wo.product_code "
			"WHERE wo.status = 'Released' AND NOT EXISTS "
			"(SELECT 1 FROM mes.schedule_entry se WHERE se.work_order_id = wo.work_order_id)", [], DATABASE)
		return [{'work_order_id': r['work_order_id'], 'priority': r['priority'], 'due_date': _ms(r['due_date']),
				 'scheduled_changeover_s': r['scheduled_changeover_s']} for r in ds]

	def max_sequence(self):
		ds = system.db.runPrepQuery("SELECT max(sequence) FROM mes.schedule_entry", [], DATABASE)
		return ds[0][0] if len(ds) else None

	def insert_entry(self, wo, seq, changeover):
		system.db.runPrepUpdate(
			"INSERT INTO mes.schedule_entry (work_order_id, sequence, scheduled_changeover_s) VALUES (?, ?, ?)",
			[wo, seq, changeover], DATABASE)

	def readiness(self, wo):
		ds = system.db.runPrepQuery(
			"SELECT plates_ready, ink_ready, proof_approved, is_ready, entered_by FROM mes.job_readiness "
			"WHERE work_order_id = ?", [wo], DATABASE)
		if not len(ds):
			return None
		r = ds[0]
		return {'plates_ready': r['plates_ready'], 'ink_ready': r['ink_ready'], 'proof_approved': r['proof_approved'],
				'is_ready': r['is_ready'], 'entered_by': r['entered_by']}

	def set_started_without_readiness(self, entry_id, flag):
		system.db.runPrepUpdate(
			"UPDATE mes.schedule_entry SET started_without_readiness = ? WHERE entry_id = ?",
			[bool(flag), entry_id], DATABASE)

	def order_status(self, wo):
		ds = system.db.runPrepQuery("SELECT status FROM mes.work_order WHERE work_order_id = ?", [wo], DATABASE)
		return ds[0][0] if len(ds) else None

	def upsert_readiness(self, wo, plates, ink, proof, entered_by):
		"""The status guard is in the SELECT, so an order that started since the check is not written."""
		return system.db.runPrepUpdate(
			"INSERT INTO mes.job_readiness (work_order_id, plates_ready, ink_ready, proof_approved, entered_by) "
			"SELECT work_order_id, ?, ?, ?, ? FROM mes.work_order "
			"WHERE work_order_id = ? AND status IN ('Planned', 'Released', 'On Hold') "
			"ON CONFLICT (work_order_id) DO UPDATE SET plates_ready = EXCLUDED.plates_ready, "
			"ink_ready = EXCLUDED.ink_ready, proof_approved = EXCLUDED.proof_approved, "
			"entered_by = EXCLUDED.entered_by, entered_at = now()",
			[plates, ink, proof, entered_by, wo], DATABASE) > 0

	# SCHED-003
	def overrides(self):
		ds = system.db.runPrepQuery(
			"SELECT override_id, work_order_id, product_code, from_sequence, to_sequence, reason, entered_by, "
			"entered_at FROM mes.schedule_override ORDER BY entered_at, override_id", [], DATABASE, self.tx)
		return [{'override_id': r['override_id'], 'work_order_id': r['work_order_id'],
				 'product_code': r['product_code'], 'from_sequence': r['from_sequence'],
				 'to_sequence': r['to_sequence'], 'reason': r['reason'], 'entered_by': r['entered_by'],
				 'entered_at': _ms(r['entered_at'])} for r in ds]

	def set_sequence(self, entry_id, seq):
		"""Guarded on Queued: 0 rows when the entry started since it was read."""
		return system.db.runPrepUpdate(
			"UPDATE mes.schedule_entry SET sequence = ? WHERE entry_id = ? AND status = 'Queued'",
			[seq, entry_id], DATABASE, self.tx) > 0

	def insert_override(self, row):
		"""INSERT mes.schedule_override (entered_at default, published_at NULL). Returns entered_at, epoch ms."""
		ds = system.db.runPrepQuery(
			"INSERT INTO mes.schedule_override (work_order_id, product_code, from_sequence, to_sequence, reason, "
			"entered_by) VALUES (?, ?, ?, ?, ?, ?) RETURNING entered_at",
			[row['work_order_id'], row['product_code'], row['from_sequence'], row['to_sequence'], row['reason'],
			 row['entered_by']], DATABASE, self.tx)
		return _ms(ds[0][0])

	def set_reconcile(self, entry_id, result):
		system.db.runPrepUpdate(
			"UPDATE mes.schedule_entry SET reconcile_result = ? WHERE entry_id = ?", [result, entry_id], DATABASE)


# ---------- actions (page buttons, SCHED-003) ----------

def _now_ms():
	return int(time.time() * 1000)


def _run(name, fn):
	"""Run one action, log its result string at INFO (a failure at ERROR with the traceback)."""
	try:
		res = fn()
	except:
		import sys, traceback
		log.error('%s failed: %s' % (name, traceback.format_exc()))
		return 'error: %s' % sys.exc_info()[1]
	log.info('%s %s' % (name, res))
	return res


def replan(anchor_ms=None):
	"""Recompute planned start / end for every non-Cancelled entry. anchor_ms overrides rule 1's anchor."""
	return _run('replan', lambda: do_replan(_SqlDb(), _now_ms(), anchor_ms))


def sync_from_runs():
	return _run('sync', lambda: do_sync(_SqlDb()))


def schedule_released():
	return _run('schedule_released', lambda: do_schedule_released(_SqlDb(), _now_ms()))


def save_readiness(wo, plates, ink, proof, entered_by):
	"""SCHED-002: manual readiness entry from the page. Returns 'ok: ...' / 'error: ...', logged at INFO."""
	return _run('save_readiness', lambda: do_save_readiness(_SqlDb(), wo, plates, ink, proof, entered_by))


def move_up(wo):
	"""SCHED-003 rule 1: plan edit from the page, no override record."""
	return _run('move_up', lambda: do_move_up(_SqlDb(), _now_ms(), wo))


def move_to_front(wo, reason, entered_by):
	"""SCHED-003 rule 2: rush override from the page."""
	return _run('move_to_front', lambda: do_move_to_front(_SqlDb(), _now_ms(), wo, reason, entered_by, _write_tags))


def _write_tags(writes):
	"""Rule 4: write [(tag name under Schedule/, value)] in one writeBlocking. Returns one line per tag with
	its write quality; a bad quality is also logged here at WARN, never raised."""
	paths = [TAG_FOLDER + name for name, _ in writes]
	qs = system.tag.writeBlocking(paths, [v for _, v in writes])
	lines = []
	for (name, _), q in zip(writes, qs):
		lines.append('tag %s write %s' % (name, q))
		if not q.isGood():
			log.warn('tag %s write quality %s' % (paths[len(lines) - 1], q))
	return lines


# ---------- engine handler ----------

def on_tick(sample, events):
	st = _store()
	try:
		lines = handle(st, sample, events, _SqlDb(), _write_tags)
	except:
		import traceback
		log.error('handle failed: %s' % traceback.format_exc())
		return
	for line in lines:
		log.info(line)


# ---------- read API for the test page ----------

def _fmt(ms, pattern):
	return system.date.format(system.date.fromMillis(int(ms)), pattern)


VIEW_COLUMNS = ['sequence', 'work_order_id', 'product_code', 'plates_ready', 'ink_ready', 'proof_approved',
				'planned_start', 'planned_end', 'actual_start_ts', 'actual_end_ts', 'status', 'scheduled_changeover_s',
				'started_without_readiness', 'reconcile_result']
TIME_COLUMNS = ('planned_start', 'planned_end', 'actual_start_ts', 'actual_end_ts')


def schedule_rows():
	ds = system.db.runPrepQuery(
		"SELECT v.*, se.status, se.scheduled_changeover_s FROM mes.v_schedule_table v "
		"JOIN mes.schedule_entry se ON se.work_order_id = v.work_order_id ORDER BY v.sequence", [], DATABASE)
	rows = []
	for rec in ds:
		row = dict((c, rec[c]) for c in VIEW_COLUMNS)
		for c in TIME_COLUMNS:
			row[c] = _ms(row[c])
		rows.append(row)
	return shape_schedule(rows, _fmt)


SEP = u' \u00b7 '  # middle dot, as mes.ui; an escape so the file stays ASCII for Jython 2.7


def readiness_targets():
	"""Dropdown options: orders whose readiness may be edited, 'WO-1012 . 101 . Released . ready'."""
	ds = system.db.runPrepQuery(
		"SELECT wo.work_order_id, wo.product_code, wo.status, coalesce(jr.is_ready, false) AS is_ready "
		"FROM mes.work_order wo LEFT JOIN mes.job_readiness jr ON jr.work_order_id = wo.work_order_id "
		"WHERE wo.status IN ('Planned', 'Released', 'On Hold') ORDER BY wo.work_order_id", [], DATABASE)
	return [{'value': r['work_order_id'],
			 'label': SEP.join([r['work_order_id'], '%s' % r['product_code'], r['status'],
								'ready' if r['is_ready'] else 'not ready'])} for r in ds]


def readiness_of(wo):
	"""The order's readiness row for pre-filling the panel; all False when it has none."""
	r = _SqlDb().readiness(wo) if wo else None
	if r is None:
		return {'plates_ready': False, 'ink_ready': False, 'proof_approved': False, 'entered_by': ''}
	return r


def override_targets():
	"""Dropdown options: Queued entries in sequence order, '3 . WO-1004 . 104'."""
	return [{'value': e['work_order_id'], 'label': SEP.join(['%s' % e['sequence'], e['work_order_id'],
															 '%s' % e['product_code']])}
			for e in _queued(_SqlDb().entries())]


def override_rows():
	"""The override records for the page, newest first: time, order, from -> to, reason, by, active."""
	db = _SqlDb()
	overrides = db.overrides()
	active = set(o['override_id'] for o in active_overrides(overrides, db.entries()))
	return [{'time': _time(_fmt, o['entered_at']), 'wo': o['work_order_id'], 'product': o['product_code'],
			 'move': '%d -> %d' % (o['from_sequence'], o['to_sequence']), 'reason': o['reason'],
			 'by': o['entered_by'], 'active': _flag(o['override_id'] in active)} for o in reversed(overrides)]

