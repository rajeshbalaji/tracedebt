"""Intended-architecture specification.

The spec is a small YAML file kept next to the code. It names the layers,
assigns every service to a layer and (optionally) a bounded context, says
which service owns which datastore, and lists the dependencies the team
has agreed on. Anything observed at runtime is checked against this.

Real telemetry rarely uses the names a team uses. `aliases` maps raw names
(service.name values, gRPC service names such as "hipstershop.CartService",
host:port addresses, derived datastore ids such as "redis://10.0.0.5:6379")
to the canonical names used in the spec. `ignore` entries are shell-style
patterns ("opentelemetry.proto.collector.*").
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import yaml

DB_PREFIX = "db:"
TOPIC_PREFIX = "topic:"


@dataclass
class ServiceDecl:
    name: str
    layer: str
    context: Optional[str] = None
    owns: List[str] = field(default_factory=list)
    publishes: List[str] = field(default_factory=list)
    subscribes: List[str] = field(default_factory=list)


@dataclass
class Spec:
    system: str
    layers: List[str]
    services: Dict[str, ServiceDecl]
    datastores: Dict[str, str]                 # datastore -> owning service
    declared: Set[Tuple[str, str]]             # (caller, callee) incl. db:<name>
    allow_layer_skip: bool = False
    ignore_services: Set[str] = field(default_factory=set)
    ignore_operations: Set[str] = field(default_factory=set)
    topics: Dict[str, Optional[str]] = field(default_factory=dict)  # topic -> owner
    severity: Dict[str, int] = field(default_factory=dict)         # rule overrides
    aliases: Dict[str, str] = field(default_factory=dict)          # raw -> canonical

    # ------------------------------------------------------------------
    def canon(self, raw: Optional[str]) -> Optional[str]:
        if raw is None:
            return None
        return self.aliases.get(raw, raw)

    # Pattern checks run once per span, so results are memoized per name.
    def ignored_service(self, name: str) -> bool:
        c = self.__dict__.setdefault("_ign_s", {})
        r = c.get(name)
        if r is None:
            r = c[name] = any(fnmatch.fnmatchcase(name, p) for p in self.ignore_services)
        return r

    def ignored_operation(self, op: str) -> bool:
        c = self.__dict__.setdefault("_ign_o", {})
        r = c.get(op)
        if r is None:
            r = c[op] = any(fnmatch.fnmatchcase(op, p) for p in self.ignore_operations)
        return r

    # ------------------------------------------------------------------
    def layer_index(self, node: str) -> Optional[int]:
        if node.startswith(TOPIC_PREFIX):
            return None                       # brokers sit outside the layering
        if node.startswith(DB_PREFIX):
            # datastores always sit on the last layer
            return len(self.layers) - 1
        decl = self.services.get(node)
        if decl is None:
            return None
        return self.layers.index(decl.layer)

    def context_of(self, node: str) -> Optional[str]:
        if node.startswith(TOPIC_PREFIX):
            owner = self.topics.get(node[len(TOPIC_PREFIX):])
            return self.context_of(owner) if owner else None
        if node.startswith(DB_PREFIX):
            owner = self.datastores.get(node[len(DB_PREFIX):])
            return self.context_of(owner) if owner else None
        decl = self.services.get(node)
        return decl.context if decl else None

    def owner_of(self, node: str) -> Optional[str]:
        if node.startswith(TOPIC_PREFIX):
            return self.topics.get(node[len(TOPIC_PREFIX):])
        return self.datastores.get(node[len(DB_PREFIX):])

    def is_known(self, node: str) -> bool:
        if node.startswith(TOPIC_PREFIX):
            return node[len(TOPIC_PREFIX):] in self.topics
        if node.startswith(DB_PREFIX):
            return node[len(DB_PREFIX):] in self.datastores
        return node in self.services

    # ------------------------------------------------------------------
    @staticmethod
    def from_dict(d: dict) -> "Spec":
        layers = list(d["layers"])
        services: Dict[str, ServiceDecl] = {}
        datastores: Dict[str, str] = {}
        for name, s in (d.get("services") or {}).items():
            s = s or {}
            layer = s.get("layer")
            if layer not in layers:
                raise ValueError(f"service {name}: unknown layer {layer!r}")
            owns = list(s.get("owns") or [])
            services[name] = ServiceDecl(name, layer, s.get("context"), owns,
                                         list(s.get("publishes") or []),
                                         list(s.get("subscribes") or []))
            for db in owns:
                if db in datastores:
                    raise ValueError(f"datastore {db} owned twice")
                datastores[db] = name
        declared: Set[Tuple[str, str]] = set()
        for caller, callees in (d.get("dependencies") or {}).items():
            for callee in callees or []:
                declared.add((caller, callee))
        # owning a datastore implies a declared dependency on it
        for db, owner in datastores.items():
            declared.add((owner, DB_PREFIX + db))
        # topics: explicit owner, else the single declared publisher
        topics: Dict[str, Optional[str]] = {}
        for t, td in (d.get("topics") or {}).items():
            topics[t] = (td or {}).get("owner")
        pubs: Dict[str, List[str]] = {}
        for sd in services.values():
            for t in sd.publishes:
                pubs.setdefault(t, []).append(sd.name)
                declared.add((sd.name, TOPIC_PREFIX + t))
            for t in sd.subscribes:
                declared.add((TOPIC_PREFIX + t, sd.name))
                topics.setdefault(t, None)
        for t, ps in pubs.items():
            if topics.get(t) is None:
                topics[t] = ps[0] if len(ps) == 1 else None
        ign = d.get("ignore") or {}
        return Spec(
            system=d.get("system", "system"),
            layers=layers,
            services=services,
            datastores=datastores,
            declared=declared,
            allow_layer_skip=bool(d.get("allow_layer_skip", False)),
            ignore_services=set(ign.get("services") or []),
            ignore_operations=set(ign.get("operations") or []),
            topics=topics,
            severity={k: int(v) for k, v in (d.get("severity") or {}).items()},
            aliases={str(k): str(v) for k, v in (d.get("aliases") or {}).items()},
        )

    @staticmethod
    def load(path: str) -> "Spec":
        with open(path, "r", encoding="utf-8") as fh:
            return Spec.from_dict(yaml.safe_load(fh))

    def to_dict(self) -> dict:
        deps: Dict[str, List[str]] = {}
        implied = {(o, DB_PREFIX + db) for db, o in self.datastores.items()}
        implied |= {e for e in self.declared if e[0].startswith(TOPIC_PREFIX)
                    or e[1].startswith(TOPIC_PREFIX)}
        for a, b in sorted(self.declared - implied):
            deps.setdefault(a, []).append(b)
        return {
            "system": self.system,
            "layers": self.layers,
            "allow_layer_skip": self.allow_layer_skip,
            "services": {
                n: {k: v for k, v in (("layer", s.layer), ("context", s.context),
                                        ("owns", s.owns), ("publishes", s.publishes),
                                        ("subscribes", s.subscribes)) if v}
                for n, s in sorted(self.services.items())
            },
            "dependencies": deps,
            **({"topics": {t: ({"owner": o} if o else {}) for t, o in sorted(self.topics.items())}}
               if self.topics else {}),
            **({"severity": dict(self.severity)} if self.severity else {}),
            **({"aliases": dict(sorted(self.aliases.items()))} if self.aliases else {}),
            "ignore": {"services": sorted(self.ignore_services),
                       "operations": sorted(self.ignore_operations)},
        }
