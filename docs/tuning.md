# Tuning

Everything here is optional. The defaults work; this is for when you want them sharper.

## Settings

All settings live under your plugin entry in `config.yaml`. Nothing needs hand-editing — use
`hermes config set`, or the plugin's own `set_config` API:

```yaml
plugins:
  entries:
    nimble-steering:
      settings:
        base_url: http://127.0.0.1:11434   # where Ollama lives
        model: nimble                       # or a smaller quant
        keep_alive: 30m                     # how long the model stays loaded
        timeout_s: 30.0                     # per HTTP call
        budget_s: 3.0                       # abandon the pre-step after this
        harm_threshold: 0.7                 # 0 disables the gate
        route_enabled: true
        gate_enabled: true
        boot_on_mention: true               # arm by saying "boot up the nimble skill"
```

| Setting | When to change it |
|---|---|
| `harm_threshold` | Lower (0.5) if you want more warnings; raise (0.9) if you're getting noise. `0` turns the gate off and saves a call per tool use. |
| `keep_alive` | Raise if you chat in bursts with long gaps and don't want to pay the ~30s reload. `0` releases the model immediately. |
| `budget_s` | Lower (1.0) if you'd rather skip the note than wait on a cold model. |
| `boot_on_mention` | Set `false` if you'd rather only ever arm it via `/nimble boot`. |
| `route_enabled` / `gate_enabled` | Turn off the half you don't want. |

## Writing good `DOMAIN_HINTS`

This is the highest-leverage edit in the whole plugin. A hint is injected on every turn whose
domain matches, so it gets a lot of exposure — for better and worse.

**Do:**

- Include the things an agent would otherwise *guess*: filesystem path, run command, port,
  database name, deploy branch.
- Prefer specifics over descriptions. `port 8080` beats `runs on a web port`.
- Say which skill to load, if you have one: `load skill myapp first`.
- Add constraints that are easy to violate: `never push to prod unasked`.

**Don't:**

- Write paragraphs. One line, ~40 words. It's a permanent context tax.
- Restate what the model already knows (`FastAPI is a Python framework`).
- Put secrets in it — **this is injected into prompts and will end up in logs.**
- Add a hint for every domain. `web` and `chat` don't need one; they're already unambiguous.

### Worked example

```python
DOMAIN_HINTS = {
    "myapp": ("myapp lives at ~/code/myapp (Django). `make dev` -> http://localhost:8000; "
              "tests `make test`; dev db `myapp_dev`; deploys from `release` only; "
              "owned by Platform."),
}
```

And the matching option so the classifier can pick it:

```python
ROUTE_QUESTIONS["domain"]["criteria"]["myapp"] = "myapp — my main Django service"
```

Both halves are required. Adding a hint without a domain option means it can never fire —
which is the exact bug this repo shipped in its first benchmark run, and why the benchmark
now imports the plugin's real question set instead of keeping its own copy.

## Tuning in the other direction: fewer, better domains

Choice accuracy sits around 81-83%. Each domain you add steals probability mass from the
others, so more options genuinely means more mistakes, not just more options. If two of your
domains feel similar ("api" and "backend"), merge them and let the hint carry the
distinction.

The hard cap is **26 options per question** — the Ollama endpoint rejects a 27th with
`criteria must contain 2-26 candidates`. If you need more, bucket first in code.

## Troubleshooting

Start with `/nimble test` — it runs a live classification and reports the endpoint state,
latency and a benign/dangerous pair.

| Symptom | Cause | Fix |
|---|---|---|
| `UNREACHABLE` in `/nimble status` | Ollama isn't running | `ollama serve` |
| Every call takes ~30s | model isn't resident | set `keep_alive: 30m`; the first call always pays the load |
| Every call takes ~2s, not 80ms | you're hitting `localhost`, not `127.0.0.1` | on Windows `localhost` resolves to `::1` first and Python retries after a ~2s timeout. Use `127.0.0.1` |
| `criteria must contain 2-26 candidates` | too many domain options | merge options or bucket them |
| Notes are wrong a lot | domain list is too broad, or hints are stale | trim to domains you actually use |
| Gate never fires | it's armed? threshold sane? tool in `READ_ONLY_TOOLS`? | `/nimble status` |
| Gate fires constantly | threshold too low, or a tool that legitimately looks dangerous | raise `harm_threshold`, or add the tool to `READ_ONLY_TOOLS` |

### Reading the confidence number

`confidence` reports how concentrated the probabilities were. It is **not** the chance of
being right.

You'll see this in practice: a genuinely ambiguous message ("it's broken again, fix it")
scored `hermes` at high confidence. The model had no real basis for that — it just had to
pick something, and picked firmly. This is why the note tells the agent to ignore a label
that doesn't fit, and why the benchmark keeps an intentionally ambiguous case around to
watch for it.

### Checking your setup without the agent

```bash
# is the model there?
curl -s http://127.0.0.1:11434/api/version

# what does it decide about a message?
curl -s http://127.0.0.1:11434/v1/systemone -d '{
  "model": "nimble",
  "state": "how do I start myapp locally?",
  "questions": {
    "domain": {"type": "choice", "instructions": "What area?",
               "criteria": {"myapp": null, "other": null}}
  }
}'
```

## Uninstalling

```bash
hermes plugins disable nimble-steering
rm -rf ~/.hermes/plugins/nimble-steering
rm -rf ~/.hermes/skills/nimble-decision-model
```

The model itself stays in Ollama; remove it with `ollama rm nimble`.
