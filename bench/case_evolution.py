"""Approved architectural change on Online Boutique.

    python -m bench.case_evolution <windows-dir> [--out results/case_evolution]

The run has three phases of six one-minute windows (manifest.json):
  v0  base,   e1  + evolve-1 (recommendationservice -> cartservice, approved),
  e2  + drift-2 (emailservice -> productcatalogservice, a violation).

The traces are analysed three times with the same tracker settings:
  updated   the specification gains the new dependency in the change that
            deploys it (it is swapped in at the first window of e1)
  late      the dependency is declared four windows after deployment
  never     the specification is not updated (spec lag that nobody closes)
"""
from __future__ import annotations

import argparse
import json
import os

from tracedebt.ingest import load_file
from tracedebt.pipeline import Analyzer
from tracedebt.spec import Spec

HERE = os.path.dirname(os.path.abspath(__file__))
OLD = os.path.join(HERE, "..", "examples", "online-boutique", "spec.yaml")
NEW = os.path.join(HERE, "..", "examples", "online-boutique", "spec_evolved.yaml")
APPROVED = ("recommendationservice", "cartservice")
VIOLATION = ("emailservice", "productcatalogservice")


def run(windows, manifest, switch_at):
    old, new = Spec.load(OLD), Spec.load(NEW)
    an = Analyzer(old)
    first_e1 = next(i for i, m in enumerate(manifest) if m["phase"] == "e1")
    swap = None if switch_at is None else first_e1 + switch_at
    series = []
    for i, ((w, traces), m) in enumerate(zip(windows, manifest)):
        if swap is not None and i == swap:
            an.set_spec(new)
        an.feed(w, traces)
        d = an.result.debt[-1]
        series.append({"window": w, "phase": m["phase"], "severity": d.severity,
                       "exposure": d.exposure,
                       "active": sorted(f"{f.rule}:{'|'.join(a + '>' + b for a, b in f.edges)}"
                                        for f in an.result.tracker.active())})
    res = an.finish()
    fs = []
    for f in res.tracker.findings.values():
        fs.append({"rule": f.rule, "edges": [list(e) for e in f.edges],
                   "first_seen": f.first_seen, "confirmed_at": f.confirmed_at,
                   "resolved_at": f.resolved_at, "exposure": round(f.last_exposure, 3)})
    return {"findings": sorted(fs, key=lambda x: (x["first_seen"], x["rule"])), "series": series,
            "swap_window": windows[swap][0] if swap is not None else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("windows")
    ap.add_argument("--out", default="results/case_evolution")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    manifest = json.load(open(os.path.join(a.windows, "manifest.json")))

    def path(w):
        p = os.path.join(a.windows, w + ".jsonl")
        return p if os.path.exists(p) else p + ".gz"
    windows = [(m["window"], load_file(path(m["window"]))) for m in manifest]
    out = {"windows": len(windows),
           "traces_per_window": round(sum(1 for _, t in windows for _ in t) / len(windows)),
           "runs": {}}
    for name, at in [("updated", 0), ("late", 4), ("never", None)]:
        out["runs"][name] = run(windows, manifest, at)
        print(f"== spec {name}" + (f" (swapped in at {out['runs'][name]['swap_window']})" if at is not None else ""))
        for f in out["runs"][name]["findings"]:
            print("  ", f["rule"], f["edges"], "seen", f["first_seen"], "confirmed", f["confirmed_at"],
                  "resolved", f["resolved_at"], "exp", f["exposure"])
    json.dump(out, open(os.path.join(a.out, "case_evolution.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
