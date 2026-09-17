"""Render dsh's model overlay from the scalars the engine names (S-0064/D-7).

A real file rather than a heredoc so it can be run, read and tested on its
own. It is also where torve's units become pi-ai's: seconds arrive and
milliseconds are written, `openai` arrives and `openai-completions` is
written. A renderer inside the engine would have to know that for three
harnesses across their versions; this one only has to know the harness it
ships beside.

    python3 model_overlay.py <out.yml>
"""

import os
import sys

# `DSH_MODEL` — a JSON document in a string that torve set and never read — is
# gone: every value below is a scalar the engine validated against a provider
# record, so a window that goes stale is a gate's problem rather than nobody's.
DIALECTS = {"openai": "openai-completions", "anthropic": "anthropic-messages"}

model = os.environ["TORVE_MODEL"]
provider = os.environ.get("TORVE_PROVIDER") or "torve-provider"
api = DIALECTS.get(os.environ.get("TORVE_API") or "openai", "openai-completions")


def seconds(name):
    raw = os.environ.get(name)

    return int(float(raw) * 1000) if raw else None


lines = [
    "- id: llm-pi-ai",
    "  config:",
    "    providers:",
    f"      {provider}:",
    f"        api: {api}",
    "        baseURL: !!js process.env.TORVE_BASE_URL",
    "        apiKeyEnv: !!js process.env.TORVE_API_KEY_ENV",
]

for source, field in (
    ("TORVE_REQUEST_TIMEOUT_S", "timeoutMs"),
    ("TORVE_STREAM_IDLE_TIMEOUT_S", "streamIdleTimeoutMs"),
):
    value = seconds(source)

    if value:
        lines.append(f"        {field}: {value}")

# pi-ai switches to the `developer` role for a reasoning model, and every
# OpenAI-compatible third party this repository reaches answers 400. torve never
# builds a request, so it has no name for this and should not grow one
# (S-0064/D-11): the client that would send it lives here, so the decision does
# too. Real OpenAI would accept the role; using `system` there is a request
# shaped conservatively, not a failure.
if api == "openai-completions":
    lines += ["        compat:", "          supportsDeveloperRole: false"]

effort = os.environ.get("TORVE_REASONING") or ""

if effort:
    lines.append(f"        reasoning: {effort}")

lines += [
    "        models:",
    f"          - id: {model}",
    f"            name: {model}",
]

for source, field in (("TORVE_CONTEXT_WINDOW", "contextWindow"), ("TORVE_MAX_TOKENS", "maxTokens")):
    raw = os.environ.get(source)

    if raw:
        lines.append(f"            {field}: {raw}")

lines.append("            input: [text]")

# `reasoningEfforts: false` strips the capability from a reasoning model —
# measured, every trace of the seat that ran without the map read
# `reasoningTokens: 0` while the endpoint returned reasoning tokens for the same
# prompt. The engine refuses an effort the model does not declare, so a level
# that arrives here is one the model has.
#
# The map is the level the engine named and nothing else. A fixed
# off/low/medium/high list was four names against a record that may hold any:
# `deepseek-v4.1-flash` declares minimal, low, medium, high, xhigh, max and
# ultra, so four of its seven passed the engine's check and then reached a map
# with no entry for them. Two lists, one validated against and a different one
# used, is the shape of every silent drop this image has had — `maxBytes`
# included. `off:` stays beside it because pi-ai's own schema wants the
# disabled rung present, and it is the one name that never comes from a record.
if effort:
    lines += [
        "            reasoningEfforts:",
        "              off:",
        f"              {effort}: {effort}",
    ]
else:
    lines.append("            reasoningEfforts: false")

lines += [
    "- id: agent-default-model",
    "  config:",
    f"    provider: {provider}",
    f"    model: {model}",
]

with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")
