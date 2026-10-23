"""Analyse the Online Boutique case-study traces.

    python -m bench.case_online_boutique <windows-dir> [--out results/case]

Expects w00.jsonl .. wNN.jsonl (OTLP/JSON lines) and manifest.json from
the case-study driver. Runs TraceDebt with the hand-written spec in
examples/online-boutique/spec.yaml on all traces and on a 10% head sample
(keep a trace iff hash(trace id) < 10%, which is what a parent-based head
sampler does), once more without the spec's aliases to show what they fix,
and drafts a spec with `init` from the clean phase for comparison.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from collections import Counter

import yaml

from tracedebt.bootstrap import LAYERS, draft
from tracedebt.graph import build
from tracedebt.ingest import load_file
from tracedebt.pipeline import Analyzer
from tracedebt.report import to_dot, to_markdown, write_json
from tracedebt.spec import DB_PREFIX, Spec

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "..", "examples", "online-boutique", "spec.yaml")

# ground truth: the seeded drifts and the phase each is present in
DRIFTS = {
    "drift-1": {"edges": [("recommendationservice", "db:redis-cart")], "phases": ["v1", "v2", "v3"]},
    "drift-2": {"edges": [("emailservice", "productcatalogservice")], "phases": ["v2", "v3", "v4"]},
    "drift-3": {"edges": [("productcatalogservice", "recommendationservice")], "phases": ["v3", "v4"]},
}


def sampled(traces, rate):
    if rate >= 1:
        return traces
    lim = int(rate * 2**32)
    return [t for t in traces
            if int(hashlib.sha1(t[0].trace_id.encode()).hexdigest()[:8], 16) < lim]


def run(spec, windows, manifest, rate=1.0, label="full"):
    an = Analyzer(spec)
    timeline = []
    per_window = []
    for (w, traces), m in zip(windows, manifest):
        tr = sampled(traces, rate)
        g = an.feed(w, tr)
        act = an.result.tracker.active()
        d = an.result.debt[-1]
        timeline.append({"window": w, "phase": m["phase"], "severity": d.severity,
                         "exposure": d.exposure, "trace_share": d.trace_share,
                         "findings": sorted(f"{f.rule}:{'|'.join(a + '>' + b for a, b in f.edges)}"
                                            for f in act)})
        per_window.append({"window": w, "traces": g.n_traces,
                           "spans": sum(len(t) for t in tr), "edges": len(g.edges),
                           "inferred_edges": sum(1 for st in g.edges.values() if st.inferred == st.calls),
                           "dropped_spans": g.dropped_spans})
    res = an.finish()
    return res, timeline, per_window


def score(res, manifest):
    """Detection delay per seeded drift and findings that are not seeded drifts."""
    start = {}
    for i, m in enumerate(manifest):
        start.setdefault(m["phase"], i)
    out = {}
    for name, d in DRIFTS.items():
        first_phase = d["phases"][0]
        hits = sorted((f for f in res.tracker.findings.values()
                       if set(d["edges"]) & set(f.edges) and f.confirmed_at),
                      key=lambda f: (int(f.confirmed_at[1:]), f.rule))
        if not hits:
            out[name] = {"confirmed": False}
            continue
        f = hits[0]
        ci = int(f.confirmed_at[1:])
        out[name] = {"confirmed": True, "rules": sorted({h.rule for h in hits}),
                     "confirmed_at": f.confirmed_at,
                     "windows_after_deploy": ci - start[first_phase] + 1,
                     "minutes_after_deploy": round(manifest[ci]["end"] - manifest[start[first_phase]]["start"]) / 60,
                     "resolved_at": f.resolved_at, "exposure": f.last_exposure,
                     "evidence": f.evidence_trace}
    seeded = {e for d in DRIFTS.values() for e in d["edges"]}
    others = []
    for f in res.tracker.findings.values():
        if f.confirmed_at and not (set(f.edges) & seeded):
            others.append({"rule": f.rule, "edges": f.edges, "confirmed_at": f.confirmed_at,
                           "resolved_at": f.resolved_at})
    candidates_only = [f for f in res.tracker.findings.values() if not f.confirmed_at]
    return out, others, [{"rule": f.rule, "edges": f.edges, "first_seen": f.first_seen}
                         for f in candidates_only]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("windows")
    ap.add_argument("--out", default="results/case")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    manifest = json.load(open(os.path.join(a.windows, "manifest.json")))
    def path(w):
        p = os.path.join(a.windows, w + ".jsonl")
        return p if os.path.exists(p) else p + ".gz"
    windows = [(m["window"], load_file(path(m["window"]))) for m in manifest]
    spec = Spec.load(SPEC)
    summary = {"windows": len(windows),
               "window_seconds": round(sum(m["end"] - m["start"] for m in manifest) / len(manifest), 1),
               "requests": manifest[-1]["load"]}

    # raw telemetry facts
    names, kinds = Counter(), Counter()
    for _, traces in windows:
        for t in traces:
            for s in t:
                names[s.service] += 1
                kinds[s.kind] += 1
    summary["service_names_in_telemetry"] = dict(names)
    summary["span_kinds"] = {str(k): v for k, v in kinds.items()}

    runs = {}
    for label, rate, sp in [("full", 1.0, spec), ("head10", 0.10, spec)]:
        res, tl, pw = run(sp, windows, manifest, rate, label)
        sc, others, cands = score(res, manifest)
        runs[label] = {"drifts": sc, "other_confirmed": others, "unconfirmed_candidates": cands,
                       "timeline": tl, "per_window": pw,
                       "coverage": {k: v for k, v in res.coverage.items()},
                       "inferred_edges": res.inferred_edges, "stale": res.stale}
        open(os.path.join(a.out, f"report_{label}.md"), "w").write(to_markdown(res))
        write_json(res, os.path.join(a.out, f"report_{label}.json"))
        if label == "full":
            open(os.path.join(a.out, "drift_full.dot"), "w").write(to_dot(res, focus=True))

    # same traces, spec without aliases: what breaks
    d = yaml.safe_load(open(SPEC))
    d.pop("aliases", None)
    res, tl, pw = run(Spec.from_dict(d), windows, manifest, 1.0, "noalias")
    sc, others, cands = score(res, manifest)
    runs["noalias"] = {"drifts": sc, "other_confirmed": others,
                       "baseline_findings_v0": [f for f in tl[5]["findings"]]}

    # init on the clean phase vs the hand-written spec
    base = Spec.from_dict({"layers": LAYERS, "aliases": spec.aliases,
                           "ignore": {"operations": sorted(spec.ignore_operations)}})
    v0 = [build(w, tr, base, keep_trace_edges=False)
          for (w, tr), m in zip(windows, manifest) if m["phase"] == "v0"]
    dr, notes = draft(v0)
    dspec = Spec.from_dict(dict(dr, aliases=spec.aliases))
    hand = {e for e in spec.declared if not e[1].startswith(DB_PREFIX)}
    got = {e for e in dspec.declared if not e[1].startswith(DB_PREFIX)}
    summary["init_vs_hand"] = {
        "hand_deps": len(hand), "draft_deps": len(got),
        "agree": len(hand & got), "missing": sorted(hand - got), "extra": sorted(got - hand),
        "owners_hand": spec.datastores, "owners_draft": dspec.datastores,
        "review_notes": notes}
    yaml.safe_dump(dr, open(os.path.join(a.out, "init_draft.yaml"), "w"), sort_keys=False)

    summary["runs"] = runs
    json.dump(summary, open(os.path.join(a.out, "case.json"), "w"), indent=1, default=list)
    for label in ("full", "head10", "noalias"):
        print(label, json.dumps(runs[label]["drifts"], default=str))
        print("   other confirmed:", runs[label]["other_confirmed"])
    print("init:", {k: v for k, v in summary["init_vs_hand"].items() if k != "review_notes"})


if __name__ == "__main__":
    main()
