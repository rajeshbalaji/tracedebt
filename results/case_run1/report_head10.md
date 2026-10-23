# TraceDebt report: online-boutique

Windows analysed: 30 (w00 .. w29)

**Drift indicators** severity 9, exposure 3.04; 49.4% of traces and 10.8% of latency touch drifted edges. (Prioritization aids, not cost estimates.)

## Active drift

| Sev | Rule | Where | Exposure | Since | Evidence |
|---:|---|---|---:|---|---|
| 4 | CYCLE (runtime dependency cycle) | productcatalogservice → recommendationservice | 49.35% | w20 | `7d886126d6029a8d7bd7586747d2fbf3` |
| 3 | UNDECLARED_XCTX (undeclared call across bounded contexts) | emailservice → productcatalogservice | 2.60% | w14 | `c7ceac71025fd76f0c75f7882ed68554` |
| 2 | UNDECLARED (undeclared call) | productcatalogservice → recommendationservice | 49.35% | w20 | `7d886126d6029a8d7bd7586747d2fbf3` |

## Resolved

- FOREIGN_DATA recommendationservice → db:redis-cart (confirmed w08, gone w26)

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
| emailservice | 1.2 | 79.0% |
| paymentservice | 1.5 | 72.7% |
| checkoutservice | 9.5 | 18.5% |
| recommendationservice | 46.8 | 4.1% |
| currencyservice | 61 | 3.1% |
| cartservice | 61.8 | 3.1% |
| productcatalogservice | 62 | 3.1% |
| frontend | 67 | 2.9% |

## Triage

Each finding usually ends in one of three ways: (1) a genuine violation, fixed by a code change; (2) an intended dependency the spec has not caught up with, fixed by declaring it; (3) unclear, for example a fallback path or a batch job, which needs someone to look at the evidence trace before deciding. Use `ignore` only for infrastructure traffic, not to silence (3).
