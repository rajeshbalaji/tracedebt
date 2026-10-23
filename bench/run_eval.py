"""Evaluation harness for TraceDebt.

RQ1  How accurately does TraceDebt report architectural drift compared with
     a one-shot conformance check and a spec-less graph diff?
RQ2  How do persistence (k-of-m) and the trace-support floor trade
     precision, recall and detection delay? How does sampling matter?
RQ3  Is it fast enough to run on every observation window?

Usage:  python -m bench.run_eval --out results/
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics as st
import time
from collections import defaultdict
from typing import Dict, List, Set, Tuple

from bench.generator import NoiseModel, System, TraceGen
from tracedebt import graph as G
from tracedebt.bootstrap import draft
from tracedebt.spec import DB_PREFIX, Spec
from tracedebt.debt import snapshot
from tracedebt.rules import check
from tracedebt.tracker import Tracker

TOPOLOGIES = {"S": (14, 6000), "M": (28, 12000), "L": (42, 20000)}
RELEASES, WINDOWS = 10, 6
SNAP_T = [2, 3, 5, 10, 20, 50]


def merge(label: str, graphs: List[G.WindowGraph]) -> G.WindowGraph:
    m = G.WindowGraph(label)
    for g in graphs:
        m.n_traces += g.n_traces
        m.nodes |= g.nodes
        for e, s in g.edges.items():
            t = m.edges.setdefault(e, G.EdgeStat(sample_trace=s.sample_trace))
            t.calls += s.calls; t.traces += s.traces; t.duration_us += s.duration_us
    return m


def flagged(tr: Tracker) -> Set[Tuple[str, str]]:
    out = set()
    for f in tr.active():
        out.update(f.edges)
    return out


def run_one(size: str, seed: int, sampling: float, variants: Dict[str, dict],
            collect_timeline: bool = False, noise: NoiseModel = None,
            spec_lag: float = None):
    n_svc, requests = TOPOLOGIES[size]
    sysm = System.build(n_svc, seed)
    if spec_lag is not None:
        sysm.spec_lag = spec_lag
    tg = TraceGen(sysm, random.Random(seed * 7919 + 13), noise or NoiseModel())
    trackers = {k: Tracker(k=v["k"], m=v["m"], close_after=v.get("close", 3)) for k, v in variants.items()}
    counts = {k: [0, 0, 0] for k in list(variants) + ["Snapshot", "GraphDiff"]
              + [f"Snap@{t}" for t in SNAP_T]}  # tp fp fn
    per_type = defaultdict(lambda: [0, 0])                    # kind -> [hit, total]
    first_flag: Dict[int, int] = {}
    baseline_edges: Set[Tuple[str, str]] = set()
    timeline, spans_total, t_analysis = [], 0, 0.0
    gidx = 0
    for r in range(RELEASES):
        if r > 0:
            sysm.evolve(r)
        spec = sysm.spec()
        rel_graphs = []
        for w in range(WINDOWS):
            traces, _noise = tg.window(requests, sampling)
            spans_total += sum(len(t) for t in traces)
            t0 = time.perf_counter()
            g = G.build(f"r{r}w{w}", traces, spec)
            cand_cache = {}
            for name, v in variants.items():
                c = v["min"]
                if c not in cand_cache:
                    cand_cache[c] = check(g, spec, c)
                trackers[name].update(g, cand_cache[c])
            t_analysis += time.perf_counter() - t0
            rel_graphs.append(g)
            # detection delay for the default configuration
            fl = flagged(trackers["TraceDebt"])
            for d in sysm.drifts:
                if d.repaired is None and d.edge in fl and d.id not in first_flag:
                    first_flag[d.id] = gidx - d.injected * WINDOWS
            if collect_timeline:
                snap = snapshot(g, trackers["TraceDebt"].active())
                timeline.append({"window": g.label, "release": r, "severity": snap.severity,
                                 "exposure": snap.exposure, "trace_share": snap.trace_share,
                                 "latency_share": snap.latency_share,
                                 "gt_active": len(sysm.active_drift()),
                                 "findings": snap.findings})
            g.trace_edges = []
            gidx += 1
        rel = merge(f"r{r}", rel_graphs)
        if r == 0:
            baseline_edges = set(rel.edges)
            continue
        gt = sysm.active_drift()
        res = {name: flagged(trackers[name]) for name in variants}
        snap_edges = set()
        for c in check(rel, spec, 1):
            snap_edges.update(c.edges)
        res["Snapshot"] = snap_edges
        for t in SNAP_T:
            se = set()
            for c in check(rel, spec, t):
                se.update(c.edges)
            res[f"Snap@{t}"] = se
        res["GraphDiff"] = set(rel.edges) - baseline_edges
        for name, fl in res.items():
            counts[name][0] += len(fl & gt)
            counts[name][1] += len(fl - gt)
            counts[name][2] += len(gt - fl)
        for d in sysm.drifts:
            if d.repaired is None:
                per_type[d.kind][1] += 1
                per_type[d.kind][0] += d.edge in res["TraceDebt"]
    # drifts that were active for at least one full release
    delays = [v for v in first_flag.values()]
    rare_missed = sum(1 for d in sysm.drifts if d.id not in first_flag)
    return {"counts": counts, "per_type": dict(per_type), "delays": delays,
            "n_drifts": len(sysm.drifts), "never_flagged": rare_missed,
            "drift_probs": [(d.prob, d.id in first_flag) for d in sysm.drifts],
            "drift_kinds": [(d.kind, d.id in first_flag) for d in sysm.drifts],
            "spans": spans_total, "t_analysis": t_analysis, "timeline": timeline,
            "services": len(sysm.services)}


def bootstrap_eval(size: str, seed: int, sampling: float = 0.10):
    """Draft a spec from release-0 traces and compare it with the true one."""
    n_svc, requests = TOPOLOGIES[size]
    sysm = System.build(n_svc, seed)
    tg = TraceGen(sysm, random.Random(seed * 7919 + 13), NoiseModel())
    base = Spec.from_dict({"layers": ["edge", "api", "domain", "data"],
                           "ignore": {"services": ["mesh-probe"], "operations": ["/health"]}})
    gs = [G.build(f"w{w}", tg.window(requests, sampling)[0], base, keep_trace_edges=False)
          for w in range(WINDOWS)]
    d, notes = draft(gs)
    true = sysm.spec()
    ds = Spec.from_dict(d)
    lay = [ds.services[n].layer == true.services[n].layer for n in ds.services if n in true.services]
    own = [ds.datastores.get(db) == o for db, o in true.datastores.items() if db in ds.datastores]
    def deps(sp):
        return {e for e in sp.declared if not e[1].startswith(DB_PREFIX)}
    td, dd = deps(true), deps(ds)
    return {"layer_acc": sum(lay) / len(lay), "owner_acc": sum(own) / max(len(own), 1),
            "dep_P": len(td & dd) / max(len(dd), 1), "dep_R": len(td & dd) / max(len(td), 1),
            "notes": len(notes), "services": len(ds.services), "true_services": len(true.services)}


def prf(c):
    tp, fp, fn = c
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results")
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    seeds = list(range(1, args.seeds + 1))
    main_variants = {
        "TraceDebt": {"k": 3, "m": 4, "min": 1},
        "TD-noPersist": {"k": 1, "m": 1, "min": 1, "close": 1},
        "TD-floor2": {"k": 3, "m": 4, "min": 2},
    }
    out = {"rq1": {}, "rq2_k": {}, "rq2_sampling": {}, "rq3": {}, "per_type": {}}

    # RQ1 + RQ3: three topology sizes, sampling 10%
    sizes = ["S", "M"] if args.quick else ["S", "M", "L"]
    probs_all = []
    kinds_all = []
    for size in sizes:
        agg = defaultdict(list)
        pt = defaultdict(lambda: [0, 0])
        delays, spans, t = [], 0, 0.0
        n_dr, never = 0, 0
        for sd in seeds:
            r = run_one(size, sd, 0.10, main_variants, collect_timeline=(size == "M" and sd == 1))
            if r["timeline"]:
                out["timeline"] = r["timeline"]
            for name, c in r["counts"].items():
                agg[name].append(prf(c) + tuple(c))
            for k, v in r["per_type"].items():
                pt[k][0] += v[0]; pt[k][1] += v[1]
            delays += r["delays"]; spans += r["spans"]; t += r["t_analysis"]
            n_dr += r["n_drifts"]; never += r["never_flagged"]
            probs_all += r["drift_probs"]
            kinds_all += r["drift_kinds"]
            print(f"[{size} seed {sd}] services={r['services']} drifts={r['n_drifts']} "
                  + " ".join(f"{n}:F1={prf(c)[2]:.2f}" for n, c in r["counts"].items()), flush=True)
        # oracle: best threshold picked on the test seeds themselves
        snap_best = max(SNAP_T, key=lambda t: st.mean(x[2] for x in agg[f"Snap@{t}"]))
        agg["Snapshot-oracle"] = agg[f"Snap@{snap_best}"]
        out.setdefault("snap_best_t", {})[size] = snap_best
        # calibrated: threshold picked on separate calibration seeds
        cal = defaultdict(list)
        for sd in range(101, 101 + max(3, args.seeds // 2)):
            rc = run_one(size, sd, 0.10, {"TraceDebt": {"k": 3, "m": 4, "min": 1}})
            for t in SNAP_T:
                cal[t].append(prf(rc["counts"][f"Snap@{t}"])[2])
        snap_cal = max(SNAP_T, key=lambda t: st.mean(cal[t]))
        agg["Snapshot-calibrated"] = agg[f"Snap@{snap_cal}"]
        out.setdefault("snap_cal_t", {})[size] = snap_cal
        out["rq1"][size] = {name: {"P": st.mean(x[0] for x in v), "R": st.mean(x[1] for x in v),
                                   "F1": st.mean(x[2] for x in v), "F1_sd": st.pstdev([x[2] for x in v]),
                                   "TP": sum(x[3] for x in v), "FP": sum(x[4] for x in v),
                                   "FN": sum(x[5] for x in v)}
                            for name, v in agg.items()}
        out["per_type"][size] = {k: v for k, v in pt.items()}
        out["rq3"][size] = {"spans": spans, "seconds": t, "spans_per_s": spans / t,
                            "delay_median": st.median(delays) if delays else None,
                            "delay_p90": sorted(delays)[int(0.9 * (len(delays) - 1))] if delays else None,
                            "drifts": n_dr, "never_flagged": never}
    out["drift_probs"] = probs_all
    kk = defaultdict(lambda: [0, 0])
    for kind, hit in kinds_all:
        kk[kind][0] += hit
        kk[kind][1] += 1
    out["ever_flagged_by_kind"] = {k: v for k, v in kk.items()}

    # RQ2a: k-of-4 sweep on M
    for k in [1, 2, 3, 4]:
        v = {"TraceDebt": {"k": k, "m": 4, "min": 1}}
        rows, delays = [], []
        for sd in seeds:
            r = run_one("M", sd, 0.10, v)
            rows.append(prf(r["counts"]["TraceDebt"])); delays += r["delays"]
        out["rq2_k"][k] = {"P": st.mean(x[0] for x in rows), "R": st.mean(x[1] for x in rows),
                           "F1": st.mean(x[2] for x in rows),
                           "delay_median": st.median(delays) if delays else None}
        print("k", k, out["rq2_k"][k], flush=True)

    # RQ2b: sampling sweep on M
    for s in ([0.05, 0.1] if args.quick else [0.01, 0.05, 0.10, 0.25]):
        v = {"TraceDebt": {"k": 3, "m": 4, "min": 1}}
        rows = defaultdict(list)
        for sd in seeds:
            r = run_one("M", sd, s, v)
            for name, c in r["counts"].items():
                rows[name].append(prf(c))
        out["rq2_sampling"][s] = {n: {"P": st.mean(x[0] for x in v_), "R": st.mean(x[1] for x in v_),
                                      "F1": st.mean(x[2] for x in v_)} for n, v_ in rows.items()}
        print("sampling", s, {n: round(x["F1"], 3) for n, x in out["rq2_sampling"][s].items()}, flush=True)

    # Stress test: benign undeclared calls that recur in every window (M)
    out["stress"] = {}
    for rate in [0.001, 0.005]:
        v = {"TraceDebt": {"k": 3, "m": 4, "min": 1}, "TD-floor2": {"k": 3, "m": 4, "min": 2},
             "TD-floor5": {"k": 3, "m": 4, "min": 5}}
        rows = defaultdict(list)
        for sd in seeds:
            r = run_one("M", sd, 0.10, v, noise=NoiseModel(recurring=3, recurring_rate=rate))
            for name, c in r["counts"].items():
                rows[name].append(prf(c) + tuple(c))
        out["stress"][rate] = {n: {"P": st.mean(x[0] for x in v_), "R": st.mean(x[1] for x in v_),
                                   "F1": st.mean(x[2] for x in v_), "FP": sum(x[4] for x in v_)}
                               for n, v_ in rows.items()
                               if n in ("TraceDebt", "TD-floor2", "TD-floor5", "Snapshot", "Snap@5")}
        print("stress", rate, {n: round(x["F1"], 3) for n, x in out["stress"][rate].items()}, flush=True)

    # Spec bootstrap accuracy (release 0, all sizes)
    out["bootstrap"] = {}
    for samp in (0.10, 0.01):
        bs = defaultdict(list)
        for size in sizes:
            for sd in seeds:
                b = bootstrap_eval(size, sd, samp)
                for k, val in b.items():
                    bs[(size, k)].append(val)
        out["bootstrap"][samp] = {size: {k: st.mean(bs[(size, k)]) for k in
                                         ["layer_acc", "owner_acc", "dep_P", "dep_R", "notes"]}
                                  for size in sizes}
    print("bootstrap", out["bootstrap"], flush=True)

    with open(os.path.join(args.out, "results.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)


if __name__ == "__main__":
    main()
