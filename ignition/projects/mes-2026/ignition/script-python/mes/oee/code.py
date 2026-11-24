# mes.oee -- OEE interval writer for Press 1 (OEE-001). An mes.engine handler.
#
# Rules of docs/tasks/task_OEE-001_oee_intervals.md (MES section 6 time basis): one mes.oee_interval
# row per minute (UTC) per product code, holding the inputs, not the ratios (OEE-002 computes those).
#   time   -> sample-and-hold: [t_prev, t) belongs to the previous sample's state and product.
#             state 1 running + planned production; state 3 planned production; state 2 split at the
#             product's scheduled changeover since the Setup started: up to it planned (excluded from
#             everything), beyond it overrun (planned production). No time on a gap or with no previous
#             sample. Operator planned stops (DT-003) come off planned production via planned_stop_overlap().
#   meters -> the sample's engine deltas dg / dr go to the previous sample's product (BUG-002), in the
#             minute containing t.
#   ideal  -> running seconds / 60 x the product's ideal speed (product master).
# An interval that crosses a minute boundary is split by time. Buckets hold milliseconds (integers),
# so a split conserves the totals exactly; seconds are rounded to 0.1 only on write.
#
# Each tick upserts every changed bucket with absolute values, so a failed write heals on the next
# tick. A bucket new in memory first loads its row from the database (if any) and continues from it:
# a reload never lowers a minute. Buckets older than 3 minutes are dropped once written.
#
# accumulate() is pure and handle() is pure except for calls on its db argument, so the offline replay
# (ignition/tests/replay_oee.py) runs this file under Python 3 with a fake db. Times are epoch ms.
#
# Writes: INSERT ... ON CONFLICT DO UPDATE on mes.oee_interval (connection 'mes'). Never deletes rows.

import time

DATABASE = 'mes'
MINUTE_MS = 60000
KEEP_MINUTES = 3        # buckets kept in memory: the current minute and the two before it
STORE_VERSION = 1
STORE_KEY = 'mes.oee.v%d' % STORE_VERSION

log = system.util.getLogger('mes.oee')


# ---------- store ----------

def _new_store():
	return {
		'version': STORE_VERSION,
		'prev': None,           # previous accepted sample: t, state, product
		'setup_start': None,    # t of the first state-2 sample of the current Setup (raw samples)
		'buckets': {},          # (interval_start ms, product_code) -> bucket, see _bucket()
		'products': None,       # product_code -> (ideal_speed_mpm, scheduled_changeover_s), loaded once
		'minute': None,         # minute of the last sample, for the minute-closed log line
		'unknown': {},          # product codes not in the product master, logged once
		'warned': False,        # a failed write has been logged
	}


def _store():
	g = system.util.getGlobals()
	st = g.get(STORE_KEY)
	if st is None:
		st = g[STORE_KEY] = _new_store()
		log.info('oee store created %s' % STORE_KEY)
	return st


# ---------- rules (pure) ----------

def planned_stop_overlap(start, end):
	"""Seconds of operator-logged planned stops inside [start, end) (epoch ms). DT-003 is not built: 0."""
	return 0


def _minute(t):
	return t - t % MINUTE_MS


def _bucket(st, key):
	b = st['buckets'].get(key)
	if b is None:
		b = st['buckets'][key] = {'run_ms': 0, 'pp_ms': 0, 'good': 0.0, 'reject': 0.0, 'ideal': 0.0,
								  'work_order_id': None, 'loaded': False, 'dirty': False}
	return b


def _add_time(st, changed, a, b, product, running, speed, wo_now):
	"""Spread [a, b) over its minutes: planned production, and running + ideal if running."""
	while a < b:
		m = _minute(a)
		e = min(b, m + MINUTE_MS)
		k = (m, product)
		bk = _bucket(st, k)
		ms = e - a
		bk['pp_ms'] += max(0, ms - int(round(planned_stop_overlap(a, e) * 1000)))
		if running:
			bk['run_ms'] += ms
			bk['ideal'] += ms / 60000.0 * speed
			bk['work_order_id'] = wo_now
		bk['dirty'] = True
		changed[k] = bk
		a = e


def accumulate(st, prev, sample, events, changeover, ideal_speed, wo_now):
	"""Rules 1-4 for one accepted sample; prev is the previous one (or None). changeover(product) and
	ideal_speed(product) read the product master (None if unknown). Returns {key: bucket} changed."""
	changed = {}
	t = sample['t']
	if prev is not None and prev.get('product') is not None and ideal_speed(prev['product']) is not None:
		p = prev['product']
		speed = ideal_speed(p)
		gap = any(ev['type'] == 'gap' for ev in events)
		if not gap and t > prev['t']:
			a = prev['t']
			if prev['state'] == 1:
				_add_time(st, changed, a, t, p, True, speed, wo_now)
			elif prev['state'] == 3:
				_add_time(st, changed, a, t, p, False, speed, wo_now)
			elif prev['state'] == 2:
				start = st['setup_start'] if st['setup_start'] is not None else a
				over = max(a, start + int(changeover(p) or 0) * 1000)  # planned up to here, overrun after
				_add_time(st, changed, over, t, p, False, speed, wo_now)
		dg, dr = sample.get('dg') or 0.0, sample.get('dr') or 0.0
		if dg > 0 or dr > 0:
			k = (_minute(t), p)
			bk = _bucket(st, k)
			bk['good'] += dg
			bk['reject'] += dr
			bk['dirty'] = True
			changed[k] = bk
	if sample['state'] == 2 and (prev is None or prev['state'] != 2):
		st['setup_start'] = t
	return changed


def row_values(key, b):
	"""Rule 7: the oee_interval row for bucket b, rounded as the columns hold it."""
	return {'interval_start': key[0], 'product_code': key[1], 'work_order_id': b['work_order_id'],
			'running_s': round(b['run_ms'] / 1000.0, 1), 'planned_production_s': round(b['pp_ms'] / 1000.0, 1),
			'good_m': int(round(b['good'])), 'reject_m': int(round(b['reject'])), 'ideal_m': round(b['ideal'], 1)}


def _load(db, key, b):
	"""Rule 6: add the row already in the database for key (if any) to a bucket new in memory."""
	r = db.load(key[0], key[1])
	if r is not None:
		b['run_ms'] += int(round(_num(r['running_s']) * 1000))
		b['pp_ms'] += int(round(_num(r['planned_production_s']) * 1000))
		b['good'] += _num(r['good_m'])
		b['reject'] += _num(r['reject_m'])
		b['ideal'] += _num(r['ideal_m'])
		if b['work_order_id'] is None:
			b['work_order_id'] = r['work_order_id']
	b['loaded'] = True
	return r is not None


def _products(st, db):
	if st['products'] is None:
		st['products'] = dict((int(r[0]), (_num(r[1]), int(_num(r[2])))) for r in db.product_master())
	return st['products']


def handle(st, sample, events, db, wo_now):
	"""One accepted engine sample and its events; wo_now is the work order of the open run (or None).
	Accumulates, loads new buckets, upserts every dirty one, drops old ones. Returns log lines."""
	out = []
	pm = _products(st, db)
	prev = st['prev']
	if prev is not None and prev.get('product') is not None and prev['product'] not in pm \
			and prev['product'] not in st['unknown']:
		st['unknown'][prev['product']] = True
		out.append('product %s not in product_master: its time and meters are not counted' % prev['product'])
	accumulate(st, prev, sample, events,
			   lambda p: pm[p][1] if p in pm else None, lambda p: pm[p][0] if p in pm else None, wo_now)
	st['prev'] = {'t': sample['t'], 'state': sample['state'], 'product': sample.get('product')}
	m = _minute(sample['t'])
	if st['minute'] is not None and m > st['minute']:
		for k in sorted(st['buckets']):
			if k[0] == st['minute']:
				r = row_values(k, st['buckets'][k])
				out.append('minute %s product=%s wo=%s running=%.1f planned_production=%.1f good=%d reject=%d '
						   'ideal=%.1f' % (_iso(k[0]), k[1], r['work_order_id'], r['running_s'],
										   r['planned_production_s'], r['good_m'], r['reject_m'], r['ideal_m']))
	st['minute'] = m
	failed = []
	for k in sorted(st['buckets']):
		b = st['buckets'][k]
		if not b['dirty']:
			continue
		try:
			if not b['loaded'] and _load(db, k, b):
				out.append('continued %s product=%s from the database' % (_iso(k[0]), k[1]))
			db.upsert(row_values(k, b))
			b['dirty'] = False
		except:
			import sys
			failed.append('%s product=%s: %s' % (_iso(k[0]), k[1], sys.exc_info()[1]))
	if failed and not st['warned']:
		out.append('write failed (retried next tick) %s' % '; '.join(failed))
	st['warned'] = bool(failed)
	oldest = m - (KEEP_MINUTES - 1) * MINUTE_MS
	for k in list(st['buckets']):
		if k[0] < oldest and not st['buckets'][k]['dirty']:
			del st['buckets'][k]
	return out


def _num(v):
	"""numeric columns arrive as java.math.BigDecimal on the gateway."""
	return float(v.doubleValue()) if hasattr(v, 'doubleValue') else float(v)


def _iso(ms):
	return time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(ms // 1000))


# ---------- database ----------

class _SqlDb(object):
	"""The db interface of handle() on PostgreSQL, connection 'mes'. Parameterised SQL only."""

	def _ts(self, t):
		return system.date.fromMillis(int(t))

	def product_master(self):
		ds = system.db.runPrepQuery(
			"SELECT product_code, ideal_speed_mpm, scheduled_changeover_s FROM mes.product_master", [], DATABASE)
		return [(row[0], row[1], row[2]) for row in ds]

	def load(self, interval_start, product):
		ds = system.db.runPrepQuery(
			"SELECT work_order_id, running_s, planned_production_s, good_m, reject_m, ideal_m "
			"FROM mes.oee_interval WHERE interval_start = ? AND product_code = ?",
			[self._ts(interval_start), product], DATABASE)
		if not len(ds):
			return None
		cols = ['work_order_id', 'running_s', 'planned_production_s', 'good_m', 'reject_m', 'ideal_m']
		return dict(zip(cols, [ds[0][c] for c in cols]))

	def upsert(self, r):
		system.db.runPrepUpdate(
			"INSERT INTO mes.oee_interval (interval_start, product_code, work_order_id, running_s, "
			"planned_production_s, good_m, reject_m, ideal_m) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
			"ON CONFLICT (interval_start, product_code) DO UPDATE SET work_order_id = EXCLUDED.work_order_id, "
			"running_s = EXCLUDED.running_s, planned_production_s = EXCLUDED.planned_production_s, "
			"good_m = EXCLUDED.good_m, reject_m = EXCLUDED.reject_m, ideal_m = EXCLUDED.ideal_m",
			[self._ts(r['interval_start']), r['product_code'], r['work_order_id'], r['running_s'],
			 r['planned_production_s'], r['good_m'], r['reject_m'], r['ideal_m']], DATABASE)


# ---------- engine handler ----------

def on_tick(sample, events):
	st = _store()
	try:
		run = mes.workorder.current()
		lines = handle(st, sample, events, _SqlDb(), run['work_order_id'] if run else None)
	except:
		import traceback
		log.error('handle failed: %s' % traceback.format_exc())
		return
	for line in lines:
		log.info(line)


# ---------- read API (copies, never the live store) ----------

def buckets():
	"""The rows held in memory, oldest first."""
	st = _store()
	return [row_values(k, st['buckets'][k]) for k in sorted(st['buckets'])]
