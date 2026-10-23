"""Build docs/demo.mp4: a captioned terminal recording (no audio) of TraceDebt on the
Online Boutique case-study traces. Every command is actually run when this script
runs and its real output is what appears on screen; only long outputs are cut.

    pip install pillow            # and ffmpeg on the PATH
    python docs/make_demo.py      # writes docs/demo.mp4 (about 4 minutes)

Commands are shown as `tracedebt ...` (the installed command); the script runs the
same code through `python -m tracedebt` so it also works without installing.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "docs", "demo.mp4")
W, H = 1280, 720
CAP_H = 96
FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 19)
FONT_B = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 19)
CAP = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 25)
BIG = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
CW = int(FONT.getlength("M"))
LH = 25
COLS = (W - 40) // CW
ROWS = (H - CAP_H - 24) // LH
BG, FG, DIM = (12, 12, 12), (204, 204, 204), (130, 130, 130)
GREEN, AMBER, RED, CYAN = (96, 200, 96), (240, 190, 70), (240, 100, 90), (100, 190, 230)
CAPBG, CAPFG = (24, 54, 98), (255, 255, 255)

STATE = os.path.join(tempfile.gettempdir(), "tracedebt_demo_state.json")
DRAFT = os.path.join(tempfile.gettempdir(), "tracedebt_demo_draft.yaml")


def run(display: str) -> str:
    cmd = display.replace("\\\n", " ")
    cmd = re.sub(r"(^|\| *|; *)tracedebt ", lambda m: m.group(1) + f"{sys.executable} -m tracedebt ", cmd)
    cmd = cmd.replace("state.json", STATE).replace("draft.yaml", DRAFT)
    r = subprocess.run(["bash", "-c", cmd], cwd=ROOT, capture_output=True, text=True)
    return (r.stdout + ("" if "echo" in display else "")).rstrip("\n")


def colour(line: str):
    if line.startswith("$ ") or line.startswith("> "):
        return FG
    for k in ("FOREIGN_DATA", "CYCLE", "UNDECLARED_XCTX", "UNDECLARED"):
        if k in line:
            return RED
    if "gone" in line:
        return GREEN
    return FG


class Film:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="demo_frames_")
        self.n = 0
        self.items = []

    def frame(self, caption, term_lines, seconds, card=None):
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        if card:
            d.rectangle([0, 0, W, H], fill=CAPBG)
            y = 190
            for i, t in enumerate(card):
                f = BIG if i == 0 else CAP
                d.text((80, y), t, font=f, fill=CAPFG)
                y += 70 if i == 0 else 44
        else:
            d.rectangle([0, 0, W, CAP_H], fill=CAPBG)
            for i, t in enumerate(caption[:2]):
                d.text((24, 12 + i * 36), t, font=CAP, fill=CAPFG)
            y = CAP_H + 14
            for text, col in term_lines[-ROWS:]:
                d.text((20, y), text[:COLS], font=FONT, fill=col)
                y += LH
        p = os.path.join(self.dir, f"f{self.n:05d}.png")
        img.save(p)
        self.n += 1
        self.items.append((p, seconds))

    def card(self, lines, seconds):
        self.frame(None, None, seconds, card=lines)

    def screen(self, caption, entries):
        """entries: list of (display command, output text or None to run it)."""
        term = []
        for disp, out in entries:
            real = run(disp) if out is None else out
            cmd_lines = disp.split("\n")
            outl = []
            for ln in real.split("\n") if real else []:
                outl.append(ln if len(ln) <= COLS else ln[:COLS - 1] + "…")
            if len(term) + len(cmd_lines) + len(outl) + 1 > ROWS:
                term = []                                    # `clear`
            # type the command
            full = "$ " + disp.replace("\n", "\n  ")
            typed = ""
            chunk = 3
            for i in range(0, len(full), chunk):
                typed = full[: i + chunk]
                tl = term + [((("$ " if j == 0 else "") + s) if False else s, GREEN if k == 0 else FG)
                             for k, s in enumerate(typed.split("\n")) for j in [k]]
                self.frame(caption, tl, 0.07)
            term += [(s, GREEN if k == 0 else FG) for k, s in enumerate(full.split("\n"))]
            self.frame(caption, term, 0.5)
            for ln in outl:
                term.append((ln, colour(ln)))
                self.frame(caption, term, 0.07)
            hold = 5.0 + 0.8 * len(outl)
            self.frame(caption, term, hold)
            term.append(("", FG))

    def encode(self, out):
        lst = os.path.join(self.dir, "list.txt")
        with open(lst, "w") as fh:
            for p, s in self.items:
                fh.write(f"file '{p}'\nduration {s}\n")
            fh.write(f"file '{self.items[-1][0]}'\n")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
                        "-vf", "fps=10,format=yuv420p", "-c:v", "libx264", "-crf", "26",
                        "-movflags", "+faststart", out], check=True)
        shutil.rmtree(self.dir)


def main():
    if os.path.exists(STATE):
        os.remove(STATE)
    f = Film()
    f.card(["TraceDebt", "Runtime-guided detection of architectural drift in evolving microservices",
            "Demo on Online Boutique v0.10.7, with real OpenTelemetry traces",
            "Captioned terminal recording; every command shown was run on the repository's own data"], 9)

    f.screen(["1. The intended architecture is a short YAML file that lives in the repository:",
              "layers, bounded contexts, who owns the Redis store, and the calls that are allowed."],
             [("head -22 examples/online-boutique/spec.yaml", None)])

    f.screen(["2. Six clean one-minute windows: no active drift.",
              "Shipping and ad service emit no spans; their callers' client spans are the only evidence."],
             [("tracedebt analyze examples/online-boutique/spec.yaml \\\n    case-study/windows/w0[0-5].jsonl.gz | head -21", None)])

    f.screen(["3. A violation is deployed: recommendationservice reads the cart's Redis store directly",
              "instead of asking cartservice. Windows w06, w07 and w08 are analysed one more at a time."],
             [("grep -A1 Subject case-study/patches/0002-*.patch", None),
              ("tracedebt analyze examples/online-boutique/spec.yaml \\\n    case-study/windows/w0[0-6].jsonl.gz | grep FOREIGN_DATA", None),
              ("tracedebt analyze examples/online-boutique/spec.yaml \\\n    case-study/windows/w0[0-7].jsonl.gz | grep FOREIGN_DATA", None),
              ("tracedebt analyze examples/online-boutique/spec.yaml \\\n    case-study/windows/w0[0-8].jsonl.gz | grep FOREIGN_DATA | cut -c1-118", None)])
    f.card(["Why nothing after one or two windows?",
            "A call is reported only when it appears in 3 of the last 4 windows.",
            "That keeps one-off jobs out of the report. The three-minute delay is set by",
            "one-minute windows and this rule; it is not a speed claim."], 12)

    f.screen(["4. The full 30-window run. Exit status 1 is what a CI gate would see.",
              "The full report also lists an evidence trace ID for each finding, and what was resolved."],
             [("tracedebt analyze examples/online-boutique/spec.yaml \\\n    case-study/windows/w*.jsonl.gz --md report.md --fail-on 4 > /dev/null 2>&1; echo \"exit status $?\"", None),
              ("grep -E \"^\\| [0-9]\" report.md | cut -d'|' -f2-6", None),
              ("grep \"gone\" report.md", None)])

    f.screen(["5. An approved change. recommendationservice may now ask cartservice which products",
              "are in the cart, and the specification gains that one dependency in the same change."],
             [("diff examples/online-boutique/spec.yaml \\\n     examples/online-boutique/spec_evolved.yaml | grep \"^[<>]   recommendationservice\"", None)])

    f.screen(["6. Two nightly jobs sharing --state. Job 1 runs before the spec is updated: the new call",
              "is still reported. Job 2 runs with the updated spec: the finding closes after three clean windows."],
             [("tracedebt analyze examples/online-boutique/spec.yaml \\\n    case-study/windows-evolution/w0[0-9].jsonl.gz \\\n    --state state.json | grep -E \"^\\| [0-9]\" | cut -d'|' -f2-6", None),
              ("tracedebt analyze examples/online-boutique/spec_evolved.yaml \\\n    case-study/windows-evolution/w1[0-7].jsonl.gz \\\n    --state state.json | grep -E \"^\\| [0-9]|^- UNDECLARED\" | cut -c1-118", None)])

    f.screen(["7. No spec yet? Draft one from clean windows and review it; bounded contexts are left to",
              "people. In the paper the draft reproduced all 14 hand-written dependencies."],
             [("tracedebt init case-study/windows/w0[0-5].jsonl.gz \\\n    --base examples/online-boutique/spec.yaml --out draft.yaml 2>&1", None),
              ("grep -A1 \"Review notes\" draft.yaml", None),
              ("sed -n '/^dependencies/,/^aliases/p' draft.yaml | head -12", None)])

    f.card(["What this does not show",
            "One small system, and violations and an approved change that we chose.",
            "No accuracy figure on a real system; the F1 values come from a synthetic benchmark.",
            "A call present in fewer than 3 of 4 windows is not confirmed.",
            "github.com/rajeshbalaji/tracedebt"], 14)
    f.encode(OUT)
    if os.path.exists(STATE):
        os.remove(STATE)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
