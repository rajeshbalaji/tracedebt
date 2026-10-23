"""Does the coverage formula predict what TraceDebt actually confirms?

For every drift injected on the synthetic benchmark we know the share p of
the caller's requests that carry it. Under 3-of-4 persistence the drift is
confirmed within the first four windows of its release exactly when it is
seen in at least three of them. If the caller appears in n_w sampled traces
in window w, the formula in tracedebt/coverage.py predicts

    q_w = 1 - (1 - p)^{n_w},   P(confirmed) = P(at least 3 of 4 windows seen)

(a Poisson-binomial tail, because n_w differs between windows). We compare
that prediction with what the tracker did, binned by predicted probability,
and report how often drifts at or above the 90% floor were in fact confirmed.

    python -m bench.coverage_check --out results/coverage_check.json
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import random
from collections import defaultdict

from bench.generator import NoiseModel, System, TraceGen
from bench.run_eval import RELEASES, TOPOLOGIES, WINDOWS, flagged
from tracedebt import graph as G
from tracedebt.coverage import q_star
from tracedebt.rules import check
from tracedebt.tracker import Tracker

K, M = 3, 4


def p_at_least_k(qs, k=K):
    tot = 0.0
    for hits in itertools.product([0, 1], repeat=len(qs)):
        if sum(hits) >= k:
            pr = 1.0
            for h, q in zip(hits, qs):
                pr *= q if h else 1 - q
            tot += pr
    return tot


def one(size, seed, sampling, sigma=0.0, intermittent=0.0):
    n_svc, requests = TOPOLOGIES[size]
    sysm = System.build(n_svc, seed)
    tg = TraceGen(sysm, random.Random(seed * 7919 + 13), NoiseModel())
    tr = Tracker(k=K, m=M)
    rng2 = random.Random(seed * 31 + 7)      # traffic and on/off draws, apart from the generator's
    rows = []
    for r in range(RELEASES):
        if r > 0:
            sysm.evolve(r)
        spec = sysm.spec()
        new = [d for d in sysm.drifts if d.injected == r]
        counts = {d.id: [] for d in new}
        seen_by_w3 = {}
        for w in range(WINDOWS):
            req = requests
            if sigma:                         # uneven traffic: lognormal factor with mean 1
                req = max(1, int(requests * math.exp(rng2.gauss(0, sigma) - sigma ** 2 / 2)))
            off = {}
            if intermittent:                  # drift that only runs in some windows
                for dd in sysm.drifts:
                    if dd.repaired is None and dd.edge in sysm.runtime and rng2.random() < intermittent:
                        off[dd.edge] = sysm.runtime.pop(dd.edge)
            traces, _ = tg.window(req, sampling)
            sysm.runtime.update(off)
            g = G.build(f"r{r}w{w}", traces, spec, keep_trace_edges=False)
            tr.update(g, check(g, spec, 1))
            if w < M:
                for d in new:
                    counts[d.id].append(g.svc_traces.get(d.edge[0], 0))
            if w == M - 1:
                fl = flagged(tr)
                for d in new:
                    seen_by_w3[d.id] = d.edge in fl
        for d in new:
            n = counts[d.id]
            qs = [1 - (1 - d.prob) ** x for x in n]
            nbar = sum(n) / len(n)
            qstar = q_star(K, M, 0.9)
            pmin = 1.0 if nbar < 1 else 1 - (1 - qstar) ** (1 / nbar)
            rows.append({"p": d.prob, "n": nbar, "pred": p_at_least_k(qs),
                         "obs": bool(seen_by_w3[d.id]), "above_floor": d.prob >= pmin,
                         "size": size, "sampling": sampling})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/coverage_check.json")
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--sigma", type=float, default=0.0, help="spread of per-window traffic")
    ap.add_argument("--intermittent", type=float, default=0.0,
                    help="chance that a drift is switched off in a given window")
    ap.add_argument("--configs", default="S:0.1,M:0.1,L:0.1,M:0.01,M:0.05")
    a = ap.parse_args()
    rows = []
    for c in a.configs.split(","):
        size, s = c.split(":")
        for sd in range(1, a.seeds + 1):
            rows += one(size, sd, float(s), a.sigma, a.intermittent)
        print(size, s, len(rows), flush=True)
    bins = [(0, .1), (.1, .3), (.3, .5), (.5, .7), (.7, .9), (.9, 1.01)]
    table = []
    for lo, hi in bins:
        b = [x for x in rows if lo <= x["pred"] < hi]
        if b:
            table.append({"bin": [lo, min(hi, 1.0)], "n": len(b),
                          "pred": sum(x["pred"] for x in b) / len(b),
                          "obs": sum(x["obs"] for x in b) / len(b)})
    af = [x for x in rows if x["above_floor"]]
    bf = [x for x in rows if not x["above_floor"]]
    out = {"n": len(rows), "bins": table,
           "above_floor": {"n": len(af), "confirmed": sum(x["obs"] for x in af) / max(len(af), 1),
                           "mean_pred": sum(x["pred"] for x in af) / max(len(af), 1)},
           "below_floor": {"n": len(bf), "confirmed": sum(x["obs"] for x in bf) / max(len(bf), 1),
                           "mean_pred": sum(x["pred"] for x in bf) / max(len(bf), 1)},
           "brier": sum((x["pred"] - x["obs"]) ** 2 for x in rows) / len(rows),
           "mean_pred": sum(x["pred"] for x in rows) / len(rows),
           "mean_obs": sum(x["obs"] for x in rows) / len(rows), "rows": rows}
    json.dump(out, open(a.out, "w"), indent=1)
    for t in table:
        print(f"pred {t['bin'][0]:.1f}-{t['bin'][1]:.1f}  n={t['n']:3d}  predicted {t['pred']:.2f}  observed {t['obs']:.2f}")
    print("above floor:", out["above_floor"], "\nbelow floor:", out["below_floor"],
          "\nBrier", round(out["brier"], 3), "mean pred", round(out["mean_pred"], 3),
          "mean obs", round(out["mean_obs"], 3))


if __name__ == "__main__":
    main()
