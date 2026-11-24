"""Standalone Python replay of the UNS Studio sim `fourzero_printing_press1`.

Reads the sim definition (docs/sim/fourzero_printing_press1.json) and publishes the
16 Press 1 raw-layer tags to an MQTT broker in the UNS Studio JSON envelope, looping
the 35 phases (1,046 s) the same way UNS Studio does. Raw layer only: it adds nothing
the definition does not have (docs/sim_spec_press1.md).

Do not point this at a broker where the real UNS Studio sim is publishing under the
same topic root, or two writers will fight over the same tags. Use your own broker,
or pass --root to publish under a different root.

    py tools/sim/press1_sim.py --host localhost --port 1883
    py tools/sim/press1_sim.py --dry-run --speed 10
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DEF = Path(__file__).resolve().parents[2] / "docs" / "sim" / "fourzero_printing_press1.json"


class Generators:
    """Per-topic generator state. Counters and random walks carry across phases and loops."""

    def __init__(self):
        self.counters = {}
        self.walks = {}

    def next(self, topic, gen):
        kind = gen["type"]
        if kind == "static":
            return gen["value"]
        if kind == "counter":
            # Cumulative; step 0 holds the value (Build Guide §3, probe #1). Publishes as float.
            value = self.counters.get(topic, float(gen.get("start", 0))) + gen.get("step", 0)
            self.counters[topic] = value
            return float(value)
        if kind == "random_walk":
            lo, hi = gen["min"], gen["max"]
            prev = self.walks.get(topic)
            if prev is None or not lo <= prev <= hi:
                # New band (e.g. over-temp 215-235): jump to the phase's start, no ramp (spec §11 rule 6).
                value = gen["start"]
            else:
                value = prev + random.uniform(-gen["step"], gen["step"])
            value = round(min(max(value, lo), hi), gen.get("decimals", 2))
            self.walks[topic] = value
            return float(value)
        raise ValueError("unsupported generator type %r on %s" % (kind, topic))


def datatype(value):
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    return "string"


def envelope(value, units):
    msg = {
        "value": value,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "datatype": datatype(value),
        "status": "Good",
    }
    if units:
        msg["units"] = units
    return msg


def connect(args):
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        sys.exit("paho-mqtt is not installed: py -m pip install -r tools/sim/requirements.txt")
    # CoreFlux 2.2.0 rejects an empty client ID, so always send one.
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=args.client_id)
    if args.username:
        client.username_pw_set(args.username, args.password)
    try:
        client.connect(args.host, args.port, keepalive=30)
    except OSError as e:
        sys.exit("cannot reach broker %s:%d (%s)" % (args.host, args.port, e))
    client.loop_start()
    return client


def run(args):
    definition = json.loads(Path(args.definition).read_text(encoding="utf-8"))
    phases = definition["phases"]
    loop_s = sum(p["duration_seconds"] for p in phases)
    print("%s: %d phases, %d s loop" % (definition["name"], len(phases), loop_s))

    client = None if args.dry_run else connect(args)
    gens = Generators()
    next_tick = time.monotonic()
    loop_no = 0
    try:
        while True:
            loop_no += 1
            for phase in phases:
                interval = phase["interval_seconds"]
                samples = phase["duration_seconds"] // interval
                print("loop %d  %-14s %3d s" % (loop_no, phase["name"], phase["duration_seconds"]))
                for _ in range(samples):
                    for group in phase["groups"]:
                        prefix = args.root or group["topic_prefix"]
                        for t in group["topics"]:
                            value = gens.next(t["topic"], t["generator"])
                            topic = "%s/%s" % (prefix, t["topic"])
                            payload = json.dumps(envelope(value, t.get("units")), ensure_ascii=False)
                            if client:
                                client.publish(topic, payload, qos=args.qos, retain=args.retain)
                            elif args.verbose:
                                print("  %s %s" % (topic, payload))
                    next_tick += interval / args.speed
                    time.sleep(max(0.0, next_tick - time.monotonic()))
            if args.loops and loop_no >= args.loops:
                break
    except KeyboardInterrupt:
        print("stopped")
    finally:
        if client:
            client.loop_stop()
            client.disconnect()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--client-id", default="press1-python-sim")
    p.add_argument("--username")
    p.add_argument("--password")
    p.add_argument("--qos", type=int, default=0, choices=(0, 1, 2))
    p.add_argument("--retain", action="store_true", help="publish retained, so new subscribers get the last value")
    p.add_argument("--root", help="override the topic root (default: the definition's 40Printing/Dallas/Press Room/Press 1)")
    p.add_argument("--definition", default=str(DEFAULT_DEF))
    p.add_argument("--speed", type=float, default=1.0, help="time multiplier; 10 runs a loop in about 105 s")
    p.add_argument("--loops", type=int, default=0, help="stop after N loops (0 = forever)")
    p.add_argument("--dry-run", action="store_true", help="no broker; print phases only")
    p.add_argument("-v", "--verbose", action="store_true", help="with --dry-run, print every message")
    run(p.parse_args())


if __name__ == "__main__":
    main()
