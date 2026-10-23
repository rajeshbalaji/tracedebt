"""Trace ingestion.

Two input formats are supported:
  * Jaeger JSON as returned by the Jaeger query API / UI export
    ({"data": [{"traceID", "spans": [...], "processes": {...}}]})
  * OTLP/JSON as written by the OpenTelemetry Collector file exporter
    ({"resourceSpans": [...]}, one object per line or a single object)

Both are reduced to a flat list of Span records grouped by trace. Real
instrumentation libraries disagree on attribute names, so ingestion
normalizes the few fields TraceDebt needs:

  kind         client / server / producer / consumer / internal
  rpc_service  `rpc.service`, or the part of `rpc.method` before "/" (newer
               gRPC conventions put "pkg.Service/Method" in rpc.method)
  peer         `peer.service`, else host:port from `server.address` /
               `net.peer.name` and `server.port` / `net.peer.port`
  db_name      `db.name` / `db.namespace`; when both are missing or empty
               (common for Redis and SQLite), "<db.system>://host:port"
  msg_dest     `messaging.destination.name` (or the older
               `messaging.destination`)
"""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional


@dataclass(slots=True)
class Span:
    trace_id: str
    span_id: str
    parent_id: Optional[str]
    service: str
    operation: str
    duration_us: int
    db_name: Optional[str] = None
    kind: Optional[str] = None          # client/server/producer/consumer/internal
    msg_dest: Optional[str] = None      # topic / queue name
    rpc_service: Optional[str] = None
    peer: Optional[str] = None
    error: bool = False


Trace = List[Span]

_OTLP_KIND = {1: "internal", 2: "server", 3: "client", 4: "producer", 5: "consumer",
              "SPAN_KIND_INTERNAL": "internal", "SPAN_KIND_SERVER": "server",
              "SPAN_KIND_CLIENT": "client", "SPAN_KIND_PRODUCER": "producer",
              "SPAN_KIND_CONSUMER": "consumer"}


def _s(v) -> Optional[str]:
    if v is None:
        return None
    v = str(v)
    return v if v else None


def _normalize(attrs: Dict[str, object]) -> Dict[str, Optional[str]]:
    rpc = _s(attrs.get("rpc.service"))
    method = _s(attrs.get("rpc.method"))
    if not rpc and method and "/" in method:
        rpc = method.split("/")[0].lstrip("/") or None
    host = _s(attrs.get("server.address")) or _s(attrs.get("net.peer.name"))
    port = _s(attrs.get("server.port")) or _s(attrs.get("net.peer.port"))
    addr = f"{host}:{port}" if host and port else host
    peer = _s(attrs.get("peer.service")) or addr
    db = _s(attrs.get("db.name")) or _s(attrs.get("db.namespace")) or _s(attrs.get("db.instance"))
    system = _s(attrs.get("db.system")) or _s(attrs.get("db.system.name"))
    if not db and system:
        db = f"{system}://{addr}" if addr else system
    dest = _s(attrs.get("messaging.destination.name")) or _s(attrs.get("messaging.destination"))
    return {"rpc": rpc, "peer": peer, "db": db, "dest": dest}


# The only attribute keys TraceDebt looks at; everything else on a span is skipped
# while parsing, which is most of the cost on instrumented HTTP and gRPC spans.
_WANT = frozenset({
    "rpc.service", "rpc.method", "server.address", "net.peer.name", "server.port",
    "net.peer.port", "peer.service", "db.name", "db.namespace", "db.instance",
    "db.system", "db.system.name", "messaging.destination.name", "messaging.destination",
    "span.kind", "error", "otel.status_code", "service.name"})


def _jaeger_tags(tags) -> Dict[str, object]:
    return {t["key"]: t.get("value") for t in (tags or ()) if t.get("key") in _WANT}


def from_jaeger(doc: dict) -> Iterator[Trace]:
    for tr in doc.get("data", []):
        procs = {pid: p.get("serviceName", "?")
                 for pid, p in (tr.get("processes") or {}).items()}
        out: Trace = []
        for s in tr.get("spans", []):
            parent = None
            for ref in s.get("references") or []:
                if ref.get("refType") == "CHILD_OF":
                    parent = ref.get("spanID")
                    break
            if parent is None:              # FOLLOWS_FROM is how async hops are often linked
                for ref in s.get("references") or []:
                    parent = ref.get("spanID")
                    break
            tags = _jaeger_tags(s.get("tags"))
            n = _normalize(tags)
            svc = procs.get(s.get("processID"), s.get("process", {}).get("serviceName", "?"))
            kind = _s(tags.get("span.kind"))
            err = tags.get("error") in (True, "true") or _s(tags.get("otel.status_code")) == "ERROR"
            out.append(Span(s.get("traceID", tr.get("traceID")), s["spanID"], parent,
                            svc, s.get("operationName", ""), int(s.get("duration", 0)),
                            n["db"], kind, n["dest"], n["rpc"], n["peer"], bool(err)))
        if out:
            yield out


def _otlp_attr(attrs) -> Dict[str, object]:
    res = {}
    for a in attrs or ():
        k = a.get("key")
        if k in _WANT:
            v = a.get("value")
            res[k] = next(iter(v.values()), None) if v else None
    return res


def from_otlp(doc: dict) -> Iterator[Trace]:
    by_trace: Dict[str, Trace] = {}
    for rs in doc.get("resourceSpans", []):
        svc = _otlp_attr(rs.get("resource", {}).get("attributes")).get("service.name", "?")
        for ss in rs.get("scopeSpans", []) or rs.get("instrumentationLibrarySpans", []):
            for s in ss.get("spans", []):
                attrs = _otlp_attr(s.get("attributes"))
                n = _normalize(attrs)
                dur = (int(s.get("endTimeUnixNano", 0)) - int(s.get("startTimeUnixNano", 0))) // 1000
                tid = s.get("traceId")
                parent = s.get("parentSpanId") or None
                links = s.get("links") or []
                if parent is None and links:    # consumer linked to its producer
                    parent = links[0].get("spanId")
                kind = _OTLP_KIND.get(s.get("kind"))
                err = (s.get("status") or {}).get("code") in (2, "STATUS_CODE_ERROR")
                by_trace.setdefault(tid, []).append(
                    Span(tid, s.get("spanId"), parent, str(svc), s.get("name", ""),
                         max(dur, 0), n["db"], kind, n["dest"], n["rpc"], n["peer"], bool(err)))
    yield from by_trace.values()


def _docs(fh) -> Iterator[dict]:
    """Yield JSON documents from an open file without holding it all in memory.

    OTLP files from the Collector are one export request per line, so they are
    parsed line by line. A pretty-printed or single-document file (typical for
    a Jaeger export) falls back to reading the whole text."""
    first = ""
    for first in fh:
        if first.strip():
            break
    if not first.strip():
        return
    try:
        doc = json.loads(first)
    except json.JSONDecodeError:
        doc = None
    if doc is None:
        yield json.loads(first + fh.read())
        return
    yield doc
    for line in fh:
        if line.strip():
            yield json.loads(line)


def load_file(path: str) -> List[Trace]:
    """Load a Jaeger or OTLP JSON file (OTLP may be newline-delimited).

    Spans of one trace that arrive in different export batches (normal for
    OTLP files) are merged by trace ID."""
    opener = gzip.open if path.endswith(".gz") else open
    jaeger: List[Trace] = []
    otlp: Dict[str, Trace] = {}
    with opener(path, "rt", encoding="utf-8") as fh:
        for d in _docs(fh):
            if "data" in d:
                jaeger.extend(from_jaeger(d))
            elif "resourceSpans" in d:
                for tr in from_otlp(d):
                    otlp.setdefault(tr[0].trace_id, []).extend(tr)
            else:
                raise ValueError(f"{path}: neither Jaeger nor OTLP JSON")
    return jaeger + list(otlp.values())
