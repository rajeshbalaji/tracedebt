"""Command line entry point.

    tracedebt init w1.json w2.json ... [--out spec.draft.yaml]
              [--ignore-service mesh-probe] [--ignore-op /health]
    tracedebt analyze spec.yaml w1.json w2.json ... [--k 3 --m 4]
              [--min-traces 1] [--state state.json] [--md report.md] [--json report.json]
              [--dot drift.dot] [--fail-on 4]

Windows are processed in the order given; each file is one window and
its file stem is used as the window label.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time

from . import bootstrap
from .graph import build
from .ingest import load_file
from .pipeline import Analyzer
from .report import to_dot, to_markdown, write_json
from .spec import Spec


def _expand(paths):
    """Expand wildcards ourselves: Windows shells pass `w*.jsonl.gz` through
    unchanged. Existing paths are kept as given; patterns are sorted."""
    out = []
    for p in paths:
        if os.path.exists(p):
            out.append(p)
        elif any(c in p for c in "*?["):
            hits = sorted(glob.glob(p))
            if not hits:
                raise SystemExit(f"tracedebt: no files match {p!r}")
            out.extend(hits)
        else:
            raise SystemExit(f"tracedebt: file not found: {p!r}")
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tracedebt")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="check trace windows against a spec")
    a.add_argument("spec")
    a.add_argument("windows", nargs="+")
    a.add_argument("--k", type=int, default=3, help="windows needed to confirm (k of m)")
    a.add_argument("--m", type=int, default=4)
    a.add_argument("--close-after", type=int, default=3)
    a.add_argument("--min-traces", type=int, default=1)
    a.add_argument("--state", help="JSON file with the tracker state: read if it exists, "
                   "written after the run, so a nightly job only feeds the new windows")
    a.add_argument("--md")
    a.add_argument("--json")
    a.add_argument("--dot")
    a.add_argument("--dot-focus", action="store_true",
                   help="draw only drifted edges and their immediate context")
    a.add_argument("--fail-on", type=int, default=0,
                   help="exit 1 if an active finding has severity >= N")
    i = sub.add_parser("init", help="draft a spec from baseline trace windows")
    i.add_argument("windows", nargs="+")
    i.add_argument("--out")
    i.add_argument("--min-share", type=float, default=0.5,
                   help="share of windows a call must appear in to be declared")
    i.add_argument("--base", help="partial spec whose aliases/ignore are reused")
    i.add_argument("--ignore-service", action="append", default=[])
    i.add_argument("--ignore-op", action="append", default=[])
    args = p.parse_args(argv)
    args.windows = _expand(args.windows)

    if args.cmd == "init":
        import yaml
        bd = yaml.safe_load(open(args.base)) if args.base else {}
        ign = dict(bd.get("ignore") or {})
        ign["services"] = list(ign.get("services") or []) + args.ignore_service
        ign["operations"] = list(ign.get("operations") or []) + args.ignore_op
        args.ignore_service, args.ignore_op = ign["services"], ign["operations"]
        base = Spec.from_dict({"layers": bootstrap.LAYERS, "aliases": bd.get("aliases") or {},
                               "ignore": ign})
        graphs = [build(os.path.basename(w), load_file(w), base, keep_trace_edges=False)
                  for w in args.windows]
        spec, notes = bootstrap.draft(graphs, args.min_share)
        spec["ignore"] = {"services": args.ignore_service, "operations": args.ignore_op}
        if base.aliases:
            spec["aliases"] = dict(base.aliases)
        text = bootstrap.to_yaml(spec, notes)
        if args.out:
            open(args.out, "w", encoding="utf-8").write(text)
        else:
            sys.stdout.write(text)
        print(f"[tracedebt] draft spec: {len(spec['services'])} services, "
              f"{len(notes)} review notes", file=sys.stderr)
        return 0

    spec = Spec.load(args.spec)
    an = Analyzer(spec, k=args.k, m=args.m, min_traces=args.min_traces,
                  close_after=args.close_after)
    if args.state and os.path.exists(args.state):
        an.load_state(args.state)
    t0, spans = time.perf_counter(), 0
    for path in args.windows:
        traces = load_file(path)
        spans += sum(len(t) for t in traces)
        an.feed(os.path.basename(path).split(".")[0], traces)
    res = an.finish()
    if args.state:
        an.save_state(args.state)
    dt = time.perf_counter() - t0
    md = to_markdown(res)
    if args.md:
        open(args.md, "w", encoding="utf-8").write(md)
    else:
        sys.stdout.write(md)
    if args.json:
        write_json(res, args.json)
    if args.dot:
        open(args.dot, "w", encoding="utf-8").write(to_dot(res, focus=args.dot_focus))
    print(f"[tracedebt] {len(args.windows)} windows, {spans} spans in {dt:.2f}s; "
          f"{len(res.active())} active findings", file=sys.stderr)
    if args.fail_on and any(f.severity >= args.fail_on for f in res.active()):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
