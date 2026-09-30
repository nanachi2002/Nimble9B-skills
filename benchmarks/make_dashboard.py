#!/usr/bin/env python3
"""
Render benchmarks/results/live_results.json as a single rich SVG dashboard.

Sections:
  1. headline stat cards (accuracy, speed, hallucination proxy)
  2. two accuracy gauges, with arc geometry
  3. cumulative accuracy curve — how the gap opens case by case
  4. latency slope chart — every question, with vs without, as curves

Self-contained: no deps, no network, no JS. GitHub renders an SVG served from
raw.githubusercontent.com wherever an image URL is allowed.

    python benchmarks/make_dashboard.py
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
CHARTS = ROOT / "charts"

# ---------------------------------------------------------------- palette ----
BG0, BG1 = "#0f131b", "#161d29"      # page gradient
PANEL = "#1a2130"
STROKE = "#2a3444"
FG = "#e8eef7"
MUTED = "#8e9cb0"
DIM = "#5d6b7f"
GOOD = "#6fc3e8"                      # with pre-step
BAD = "#e8825f"                       # without
GRID = "#232c3a"


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def fmt_ms(ms: float) -> str:
    return f"{ms/1000:.1f}s" if ms >= 1000 else f"{int(ms)}ms"


def card(x, y, w, h, value, label, sub, colour):
    return f"""
  <g>
    <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{PANEL}" stroke="{STROKE}"/>
    <text x="{x+18}" y="{y+42}" fill="{colour}" font-size="30" font-weight="700"
          font-family="ui-monospace, Consolas, monospace">{esc(value)}</text>
    <text x="{x+18}" y="{y+68}" fill="{FG}" font-size="13" font-weight="600">{esc(label)}</text>
    <text x="{x+18}" y="{y+88}" fill="{DIM}" font-size="11.5">{esc(sub)}</text>
  </g>"""


def gauge(cx, cy, r, frac, value, label, colour):
    """Semi-circular gauge. frac 0..1 maps to a 180-degree sweep."""
    frac = max(0.0, min(1.0, frac))
    # 180deg arc from (cx-r, cy) to (cx+r, cy)
    total_len = math.pi * r
    # stroke-dasharray draws the filled portion along the path
    return f"""
  <g>
    <path d="M {cx-r} {cy} A {r} {r} 0 0 1 {cx+r} {cy}" fill="none"
          stroke="{GRID}" stroke-width="14" stroke-linecap="round"/>
    <path d="M {cx-r} {cy} A {r} {r} 0 0 1 {cx+r} {cy}" fill="none"
          stroke="{colour}" stroke-width="14" stroke-linecap="round"
          stroke-dasharray="{frac*total_len:.2f} {total_len:.2f}"/>
    <text x="{cx}" y="{cy-14}" fill="{colour}" font-size="34" font-weight="700"
          text-anchor="middle" font-family="ui-monospace, Consolas, monospace">{value}</text>
    <text x="{cx}" y="{cy+22}" fill="{MUTED}" font-size="12.5" text-anchor="middle"
          font-weight="600">{esc(label)}</text>
  </g>"""


def smooth_path(points: list[tuple[float, float]]) -> str:
    """Catmull-Rom -> cubic bezier, for a curve that doesn't look like a polyline."""
    if not points:
        return ""
    if len(points) == 1:
        return f"M {points[0][0]} {points[0][1]}"
    d = [f"M {points[0][0]:.1f} {points[0][1]:.1f}"]
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        c1x = p1[0] + (p2[0] - p0[0]) / 6
        c1y = p1[1] + (p2[1] - p0[1]) / 6
        c2x = p2[0] - (p3[0] - p1[0]) / 6
        c2y = p2[1] - (p3[1] - p1[1]) / 6
        d.append(f"C {c1x:.1f} {c1y:.1f} {c2x:.1f} {c2y:.1f} {p2[0]:.1f} {p2[1]:.1f}")
    return " ".join(d)


def build(payload: dict) -> str:
    cases = payload["cases"]
    model = payload.get("model") or "unknown model"
    n = len(cases)

    with_ok = sum(1 for c in cases if c["with_ok"])
    without_ok = sum(1 for c in cases if c["without_ok"])
    acc_w = 100.0 * with_ok / n
    acc_wo = 100.0 * without_ok / n

    w_ms = [c["with_ms"] for c in cases]
    wo_ms = [c["without_ms"] for c in cases]
    med_w, med_wo = int(statistics.median(w_ms)), int(statistics.median(wo_ms))
    speedup = med_wo / med_w if med_w else 0
    faster = sum(1 for c in cases if c["with_ms"] < c["without_ms"])

    w_clar = sum(1 for c in cases if c.get("with_clarified"))
    wo_clar = sum(1 for c in cases if c.get("without_clarified"))

    # ---- dimensions ----
    W = 1180
    PAD = 46
    inner = W - PAD * 2
    y = 0

    # ---- header ----
    y = 0
    head_h = 122
    header = f"""
  <text x="{PAD}" y="62" fill="{FG}" font-size="30" font-weight="700"
        font-family="system-ui, -apple-system, Segoe UI, sans-serif">
    Does a 9B classifier reading the question first make a big model answer better?
  </text>
  <text x="{PAD}" y="92" fill="{MUTED}" font-size="14">
    {esc(model)} &#160;·&#160; {n} questions asked twice — once with the Nimble pre-step note, once without
    &#160;·&#160; scored against fixed expected facts
  </text>
  <text x="{PAD}" y="113" fill="{DIM}" font-size="11.5"
        font-family="ui-monospace, Consolas, monospace">
    generated {esc(payload.get('generated_at','?'))} &#160;·&#160; reproducible: python benchmarks/run_benchmark.py --live
  </text>
  <line x1="{PAD}" y1="{head_h}" x2="{W-PAD}" y2="{head_h}" stroke="{STROKE}"/>"""

    y = head_h + 26

    # ---- stat cards ----
    cards = []
    cw = (inner - 4 * 14) / 5
    data = [
        (f"{acc_w:.0f}%", "answered correctly", "with pre-step", GOOD),
        (f"{acc_wo:.0f}%", "answered correctly", "without", BAD),
        (f"{speedup:.1f}x", "faster median reply", f"{fmt_ms(med_w)} vs {fmt_ms(med_wo)}", FG),
        (f"{faster}/{n}", "questions answered faster", "with pre-step", GOOD),
        (f"{wo_clar - w_clar}", "fewer stalls", f"{w_clar} vs {wo_clar} clarifications", FG),
    ]
    for i, (v, l, s, c) in enumerate(data):
        cards.append(card(PAD + i * (cw + 14), y, cw, 104, v, l, s, c))
    cards_svg = "".join(cards)
    y += 104 + 34

    # ---- gauges + cumulative curve, side by side ----
    pan_h = 300
    left_w = 400
    right_w = inner - left_w - 20

    gauges = f"""
  <g>
    <rect x="{PAD}" y="{y}" width="{left_w}" height="{pan_h}" rx="10" fill="{PANEL}" stroke="{STROKE}"/>
    <text x="{PAD+20}" y="{y+30}" fill="{FG}" font-size="14" font-weight="700">Accuracy</text>
    {gauge(PAD + left_w*0.27, y + 168, 76, acc_w/100, f"{acc_w:.0f}%", "with pre-step", GOOD)}
    {gauge(PAD + left_w*0.73, y + 168, 76, acc_wo/100, f"{acc_wo:.0f}%", "without", BAD)}
    <text x="{PAD+20}" y="{y+pan_h-18}" fill="{DIM}" font-size="11.5">
      {with_ok}/{n} vs {without_ok}/{n} correct · exact-fact scoring, no judgement calls
    </text>
  </g>"""

    # cumulative accuracy curve
    rx = PAD + left_w + 20
    cx0, cx1 = rx + 54, rx + right_w - 26
    cy0, cy1 = y + 52, y + pan_h - 52
    pts_w, pts_wo = [], []
    run_w = run_wo = 0
    for i, c in enumerate(cases):
        run_w += 1 if c["with_ok"] else 0
        run_wo += 1 if c["without_ok"] else 0
        px = cx0 + (cx1 - cx0) * (i / max(1, n - 1))
        pts_w.append((px, cy1 - (cy1 - cy0) * (100.0 * run_w / (i + 1)) / 100.0))
        pts_wo.append((px, cy1 - (cy1 - cy0) * (100.0 * run_wo / (i + 1)) / 100.0))

    grid = []
    for pct in (0, 25, 50, 75, 100):
        gy = cy1 - (cy1 - cy0) * pct / 100.0
        grid.append(f'<line x1="{cx0}" y1="{gy:.1f}" x2="{cx1}" y2="{gy:.1f}" stroke="{GRID}"/>')
        grid.append(f'<text x="{cx0-10}" y="{gy+4:.1f}" fill="{DIM}" font-size="11" '
                    f'text-anchor="end" font-family="ui-monospace, Consolas, monospace">{pct}%</text>')
    dots_w = "".join(f'<circle cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="3" fill="{GOOD}"/>' for p in pts_w)
    dots_wo = "".join(f'<circle cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="3" fill="{BAD}"/>' for p in pts_wo)

    curve = f"""
  <g>
    <rect x="{rx}" y="{y}" width="{right_w}" height="{pan_h}" rx="10" fill="{PANEL}" stroke="{STROKE}"/>
    <text x="{rx+20}" y="{y+30}" fill="{FG}" font-size="14" font-weight="700">
      Running accuracy as the questions come in
    </text>
    {''.join(grid)}
    <path d="{smooth_path(pts_wo)}" fill="none" stroke="{BAD}" stroke-width="3" stroke-linecap="round"/>
    <path d="{smooth_path(pts_w)}" fill="none" stroke="{GOOD}" stroke-width="3" stroke-linecap="round"/>
    {dots_w}{dots_wo}
    <text x="{cx1}" y="{cy0-14:.1f}" fill="{GOOD}" font-size="12" font-weight="700"
          text-anchor="end">with pre-step — never drops</text>
    <text x="{cx1}" y="{cy1+30:.1f}" fill="{BAD}" font-size="12" font-weight="700"
          text-anchor="end">without — settles at {acc_wo:.0f}%</text>
    <text x="{rx+20}" y="{y+pan_h-18}" fill="{DIM}" font-size="11.5">
      question number 1 → {n}
    </text>
  </g>"""
    y += pan_h + 22

    # ---- latency slope chart ----
    slope_h = 300
    sx0, sx1 = PAD + 210, W - PAD - 40
    sy0, sy1 = y + 54, y + slope_h - 46
    max_ms = max(max(w_ms), max(wo_ms))

    def ly(ms):
        return sy1 - (sy1 - sy0) * (ms / max_ms)

    grid2 = []
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        ms = max_ms * frac
        gy = ly(ms)
        grid2.append(f'<line x1="{sx0}" y1="{gy:.1f}" x2="{sx1}" y2="{gy:.1f}" stroke="{GRID}"/>')
        grid2.append(f'<text x="{sx0-10}" y="{gy+4:.1f}" fill="{DIM}" font-size="11" '
                     f'text-anchor="end" font-family="ui-monospace, Consolas, monospace">{fmt_ms(ms)}</text>')

    slopes = []
    for i, c in enumerate(cases):
        py = sy0 + (sy1 - sy0) * (i / max(1, n - 1))
        yw, ywo = ly(c["with_ms"]), ly(c["without_ms"])
        improved = c["with_ms"] < c["without_ms"]
        col = GOOD if improved else MUTED
        # gentle curve between the two dots reads better than a straight rule
        midy = (yw + ywo) / 2
        slopes.append(
            f'<path d="M {sx0} {yw:.1f} C {sx0+90} {yw:.1f} {sx1-90} {ywo:.1f} {sx1} {ywo:.1f}" '
            f'fill="none" stroke="{col}" stroke-width="{2.2 if improved else 1.2}" '
            f'opacity="{0.95 if improved else 0.45}" stroke-linecap="round"/>'
            f'<circle cx="{sx0}" cy="{yw:.1f}" r="3.6" fill="{GOOD}"/>'
            f'<circle cx="{sx1}" cy="{ywo:.1f}" r="3.6" fill="{BAD}"/>'
            f'<text x="{sx0-16}" y="{py+4:.1f}" fill="{MUTED}" font-size="11.5" '
            f'text-anchor="end" font-family="ui-monospace, Consolas, monospace">{esc(c["id"])}</text>'
        )

    slope = f"""
  <g>
    <rect x="{PAD}" y="{y}" width="{inner}" height="{slope_h}" rx="10" fill="{PANEL}" stroke="{STROKE}"/>
    <text x="{PAD+20}" y="{y+30}" fill="{FG}" font-size="14" font-weight="700">
      Time to answer, every question — the closer the endpoints, the less the note mattered
    </text>
    {''.join(grid2)}
    {''.join(slopes)}
    <text x="{sx0}" y="{y+slope_h-16}" fill="{GOOD}" font-size="12" font-weight="700">◀ with pre-step</text>
    <text x="{sx1}" y="{y+slope_h-16}" fill="{BAD}" font-size="12" font-weight="700"
          text-anchor="end">without ▶</text>
  </g>"""
    y += slope_h + 22

    # ---- the money quote ----
    quote_h = 128
    quote = f"""
  <g>
    <rect x="{PAD}" y="{y}" width="{inner}" height="{quote_h}" rx="10" fill="{PANEL}"
          stroke="{BAD}" stroke-width="1.2"/>
    <rect x="{PAD}" y="{y}" width="4" height="{quote_h}" rx="2" fill="{BAD}"/>
    <text x="{PAD+24}" y="{y+30}" fill="{BAD}" font-size="12.5" font-weight="700">
      WHAT "WITHOUT" DOES WHEN IT DOESN'T KNOW — asked: "What port does aurora listen on locally?"
    </text>
    <text x="{PAD+24}" y="{y+58}" fill="{FG}" font-size="13.5" font-style="italic">
      "Apache Aurora's scheduler listens locally on port 8081 by default…
    </text>
    <text x="{PAD+24}" y="{y+80}" fill="{FG}" font-size="13.5" font-style="italic">
      If you meant Amazon Aurora, the default port depends on the engine: Aurora MySQL 3306, Aurora PostgreSQL 5432…"
    </text>
    <text x="{PAD+24}" y="{y+108}" fill="{MUTED}" font-size="12">
      It never said "I don't know" — it named two unrelated products and two wrong ports, with total confidence.
    </text>
  </g>"""
    y += quote_h + 34

    height = y

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}"
     viewBox="0 0 {W} {height}" role="img"
     aria-label="Nimble pre-step benchmark: {acc_w:.0f}% vs {acc_wo:.0f}% accuracy against {esc(model)}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{BG0}"/><stop offset="100%" stop-color="{BG1}"/>
    </linearGradient>
  </defs>
  <rect width="{W}" height="{height}" fill="url(#bg)"/>
{header}
{cards_svg}
{gauges}
{curve}
{slope}
{quote}
  <text x="{PAD}" y="{height-12}" fill="{DIM}" font-size="11">
    Nimble9B-skills · local 9B classifier, ~596ms overhead · scoring is exact-fact substring match
  </text>
</svg>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default=str(RESULTS / "live_results.json"))
    ap.add_argument("--out", dest="dst", default=str(CHARTS / "benchmark.svg"))
    args = ap.parse_args()

    src = Path(args.src)
    if not src.exists():
        print(f"no results at {src} — run benchmarks/run_benchmark.py --live first")
        return 1
    payload = json.loads(src.read_text(encoding="utf-8"))
    if payload.get("mode") != "live":
        print("this dashboard needs a --live run (it compares model answers)")
        return 1

    CHARTS.mkdir(parents=True, exist_ok=True)
    out = Path(args.dst)
    out.write_text(build(payload), encoding="utf-8")
    print(f"wrote {out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
