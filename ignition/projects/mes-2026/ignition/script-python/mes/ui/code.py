# mes.ui -- view-data shaping for the MES test visuals (WO-002 onward). Read only.
#
# WO-003: the dropdown options for the action panel are shaped here too. The actions themselves
# are called by the button scripts directly on mes.workorder.
#
# Each view binding polls one function here every 2 s (now(2000) + script transform, the VIS-001
# pattern). The I/O wrappers read PostgreSQL (connection 'mes', SELECT only) or the read API of
# mes.workorder, turn dates into epoch ms and hand them to the pure shaping functions.
#
# shape_work_orders() is pure: it takes rows of plain values, epoch ms or None for times, and a
# formatter. The offline check (ignition/tests/test_ui_work_orders.py) runs it under Python 3
# with a fake formatter. In the gateway the formatter is system.date.format, so times are in the
# gateway time zone (America/Chicago).

DATABASE = 'mes'
LOG_KEY = 'mes.ui.lastError'

log = system.util.getLogger('mes.ui')

# Table column order and headers. The view's props.columns use the same order.
WORK_ORDER_COLUMNS = [
	('wo',        'Work order'),
	('product',   'Product'),
	('status',    'Status'),
	('priority',  'Priority'),
	('planned_m', 'Planned m'),
	('good_m',    'Good m'),
	('reject_m',  'Reject m'),
	('lanes',     'Lanes'),
	('lane_m',    'Lane-m'),
	('diff_m',    'Over/under m'),
	('run_start', 'Run start'),
	('run_end',   'Run end'),
	('due',       'Due'),
]
NOT_LATE = ('Complete', 'Closed', 'Cancelled')
VIEW_COLUMNS = ['work_order_id', 'product_code', 'product_family', 'status', 'priority',
				'planned_meters', 'good_m', 'reject_m', 'lanes', 'run_start', 'run_end', 'due_date']
TIME_COLUMNS = ('run_start', 'run_end', 'due_date')
SEP = u' \u00b7 '  # middle dot; kept as an escape so the file stays ASCII for Jython 2.7


# ---------- pure shaping ----------

def _int(v):
	return 0 if v is None else int(round(v))


def _time(fmt, ms, pattern):
	return '' if ms is None else fmt(ms, pattern)


def shape_work_orders(rows, now_ms, fmt):
	"""Rows of mes.v_work_order_table (dicts, times as epoch ms or None) -> table rows.

	fmt(ms, pattern) formats an epoch-ms time. Returns one dict per order, keys in
	WORK_ORDER_COLUMNS order.
	"""
	out = []
	for r in rows:
		status = r['status']
		good, planned, lanes = _int(r['good_m']), _int(r['planned_meters']), _int(r['lanes'])
		due = _time(fmt, r['due_date'], 'HH:mm')
		if r['due_date'] is not None and r['due_date'] < now_ms and status not in NOT_LATE:
			due += ' late'
		out.append({
			'wo': r['work_order_id'],
			'product': '%s %s' % (r['product_code'], r['product_family']),
			'status': status,
			'priority': r['priority'],
			'planned_m': planned,
			'good_m': good,
			'reject_m': _int(r['reject_m']),
			'lanes': lanes,
			'lane_m': good * lanes,                                     # MES section 3 FR4
			'diff_m': good - planned if status == 'Complete' else '',
			'run_start': _time(fmt, r['run_start'], 'HH:mm:ss'),
			'run_end': _time(fmt, r['run_end'], 'HH:mm:ss'),
			'due': due,
		})
	return out


def shape_open_run(run, fmt):
	"""mes.workorder.current() -> one line of text."""
	if run is None:
		return 'No open run'
	wo = run['work_order_id'] if run['work_order_id'] is not None else 'UNATTRIBUTED'
	parts = ['Running %s' % wo, '%s' % run['product'], 'good %d m' % _int(run['good']), 'reject %d m' % _int(run['reject'])]
	if run.get('start') is not None:  # None for a run resumed after a gateway restart
		parts.append('since %s' % fmt(run['start'], 'HH:mm:ss'))
	return SEP.join(parts)


def run_option(r):
	"""One mes.workorder.reassignable_runs() entry -> dropdown option. The value carries the product."""
	if r['run_id'] is not None:
		value = 'run:%s:%s' % (r['run_id'], r['product'])
	else:
		value = 'ua:%s:%s' % (r['start'], r['product'])
	return {'value': value, 'label': r['label']}


def parse_run_option(value):
	"""run_option() value -> (run_ref for mes.workorder.reassign, product), or (None, None)."""
	try:
		kind, key, product = ('%s' % value).split(':')
		ref = {'run_id': int(key)} if kind == 'run' else {'unattributed_start': int(key)}
		return ref, int(product)
	except ValueError:
		return None, None


def shape_unattributed(runs, fmt):
	"""mes.workorder.unattributed_runs() -> table rows."""
	return [{
		'product': r['product'],
		'start': _time(fmt, r.get('start'), 'HH:mm:ss'),
		'end': _time(fmt, r.get('end'), 'HH:mm:ss'),
		'good_m': _int(r['good']),
		'reject_m': _int(r['reject']),
	} for r in runs]


# ---------- I/O wrappers ----------

def _fmt(ms, pattern):
	return system.date.format(system.date.fromMillis(int(ms)), pattern)


def _ms(v):
	return None if v is None else v.getTime()


def work_order_rows():
	ds = system.db.runQuery("SELECT * FROM mes.v_work_order_table ORDER BY work_order_id", DATABASE)
	rows = []
	for rec in ds:
		row = dict((c, rec[c]) for c in VIEW_COLUMNS)
		for c in TIME_COLUMNS:
			row[c] = _ms(row[c])
		rows.append(row)
	return shape_work_orders(rows, system.date.toMillis(system.date.now()), _fmt)


def open_run_line():
	return shape_open_run(mes.workorder.current(), _fmt)


def unattributed_rows():
	return shape_unattributed(mes.workorder.unattributed_runs(), _fmt)


def log_once(where, err):
	"""For the view transforms: log a failure once, not every 2 s while it repeats."""
	msg = '%s: %s' % (where, err)
	g = system.util.getGlobals()
	if g.get(LOG_KEY, {}).get(where) != msg:
		g.setdefault(LOG_KEY, {})[where] = msg
		log.warn(msg)


# ---------- WO-003 action panel options ----------

def product_options():
	ds = system.db.runQuery("SELECT product_code, product_family FROM mes.product_master ORDER BY product_code", DATABASE)
	return [{'value': r[0], 'label': '%s %s' % (r[0], r[1])} for r in ds]


def order_options():
	ds = system.db.runQuery("SELECT work_order_id, product_code, status FROM mes.work_order ORDER BY work_order_id", DATABASE)
	return [{'value': r[0], 'label': SEP.join([r[0], '%s' % r[1], r[2]])} for r in ds]


def run_options():
	return [run_option(r) for r in mes.workorder.reassignable_runs()]


def target_options(run_value):
	ref, product = parse_run_option(run_value)
	if product is None:
		return []
	return [{'value': wo, 'label': wo} for wo in mes.workorder.eligible_targets(product)]


# ---------- DT-004 downtime test page ----------
#
# Read only: SELECT on mes.downtime_event, tag reads of Downtime/*. A window of None or 0 minutes
# means no filter ("Since start" in the view's dropdown is 0). Durations are cast to float8 in SQL
# so the shaping gets plain floats, not BigDecimal.

DOWNTIME_COLUMNS = [
	('start',    'Start'),
	('end',      'End'),
	('duration', 'Duration s'),
	('class',    'Class'),
	('planned',  'Planned'),
	('source',   'Source'),
	('code',     'Code'),
	('product',  'Product'),
	('wo',       'Work order'),
]
CLASS_TOTAL_COLUMNS = [
	('class',   'Class'),
	('planned', 'Planned'),
	('events',  'Events'),
	('total_s', 'Total s'),
]
DOWNTIME_TAGS = ['State', 'CurrentAlarm', 'Class', 'ActiveSince', 'DurationSec']


def _s1(v):
	return 0.0 if v is None else round(float(v), 1)


def _blank(v):
	return '' if v is None else v


def shape_downtime(rows, fmt):
	"""Rows of mes.downtime_event (dicts, times as epoch ms or None) -> events table rows."""
	return [{
		'start': _time(fmt, r['start_ts'], 'HH:mm:ss'),
		'end': _time(fmt, r['end_ts'], 'HH:mm:ss'),
		'duration': '' if r['duration_s'] is None else _s1(r['duration_s']),
		'class': r['class'],
		'planned': 'yes' if r['is_planned'] else '',
		'source': r['source'],
		'code': r['fault_code'] if r['fault_code'] else '',
		'product': _blank(r['product_code']),
		'wo': _blank(r['work_order_id']),
	} for r in rows]


def shape_class_totals(rows):
	"""Rows of (class, is_planned, events, total_s) dicts -> totals table rows: planned first, then by
	total desc, then 'All unplanned' and 'All planned'."""
	rows = sorted(rows, key=lambda r: (not r['is_planned'], -_s1(r['total_s'])))
	out = []
	sums = {False: [0, 0.0], True: [0, 0.0]}
	for r in rows:
		planned = bool(r['is_planned'])
		sums[planned][0] += int(r['events'])
		sums[planned][1] += float(r['total_s'] or 0)
		out.append({'class': r['class'], 'planned': 'yes' if planned else '', 'events': int(r['events']),
					'total_s': _s1(r['total_s'])})
	for label, planned in (('All unplanned', False), ('All planned', True)):
		out.append({'class': label, 'planned': 'yes' if planned else '', 'events': sums[planned][0],
					'total_s': _s1(sums[planned][1])})
	return out


def shape_now_line(v, fmt):
	"""Downtime/* tag values (dict, ActiveSince as epoch ms or None) -> one line of text."""
	if v['State'] not in ('Unplanned', 'Planned'):
		return 'Running'
	cls = v['Class'] or ''
	if v['CurrentAlarm']:
		cls = '%s (%d)' % (cls, int(v['CurrentAlarm']))
	parts = [v['State'], cls]
	if v['ActiveSince'] is not None:
		parts.append('since %s' % fmt(v['ActiveSince'], 'HH:mm:ss'))
	parts.append('%.1f s' % float(v['DurationSec'] or 0))
	return SEP.join(parts)


def _window(window_min):
	"""(where clause, args) for the window: None or 0 -> no filter."""
	if not window_min:
		return '', []
	return " WHERE start_ts >= now() - (? * interval '1 minute')", [int(window_min)]


def downtime_rows(window_min):
	where, args = _window(window_min)
	ds = system.db.runPrepQuery(
		"SELECT event_id, start_ts, end_ts, duration_s::float8 AS duration_s, class, is_planned, source, "
		"fault_code, product_code, work_order_id FROM mes.downtime_event" + where + " ORDER BY start_ts DESC",
		args, DATABASE)
	rows = []
	for rec in ds:
		rows.append({
			'start_ts': _ms(rec['start_ts']), 'end_ts': _ms(rec['end_ts']), 'duration_s': rec['duration_s'],
			'class': rec['class'], 'is_planned': rec['is_planned'], 'source': rec['source'],
			'fault_code': rec['fault_code'], 'product_code': rec['product_code'],
			'work_order_id': rec['work_order_id'],
		})
	return shape_downtime(rows, _fmt)


def class_totals(window_min):
	where, args = _window(window_min)
	ds = system.db.runPrepQuery(
		"SELECT class, is_planned, count(*) AS events, coalesce(sum(duration_s), 0)::float8 AS total_s "
		"FROM mes.downtime_event" + where + " GROUP BY class, is_planned", args, DATABASE)
	return shape_class_totals([{'class': r['class'], 'is_planned': r['is_planned'], 'events': r['events'],
								'total_s': r['total_s']} for r in ds])


def downtime_now_line():
	paths = ['%s/Downtime/%s' % (mes.engine.ROOT, n) for n in DOWNTIME_TAGS]
	qvs = system.tag.readBlocking(paths)
	bad = [n for n, qv in zip(DOWNTIME_TAGS, qvs) if not qv.quality.isGood()]
	if bad:
		return 'Downtime tags not good: %s' % ', '.join(bad)
	v = dict((n, qv.value) for n, qv in zip(DOWNTIME_TAGS, qvs))
	v['ActiveSince'] = _ms(v['ActiveSince'])
	return shape_now_line(v, _fmt)
