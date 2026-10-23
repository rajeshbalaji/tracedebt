"""How much of the benchmark's precision is a statement about its spec-lag setting?

Re-runs TraceDebt and the calibrated-threshold Snapshot on topology M with the
share of legitimate new calls whose specification entry arrives a release late
set to 0, 0.15, 0.3 (the default) and 0.6.

    python -m bench.sensitivity --out results/sensitivity.json
"""
import argparse
import json
import statistics as st
from collections import defaultdict

from bench.run_eval import prf, run_one


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/sensitivity.json")
    ap.add_argument("--seeds", type=int, default=10)
    a = ap.parse_args()
    out = {}
    for lag in [0.0, 0.15, 0.3, 0.6]:
        rows = defaultdict(list)
        for sd in range(1, a.seeds + 1):
            r = run_one("M", sd, 0.10, {"TraceDebt": {"k": 3, "m": 4, "min": 1}}, spec_lag=lag)
            for name in ("TraceDebt", "Snap@3"):
                c = r["counts"][name]
                rows[name].append(prf(c) + tuple(c))
        out[lag] = {n: {"P": st.mean(x[0] for x in v), "R": st.mean(x[1] for x in v),
                        "F1": st.mean(x[2] for x in v), "FP": sum(x[4] for x in v)}
                    for n, v in rows.items()}
        print(lag, {n: {k: round(x, 2) for k, x in d.items()} for n, d in out[lag].items()}, flush=True)
    json.dump(out, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
