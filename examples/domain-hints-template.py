"""
Template: teach the pre-step about YOUR projects.

Copy this pattern into `plugins/nimble-steering/__init__.py`, or set your own
by editing the two dicts below. Both halves are required:

  1. add the domain to ROUTE_QUESTIONS["domain"]["criteria"] so the classifier
     can actually choose it
  2. add the matching DOMAIN_HINTS entry so there is something useful to inject

Adding a hint without a domain option means it can never fire.

RULES OF THUMB
--------------
* One line per domain, ~40 words. It is injected on every turn it matches, so
  it is a permanent tax on the context window.
* Put the things a model would otherwise GUESS: path, run command, port,
  database, deploy branch.
* Never put secrets, tokens or passwords here. This text is injected into
  prompts and will end up in logs and transcripts.
* Fewer domains beats more. Choice accuracy is ~81%; every extra option steals
  probability mass from the others. Merge similar domains and let the hint
  carry the distinction. Hard cap is 26 options per question.
"""

# ---------------------------------------------------------------------------
# 1. The options the classifier chooses between.
#    Keep the escape hatch ("none") — the model has no concept of an answer it
#    was not offered, so without it a message about nothing gets forced into a
#    wrong bucket.
# ---------------------------------------------------------------------------
DOMAIN_CRITERIA = {
    "myapp": "myapp — my main Django service",
    "data": "data — pipelines, warehouses, notebooks",
    "hermes": "Hermes itself — config, skills, plugins, agent behaviour",
    "infra": "Servers, networking, VMs, Docker, SSH",
    "web": "General web research or browsing",
    "code": "General programming with no project named",
    "chat": "Conversation only, no task",
    "none": "None of the above",
}

# ---------------------------------------------------------------------------
# 2. What to hand the agent when one of those is picked confidently.
#    A label ("domain=myapp") is easy to ignore. A path is not.
# ---------------------------------------------------------------------------
DOMAIN_HINTS = {
    "myapp": (
        "myapp lives at ~/code/myapp (Django). `make dev` -> http://localhost:8000; "
        "tests with `make test`; dev database `myapp_dev`; deploys from the `release` "
        "branch only; owned by the Platform team."
    ),
    "data": (
        "pipeline repo at ~/code/data-pipelines; run jobs with `dagster dev` on :3001; "
        "warehouse is `warehouse_dev`; production data is read-only, never write to it."
    ),
    "infra": (
        "hosts: staging-db 10.20.30.41 (ssh deploy@10.20.30.41), staging-web 10.20.30.42; "
        "application logs under /var/log/myapp."
    ),
    "hermes": (
        "changing Hermes itself: load skill hermes-agent first; use `hermes config set`, "
        "never hand-edit config.yaml."
    ),
    # Deliberately absent: "web", "code", "chat", "none".
    # They are unambiguous and need no ground truth. Skipping them keeps the
    # note short and keeps the classifier's options tight.
}

# ---------------------------------------------------------------------------
# 3. Which tools are never worth gating (they cannot damage anything).
#    Every one you skip is ~80ms you do not spend.
# ---------------------------------------------------------------------------
READ_ONLY_TOOLS = {
    "read_file", "search_files", "skills_list", "skill_view", "session_search",
    "web_search", "web_extract", "todo_list", "tool_search", "tool_describe",
    "nimble_decide", "vision_analyze", "browser_vault_list", "list_dir",
}
