# TraceDebt

Runtime-guided detection of architectural drift in evolving microservices.

TraceDebt checks distributed traces (Jaeger JSON or OTLP JSON) against a small
YAML description of the architecture your team intended: layers, bounded
contexts, datastore and event-topic ownership, and declared dependencies.
It covers synchronous calls, datastore access and publish/subscribe messaging. It reports drift only
after it has persisted for k of the last m observation windows, attaches an
evidence trace ID to each finding, and summarizes the state as a drift-debt
figure that a CI job can gate on.

It has been run on Online Boutique with that project's own OpenTelemetry
instrumentation; see `case-study/`.

## Install

    git clone https://github.com/rajeshbalaji/tracedebt && cd tracedebt
    pip install .                 # installs the `tracedebt` command; PyYAML is the only dependency

Python 3.10+. Without installing, `pip install pyyaml` and use
`python -m tracedebt` in place of `tracedebt` everywhere below.

## Quick start

    python -m examples.make_example examples/demo
    tracedebt analyze examples/demo/spec.yaml examples/demo/w*.json \
        --md report.md --json report.json --dot drift.dot --dot-focus --fail-on 4
    dot -Tpdf drift.dot -o drift.pdf          # optional, needs Graphviz

The demo injects a foreign-datastore access and an undeclared cross-context
call from window w2 on, a publish into another service's topic from w3 on,
and a one-off burst in w1. The report confirms the first two at w4 and the
topic drift at w5, and ignores the burst.

## No spec yet? Draft one

    tracedebt init baseline/w*.json --out spec.draft.yaml \
        --ignore-service mesh-probe --ignore-op /health
    # on telemetry with odd names, reuse the aliases/ignore of a partial spec:
    tracedebt init baseline/w*.jsonl.gz --base spec.partial.yaml --out spec.draft.yaml

The draft layers services by hop distance from entry points, assigns
datastore owners, declares calls seen in at least half of the windows and
turns producer/consumer spans into `publishes`/`subscribes`. Review notes
(shared datastores, occasional calls) are written at the top of the file.
Bounded contexts are left for you to add.

## Specification

```yaml
system: shop
layers: [edge, api, domain, data]
allow_layer_skip: false
services:
  gateway: {layer: edge}
  web-bff: {layer: api}
  order:   {layer: domain, context: sales, owns: [order-db],
            publishes: [order-events]}
  payment: {layer: domain, context: billing, owns: [pay-db],
            subscribes: [order-events]}
dependencies:
  gateway: [web-bff]
  web-bff: [order]
  order:   [payment]
topics:                      # optional; defaults to the single publisher
  order-events: {owner: order}
severity:                    # optional overrides of the defaults below
  UNDECLARED: 1
ignore:
  services: [mesh-probe]
  operations: [/health]
```

## Trace formats and the attributes TraceDebt reads

Input: Jaeger JSON (query API / UI export) or OTLP/JSON (Collector file
exporter, one export request per line; spans of one trace spread over several
lines are merged by trace ID). Only these fields are used:

| Purpose | Attributes, in order of preference |
|---|---|
| service | resource `service.name` (Jaeger: process `serviceName`), then `aliases` |
| span kind | OTLP `kind`, Jaeger `span.kind` tag |
| parent | `parentSpanId` / `CHILD_OF`; if absent, first span link / `FOLLOWS_FROM` |
| datastore | `db.name`, `db.namespace`, `db.instance`; if all empty: `<db.system>://<host>:<port>` from `server.address`/`net.peer.name` and `server.port`/`net.peer.port` |
| topic | `messaging.destination.name`, `messaging.destination` on producer/consumer spans |
| callee of an unanswered client span | `rpc.service`, or the part of `rpc.method` before `/`; then `peer.service`; then host:port |

Missing or broken pieces of a trace are handled as follows:

- **Missing parent span** (dropped by sampling, or lost in transit): the span
  starts its own subtree; no edge is invented for it.
- **Uninstrumented callee**: the caller's client span has no child from
  another service, so the callee is inferred from the fields above and mapped
  through `aliases` (for example `hipstershop.CartService: cartservice`).
  Unresolved names show up as `UNKNOWN_SERVICE`, which is usually the cue to
  add an alias. Reports list these "seen from the caller only" edges.
- **Broken context propagation** (callee spans start new traces): same as an
  uninstrumented callee from the caller's side; the callee's orphan traces
  still count towards its traffic.
- **Sidecars and proxies**: list them under `ignore.services`; a span whose
  parent is ignored is linked to the nearest kept ancestor.
- **Telemetry self-traffic** (exporters tracing their own exports, health
  checks): list the operations under `ignore.operations` (glob patterns).

A worked example for a real system, Online Boutique, is in
`examples/online-boutique/spec.yaml`.

## Rules

| Rule | Severity |
|---|---|
| FOREIGN_DATA – datastore owned by another service | 5 |
| FOREIGN_TOPIC – publishes to another service's topic | 4 |
| LAYER_UPWARD – lower layer calls a higher one | 4 |
| CYCLE – runtime cycle between services | 4 |
| LAYER_SKIP – bypasses an intermediate layer | 3 |
| UNKNOWN_SERVICE – service or topic missing from the spec | 3 |
| UNDECLARED_XCTX – undeclared call across contexts | 3 |
| UNDECLARED – undeclared call within a context | 2 |

Options: `--k`, `--m` (persistence, default 3 of 4), `--close-after`
(default 3), `--min-traces` (support floor, default 1).

## Reading a report

- **Drift indicators.** `severity` is the sum of severity weights of active
  findings; `exposure` weights each by the share of traces that touch it. They
  are for ordering work, not estimates of cost or effort.
- **Coverage.** For each service, the rarest drifted call that would still be
  confirmed with 90% probability at the current trace volume. Below that
  rate, a quiet report says little.
- **Triage.** A finding usually ends one of three ways: a genuine violation
  (change the code), an intended dependency the spec has not caught up with
  (declare it), or something unclear such as a fallback path or batch job
  (look at the evidence trace before deciding). Keep `ignore` for
  infrastructure traffic.

## Choosing a window

A window is whatever slice of traces you feed in, so detection delay is
`k` windows of wall-clock time at best. Pick the window so that each service
of interest has a few hundred traces in it (see the coverage table);
with 10 users on Online Boutique, one minute was enough. Tail sampling keeps
errors and slow traces preferentially, which over-represents some paths;
analyse a head-sampled or unsampled stream if you have one.

## Running continuously, and approved changes

Each `analyze` run replays the windows it is given. For a nightly or per-release
job, add `--state state.json`: the tracker's history is read from that file if
it exists and written back afterwards, so you feed only the new windows.
`--k`, `--m` and `--close-after` must match the run that wrote the file.

The specification lives in the repository, so an intended change updates it in
the same commit. The next run reads the new file: findings for a dependency that
is now declared stop firing, and an open finding closes after `--close-after`
clean windows (three by default). Example from the case study, in two jobs:

    tracedebt analyze examples/online-boutique/spec.yaml \
        case-study/windows-evolution/w0[0-9].jsonl.gz --state state.json
    tracedebt analyze examples/online-boutique/spec_evolved.yaml \
        case-study/windows-evolution/w1[0-7].jsonl.gz --state state.json

`spec_evolved.yaml` is `spec.yaml` plus one approved dependency. The first job
confirms recommendationservice -> cartservice (w08); the second closes it (w12)
and confirms the later emailservice violation (w14). `bench/case_evolution.py`
runs the three cases (updated with the change, four windows late, never) in one
process.

## Memory and speed

Files are read one window at a time, so memory follows the size of a window,
not of the run: about 0.6 KB per span while a window is held (317 MB for 539k
spans as one window, 52 MB for the 30 one-minute windows of the case study).
Analysis runs at roughly 0.45 million spans per second on one core; with
decompression and JSON parsing the end-to-end rate is about 70k spans per
second. Cut very large windows into several files if memory matters, keeping
in mind that persistence counts files.

## Reproducing the paper

    python -m unittest tests.test_tracedebt
    python -m bench.case_online_boutique case-study/windows --out results/case
    python bench/plot_case.py results/case/case.json
    python -m bench.case_evolution case-study/windows-evolution --out results/case_evolution
    python -m bench.coverage_check              # coverage formula vs. tracker, ~10 min
    python -m bench.coverage_check --sigma 0.8 --configs S:0.1,M:0.1,M:0.05 \
        --out results/coverage_check_uneven.json          # uneven traffic
    python -m bench.coverage_check --intermittent 0.5 --configs S:0.1,M:0.1,M:0.05 \
        --out results/coverage_check_intermittent.json    # drift on in half the windows
    python -m bench.sensitivity                 # precision vs. spec lag, ~5 min
    python -m bench.ingest_profile case-study/windows --mode merged   # parse time, memory
    python -m bench.run_eval --out results      # ~10 min on one core
    python -m bench.fp_sources                  # false-positive attribution
    python bench/plots.py                       # results/fig_eval.pdf
    python -m bench.throughput case-study/windows   # timing, on an idle machine

`results/` in this archive holds the exact outputs used in the paper.

## Layout

    tracedebt/   spec, ingest, graph, rules, tracker, debt, coverage, report, bootstrap, cli
    bench/       benchmark generator, evaluation harness, case-study analysis, plots
    case-study/  Online Boutique harness, drift patches, traces (see its README)
    examples/    synthetic demo; Online Boutique spec and expected report
    docs/        screencast script
    tests/       unit tests

License: MIT.
