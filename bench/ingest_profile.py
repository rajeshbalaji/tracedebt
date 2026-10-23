"""Where does the time go end to end, and how much memory does a window need?

    python -m bench.ingest_profile <windows-dir> [--mode perwindow|merged]

perwindow  load one window file, analyse it, drop it (what the CLI does)
merged     hold every window of the directory as one big window (worst case)
Reports parse time, analysis time, spans per second for each, and the peak
resident set size of the process.
"""
import argparse
import glob
import json
import os
import resource
import time

from tracedebt.graph import build
from tracedebt.ingest import load_file
from tracedebt.pipeline import Analyzer
from tracedebt.spec import Spec

SPEC = os.path.join(os.path.dirname(__file__), "..", "examples", "online-boutique", "spec.yaml")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("windows")
    ap.add_argument("--mode", default="perwindow", choices=["perwindow", "merged"])
    a = ap.parse_args()
    fs = sorted(glob.glob(os.path.join(a.windows, "w*.jsonl*")))
    spec = Spec.load(SPEC)
    spans = t_load = t_an = 0.0
    if a.mode == "perwindow":
        an = Analyzer(spec)
        for f in fs:
            t = time.perf_counter(); tr = load_file(f); t_load += time.perf_counter() - t
            spans += sum(len(x) for x in tr)
            t = time.perf_counter(); an.feed(os.path.basename(f).split(".")[0], tr); t_an += time.perf_counter() - t
        an.finish()
    else:
        allt = []
        t = time.perf_counter()
        for f in fs:
            allt += load_file(f)
        t_load = time.perf_counter() - t
        spans = sum(len(x) for x in allt)
        t = time.perf_counter()
        an = Analyzer(spec, keep_trace_edges=False)
        an.feed("all", allt)
        t_an = time.perf_counter() - t
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    out = {"mode": a.mode, "files": len(fs), "spans": int(spans), "parse_s": round(t_load, 2),
           "analyse_s": round(t_an, 2), "end_to_end_spans_per_s": round(spans / (t_load + t_an)),
           "analysis_only_spans_per_s": round(spans / t_an), "peak_rss_mb": round(rss),
           "bytes_per_span": round(rss * 1024 * 1024 / spans)}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
