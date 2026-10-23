"""Show the trace behind a finding.

Every finding in a report carries an evidence trace ID. `tracedebt trace`
finds that trace in the window files, prints its span tree and, given the
specification, marks the span that produced each rule violation, so a reader
can see which request, which operation and which call broke the agreement.

    tracedebt trace <trace-id> w*.jsonl.gz --spec spec.yaml [--path]

The output is plain ASCII so it can be redirected to a file on any platform.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .graph import build, walk
from .ingest import Span, Trace, load_file
from .rules import check
from .spec import Spec


def find(trace_id: str, paths: Sequence[str]) -> Tuple[Optional[Trace], List[str]]:
    """All spans of the trace whose ID equals `trace_id` (or starts with it, for 8+
    characters), gathered from every file given. A trace that was still running
    when a window closed has its spans in two files; both halves are needed."""
    tid = trace_id.strip().strip("`").lower()
    spans: Trace = []
    sources: List[str] = []
    for p in paths:
        for tr in load_file(p):
            t = (tr[0].trace_id or "").lower()
            if t == tid or (len(tid) >= 8 and t.startswith(tid)):
                spans.extend(tr)
                if p not in sources:
                    sources.append(p)
    seen, merged = set(), []
    for s in spans:                      # the same span exported twice is shown once
        if s.span_id not in seen:
            seen.add(s.span_id)
            merged.append(s)
    return (merged or None), sources


def _marks(tr: Trace, spec: Spec) -> Dict[str, List[str]]:
    """span_id -> labels of the violations that span produced."""
    g = build("trace", [tr], spec)
    bad: Dict[Tuple[str, str], Set[str]] = {}
    for c in check(g, spec, 1):
        for e in c.edges:
            bad.setdefault(e, set()).add(c.rule)
    out: Dict[str, List[str]] = {}
    for s, e, _inferred in walk(tr, spec).hits:
        if e in bad:
            out.setdefault(s.span_id, []).append(f"{' + '.join(sorted(bad[e]))} ({e[0]} -> {e[1]})")
    return out


def _label(s: Span, spec: Optional[Spec], ignored: Set[str], targets: Dict[str, str]) -> str:
    parts = [s.service, s.kind or "-", s.operation or "-"]
    if s.span_id in targets:
        parts.append("-> " + targets[s.span_id])
    parts.append(f"{s.duration_us / 1000:.1f} ms")
    if s.span_id in ignored:
        parts.append("(ignored)")
    return "  ".join(parts)


def render(tr: Trace, spec: Optional[Spec] = None, path_only: bool = False,
           sources: Sequence[str] = ()) -> List[str]:
    ids = {s.span_id for s in tr}
    kids: Dict[Optional[str], List[Span]] = {}
    for s in tr:
        parent = s.parent_id if s.parent_id in ids else None
        kids.setdefault(parent, []).append(s)
    marks = _marks(tr, spec) if spec else {}
    ignored: Set[str] = set()
    targets: Dict[str, str] = {}
    if spec:
        w = walk(tr, spec)
        ignored = w.ignored_ids
        for s, e, _inf in w.hits:
            if e[1] != s.service and e[0] != e[1]:
                if s.db_name or _inf:
                    targets[s.span_id] = e[1]
    keep: Optional[Set[str]] = None
    if path_only and marks:
        by_id = {s.span_id: s for s in tr}
        keep = set()
        for sid in marks:
            cur = by_id.get(sid)
            while cur is not None and cur.span_id not in keep:
                keep.add(cur.span_id)
                cur = by_id.get(cur.parent_id) if cur.parent_id else None
    services = sorted({s.service for s in tr})
    where = ", ".join(os.path.basename(x) for x in sources)
    head = (f"trace {tr[0].trace_id}  ({len(tr)} spans from {len(services)} "
            f"service{'s' if len(services) != 1 else ''}; in {where})")
    lines = [head]

    def emit(s: Span, prefix: str, last: bool, top: bool):
        conn = "" if top else ("`- " if last else "|- ")
        lines.append(prefix + conn + _label(s, spec, ignored, targets))
        children = [c for c in kids.get(s.span_id, []) if keep is None or c.span_id in keep]
        nxt = prefix + ("" if top else ("   " if last else "|  "))
        for m in marks.get(s.span_id, []):
            lines.append(nxt + ("|  " if children else "   ") + "<== " + m)
        for i, c in enumerate(children):
            emit(c, nxt, i == len(children) - 1, False)

    roots = [r for r in kids.get(None, []) if keep is None or r.span_id in keep]
    for r in roots:
        emit(r, "", True, True)
    if keep is not None:
        lines.append(f"({len(tr) - len(keep)} other spans hidden; drop --path to see them)")
    if spec and not marks:
        lines.append("(no rule violation in this trace under the given spec)")
    return lines
