"""Minimal OTLP/gRPC trace receiver.

Listens on :4317 like an OpenTelemetry Collector and appends every export
request, as one OTLP/JSON line, to the file named in `current_window`
(re-read on every request, so the case-study driver can rotate windows by
rewriting that file). IDs are written as lowercase hex as the OTLP/JSON
spec requires.
"""
import base64
import json
import os
import sys
import threading
from concurrent import futures

import grpc
from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2, trace_service_pb2_grpc

CTL = sys.argv[1] if len(sys.argv) > 1 else "current_window"
LOCK = threading.Lock()


def _hex(b64):
    return base64.b64decode(b64).hex() if b64 else b64


def _fix_ids(d):
    for rs in d.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            for s in ss.get("spans", []):
                for k in ("traceId", "spanId", "parentSpanId"):
                    if k in s:
                        s[k] = _hex(s[k])
                for l in s.get("links", []) or []:
                    for k in ("traceId", "spanId"):
                        if k in l:
                            l[k] = _hex(l[k])
    return d


class Svc(trace_service_pb2_grpc.TraceServiceServicer):
    def Export(self, request, context):
        d = _fix_ids(MessageToDict(request))
        try:
            path = open(CTL).read().strip()
        except OSError:
            path = ""
        if path:
            with LOCK, open(path, "a") as fh:
                fh.write(json.dumps(d) + "\n")
        return trace_service_pb2.ExportTraceServiceResponse()


def main():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=16),
                         options=[("grpc.max_receive_message_length", 64 * 1024 * 1024)])
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(Svc(), server)
    server.add_insecure_port("127.0.0.1:4317")
    server.start()
    print("otlp receiver on :4317, control file", os.path.abspath(CTL), flush=True)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
