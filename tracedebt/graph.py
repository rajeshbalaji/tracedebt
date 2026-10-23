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


def build(label: str, traces: Iterable[Trace], spec: Spec,
          keep_trace_edges: bool = True) -> WindowGraph:
    g = WindowGraph(label)
    canon = spec.canon
    for tr in traces:
        kept_spans = []
        dropped: Set[str] = set()
        ign_s, ign_o = spec.ignored_service, spec.ignored_operation
        for s in tr:
            if ign_s(s.service):
                dropped.add(s.span_id)
                g.dropped_spans += 1
            elif ign_o(s.operation):
                g.dropped_spans += 1
            else:
                kept_spans.append(s)
        if not kept_spans:        # trace made only of ignored spans (health probes, exports)
            continue
        by_id = {s.span_id: s for s in tr}
        aliases = spec.aliases
        svc_of = {s.span_id: aliases.get(s.service, s.service) for s in tr}
        remote_child: Set[str] = set()
        for s in kept_spans:
            if s.parent_id and s.parent_id in svc_of and svc_of[s.parent_id] != svc_of[s.span_id]:
                remote_child.add(s.parent_id)
        seen: Set[Edge] = set()
        services: Set[str] = set()
        root_dur = 0
        for s in kept_spans:
            svc = svc_of[s.span_id]
            services.add(svc)
            if s.parent_id is None or s.parent_id not in by_id:
                if s.parent_id is None:
                    root_dur += s.duration_us
                    g.roots[svc] = g.roots.get(svc, 0) + 1
            g.nodes.add(svc)
            # nearest ancestor that is not an ignored service (sidecars, proxies)
            p = by_id.get(s.parent_id) if s.parent_id else None
            while p is not None and p.span_id in dropped:
                p = by_id.get(p.parent_id) if p.parent_id else None
            psvc = svc_of[p.span_id] if p is not None else None
            if s.msg_dest and s.kind in ("producer", "consumer"):
                topic = TOPIC_PREFIX + canon(s.msg_dest)
                g.nodes.add(topic)
                if s.kind == "producer":
                    _add(g, (svc, topic), s, seen)
                else:
                    # the consumer is reached through the broker, not directly
                    # from the producing service
                    _add(g, (topic, svc), s, seen)
            elif psvc is not None and psvc != svc and not (p.kind == "producer" and p.msg_dest):
                _add(g, (psvc, svc), s, seen)
            if s.db_name:
                node = DB_PREFIX + canon(s.db_name)
                g.nodes.add(node)
                _add(g, (svc, node), s, seen)
            elif (s.kind == "client" and s.span_id not in remote_child and not s.msg_dest):
                callee = _infer_callee(s, spec)
                if callee and callee != svc:
                    g.nodes.add(callee)
                    _add(g, (svc, callee), s, seen, inferred=True)
        g.n_traces += 1
        g.total_duration_us += root_dur
        for sv in services:
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
