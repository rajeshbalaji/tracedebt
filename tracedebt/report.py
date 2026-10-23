"""Report writers: Markdown (for humans and PR comments), JSON (for CI and
dashboards) and Graphviz DOT (for the drift map)."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Dict

from .coverage import weakest
from .pipeline import Result
from .spec import DB_PREFIX, TOPIC_PREFIX

_LABEL = {
    "FOREIGN_DATA": "reads/writes a datastore owned by another service",
    "FOREIGN_TOPIC": "publishes to a topic owned by another service",
    "LAYER_UPWARD": "calls a higher layer",
    "LAYER_SKIP": "bypasses an intermediate layer",
    "CYCLE": "runtime dependency cycle",
    "UNKNOWN_SERVICE": "talks to a service or topic missing from the spec",
    "UNDECLARED_XCTX": "undeclared call across bounded contexts",
    "UNDECLARED": "undeclared call",
}


def to_json(res: Result) -> Dict:
    return {
        "system": res.spec.system,
        "windows": res.tracker.windows,
        "active": [_f(f) for f in res.active()],
        "resolved": [_f(f) for f in res.tracker.findings.values() if f.resolved_at],
        "indicators": [asdict(d) for d in res.debt],
        "stale_declarations": [list(e) for e in res.stale],
        "inferred_edges": [list(e) for e in res.inferred_edges],
        "coverage": {s: {"traces_per_window": v["traces_per_window"],
                         "p_min": round(v["p_min"], 5)} for s, v in sorted(res.coverage.items())},
    }


def _f(f):
    return {"rule": f.rule, "key": list(f.key), "edges": [list(e) for e in f.edges],
            "severity": f.severity, "first_seen": f.first_seen,
            "confirmed_at": f.confirmed_at, "resolved_at": f.resolved_at,
            "exposure": round(f.last_exposure, 4),
            "latency_share": round(f.last_latency_share, 4),
            "evidence_trace": f.evidence_trace, "triage": "open"}


def to_markdown(res: Result) -> str:
    act = res.active()
    d = res.debt[-1] if res.debt else None
    lines = [f"# TraceDebt report: {res.spec.system}", ""]
    lines.append(f"Windows analysed: {len(res.tracker.windows)} "
                 f"({res.tracker.windows[0]} .. {res.tracker.windows[-1]})" if res.tracker.windows else "")
    if d:
        lines += ["", f"**Drift indicators** severity {d.severity:.0f}, exposure {d.exposure:.2f}; "
                      f"{d.trace_share*100:.1f}% of traces and {d.latency_share*100:.1f}% of "
                      f"latency touch drifted edges. (Prioritization aids, not cost estimates.)", ""]
    lines += ["## Active drift", "",
              "| Sev | Rule | Where | Exposure | Since | Evidence |",
              "|---:|---|---|---:|---|---|"]
    for f in act:
        where = ", ".join(f"{a} → {b}" for a, b in f.edges)
        lines.append(f"| {f.severity} | {f.rule} ({_LABEL[f.rule]}) | {where} | "
                     f"{f.last_exposure*100:.2f}% | {f.confirmed_at} | `{f.evidence_trace}` |")
    if not act:
        lines.append("| – | none | | | | |")
    res_f = [f for f in res.tracker.findings.values() if f.resolved_at]
    if res_f:
        lines += ["", "## Resolved", ""]
        for f in res_f:
            lines.append(f"- {f.rule} {', '.join(f'{a} → {b}' for a, b in f.edges)} "
                         f"(confirmed {f.confirmed_at}, gone {f.resolved_at})")
    if res.stale:
        lines += ["", "## Declared but not observed recently", ""]
        lines += [f"- {a} → {b}" for a, b in res.stale]
    if res.inferred_edges:
        lines += ["", "## Seen from the caller only", "",
                  "These calls appear only as client spans: the callee did not continue the trace "
                  "(it is not instrumented, or context propagation is broken), so it was inferred "
                  "from the rpc service or peer address via `aliases`.", ""]
        lines += [f"- {a} → {b}" for a, b in res.inferred_edges]
    weak = weakest(res.coverage)
    if weak:
        lines += ["", "## Coverage", "",
                  f"With current trace volume, a drifted call that occurs in fewer than the "
                  f"share of a service's traces shown below would probably not be confirmed "
                  f"(90% target, k={res.tracker.k} of m={res.tracker.m}). A quiet report is "
                  f"not evidence of conformance below these rates.", "",
                  "| Service | Traces/window | Rarest reliably confirmed |", "|---|---:|---:|"]
        for s_ in weak[:10]:
            v = res.coverage[s_]
            lines.append(f"| {s_} | {v['traces_per_window']:g} | {v['p_min']*100:.1f}% |")
    if act:
        lines += ["", "## Triage", "",
                  "Each finding usually ends in one of three ways: (1) a genuine violation, "
                  "fixed by a code change; (2) an intended dependency the spec has not caught up "
                  "with, fixed by declaring it; (3) unclear, for example a fallback path or a "
                  "batch job, which needs someone to look at the evidence trace before deciding. "
                  "Use `ignore` only for infrastructure traffic, not to silence (3).", "",
                  "To see the request behind a finding: `tracedebt trace <evidence id> "
                  "<window files> --spec <spec> --path` prints the call path with the "
                  "offending span marked."]
    return "\n".join(lines) + "\n"


def to_dot(res: Result, focus: bool = False) -> str:
    """Drift map. With focus=True only drifted edges and the ordinary edges
    between the nodes they touch are drawn, which keeps large systems readable."""
    g = res.last_graph
    bad = {}
    rule_of = {}
    for f in res.active():
        for e in f.edges:
            if f.severity >= bad.get(e, 0):
                bad[e] = f.severity
                rule_of[e] = f.rule
    edges = dict(g.edges)
    if focus and bad:
        hot = {n for e in bad for n in e}
        # drifted edges plus the ordinary edges among the services involved
        edges = {e: st for e, st in g.edges.items() if e in bad or (e[0] in hot and e[1] in hot)
                 or (e[1] in hot and e[1].startswith((DB_PREFIX, TOPIC_PREFIX)))}
        nodes = {n for e in edges for n in e}
        g = type(g)(g.label, nodes=nodes, edges=edges)
    out = ["digraph tracedebt {", "  rankdir=TB; node [shape=box, fontsize=10];"]
    topics = [n for n in sorted(g.nodes) if n.startswith(TOPIC_PREFIX)]
    if topics:
        out.append('  subgraph "cluster_broker" { label="broker"; style=dashed; ' +
                   " ".join(f'"{n}" [shape=parallelogram];' for n in topics) + " }")
    for layer in res.spec.layers:
        members = [n for n in sorted(g.nodes) if res.spec.layer_index(n) == res.spec.layers.index(layer)]
        if not members:
            continue
        if not members:
            continue
        out.append(f'  subgraph "cluster_{layer}" {{ label="{layer}"; ' +
                   " ".join(f'"{n}"{" [shape=cylinder]" if n.startswith(DB_PREFIX) else ""};' for n in members) + " }")
    for (a, b), st in sorted(g.edges.items()):
        if (a, b) in bad:
            out.append(f'  "{a}" -> "{b}" [color=red, penwidth=2, fontcolor=red, '
                       f'fontsize=8, label="{rule_of[(a, b)]}"];')
        else:
            out.append(f'  "{a}" -> "{b}" [color=gray60];')
    out.append("}")
    return "\n".join(out) + "\n"


def write_json(res: Result, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(to_json(res), fh, indent=2)
