# TraceDebt report: ticketing-demo

Windows analysed: 6 (w0 .. w5)

**Drift indicators** severity 12, exposure 3.39; 45.5% of traces and 10.2% of latency touch drifted edges. (Prioritization aids, not cost estimates.)

## Active drift

| Sev | Rule | Where | Exposure | Since | Evidence |
|---:|---|---|---:|---|---|
| 5 | FOREIGN_DATA (reads/writes a datastore owned by another service) | basic-svc → db:assurance-db | 26.32% | w4 | `66a6t` |
| 4 | FOREIGN_TOPIC (publishes to a topic owned by another service) | assurance-svc → topic:route-events | 39.74% | w5 | `66a6t` |
| 3 | UNDECLARED_XCTX (undeclared call across bounded contexts) | basic-svc → assurance-svc | 16.05% | w4 | `66d6t` |

## Coverage

With current trace volume, a drifted call that occurs in fewer than the share of a service's traces shown below would probably not be confirmed (90% target, k=3 of m=4). A quiet report is not evidence of conformance below these rates.

| Service | Traces/window | Rarest reliably confirmed |
|---|---:|---:|
| travel-svc | 21.5 | 8.7% |
| route-svc | 74.8 | 2.6% |
| auth-svc | 84.8 | 2.3% |
| loyalty-svc | 97.2 | 2.0% |
| station-svc | 105 | 1.8% |
| seat-svc | 134.8 | 1.4% |
| web-bff | 149.5 | 1.3% |

## Triage

Each finding usually ends in one of three ways: (1) a genuine violation, fixed by a code change; (2) an intended dependency the spec has not caught up with, fixed by declaring it; (3) unclear, for example a fallback path or a batch job, which needs someone to look at the evidence trace before deciding. Use `ignore` only for infrastructure traffic, not to silence (3).
