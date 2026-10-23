"""Drive the Online Boutique case study.

Five phases of six one-minute windows each, with load running throughout:

  v0  base (build commit only)          window w03 also runs a one-off backfill job
  v1  + drift-1  recommendation reads redis-cart directly
  v2  + drift-2  emailservice calls productcatalogservice
  v3  + drift-3  productcatalogservice calls recommendationservice (25%)
  v4  revert of drift-1

A "deployment" checks out the service's source at the phase's git tag and
restarts only that service, with the configuration the change needs.
All traces are written, unsampled, as OTLP/JSON, one file per window.
"""
import json
import os
import random
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))
import loadgen  # noqa: E402
import stack  # noqa: E402

ROOT = stack.ROOT
OB = f"{ROOT}/ob"
WIN = f"{ROOT}/windows/cs"
WINDOW_S = int(os.environ.get("WINDOW_S", "60"))
PER_PHASE = 6
BASE_COMMIT = "23f5b0ae"


def git(*a):
    subprocess.run(["git", "-C", OB, *a], check=True, capture_output=True)


def svc_entry(name):
    for i, e in enumerate(stack.SERVICES):
        if e[0] == name:
            return i, e
    raise KeyError(name)


def redeploy(name, tag=None, env=None, cmd=None):
    if tag:
        git("checkout", tag, "--", f"src/{name}")
    i, (n, c, cwd, e, port) = svc_entry(name)
    e = dict(e, **(env or {}))
    stack.SERVICES[i] = (n, cmd or c, cwd, e, port)
    stack.down([name])
    stack.up([name])


def backfill_job():
    """One-off maintenance script: walks every cart straight in Redis."""
    py = stack.PY
    code = r'''
import os, redis
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.redis import RedisInstrumentor
tp = TracerProvider(); tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint="127.0.0.1:4317", insecure=True)))
trace.set_tracer_provider(tp); RedisInstrumentor().instrument()
r = redis.Redis(host="127.0.0.1", port=6379); t = trace.get_tracer("backfill")
for key in r.scan_iter("cart:*"):
    with t.start_as_current_span("backfill-cart"):
        r.hgetall(key)
tp.shutdown()
'''
    env = dict(os.environ, OTEL_SERVICE_NAME="cart-backfill", NO_PROXY="*", no_proxy="*",
               HTTPS_PROXY="", https_proxy="", grpc_proxy="")
    r = subprocess.run([py, "-c", code], env=env, timeout=120, capture_output=True, text=True)
    open(f"{stack.LOGS}/backfill.log", "w").write(r.stdout + r.stderr)


PHASES = [
    ("v0", None),
    ("v1", lambda: redeploy("recommendationservice", "drift-1", {"CART_REDIS_ADDR": "127.0.0.1:6379"})),
    ("v2", lambda: redeploy("emailservice", "drift-2", {"PRODUCT_CATALOG_SERVICE_ADDR": "127.0.0.1:3550"})),
    ("v3", lambda: redeploy("productcatalogservice", "drift-3",
                            {"RECOMMENDATION_SERVICE_ADDR": "127.0.0.1:8081"},
                            [f"{stack.BIN}/productcatalogservice-d3"])),
    ("v4", lambda: redeploy("recommendationservice", "revert-1")),
]


def main():
    os.makedirs(WIN, exist_ok=True)
    for f in os.listdir(WIN):
        os.remove(os.path.join(WIN, f))
    # start from the base commit
    for s in ("recommendationservice", "emailservice", "productcatalogservice"):
        git("checkout", BASE_COMMIT, "--", f"src/{s}")
    stack.down()
    stack.up()
    time.sleep(5)
    stop = time.time() + WINDOW_S * PER_PHASE * len(PHASES) + 600
    load = threading.Thread(target=loadgen.run,
                            args=("http://127.0.0.1:8080", 10, stop - time.time(), (0.5, 2.0), 7),
                            daemon=True)
    load.start()
    time.sleep(10)                     # warm-up, not recorded
    manifest = []
    w = 0
    for phase, deploy in PHASES:
        if deploy:
            ctl_file = open(f"{ROOT}/current_window", "w")
            ctl_file.write("")         # nothing recorded during the restart
            ctl_file.close()
            deploy()
            time.sleep(3)
        for j in range(PER_PHASE):
            path = f"{WIN}/w{w:02d}.jsonl"
            open(f"{ROOT}/current_window", "w").write(path)
            t0 = time.time()
            if phase == "v0" and j == 3:
                threading.Thread(target=backfill_job, daemon=True).start()
            time.sleep(WINDOW_S)
            manifest.append({"window": f"w{w:02d}", "phase": phase, "start": t0,
                             "end": time.time(), "load": dict(loadgen.STATS)})
            print(f"{phase} w{w:02d} done; requests so far {loadgen.STATS}", flush=True)
            w += 1
    open(f"{ROOT}/current_window", "w").write("")
    time.sleep(6)
    json.dump(manifest, open(f"{WIN}/manifest.json", "w"), indent=1)
    git("checkout", "casestudy", "--", "src")
    stack.down()
    print("done")


if __name__ == "__main__":
    main()
