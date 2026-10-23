# Case study: Online Boutique

This folder holds everything needed to repeat the real-system case study in
the paper, plus the traces it produced.

## What was run

- **System.** Google's Online Boutique, release `v0.10.7`
  (github.com/GoogleCloudPlatform/microservices-demo), built from source and
  run natively without containers. Its own OpenTelemetry code was used,
  configured as in the repository's `google-cloud-operations` kustomize
  component (`ENABLE_TRACING=1`, `COLLECTOR_SERVICE_ADDR`, `OTEL_SERVICE_NAME`).
- **Services.** frontend, checkout, productcatalog, shipping (Go); currency,
  payment (Node.js); email, recommendation (Python); Redis for carts.
- **Deviations from upstream**, all forced by the build environment (no
  container registry, no Go module proxy, no Maven, no .NET):
  - Go dependencies were fetched from their GitHub source repositories via
    `replace` directives (patch 0001); no code changes.
  - `cartservice` (C#) is replaced by `harness/cart_server.py`, the same gRPC
    interface over Redis, instrumented like the other Python services.
  - `adservice` (Java) is not run; the frontend logs a warning and carries on.
    Its calls still appear as failed client spans.
  - The Node services bind `0.0.0.0` instead of `[::]` (no IPv6 in the
    container), via `harness/ipv4_bind.js`; no code changes.
- **Collector.** `harness/otlp_receiver.py`, a minimal OTLP/gRPC receiver that
  writes one OTLP/JSON file per window.
- **Load.** `harness/loadgen.py`, the upstream locustfile's tasks and weights,
  10 users, 0.5–2 s think time.
- **Schedule.** Five phases of six one-minute windows (`harness/casestudy.py`):

  | Phase | Windows | Deployed change (git) |
  |---|---|---|
  | v0 | w00–w05 | base; w03 also runs a one-off backfill job reading Redis directly |
  | v1 | w06–w11 | patch 0002: recommendationservice reads `redis-cart` directly |
  | v2 | w12–w17 | patch 0003: emailservice calls productcatalogservice |
  | v3 | w18–w23 | patch 0004: productcatalogservice calls recommendationservice (25%) |
  | v4 | w24–w29 | patch 0005: revert of 0002 |

  The intended architecture, `../examples/online-boutique/spec.yaml`: its
  layers, contexts, datastore owner and dependencies were written from the
  project README before any trace was analysed. The `aliases` block was added
  afterwards, once we had seen how the services' telemetry names each other.

## Second run: an approved change

`harness/evolution.py` runs 18 windows in three phases of six, on the same stack
and load:

| Phase | Windows | Deployed change (git) |
|---|---|---|
| v0 | w00–w05 | base |
| e1 | w06–w11 | patch 0006: recommendationservice asks cartservice which products are in the cart (accepted dependency) |
| e2 | w12–w17 | patch 0003 again: emailservice calls productcatalogservice (a violation) |

`../examples/online-boutique/spec_evolved.yaml` differs from `spec.yaml` by one
dependency (`recommendationservice -> cartservice`). Traces are in
`windows-evolution/`; the analysis runs the traces three ways (specification
updated with the change, updated four windows late, never updated):

    python -m bench.case_evolution case-study/windows-evolution --out results/case_evolution

## Reproducing the analysis

    python -m bench.case_online_boutique case-study/windows --out results/case
    python bench/plot_case.py results/case/case.json

`windows/` holds the 30 windows as gzipped OTLP/JSON plus `manifest.json`.
`windows-run1/` is an earlier full run of the same schedule, in which the
backfill job failed to export its spans; detection results were the same.

## Re-running the system

Clone microservices-demo at `v0.10.7`, apply the patches with `git am`, tag
the drift commits `drift-1`, `drift-2`, `drift-3`, the revert `revert-1` and
the approved change `evolve-1` (patch 0006),
build the Go services (patch 0001 makes this work without the module proxy),
install the Node and Python dependencies, build Redis, then adjust the paths
at the top of `harness/stack.py` and run `python harness/casestudy.py` (or `harness/evolution.py`).
