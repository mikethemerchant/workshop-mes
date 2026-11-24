# mes.workorder -- work orders from the PLC for Press 1 (WO-001). An mes.engine handler.
#
# mes.engine calls on_tick(sample, events) once per accepted sample. handle() applies the rules
# of docs/tasks/task_WO-001_run_attribution.md (MES section 3):
#   run_start      -> attribute the run to the earliest Released order for the product
#                     (ties on work_order_id), order Running, insert a work_order_run row
#   every sample   -> add the engine's meter deltas dg / dr to the open run, write absolute totals
#   restart        -> counter_reset_seen on the open run (the engine already gives delta 0)
#   product_change -> close the open run, order Complete (never on quantity)
#   Setup sample   -> rule 8 (BUG-002): with no run open, a meter delta is late production of the run
#                     just closed (meters hold in Setup): add it to that run, write absolute totals.
#                     Forgotten at the next run_start, a gap, or after rule 6.
# No Released order -> the run is unattributed: kept in memory only, no database row.
#
# handle() is pure except for calls on its db argument, so the offline replay
# (ignition/tests/replay_workorder.py) runs this file under Python 3 with a fake db.
#
# WO-003 adds the manual actions (create, release, hold, cancel, close, reassign) at the end of the file.
#
# State lives in system.util.getGlobals()[STORE_KEY]: the open run and the last few unattributed
# runs. Records live in PostgreSQL (connection 'mes'). After a gateway restart the store is empty
# and the first tick resumes or closes the open runs found in the database (rule 6).

import copy

DATABASE = 'mes'
MAX_UNATTRIBUTED = 20   # closed unattributed runs kept for WO-002, oldest first
STORE_VERSION = 1
STORE_KEY = 'mes.workorder.v%d' % STORE_VERSION

log = system.util.getLogger('mes.workorder')


# ---------- store ----------

def _new_store():
	return {
		'version': STORE_VERSION,
		'run': None,            # open run, see _open()
		'resumed': False,       # rule 6 done since the store was created
		'unattributed': [],     # closed unattributed runs, oldest first
		'last_closed': None,    # run closed by the last product_change, for rule 8
		'pending': [],          # WO-003: reassigns of the open run, applied on the timer thread
	}


def _store():
	g = system.util.getGlobals()
	st = g.get(STORE_KEY)
	if st is None:
		st = g[STORE_KEY] = _new_store()
		log.info('workorder store created %s' % STORE_KEY)
	st.setdefault('last_closed', None)  # stores created before rule 8
	st.setdefault('pending', [])        # stores created before WO-003
	return st


# ---------- rules (pure except for db calls) ----------

def _open(run_id, wo, product, start, good, reject, reset_seen):
	return {'run_id': run_id, 'work_order_id': wo, 'product': product, 'start': start,
			'good': good, 'reject': reject, 'reset_seen': reset_seen, 'attributed': wo is not None,
			'saved': (good, reject, reset_seen)}  # last totals written to the database


def _close(st, db, out, t, run):
	"""Rule 4: close run at t; complete its order. An unattributed run closes in memory."""
	run['end'] = t
	st['last_closed'] = run  # rule 8; for an unattributed run, the same dict as in st['unattributed']
	if not run['attributed']:
		st['unattributed'].append(run)
		del st['unattributed'][:-MAX_UNATTRIBUTED]
		out.append('unattributed run closed product=%s good=%g reject=%g' % (run['product'], run['good'], run['reject']))
		return
	db.end_run(run['run_id'], t, run['good'], run['reject'])
	planned = db.complete(run['work_order_id'], t)
	if planned is None:
		out.append('closed run %s, %s not Running: status left as is' % (run['run_id'], run['work_order_id']))
		return
	out.append('complete %s good=%g planned=%s diff=%g reject=%g' % (
		run['work_order_id'], run['good'], planned, run['good'] - planned, run['reject']))


def _resume(st, sample, db, out):
	"""Rule 6: first tick with an empty store. Resume or close the open runs in the database."""
	t = sample['t']
	runs = db.open_runs()  # newest first
	for i, r in enumerate(runs):
		run = _open(r['run_id'], r['work_order_id'], r['product_code'], None,
					r['good_meters'], r['reject_meters'], False)
		if i == 0 and r['product_code'] == sample.get('product'):
			st['run'] = run
			out.append('resumed run %s (%s) after gateway restart good=%s reject=%s' % (
				r['run_id'], r['work_order_id'], r['good_meters'], r['reject_meters']))
			continue
		out.append('open run %s (%s, product %s) not current after gateway restart: closing' % (
			r['run_id'], r['work_order_id'], r['product_code']))
		_close(st, db, out, t, run)
	st['last_closed'] = None  # rule 8: no late deltas for a run closed at startup
	if not runs and sample.get('state') == 1:
		out.append('press running at startup, no open run: waiting for next run_start')
	st['resumed'] = True


def _attribute(st, sample, ev, db, out):
	"""Rule 1: attribute the run that starts on this sample."""
	t = ev['t']
	product = ev.get('product')
	# Counter values before this sample, so good_meters = counter - start counter.
	good0 = sample['good'] - sample['dg'] if sample.get('good') is not None else None
	reject0 = sample['reject'] - sample['dr'] if sample.get('reject') is not None else None
	wo = db.next_released(product)
	if wo is None:
		st['run'] = _open(None, None, product, t, 0.0, 0.0, False)
		out.append('unattributed run product=%s' % product)
		return
	run_id = db.start_run(wo, product, t, good0, reject0)
	st['run'] = _open(run_id, wo, product, t, 0.0, 0.0, False)
	out.append('run %s attributed to %s product=%s' % (run_id, wo, product))


def _late_delta(st, sample, db, out):
	"""Rule 8: no run open. A meter delta on a Setup sample belongs to the run just closed."""
	closed = st.get('last_closed')
	dg, dr = sample.get('dg') or 0.0, sample.get('dr') or 0.0
	if closed is None or sample.get('state') != 2 or not (dg or dr):
		return
	closed['good'] += dg
	closed['reject'] += dr
	if closed['attributed']:
		# Absolute totals; end_ts and the order's status stay as rule 4 left them.
		db.update_run(closed['run_id'], closed['good'], closed['reject'], closed['reset_seen'])
		closed['saved'] = (closed['good'], closed['reject'], closed['reset_seen'])
		out.append('late delta good=+%g reject=+%g credited to run %s (%s)' % (
			dg, dr, closed['run_id'], closed['work_order_id']))
	else:
		out.append('late delta good=+%g reject=+%g credited to unattributed run product=%s' % (
			dg, dr, closed['product']))


def handle(st, sample, events, db):
	"""One accepted engine sample and its events. Returns log lines; does not log."""
	out = []
	if not st['resumed']:
		_resume(st, sample, db, out)
	order = {'gap': -1, 'restart': 0, 'product_change': 1, 'run_start': 2}  # rule 5
	for ev in sorted([e for e in events if e['type'] in order], key=lambda e: order[e['type']]):
		if ev['type'] == 'gap':
			st['last_closed'] = None  # rule 8: a delta across a gap is not late production
		elif ev['type'] == 'restart':
			if st['run'] is not None:
				st['run']['reset_seen'] = True
				out.append('sim restart during run %s: counter_reset_seen' % st['run']['run_id'])
		elif ev['type'] == 'product_change':
			if st['run'] is not None:
				run, st['run'] = st['run'], None
				_close(st, db, out, ev['t'], run)
		elif st['run'] is not None:
			# Setup -> Running without a product change: not covered by the rules, keep the open run.
			out.append('run_start product=%s while run %s is open: ignored' % (ev.get('product'), st['run']['run_id']))
		else:
			st['last_closed'] = None  # rule 8
			_attribute(st, sample, ev, db, out)
	run = st['run']
	if run is None:
		_late_delta(st, sample, db, out)
		return out
	run['good'] += sample.get('dg') or 0.0
	run['reject'] += sample.get('dr') or 0.0
	totals = (run['good'], run['reject'], run['reset_seen'])
	if run['attributed'] and totals != run['saved']:
		# Absolute values: a failed write heals on the next tick.
		db.update_run(run['run_id'], run['good'], run['reject'], run['reset_seen'])
		run['saved'] = totals
	return out


# ---------- database ----------

class _SqlDb(object):
	"""The db interface of handle() on PostgreSQL, connection 'mes'. Parameterised SQL only."""

	def _ts(self, t):
		return system.date.fromMillis(int(t))

	def next_released(self, product):
		ds = system.db.runPrepQuery(
			"SELECT work_order_id FROM mes.work_order WHERE product_code = ? AND status = 'Released' "
			"ORDER BY released_at, work_order_id LIMIT 1", [product], DATABASE)
		return ds[0][0] if len(ds) else None

	def start_run(self, wo, product, t, good_counter, reject_counter):
		ts = self._ts(t)
		system.db.runPrepUpdate(
			"UPDATE mes.work_order SET status = 'Running', started_at = coalesce(started_at, ?) "
			"WHERE work_order_id = ? AND status = 'Released'", [ts, wo], DATABASE)
		ds = system.db.runPrepQuery(
			"INSERT INTO mes.work_order_run (work_order_id, product_code, start_ts, attributed_by, "
			"start_good_counter, start_reject_counter) VALUES (?, ?, ?, 'auto', ?, ?) RETURNING run_id",
			[wo, product, ts, _int(good_counter), _int(reject_counter)], DATABASE)
		return ds[0][0]

	def update_run(self, run_id, good, reject, reset_seen):
		system.db.runPrepUpdate(
			"UPDATE mes.work_order_run SET good_meters = ?, reject_meters = ?, counter_reset_seen = ? "
			"WHERE run_id = ?", [_int(good), _int(reject), bool(reset_seen), run_id], DATABASE)

	def end_run(self, run_id, t, good, reject):
		system.db.runPrepUpdate(
			"UPDATE mes.work_order_run SET end_ts = ?, good_meters = ?, reject_meters = ? WHERE run_id = ?",
			[self._ts(t), _int(good), _int(reject), run_id], DATABASE)

	def complete(self, wo, t):
		"""Order Complete if it is Running. Returns its planned_meters, or None if no row changed."""
		ds = system.db.runPrepQuery(
			"UPDATE mes.work_order SET status = 'Complete', completed_at = ? "
			"WHERE work_order_id = ? AND status = 'Running' RETURNING planned_meters",
			[self._ts(t), wo], DATABASE)
		return ds[0][0] if len(ds) else None

	def open_runs(self):
		ds = system.db.runPrepQuery(
			"SELECT r.run_id, r.work_order_id, r.product_code, r.good_meters, r.reject_meters "
			"FROM mes.work_order_run r WHERE r.end_ts IS NULL ORDER BY r.start_ts DESC", [], DATABASE)
		cols = ['run_id', 'work_order_id', 'product_code', 'good_meters', 'reject_meters']
		return [dict(zip(cols, [row[c] for c in cols])) for row in ds]

	# WO-003 manual actions. Every write has a status guard in its WHERE and returns the rows changed;
	# atomic() runs one action in one transaction (self.tx), committed only on an 'ok' result.

	tx = None  # set by atomic()

	def _query(self, sql, args):
		return system.db.runPrepQuery(sql, args, DATABASE, self.tx)

	def _update(self, sql, args):
		return system.db.runPrepUpdate(sql, args, DATABASE, self.tx)

	def _ts_or_null(self, t):
		return None if t is None else system.date.fromMillis(int(t))

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

	def product_exists(self, product):
		return len(self._query("SELECT 1 FROM mes.product_master WHERE product_code = ?", [product])) > 0

	def insert_order(self, product, planned, due, priority, notes):
		ds = self._query(
			"INSERT INTO mes.work_order (product_code, planned_meters, due_date, priority, status, notes) "
			"VALUES (?, ?, ?, ?, 'Planned', ?) RETURNING work_order_id",
			[product, planned, self._ts_or_null(due), priority, notes])
		return ds[0][0]

	def order(self, wo):
		ds = self._query("SELECT work_order_id, product_code, status FROM mes.work_order WHERE work_order_id = ?", [wo])
		if not len(ds):
			return None
		return {'work_order_id': ds[0][0], 'product_code': ds[0][1], 'status': ds[0][2]}

	def transition(self, wo, expected, action, reason):
		sql, args = {
			'release': ("status = 'Released', released_at = coalesce(released_at, now()), hold_reason = NULL", []),
			'hold':    ("status = 'On Hold', hold_reason = ?", [reason]),
			'cancel':  ("status = 'Cancelled'", []),
			'close':   ("status = 'Closed', closed_at = now()", []),
		}[action]
		return self._update("UPDATE mes.work_order SET " + sql + " WHERE work_order_id = ? AND status = ?",
							args + [wo, expected])

	def run_row(self, run_id):
		ds = self._query("SELECT run_id, work_order_id, product_code, start_ts, end_ts FROM mes.work_order_run "
						 "WHERE run_id = ?", [run_id])
		if not len(ds):
			return None
		r = ds[0]
		return {'run_id': r[0], 'work_order_id': r[1], 'product_code': r[2], 'start_ts': _ms(r[3]), 'end_ts': _ms(r[4])}

	def move_run(self, run_id, from_wo, to_wo):
		return self._update("UPDATE mes.work_order_run SET work_order_id = ?, attributed_by = 'manual' "
							"WHERE run_id = ? AND work_order_id = ?", [to_wo, run_id, from_wo])

	def insert_run(self, wo, product, start, end, good, reject, reset_seen):
		self._update(
			"INSERT INTO mes.work_order_run (work_order_id, product_code, start_ts, end_ts, good_meters, "
			"reject_meters, attributed_by, counter_reset_seen) VALUES (?, ?, ?, ?, ?, ?, 'manual', ?)",
			[wo, product, self._ts_or_null(start), self._ts_or_null(end), _int(good), _int(reject), bool(reset_seen)])

	def run_at(self, product, start):
		"""run_id of the run of product starting at start (epoch ms), or None."""
		ds = self._query("SELECT run_id FROM mes.work_order_run WHERE product_code = ? AND start_ts = ?",
						 [product, self._ts_or_null(start)])
		return ds[0][0] if len(ds) else None

	def run_times(self, wo):
		ds = self._query("SELECT start_ts, end_ts FROM mes.work_order_run WHERE work_order_id = ?", [wo])
		return [(_ms(r[0]), _ms(r[1])) for r in ds]

	def set_from_runs(self, wo, expected, status, started, completed):
		return self._update(
			"UPDATE mes.work_order SET status = ?, started_at = ?, completed_at = ? "
			"WHERE work_order_id = ? AND status = ?",
			[status, self._ts_or_null(started), self._ts_or_null(completed), wo, expected])


def _int(v):
	return None if v is None else int(round(v))


def _ms(v):
	return None if v is None else v.getTime()



# ---------- engine handler ----------

def on_tick(sample, events):
	st = _store()
	try:
		lines = apply_pending(st, _SqlDb())
		lines += handle(st, sample, events, _SqlDb())
	except:
		import traceback
		log.error('handle failed: %s' % traceback.format_exc())
		return
	for line in lines:
		log.info(line)


# ---------- read API (copies, never the live store) ----------

def current():
	"""The open run, or None."""
	return copy.deepcopy(_store()['run'])


def unattributed_runs():
	"""Closed unattributed runs, oldest first."""
	return copy.deepcopy(_store()['unattributed'])


# ---------- WO-003 manual actions: pure rules ----------
# MES section 3: people set every status except Running and Complete. Each do_*() takes a db (the
# methods above on _SqlDb, or the FakeDb of the offline test) and returns 'ok: ...' or 'error: ...';
# the caller runs it inside db.atomic(), so an error result rolls the transaction back.

TRANSITIONS = {  # action -> (statuses it is allowed from, new status)
	'release': (('Planned', 'On Hold'), 'Released'),
	'hold':    (('Planned', 'Released'), 'On Hold'),
	'cancel':  (('Planned', 'Released', 'On Hold'), 'Cancelled'),
	'close':   (('Complete',), 'Closed'),
}
PRIORITIES = ('Normal', 'Rush')
STALE = 'error: status changed since you looked; refresh'
SEP = u' · '  # middle dot; kept as an escape so the file stays ASCII for Jython 2.7


def check_transition(action, current_status):
	"""None if action is allowed from current_status, else why not (without the order id)."""
	if action not in TRANSITIONS:
		return 'unknown action %s' % action
	if current_status not in TRANSITIONS[action][0]:
		return '%s; %s is not allowed' % (current_status, action)
	return None


def status_from_runs(runs):
	"""Runs as (start, end) pairs, end None while open -> (status, started_at, completed_at)."""
	if not runs:
		return 'Released', None, None
	started = min(r[0] for r in runs)
	if any(r[1] is None for r in runs):
		return 'Running', started, None
	return 'Complete', started, max(r[1] for r in runs)


def do_create(db, product, planned_m, due_ms, priority, notes):
	try:
		product, planned_m = int(product), int(round(float(planned_m)))
	except (TypeError, ValueError):
		return 'error: product and planned m must be numbers'
	if not db.product_exists(product):
		return 'error: no product %s' % product
	if planned_m <= 0:
		return 'error: planned m must be > 0'
	if due_ms is None:
		return 'error: due date is required'
	if priority not in PRIORITIES:
		return 'error: priority must be Normal or Rush'
	wo = db.insert_order(product, planned_m, due_ms, priority, notes or None)
	return 'ok: created %s (product %s, %d m, %s), Planned' % (wo, product, planned_m, priority)


def do_transition(db, action, wo, reason=None):
	"""release / hold / cancel / close per TRANSITIONS."""
	o = db.order(wo)
	if o is None:
		return 'error: no work order %s' % wo
	err = check_transition(action, o['status'])
	if err:
		return 'error: %s is %s' % (wo, err)
	if action == 'hold' and not reason:
		return 'error: hold needs a reason'
	if not db.transition(wo, o['status'], action, reason):
		return STALE
	return 'ok: %s %s -> %s' % (wo, o['status'], TRANSITIONS[action][1])


def _check_target(db, target, product):
	o = db.order(target)
	if o is None:
		return 'error: no work order %s' % target
	if o['product_code'] != product:
		return 'error: %s is product %s, the run is %s' % (target, o['product_code'], product)
	if o['status'] != 'Released':
		return 'error: %s is %s; the target must be Released' % (target, o['status'])
	return None


def _recompute(db, wo, expected):
	"""Order status and times from its runs (rule R). Returns the new status, or None if the guard missed."""
	status, started, completed = status_from_runs(db.run_times(wo))
	return status if db.set_from_runs(wo, expected, status, started, completed) else None


def do_move_run(db, run_id, target):
	"""Rule R, attributed run: move it to target; recompute both orders."""
	run = db.run_row(run_id)
	if run is None:
		return 'error: no run %s' % run_id
	src = db.order(run['work_order_id'])
	if src['status'] not in ('Running', 'Complete'):
		return 'error: run %s belongs to %s, which is %s' % (run_id, src['work_order_id'], src['status'])
	if target == src['work_order_id']:
		return 'error: run %s is already on %s' % (run_id, target)
	err = _check_target(db, target, run['product_code'])
	if err:
		return err
	if not db.move_run(run_id, src['work_order_id'], target):
		return STALE
	s1 = _recompute(db, src['work_order_id'], src['status'])
	s2 = _recompute(db, target, 'Released')
	if s1 is None or s2 is None:
		return STALE
	return 'ok: run %s %s -> %s; %s %s, %s %s' % (run_id, src['work_order_id'], target, src['work_order_id'], s1, target, s2)


def do_insert_run(db, target, product, start, end, good, reject, reset_seen):
	"""Rule R, unattributed run: insert a run row for target; recompute target."""
	err = _check_target(db, target, product)
	if err:
		return err
	if db.run_at(product, start) is not None:
		return 'error: the run of %s at that start is already assigned; refresh' % product
	db.insert_run(target, product, start, end, good, reject, reset_seen)
	s = _recompute(db, target, 'Released')
	if s is None:
		return STALE
	return 'ok: unattributed run of %s -> %s (good %d, reject %d); %s %s' % (
		product, target, _int(good), _int(reject), target, s)


def _atomic(db, fn):
	"""db.atomic(fn), with any exception turned into an error result."""
	try:
		return db.atomic(fn)
	except:
		import sys
		return 'error: %s' % (sys.exc_info()[1],)


def _is_open(run, ref):
	"""Does ref ({'run_id': n} or {'unattributed_start': ms}) name the open run of the store?"""
	if run is None:
		return False
	if ref.get('run_id') is not None:
		return run['attributed'] and run['run_id'] == ref['run_id']
	return not run['attributed'] and run['start'] == ref.get('unattributed_start')


def apply_pending(st, db):
	"""Timer thread, before handle(): apply the queued reassigns of the open run. Returns log lines."""
	out = []
	while st['pending']:
		p = st['pending'].pop(0)
		run, target = st['run'], p['target']
		if not _is_open(run, p):
			res = 'error: the run is no longer open; nothing changed, reassign it as a closed run'
		elif run['attributed']:
			res = _atomic(db, lambda d: do_move_run(d, run['run_id'], target))
			if res.startswith('ok'):
				run['work_order_id'] = target
		else:
			res = _atomic(db, lambda d: do_insert_run(d, target, run['product'], run['start'], None,
													  run['good'], run['reject'], run['reset_seen']))
			if res.startswith('ok'):
				run['run_id'] = db.run_at(run['product'], run['start'])
				run['work_order_id'] = target
				run['attributed'] = True
				run['saved'] = (run['good'], run['reject'], run['reset_seen'])
		out.append('action reassign %s by %s: %s' % (target, p['by'], res))
	return out


# ---------- WO-003 manual actions: session-callable (never raise) ----------

def _act(db, name, wo, by, fn):
	res = _atomic(db, fn)
	log.info('action %s %s by %s: %s' % (name, wo, by or '-', res))
	return res


def create(product, planned_m, due_ms, priority, notes):
	if due_ms is not None and hasattr(due_ms, 'getTime'):
		due_ms = due_ms.getTime()  # a Date from a Perspective DateTime Input
	return _act(_SqlDb(), 'create', 'new', None, lambda d: do_create(d, product, planned_m, due_ms, priority, notes))


def release(wo, by):
	return _act(_SqlDb(), 'release', wo, by, lambda d: do_transition(d, 'release', wo))


def hold(wo, reason, by):
	return _act(_SqlDb(), 'hold', wo, by, lambda d: do_transition(d, 'hold', wo, reason))


def cancel(wo, by):
	return _act(_SqlDb(), 'cancel', wo, by, lambda d: do_transition(d, 'cancel', wo))


def close(wo, by):
	return _act(_SqlDb(), 'close', wo, by, lambda d: do_transition(d, 'close', wo))


def reassign(run_ref, target_wo, by):
	"""Rule R. run_ref is {'run_id': n} or {'unattributed_start': ms}. The open run is queued for the timer."""
	try:
		res = _reassign(_store(), _SqlDb(), dict(run_ref or {}), target_wo, by)
	except:
		import sys
		res = 'error: %s' % (sys.exc_info()[1],)
	log.info('action reassign %s by %s: %s' % (target_wo, by or '-', res))
	return res


def _reassign(st, db, ref, target, by):
	"""reassign() without the logging, on a given store and db (the offline test calls it)."""
	if not by:
		return 'error: entered by is required'
	if not target:
		return 'error: pick a target work order'
	if ref.get('run_id') is not None:
		ref['run_id'] = int(ref['run_id'])
	elif ref.get('unattributed_start') is not None:
		ref['unattributed_start'] = int(ref['unattributed_start'])
	else:
		return 'error: pick a run'
	run = st['run']
	if _is_open(run, ref):
		# Check now so the operator sees a bad target at once; apply_pending checks again.
		if run['attributed'] and run['work_order_id'] == target:
			return 'error: run %s is already on %s' % (run['run_id'], target)
		err = _check_target(db, target, run['product'])
		if err:
			return err
		st['pending'].append({'type': 'reassign', 'run_id': ref.get('run_id'),
							  'unattributed_start': ref.get('unattributed_start'), 'target': target, 'by': by})
		return 'ok: queued open run -> %s; applies on the next tick (<= 2 s)' % target
	if ref.get('run_id') is not None:
		return _atomic(db, lambda d: do_move_run(d, ref['run_id'], target))
	closed = [r for r in st['unattributed'] if r['start'] == ref['unattributed_start']]
	if not closed:
		return 'error: no unattributed run starting at that time; refresh'
	r = closed[-1]
	return _atomic(db, lambda d: do_insert_run(d, target, r['product'], r['start'], r['end'],
											   r['good'], r['reject'], r['reset_seen']))


# ---------- WO-003 read helpers for the view ----------

def run_label(r, fmt):
	"""One reassignable run (see reassignable_runs) -> dropdown label."""
	t = fmt(r['start'], 'HH:mm:ss') if r['start'] is not None else '?'
	if r['run_id'] is not None:
		parts = ['run %s' % r['run_id'], r['work_order_id'], '%s' % r['product'], t]
	else:
		parts = ['unattributed', '%s' % r['product'], t]
	if r['open']:
		parts.append('open')
	return SEP.join(parts)


def _fmt(ms, pattern):
	return system.date.format(system.date.fromMillis(int(ms)), pattern)


def reassignable_runs():
	"""Attributed runs whose order is Running/Complete, newest first, then open and closed unattributed runs."""
	ds = system.db.runPrepQuery(
		"SELECT r.run_id, r.work_order_id, r.product_code, r.start_ts, r.end_ts FROM mes.work_order_run r "
		"JOIN mes.work_order w ON w.work_order_id = r.work_order_id WHERE w.status IN ('Running', 'Complete') "
		"ORDER BY r.start_ts DESC", [], DATABASE)
	out = [{'run_id': r[0], 'work_order_id': r[1], 'product': r[2], 'start': _ms(r[3]), 'open': r[4] is None}
		   for r in ds]
	st = _store()
	manual = system.db.runPrepQuery(
		"SELECT product_code, start_ts FROM mes.work_order_run WHERE attributed_by = 'manual'", [], DATABASE)
	assigned = set((r[0], _ms(r[1])) for r in manual)
	runs = list(reversed(st['unattributed']))
	if st['run'] is not None and not st['run']['attributed']:
		runs.insert(0, st['run'])
	for r in runs:
		if (r['product'], r['start']) not in assigned:
			out.append({'run_id': None, 'work_order_id': None, 'product': r['product'], 'start': r['start'],
						'open': r is st['run']})
	for r in out:
		r['label'] = run_label(r, _fmt)
	return out


def eligible_targets(product):
	"""Released orders of product, in attribution order."""
	ds = system.db.runPrepQuery(
		"SELECT work_order_id FROM mes.work_order WHERE product_code = ? AND status = 'Released' "
		"ORDER BY released_at, work_order_id", [product], DATABASE)
	return [r[0] for r in ds]
