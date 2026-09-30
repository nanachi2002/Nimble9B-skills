---
name: nimble-decision-model
description: "Use when classifying with the local Nimble decision model."
version: 1.0.0
author: nanachi2002
license: MIT
platforms: [windows, linux, macos]
metadata:
  hermes:
    tags: [ollama, nimble, classification, routing, guardrails, decision-model]
    related_skills: [hermes-agent]
---

# Nimble decision model

## When to Use

- A quick call must be made against a **fixed set of labelled options** — routing a request,
  labelling a batch, checking a yes/no condition, applying a stated policy.
- The candidate answers are known in advance and the question is short and self-contained.
- Latency matters (~90ms warm) or many decisions must be scored in one call (up to 64).

Do **not** reach for it when the answer must be *generated* (writing, explaining, reasoning,
code, nested JSON), when the input is long, or when it would be the only thing standing
between the agent and an irreversible action.

Nimble (Bespoke Labs, 9B, fine-tuned from Qwen3.5-9B) is a **typed classifier, not a
reasoner**. It has no reasoning step and cannot write prose — you give it text plus 1-64
named questions, it scores answer tokens directly and returns a pick plus probabilities
per question. Use it for fast routing, boolean checks, policy application, and triage.

## Calling it

Endpoint is `POST /v1/systemone` on Ollama >= 0.35 (not the chat API).

```bash
curl -s http://127.0.0.1:11434/v1/systemone -d '{
  "model": "nimble",
  "state": "the text to judge (string, or JSON object/array)",
  "questions": {
    "team": {"type": "choice", "instructions": "Which team handles this?",
             "criteria": {"billing": "Payments and refunds", "technical": "Bugs", "other": null}},
    "refund": {"type": "noul", "instructions": "Does the customer ask for a refund?"},
    "urgency": {"type": "score", "instructions": "How urgent?",
                "criteria": ["Routine", "Soon", "Urgent"]}
  }
}'
```

Answer fields: `choice` + `probabilities` + `confidence`; `noul` (probability true);
`score` (probability-weighted level) + `legend` + `probabilities`. `keep_alive` pins the
model in RAM between calls.

In a Hermes session the `nimble_decide` tool wraps this — prefer it over hand-rolled curl.

## Hard limits (measured, not advertised)

- **Use `127.0.0.1`, never `localhost`.** On Windows `localhost` resolves to ::1 first and
  a client without happy-eyeballs (Python urllib) retries after ~2s. curl hid this: same
  payload measured 0.4s via curl and 2.3s via urllib. Pinned to 127.0.0.1: **~90ms warm**.
- **Context is 8192 tokens for the scoring prompt**, despite the 256K window the model card
  advertises. Keep `state` tight.
- **Cost scales with `len(state)` x number of questions** — the full state and question set
  is re-sent inside every question's prompt. 4 questions on a short state ~= 1900 input
  tokens; a 64-question call on a tiny state took 11.5s.
- **Choice and score questions allow 2-26 options.** A 27th option is rejected with
  `criteria must contain 2-26 candidates`. To route across a longer list, bucket first in
  code or do two-stage classification.
- **Cold load is ~30s** (9.5GB model). Without `keep_alive` the first call after an idle
  period pays it. Set `keep_alive: "30m"`.
- Request bodies cap at 64 KiB.

## Pitfalls that produce wrong answers

- **Confidence is concentration, not accuracy.** A `confidence` of 0.9 does not mean the
  answer is right 90% of the time. Validate any threshold on your own data.
- **Never trust argmax on a flat distribution.** A benign `tool_describe` scored
  `safe 0.33 / caution 0.30 / destructive 0.37` — argmax said "destructive" and a naive
  gate escalated a read-only call. Flat probabilities mean "no opinion". Require BOTH a
  minimum probability AND a margin over the runner-up before acting on a label.
- **Questions are scored independently.** If two answers must agree, enforce that in code;
  the model will not.
- **Always include an escape option** (`"other"`, `"none of the above"`) when candidates may
  not cover the input — the model has no notion of a candidate it was not given.
- **Its score/rubric mode is weak** (39-49% on public rubric sets). Use `choice` and `noul`,
  not `score`, when accuracy matters.
- **Not a safety guard.** 81% on prompt-safety, 70% on moderation. As a guardrail it is a
  hint, never the sole gate before something irreversible.

## In Hermes: the nimble-steering plugin

`~/.hermes/plugins/nimble-steering/` wires Nimble in as a hidden pre-step (shipped in this
repo at `plugins/nimble-steering/`):

- `pre_llm_call` classifies each incoming message (domain / needs-live / ambiguity) and
  injects a short note that carries **concrete ground truth** — the project's real path and
  host, plus which skill to load (`DOMAIN_HINTS` in the plugin) — so the agent stops
  re-deriving where things live. A bare label is ignorable; a path is not. It also arms
  itself from the phrase "boot up the nimble skill".
- `pre_tool_call` gates risky tool calls on **one** decisive harm boolean and returns
  `{"action": "approve", ...}` to escalate to the human approval gate (never an auto-veto).
  Read-only tools are allowlisted out.

Control it with `/nimble boot | deload | status | test`. Arming only lasts the session: a
restart returns to disarmed, so no permanent latency tax.

### Latency scales with question COUNT, not wording

Measured on this machine (RTX 5060 Ti): shortening criteria text saved only 35ms; removing
questions is the whole win. First question ~145ms, each additional ~220ms.

| Call | Questions | Latency |
|---|---|---|
| Tool gate (trimmed) | 1 | **~145ms** |
| Tool gate (original) | 2 | ~434ms |
| Tool gate | 3 | ~592ms |
| Routing (2q on a short state) | 2 | **~290ms** |
| Routing (4q, 9 domains) | 4 | ~576ms |

When trimming, cut whole questions — do not rewrite their text.

## What Nimble cannot do

Do not use it for: writing, explaining, summarising, multi-step reasoning, code, nested
JSON, or quoting from context. It only picks from the answers you supply. If the task needs
an answer it must *generate*, use a real model.

## Benchmarks (Bespoke Labs public run, 3,880 labelled decisions)

| Type | Nimble 9B | Jev 1.13 |
|---|---|---|
| Choice | 81.6% | 82.9% |
| Boolean | 80.2% | 84.6% |
| Score | 54.6% | 50.1% |

Contrastive behaviour is its strength: changing one deciding fact flips the answer
(verified — swapping a refund signer moved `noul` from 0.9996 to 0.0020).
