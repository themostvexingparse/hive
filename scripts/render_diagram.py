"""Draw the README's "How it works" diagram as two SVGs, light and dark, in Hive's palette.

Usage: python scripts/render_diagram.py
  -> docs/images/architecture-light.svg and docs/images/architecture-dark.svg

The layout is set by hand here, so rows stay aligned (one kind of object per row) and every
arrow runs straight. Boxes are sized from real Arial metrics (Pillow), so text never overflows;
change a label and the script fails if it no longer fits. Colours: amber for machines and
what runs on them, slate for shared state, grey for you. The dark version's background is
GitHub's dark page colour, so it blends in a README that switches with <picture>.
"""
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import ImageFont

OUT = Path(__file__).resolve().parent.parent / "docs" / "images"
FONT = "Arial, Helvetica, sans-serif"


def _font(bold: bool, size: int):
    """Arial, or a metric-compatible substitute (Liberation Sans) on systems without it."""
    names = (["arialbd.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf"] if bold else
             ["arial.ttf", "Arial.ttf", "LiberationSans-Regular.ttf"])
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    sys.exit("Arial or Liberation Sans is needed to size the boxes")


F_TITLE = _font(True, 14)
F_SUB = _font(False, 12)
F_LAB = _font(False, 11)

PAL = {
    "light": dict(page="#ffffff", ink="#1E2230", sub="#5A6172", row="#8A909C",
                  amber="#B86E00", amber_fill="#FFFFFF", machine="#FDF6E8", machine_line="#E2B66A",
                  slate="#3E6383", slate_fill="#EEF3F8", neutral="#6B7284", neutral_fill="#F4F5F7"),
    "dark": dict(page="#0d1117", ink="#F0F3F6", sub="#A9B0BF", row="#7D8590",
                 amber="#E8A33C", amber_fill="#161B22", machine="#1C1912", machine_line="#7A5418",
                 slate="#7FA6C9", slate_fill="#131D27", neutral="#9AA3AF", neutral_fill="#1A1F26"),
}

W, H = 1080, 600


class Svg:
    def __init__(self, p):
        self.p, self.out = p, []

    def add(self, s):
        self.out.append(s)

    def text(self, x, y, s, font, color, anchor="middle", weight="normal", style="normal"):
        size = font.size
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" '
                 f'font-weight="{weight}" font-style="{style}" fill="{color}" '
                 f'text-anchor="{anchor}">{escape(s)}</text>')

    def box(self, x, y, w, h, title, lines, kind, rx=6):
        p = self.p
        stroke, fill = {"amber": (p["amber"], p["amber_fill"]),
                        "slate": (p["slate"], p["slate_fill"]),
                        "neutral": (p["neutral"], p["neutral_fill"])}[kind]
        need = max([F_TITLE.getlength(title)] + [F_SUB.getlength(l) for l in lines]) + 20
        assert need <= w, f"{title!r} needs {need:.0f}px, box is {w}"
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="1.4"/>')
        total = 17 + 15 * len(lines)
        ty = y + (h - total) / 2 + 13
        self.text(x + w / 2, ty, title, F_TITLE, p["ink"], weight="bold")
        for i, l in enumerate(lines):
            self.text(x + w / 2, ty + 17 + 15 * i, l, F_SUB, p["sub"])

    def cylinder(self, x, y, w, h, title, lines):
        p, e = self.p, 7
        self.add(f'<path d="M{x},{y + e} a{w / 2},{e} 0 0,0 {w},0 a{w / 2},{e} 0 0,0 {-w},0 '
                 f'v{h - 2 * e} a{w / 2},{e} 0 0,0 {w},0 v{-(h - 2 * e)}" fill="{p["slate_fill"]}" '
                 f'stroke="{p["slate"]}" stroke-width="1.4"/>')
        total = 17 + 15 * len(lines)
        ty = y + e + (h - e - total) / 2 + 13
        self.text(x + w / 2, ty, title, F_TITLE, p["ink"], weight="bold")
        for i, l in enumerate(lines):
            self.text(x + w / 2, ty + 17 + 15 * i, l, F_SUB, p["sub"])

    def machine(self, x, y, w, h, title):
        p = self.p
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{p["machine"]}" '
                 f'stroke="{p["machine_line"]}" stroke-width="1.2"/>')
        self.text(x + 12, y + h - 12, title, F_TITLE, p["ink"], anchor="start", weight="bold")

    def wire(self, pts, kind, both=False, dashed=False, hop_at=None):
        """A straight orthogonal arrow through `pts`; arrowhead at the end (and start)."""
        color = self.p[kind]
        d = f"M{pts[0][0]},{pts[0][1]}"
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if hop_at and y0 == y1 and min(x0, x1) < hop_at < max(x0, x1):
                r, s = 5, 1 if x1 > x0 else -1
                d += f" L{hop_at - s * r},{y0} a{r},{r} 0 0,{1 if s > 0 else 0} {2 * s * r},0"
            d += f" L{x1},{y1}"
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        start = f' marker-start="url(#a-{kind})"' if both else ""
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.4"{dash}'
                 f'{start} marker-end="url(#a-{kind})"/>')

    def label(self, x, y, s, kind, anchor="start"):
        p = self.p
        w = F_LAB.getlength(s)
        bx = x if anchor == "start" else x - w / 2 if anchor == "middle" else x - w
        self.add(f'<rect x="{bx - 3:.1f}" y="{y - 10:.1f}" width="{w + 6:.1f}" height="14" '
                 f'fill="{p["page"]}" opacity="0.92"/>')
        self.text(x, y, s, F_LAB, p[kind], anchor=anchor)

    def defs(self):
        p = self.p
        marks = "".join(
            f'<marker id="a-{k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            f'markerHeight="7" orient="auto-start-reverse"><path d="M0,1 L10,5 L0,9 z" '
            f'fill="{p[k]}"/></marker>' for k in ("amber", "slate", "neutral"))
        return f"<defs>{marks}</defs>"

    def render(self):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
                f'viewBox="0 0 {W} {H}">{self.defs()}'
                f'<rect width="{W}" height="{H}" fill="{self.p["page"]}"/>' + "".join(self.out)
                + "</svg>")


def draw(p) -> str:
    s = Svg(p)
    # ---- row labels (left column)
    for y, txt in ((74, "you"), (193, "shared state"), (358, "one daemon"), (372, "per machine"),
                   (482, "agent CLIs,"), (496, "one worktree each")):
        s.text(112, y, txt, F_SUB, p["row"], anchor="end", style="italic")

    # ---- machines (background first; titles at the bottom, where no wire enters)
    s.machine(130, 312, 250, 272, "Your computer")
    s.machine(400, 312, 370, 272, "Worker machine A")
    s.machine(790, 312, 260, 272, "Worker machine B")

    # ---- wires (before the boxes, which cover their ends)
    s.wire([(150, 96), (150, 436)], "neutral")                        # chat
    s.wire([(425, 96), (425, 148)], "neutral")                        # commands -> server
    s.wire([(640, 96), (640, 148)], "neutral")                        # dashboard -> server
    s.wire([(300, 330), (300, 228)], "slate", both=True)              # daemons <-> server
    s.wire([(560, 330), (560, 228)], "slate", both=True)
    s.wire([(860, 330), (860, 290), (690, 290), (690, 228)], "slate", both=True)
    s.wire([(675, 330), (675, 260), (830, 260), (830, 232)], "slate", hop_at=690)  # -> git
    s.wire([(960, 330), (960, 232)], "slate")
    s.wire([(300, 390), (300, 436)], "amber", both=True)              # daemons <-> agents
    s.wire([(500, 390), (500, 436)], "amber", both=True)
    s.wire([(670, 390), (670, 436)], "amber", both=True)
    s.wire([(915, 390), (915, 436)], "amber", both=True)

    # ---- nodes
    s.box(135, 44, 170, 52, "terminal", ["chat with the orchestrator"], "neutral")
    s.box(330, 44, 190, 52, "hive commands", ["start, decide, status, watch"], "neutral")
    s.box(545, 44, 205, 52, "web dashboard", ["decisions, approvals, live output"], "neutral")

    s.box(230, 150, 470, 78, "hive-server",
          ["tasks, messages, leases, decisions, reports, live output",
           "assigns tasks, grants leases, enforces the fleet state"], "slate")
    s.cylinder(790, 146, 260, 86, "git origin", ["main and hive/<agent> branches"])

    s.box(205, 330, 160, 60, "daemon (hive up)", ["polls every 3 s", "local API 127.0.0.1"],
          "amber")
    s.box(470, 330, 230, 60, "daemon (hive up)", ["wakes agents, runs git", "uploads live output"],
          "amber")
    s.box(815, 330, 210, 60, "daemon (hive up)", ["wakes agents, runs git", "uploads live output"],
          "amber")

    s.box(140, 436, 230, 106, "orchestrator",
          ["Claude Code, interactive", "plans and assigns, never codes", "waits in await_events",
           "worktree app-orch"], "amber")
    s.box(410, 436, 170, 106, "claude-2",
          ["programmer", "Claude Code, headless", "edits leased files", "branch hive/claude-2"],
          "amber")
    s.box(590, 436, 170, 106, "codex-1",
          ["tester", "Codex, headless", "writes and runs tests", "branch hive/codex-1"], "amber")
    s.box(815, 436, 210, 106, "gemini-1",
          ["reviewer", "Gemini CLI, headless", "reviews the diff", "branch hive/gemini-1"],
          "amber")

    # ---- labels, beside their wire
    s.label(156, 300, "chat", "neutral")
    s.label(431, 126, "HTTPS, admin token", "neutral")
    s.label(646, 126, "HTTPS, admin token", "neutral")
    s.label(306, 270, "events, heartbeats", "slate")
    s.label(566, 270, "events, heartbeats", "slate")
    s.label(775, 285, "events, heartbeats", "slate", anchor="middle")
    s.label(760, 255, "commit, rebase, push", "slate", anchor="middle")
    s.label(954, 275, "commit, rebase, push", "slate", anchor="end")
    s.label(306, 417, "MCP tools, hooks", "amber")
    s.label(585, 417, "wake, MCP tools, hooks", "amber", anchor="middle")
    s.label(921, 417, "wake, MCP tools, hooks", "amber")
    return s.render()


if __name__ == "__main__":
    for mode, p in PAL.items():
        target = OUT / f"architecture-{mode}.svg"
        target.write_bytes(draw(p).encode("utf-8"))
        print(target)
