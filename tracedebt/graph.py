"""Runtime interaction graph for one observation window.

Edges come from three sources:
  * a span whose nearest kept ancestor runs in another service
    (the normal instrumented caller -> callee case);
  * datastore and messaging spans (service -> db:<x>, service -> topic:<t>,
    topic:<t> -> consumer);
  * client spans that have no child span in another service. This is how an
    uninstrumented callee shows up in real traces: the caller records the
    call, nobody records the other side. The callee is inferred from the
    span's rpc service, peer.service or host:port, mapped through the spec's
    aliases. Such edges are counted separately so reports can say how much
    of the graph is inferred rather than observed from both ends.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .ingest import Trace
from .spec import DB_PREFIX, TOPIC_PREFIX, Spec

Edge = Tuple[str, str]


@dataclass
class EdgeStat:
    calls: int = 0
    traces: int = 0            # number of traces containing the edge
    duration_us: int = 0       # callee time attributable to the edge
    sample_trace: Optional[str] = None
    inferred: int = 0          # calls known only from the caller's client span


@dataclass
class WindowGraph:
    label: str
    n_traces: int = 0
    total_duration_us: int = 0
    edges: Dict[Edge, EdgeStat] = field(default_factory=dict)
    nodes: Set[str] = field(default_factory=set)
    roots: Dict[str, int] = field(default_factory=dict)        # service -> root spans
    svc_traces: Dict[str, int] = field(default_factory=dict)   # service -> traces it appears in
    trace_edges: List[Set[Edge]] = field(default_factory=list)
    dropped_spans: int = 0                                      # ignored by the spec

    def support(self, e: Edge) -> int:
        st = self.edges.get(e)
        return st.traces if st else 0


@dataclass
class TraceWalk:
    """What one trace contributes: which span produced which edge."""
    hits: List[Tuple[object, Edge, bool]] = field(default_factory=list)  # (span, edge, inferred)
    services: Set[str] = field(default_factory=set)
    roots: List[str] = field(default_factory=list)
    root_dur: int = 0
    dropped: int = 0
    ignored_ids: Set[str] = field(default_factory=set)
    empty: bool = False


def walk(tr: Trace, spec: Spec) -> TraceWalk:
    """Edges of a single trace, each tied to the span that produced it. Used by
    `build` for whole windows and by `tracedebt trace` to point at the span
    behind a finding."""
    w = TraceWalk()
    canon = spec.canon
    kept_spans = []
    dropped: Set[str] = set()
    ign_s, ign_o = spec.ignored_service, spec.ignored_operation
    for s in tr:
        if ign_s(s.service):
            dropped.add(s.span_id)
            w.dropped += 1
            w.ignored_ids.add(s.span_id)
        elif ign_o(s.operation):
            w.dropped += 1
            w.ignored_ids.add(s.span_id)
        else:
            kept_spans.append(s)
    if not kept_spans:        # trace made only of ignored spans (health probes, exports)
        w.empty = True
        return w
    by_id = {s.span_id: s for s in tr}
    aliases = spec.aliases
    svc_of = {s.span_id: aliases.get(s.service, s.service) for s in tr}
    remote_child: Set[str] = set()
    for s in kept_spans:
        if s.parent_id and s.parent_id in svc_of and svc_of[s.parent_id] != svc_of[s.span_id]:
            remote_child.add(s.parent_id)
    for s in kept_spans:
        svc = svc_of[s.span_id]
        w.services.add(svc)
        if s.parent_id is None:
            w.root_dur += s.duration_us
            w.roots.append(svc)
        # nearest ancestor that is not an ignored service (sidecars, proxies)
        p = by_id.get(s.parent_id) if s.parent_id else None
        while p is not None and p.span_id in dropped:
            p = by_id.get(p.parent_id) if p.parent_id else None
        psvc = svc_of[p.span_id] if p is not None else None
        if s.msg_dest and s.kind in ("producer", "consumer"):
            topic = TOPIC_PREFIX + canon(s.msg_dest)
            if s.kind == "producer":
                w.hits.append((s, (svc, topic), False))
            else:
                # the consumer is reached through the broker, not directly
                # from the producing service
                w.hits.append((s, (topic, svc), False))
        elif psvc is not None and psvc != svc and not (p.kind == "producer" and p.msg_dest):
            w.hits.append((s, (psvc, svc), False))
        if s.db_name:
            w.hits.append((s, (svc, DB_PREFIX + canon(s.db_name)), False))
        elif (s.kind == "client" and s.span_id not in remote_child and not s.msg_dest):
            callee = _infer_callee(s, spec)
            if callee and callee != svc:
                w.hits.append((s, (svc, callee), True))
    return w


def build(label: str, traces: Iterable[Trace], spec: Spec,
          keep_trace_edges: bool = True) -> WindowGraph:
    g = WindowGraph(label)
    for tr in traces:
        w = walk(tr, spec)
        g.dropped_spans += w.dropped
        if w.empty:
            continue
        for r in w.roots:
            g.roots[r] = g.roots.get(r, 0) + 1
        g.nodes |= w.services
        seen: Set[Edge] = set()
        for s, e, inferred in w.hits:
            for n in e:
                if n.startswith(DB_PREFIX) or n.startswith(TOPIC_PREFIX) or inferred:
                    g.nodes.add(n)
            _add(g, e, s, seen, inferred=inferred)
        g.n_traces += 1
        g.total_duration_us += w.root_dur
        for sv in w.services:
            g.svc_traces[sv] = g.svc_traces.get(sv, 0) + 1
        for e in seen:
            g.edges[e].traces += 1
        if keep_trace_edges:
            g.trace_edges.append(seen)
    return g


def _infer_callee(s, spec: Spec) -> Optional[str]:
    """Best guess at the service on the other end of an unanswered client span."""
    for raw in (s.rpc_service, s.peer):
        if raw and raw in spec.aliases:
            return spec.aliases[raw]
    for raw in (s.rpc_service, s.peer):
        if raw and spec.is_known(raw):
            return raw
    # unresolved: keep the raw rpc service name so it surfaces as UNKNOWN_SERVICE
    return s.rpc_service or s.peer


def _add(g: WindowGraph, e: Edge, s, seen: Set[Edge], inferred: bool = False) -> None:
    st = g.edges.get(e)
    if st is None:
        st = g.edges[e] = EdgeStat(sample_trace=s.trace_id)
    st.calls += 1
    st.duration_us += s.duration_us
    if inferred:
        st.inferred += 1
    seen.add(e)
