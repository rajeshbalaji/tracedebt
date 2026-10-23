"""Case-study timeline: severity and exposure per one-minute window, with the
deployments marked. Two stacked panels sharing the x axis (no dual y-axis).
Reads results/case/case.json, writes results/fig_case.pdf."""
import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

c = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "results/case/case.json"))
tl = c["runs"]["full"]["timeline"]
INK, MUTED, GRID = "#222222", "#666666", "#dddddd"
BLUE = "#2a78d6"
plt.rcParams.update({"font.family": "serif", "font.size": 8, "axes.edgecolor": MUTED,
                     "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
                     "axes.linewidth": 0.6})

x = list(range(len(tl)))
sev = [t["severity"] for t in tl]
exp = [t["exposure"] for t in tl]
fig, (a, b) = plt.subplots(2, 1, figsize=(3.5, 1.95), sharex=True,
                           gridspec_kw={"height_ratios": [1, 1], "hspace": 0.12})
a.step(x, sev, where="mid", color=BLUE, lw=1.5)
b.plot(x, exp, color=BLUE, lw=1.3, marker="o", ms=2.4, markeredgecolor="white", markeredgewidth=0.4)
a.set_ylabel("severity")
b.set_ylabel("exposure")
labels = {"v0": "base", "v1": "+D1", "v2": "+D2", "v3": "+D3", "v4": "revert D1"}
starts = [i for i, t in enumerate(tl) if i == 0 or t["phase"] != tl[i - 1]["phase"]]
a.set_ylim(0, max(sev + [1]) * 1.35)
b.set_ylim(0, max(exp + [0.1]) * 1.15)
for ax in (a, b):
    for i in starts[1:]:
        ax.axvline(i - 0.5, color=MUTED, lw=0.6, ls=":")
    ax.grid(axis="y", color=GRID, lw=0.5)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
for i in starts:
    a.text(i + 0.15, a.get_ylim()[1] * 0.97, labels.get(tl[i]["phase"], tl[i]["phase"]),
           fontsize=6.8, color=INK, va="top")
b.set_xlim(-0.5, len(tl) - 0.5)
b.set_xticks(range(0, len(tl), 5))
b.set_xlabel("one-minute window")
fig.align_ylabels((a, b))
fig.savefig("results/fig_case.pdf", bbox_inches="tight", pad_inches=0.02)
fig.savefig("results/fig_case.png", dpi=220, bbox_inches="tight", pad_inches=0.02)
