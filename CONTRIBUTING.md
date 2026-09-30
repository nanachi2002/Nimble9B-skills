# Contributing

Thanks for helping. This repo is small enough that a good issue is as valuable as a patch.

## The most useful thing you can send

**A benchmark case that breaks the classifier.**

We're at 10/12 routing accuracy. The two failures are more interesting than the ten
successes, and they're documented in the README rather than hidden. If you have a message
your agent routed badly, that's a real contribution:

```json
{
  "id": "short-descriptive-name",
  "question": "the exact message you sent",
  "domain": "what it SHOULD have been",
  "expect_all": ["a fact that must appear in a good answer"],
  "why": "one line on what this case is testing"
}
```

Add it to `benchmarks/cases/routing_cases.json` and open a PR. Please include what it
*actually* routed to, so we can see the size of the problem.

**Good cases to send:**

- Messages where the classifier picked a confidently wrong domain
- Messages that straddle two of your domains (ambiguous by nature)
- Short messages ("it's broken again") where a wrong label is especially costly

**Please don't send:** cases that only pass because the answer is guessable without the
note. A case where knowing the domain doesn't change the answer measures nothing.

## Ground truth rules

The `DOMAIN_HINTS` examples in this repo use a fictional project (`aurora`) on purpose.

**Never commit real infrastructure details to a fork you intend to publish** — hostnames, IPs,
internal paths, credentials. Hints are injected into prompts and will show up in logs and
transcripts. Keep your real ones in your local install.

## Changing the classifier prompt

`ROUTE_QUESTIONS` is the prompt. If you change it:

1. Run `python benchmarks/run_benchmark.py` (offline, no API key needed).
2. Report before/after routing accuracy in your PR description.
3. If accuracy drops but the prompt is "cleaner", say so. We'd rather keep the ugly
   prompt that works.

The benchmark imports the plugin's question set directly, so it can never drift from what
ships. If you add a domain option, add it in the plugin — the benchmark will pick it up.

## Adding a domain

Two edits, both required:

```python
# 1. so the classifier can choose it
ROUTE_QUESTIONS["domain"]["criteria"]["myapp"] = "myapp — my main Django service"

# 2. so there is something useful to inject
DOMAIN_HINTS["myapp"] = "myapp lives at ~/code/myapp; `make dev` -> :8000; ..."
```

A hint with no domain option can never fire. That exact bug shipped in our first benchmark
run, and it's why the harness now imports the real question set.

**Keep the list short.** Choice accuracy is ~81%, and every extra option steals probability
mass from the others. 26 options is a hard cap (the API rejects a 27th). If two domains feel
similar, merge them and let the hint carry the distinction.

## Running the checks

```bash
python benchmarks/run_benchmark.py        # offline: routing + overhead, no API key
python benchmarks/run_benchmark.py --live --model <your-model> \
    --base-url https://your-endpoint/v1 --api-key-env YOUR_KEY_VAR
python benchmarks/make_chart.py           # regenerate the SVG from results
```

The offline run exits non-zero if routing accuracy falls below 60%, so it works as a smoke
test in CI or a pre-commit hook.

Before you open a PR:

- `python benchmarks/run_benchmark.py` passes
- `hermes plugins validate plugins/nimble-steering` passes
- You haven't added anything secret to `DOMAIN_HINTS`

## Style

- Python 3.9+, standard library only — this plugin must install with zero dependencies.
- Comments explain *why*, not *what*. The interesting comments in this repo are the ones
  recording a measurement or a mistake.
- Fail open. Nothing in this plugin may stop a user's message from being answered.

## Reporting bugs

Include the output of:

```
/nimble test
```

It reports the endpoint, whether the model is loaded, latency, and a benign/dangerous
classification pair — which is usually enough to tell a configuration problem from a model
problem.
