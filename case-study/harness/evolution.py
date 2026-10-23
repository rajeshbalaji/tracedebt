"""Second Online Boutique run: an approved architectural change.

Three phases of six one-minute windows:

  v0  base commit
  e1  + evolve-1  recommendationservice asks cartservice (through its API)
                  which products are already in the cart; the architecture
                  review accepted this dependency, and the specification is
                  updated in the same change
  e2  + drift-2   emailservice calls productcatalogservice (a violation)
                  deployed after the approved change

Same stack, load and recorder as casestudy.py.
"""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))
import casestudy as cs  # noqa: E402
import loadgen  # noqa: E402
import stack  # noqa: E402

WIN = f"{stack.ROOT}/windows/ev"
PHASES = [
    ("v0", None),
    ("e1", lambda: cs.redeploy("recommendationservice", "evolve-1",
                               {"CART_SERVICE_ADDR": "127.0.0.1:7070"})),
    ("e2", lambda: cs.redeploy("emailservice", "drift-2",
                               {"PRODUCT_CATALOG_SERVICE_ADDR": "127.0.0.1:3550"})),
]


def main():
    os.makedirs(WIN, exist_ok=True)
    for f in os.listdir(WIN):
        os.remove(os.path.join(WIN, f))
    for s in ("recommendationservice", "emailservice", "productcatalogservice"):
        cs.git("checkout", cs.BASE_COMMIT, "--", f"src/{s}")
    stack.down()
    stack.up()
    time.sleep(5)
    total = cs.WINDOW_S * cs.PER_PHASE * len(PHASES) + 600
    threading.Thread(target=loadgen.run, args=("http://127.0.0.1:8080", 10, total, (0.5, 2.0), 7),
                     daemon=True).start()
    time.sleep(10)
    manifest, w = [], 0
    for phase, deploy in PHASES:
        if deploy:
            open(f"{stack.ROOT}/current_window", "w").write("")
            deploy()
            time.sleep(3)
        for _ in range(cs.PER_PHASE):
            path = f"{WIN}/w{w:02d}.jsonl"
            open(f"{stack.ROOT}/current_window", "w").write(path)
            t0 = time.time()
            time.sleep(cs.WINDOW_S)
            manifest.append({"window": f"w{w:02d}", "phase": phase, "start": t0,
                             "end": time.time(), "load": dict(loadgen.STATS)})
            print(f"{phase} w{w:02d} done; {loadgen.STATS}", flush=True)
            w += 1
    open(f"{stack.ROOT}/current_window", "w").write("")
    time.sleep(6)
    json.dump(manifest, open(f"{WIN}/manifest.json", "w"), indent=1)
    cs.git("checkout", "casestudy", "--", "src")
    stack.down()
    print("done")


if __name__ == "__main__":
    main()
