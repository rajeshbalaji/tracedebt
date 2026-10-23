"""Synthetic evolving-microservice benchmark with injected architectural drift.

The generator builds a layered service topology (edge -> api -> domain ->
data), evolves it over a number of releases, and emits distributed traces
for several observation windows per release. Each release applies

  * legitimate evolution that the architects also record in the spec
    (new declared dependency, new service with its own datastore,
    removed dependency), and
  * architectural drift that is *not* recorded in the spec
    (layer skip, foreign datastore access, upward call, undeclared
    cross-context call, dependency cycle, shadow service).

Drift can be repaired in a later release. On top of that every window
carries trace noise that a careful tool must not report: one-window bursts
(migration jobs, canaries), sporadic single calls (misrouted retries),
and health-probe traffic from the mesh.

Head-based sampling is modelled exactly: keeping each request with
probability s is the same as drawing Binomial(requests, s) traces.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from tracedebt.ingest import Span
from tracedebt.spec import DB_PREFIX, TOPIC_PREFIX, Spec

DOMAIN_NAMES = [
    "order", "seat", "payment", "user", "auth", "route", "station", "train",
    "price", "travel", "notification", "inventory", "food", "consign",
    "cancel", "rebook", "insurance", "voucher", "assurance", "contacts",
    "config", "security", "news", "delivery", "preserve", "basic",
    "execute", "waitlist", "verification", "invoice", "loyalty", "refund",
    "schedule", "fleet", "catalog", "review", "search-index", "audit",
    "ledger", "promo", "quota", "geo", "media", "feedback", "booking",
]
API_NAMES = ["web-bff", "mobile-bff", "admin-api", "partner-api", "ops-api",
             "search-api", "kiosk-bff", "agent-api"]

DRIFT_TYPES = ["LAYER_SKIP", "FOREIGN_DATA", "UPWARD", "XCTX", "CYCLE", "SHADOW", "FOREIGN_TOPIC"]


@dataclass
class Drift:
    id: int
    kind: str
    edge: Tuple[str, str]
    prob: float
    injected: int                # release index
    repaired: Optional[int] = None


@dataclass
class System:
    rng: random.Random
    services: Dict[str, dict] = field(default_factory=dict)  # name -> layer/context/owns
    runtime: Dict[Tuple[str, str], float] = field(default_factory=dict)
    declared: Set[Tuple[str, str]] = field(default_factory=set)
    drifts: List[Drift] = field(default_factory=list)
    shadow: Set[str] = field(default_factory=set)
    pending: Set[Tuple[str, str]] = field(default_factory=set)  # legit, not yet in spec
    spec_lag: float = 0.3
    topics: Dict[str, str] = field(default_factory=dict)         # topic -> owner
    subs: Dict[str, List[str]] = field(default_factory=dict)     # topic -> subscribers
    _names: List[str] = field(default_factory=list)
    _ctx_count: int = 0

    # -------------------------------------------------------------- topology
    @staticmethod
    def build(n_services: int, seed: int) -> "System":
        rng = random.Random(seed)
        s = System(rng)
        s._names = DOMAIN_NAMES[:]
        rng.shuffle(s._names)
        n_api = max(2, round(0.15 * n_services))
        n_dom = n_services - 1 - n_api
        s.services["gateway"] = {"layer": "edge"}
        apis = API_NAMES[:n_api]
        for a in apis:
            s.services[a] = {"layer": "api"}
            s._decl("gateway", a, rng.uniform(0.3, 1.0))
        s._ctx_count = max(2, math.ceil(n_dom / 5))
        doms = []
        for i in range(n_dom):
            doms.append(s._new_domain(context=f"ctx{i % s._ctx_count}"))
        # declared domain -> domain edges form a DAG (lower index calls higher)
        for i, a in enumerate(doms):
            for _ in range(rng.randint(0, 3)):
                same = [d for d in doms[i + 1:] if s.ctx(d) == s.ctx(a)]
                other = [d for d in doms[i + 1:] if s.ctx(d) != s.ctx(a)]
                pool = same if (same and rng.random() < 0.8) else other
                if pool:
                    s._decl(a, rng.choice(pool), rng.uniform(0.1, 0.7))
        for a in apis:
            for d in rng.sample(doms, min(len(doms), rng.randint(2, 4))):
                s._decl(a, d, rng.uniform(0.3, 0.9))
        # every domain service must be reachable from some caller
        for i, d in enumerate(doms):
            if not any(b == d for (_, b) in s.declared):
                same = [x for x in doms[:i] if s.ctx(x) == s.ctx(d)]
                caller = rng.choice(same) if same and rng.random() < 0.5 else rng.choice(apis)
                s._decl(caller, d, rng.uniform(0.3, 0.8))
        # event topics (separate RNG so the synchronous topology is unchanged)
        trng = random.Random(seed * 31 + 7)
        for d in doms:
            if trng.random() < 0.3:
                t = d.replace("-svc", "") + "-events"
                s.topics[t] = d
                s._decl(d, TOPIC_PREFIX + t, trng.uniform(0.2, 0.6))
                others = [x for x in doms if x != d]
                s.subs[t] = trng.sample(others, min(len(others), trng.randint(1, 2)))
                for sub in s.subs[t]:
                    s.declared.add((TOPIC_PREFIX + t, sub))
        return s

    def _new_domain(self, context: str) -> str:
        name = self._names.pop() + "-svc" if self._names else f"svc-{len(self.services)}"
        owns = []
        if self.rng.random() < 0.65:
            owns = [name.replace("-svc", "") + "-db"]
        self.services[name] = {"layer": "domain", "context": context, "owns": owns}
        for db in owns:
            self._decl(name, DB_PREFIX + db, self.rng.uniform(0.6, 1.0))
        return name

    def _decl(self, a: str, b: str, p: float) -> None:
        self.declared.add((a, b))
        self.runtime[(a, b)] = p

    def ctx(self, n: str) -> Optional[str]:
        return self.services.get(n, {}).get("context")

    def layer(self, n: str) -> str:
        if n.startswith(TOPIC_PREFIX):
            return "broker"
        return "data" if n.startswith(DB_PREFIX) else self.services.get(n, {}).get("layer", "domain")

    def of_layer(self, layer: str) -> List[str]:
        return sorted(n for n, d in self.services.items() if d["layer"] == layer)

    def dbs(self) -> Dict[str, str]:
        return {db: n for n, d in self.services.items() for db in d.get("owns", [])}

    def spec(self) -> Spec:
        svcs = {n: {k: v for k, v in d.items() if v} for n, d in self.services.items()}
        for (a, b) in self.declared:
            if b.startswith(TOPIC_PREFIX):
                svcs[a].setdefault("publishes", []).append(b[len(TOPIC_PREFIX):])
            elif a.startswith(TOPIC_PREFIX):
                svcs[b].setdefault("subscribes", []).append(a[len(TOPIC_PREFIX):])
        return Spec.from_dict({
            "system": "bench",
            "layers": ["edge", "api", "domain", "data"],
            "services": svcs,
            "topics": {t: {"owner": o} for t, o in self.topics.items()},
            "dependencies": _group({e for e in self.declared
                                    if not (e[1].startswith(DB_PREFIX) or e[0].startswith(TOPIC_PREFIX)
                                            or e[1].startswith(TOPIC_PREFIX))}),
            "ignore": {"services": ["mesh-probe"], "operations": ["/health"]},
        })

    # ------------------------------------------------------------- evolution
    def evolve(self, release: int) -> None:
        rng = self.rng
        doms = self.of_layer("domain")
        # spec catches up with last release's undocumented-but-intended change
        self.declared |= self.pending
        self.pending = set()
        # legitimate, recorded in the spec (sometimes one release late)
        if rng.random() < 0.6:
            a, b = rng.sample(doms, 2)
            if (a, b) not in self.runtime and (b, a) not in self.runtime and not _reaches(self.declared, b, a):
                if rng.random() < self.spec_lag:
                    self.runtime[(a, b)] = rng.uniform(0.1, 0.6)
                    self.pending.add((a, b))
                else:
                    self._decl(a, b, rng.uniform(0.1, 0.6))
        if rng.random() < 0.3 and self._names:
            ctx = f"ctx{rng.randrange(self._ctx_count)}"
            n = self._new_domain(ctx)
            caller = rng.choice(self.of_layer("api") + [d for d in doms if self.ctx(d) == ctx])
            self._decl(caller, n, rng.uniform(0.2, 0.6))
        if rng.random() < 0.3:
            removable = [e for e in self.declared - self.pending
                         if self.layer(e[0]) == "domain" and self.layer(e[1]) == "domain"]
            if removable:
                e = rng.choice(sorted(removable))
                self.declared.discard(e)
                self.runtime.pop(e, None)
        # repair some active drift
        for d in self.drifts:
            if d.repaired is None and d.injected < release and rng.random() < 0.15:
                d.repaired = release
                self.runtime.pop(d.edge, None)
        # inject new drift, not recorded in the spec
        n_new = _poisson(rng, 0.9)
        for _ in range(n_new):
            self._inject(rng.choice(DRIFT_TYPES), release)

    def _inject(self, kind: str, release: int) -> None:
        rng = self.rng
        doms, apis, dbs = self.of_layer("domain"), self.of_layer("api"), self.dbs()
        # rare drift is realistic: activation probability is log-uniform
        p = math.exp(rng.uniform(math.log(0.002), math.log(0.3)))
        edge = None
        if kind == "LAYER_SKIP" and dbs:
            edge = (rng.choice(apis), DB_PREFIX + rng.choice(sorted(dbs)))
        elif kind == "FOREIGN_DATA" and dbs:
            db = rng.choice(sorted(dbs))
            caller = rng.choice([d for d in doms if d != dbs[db]])
            edge = (caller, DB_PREFIX + db)
        elif kind == "UPWARD":
            edge = (rng.choice(doms), rng.choice(apis))
        elif kind == "XCTX":
            a = rng.choice(doms)
            pool = [d for d in doms if self.ctx(d) != self.ctx(a)]
            if pool:
                edge = (a, rng.choice(pool))
        elif kind == "CYCLE":
            decl = [e for e in self.declared if self.layer(e[0]) == "domain"
                    and self.layer(e[1]) == "domain" and e in self.runtime]
            if decl:
                a, b = rng.choice(sorted(decl))
                edge = (b, a)
        elif kind == "FOREIGN_TOPIC" and self.topics:
            t = rng.choice(sorted(self.topics))
            caller = rng.choice([d for d in doms if d != self.topics[t]])
            edge = (caller, TOPIC_PREFIX + t)
        elif kind == "SHADOW":
            name = f"shadow-{len(self.shadow) + 1}"
            self.shadow.add(name)
            edge = (rng.choice(doms), name)
        if edge is None or edge in self.runtime or edge in self.declared:
            return
        self.runtime[edge] = p
        self.drifts.append(Drift(len(self.drifts), kind, edge, p, release))

    def active_drift(self) -> Set[Tuple[str, str]]:
        return {d.edge for d in self.drifts if d.repaired is None}


def _group(edges):
    out: Dict[str, List[str]] = {}
    for a, b in sorted(edges):
        out.setdefault(a, []).append(b)
    return out


def _reaches(edges, src, dst) -> bool:
    adj: Dict[str, List[str]] = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
    stack, seen = [src], {src}
    while stack:
        v = stack.pop()
        if v == dst:
            return True
        for w in adj.get(v, []):
            if w not in seen:
                seen.add(w); stack.append(w)
    return False


def _poisson(rng: random.Random, lam: float) -> int:
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


# ------------------------------------------------------------------ traces
@dataclass
class NoiseModel:
    burst_prob: float = 0.35      # chance a window carries a one-off burst edge
    burst_activation: Tuple[float, float] = (0.02, 0.3)
    sporadic_per_trace: float = 0.002
    probes: int = 3
    recurring: int = 0                 # benign undeclared edges present all the time
    recurring_rate: float = 0.0        # per-trace chance one of them fires


class TraceGen:
    def __init__(self, system: System, rng: random.Random, noise: NoiseModel):
        self.s, self.rng, self.noise = system, rng, noise
        self._ids = 0
        self.recurring: List[Tuple[str, str]] = []
        nodes = sorted(x for x in system.services if x != "gateway")
        while len(self.recurring) < noise.recurring:
            a, b = rng.sample(nodes, 2)
            if (a, b) not in system.runtime and (a, b) not in self.recurring:
                self.recurring.append((a, b))

    def _id(self) -> str:
        self._ids += 1
        return f"{self._ids:x}"

    def window(self, requests: int, sampling: float) -> Tuple[List[List[Span]], Set[Tuple[str, str]]]:
        rng, s = self.rng, self.s
        runtime = dict(s.runtime)
        noise_edges: Set[Tuple[str, str]] = set()
        if rng.random() < self.noise.burst_prob:
            nodes = [n for n in s.services if n != "gateway"]
            a, b = rng.sample(nodes, 2)
            if (a, b) not in runtime:
                runtime[(a, b)] = rng.uniform(*self.noise.burst_activation)
                noise_edges.add((a, b))
        out_adj: Dict[str, List[Tuple[str, float]]] = {}
        for (a, b), p in runtime.items():
            out_adj.setdefault(a, []).append((b, p))
        apis = out_adj.get("gateway", [])
        api_w = [p for _, p in apis]
        n = _binomial(rng, requests, sampling)
        traces = []
        all_nodes = [x for x in s.services if x != "gateway"]
        for _ in range(n):
            tid = self._id() + "t"
            spans: List[Span] = []
            root = Span(tid, self._id(), None, "gateway", "HTTP GET", 0)
            spans.append(root)
            api = rng.choices([a for a, _ in apis], weights=api_w)[0]
            root.duration_us = self._call(tid, root.span_id, api, out_adj, spans, 1)
            if self.recurring and rng.random() < self.noise.recurring_rate:
                a, b = rng.choice(self.recurring)
                host = next((x for x in spans if x.service == a), None)
                if host is not None:
                    spans.append(Span(tid, self._id(), host.span_id, b, "fallback", 600))
            if rng.random() < self.noise.sporadic_per_trace:
                host = rng.choice(spans)
                tgt = rng.choice(all_nodes)
                if tgt != host.service:
                    noise_edges.add((host.service, tgt))
                    spans.append(Span(tid, self._id(), host.span_id, tgt, "retry", 800))
            traces.append(spans)
        for _ in range(self.noise.probes):
            tid = self._id() + "p"
            r = Span(tid, self._id(), None, "mesh-probe", "probe", 100)
            traces.append([r] + [Span(tid, self._id(), r.span_id, x, "/health", 50)
                                 for x in s.services])
        return traces, noise_edges

    def _call(self, tid, parent, svc, adj, spans, depth, consume=None) -> int:
        sp = Span(tid, self._id(), parent, svc, f"{svc}.handle", 0,
                  kind="consumer" if consume else None, msg_dest=consume)
        spans.append(sp)
        dur = int(self.rng.lognormvariate(7.5, 0.6))          # ~2 ms self time
        if depth < 9:
            for tgt, p in adj.get(svc, []):
                if self.rng.random() >= p:
                    continue
                if tgt.startswith(TOPIC_PREFIX):
                    t = tgt[len(TOPIC_PREFIX):]
                    pr = Span(tid, self._id(), sp.span_id, svc, f"publish {t}", 300,
                              kind="producer", msg_dest=t)
                    spans.append(pr)
                    dur += 300
                    for sub in self.s.subs.get(t, []):     # async: not added to dur
                        self._call(tid, pr.span_id, sub, adj, spans, depth + 1, consume=t)
                    continue
                if tgt.startswith(DB_PREFIX):
                    d = int(self.rng.lognormvariate(7.0, 0.8))
                    spans.append(Span(tid, self._id(), sp.span_id, svc, "SELECT",
                                      d, tgt[len(DB_PREFIX):]))
                    dur += d
                else:
                    dur += self._call(tid, sp.span_id, tgt, adj, spans, depth + 1)
        sp.duration_us = dur
        return dur


def _binomial(rng: random.Random, n: int, p: float) -> int:
    if p >= 1.0:
        return n
    mu, var = n * p, n * p * (1 - p)
    if var > 25:
        return max(0, int(round(rng.gauss(mu, math.sqrt(var)))))
    return sum(1 for _ in range(n) if rng.random() < p)


def to_jaeger(traces: List[List[Span]]) -> dict:
    data = []
    for tr in traces:
        procs, pid_of = {}, {}
        spans = []
        for sp in tr:
            if sp.service not in pid_of:
                pid_of[sp.service] = f"p{len(pid_of) + 1}"
                procs[pid_of[sp.service]] = {"serviceName": sp.service, "tags": []}
            tags = [{"key": "db.name", "type": "string", "value": sp.db_name},
                    {"key": "db.system", "type": "string", "value": "postgresql"}] if sp.db_name else []
            if sp.msg_dest:
                tags += [{"key": "span.kind", "type": "string", "value": sp.kind},
                         {"key": "messaging.system", "type": "string", "value": "kafka"},
                         {"key": "messaging.destination.name", "type": "string", "value": sp.msg_dest}]
            spans.append({"traceID": sp.trace_id, "spanID": sp.span_id,
                          "operationName": sp.operation,
                          "references": ([{"refType": "CHILD_OF", "traceID": sp.trace_id,
                                           "spanID": sp.parent_id}] if sp.parent_id else []),
                          "startTime": 0, "duration": sp.duration_us,
                          "processID": pid_of[sp.service], "tags": tags})
        data.append({"traceID": tr[0].trace_id, "spans": spans, "processes": procs})
    return {"data": data}
