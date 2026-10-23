"""How rare a drift can be and still be confirmed, given current trace volume.

A quiet report is only evidence of conformance for calls that are common
enough to be sampled. If service `a` appears in `n` traces per window and a
drifted call fires in a share `p` of them, the chance of seeing it at least
once in a window is q = 1 - (1 - p)^n. With k-of-m persistence the finding is
confirmed when it is seen in at least k of m windows. For a target
confirmation probability (default 0.9) we solve for q*, then

    p_min = 1 - (1 - q*)^(1/n)

is the rarest drift from `a` that TraceDebt would confirm with that
probability. Services where p_min is large are where silence means little.
"""
from __future__ import annotations

from math import comb
from typing import Dict, List, Sequence

from .graph import WindowGraph


def _p_at_least(k: int, m: int, q: float) -> float:
    return sum(comb(m, i) * q ** i * (1 - q) ** (m - i) for i in range(k, m + 1))


def q_star(k: int, m: int, target: float = 0.9) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if _p_at_least(k, m, mid) >= target:
            hi = mid
        else:
            lo = mid
    return hi


def detection_floor(graphs: Sequence[WindowGraph], k: int, m: int,
                    target: float = 0.9) -> Dict[str, Dict[str, float]]:
    """Per service: traces per window (mean of the last m windows) and p_min."""
    recent = list(graphs)[-m:]
    if not recent:
        return {}
    qs = q_star(k, m, target)
    out: Dict[str, Dict[str, float]] = {}
    services = set()
    for g in recent:
        services.update(g.svc_traces)
    for s in services:
        n = sum(g.svc_traces.get(s, 0) for g in recent) / len(recent)
        p = 1.0 if n < 1 else 1 - (1 - qs) ** (1 / n)
        out[s] = {"traces_per_window": round(n, 1), "p_min": p}
    return out


def weakest(floor: Dict[str, Dict[str, float]], limit: float = 0.01) -> List[str]:
    """Services whose detection floor is above `limit`, worst first."""
    return sorted((s for s, v in floor.items() if v["p_min"] > limit),
                  key=lambda s: (-floor[s]["p_min"], s))
