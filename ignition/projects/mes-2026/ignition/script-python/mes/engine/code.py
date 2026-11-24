# mes.engine -- the shared MES core for Press 1 (ENG-001). No database, no functional tags.
#
# A gateway timer (ignition/timer/mesTick, 2000 ms fixed delay) calls tick(), which reads the
# 17 [default] edge tags (UNS-002, already typed) and hands one sample to step(). step() holds
# all detection logic -- de-dup on PLC/SampleTime, gap guard, meter deltas, product change,
# debounced state segments, run start -- and returns the new events. tick() logs them and
# passes them to HANDLERS, where WO-001, DT-001 and OEE-001 plug in.
#
# step() and everything it calls is pure Python (no system.*, no Java, no logging) so the
# offline replay (ignition/tests/replay_engine.py) runs the same file under Python 3.
#
# State lives in system.util.getGlobals()[STORE_KEY] for the JVM's lifetime. Only plain dicts,
# lists, numbers and strings go in it. It is lost on a gateway restart.

import copy
import time

# The single place a tag path starts from.
ROOT = '[default]40Printing/Dallas/Press Room/Press 1'

# (key, sub-path)
TAGS = [
	('conn',   'PLC/Connected'),
	('state',  'PLC/MachineState'),
	('fault',  'PLC/FaultCode'),
	('product','PLC/ProductCode'),
	('lanes',  'PLC/WebLanes'),
	('good',   'PLC/GoodMeters'),
	('reject', 'PLC/RejectMeters'),
	('rto',    'PLC/RTOInhibit'),
	('tenU',   'Unwind/WebTension'),
	('tenR',   'Rewind/WebTension'),
	('speed',  'Print Section/LineSpeed'),
	('reg',    'Print Section/RegistrationError'),
	('visc',   'Print Section/InkViscosity'),
	('z1',     'Tunnel Dryer/Zone1Temp'),
	('z2',     'Tunnel Dryer/Zone2Temp'),
	('z3',     'Tunnel Dryer/Zone3Temp'),
	('t',      'PLC/SampleTime'),
]
PATHS = [ROOT + '/' + p for _, p in TAGS]

DEBOUNCE = 2        # consecutive samples in the same (state, fault) before a segment opens
GAP_S = 10          # samples further apart than this are a sampling break (e.g. the host slept)
MAX_EVENTS = 500    # events kept in the store, oldest first
STORE_VERSION = 1
STORE_KEY = 'mes.engine.v%d' % STORE_VERSION  # versioned key: a stale copy of older code can't clobber the live store

# Field order in the one-line event log.
LOG_FIELDS = ['state', 'fault', 'product', 'lanes', 'from', 'to', 'start', 'end', 'dur_s', 'good', 'reject', 'gap_s']
TIME_FIELDS = ('t', 'start', 'end', 'from')

def _workorder(sample, events):
	mes.workorder.on_tick(sample, events)

def _schedule(sample, events):
	mes.schedule.on_tick(sample, events)

def _downtime(sample, events):
	mes.downtime.on_tick(sample, events)

def _oee(sample, events):
	mes.oee.on_tick(sample, events)

# Later tasks append h(sample, events) here. _workorder first: the schedule reads the runs it writes.
HANDLERS = [_workorder, _schedule, _downtime, _oee]

log = system.util.getLogger('mes.engine')


# ---------- store ----------

def _store():
	g = system.util.getGlobals()
	st = g.get(STORE_KEY)
	if st is None:
		st = {
			'version': STORE_VERSION,
			'started': int(time.time() * 1000),
			'samples': 0,           # samples accepted by step() (after de-dup)
			'last': None,           # previous accepted sample
			'run': None,            # current run of identical (state, fault): key, start, n
			'open_segment': None,
			'events': [],           # newest MAX_EVENTS, oldest first
			'seq': 0,
			'totals': {'good': 0.0, 'reject': 0.0},  # sum of meter deltas since engine start
			'warned': {},           # tag key -> True once a bad read has been logged
		}
		g[STORE_KEY] = st
		log.info('engine store created %s' % STORE_KEY)
	return st


# ---------- detection (pure) ----------

def _emit(st, out, typ, t, **fields):
	st['seq'] += 1
	ev = {'seq': st['seq'], 'type': typ, 't': t}
	ev.update(fields)
	st['events'].append(ev)
	del st['events'][:-MAX_EVENTS]
	out.append(ev)


def _meter_delta(cur, prev):
	"""(delta, restart). A drop means the sim restarted: delta 0, not negative production."""
	if cur is None or prev is None:
		return 0.0, False
	if cur >= prev:
		return float(cur - prev), False
	return 0.0, True


def _close_segment(st, out, t):
	seg = st['open_segment']
	if seg is None:
		return
	st['open_segment'] = None
	_emit(st, out, 'segment_close', t, state=seg['state'], fault=seg['fault'], product=seg['product'],
		  start=seg['start'], end=t, dur_s=(t - seg['start']) / 1000.0)


def step(st, sample):
	"""Feed one sample ({key: typed value}, 't' in epoch ms) into the store. Returns the new events."""
	out = []
	t = sample.get('t')
	if t is None or sample.get('state') is None:
		return out
	last = st['last']
	# De-duplicate on the sim's sample time: timer and sim drift around 2 s.
	if last is not None and t <= last['t']:
		return out
	if last is not None and t - last['t'] > GAP_S * 1000:
		# Sampling break: close at the last sample before the gap, not the first after it, and restart the debounce.
		_close_segment(st, out, last['t'])
		st['run'] = None
		_emit(st, out, 'gap', t, gap_s=(t - last['t']) / 1000.0, **{'from': last['t']})
	s = dict(sample)
	dg, rg = _meter_delta(s.get('good'), last['good'] if last else None)
	dr, rr = _meter_delta(s.get('reject'), last['reject'] if last else None)
	s['dg'] = dg
	s['dr'] = dr
	if rg or rr:
		_emit(st, out, 'restart', t, good=s.get('good'), reject=s.get('reject'))
	st['totals']['good'] += dg
	st['totals']['reject'] += dr
	if last is not None and s.get('product') is not None and last['product'] is not None \
			and s['product'] != last['product']:
		_emit(st, out, 'product_change', t, lanes=s.get('lanes'), **{'from': last['product'], 'to': s['product']})
	key = [s['state'], s.get('fault') if s['state'] == 3 else 0]
	run = st['run']
	if run is None or run['key'] != key:
		# First sample that differs closes the open segment (a fault-code change inside Faulted too).
		_close_segment(st, out, t)
		run = st['run'] = {'key': key, 'start': t, 'n': 0}
	run['n'] += 1
	if run['n'] == DEBOUNCE:
		st['open_segment'] = {'state': key[0], 'fault': key[1], 'product': s.get('product'),
							  'start': run['start']}  # backdated to the first of the debounce samples
		_emit(st, out, 'segment_open', t, state=key[0], fault=key[1], product=s.get('product'), start=run['start'])
	if last is not None and s['state'] == 1 and last['state'] == 2:
		# Setup -> Running, not debounced (WO-001 needs it within one sample). Faulted -> Running is not a run start.
		_emit(st, out, 'run_start', t, product=s.get('product'), lanes=s.get('lanes'))
	st['last'] = s
	st['samples'] += 1
	return out


# ---------- Ignition I/O ----------

def _epoch_ms(iso):
	# ISO 8601 UTC with microseconds, e.g. 2026-10-20T19:00:51.377788Z
	from java.time import Instant
	return Instant.parse(iso).toEpochMilli()


def _iso(ms):
	from java.time import Instant
	return str(Instant.ofEpochMilli(ms))


def _warn_once(st, key, msg):
	if not st['warned'].get(key):
		st['warned'][key] = True
		log.warn('%s: %s' % (key, msg))


def _read(st):
	"""One aligned snapshot of all 17 tags -> sample dict ('t' in epoch ms, or None)."""
	qvs = system.tag.readBlocking(PATHS)
	vals = {}
	for (key, sub), qv in zip(TAGS, qvs):
		vals[key] = None
		if not qv.quality.isGood() or qv.value is None:
			_warn_once(st, key, 'bad read %s at %s/%s' % (qv.quality, ROOT, sub))
			continue
		vals[key] = qv.value
	if vals['t'] is not None:
		try:
			vals['t'] = _epoch_ms(str(vals['t']))
		except Exception as e:
			_warn_once(st, 't', 'cannot parse SampleTime %r (%s)' % (vals['t'], e))
			vals['t'] = None
	return vals


def _fmt(ev):
	parts = [ev['type'], 'seq=%d' % ev['seq']]
	for f in LOG_FIELDS:
		if f in ev:
			v = ev[f]
			if f in TIME_FIELDS and v is not None and ev['type'] != 'product_change':
				v = _iso(v)
			parts.append('%s=%s' % (f, v))
	parts.append('t=%s' % _iso(ev['t']))
	return ' '.join(parts)


def _dispatch(sample, events):
	for h in HANDLERS:
		try:
			h(sample, events)
		except:
			import traceback
			log.error('handler %s failed: %s' % (getattr(h, '__name__', h), traceback.format_exc()))


def tick():
	st = _store()
	sample = _read(st)
	if sample['t'] is None or sample['state'] is None:
		return
	n = st['samples']
	events = step(st, sample)
	if st['samples'] == n:
		return  # duplicate read: step() accepted nothing
	for ev in events:
		log.info(_fmt(ev))
	_dispatch(copy.deepcopy(st['last']), copy.deepcopy(events))


# ---------- read API (copies, never the live store) ----------

def latest():
	"""Last accepted sample, with its meter deltas dg / dr, or None."""
	return copy.deepcopy(_store()['last'])


def events_since(seq):
	"""Events with seq > seq still in the store, oldest first."""
	return copy.deepcopy([e for e in _store()['events'] if e['seq'] > seq])


def open_segment():
	return copy.deepcopy(_store()['open_segment'])


def totals():
	st = _store()
	return {'good': st['totals']['good'], 'reject': st['totals']['reject'], 'since': st['started']}


def info():
	st = _store()
	return {'started': st['started'], 'samples': st['samples'],
			'last_t': st['last']['t'] if st['last'] else None, 'store_key': STORE_KEY}
