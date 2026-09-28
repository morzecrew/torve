"""Render mimo's provider from the scalars the engine names (S-0064/D-7).

mimo's built-in `openai` provider knows its own catalog and nothing else: with
`OPENAI_BASE_URL` pointed at an OpenAI-compatible endpoint, `--model
openai/qwen3.8-flash` answered "Model not found" and no request left the
sandbox. A provider declared in configuration takes any model it names, so this
writes one — the endpoint, the variable holding its key, and the one model the
seat runs — as the JSON `MIMOCODE_CONFIG_CONTENT` carries. mimo merges it over
whatever the equipment installed.

It is also where torve's units become mimo's: seconds arrive and milliseconds
are written.

    python3 provider_config.py    # prints the document
"""

import json
import os
import sys

# The AI SDK package mimo loads for each dialect the harness record admits. The
# record admits `openai` alone, so any other value is an engine that stopped
# reading the record, and it is refused rather than guessed at.
DIALECTS = {"openai": "@ai-sdk/openai-compatible"}

api = os.environ.get("TORVE_API") or "openai"

if api not in DIALECTS:
    sys.exit(f"provider_config: mimo reaches no {api!r} dialect")

provider = os.environ.get("TORVE_PROVIDER") or "torve-provider"
model = os.environ["TORVE_MODEL"]

# The key by name, dereferenced by mimo when it dials: the value never lands in
# a document an attempt could print (S-0001/D-13).
options = {
    "baseURL": os.environ["TORVE_BASE_URL"],
    "apiKey": "{env:" + os.environ["TORVE_API_KEY_ENV"] + "}",
}

timeout = os.environ.get("TORVE_REQUEST_TIMEOUT_S")

if timeout:
    options["timeout"] = int(float(timeout) * 1000)

entry = {"name": model}
window = os.environ.get("TORVE_CONTEXT_WINDOW")
ceiling = os.environ.get("TORVE_MAX_TOKENS")

# Both or neither: mimo's schema refuses a `limit` missing either number
# ("expected number, received undefined … limit.output"), and a record that
# measured one has not measured the other.
if window and ceiling:
    entry["limit"] = {"context": int(window), "output": int(ceiling)}

effort = os.environ.get("TORVE_REASONING")

if effort:
    entry["options"] = {"reasoningEffort": effort}

document = {
    "provider": {
        provider: {
            "npm": DIALECTS[api],
            "name": provider,
            "options": options,
            "models": {model: entry},
        }
    }
}

sys.stdout.write(json.dumps(document) + "\n")
