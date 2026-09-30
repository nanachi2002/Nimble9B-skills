#!/usr/bin/env python3
"""
Render the one-chart comparison: cumulative answer accuracy, with vs without.

Two lines, nothing else. White background, no gridline clutter, data labels —
the repository "talks" elsewhere; this chart's only job is to show the gap.

    python benchmarks/make_comparison.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
CHARTS = ROOT / "charts"

# --- palette: restrained, matches the reference aesthetic -------------------
BLUE = "#2563eb"      # with pre-step
ORANGE = "#e8825f"    # without
GREY = "#c9d2e0"
DARK = "#111827"
MUTED = "#6b7280"


def smooth_path(points):
    """Catmull-Rom -> cubic bezier for a curve that reads as a line, not a polylne."""
    d = [f"M {points[0][0]:.1f} {points[0][1]:.1f}"]
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        d.append(
            f"C {p1[0]+(p2[0]-p0[0])/6:.1f} {p1[1]+(p2[1]-p0[1])/6:.1f} "
            f"{p2[0]-(p3[0]-p1[0])/6:.1f} {p2[1]-(p3[1]-p1[1])/6:.1f} "
            f"{p2[0]:.1f} {p2[1]:.1f}"
        )
    return " ".join(d)


def build(payload: dict) -> str:
    cases = payload["cases"]
    model = payload.get("model") or "unknown model"
    n = len(cases)

    cum_w, cum_wo = [], []
    run_w = run_wo = 0
    for i, c in enumerate(cases):
        run_w += 1 if c["with_ok"] else 0
        run_wo += 1 if c["without_ok"] else 0
        cum_w.append(100.0 * run_w / (i + 1))
        cum_wo.append(100.0 * run_wo / (i + 1))

    W, H = 1080, 460
    ml, mr, mt, mb = 70, 210, 112, 76
    x0, x1 = ml, W - mr
    y0, y1 = H - mb, mt   # y0 = 0%, y1 = 100%

    def px(i): return x0 + (x1 - x0) * (i / (n - 1))
    def py(v): return y0 - (y0 - y1) * (v / 100.0)  # 0% at bottom, 100% at top

    pts_w = [(px(i), py(v)) for i, v in enumerate(cum_w)]
    pts_wo = [(px(i), py(v)) for i, v in enumerate(cum_wo)]

    dots_w = "".join(f'<circle cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="4.5" fill="{BLUE}"/>' for p in pts_w)
    dots_wo = "".join(f'<circle cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="4.5" fill="{ORANGE}"/>' for p in pts_wo)

    # subtle dashed reference at the 50% level where "without" settles
    ref = f'<line x1="{x0}" y1="{py(50):.1f}" x2="{W-40}" y2="{py(50):.1f}" stroke="{GREY}" stroke-dasharray="2 6"/>'
    yaxis = "".join(
        f'<text x="{ml-12}" y="{py(v)+4:.1f}" fill="{MUTED}" font-size="12" '
        f'text-anchor="end" font-family="ui-monospace, Consolas, monospace">{v}%</text>'
        for v in (0, 25, 50, 75, 100)
    ) + "".join(
        f'<line x1="{ml-6}" y1="{py(v):.1f}" x2="{ml}" y2="{py(v):.1f}" stroke="{GREY}"/>'
        for v in (0, 25, 50, 75, 100)
    )
    xaxis = "".join(
        f'<text x="{px(i):.1f}" y="{y0+24:.1f}" fill="{MUTED}" font-size="11.5" '
        f'text-anchor="middle" font-family="ui-monospace, Consolas, monospace">{i+1}</text>'
        for i in range(n)
    ) + f'<text x="{(x0+x1)/2:.0f}" y="{y0+48}" fill="{MUTED}" font-size="11.5" text-anchor="middle">question number</text>'
    baseline = f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y0}" stroke="{GREY}"/>'

    acc_w, acc_wo = cum_w[-1], cum_wo[-1]

    out = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"
     viewBox="0 0 {W} {H}" role="img"
     aria-label="Answer accuracy with and without the Nimble pre-step: {acc_w:.0f}% vs {acc_wo:.0f}%">
  <rect width="{W}" height="{H}" fill="#ffffff"/>

  <!-- title block -->
  <text x="{ml}" y="44" fill="{DARK}" font-size="22" font-weight="700"
        font-family="system-ui, -apple-system, Segoe UI, sans-serif">
    Answer accuracy — with vs without the Nimble pre-step
  </text>
  <text x="{ml}" y="68" fill="{MUTED}" font-size="13">
    {model}, same {n} questions asked twice · cumulative share of correct answers
  </text>

  <!-- legend -->
  <g font-size="13" font-family="system-ui, -apple-system, Segoe UI, sans-serif">
    <rect x="{ml}" y="88" width="12" height="12" rx="3" fill="{BLUE}"/>
    <text x="{ml+20}" y="99" fill="{DARK}" font-weight="600">with pre-step</text>
    <rect x="{ml+130}" y="88" width="12" height="12" rx="3" fill="{ORANGE}"/>
    <text x="{ml+150}" y="99" fill="{DARK}" font-weight="600">without</text>
  </g>

  {ref}{yaxis}{baseline}{xaxis}

  <!-- cumulative accuracy -->
  <path d="{smooth_path(pts_wo)}" fill="none" stroke="{ORANGE}" stroke-width="3.5" stroke-linecap="round"/>
  <path d="{smooth_path(pts_w)}" fill="none" stroke="{BLUE}" stroke-width="3.5" stroke-linecap="round"/>
  {dots_wo}{dots_w}

  <!-- end labels, named so nobody has to guess which line wins -->
  <text x="{x1+16}" y="{py(acc_w)-2:.1f}" fill="{BLUE}" font-size="17" font-weight="700"
        font-family="ui-monospace, Consolas, monospace">{acc_w:.0f}%</text>
  <text x="{x1+16}" y="{py(acc_w)+16:.1f}" fill="{BLUE}" font-size="12">with pre-step</text>
  <text x="{x1+16}" y="{py(acc_wo)-2:.1f}" fill="{ORANGE}" font-size="17" font-weight="700"
        font-family="ui-monospace, Consolas, monospace">{acc_wo:.0f}%</text>
  <text x="{x1+16}" y="{py(acc_wo)+16:.1f}" fill="{ORANGE}" font-size="12">without</text>

  <text x="{ml}" y="{H-18}" fill="{MUTED}" font-size="11">
    scored by exact-fact matching · no LLM judge · reproduce: python benchmarks/run_benchmark.py --live
  </text>
</svg>
"""
    return out


def main() -> int:
    src = RESULTS / "live_results.json"
    if not src.exists():
        print("no results — run benchmarks/run_benchmark.py --live first")
        return 1
    payload = json.loads(src.read_text(encoding="utf-8"))
    if payload.get("mode") != "live":
        print("needs a --live run")
        return 1

    CHARTS.mkdir(parents=True, exist_ok=True)
    out = CHARTS / "comparison.svg"
    out.write_text(build(payload), encoding="utf-8")
    print(f"wrote {out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())