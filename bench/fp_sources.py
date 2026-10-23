"""Attribute TraceDebt false positives to their cause (spec lag vs. noise)."""
import json, random, sys
from collections import Counter
from bench.generator import NoiseModel, System, TraceGen
from bench.run_eval import TOPOLOGIES, RELEASES, WINDOWS, flagged
from tracedebt import graph as G
from tracedebt.rules import check
from tracedebt.tracker import Tracker

def run(size, seed):
    n, req = TOPOLOGIES[size]
    s = System.build(n, seed); tg = TraceGen(s, random.Random(seed * 7919 + 13), NoiseModel())
    tr = Tracker(k=3, m=4, close_after=3); c = Counter(); recent_noise = []
    for r in range(RELEASES):
        if r: s.evolve(r)
        spec = s.spec()
        for w in range(WINDOWS):
            traces, noise = tg.window(req, 0.10)
            recent_noise = (recent_noise + [noise])[-4:]
            g = G.build("x", traces, spec, keep_trace_edges=False); tr.update(g, check(g, spec, 1))
        if r == 0: continue
        gt = s.active_drift()
        for e in flagged(tr) - gt:
            if e in s.pending: c["spec_lag"] += 1
            elif any(e in nz for nz in recent_noise): c["noise"] += 1
            elif any(d.edge == e and d.repaired is not None for d in s.drifts): c["repaired_drift_lingering"] += 1
            else: c["other"] += 1
    return c

tot = Counter()
for size in "SML":
    for sd in range(1, 11):
        tot += run(size, sd)
print(dict(tot))
json.dump(dict(tot), open("results/fp_sources.json", "w"))
