"""Stand-in for Online Boutique's cartservice.

The upstream service is written in C#/.NET and keeps carts in Redis
(`redis-cart`). No .NET runtime could be installed in the environment used
for this case study, so this file implements the same gRPC interface
(hipstershop.CartService: AddItem, GetCart, EmptyCart) in Python, against the
same kind of store: a Redis server, one hash per user. It is instrumented the
way the other Python services in the demo are (gRPC server spans), plus the
OpenTelemetry Redis integration, which emits one client span per Redis
command with db.system=redis and the server address and port.
"""
import os
import sys
from concurrent import futures

import grpc
import redis

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "ob", "src", "emailservice"))  # generated stubs
import demo_pb2  # noqa: E402
import demo_pb2_grpc  # noqa: E402
from grpc_health.v1 import health, health_pb2_grpc  # noqa: E402

from opentelemetry import trace  # noqa: E402
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter  # noqa: E402
from opentelemetry.instrumentation.grpc import GrpcInstrumentorServer  # noqa: E402
from opentelemetry.instrumentation.redis import RedisInstrumentor  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: E402

REDIS_ADDR = os.environ.get("REDIS_ADDR", "127.0.0.1:6379")


class Cart(demo_pb2_grpc.CartServiceServicer):
    def __init__(self):
        host, port = REDIS_ADDR.split(":")
        self.r = redis.Redis(host=host, port=int(port))

    def AddItem(self, req, ctx):
        self.r.hincrby(f"cart:{req.user_id}", req.item.product_id, req.item.quantity)
        return demo_pb2.Empty()

    def GetCart(self, req, ctx):
        items = self.r.hgetall(f"cart:{req.user_id}")
        return demo_pb2.Cart(user_id=req.user_id, items=[
            demo_pb2.CartItem(product_id=k.decode(), quantity=int(v)) for k, v in items.items()])

    def EmptyCart(self, req, ctx):
        self.r.delete(f"cart:{req.user_id}")
        return demo_pb2.Empty()


def main():
    if os.environ.get("ENABLE_TRACING") == "1":
        tp = TracerProvider()   # service.name from OTEL_SERVICE_NAME
        tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
            endpoint=os.environ.get("COLLECTOR_SERVICE_ADDR", "localhost:4317"), insecure=True)))
        trace.set_tracer_provider(tp)
        GrpcInstrumentorServer().instrument()
        RedisInstrumentor().instrument()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    demo_pb2_grpc.add_CartServiceServicer_to_server(Cart(), server)
    health_pb2_grpc.add_HealthServicer_to_server(health.HealthServicer(), server)
    port = os.environ.get("PORT", "7070")
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    print(f"cartservice (python stand-in) on :{port}, redis={REDIS_ADDR}", flush=True)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
