"""Start/stop Online Boutique natively (no containers) with tracing on.

Service configuration follows the repository's kubernetes-manifests and the
google-cloud-operations kustomize component (ENABLE_TRACING=1,
COLLECTOR_SERVICE_ADDR, OTEL_SERVICE_NAME), with ports changed where two
services would collide on one host. adservice (Java) is not started: its
Gradle build could not fetch dependencies here; the frontend tolerates a
missing ad service and only logs a warning.

    python stack.py up      # start receiver + services, wait until healthy
    python stack.py down
"""
import os
import signal
import socket
import subprocess
import sys
import time

ROOT = "/home/claude/real"
SRC = f"{ROOT}/ob/src"
BIN = f"{ROOT}/bin"
PY = f"{ROOT}/venv/bin/python"
LOGS = f"{ROOT}/logs"
PIDS = f"{ROOT}/pids"
COLLECTOR = "127.0.0.1:4317"

BASE = {"ENABLE_TRACING": "1", "COLLECTOR_SERVICE_ADDR": COLLECTOR,
        "DISABLE_PROFILER": "1", "ENABLE_PROFILER": "0", "GCP_PROJECT_ID": "local",
        "NO_PROXY": "*", "no_proxy": "*", "HTTPS_PROXY": "", "https_proxy": ""}

SERVICES = [
    ("redis-cart", [f"{ROOT}/redis-src/src/redis-server", "--port", "6379", "--bind", "127.0.0.1",
                    "--save", "", "--appendonly", "no"], ROOT, {}, 6379),
    ("productcatalogservice", [f"{BIN}/productcatalogservice"], f"{SRC}/productcatalogservice",
     {"PORT": "3550"}, 3550),
    ("currencyservice", ["node", "-r", f"{ROOT}/tools/ipv4_bind.js", "server.js"],
     f"{SRC}/currencyservice", {"PORT": "7000"}, 7000),
    ("paymentservice", ["node", "-r", f"{ROOT}/tools/ipv4_bind.js", "index.js"],
     f"{SRC}/paymentservice", {"PORT": "50052"}, 50052),
    ("shippingservice", [f"{BIN}/shippingservice"], f"{SRC}/shippingservice",
     {"PORT": "50051", "DISABLE_TRACING": "1"}, 50051),
    ("emailservice", [PY, "email_server.py"], f"{SRC}/emailservice", {"PORT": "5000"}, 5000),
    ("cartservice", [PY, "cart_server.py"], f"{ROOT}/cart", {"PORT": "7070", "REDIS_ADDR": "127.0.0.1:6379"}, 7070),
    ("recommendationservice", [PY, "recommendation_server.py"], f"{SRC}/recommendationservice",
     {"PORT": "8081", "PRODUCT_CATALOG_SERVICE_ADDR": "127.0.0.1:3550"}, 8081),
    ("checkoutservice", [f"{BIN}/checkoutservice"], f"{SRC}/checkoutservice",
     {"PORT": "5050", "PRODUCT_CATALOG_SERVICE_ADDR": "127.0.0.1:3550",
      "SHIPPING_SERVICE_ADDR": "127.0.0.1:50051", "PAYMENT_SERVICE_ADDR": "127.0.0.1:50052",
      "EMAIL_SERVICE_ADDR": "127.0.0.1:5000", "CURRENCY_SERVICE_ADDR": "127.0.0.1:7000",
      "CART_SERVICE_ADDR": "127.0.0.1:7070"}, 5050),
    ("frontend", [f"{BIN}/frontend"], f"{SRC}/frontend",
     {"PORT": "8080", "PRODUCT_CATALOG_SERVICE_ADDR": "127.0.0.1:3550",
      "CURRENCY_SERVICE_ADDR": "127.0.0.1:7000", "CART_SERVICE_ADDR": "127.0.0.1:7070",
      "RECOMMENDATION_SERVICE_ADDR": "127.0.0.1:8081", "SHIPPING_SERVICE_ADDR": "127.0.0.1:50051",
      "CHECKOUT_SERVICE_ADDR": "127.0.0.1:5050", "AD_SERVICE_ADDR": "127.0.0.1:9555",
      "SHOPPING_ASSISTANT_SERVICE_ADDR": "127.0.0.1:9556"}, 8080),
]

# shippingservice and cartservice(upstream C#) have no OpenTelemetry code in
# this release; the Go shipping service is run as-is.
NO_OTEL_NAME = {"shippingservice", "redis-cart"}


def _wait(port, t=60):
    end = time.time() + t
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), 1).close()
            return True
        except OSError:
            time.sleep(0.3)
    return False


def _start(name, cmd, cwd, env, port):
    e = dict(os.environ)
    e.update(BASE)
    e.update(env)
    if name not in NO_OTEL_NAME:
        e["OTEL_SERVICE_NAME"] = name
    log = open(f"{LOGS}/{name}.log", "a")
    p = subprocess.Popen(cmd, cwd=cwd, env=e, stdout=log, stderr=subprocess.STDOUT,
                         start_new_session=True)
    open(f"{PIDS}/{name}.pid", "w").write(str(p.pid))
    return p


def up(only=None):
    os.makedirs(LOGS, exist_ok=True)
    os.makedirs(PIDS, exist_ok=True)
    if only is None and not os.path.exists(f"{PIDS}/receiver.pid"):
        e = dict(os.environ, NO_PROXY="*", no_proxy="*")
        p = subprocess.Popen([PY, f"{ROOT}/tools/otlp_receiver.py", f"{ROOT}/current_window"],
                             cwd=ROOT, env=e, stdout=open(f"{LOGS}/receiver.log", "a"),
                             stderr=subprocess.STDOUT, start_new_session=True)
        open(f"{PIDS}/receiver.pid", "w").write(str(p.pid))
        assert _wait(4317), "receiver did not start"
    for name, cmd, cwd, env, port in SERVICES:
        if only and name not in only:
            continue
        _start(name, cmd, cwd, env, port)
        if not _wait(port):
            print(f"!! {name} not listening on {port}; see {LOGS}/{name}.log")
    print("stack up")


def down(only=None):
    for f in sorted(os.listdir(PIDS)) if os.path.isdir(PIDS) else []:
        name = f[:-4]
        if only and name not in only:
            continue
        try:
            os.killpg(int(open(f"{PIDS}/{f}").read()), signal.SIGTERM)
        except (ProcessLookupError, ValueError):
            pass
        os.remove(f"{PIDS}/{f}")
    time.sleep(1)


if __name__ == "__main__":
    cmd = sys.argv[1]
    only = sys.argv[2:] or None
    {"up": up, "down": down}[cmd](only)
