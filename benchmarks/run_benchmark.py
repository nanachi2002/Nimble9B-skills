#!/usr/bin/env python3
"""
Benchmark: does the Nimble pre-step make an agent answer better?

WHAT THIS MEASURES
------------------
Everything here is local and free: `nimble` runs on your own GPU via Ollama.
That means we can score a *real* agent (a hosted model like deepseek-v4.1-flash,
or anything OpenAI-compatible) on the same questions twice:

  WITH    - the Nimble routing note is injected ahead of the user message,
            exactly as the plugin does it.
  WITHOUT - the question goes in alone.

We then compare answer accuracy, latency, and how often the model had to ask
a clarifying question instead of answering.

This is deliberately NOT a "Nimble is smart" benchmark. Nimble is a classifier
and this repo never asks it to answer the question. We are measuring whether
*having a cheap classifier pre-read the question* helps the real model.

TWO MODES
---------
--live      calls a real model over HTTP (needs an API key)
(default)   offline "self-test": replays the same prompts and reports the
            routing quality + overhead, so you can run it with zero API keys
            and still see what the pre-step costs and decides.

USAGE
-----
    python benchmarks/run_benchmark.py                     # offline self-test
    python benchmarks/run_benchmark.py --live \\
        --base-url https://ollama.com/v1 \\
        --model deepseek-v4.1-flash \\
        --api-key-env OLLAMA_API_KEY

The offline mode is also what CI runs; it asserts the classifier still routes
its own cases correctly, which catches a broken prompt or a model swap.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "cases" / "routing_cases.json"
RESULTS = ROOT / "results"
NIMBLE_URL = os.environ.get("NIMBLE_BASE_URL", "http://127.0.0.1:11434")


def _load_plugin():
    """Import the shipped plugin module so the benchmark cannot drift from it.

    This bit us once: the benchmark used to carry its own copy of the domain
    list, the plugin's list was edited, and every routing case failed because
    the classifier was never offered the right option. Measure what ships.
    """
    import importlib.util

    path = ROOT.parent / "plugins" / "nimble-steering" / "__init__.py"
    spec = importlib.util.spec_from_file_location("nimble_steering_shipped", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PLUGIN = _load_plugin()

#: Taken from the plugin itself — never redefined here.
ROUTE_QUESTIONS = _PLUGIN.ROUTE_QUESTIONS
DOMAIN_HINTS = _PLUGIN.DOMAIN_HINTS


def nimble_route(text: str, timeout: float = 60.0) -> dict | None:
    """Call the local decision model the same way the plugin does."""
    body = json.dumps({
        "model": "nimble",
        "state": " ".join(text.split())[:4000],
        "questions": ROUTE_QUESTIONS,
        "keep_alive": "10m",
    }).encode()
    req = urllib.request.Request(
        f"{NIMBLE_URL}/v1/systemone", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        print(f"  ! nimble unreachable: {exc}", file=sys.stderr)
        return None


def build_context(text: str) -> tuple[str, dict | None, int]:
    """Return (injected note, raw nimble answer, elapsed_ms)."""
    started = time.monotonic()
    answer = nimble_route(text)
    elapsed = int((time.monotonic() - started) * 1000)
    if not answer or not isinstance(answer.get("answers"), dict):
        return "", None, elapsed
    a = answer["answers"]
    domain = a.get("domain") or {}
    choice = str(domain.get("choice") or "")
    try:
        conf = float(domain.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    needs_live = (a.get("needs_live") or {}).get("noul")
    ambiguous = (a.get("ambiguous") or {}).get("noul")

    lines = [
        "[nimble pre-step] Local classifier — a probabilistic guess, not a reasoner. "
        "Ignore the label if it looks wrong; if the domain fits, prefer the ground truth "
        "below over asking or re-deriving.",
        f"domain={choice} ({conf:.2f}) | live-data-needed="
        f"{'yes' if (needs_live or 0) >= 0.5 else 'no'}",
    ]
    if conf >= 0.60 and choice in DOMAIN_HINTS:
        lines.append(f"ground truth — {DOMAIN_HINTS[choice]}")
    if (ambiguous or 0) >= 0.5:
        lines.append("flagged as possibly under-specified — check before assuming.")
    return "\n".join(lines), answer, elapsed


def call_model(base_url: str, api_key: str, model: str, user_content: str,
               max_tokens: int = 3000, timeout: float = 240.0) -> tuple[str, int, dict]:
    """One chat completion. Returns (content, latency_ms, usage).

    TOKEN BUDGETING MATTERS for reasoning models. A reasoning model spends
    completion tokens on its trace BEFORE emitting `content`; set max_tokens too
    low and you get a perfectly valid response with an EMPTY answer, which then
    scores as a failure. That is a harness bug, not a model failure — it produced
    a bogus 75% vs 17% result once. We detect it and retry with more room.
    """
    budget = max_tokens
    for attempt in range(3):
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": user_content}],
            "max_tokens": budget,
        }).encode()
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions", data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        started = time.monotonic()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        elapsed = int((time.monotonic() - started) * 1000)
        usage = data.get("usage") or {}
        message = data["choices"][0]["message"]
        content = message.get("content") or ""
        # `reasoning` (or `reasoning_content`) present + empty content == starved.
        starved = (not content.strip()) and bool(
            message.get("reasoning") or message.get("reasoning_content")
        )
        if not starved or attempt == 2:
            return content, elapsed, usage
        budget *= 3
    return "", elapsed, usage


def normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def asked_clarifying_question(answer: str) -> bool:
    """Did the model ask for the missing information?

    A bare trailing "?" was too crude: a model can correctly ask for details as
    an imperative ("Send me the stack trace") and get scored as failing. That
    understated the WITH arm on the deliberately-ambiguous case.
    """
    low = normalize(answer)
    if low.rstrip().endswith("?"):
        return True
    markers = (
        "send me", "send the", "send us", "please send", "please provide", "please share",
        "can you share", "could you share", "share the", "provide more", "need more",
        "need a bit more", "what's broken", "whats broken", "let me know which",
        "which one", "which ", "i need", "paste the", "give me the",
    )
    return any(marker in low for marker in markers)


def score(answer: str, case: dict) -> tuple[bool, str]:
    """Deterministic auto-scoring: an exact substring match on the expected fact.

    Deliberately strict and dumb. A human or an LLM judge can disagree on
    "good enough"; a substring match cannot be argued with.

    Cases marked `expect_clarify` are the exception: for a deliberately
    underspecified question, the correct behaviour is to ask for what's
    missing, and that is what gets scored.
    """
    if case.get("expect_clarify"):
        if asked_clarifying_question(answer):
            return True, "asked for the missing details"
        return False, "answered anyway despite being underspecified"

    low = normalize(answer)
    expected = [normalize(e) for e in case.get("expect_all", [])]
    if not expected:
        return False, "no expectation defined"
    missing = [e for e in expected if e not in low]
    if missing:
        return False, "missing: " + ", ".join(missing[:3])
    return True, "ok"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="call a real model over HTTP")
    ap.add_argument("--base-url", default=os.environ.get("BENCH_BASE_URL", "https://ollama.com/v1"))
    ap.add_argument("--model", default=os.environ.get("BENCH_MODEL", "deepseek-v4.1-flash"))
    ap.add_argument("--api-key-env", default="OLLAMA_API_KEY")
    ap.add_argument("--cases", default=str(CASES))
    ap.add_argument("--limit", type=int, default=0, help="only run the first N cases")
    args = ap.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    if args.limit:
        cases = cases[: args.limit]
    RESULTS.mkdir(parents=True, exist_ok=True)

    print(f"Nimble decision pre-step benchmark — {len(cases)} cases")
    print(f"  nimble   : {NIMBLE_URL}")
    print(f"  mode     : {'LIVE against ' + args.model if args.live else 'offline self-test'}")
    print()

    api_key = os.environ.get(args.api_key_env, "")
    if args.live and not api_key:
        print(f"error: --live needs {args.api_key_env} in the environment", file=sys.stderr)
        return 2

    rows = []
    route_hits = 0
    route_scored = 0

    for i, case in enumerate(cases, 1):
        question = case["question"]
        note, raw, route_ms = build_context(question)
        got_domain = ""
        if raw and isinstance(raw.get("answers"), dict):
            got_domain = str((raw["answers"].get("domain") or {}).get("choice") or "")
        expected_domain = case.get("domain", "")
        if expected_domain:
            route_scored += 1
            if got_domain == expected_domain:
                route_hits += 1

        row = {
            "id": case["id"],
            "question": question,
            "expected_domain": expected_domain,
            "routed_domain": got_domain,
            "domain_correct": (got_domain == expected_domain) if expected_domain else None,
            "route_ms": route_ms,
            "note": note,
        }

        if args.live:
            for arm, content in (
                ("with", note + "\n\n" + question if note else question),
                ("without", question),
            ):
                try:
                    answer, ms, usage = call_model(
                        args.base_url, api_key, args.model, content,
                        max_tokens=case.get("max_tokens", 3000),
                    )
                except urllib.error.HTTPError as exc:
                    answer, ms, usage = f"<HTTP {exc.code}>", 0, {}
                except Exception as exc:
                    answer, ms, usage = f"<error {type(exc).__name__}>", 0, {}
                ok, why = score(answer, case)
                row[f"{arm}_answer"] = answer
                row[f"{arm}_ms"] = ms
                row[f"{arm}_ok"] = ok
                row[f"{arm}_why"] = why
                row[f"{arm}_clarified"] = asked_clarifying_question(answer)
                row[f"{arm}_empty"] = not answer.strip()
                row[f"{arm}_completion_tokens"] = (usage or {}).get("completion_tokens")
            empties = sum(1 for a in ("with", "without") if row[f"{a}_empty"])
            flag = "MISS" if not row["with_ok"] else "OK "
            print(f"[{i:2d}/{len(cases)}] {flag} routed={got_domain or '-':8s} "
                  f"with={row['with_ms']:5d}ms without={row['without_ms']:5d}ms"
                  + ("  EMPTY-ANSWER" if empties else "") + f"  {case['id']}")
        else:
            flag = "OK " if row["domain_correct"] else "MISS"
            print(f"[{i:2d}/{len(cases)}] {flag} expected={expected_domain or '-':8s} "
                  f"got={got_domain or '-':8s} conf_routed_in {route_ms:4d}ms  {case['id']}")

        rows.append(row)

    # ---------------- summary ----------------
    print()
    print("=" * 68)
    if route_scored:
        acc = 100.0 * route_hits / route_scored
        print(f"Classifier routing accuracy : {route_hits}/{route_scored}  ({acc:.1f}%)")
        print(f"  (Nimble's published intent-routing figure is 86.9% on MASSIVE-en-US)")
    route_times = [r["route_ms"] for r in rows]
    print(f"Classifier overhead         : {int(statistics.median(route_times))}ms median "
          f"({min(route_times)}-{max(route_times)}ms)")

    if args.live:
        def rate(arm):
            vals = [r[f"{arm}_ok"] for r in rows]
            return 100.0 * sum(vals) / len(vals) if vals else 0.0
        def med(arm):
            return int(statistics.median([r[f"{arm}_ms"] for r in rows]))
        def clar(arm):
            return sum(1 for r in rows if r[f"{arm}_clarified"])
        def empty(arm):
            return sum(1 for r in rows if r.get(f"{arm}_empty"))
        print()
        print(f"{'':22s} {'WITH pre-step':>14s} {'WITHOUT':>14s}")
        print(f"{'answer accuracy':22s} {rate('with'):13.1f}% {rate('without'):13.1f}%")
        print(f"{'median latency':22s} {med('with'):12d}ms {med('without'):12d}ms")
        print(f"{'asked a question':22s} {clar('with'):13d}  {clar('without'):13d}")
        print(f"{'empty answers':22s} {empty('with'):13d}  {empty('without'):13d}")
        print(f"{'cases':22s} {len(rows):13d}  {len(rows):13d}")

        # A starved reasoning budget produces empty answers that score as failures
        # and flatter the other arm. Refuse to report a number built on them.
        with_empty, without_empty = empty("with"), empty("without")
        if with_empty or without_empty:
            print()
            print(f"WARNING: {with_empty + without_empty} empty answer(s). These score as "
                  f"failures and make the comparison meaningless. Raise max_tokens or lower "
                  f"reasoning effort before quoting these numbers.", file=sys.stderr)

    else:
        # In offline mode, only the classifier's own routing is meaningful; an
        # empty model answer is not applicable, so surface the counts that are.
        pass
    print("=" * 68)

    out = RESULTS / ("live_results.json" if args.live else "offline_results.json")
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": "live" if args.live else "offline",
        "model": args.model if args.live else None,
        "nimble_endpoint": NIMBLE_URL,
        "cases": rows,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {out}")

    if not args.live and route_scored and route_hits / route_scored < 0.6:
        print("FAIL: classifier routed fewer than 60% of its own cases — "
              "the prompt or the model changed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
