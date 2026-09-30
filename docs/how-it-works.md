# How it works

This page is for people who want to know what is actually happening on their machine. If
you just want to install it, the [README](../README.md) is the faster read.

## The 30-second version

Your agent is smart but it doesn't know your stuff. You have to tell it where your project
lives, what port it runs on, which branch deploys. Every new session, every time.

Nimble is a small model that sits in front of your agent. It reads your message, decides
which project it's about, and attaches the facts your agent would otherwise have to guess.

It's cheap enough (about 80 milliseconds) that you never think about it.

## What Nimble is

[Nimble](https://ollama.com/library/nimble) is a 9B model from
[Bespoke Labs](https://github.com/bespokelabsai/nimble), and it is **very** different from a
chat model. That difference is the whole reason this works.

| | A chat model (what you're used to) | Nimble |
|---|---|---|
| What it does | generates the most likely next words | scores answers from a list you give it |
| Can it explain itself? | yes | **no** — it will refuse |
| Can it write prose? | yes | **no** |
| Can it invent an answer? | yes, constantly | **no** — it can only pick from your list |
| Speed | seconds | ~80ms warm |
| Cost | per token | free, local |

Because Nimble can only choose from answers *you* supply, it cannot hallucinate a project
name or make up a port. It can be **wrong** — picking the wrong entry from your list — but it
cannot be creative about it. For routing, that's exactly the property you want.

Two more things worth knowing:

- **It has no reasoning step.** That is what makes it fast, and it is also why it can't tell
  you *why* it chose something.
- **Its confidence score is not accuracy.** A confidence of 0.9 means "my probabilities were
  concentrated", not "I'm right 90% of the time". We don't treat it as a guarantee, and you
  shouldn't either.

## What gets called, and when

Nimble is asked questions in exactly two places.

### 1. Before your message reaches the model — `pre_llm_call`

Your message is sent to Nimble with three questions:

```
domain        → which area of your work is this about?   (choice, ~9 options)
needs_live    → does answering this need live data?      (true/false)
ambiguous     → is this missing something important?     (true/false)
```

One HTTP request, one response, roughly **700ms** for all three on a consumer GPU.

If `domain` comes back confidently (≥ 0.60), the plugin looks up your `DOMAIN_HINTS` entry
for it and attaches the real text. If it's not confident, it says so and attaches nothing —
a guess shouldn't be dressed up as a fact.

The note is added to your message. It is deliberately visible: `hermes` shows it, so you can
see when the classifier is wrong. That transparency is the point — a pre-step you can't
audit is a pre-step you can't trust.

### 2. Before a tool actually runs — `pre_tool_call`

```
harm → could this action cause loss of data or damage a system the user cares about?
```

That's it. **One** question.

Two things we learned building this, both from things going wrong:

**Latency tracks the number of questions, not their length.** Rewriting the question text
saved 35ms. Deleting a question saved ~290ms. So the gate asks one question instead of three.

**Never act on a coin flip.** An early version asked a 3-way "how risky is this?" and
escalated based on whichever option scored highest. Then it flagged `tool_describe` — a
read-only operation — because the model returned `safe 0.33 / caution 0.30 / destructive
0.37`. Nobody actually believed it was destructive; the model just had no opinion and argmax
picked a winner. A single clear yes/no question doesn't have that failure mode.

The gate returns `{"action": "approve"}`, which means **"ask the human"** — never "block
silently". A model that's wrong roughly one time in five does not get to make irreversible
decisions on its own.

Read-only tools skip the gate entirely:

```python
READ_ONLY_TOOLS = {"read_file", "search_files", "skills_list", "web_search", ...}
```

They can't damage anything, so there's no reason to spend 80ms asking.

## Why the note contains real paths

This is the part people skip, and it's the part that matters.

Handing an agent a label is nearly useless:

```
domain=aurora
```

The agent will nod and then ask you where aurora lives. Or worse, invent a plausible answer.
Handing it facts is different:

```
ground truth — aurora lives at ~/code/aurora. Run it with `make serve` -> http://localhost:8080
```

Now there is nothing to guess. **A label is ignorable; a path is not.** That's the whole
design, and it's why `DOMAIN_HINTS` is the first thing you should edit.

## Arming and disarming

The plugin registers always (so `/nimble boot` is available), but the pre-step is **off**
until armed:

```
/nimble boot      → armed for this session
/nimble deload    → disarmed
```

Arming is in-memory only. Restart Hermes and you're back to disarmed. This is intentional:
a latency tax you can't turn off is a latency tax you'll grow to resent.

While disarmed, the only thing registered is the always-cheap "check if this message is
telling me to arm" test — no model calls, no latency.

## Where your data goes

**Nowhere.** The plugin makes HTTP requests to `http://127.0.0.1:11434` — your own machine —
and to nothing else. It has no telemetry, no update check, no analytics.

The only text it ever sends to Nimble is your current message, truncated to 4000 characters.
It does not receive your conversation history, your files, or your tools.

## Failure behaviour

Everything here fails **open**, by design:

| What breaks | What happens |
|---|---|
| Ollama isn't running | note is skipped, your message goes through untouched |
| Model not pulled | same — `/nimble status` tells you the endpoint is unreachable |
| Response takes too long | call abandoned after `budget_s` (default 3s), message goes through |
| Model returns garbage | ignored; no note, no crash |
| The gate is unsure | tool runs normally (it escalates only on a clear signal) |

A pre-step that can break your agent is worse than no pre-step. There is no code path in
this plugin that can stop your message from being answered.
