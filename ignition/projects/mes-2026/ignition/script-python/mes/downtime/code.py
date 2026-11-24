# mes.downtime -- downtime events from the PLC for Press 1 (DT-001, DT-002). An mes.engine handler.
#
# Rules of docs/tasks/task_DT-001_downtime_events.md and task_DT-002_setup_split.md (MES section 5):
#   segment_open, state 3  -> remember the open fault in the store: start, fault, class, product and the
#                             work order of mes.workorder.current() at this moment. 103 without
#                             PLC/RTOInhibit is logged (cross-check only).
#   segment_close, state 3 -> INSERT one complete mes.downtime_event row; forget the open fault.
#                             A fault-code change inside Faulted is a close + open, so two rows.
#   segment_close, state 2 -> split the Setup at the product's scheduled changeover (product_master,
#                             cached here): a planned 'Changeover' row and, past it, an unplanned
#                             'Changeover overrun' row (source 'derived'), both in one transaction, on
#                             the work order of mes.workorder.current() at the close (the run it set up).
#   every sample           -> the Downtime/* memory tags follow the open segment (Running / Planned /
#                             Unplanned; a Setup turns Unplanned once it passes its scheduled changeover);
#                             unchanged tags are not rewritten, DurationSec is.
# Rows are written only when an event closes (Walker, 2026-10-22): the open event lives here, so a
# gateway restart loses only that one; the engine re-opens it from a new backdated start.
# State 1 segments write no rows. A product with no product_master row has changeover 0 s: the whole
# Setup is overrun (warned once per product).
#
# handle() is pure except for calls on its db argument, so the offline replay
# (ignition/tests/replay_downtime.py) runs this file under Python 3 with a fake db.
# Times are epoch ms everywhere except inside _SqlDb and _write_tags.
#
# Reads: mes.product_master.scheduled_changeover_s.
# Writes: INSERT on mes.downtime_event (connection 'mes'), the five Downtime/* memory tags.
# Never updates or deletes rows.

import time

DATABASE = 'mes'
STORE_VERSION = 1
STORE_KEY = 'mes.downtime.v%d' % STORE_VERSION

# Rule 1: fault code -> class. Any other code is 'Unclassified alarm', never dropped.
CLASSES = {101: 'Web break', 102: 'Tunnel over-temp', 103: 'RTO inhibit'}
UNCLASSIFIED = 'Unclassified alarm'
RTO_FAULT = 103
SETUP_STATE = 2
CHANGEOVER = 'Changeover'
OVERRUN = 'Changeover overrun'
WARN = 'warning: '  # log lines with this prefix are logged as warnings

# Downtime/<name>, in write order
TAG_NAMES = ['State', 'CurrentAlarm', 'Class', 'ActiveSince', 'DurationSec']
ALWAYS_WRITTEN = ('DurationSec',)
_UNWRITTEN = object()

log = system.util.getLogger('mes.downtime')


# ---------- store ----------

def _new_store():
	return {
		'version': STORE_VERSION,
		'segment': None,    # open engine segment: state, fault, start, changeover (any state; drives the tags)
		'fault': None,      # open fault event (state 3), see _open_fault()
		'written': {},      # tag name -> last value written; emptied when a write fails
		'warned': False,    # a failed tag write has been logged
		'changeover': {},   # product -> scheduled changeover s (product_master); refreshed on a miss
		'unknown': [],      # products with no product_master row, warned once each
	}


def _store():
	g = system.util.getGlobals()
	st = g.get(STORE_KEY)
	if st is None:
		st = g[STORE_KEY] = _new_store()
		log.info('downtime store created %s' % STORE_KEY)
	for k, v in _new_store().items():
		st.setdefault(k, v)  # keys added after the store was created (DT-002) without dropping an open fault
	return st


# ---------- rules (pure except for db calls) ----------

def classify(fault):
	return CLASSES.get(fault, UNCLASSIFIED)


def _iso_s(ms):
	return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ms // 1000))


def _open_fault(st, sample, ev, wo_now, out):
	"""Rule 2: remember the fault that opened on this sample; rule 4: RTO cross-check."""
	f = {'start': ev['start'], 'fault': ev.get('fault'), 'class': classify(ev.get('fault')),
		 'product': ev.get('product'), 'work_order_id': wo_now}
	st['fault'] = f
	out.append('fault open %s fault=%s start=%s product=%s wo=%s' % (
		f['class'], f['fault'], _iso_s(f['start']), f['product'], f['work_order_id']))
	if f['fault'] == RTO_FAULT and sample.get('rto') is not True:
		out.append('RTO inhibit without PLC/RTOInhibit (rto=%s) start=%s' % (sample.get('rto'), _iso_s(f['start'])))


def _close_fault(st, ev, db, out):
	"""Rule 3: one complete row for the fault that closed on this sample."""
	f, st['fault'] = st['fault'], None
	if f is None or f['start'] != ev['start'] or f['fault'] != ev.get('fault'):
		# Not opened in this store (created mid-fault): the work order at the start is unknown.
		f = {'start': ev['start'], 'fault': ev.get('fault'), 'class': classify(ev.get('fault')),
			 'product': ev.get('product'), 'work_order_id': None}
		out.append('fault closed with no open event in the store: work order unknown')
	row = {'start': ev['start'], 'end': ev['end'], 'dur_s': ev['dur_s'], 'source': 'plc', 'machine_state': 3,
		   'fault_code': f['fault'] if f['fault'] is not None else 0, 'class': f['class'], 'is_planned': False,
		   'product_code': f['product'], 'work_order_id': f['work_order_id']}
	try:
		event_id = db.insert_event(row)
	except:
		import sys
		out.append('insert failed %s fault=%s start=%s end=%s: %s' % (
			row['class'], row['fault_code'], _iso_s(row['start']), _iso_s(row['end']), sys.exc_info()[1]))
		return
	out.append('event %s %s fault=%s start=%s end=%s dur=%.1fs product=%s wo=%s' % (
		event_id, row['class'], row['fault_code'], _iso_s(row['start']), _iso_s(row['end']), row['dur_s'],
		row['product_code'], row['work_order_id']))


def split_setup(start, end, dur_s, changeover_s):
	"""Rule 2: a closed Setup segment (epoch ms, seconds) -> the planned Changeover row and, when the Setup ran
	past its scheduled changeover, the Changeover overrun row. The caller adds product and work order."""
	c = float(changeover_s)
	rows = [{'start': start, 'end': end if dur_s <= c else start + int(round(c * 1000)), 'dur_s': min(dur_s, c),
			 'source': 'plc', 'machine_state': SETUP_STATE, 'fault_code': 0, 'class': CHANGEOVER, 'is_planned': True}]
	if dur_s > c:
		rows.append({'start': start + int(round(c * 1000)), 'end': end, 'dur_s': dur_s - c, 'source': 'derived',
					 'machine_state': SETUP_STATE, 'fault_code': 0, 'class': OVERRUN, 'is_planned': False})
	return rows


def _changeover(st, product, db, out):
	"""Rules 1 and 4: scheduled changeover seconds of product, cached; no product_master row -> 0."""
	if product in st['changeover']:
		return st['changeover'][product]
	c = db.changeover_s(product)
	if c is None:
		if product not in st['unknown']:
			st['unknown'].append(product)
			out.append(WARN + 'no product_master row for product %s: scheduled changeover 0 s, '
					   'the whole Setup is overrun' % product)
		return 0.0
	st['changeover'][product] = float(c)
	return st['changeover'][product]


def _close_setup(st, ev, db, wo_now, out):
	"""Rule 2: the rows of the Setup that closed on this sample, in one transaction."""
	try:
		c = _changeover(st, ev.get('product'), db, out)
	except:
		import sys
		out.append('changeover lookup failed product=%s start=%s end=%s, no Setup rows: %s' % (
			ev.get('product'), _iso_s(ev['start']), _iso_s(ev['end']), sys.exc_info()[1]))
		return
	rows = split_setup(ev['start'], ev['end'], ev['dur_s'], c)
	for r in rows:
		r['product_code'] = ev.get('product')
		r['work_order_id'] = wo_now
	try:
		ids = db.insert_events(rows)
	except:
		import sys
		out.append('insert failed Setup product=%s start=%s end=%s: %s' % (
			ev.get('product'), _iso_s(ev['start']), _iso_s(ev['end']), sys.exc_info()[1]))
		return
	for event_id, r in zip(ids, rows):
		out.append('event %s %s start=%s end=%s dur=%.1fs changeover=%gs product=%s wo=%s' % (
			event_id, r['class'], _iso_s(r['start']), _iso_s(r['end']), r['dur_s'], c, r['product_code'],
			r['work_order_id']))


def tag_values(seg, f, t):
	"""Rule 5: Downtime/* values for the open segment seg at sample time t (ActiveSince in epoch ms).
	f is the open fault event, or None. None when no segment is open: leave the tags as they are."""
	if seg is None:
		return None
	if seg['state'] == 1:
		return {'State': 'Running', 'CurrentAlarm': 0, 'Class': '', 'ActiveSince': None, 'DurationSec': 0.0}
	dur = (t - seg['start']) / 1000.0
	if seg['state'] == 3:
		cls = f['class'] if f is not None else classify(seg['fault'])
		return {'State': 'Unplanned', 'CurrentAlarm': seg['fault'] if seg['fault'] is not None else 0,
				'Class': cls, 'ActiveSince': seg['start'], 'DurationSec': dur}
	if seg['state'] == SETUP_STATE:
		# DT-002 rule 3: Planned up to the scheduled changeover, Unplanned past it (lookup failed: Planned).
		c = seg.get('changeover')
		if c is not None and dur > c:
			return {'State': 'Unplanned', 'CurrentAlarm': 0, 'Class': OVERRUN, 'ActiveSince': seg['start'],
					'DurationSec': dur}
		return {'State': 'Planned', 'CurrentAlarm': 0, 'Class': CHANGEOVER, 'ActiveSince': seg['start'],
				'DurationSec': dur}
	return None


def handle(st, sample, events, db, wo_now):
	"""One accepted engine sample and its events; wo_now is the work order of the open run (or None).
	Returns (log lines, tag values to write). Does not log, does not write tags."""
	out = []
	for ev in events:
		if ev['type'] == 'segment_close':
			st['segment'] = None
			if ev['state'] == 3:
				_close_fault(st, ev, db, out)
			elif ev['state'] == SETUP_STATE:
				_close_setup(st, ev, db, wo_now, out)
		elif ev['type'] == 'segment_open':
			st['segment'] = {'state': ev['state'], 'fault': ev.get('fault'), 'start': ev['start'], 'changeover': None}
			if ev['state'] == 3:
				_open_fault(st, sample, ev, wo_now, out)
			elif ev['state'] == SETUP_STATE:
				try:
					st['segment']['changeover'] = _changeover(st, ev.get('product'), db, out)
				except:
					import sys
					out.append('changeover lookup failed product=%s (tags stay Planned): %s' % (
						ev.get('product'), sys.exc_info()[1]))
	want = tag_values(st['segment'], st['fault'], sample['t'])
	if want is None:
		return out, {}
	tags = {}
	for name in TAG_NAMES:
		if name in ALWAYS_WRITTEN or st['written'].get(name, _UNWRITTEN) != want[name]:
			tags[name] = want[name]
	st['written'].update(tags)
	return out, tags


# ---------- database ----------

class _SqlDb(object):
	"""The db interface of handle() on PostgreSQL, connection 'mes'. Parameterised SQL only."""

	def _ts(self, t):
		return system.date.fromMillis(int(t))

	def insert_event(self, r, tx=None):
		sql = ("INSERT INTO mes.downtime_event (start_ts, end_ts, duration_s, source, machine_state, fault_code, "
			   "class, is_planned, product_code, work_order_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING event_id")
		args = [self._ts(r['start']), self._ts(r['end']), round(r['dur_s'], 1), r['source'], r['machine_state'],
				r['fault_code'], r['class'], r['is_planned'], r['product_code'], r['work_order_id']]
		ds = system.db.runPrepQuery(sql, args, DATABASE, tx) if tx else system.db.runPrepQuery(sql, args, DATABASE)
		return ds[0][0]

	def insert_events(self, rows):
		"""All rows in one transaction (the Setup split); returns their event_ids."""
		tx = system.db.beginTransaction(DATABASE)
		try:
			ids = [self.insert_event(r, tx) for r in rows]
			system.db.commitTransaction(tx)
			return ids
		except:
			system.db.rollbackTransaction(tx)
			raise
		finally:
			system.db.closeTransaction(tx)

	def changeover_s(self, product):
		"""product_master.scheduled_changeover_s, or None when the product has no row."""
		ds = system.db.runPrepQuery(
			"SELECT scheduled_changeover_s FROM mes.product_master WHERE product_code = ?", [product], DATABASE)
		return ds[0][0] if len(ds) else None


# ---------- tags (I/O only) ----------

def _write_tags(st, tags):
	if not tags:
		return
	names = [n for n in TAG_NAMES if n in tags]
	paths = ['%s/Downtime/%s' % (mes.engine.ROOT, n) for n in names]
	vals = [system.date.fromMillis(int(tags[n])) if n == 'ActiveSince' and tags[n] is not None else tags[n]
			for n in names]
	qcs = system.tag.writeBlocking(paths, vals)
	bad = ['%s=%s' % (n, q) for n, q in zip(names, qcs) if not q.isGood()]
	if bad:
		st['written'] = {}  # rewrite everything on the next tick
		if not st['warned']:
			log.warn('tag write failed: %s' % ', '.join(bad))
		st['warned'] = True
	else:
		st['warned'] = False


# ---------- engine handler ----------

def on_tick(sample, events):
	st = _store()
	try:
		run = mes.workorder.current()
		lines, tags = handle(st, sample, events, _SqlDb(), run['work_order_id'] if run else None)
	except:
		import traceback
		log.error('handle failed: %s' % traceback.format_exc())
		return
	for line in lines:
		if line.startswith(WARN):
			log.warn(line[len(WARN):])
		else:
			log.info(line)
	try:
		_write_tags(st, tags)
	except:
		import traceback
		st['written'] = {}
		log.error('tag write failed: %s' % traceback.format_exc())


# ---------- read API (copies, never the live store) ----------

def open_fault():
	"""The open fault event, or None."""
	f = _store()['fault']
	return dict(f) if f is not None else None
