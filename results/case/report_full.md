# TraceDebt report: online-boutique

Windows analysed: 30 (w00 .. w29)

**Drift indicators** severity 9, exposure 3.44; 56.0% of traces and 9.8% of latency touch drifted edges. (Prioritization aids, not cost estimates.)

## Active drift

| Sev | Rule | Where | Exposure | Since | Evidence |
|---:|---|---|---:|---|---|
| 4 | CYCLE (runtime dependency cycle) | productcatalogservice → recommendationservice | 55.95% | w20 | `d4cf459839962de5f4386752a49c94f2` |
| 3 | UNDECLARED_XCTX (undeclared call across bounded contexts) | emailservice → productcatalogservice | 2.86% | w14 | `7a0504f79a37e6d986d986b5b332f4e3` |
| 2 | UNDECLARED (undeclared call) | productcatalogservice → recommendationservice | 55.95% | w20 | `d4cf459839962de5f4386752a49c94f2` |

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
| emailservice | 25 | 7.5% |
| paymentservice | 25 | 7.5% |
| checkoutservice | 113.5 | 1.7% |

## Triage

Each finding usually ends in one of three ways: (1) a genuine violation, fixed by a code change; (2) an intended dependency the spec has not caught up with, fixed by declaring it; (3) unclear, for example a fallback path or a batch job, which needs someone to look at the evidence trace before deciding. Use `ignore` only for infrastructure traffic, not to silence (3).
