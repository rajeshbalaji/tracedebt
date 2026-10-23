"""Drift indicators for prioritization.

The project is called TraceDebt, but these numbers are not cost estimates.
They are two simple indicators meant to order work:

  severity  = sum of severity weights of active findings
              (how much is wrong with the structure)
  exposure  = sum of severity weight x share of traces touching the finding
              (how much of observed production traffic leans on it)

Two plain runtime measures are reported alongside: the share of traces that
touch at least one drifted edge, and the share of end-to-end latency spent on
drifted edges. None of them has been validated against maintenance effort or
defect data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Set

from .graph import Edge, WindowGraph
from .tracker import Finding


@dataclass
class DebtSnapshot:
    window: str
    findings: int
    severity: float
    exposure: float
    trace_share: float
    latency_share: float


def snapshot(g: WindowGraph, active: List[Finding]) -> DebtSnapshot:
    severity = float(sum(f.severity for f in active))
    exposure = float(sum(f.severity * f.last_exposure for f in active))
    drifted: Set[Edge] = set()
    for f in active:
        drifted.update(f.edges)
    if g.trace_edges:
        share = sum(1 for te in g.trace_edges if te & drifted) / max(g.n_traces, 1)
    else:
        share = max((f.last_exposure for f in active), default=0.0)
    dur = sum(g.edges[e].duration_us for e in drifted if e in g.edges)
    return DebtSnapshot(g.label, len(active), severity, round(exposure, 4),
                        round(share, 4), round(dur / max(g.total_duration_us, 1), 4))
