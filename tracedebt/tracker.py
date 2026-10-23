"""Persistence tracking across windows.

A candidate becomes a confirmed drift finding when it has been present in
at least k of the last m windows. A confirmed finding is closed (resolved)
after it has been absent for `close_after` consecutive windows. This keeps
one-off bursts (a migration job, a canary, a misrouted retry) out of the
report, at the cost of a small detection delay.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from .graph import Edge, WindowGraph
from .rules import Candidate


@dataclass
class Finding:
    rule: str
    key: Tuple[str, ...]
    edges: Tuple[Edge, ...]
    severity: int
    first_seen: str                # window label of first candidate sighting
    confirmed_at: Optional[str] = None
    resolved_at: Optional[str] = None
    windows_present: int = 0
    last_traces: int = 0
    last_exposure: float = 0.0     # share of traces carrying the finding
    last_latency_share: float = 0.0
    evidence_trace: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.confirmed_at is not None and self.resolved_at is None


@dataclass
class Tracker:
    k: int = 3
    m: int = 4
    close_after: int = 3
    findings: Dict[Tuple[str, ...], Finding] = field(default_factory=dict)
    _hist: Dict[Tuple[str, ...], Deque[bool]] = field(default_factory=dict)
    _absent: Dict[Tuple[str, ...], int] = field(default_factory=dict)
    windows: List[str] = field(default_factory=list)

    def update(self, g: WindowGraph, cands: List[Candidate]) -> None:
        self.windows.append(g.label)
        now = {c.key: c for c in cands}
        for key in set(self._hist) | set(now):
            h = self._hist.setdefault(key, deque(maxlen=self.m))
            c = now.get(key)
            h.append(c is not None)
            f = self.findings.get(key)
            if c is not None:
                if f is None:
                    f = self.findings[key] = Finding(c.rule, key, c.edges, c.severity, g.label)
                f.edges = c.edges
                f.windows_present += 1
                self._absent[key] = 0
                self._measure(f, g)
                if f.resolved_at is not None and sum(h) >= self.k:
                    f.resolved_at = None                     # regression
                    f.confirmed_at = g.label
                if f.confirmed_at is None and sum(h) >= self.k:
                    f.confirmed_at = g.label
            elif f is not None:
                self._absent[key] = self._absent.get(key, 0) + 1
                if f.active and self._absent[key] >= self.close_after:
                    f.resolved_at = g.label

    @staticmethod
    def _measure(f: Finding, g: WindowGraph) -> None:
        es = set(f.edges)
        f.last_traces = max((g.edges[e].traces for e in es if e in g.edges), default=0)
        if g.trace_edges:
            hit = sum(1 for te in g.trace_edges if te & es)
            f.last_exposure = hit / max(g.n_traces, 1)
        else:
            f.last_exposure = f.last_traces / max(g.n_traces, 1)
        dur = sum(g.edges[e].duration_us for e in es if e in g.edges)
        f.last_latency_share = dur / max(g.total_duration_us, 1)
        for e in es:
            if e in g.edges and g.edges[e].sample_trace:
                f.evidence_trace = g.edges[e].sample_trace
                break

    def active(self) -> List[Finding]:
        return [f for f in self.findings.values() if f.active]

    # -- persistence between invocations ---------------------------------
    def to_dict(self) -> dict:
        """JSON-serialisable state: findings, the last m presence bits per
        candidate and the clean-window counters."""
        return {
            "version": 1, "k": self.k, "m": self.m, "close_after": self.close_after,
            "windows": self.windows[-64:],
            "findings": [{"rule": f.rule, "key": list(f.key), "edges": [list(e) for e in f.edges],
                          "severity": f.severity, "first_seen": f.first_seen,
                          "confirmed_at": f.confirmed_at, "resolved_at": f.resolved_at,
                          "windows_present": f.windows_present, "last_traces": f.last_traces,
                          "last_exposure": f.last_exposure,
                          "last_latency_share": f.last_latency_share,
                          "evidence_trace": f.evidence_trace}
                         for f in self.findings.values()],
            "history": [{"key": list(k), "bits": [bool(b) for b in h],
                         "absent": self._absent.get(k, 0)} for k, h in self._hist.items()],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Tracker":
        t = cls(k=d["k"], m=d["m"], close_after=d["close_after"])
        t.windows = list(d.get("windows", []))
        for f in d["findings"]:
            key = tuple(f["key"])
            t.findings[key] = Finding(
                f["rule"], key, tuple(tuple(e) for e in f["edges"]), f["severity"],
                f["first_seen"], f["confirmed_at"], f["resolved_at"], f["windows_present"],
                f["last_traces"], f["last_exposure"], f["last_latency_share"], f["evidence_trace"])
        for h in d["history"]:
            key = tuple(h["key"])
            t._hist[key] = deque(h["bits"], maxlen=t.m)
            t._absent[key] = h["absent"]
        return t
