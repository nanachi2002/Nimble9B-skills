#!/usr/bin/env python3
"""
Render the benchmark results as a self-contained SVG.

No dependencies, no network: run it after a benchmark and it rewrites the
chart it finds. Embed the output anywhere GitHub renders an image
(README, issue, PR comment) — GitHub sanitises inline SVG in Markdown, so
link the .svg file rather than pasting raw markup.

    python benchmarks/make_chart.py                 # uses live_results.json
    python benchmarks/make_chart.py --offline       # uses offline_results.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
CHARTS = ROOT / "charts"

# Palette borrowed from the repo's own docs — dark, high contrast.
BG = "#131720"
FG = "#e6edf3"
MUTED = "#8b98a9"
ACCENT = "#6fc3e8"
WARN = "#e8a06f"
TRACK = "#232a36"


def _fmt_ms(ms: int) -> str:
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms}ms"


def bar(x: int, y: int, width: int, height: int, frac: float, colour: str, label: str,
        sub: str, label_colour: str = FG) -> str:
    """One horizontal bar with its label above and value to the right."""
    filled = max(2, int(width * max(0.0, min(1.0, frac))))
    return f"""
  <g>
    <text x="{x}" y="{y - 10}" fill="{label_colour}" font-size="15" font-weight="600">{label}</text>
    <rect x="{x}" y="{y}" width="{width}" height="{height}" rx="5" fill="{TRACK}"/>
    <rect x="{x}" y="{y}" width="{filled}" height="{height}" rx="5" fill="{colour}"/>
    <text x="{x + width + 16}" y="{y + height - 5}" fill="{MUTED}" font-size="15"
          font-family="ui-monospace, Consolas, monospace">{sub}</text>
  </g>"""


def build_svg(payload: dict) -> str:
    cases = payload["cases"]
    live = payload.get("mode") == "live"
    model = payload.get("model") or "n/a"

    width, row_h, bar_h = 900, 58, 22
    top = 150

    if live:
        def rate(arm):
            vals = [c[f"{arm}_ok"] for c in cases if f"{arm}_ok" in c]
            return 100.0 * sum(vals) / len(vals) if vals else 0.0
        def med(arm):
            vals = [c[f"{arm}_ms"] for c in cases if f"{arm}_ms" in c]
            return int(statistics.median(vals)) if vals else 0

        answer_with, answer_without = rate("with"), rate("without")
        ms_with, ms_without = med("with"), med("without")
        max_ms = max(ms_with, ms_without, 1)

        rows = [
            ("Answers the question correctly", ACCENT, answer_with / 100, f"{answer_with:.0f}%  (with pre-step)"),
            ("", TRACK, 0, ""),
            ("Answers the question correctly", WARN, answer_without / 100, f"{answer_without:.0f}%  (without)"),
            ("", TRACK, 0, ""),
            ("Response time (lower is better)", ACCENT, ms_with / max_ms, f"{_fmt_ms(ms_with)}  (with pre-step)"),
            ("", TRACK, 0, ""),
            ("Response time (lower is better)", WARN, ms_without / max_ms, f"{_fmt_ms(ms_without)}  (without)"),
        ]
        title = "Same model, same questions — with and without the Nimble pre-step"
        subtitle = f"model: {model} &nbsp;·&nbsp; {len(cases)} cases &nbsp;·&nbsp; higher is better, except time"
    else:
        scored = [c for c in cases if c.get("domain_correct") is not None]
        hits = sum(1 for c in scored if c["domain_correct"])
        acc = 100.0 * hits / len(scored) if scored else 0.0
        overhead = int(statistics.median([c["route_ms"] for c in cases]))
        rows = [
            ("Routing accuracy", ACCENT, acc / 100, f"{acc:.1f}%  ({hits}/{len(scored)})"),
            ("Published Nimble intent routing", MUTED, 0.869, "86.9%  (MASSIVE-en-US)"),
            ("Overhead per message", WARN, min(1.0, overhead / 1500), _fmt_ms(overhead)),
        ]
        title = "Nimble classifier — routing accuracy and overhead"
        subtitle = f"{len(cases)} routing cases &nbsp;·&nbsp; all local, no API calls"

    height = top + len(rows) * row_h + 70
    body = []
    y = top
    for label, colour, frac, sub in rows:
        if not label:
            y += 22
            continue
        body.append(bar(70, y, 560, bar_h, frac, colour, label, sub))
        y += row_h

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
     viewBox="0 0 {width} {height}" role="img"
     aria-label="{title}">
  <rect width="{width}" height="{height}" fill="{BG}"/>
  <text x="70" y="58" fill="{FG}" font-size="23" font-weight="700"
        font-family="system-ui, -apple-system, Segoe UI, sans-serif">{title}</text>
  <text x="70" y="88" fill="{MUTED}" font-size="14">{subtitle}</text>
  <text x="70" y="112" fill="{MUTED}" font-size="12" font-family="ui-monospace, Consolas, monospace">
    generated {payload.get('generated_at', '?')} — reproduce with benchmarks/run_benchmark.py
  </text>
  <line x1="70" y1="126" x2="{width - 70}" y2="126" stroke="{TRACK}" stroke-width="1"/>
{''.join(body)}
</svg>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="chart offline_results.json instead")
    args = ap.parse_args()

    src = RESULTS / ("offline_results.json" if args.offline else "live_results.json")
    if not src.exists():
        print(f"no results at {src} — run benchmarks/run_benchmark.py first")
        return 1
    payload = json.loads(src.read_text(encoding="utf-8"))
    CHARTS.mkdir(parents=True, exist_ok=True)
    out = CHARTS / ("offline.svg" if args.offline else "with-vs-without.svg")
    out.write_text(build_svg(payload), encoding="utf-8")
    print(f"wrote {out}  ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
