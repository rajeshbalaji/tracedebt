"""End-to-end analysis over an ordered sequence of observation windows."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

from . import graph as graph_mod
from .coverage import detection_floor
from .debt import DebtSnapshot, snapshot
from .ingest import Trace
from .rules import check, stale_declarations
from .spec import Spec
from .tracker import Finding, Tracker


@dataclass
class Result:
    spec: Spec
    tracker: Tracker
    debt: List[DebtSnapshot] = field(default_factory=list)
    stale: List[Tuple[str, str]] = field(default_factory=list)
    last_graph: graph_mod.WindowGraph = None
    coverage: Dict[str, Dict[str, float]] = field(default_factory=dict)
    inferred_edges: List[Tuple[str, str]] = field(default_factory=list)

    def active(self) -> List[Finding]:
        return sorted(self.tracker.active(), key=lambda f: (-f.severity, f.key))


class Analyzer:
    """Incremental analyzer: feed windows one at a time (used by the CLI,
    the benchmark harness and CI integrations alike)."""

    def __init__(self, spec: Spec, k: int = 3, m: int = 4, min_traces: int = 1,
                 close_after: int = 3, keep_trace_edges: bool = True):
        self.spec = spec
        self.min_traces = min_traces
        self.keep = keep_trace_edges
        self.result = Result(spec, Tracker(k=k, m=m, close_after=close_after))
        self._graphs: List[graph_mod.WindowGraph] = []

    def set_spec(self, spec: Spec) -> None:
        """Architects updated the intended architecture (e.g. new release)."""
        self.spec = spec
        self.result.spec = spec

    def save_state(self, path: str) -> None:
        """Write the tracker state so a later invocation can continue from it."""
        import json
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.result.tracker.to_dict(), fh)

    def load_state(self, path: str) -> None:
        """Continue from a state file written by `save_state`. The persistence
        settings (k, m, close-after) must match the ones used to write it."""
        import json
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        t = self.result.tracker
        if (d["k"], d["m"], d["close_after"]) != (t.k, t.m, t.close_after):
            raise ValueError(f"{path}: written with k={d['k']} m={d['m']} close-after="
                             f"{d['close_after']}, current run uses k={t.k} m={t.m} "
                             f"close-after={t.close_after}")
        self.result.tracker = Tracker.from_dict(d)

    def feed(self, label: str, traces: Iterable[Trace]) -> graph_mod.WindowGraph:
        g = graph_mod.build(label, traces, self.spec, self.keep)
        cands = check(g, self.spec, self.min_traces)
        self.result.tracker.update(g, cands)
        self.result.debt.append(snapshot(g, self.result.tracker.active()))
        self.result.last_graph = g
        self._graphs = (self._graphs + [g])[-8:]
        if not self.keep:
            g.trace_edges = []
        return g

    def finish(self) -> Result:
        self.result.stale = stale_declarations(self._graphs, self.spec)
        t = self.result.tracker
        self.result.coverage = detection_floor(self._graphs, t.k, t.m)
        g = self.result.last_graph
        if g is not None:
            self.result.inferred_edges = sorted(e for e, st in g.edges.items()
                                                if st.inferred == st.calls)
        return self.result


def analyze(spec: Spec, windows: Iterable[Tuple[str, Iterable[Trace]]], **kw) -> Result:
    a = Analyzer(spec, **kw)
    for label, traces in windows:
        a.feed(label, traces)
    return a.finish()
