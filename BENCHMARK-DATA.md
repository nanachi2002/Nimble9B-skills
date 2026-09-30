# Benchmark data — handoff for design

Everything below is measured output, not marketing copy. Every number traces to a JSON file
in `benchmarks/results/`. If a claim here isn't backed by a row in this document, don't put
it on the page.

---

## 1. What the benchmark actually tests

**The question:** does a small local classifier (Nimble, 9B) pre-reading a message make a
large hosted model answer *better*?

**The method:** 12 identical questions are asked twice by the same model.

| Arm | What the model receives |
|---|---|
| **WITH** | a short `[nimble pre-step]` note prepended, naming the project's real facts |
| **WITHOUT** | the question alone |

Answers are scored by exact substring match against a fixed expected fact. No LLM judge, no
human opinion — an answer either contains `make serve` or it doesn't.

- **Model under test:** `deepseek-v4.1-flash` (Ollama Cloud)
- **Classifier:** `nimble` 9B locally, Ollama `POST /v1/systemone`
- **Cases:** 12 · **API calls:** 24 · **Run:** 2026-09-30, single session
- **Source:** `benchmarks/results/live_results.json`

---

## 2. Headline numbers

These five are the page. Everything else is supporting detail.

| # | Metric | WITH pre-step | WITHOUT | Notes |
|---|---|---|---|---|
| 1 | **Answer accuracy** | **100%** (12/12) | **50%** (6/12) | primary result |
| 2 | **Median response time** | **947ms** | **7,615ms** | 8.0× — WITH is faster |
| 3 | **Questions answered faster** | **9 / 12** | 3 / 12 | |
| 4 | **Times it stalled to ask for clarification** | **2** | **8** | lower is better |
| 5 | **Median completion tokens** | **187** | **2,198** | 11.7× — WITH is far shorter |

> **Label the winner on every card.** An earlier draft of the chart printed "8.0×" with the
> model name as the subtitle and readers concluded the *without* arm was faster. Put
> "with pre-step" in the label, not just in the legend.

---

## 3. Per-question results (the main table)

| Case | Question (abbrev.) | Routed as | WITH | WITHOUT | WITH correct | WITHOUT correct |
|---|---|---|---:|---:|:---:|:---:|
| `aurora-run` | How do I start the aurora backend? | aurora | 1,783ms | 21,908ms | ✅ | ❌ |
| `aurora-port` | What port does aurora listen on? | aurora | 516ms | 3,702ms | ✅ | ❌ |
| `aurora-db` | Which database for dev? | aurora | 582ms | 8,432ms | ✅ | ✅ |
| `aurora-deploy` | What's the deploy path? | aurora | 1,032ms | 8,034ms | ✅ | ❌ |
| `aurora-serve-tests` | How do I run aurora's tests? | aurora | 794ms | 14,279ms | ✅ | ❌ |
| `aurora-owner` | Who owns the aurora service? | aurora | 731ms | 10,125ms | ✅ | ✅ |
| `generic-code` | Explain hash map collisions | code | 34,486ms | 7,183ms | ✅ | ✅ |
| `generic-chat` | "hey, good morning!" | chat | 862ms | 940ms | ✅ | ✅ |
| `infra-host` | Which machine runs staging DB? | infra | 763ms | 4,123ms | ✅ | ❌ |
| `web-live` | Latest stable Python release? | code | 10,579ms | 7,197ms | ✅ | ✅ |
| `ambiguous-short` | "it's broken again, fix it" | hermes | 3,405ms | 2,043ms | ✅ | ✅ |
| `trap-wrong-domain` | aurora frontend build failing | aurora | 10,549ms | 10,629ms | ✅ | ❌ |

**Reading it:** six cases are *project-specific* (`aurora-*`, `infra-host`) — WITHOUT fails
5 of those 6. Two are *controls* (`generic-code`, `generic-chat`) where no project facts
apply — both arms pass, as they should.

### Case types (useful for a grouped or colour-coded chart)

| Type | Cases | Expected outcome |
|---|---|---|
| Project-specific | `aurora-run`, `aurora-port`, `aurora-deploy`, `aurora-serve-tests`, `aurora-owner`, `infra-host` | WITH wins big |
| Control (no ground truth applies) | `generic-code`, `generic-chat` | tie — proves it isn't just "more tokens" |
| Live-data | `web-live` | tie |
| Deliberately ambiguous | `ambiguous-short` | correct behaviour is to ask |
| Adversarial trap | `trap-wrong-domain` | WITH must not be derailed by the note |

---

## 4. Token economics

Why the WITH arm is also *cheaper*, not just faster.

| | WITH | WITHOUT |
|---|---:|---:|
| Median completion tokens | **187** | **2,198** |
| Mean completion tokens | 714 | 2,340 |
| Total across 12 cases | 8,574 | 28,088 |

Without the facts, the model **thinks for thousands of tokens** trying to work out what
"aurora" is. Worst offenders: `aurora-serve-tests` (89 → 4,319 tokens, 48.5×),
`aurora-db` (61 → 2,445, 40.1×).

The three cases where WITHOUT used *fewer* tokens are `web-live` (0.8×),
`ambiguous-short` (0.6×) and `trap-wrong-domain` (1.0×) — consistent with those being the
cases the note doesn't help with.

---

## 5. Classifier routing accuracy (a separate, secondary measurement)

Measured independently by `benchmarks/run_benchmark.py` (offline mode, no API calls).

- **Routing accuracy: 10/12 = 83.3%**
- **Published reference: 86.9%** (Nimble on MASSIVE-en-US intent routing)
- **Classifier overhead: 551ms median** (range 496–626ms), n = 12

| Case | Expected | Predicted | Correct |
|---|---|---|:---:|
| `aurora-run` | aurora | aurora | ✅ |
| `aurora-port` | aurora | aurora | ✅ |
| `aurora-db` | aurora | aurora | ✅ |
| `aurora-deploy` | aurora | aurora | ✅ |
| `aurora-serve-tests` | aurora | aurora | ✅ |
| `aurora-owner` | aurora | aurora | ✅ |
| `generic-code` | code | code | ✅ |
| `generic-chat` | chat | chat | ✅ |
| `infra-host` | infra | infra | ✅ |
| `web-live` | **web** | **code** | ❌ |
| `ambiguous-short` | **none** | **hermes** | ❌ |
| `trap-wrong-domain` | aurora | aurora | ✅ |

**Show the two failures.** They're the credibility of the whole page:
- `web-live` → live-data questions get read as programming. Harmless: the note still flags
  "live data needed".
- `ambiguous-short` → a genuinely ambiguous message got a confident-sounding wrong label.
  This is *why* the note tells the agent to ignore it if it looks wrong.

---

## 6. The qualitative exhibit

The single most persuasive piece of the whole dataset. Verbatim, from `without_answer` on
`aurora-port`:

> "Apache Aurora's scheduler listens locally on **port 8081** by default (for the HTTP/Web UI
> and Thrift API). If you meant **Amazon Aurora** (the managed database), the default port
> depends on the engine: **Aurora MySQL**: `3306` — **Aurora PostgreSQL**: `5432`"

The actual answer, from the note, is **8080**. The model never said "I don't know" — it named
two unrelated products and three wrong ports, fluently.

Two more, shorter:

- **`aurora-serve-tests`** (WITHOUT): *"It depends which 'Aurora' you mean. If you mean
  Apache Aurora, it uses the Gradle wrapper: `./gradlew test`"* — invented build system.
- **`infra-host`** (WITHOUT): *"I can't tell you the exact machine from this context — I
  don't have access to your infrastructure"* … and then speculated anyway.

**The line that ties the page together:** the pre-step doesn't make the model smarter — it
removes the opportunity for it to invent facts it was never given.

---

## 7. Honest caveats (please keep these on the page)

Putting these up *raises* trust rather than lowering it.

1. **Latency is noisy.** Median over 12 cases hides a huge spread: WITH ranges 516ms–34.5s,
   WITHOUT 940ms–21.9s. Accuracy and stall counts are the stable measurements; treat timing
   as directional.
2. **n = 12.** This is a demonstration, not a statistically rigorous study. Don't print
   confidence intervals; a designer shouldn't imply them either.
3. **One model, one run.** `deepseek-v4.1-flash`, one session. Not a claim about all models.
4. **The demo project is fictional.** `aurora` doesn't exist; it's a stand-in so nobody's
   real infrastructure ends up in a public repo.
5. **Scoring is strict substring matching.** A correct answer phrased unusually could score
   as a miss. It cuts both ways, and it's why no judgement calls are involved.
6. **The classifier is ~83%, not 100%.** Some notes will be wrong. The note says so itself,
   and the page should too.

---

## 8. Suggested page structure

Ordered by what convinces a sceptical reader fastest.

1. **Headline:** the five KPI numbers, each labelled with which arm won.
2. **The exhibit:** the "port 8081 / 3306 / 5432" quote. This is the hook — it makes the
   abstract numbers concrete in one glance.
3. **Per-question table or grouped bars**, with control cases visually separated so it's
   obvious the win is specific to project facts.
4. **Accuracy gauges** or two big numbers for 100% vs 50%.
5. **Token bar** (187 vs 2,198) — the "it's cheaper too" beat.
6. **Routing accuracy panel** including the two failures.
7. **Caveats**, in small type but present.

### Visual guidance from what already failed

- A 12-line slope chart is unreadable spaghetti — every line crosses. Use **one row per
  case** (dumbbell) or a **grouped bar chart**.
- Response times span 0.5s → 22s. On a **linear** axis everything collapses into the left
  margin. Use a **log scale**, and say so on the axis.
- Keep long labels *outside* the plot area; text overlapping data dots was the other
  complaint about the first attempt.
- Leave generous padding; the first draft felt cramped at the bottom.

---

## 9. Data files and how to regenerate

| File | Contents |
|---|---|
| `benchmarks/results/live_results.json` | Every per-case number in this doc (the source of truth) |
| `benchmarks/results/offline_results.json` | Routing accuracy + overhead only |
| `benchmarks/cases/routing_cases.json` | The 12 questions, expected facts, and why each exists |

Each live row carries: `id`, `question`, `expected_domain`, `routed_domain`, `domain_correct`,
`route_ms`, `note`, and for both arms `answer` / `ms` / `ok` / `why` / `clarified` / `empty` /
`completion_tokens`.

```bash
python benchmarks/run_benchmark.py            # routing + overhead, no API key
python benchmarks/run_benchmark.py --live \
    --model deepseek-v4.1-flash \
    --base-url https://ollama.com/v1 \
    --api-key-env OLLAMA_API_KEY
```

Live runs are reproducible but **not deterministic** — a re-run gives different timings and
may move a borderline case. If numbers are re-generated, update every figure in this
document from the new JSON rather than mixing runs.
