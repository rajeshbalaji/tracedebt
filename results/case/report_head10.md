# TraceDebt report: online-boutique

Windows analysed: 30 (w00 .. w29)

**Drift indicators** severity 9, exposure 3.03; 48.9% of traces and 9.4% of latency touch drifted edges. (Prioritization aids, not cost estimates.)

## Active drift

| Sev | Rule | Where | Exposure | Since | Evidence |
|---:|---|---|---:|---|---|
| 4 | CYCLE (runtime dependency cycle) | productcatalogservice → recommendationservice | 48.89% | w20 | `5745eeb9d8ad5c2f0dfcfebf79d03e90` |
| 3 | UNDECLARED_XCTX (undeclared call across bounded contexts) | emailservice → productcatalogservice | 3.33% | w14 | `f5f47af756ba4147d373fc6c4a19fa5e` |
| 2 | UNDECLARED (undeclared call) | productcatalogservice → recommendationservice | 48.89% | w20 | `5745eeb9d8ad5c2f0dfcfebf79d03e90` |

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
| emailservice | 1.5 | 72.7% |
| paymentservice | 1.5 | 72.7% |
| checkoutservice | 11 | 16.2% |
| recommendationservice | 46.5 | 4.1% |
| cartservice | 61 | 3.1% |
| productcatalogservice | 63 | 3.0% |
| currencyservice | 63.8 | 3.0% |
| frontend | 68.2 | 2.8% |

## Triage

Each finding usually ends in one of three ways: (1) a genuine violation, fixed by a code change; (2) an intended dependency the spec has not caught up with, fixed by declaring it; (3) unclear, for example a fallback path or a batch job, which needs someone to look at the evidence trace before deciding. Use `ignore` only for infrastructure traffic, not to silence (3).

To see the request behind a finding: `tracedebt trace <evidence id> <window files> --spec <spec> --path` prints the call path with the offending span marked.
