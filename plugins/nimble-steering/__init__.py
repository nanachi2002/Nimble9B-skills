"""nimble-steering — a hidden decision pre-step for Hermes, powered by Nimble.

Nimble (Bespoke Labs, 9B, Ollama >= 0.35) is a *typed classifier*, not a
reasoning model: it picks one answer per question from a fixed candidate set
and returns calibrated probabilities. It cannot write prose, so it can never
"make a decision" for the agent — what it can do, in ~90ms warm, is classify.

This plugin wires that classifier into two places:

* ``pre_llm_call``  -> route the incoming user message (domain / needs-live /
  ambiguity) and inject a short note that carries CONCRETE ground truth: the
  project's real path/host and which skill to load, so the agent stops
  re-deriving where things live. It can also arm itself from the message text
  ("boot up the nimble skill").
* ``pre_tool_call`` -> gate risky tool calls: one decisive harm boolean.
  Returns ``{"action": "approve", ...}`` to escalate to the human approval
  gate. Read-only tools are allowlisted out entirely.

ARMING
------
The pre-step is OFF until armed. ``/nimble boot`` arms it for the session,
``/nimble deload`` disarms. ``boot_on_mention`` also lets the agent arm it by
just saying so in a message, which is why ``pre_llm_call`` registers always
(cheap no-op while disarmed) while ``pre_tool_call`` only registers on arm.

DESIGN NOTES (measured on this machine, RTX 5060 Ti, nimble:latest)
-------------------------------------------------------------------
* Warm latency ~90ms via 127.0.0.1 for a SMALL payload, but the real numbers
  scale with the number of questions: the routing call (2 questions on a
  ~25-token state) measures ~290ms and the gate (1 question) ~145ms. Question
  COUNT is what costs — shortening criteria text saved only 35ms.
* Do NOT use ``localhost``: on Windows it resolves to ::1 first and urllib
  retries after ~2s.  curl uses curl's own happy-eyeballs and is unaffected,
  which is why curl measured 0.4s while urllib measured 2.3s for the same
  payload.
* Cold load is ~30s (9.5GB model). ``keep_alive`` keeps it resident.
* One scoring prompt = one call, but the *state* is re-sent inside every
  question's prompt, so cost scales with len(state) x n_questions. The context
  cap is 8192 tokens (not the advertised 256K). Keep ``state`` tight and
  question sets small.
* Confidence is concentration, NOT accuracy. A 0.9 is not "90% right".
* Criteria caps at 26 options; the model has no notion of a candidate it
  wasn't given, so always include an escape option.

BENCHMARK FINDING (what actually justifies the pre-step)
--------------------------------------------------------
Scoring a hosted model on with-note vs without-note questions, the win is not
in the easy cases -- it is in what the model does when it *doesn't know*. Asked
"what port does aurora listen on locally?" with no note, a strong model refused
to say "I don't know": it answered about Apache Aurora (port 8081) and Amazon
Aurora (3306/5432), and invented `./gradlew test` for the test command. The
note contains the actual answer, so the guess never happens.

That is the argument for this plugin: it does not make the model smarter, it
removes the opportunity to hallucinate project facts the model was never given.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

logger = logging.getLogger("plugin.nimble_steering")

# --------------------------------------------------------------------------
# Configuration (overridable via config.yaml -> plugins.entries.<id>.settings)
# --------------------------------------------------------------------------

#: 127.0.0.1 explicitly — "localhost" costs ~2s/turn on Windows (see module docstring).
DEFAULT_BASE_URL = os.environ.get("NIMBLE_BASE_URL") or "http://127.0.0.1:11434"
DEFAULT_MODEL = os.environ.get("NIMBLE_MODEL") or "nimble"
#: Keeps the 9.5GB model resident so the next turn pays ~90ms, not a 30s reload.
DEFAULT_KEEP_ALIVE = "30m"
#: Per-call HTTP timeout. The first call after an idle period includes model load.
DEFAULT_TIMEOUT_S = 30.0
#: Turns slower than this are not waited for: the pre-step is advisory and must
#: never be the reason a reply stalls. Callers pass whatever budget they have.
DEFAULT_BUDGET_S = 3.0
#: 0 disables the tool gate entirely (route-only mode), saving 2 calls per tool call.
DEFAULT_HARM_THRESHOLD = 0.70
#: Below this routing confidence the injected context says "uncertain" instead
#: of asserting the domain. Measured: a genuinely ambiguous message scored 0.51.
DEFAULT_ROUTE_MIN_CONFIDENCE = 0.60

#: Arming phrases for boot_on_mention. Deliberately narrow — the pre-step should
#: not arm itself because a message merely contains the word "nimble".
BOOT_PHRASES = (
    "boot up the nimble skill", "boot nimble", "nimble skill up",
    "enable nimble routing", "arm nimble", "load the nimble skill",
)
DELOAD_PHRASES = (
    "deload the nimble skill", "deload nimble", "de load nimble",
    "disable nimble routing", "disarm nimble", "unload the nimble skill",
)


# --------------------------------------------------------------------------
# Session arming state (in-memory: a restart correctly returns to disarmed)
# --------------------------------------------------------------------------

_armed: set[str] = set()
_armed_lock = threading.Lock()
_last_boot_turn: dict[str, str] = {}


def _is_armed(session_id: str) -> bool:
    with _armed_lock:
        return session_id in _armed


def _set_armed(session_id: str, value: bool) -> None:
    with _armed_lock:
        if value:
            _armed.add(session_id)
        else:
            _armed.discard(session_id)


def _contains_phrase(text: str, phrases: tuple[str, ...]) -> str | None:
    """Return the matched phrase, ignoring case/hyphen spacing."""
    low = (text or "").lower()
    for phrase in phrases:
        if phrase in low:
            return phrase
    # tolerate "de-load" / "boot-up" spellings
    squeezed = low.replace("-", " ").replace("  ", " ")
    for phrase in phrases:
        if phrase in squeezed:
            return phrase
    return None


def _flatten(text: str) -> str:
    """Collapse whitespace for state payloads (tokens are the scarce resource)."""
    return " ".join((text or "").split())[:4000]


# --------------------------------------------------------------------------
# Nimble client
# --------------------------------------------------------------------------


def _ask(base_url: str, model: str, state, questions: dict, timeout: float,
         keep_alive: str | None) -> dict | None:
    """POST /v1/systemone. Returns the parsed body, or None on any failure.

    Failures are swallowed on purpose: the pre-step is an optimisation, and a
    dead Ollama must degrade to "no steering", never to a broken turn.
    """
    payload = {"model": model, "state": state, "questions": questions}
    if keep_alive:
        payload["keep_alive"] = keep_alive
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/v1/systemone", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.debug("nimble: request failed (%s)", exc)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.debug("nimble: unparseable response (%s)", exc)
    return None


def _budgeted(fn, budget_s: float):
    """Run ``fn`` in a daemon thread; give up after ``budget_s`` and return None.

    A daemon thread can't be killed, but the caller (and therefore the turn)
    stops waiting — which is the property that matters here.
    """
    box: dict = {}

    def runner():
        try:
            box["result"] = fn()
        except Exception as exc:  # never let a pre-step raise into the agent loop
            box["error"] = exc

    thread = threading.Thread(target=runner, name="nimble-steer", daemon=True)
    thread.start()
    thread.join(budget_s)
    return box.get("result")


# --------------------------------------------------------------------------
# Question sets
# --------------------------------------------------------------------------

ROUTE_QUESTIONS = {
    "domain": {
        "type": "choice",
        "instructions": "What area does this message concern?",
        "criteria": {
            "aurora": "aurora — the example web service this repo ships with",
            "hermes": "Hermes itself — config, skills, plugins, agent behaviour",
            "infra": "Servers, networking, VMs, Docker, SSH",
            "web": "General web research or browsing",
            "code": "General programming with no project named",
            "chat": "Conversation only, no task",
            "none": "None of the above",
        },
    },
    "needs_live": {
        "type": "noul",
        "instructions": "Does answering this require live data from the internet or a running system?",
    },
    "ambiguous": {
        "type": "noul",
        "instructions": "Is the request missing information that must be asked before acting?",
    },
}

#: Concrete, per-domain ground truth injected when a domain is confidently picked.
#: THIS IS THE WHOLE POINT OF THE PLUGIN. A bare label ("domain=aurora") is easy
#: for a model to ignore; a real path, host and branch are not. The example
#: `aurora` entries are a demo project — REPLACE THEM WITH YOUR OWN. Add one
#: entry per project you want the agent to stop asking about.
#:
#: Keep each hint to one line: it is injected on every turn it matches, so it
#: is a permanent tax on the context window.
DOMAIN_HINTS = {
    "aurora": ("aurora lives at ~/code/aurora (FastAPI). Run it with `make serve` -> "
               "http://localhost:8080; tests with `make test`; dev database `aurora_dev`; "
               "production deploys from the `release` branch only; owned by the Platform team."),
    "infra": ("hosts: staging-db at 10.20.30.41 (ssh deploy@10.20.30.41), "
              "staging-web at 10.20.30.42; logs under /var/log/aurora."),
    "hermes": ("changing Hermes itself: load skill hermes-agent first; use "
               "`hermes config set`, never hand-edit config.yaml."),
}

#: Single harm question. Measured: 1 question = ~145ms, 2 questions = ~434ms,
#: and it is the *count* of questions, not their wording, that costs (shortening
#: the criteria text saved 35ms). A clean boolean also beats a 3-way "risk" pick:
#: Nimble scored a benign tool_describe as safe 0.33 / caution 0.30 / destructive
#: 0.37 — argmax said "destructive" on a flat spread. One decisive boolean is both
#: faster and more robust than compensating for argmax noise.
GATE_QUESTIONS = {
    "harm": {
        "type": "noul",
        "instructions": "Could this action cause loss of data, or damage a system the user cares about?",
    },
}


def _context_block(session_id: str, msg: str, base_url: str, model: str,
                   timeout: float, keep_alive: str, budget: float) -> str | None:
    """Classify an incoming user message and render the steering note."""
    answer = _budgeted(
        lambda: _ask(base_url, model, _flatten(msg), ROUTE_QUESTIONS, timeout, keep_alive),
        budget,
    )
    if not answer or not isinstance(answer.get("answers"), dict):
        return None
    a = answer["answers"]

    domain = a.get("domain") or {}
    choice = str(domain.get("choice") or "").strip()
    conf = domain.get("confidence")
    try:
        conf_f = float(conf)
    except (TypeError, ValueError):
        conf_f = 0.0

    def prob_of(name: str) -> float:
        try:
            return float((domain.get("probabilities") or {}).get(name, 0.0))
        except (TypeError, ValueError):
            return 0.0

    def noul_of(name: str) -> float | None:
        v = (a.get(name) or {}).get("noul")
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    min_conf = DEFAULT_ROUTE_MIN_CONFIDENCE
    confident = conf_f >= min_conf and choice and choice != "none"
    if confident:
        domain_text = choice
    else:
        # Surface the runner-up instead of asserting a wrong domain.
        probs = domain.get("probabilities") or {}
        ranked = sorted(
            ((k, float(v)) for k, v in probs.items() if k != "none"),
            key=lambda kv: kv[1], reverse=True,
        )[:2]
        domain_text = "unclear" + (
            " (" + " or ".join(f"{k} {v:.2f}" for k, v in ranked) + ")" if ranked else ""
        )

    needs_live = noul_of("needs_live")
    ambiguous = noul_of("ambiguous")

    lines = [
        "[nimble pre-step] Local classifier — a probabilistic guess, not a reasoner. "
        "Ignore the label if it looks wrong; if the domain fits, prefer the ground truth "
        "below over asking or re-deriving.",
        f"domain={domain_text} ({conf_f:.2f}) | live-data-needed="
        f"{'yes' if (needs_live or 0) >= 0.5 else 'no'} ({needs_live if needs_live is not None else '?'})",
    ]

    # The concrete payload: a real path/host + which skill to load. Only added on a
    # confident domain pick, so a wrong guess can't confidently send the agent wrong.
    hint = DOMAIN_HINTS.get(choice) if confident else None
    if hint:
        lines.append(f"ground truth — {hint}")
    if (ambiguous or 0) >= 0.5:
        lines.append("flagged as possibly under-specified — check before assuming.")
    if needs_live is not None and needs_live >= 0.7:
        lines.append("live data needed — a tool lookup beats answering from memory.")
    return "\n".join(lines)


#: Tools that cannot mutate anything. Never worth a classification call, and
#: gating them produces pure friction.
READ_ONLY_TOOLS = frozenset({
    "read_file", "search_files", "skills_list", "skill_view", "session_search",
    "web_search", "web_extract", "todo_list", "tool_search", "tool_describe",
    "nimble_decide", "vision_analyze", "browser_vault_list", "list_dir",
})

#: The classifier must be this sure before a label is acted on. Kept as a named
#: constant because the *principle* outlived the code that first needed it: a
#: benign tool_describe once scored safe 0.33 / caution 0.30 / destructive 0.37,
#: and argmax alone would have escalated a read-only call. Flat distributions
#: mean "no opinion", not "danger".
DESTRUCTIVE_MIN_PROB = 0.50
DESTRUCTIVE_MIN_MARGIN = 0.20


def _gate_verdict(tool_name: str, args: dict, base_url: str, model: str,
                  timeout: float, keep_alive: str, budget: float,
                  harm_threshold: float) -> dict | None:
    """Classify a pending tool call. Returns a directive dict, or None to proceed."""
    if tool_name in READ_ONLY_TOOLS:
        return None
    try:
        args_text = json.dumps(args, ensure_ascii=False, default=str)
    except Exception:
        args_text = str(args)
    state = f"Tool: {tool_name}\nArguments: {args_text[:3000]}"
    answer = _budgeted(
        lambda: _ask(base_url, model, state, GATE_QUESTIONS, timeout, keep_alive),
        budget,
    )
    if not answer or not isinstance(answer.get("answers"), dict):
        return None
    a = answer["answers"]

    harm = (a.get("harm") or {}).get("noul")
    try:
        harm_f = float(harm)
    except (TypeError, ValueError):
        harm_f = 0.0

    if harm_f < harm_threshold:
        return None
    return {
        "action": "approve",  # escalate to the human gate — never auto-veto
        "message": f"Nimble flagged {tool_name} as potentially harmful (harm={harm_f:.2f}). "
                   f"Confirm before running.",
    }


# --------------------------------------------------------------------------
# Plugin registration
# --------------------------------------------------------------------------


def _register_gate(ctx) -> None:
    """Register the tool gate. Called once, on arm — the gate is not free."""
    def on_pre_tool_call(tool_name: str = "", args: dict | None = None,
                         session_id: str = "", **kwargs):
        if not _is_armed(session_id):
            return None
        if tool_name == "nimble_decide":  # never gate our own tool
            return None

        cfg = _cfg(ctx)
        if cfg["harm_threshold"] <= 0:
            return None
        started = time.monotonic()
        try:
            verdict = _gate_verdict(
                tool_name, args or {}, cfg["base_url"], cfg["model"], cfg["timeout"],
                cfg["keep_alive"], cfg["budget"], cfg["harm_threshold"],
            )
        except Exception as exc:
            logger.debug("nimble gate failed: %s", exc)
            return None
        elapsed = (time.monotonic() - started) * 1000
        if verdict:
            logger.info("nimble gate: %s -> %s (%.0fms)", tool_name, verdict["action"], elapsed)
        return verdict

    ctx.register_hook("pre_tool_call", on_pre_tool_call)


def _cfg(ctx) -> dict:
    """Read settings, tolerating a missing/odd config tree."""
    def get(key, default):
        try:
            value = ctx.get_config(key, default)
            return default if value is None else value
        except Exception:
            return default

    return {
        "base_url": str(get("base_url", DEFAULT_BASE_URL)).rstrip("/"),
        "model": str(get("model", DEFAULT_MODEL)),
        "keep_alive": get("keep_alive", DEFAULT_KEEP_ALIVE),
        "timeout": float(get("timeout_s", DEFAULT_TIMEOUT_S)),
        "budget": float(get("budget_s", DEFAULT_BUDGET_S)),
        "harm_threshold": float(get("harm_threshold", DEFAULT_HARM_THRESHOLD)),
        "route_enabled": bool(get("route_enabled", True)),
        "gate_enabled": bool(get("gate_enabled", True)),
        "boot_on_mention": bool(get("boot_on_mention", True)),
    }


def register(ctx) -> None:
    """Plugin entry point."""
    # ---- /nimble ------------------------------------------------------
    def command(raw_args: str) -> str:
        arg = (raw_args or "").strip().lower()
        session_id = _current_session_id()

        if arg in ("boot", "on", "up", "start"):
            _set_armed(session_id, True)
            _register_gate(ctx)
            cfg = _cfg(ctx)
            warm = _warm(ctx)
            return (
                "Nimble steering ARMED for this session.\n"
                f"  endpoint : {cfg['base_url']} ({cfg['model']})\n"
                f"  gate     : harm_threshold={cfg['harm_threshold']} "
                f"(0 disables), budget={cfg['budget']}s\n"
                f"  warmup   : {warm}\n"
                "  /nimble status | /nimble deload"
            )
        if arg in ("deload", "off", "down", "stop"):
            _set_armed(session_id, False)
            return "Nimble steering DISARMED for this session. Use /nimble boot to re-arm."
        if arg in ("status", "stat", ""):
            cfg = _cfg(ctx)
            reachable = _ping(cfg)
            return (
                f"Nimble steering: {'ARMED' if _is_armed(session_id) else 'disarmed'}\n"
                f"  endpoint : {cfg['base_url']} ({cfg['model']}) — {reachable}\n"
                f"  routing  : {'on' if cfg['route_enabled'] else 'off'}\n"
                f"  gate     : {'on' if cfg['gate_enabled'] else 'off'} "
                f"(harm_threshold={cfg['harm_threshold']})\n"
                f"  warmup   : {_warm(ctx, quiet=True)}"
            )
        if arg in ("test", "check"):
            return _selftest(ctx)
        return ("Usage: /nimble [boot|deload|status|test]\n"
                "  boot   — arm the hidden pre-step for this session\n"
                "  deload — disarm it\n"
                "  status — show state and endpoint health\n"
                "  test   — run a live classification through the model")

    ctx.register_command("nimble", command, description="Arm/disable the Nimble decision pre-step",
                         args_hint="[boot|deload|status|test]")

    # ---- hidden pre-step ----------------------------------------------
    def on_pre_llm_call(user_message: str = "", session_id: str = "",
                        conversation_history: list | None = None, **kwargs):
        cfg = _cfg(ctx)
        text = user_message if isinstance(user_message, str) else str(user_message or "")

        if cfg["boot_on_mention"]:
            if _contains_phrase(text, DELOAD_PHRASES):
                _set_armed(session_id, False)
                return {"context": "[nimble] steering disarmed for this session."}
            if _contains_phrase(text, BOOT_PHRASES):
                _set_armed(session_id, True)
                _register_gate(ctx)
                _warm(ctx)

        if not _is_armed(session_id) or not cfg["route_enabled"]:
            return None
        if not text.strip():
            return None
        try:
            block = _context_block(
                session_id, text, cfg["base_url"], cfg["model"], cfg["timeout"],
                cfg["keep_alive"], cfg["budget"],
            )
        except Exception as exc:
            logger.debug("nimble routing failed: %s", exc)
            return None
        return {"context": block} if block else None

    ctx.register_hook("pre_llm_call", on_pre_llm_call)

    # ---- agent-facing tool --------------------------------------------
    schema = {
        "name": "nimble_decide",
        "description": (
            "Fast typed classification via the local Nimble 9B decision model (~90ms warm). "
            "Use it to make a quick call on a short, self-contained question: route, label, "
            "verify a condition, or triage a batch. It is a CLASSIFIER, not a reasoner — it "
            "only picks from the options you give it, cannot explain itself, and its confidence "
            "is concentration, not accuracy. Keep state under ~2000 chars and options under 26."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "description": "The text to judge. Short and self-contained is best.",
                },
                "questions": {
                    "type": "object",
                    "description": (
                        "Map of question name -> {type, instructions, criteria}. type is "
                        "'choice' (criteria = {option: description|None}, 2-26 options), "
                        "'noul' (true/false; criteria optional), or 'score' (criteria = "
                        "ordered level descriptions, lowest first). Max 64 questions."
                    ),
                },
            },
            "required": ["state", "questions"],
        },
    }

    def nimble_decide(state: str = "", questions: dict | None = None) -> str:
        cfg = _cfg(ctx)
        if not isinstance(questions, dict) or not questions:
            return json.dumps({"error": "questions must be a non-empty object"})
        if len(questions) > 64:
            return json.dumps({"error": "at most 64 questions per call"})
        started = time.monotonic()
        answer = _ask(cfg["base_url"], cfg["model"], _flatten(state), questions,
                      max(cfg["timeout"], 60.0), cfg["keep_alive"])
        if answer is None:
            return json.dumps({
                "error": "nimble is unreachable or returned an error",
                "endpoint": f"{cfg['base_url']}/v1/systemone",
                "hint": "is `ollama serve` running and is the nimble model pulled?",
            })
        answer["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return json.dumps(answer, ensure_ascii=False)

    ctx.register_tool(
        name="nimble_decide",
        toolset="nimble",
        schema=schema,
        handler=nimble_decide,
        description="Typed classification via the local Nimble decision model",
        emoji="⚡",
    )

    logger.info("nimble-steering registered (disarmed until /nimble boot)")


# --------------------------------------------------------------------------
# Small helpers used by the slash command
# --------------------------------------------------------------------------


def _current_session_id() -> str:
    """Best-effort current session id for slash-command context."""
    try:
        from hermes_cli.plugins import get_active_session_id  # type: ignore
        value = get_active_session_id()
        if value:
            return str(value)
    except Exception:
        pass
    try:
        from run_agent import _CURRENT_SESSION_ID  # type: ignore
        return str(_CURRENT_SESSION_ID)
    except Exception:
        return os.environ.get("HERMES_SESSION_ID", "") or "default"


def _ping(cfg: dict) -> str:
    req = urllib.request.Request(f"{cfg['base_url']}/api/version")
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return "reachable (ollama " + json.loads(resp.read().decode()).get("version", "?") + ")"
    except Exception as exc:
        return f"UNREACHABLE ({type(exc).__name__})"


def _warm(ctx, quiet: bool = False) -> str:
    """Pin the model in memory so the first real call isn't a 30s load."""
    cfg = _cfg(ctx)
    started = time.monotonic()
    answer = _ask(
        cfg["base_url"], cfg["model"], "warmup",
        {"ok": {"type": "noul", "instructions": "Is this a warmup ping?"}},
        timeout=max(cfg["timeout"], 120.0), keep_alive=cfg["keep_alive"],
    )
    ms = int((time.monotonic() - started) * 1000)
    if answer is None:
        return f"FAILED — model not loaded ({_ping(cfg)})"
    return f"model resident in {ms}ms" + (" (includes ~30s first-load if it was cold)" if ms > 5000 else "")


def _selftest(ctx) -> str:
    cfg = _cfg(ctx)
    lines = [f"endpoint: {cfg['base_url']}  model: {cfg['model']}  {_ping(cfg)}"]
    probe = {
        "route": {
            "type": "choice",
            "instructions": "Which handler fits best?",
            "criteria": {"local": "General knowledge", "search": "Needs live data",
                         "tool": "Needs to run something", "clarify": "Ambiguous"},
        },
        "harm": {"type": "noul", "instructions": "Could this action cause damage?"},
    }
    for label, state in (
        ("benign", "What is the capital of France?"),
        ("dangerous", "A shell command that recursively deletes the user's home Documents folder"),
    ):
        started = time.monotonic()
        answer = _ask(cfg["base_url"], cfg["model"], state, probe, timeout=cfg["timeout"],
                      keep_alive=cfg["keep_alive"])
        ms = int((time.monotonic() - started) * 1000)
        if answer is None:
            lines.append(f"  {label:10s}: FAILED")
            continue
        a = answer.get("answers", {})
        route = (a.get("route") or {}).get("choice", "?")
        harm = (a.get("harm") or {}).get("noul", "?")
        harm_s = f"{harm:.2f}" if isinstance(harm, (int, float)) else str(harm)
        lines.append(f"  {label:10s}: route={route:8s} harm={harm_s:5s} ({ms}ms)")
    lines.append(f"  armed for this session: {_is_armed(_current_session_id())}")
    return "\n".join(lines)
