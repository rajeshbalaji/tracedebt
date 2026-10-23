# TraceDebt report: online-boutique

Windows analysed: 24 (w00 .. w23)

**Drift indicators** severity 14, exposure 6.23; 63.3% of traces and 10.7% of latency touch drifted edges. (Prioritization aids, not cost estimates.)

## Active drift

| Sev | Rule | Where | Exposure | Since | Evidence |
|---:|---|---|---:|---|---|
| 5 | FOREIGN_DATA (reads/writes a datastore owned by another service) | recommendationservice → db:redis-cart | 61.45% | w08 | `36cfe079bb413c04a109c5e053514a82` |
| 4 | CYCLE (runtime dependency cycle) | productcatalogservice → recommendationservice | 51.44% | w20 | `4851362f8237f331c7d6c5c5bb88b560` |
| 3 | UNDECLARED_XCTX (undeclared call across bounded contexts) | emailservice → productcatalogservice | 2.38% | w14 | `15d1426139325ab4a1fedfca3e92a63c` |
| 2 | UNDECLARED (undeclared call) | productcatalogservice → recommendationservice | 51.44% | w20 | `4851362f8237f331c7d6c5c5bb88b560` |

## Seen from the caller only

These calls appear only as client spans: the callee did not continue the trace (it is not instrumented, or context propagation is broken), so it was inferred from the rpc service or peer address via `aliases`.

- checkoutservice → currencyservice
- checkoutservice → paymentservice
- checkoutservice → shippingservice
- frontend → adservice
- frontend → currencyservice
- frontend → shippingservice

## Coverage

With current trace volume, a drifted call that occurs in fewer than the share of a service's traces shown below would probably not be confirmed (90% target, k=3 of m=4). A quiet report is not evidence of conformance below these rates.

| Service | Traces/window | Rarest reliably confirmed |
|---|---:|---:|
| paymentservice | 21.8 | 8.6% |
| emailservice | 22.2 | 8.4% |
| checkoutservice | 100.8 | 1.9% |

## Triage

Each finding usually ends in one of three ways: (1) a genuine violation, fixed by a code change; (2) an intended dependency the spec has not caught up with, fixed by declaring it; (3) unclear, for example a fallback path or a batch job, which needs someone to look at the evidence trace before deciding. Use `ignore` only for infrastructure traffic, not to silence (3).
