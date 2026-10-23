"""Figure for the paper: (a) F1 vs. trace sampling rate, (b) TraceDebt recall
by drift rarity. Reads results/results.json, writes results/fig_eval.pdf."""
import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

r = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "results/results.json"))
INK, MUTED, GRID = "#222222", "#666666", "#dddddd"
COL = {"TraceDebt": "#2a78d6", "Snap@3": "#eb6834", "Snapshot": "#1baf7a"}
MARK = {"TraceDebt": ("o", "-"), "Snap@3": ("s", "--"), "Snapshot": ("^", ":")}
NAME = {"TraceDebt": "TraceDebt", "Snap@3": "Snapshot, t=3", "Snapshot": "Snapshot, t=1"}

plt.rcParams.update({"font.family": "serif", "font.size": 8.5, "axes.edgecolor": MUTED,
                     "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
                     "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6})
fig, (a, b) = plt.subplots(1, 2, figsize=(3.5, 2.15), gridspec_kw={"width_ratios": [1.05, 1]})

rates = sorted(r["rq2_sampling"], key=float)
xs = [float(x) * 100 for x in rates]
for m in ["TraceDebt", "Snap@3", "Snapshot"]:
    ys = [r["rq2_sampling"][k][m]["F1"] for k in rates]
    mk, ls = MARK[m]
    a.plot(xs, ys, ls, marker=mk, ms=3.8, lw=1.5, color=COL[m], label=NAME[m],
           markeredgecolor="white", markeredgewidth=0.5)
a.set_xscale("log"); a.set_xticks(xs); a.set_xticklabels([f"{x:g}" for x in xs])
a.minorticks_off()
a.set_ylim(0, 1); a.set_xlabel("Head sampling rate (%)"); a.set_ylabel("F1")
a.grid(axis="y", color=GRID, lw=0.5); a.set_axisbelow(True)
for s in ("top", "right"):
    a.spines[s].set_visible(False)
a.set_title("(a) F1 vs. sampling (M)", fontsize=8, color=INK, loc="left")

bins = [(0.002, 0.005), (0.005, 0.01), (0.01, 0.03), (0.03, 0.1), (0.1, 0.3)]
labels = ["<.5", ".5–1", "1–3", "3–10", "10+"]
rec, ns = [], []
for lo, hi in bins:
    hits = [f for p, f in r["drift_probs"] if lo <= p < hi]
    rec.append(sum(hits) / max(len(hits), 1)); ns.append(len(hits))
bars = b.bar(range(len(bins)), rec, width=0.78, color=COL["TraceDebt"], edgecolor="white", lw=1)
for i, (v, n) in enumerate(zip(rec, ns)):
    b.text(i, v - 0.04, f"{v:.2f}".lstrip("0"), ha="center", va="top", fontsize=7, color="white")
b.set_xticks(range(len(bins))); b.set_xticklabels(labels, fontsize=6.8)
b.set_ylim(0, 1.12); b.set_yticks([0, 0.5, 1.0])
b.set_xlabel("Activation prob. (%)"); b.set_ylabel("Ever flagged")
b.grid(axis="y", color=GRID, lw=0.5); b.set_axisbelow(True)
for s in ("top", "right"):
    b.spines[s].set_visible(False)
b.set_title("(b) recall vs. drift rarity", fontsize=8, color=INK, loc="left")

h, l = a.get_legend_handles_labels()
fig.legend(h, l, loc="upper center", ncol=3, frameon=False, fontsize=7.5,
           handlelength=2.4, bbox_to_anchor=(0.5, 1.0), labelcolor=INK)
fig.tight_layout(pad=0.25, w_pad=0.6, rect=(0, 0, 1, 0.9))
fig.savefig("results/fig_eval.pdf", bbox_inches="tight", pad_inches=0.02); fig.savefig("results/fig_eval.png", dpi=220, bbox_inches="tight", pad_inches=0.02)
print("bins n =", ns)
