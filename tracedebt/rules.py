"""Drift rules.

Each rule looks at one window graph and the spec and returns candidate
findings. A candidate is not reported yet; the tracker in tracker.py only
promotes it once it has been seen often enough (see k-of-m persistence).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Set, Tuple

from .graph import Edge, WindowGraph
from .spec import DB_PREFIX, TOPIC_PREFIX, Spec

# Default severity weights used by the debt index. They are deliberately
# coarse; a spec can override any of them under `severity:`.
SEVERITY = {
    "FOREIGN_DATA": 5,      # reaching into another service's datastore
    "FOREIGN_TOPIC": 4,     # publishing into another service's event stream
    "LAYER_UPWARD": 4,      # lower layer calling back up
    "CYCLE": 4,             # runtime dependency cycle between services
    "LAYER_SKIP": 3,        # bypassing an intermediate layer
    "UNKNOWN_SERVICE": 3,   # service not in the spec at all
    "UNDECLARED_XCTX": 3,   # undeclared call crossing a bounded context
    "UNDECLARED": 2,        # undeclared call inside a context
}


@dataclass(frozen=True)
class Candidate:
    rule: str
    key: Tuple[str, ...]           # identity of the finding across windows
    edges: Tuple[Edge, ...]        # runtime edges that carry it
    severity: int = 0


def _c(spec: Spec, rule: str, key, edges) -> Candidate:
    return Candidate(rule, key, edges, spec.severity.get(rule, SEVERITY[rule]))


def present_edges(g: WindowGraph, min_traces: int) -> Set[Edge]:
    return {e for e, st in g.edges.items() if st.traces >= min_traces}


def check(g: WindowGraph, spec: Spec, min_traces: int = 1) -> List[Candidate]:
    out: List[Candidate] = []
    edges = present_edges(g, min_traces)
    for (a, b) in sorted(edges):
        e = (a, b)
        for n in (a, b):
            if not spec.is_known(n):
                out.append(_c(spec, "UNKNOWN_SERVICE", ("UNKNOWN_SERVICE", n, a, b), (e,)))
        if not (spec.is_known(a) and spec.is_known(b)):
            continue
        declared = e in spec.declared
        if a.startswith(TOPIC_PREFIX) or b.startswith(TOPIC_PREFIX):
            out.extend(_messaging(spec, e, declared))
            continue
        la, lb = spec.layer_index(a), spec.layer_index(b)
        structural = False
        if lb < la and not declared:
            out.append(_c(spec, "LAYER_UPWARD", ("LAYER_UPWARD", a, b), (e,)))
            structural = True
        elif lb > la + 1 and not spec.allow_layer_skip and not declared:
            out.append(_c(spec, "LAYER_SKIP", ("LAYER_SKIP", a, b), (e,)))
            structural = True
        if b.startswith(DB_PREFIX) and spec.owner_of(b) != a:
            out.append(_c(spec, "FOREIGN_DATA", ("FOREIGN_DATA", a, b), (e,)))
        elif not declared and not structural and not b.startswith(DB_PREFIX):
            # a layer violation already says the edge is wrong; do not count it twice
            ca, cb = spec.context_of(a), spec.context_of(b)
            rule = "UNDECLARED_XCTX" if (ca and cb and ca != cb) else "UNDECLARED"
            out.append(_c(spec, rule, (rule, a, b), (e,)))
    out.extend(_cycles(edges, spec))
    return out


def _messaging(spec: Spec, e: Edge, declared: bool) -> List[Candidate]:
    """Producer -> topic and topic -> consumer edges."""
    if declared:
        return []
    a, b = e
    if b.startswith(TOPIC_PREFIX):                      # a publishes to b
        owner = spec.owner_of(b)
        if owner and owner != a:
            return [_c(spec, "FOREIGN_TOPIC", ("FOREIGN_TOPIC", a, b), (e,))]
        ca, cb = spec.context_of(a), spec.context_of(b)
    else:                                               # b consumes from a
        ca, cb = spec.context_of(a), spec.context_of(b)
    rule = "UNDECLARED_XCTX" if (ca and cb and ca != cb) else "UNDECLARED"
    return [_c(spec, rule, (rule, a, b), (e,))]


def _cycles(edges: Set[Edge], spec: Spec) -> List[Candidate]:
    adj: Dict[str, List[str]] = {}
    for a, b in edges:
        # cycles are a synchronous-coupling problem; datastores and topics
        # are left out
        if not (b.startswith(DB_PREFIX) or a.startswith(TOPIC_PREFIX) or b.startswith(TOPIC_PREFIX)):
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, [])
    res = []
    for comp in _scc(adj):
        if len(comp) < 2:
            continue
        members = set(comp)
        in_cycle = tuple(sorted((a, b) for a, b in edges if a in members and b in members))
        # the edges that are not in the spec are the ones that closed the loop
        blame = tuple(e for e in in_cycle if e not in spec.declared) or in_cycle
        res.append(_c(spec, "CYCLE", ("CYCLE",) + tuple(sorted(members)), blame))
    return res


def _scc(adj: Dict[str, List[str]]) -> List[List[str]]:
    """Iterative Tarjan."""
    index: Dict[str, int] = {}
    low: Dict[str, int] = {}
    on: Set[str] = set()
    stack: List[str] = []
    out: List[List[str]] = []
    counter = 0
    for root in adj:
        if root in index:
            continue
        work = [(root, iter(adj[root]))]
        index[root] = low[root] = counter; counter += 1
        stack.append(root); on.add(root)
        while work:
            v, it = work[-1]
            nxt = next(it, None)
            if nxt is not None:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter; counter += 1
                    stack.append(nxt); on.add(nxt)
                    work.append((nxt, iter(adj.get(nxt, []))))
                elif nxt in on:
                    low[v] = min(low[v], index[nxt])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop(); on.discard(w); comp.append(w)
                    if w == v:
                        break
                out.append(comp)
    return out


def stale_declarations(graphs: Iterable[WindowGraph], spec: Spec) -> List[Edge]:
    """Declared dependencies never observed in any of the given windows."""
    seen: Set[Edge] = set()
    for g in graphs:
        seen.update(g.edges)
    return sorted(e for e in spec.declared if e not in seen)
