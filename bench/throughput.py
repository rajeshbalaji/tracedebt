"""Throughput on an otherwise idle machine (the evaluation harness runs other
work in parallel, so its timing is not used for the paper).

    python -m bench.throughput [case-study-windows-dir]
"""
import glob
import json
import os
import platform
import sys
import time

from bench.run_eval import run_one
from tracedebt.cli import main as cli

out = {"python": platform.python_version(), "synthetic": {}}
for size in "SML":
    spans = secs = 0.0
    for sd in (1, 2, 3):
        r = run_one(size, sd, 0.10, {"TraceDebt": {"k": 3, "m": 4, "min": 1}})
        spans += r["spans"]
        secs += r["t_analysis"]
    out["synthetic"][size] = {"spans": spans, "seconds": round(secs, 2),
                              "spans_per_s": round(spans / secs)}
if len(sys.argv) > 1:
    fs = sorted(glob.glob(os.path.join(sys.argv[1], "w*.jsonl*")))
    t = time.time()
    cli(["analyze", os.path.join(os.path.dirname(__file__), "..", "examples",
                                 "online-boutique", "spec.yaml"), *fs, "--md", os.devnull])
    out["case_cli_seconds"] = round(time.time() - t, 1)
os.makedirs("results", exist_ok=True)
json.dump(out, open("results/throughput.json", "w"), indent=1)
print(json.dumps(out))
