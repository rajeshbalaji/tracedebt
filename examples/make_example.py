"""Build a small, self-contained example: one spec and six Jaeger trace
windows from a 14-service system. A foreign-datastore access and an
undeclared cross-context call appear from window w2 on; window w1 carries a
one-off burst edge that should NOT be reported. From w3 on, a service also
publishes into another service's event topic.

    python -m examples.make_example examples/demo
    python -m tracedebt analyze examples/demo/spec.yaml examples/demo/w*.json
"""
import json
import os
import random
import sys

import yaml

from bench.generator import NoiseModel, System, TraceGen, to_jaeger
from tracedebt.spec import DB_PREFIX, TOPIC_PREFIX


def main(out: str) -> None:
    os.makedirs(out, exist_ok=True)
    sysm = System.build(14, seed=4)
    with open(os.path.join(out, "spec.yaml"), "w") as fh:
        d = sysm.spec().to_dict(); d["system"] = "ticketing-demo"
        yaml.safe_dump(d, fh, sort_keys=False)
    tg = TraceGen(sysm, random.Random(11), NoiseModel(burst_prob=0.0, sporadic_per_trace=0.0))
    doms, dbs = sysm.of_layer("domain"), sysm.dbs()
    db = sorted(dbs)[0]
    intruder = next(d for d in doms if d != dbs[db] and sysm.ctx(d) != sysm.ctx(dbs[db]))
    other = next(d for d in doms if sysm.ctx(d) != sysm.ctx(intruder)
                 and (intruder, d) not in sysm.runtime)
    for w in range(6):
        if w == 1:      # one-off migration job, gone in the next window
            tg.noise.burst_prob = 1.0
        else:
            tg.noise.burst_prob = 0.0
        if w == 2:
            sysm.runtime[(intruder, DB_PREFIX + db)] = 0.3
            sysm.runtime[(intruder, other)] = 0.15
        if w == 3 and sysm.topics:
            t = sorted(sysm.topics)[0]
            pub = next(d for d in doms if d != sysm.topics[t] and d != intruder)
            sysm.runtime[(pub, TOPIC_PREFIX + t)] = 0.4
        traces, _ = tg.window(4000, 0.1)
        with open(os.path.join(out, f"w{w}.json"), "w") as fh:
            json.dump(to_jaeger(traces), fh)
    print(f"wrote spec + 6 windows to {out}; injected {intruder} -> {DB_PREFIX}{db}, "
          f"{intruder} -> {other}" + (f", {pub} -> {TOPIC_PREFIX}{t}" if sysm.topics else ""))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "examples/demo")
