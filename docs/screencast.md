# Screencast script (about 4.5 minutes)

Record a terminal plus an editor at a readable font size. Every command
below is in the repository; nothing needs a network connection except
cloning.

## 0:00 – 0:30  The problem
Show `examples/online-boutique/spec.yaml` and say what it is: the architecture
the team intended, with layers, bounded contexts, who owns `redis-cart`, and
the calls that are allowed. Point at the `aliases` block: this is how
telemetry names the services, which is rarely how people name them.

## 0:30 – 1:15  A clean baseline
    python -m tracedebt analyze examples/online-boutique/spec.yaml \
        case-study/windows/w0[0-5].jsonl.gz --md report.md
Open `report.md`. No active drift. Scroll to "Seen from the caller only"
(shipping and ad service are not instrumented) and to "Coverage" (how rare a
call could be and still slip through at this traffic level). Mention that
window w03 contained a one-off backfill job reading Redis directly and that it
is not in the report because it never came back.

## 1:15 – 2:30  Introduce a violation
Show the git commit `drift-1`: recommendationservice reads `redis-cart`
directly instead of going through cartservice. Run the analysis over the
windows after the deployment one at a time:
    for n in 06 07 08; do python -m tracedebt analyze \
        examples/online-boutique/spec.yaml case-study/windows/w0[0-5].jsonl.gz \
        $(ls case-study/windows/w*.jsonl.gz | sed -n "7,$((10#$n+1))p") \
        --md report.md; grep FOREIGN report.md; done
Nothing after the first window, nothing after the second, confirmed after
the third: that is the 3-of-4 persistence rule at work.

## 2:30 – 3:30  The full run
    python -m tracedebt analyze examples/online-boutique/spec.yaml \
        case-study/windows/w*.jsonl.gz --md report.md --dot drift.dot --dot-focus --fail-on 4
    dot -Tpdf drift.dot -o drift.pdf
Show the active findings (CYCLE and UNDECLARED for the catalog change,
UNDECLARED_XCTX for the email change), the evidence trace IDs, the drift map,
and FOREIGN_DATA under "Resolved" after the revert. Show the exit code (`echo $?` prints 1) — this is what a CI gate sees.

## 3:30 – 4:15  An approved change
Show `git show evolve-1` (recommendationservice asks cartservice which products
are in the cart, through its API) and `diff examples/online-boutique/spec.yaml
examples/online-boutique/spec_evolved.yaml` (one dependency). Run

    python -m bench.case_evolution case-study/windows-evolution

and read the three blocks: with the updated spec nothing is reported for the new
call even though it is in about 60% of traces, and the later emailservice
violation is confirmed; without the update the call is confirmed as
UNDECLARED_XCTX; with the update four windows late the finding closes.

## 4:15 – 4:45  Wrap-up
`tracedebt init case-study/windows/w0[0-5].jsonl.gz --base
examples/online-boutique/spec.yaml` (reusing the aliases) produces a draft spec to
review rather than a blank page; its one review note is the backfill job. Close on the limits: service-level edges, sampling, and
that both case studies are one small system with changes we chose.
