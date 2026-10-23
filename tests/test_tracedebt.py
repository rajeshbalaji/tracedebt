"""Unit tests: run with  python -m pytest -q  (or python -m unittest)."""
import gzip
import json
import os
import tempfile
import unittest

from tracedebt.graph import build
from tracedebt.ingest import Span, from_jaeger, from_otlp, load_file
from tracedebt.pipeline import Analyzer
from tracedebt.rules import check
from tracedebt.spec import Spec
from tracedebt.tracker import Tracker

SPEC = Spec.from_dict({
    "system": "t",
    "layers": ["edge", "api", "domain", "data"],
    "services": {
        "gw": {"layer": "edge"},
        "web": {"layer": "api"},
        "order": {"layer": "domain", "context": "sales", "owns": ["orders"]},
        "stock": {"layer": "domain", "context": "inventory", "owns": ["stock"]},
    },
    "dependencies": {"gw": ["web"], "web": ["order"], "order": ["stock"]},
    "ignore": {"services": ["probe"], "operations": ["/health"]},
})


def trace(tid, *calls, db=None):
    """calls: list of service names forming a chain gw -> ... ; db=(svc, name)"""
    spans, parent = [], None
    for i, svc in enumerate(calls):
        sid = f"{tid}-{i}"
        spans.append(Span(tid, sid, parent, svc, "op", 10))
        parent = sid
    if db:
        owner = next(s for s in spans if s.service == db[0])
        spans.append(Span(tid, f"{tid}-db", owner.span_id, db[0], "SELECT", 5, db[1]))
    return spans


def rules_of(cands):
    return sorted({c.rule for c in cands})


class Rules(unittest.TestCase):
    def test_conformant(self):
        g = build("w", [trace("a", "gw", "web", "order", "stock", db=("order", "orders"))], SPEC)
        self.assertEqual(check(g, SPEC), [])

    def test_foreign_data_and_skip(self):
        g = build("w", [trace("a", "gw", "web", db=("web", "stock"))], SPEC)
        self.assertEqual(rules_of(check(g, SPEC)), ["FOREIGN_DATA", "LAYER_SKIP"])

    def test_upward_and_cycle(self):
        g = build("w", [trace("a", "gw", "web", "order", "web")], SPEC)
        self.assertEqual(rules_of(check(g, SPEC)), ["CYCLE", "LAYER_UPWARD"])

    def test_cross_context(self):
        g = build("w", [trace("a", "gw", "web", "order", "stock", "order")], SPEC)
        self.assertIn("UNDECLARED_XCTX", rules_of(check(g, SPEC)))

    def test_unknown_service(self):
        g = build("w", [trace("a", "gw", "web", "order", "ghost")], SPEC)
        self.assertIn("UNKNOWN_SERVICE", rules_of(check(g, SPEC)))

    def test_ignored_probe_and_sidecar(self):
        probe = [Span("p", "1", None, "probe", "probe", 1), Span("p", "2", "1", "order", "/health", 1)]
        side = [Span("s", "1", None, "gw", "op", 1), Span("s", "2", "1", "probe", "op", 1),
                Span("s", "3", "2", "web", "op", 1)]
        g = build("w", [probe, side], SPEC)
        self.assertEqual(g.n_traces, 1)
        self.assertIn(("gw", "web"), g.edges)       # stitched through the ignored hop
        self.assertEqual(check(g, SPEC), [])


MSG_SPEC = Spec.from_dict({
    "layers": ["edge", "api", "domain", "data"],
    "services": {
        "order": {"layer": "domain", "context": "sales", "publishes": ["order-events"]},
        "billing": {"layer": "domain", "context": "billing", "subscribes": ["order-events"]},
        "stock": {"layer": "domain", "context": "inventory"},
    },
    "severity": {"UNDECLARED_XCTX": 1},
})


def msg_trace(tid, producer, topic, consumer):
    return [Span(tid, "1", None, producer, "handle", 10),
            Span(tid, "2", "1", producer, "publish", 2, kind="producer", msg_dest=topic),
            Span(tid, "3", "2", consumer, "consume", 5, kind="consumer", msg_dest=topic)]


class Messaging(unittest.TestCase):
    def test_declared_flow_is_clean(self):
        g = build("w", [msg_trace("a", "order", "order-events", "billing")], MSG_SPEC)
        self.assertIn(("order", "topic:order-events"), g.edges)
        self.assertIn(("topic:order-events", "billing"), g.edges)
        self.assertNotIn(("order", "billing"), g.edges)    # no direct sync edge
        self.assertEqual(check(g, MSG_SPEC), [])

    def test_foreign_publish_and_undeclared_subscriber(self):
        g = build("w", [msg_trace("a", "stock", "order-events", "stock")], MSG_SPEC)
        c = {x.rule: x for x in check(g, MSG_SPEC)}
        self.assertIn("FOREIGN_TOPIC", c)
        self.assertIn("UNDECLARED_XCTX", c)
        self.assertEqual(c["UNDECLARED_XCTX"].severity, 1)  # spec override
        self.assertEqual(c["FOREIGN_TOPIC"].severity, 4)

    def test_unknown_topic(self):
        g = build("w", [msg_trace("a", "order", "mystery", "billing")], MSG_SPEC)
        self.assertIn("UNKNOWN_SERVICE", rules_of(check(g, MSG_SPEC)))


class Bootstrap(unittest.TestCase):
    def test_draft_layers_ownership_and_deps(self):
        from tracedebt.bootstrap import draft
        base = Spec.from_dict({"layers": ["edge", "api", "domain", "data"]})
        ws = []
        for i in range(4):
            tr = [trace(f"{i}{j}", "gw", "web", "order", "stock", db=("order", "orders"))
                  for j in range(3)]
            if i == 0:
                tr.append(trace("odd", "gw", "web", "stock"))     # rare call
            ws.append(build(f"w{i}", tr, base))
        spec, notes = draft(ws)
        sv = spec["services"]
        self.assertEqual(sv["gw"]["layer"], "edge")
        self.assertEqual(sv["web"]["layer"], "api")
        self.assertEqual(sv["stock"]["layer"], "domain")
        self.assertEqual(sv["order"]["owns"], ["orders"])
        self.assertEqual(spec["dependencies"]["web"], ["order"])
        self.assertTrue(any("web -> stock" in n for n in notes))
        Spec.from_dict(spec)                                      # loads cleanly


REAL_SPEC = Spec.from_dict({
    "layers": ["edge", "app", "domain", "data"],
    "services": {
        "frontend": {"layer": "edge"},
        "checkout": {"layer": "app", "context": "ordering"},
        "cart": {"layer": "domain", "context": "ordering", "owns": ["redis-cart"]},
        "shipping": {"layer": "domain", "context": "fulfilment"},
    },
    "dependencies": {"frontend": ["checkout", "cart", "shipping"],
                     "checkout": ["cart", "shipping"]},
    "aliases": {"hipstershop.ShippingService": "shipping",
                "redis://127.0.0.1:6379": "redis-cart"},
    "ignore": {"operations": ["opentelemetry.proto.collector.*"]},
})


class RealTraces(unittest.TestCase):
    def test_uninstrumented_callee_is_inferred(self):
        tr = [Span("t", "1", None, "frontend", "GET", 10),
              Span("t", "2", "1", "frontend", "hipstershop.ShippingService/GetQuote", 3,
                   kind="client", rpc_service="hipstershop.ShippingService",
                   peer="127.0.0.1:50051")]
        g = build("w", [tr], REAL_SPEC)
        self.assertIn(("frontend", "shipping"), g.edges)
        self.assertEqual(g.edges[("frontend", "shipping")].inferred, 1)
        self.assertEqual(check(g, REAL_SPEC), [])

    def test_answered_client_span_is_not_double_counted(self):
        tr = [Span("t", "1", None, "frontend", "GET", 10),
              Span("t", "2", "1", "frontend", "c", 5, kind="client", rpc_service="x.Checkout"),
              Span("t", "3", "2", "checkout", "s", 4, kind="server")]
        g = build("w", [tr], REAL_SPEC)
        self.assertEqual(set(g.edges), {("frontend", "checkout")})
        self.assertEqual(g.edges[("frontend", "checkout")].inferred, 0)

    def test_unknown_rpc_service_surfaces(self):
        tr = [Span("t", "1", None, "frontend", "GET", 10),
              Span("t", "2", "1", "frontend", "c", 5, kind="client",
                   rpc_service="hipstershop.AdService")]
        g = build("w", [tr], REAL_SPEC)
        self.assertIn("UNKNOWN_SERVICE", rules_of(check(g, REAL_SPEC)))

    def test_exporter_self_traffic_ignored(self):
        tr = [Span("t", "1", None, "checkout",
                   "opentelemetry.proto.collector.trace.v1.TraceService/Export", 1,
                   kind="client", rpc_service="opentelemetry.proto.collector.trace.v1.TraceService")]
        g = build("w", [tr], REAL_SPEC)
        self.assertEqual(g.n_traces, 0)
        self.assertEqual(g.edges, {})

    def test_redis_identity_and_foreign_access(self):
        from tracedebt.ingest import _normalize
        n = _normalize({"db.system": "redis", "net.peer.name": "127.0.0.1",
                        "net.peer.port": 6379, "db.redis.database_index": 0})
        self.assertEqual(n["db"], "redis://127.0.0.1:6379")
        tr = [Span("t", "1", None, "checkout", "op", 5),
              Span("t", "2", "1", "checkout", "HGETALL", 1, n["db"], "client")]
        g = build("w", [tr], REAL_SPEC)
        self.assertIn(("checkout", "db:redis-cart"), g.edges)
        self.assertIn("FOREIGN_DATA", rules_of(check(g, REAL_SPEC)))

    def test_new_rpc_conventions(self):
        from tracedebt.ingest import _normalize
        n = _normalize({"rpc.method": "hipstershop.CartService/GetCart",
                        "server.address": "127.0.0.1", "server.port": 7070})
        self.assertEqual(n["rpc"], "hipstershop.CartService")
        self.assertEqual(n["peer"], "127.0.0.1:7070")


class Coverage(unittest.TestCase):
    def test_floor_shrinks_with_volume(self):
        from tracedebt.coverage import q_star, detection_floor
        from tracedebt.graph import WindowGraph
        q = q_star(3, 4)
        self.assertAlmostEqual(4 * q**3 * (1 - q) + q**4, 0.9, places=6)
        small = [WindowGraph(f"w{i}", svc_traces={"a": 10}) for i in range(4)]
        big = [WindowGraph(f"w{i}", svc_traces={"a": 1000}) for i in range(4)]
        self.assertGreater(detection_floor(small, 3, 4)["a"]["p_min"],
                           detection_floor(big, 3, 4)["a"]["p_min"])


class Persistence(unittest.TestCase):
    def test_k_of_m_and_resolution(self):
        bad = [trace("x", "gw", "web", db=("web", "stock"))]
        ok = [trace("y", "gw", "web", "order")]
        t = Tracker(k=3, m=4, close_after=2)
        seq = [bad, ok, bad, bad, bad, ok, ok]
        for i, w in enumerate(seq):
            g = build(f"w{i}", w, SPEC)
            t.update(g, check(g, SPEC))
            if i == 2:
                self.assertEqual(t.active(), [])     # only 2 of last 3 so far
            if i == 3:
                self.assertTrue(t.active())          # 3 of last 4
        self.assertEqual(t.active(), [])             # resolved after 2 clean windows

    def test_one_off_burst_not_reported(self):
        t = Tracker(k=3, m=4)
        for i in range(6):
            w = [trace(str(i), "gw", "web", "order")]
            if i == 2:
                w += [trace("b", "gw", "web", db=("web", "orders"))] * 50
            g = build(f"w{i}", w, SPEC)
            t.update(g, check(g, SPEC))
        self.assertEqual(t.active(), [])


class Ingest(unittest.TestCase):
    def test_jaeger(self):
        doc = {"data": [{"traceID": "t", "processes": {"p1": {"serviceName": "gw"},
                                                         "p2": {"serviceName": "web"}},
                         "spans": [{"traceID": "t", "spanID": "1", "operationName": "a",
                                    "references": [], "duration": 5, "processID": "p1"},
                                   {"traceID": "t", "spanID": "2", "operationName": "b",
                                    "references": [{"refType": "CHILD_OF", "spanID": "1"}],
                                    "duration": 3, "processID": "p2",
                                    "tags": [{"key": "db.name", "value": "orders"}]}]}]}
        (tr,) = list(from_jaeger(doc))
        self.assertEqual([s.service for s in tr], ["gw", "web"])
        self.assertEqual(tr[1].parent_id, "1")
        self.assertEqual(tr[1].db_name, "orders")

    def test_otlp(self):
        doc = {"resourceSpans": [{"resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "web"}}]},
            "scopeSpans": [{"spans": [{"traceId": "t", "spanId": "2", "parentSpanId": "1",
                                       "name": "q", "startTimeUnixNano": "1000",
                                       "endTimeUnixNano": "5000",
                                       "attributes": [{"key": "db.name",
                                                       "value": {"stringValue": "orders"}}]}]}]}]}
        (tr,) = list(from_otlp(doc))
        self.assertEqual(tr[0].service, "web")
        self.assertEqual(tr[0].duration_us, 4)
        self.assertEqual(tr[0].db_name, "orders")


def _otlp_line(tid, sid, parent, svc, extra=()):
    attrs = [{"key": k, "value": {"stringValue": v}} for k, v in extra]
    attrs.append({"key": "http.user_agent", "value": {"stringValue": "ignored"}})
    return {"resourceSpans": [{"resource": {"attributes": [
        {"key": "service.name", "value": {"stringValue": svc}}]},
        "scopeSpans": [{"spans": [{"traceId": tid, "spanId": sid, "parentSpanId": parent,
                                   "name": "op", "kind": 2, "startTimeUnixNano": "0",
                                   "endTimeUnixNano": "2000", "attributes": attrs}]}]}]}


class Loading(unittest.TestCase):
    def test_ndjson_is_streamed_and_batches_merge(self):
        lines = [_otlp_line("t1", "1", "", "gw"), _otlp_line("t1", "2", "1", "web"),
                 _otlp_line("t2", "3", "", "gw")]
        with tempfile.TemporaryDirectory() as d:
            for name, opener in (("w.jsonl", open), ("w.jsonl.gz", gzip.open)):
                with opener(os.path.join(d, name), "wt") as fh:
                    fh.write("\n".join(json.dumps(x) for x in lines) + "\n\n")
                traces = load_file(os.path.join(d, name))
                self.assertEqual(sorted(len(t) for t in traces), [1, 2])

    def test_pretty_printed_single_document(self):
        doc = {"data": [{"traceID": "a", "processes": {"p1": {"serviceName": "gw"}},
                         "spans": [{"traceID": "a", "spanID": "1", "processID": "p1",
                                    "operationName": "op", "duration": 5, "tags": []}]}]}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "j.json")
            with open(path, "w") as fh:
                json.dump(doc, fh, indent=2)
            (tr,) = load_file(path)
        self.assertEqual(tr[0].service, "gw")

    def test_only_needed_attributes_are_read(self):
        doc = _otlp_line("t", "1", "", "web", [("db.name", "orders"), ("rpc.service", "x.Svc")])
        (tr,) = list(from_otlp(doc))
        self.assertEqual((tr[0].db_name, tr[0].rpc_service), ("orders", "x.Svc"))


class EvidenceTrace(unittest.TestCase):
    def test_trace_split_across_windows_is_reassembled_and_marked(self):
        from tracedebt.evidence import find, render
        first = [_otlp_line("t9", "1", "", "gw"), _otlp_line("t9", "2", "1", "web")]
        second = [_otlp_line("t9", "3", "2", "web", [("db.name", "stock")]),
                  _otlp_line("other", "8", "", "gw")]
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for n, lines in (("w0.jsonl", first), ("w1.jsonl", second)):
                p = os.path.join(d, n)
                with open(p, "w") as fh:
                    fh.write("\n".join(json.dumps(x) for x in lines) + "\n")
                paths.append(p)
            tr, src = find("t9", paths)
            self.assertEqual((len(tr), len(src)), (3, 2))
            out = render(tr, SPEC, path_only=True, sources=src)
            marked = [ln for ln in out if "<==" in ln]
            self.assertEqual(len(marked), 1)
            self.assertIn("FOREIGN_DATA", marked[0])
            self.assertIn("web -> db:stock", marked[0])
            self.assertEqual(find("nope", paths), (None, []))


class CommandLine(unittest.TestCase):
    def test_wildcards_are_expanded_by_the_tool_and_missing_files_are_reported(self):
        from tracedebt.cli import _expand
        with tempfile.TemporaryDirectory() as d:
            for n in ("w2.json", "w10.json", "w1.json", "x.txt"):
                open(os.path.join(d, n), "w").close()
            got = [os.path.basename(x) for x in _expand([os.path.join(d, "w*.json")])]
            self.assertEqual(got, ["w1.json", "w10.json", "w2.json"])
            self.assertEqual(_expand([os.path.join(d, "x.txt")]), [os.path.join(d, "x.txt")])
            with self.assertRaises(SystemExit):
                _expand([os.path.join(d, "none*.json")])
            with self.assertRaises(SystemExit):
                _expand([os.path.join(d, "missing.json")])


class Evolution(unittest.TestCase):
    """An approved dependency: no finding when the spec is updated with the change,
    and an open finding closes once the spec catches up."""

    def _spec(self, deps):
        d = {"layers": ["edge", "api", "domain", "data"],
             "services": {"gw": {"layer": "edge"}, "web": {"layer": "api"},
                          "order": {"layer": "domain", "context": "sales"},
                          "stock": {"layer": "domain", "context": "inventory"}},
             "dependencies": deps}
        return Spec.from_dict(d)

    def _run(self, update_at):
        old = self._spec({"gw": ["web"], "web": ["order"]})
        new = self._spec({"gw": ["web"], "web": ["order"], "order": ["stock"]})
        an = Analyzer(old)
        for i in range(8):
            if update_at is not None and i == update_at:
                an.set_spec(new)
            an.feed(f"w{i}", [trace(f"{i}a", "gw", "web", "order", "stock")])
        return an.finish().tracker

    def test_updated_with_the_change(self):
        tr = self._run(0)
        self.assertEqual(tr.findings, {})

    def test_state_file_continues_across_invocations(self):
        old = self._spec({"gw": ["web"], "web": ["order"]})
        new = self._spec({"gw": ["web"], "web": ["order"], "order": ["stock"]})
        whole = self._run(4)
        with tempfile.TemporaryDirectory() as d:
            st = os.path.join(d, "state.json")
            a = Analyzer(old)
            for i in range(4):
                a.feed(f"w{i}", [trace(f"{i}a", "gw", "web", "order", "stock")])
            a.finish()
            a.save_state(st)
            b = Analyzer(new)               # a later job: new spec, new process
            b.load_state(st)
            for i in range(4, 8):
                b.feed(f"w{i}", [trace(f"{i}a", "gw", "web", "order", "stock")])
            part = b.finish().tracker
            self.assertEqual(
                {k: (f.confirmed_at, f.resolved_at) for k, f in part.findings.items()},
                {k: (f.confirmed_at, f.resolved_at) for k, f in whole.findings.items()})
            with self.assertRaises(ValueError):
                Analyzer(new, k=2).load_state(st)

    def test_stale_spec_confirms_then_late_update_closes(self):
        never = self._run(None)
        (f,) = never.findings.values()
        self.assertEqual((f.rule, f.confirmed_at, f.resolved_at), ("UNDECLARED_XCTX", "w2", None))
        late = self._run(4)
        (f,) = late.findings.values()
        self.assertEqual((f.confirmed_at, f.resolved_at), ("w2", "w6"))


if __name__ == "__main__":
    unittest.main()
