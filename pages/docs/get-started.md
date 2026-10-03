# Get started

This page takes an empty repository through Torve's whole loop:

1. A gate manifest.
2. A design document.
3. A minted task contract.
4. A sandboxed run with no model in it.
5. A landing.
6. A seat with a real agent.

Every file below loads as written. Every command up to the last section
was run against Torve 0.1, and the last section was checked with
`torve doctor`.

You need Linux, git, Docker, and Python 3.13 or 3.14. Torve 0.1 is an alpha.
One operator has run it, against one adopting repository, with Claude Code
as the agent seat.

## Install and initialise

```bash
pip install torve                # or: uv tool install torve
cd your-repository
torve init
```

`torve init` writes what the code derives, and nothing you author:

- a JSON Schema for every file Torve reads, under `.torve/schemas/`;
- `.torve/.gitignore` for what Torve alone writes;
- `.wt/`, where task worktrees live, added to `.git/info/exclude`.

It never writes a configuration or a gate manifest. Until there is a
manifest, `torve gates run` stops with
`configuration error: no gate manifest at .torve/gates.yaml`.

## A gate manifest

`.torve/gates.yaml`:

```yaml
# yaml-language-server: $schema=schemas/gates.json
schema_version: 1

gates:
  - name: scope
    run: "@scope"
    state: blocking
    origin: structural
  - name: secrets
    run: "@secrets"
    state: blocking
    origin: structural
  - name: no-test-tampering
    run: "@no-test-tampering"
    state: blocking
    origin: structural
  - name: decisions-reported
    run: "@decisions-reported"
    state: blocking
    origin: structural
  - name: tests
    run: "python3 -m unittest discover -s tests -t ."
    state: blocking
    origin: structural
```

How the entries work:

- **`run`.** An `@name` runs a builtin; anything else is a shell command.
- **`state`.** `blocking` decides the exit code. `shadow` runs and reports
  without deciding it.
- **`origin`.** Says why the gate exists: `structural`, or a citation of the
  document that decided it.

Commit the manifest, then run the gates against your base branch:

```bash
torve gates run --base main
```

```text
 ✓   scope                pass      blocking
 ✓   secrets              pass      blocking
 ∅   no-test-tampering    skipped   blocking
 ∅   decisions-reported   skipped   blocking
 ✓   tests                pass      blocking
exit 0
```

`no-test-tampering` and `decisions-reported` read a task contract and its
log, so with no task they report `skipped`, never a silent green. This much
is a complete CI install: one step running `torve gates run --base
origin/main`, whose exit code is the outcome.

The run also warns `TwinlessGateWarning`. A gate can name a `sabotage:` twin,
which is the evidence that the gate is able to fail. For a builtin, the twin
is the family of the same name in `torve gates check`. For a shell gate, it
is a test path. Once any entry names a twin, every entry must, so add them
all at once or leave them all out.

## A configuration

`.torve/config.yaml`:

```yaml
# yaml-language-server: $schema=schemas/config.json
schema_version: 1

runtime:
  adapter: docker
  image: python:3.13-slim

store:
  adapter: mock
```

What these settings mean:

- **`runtime.image`.** The sandbox a task runs in, and the gates and
  acceptance commands run inside it too. The image needs your test
  toolchain. `python:3.13-slim` has no pytest, which is why the manifest
  above uses `unittest`.
- **`store: mock`.** Keeps run state in-process. It is enough for
  `torve run`; a served manager needs Postgres, as
  [Operating the engine](operating.md) says.

`torve doctor` checks the configuration and the environment.

## A first document

Work starts from an accepted design document. `torve spec new "A greeting
module" --owner you` creates `.torve/specs/S-0001/` with `document.yaml` and
`decisions.yaml`. Fill them in and add `phasing.yaml`:

```yaml title=".torve/specs/S-0001/document.yaml"
# yaml-language-server: $schema=../../schemas/document.json
id: S-0001
title: A greeting module
kind: design
change:
  type: feat
  scope: greet
status: accepted
implementation: none
depends_on: []
informed_by: []
supersedes: []
superseded_by: null
owner: you
schema_version: 4
summary: |
  A `greet(name)` function returns a greeting.
motivation: The demo needs one small, testable change.
current_state: Nothing greets.
goals: '`greet("Ada")` returns `"Hello, Ada"`.'
non_goals: Localisation.
design:
  - key: the-function
    md: '`greet.py` holds `greet(name: str) -> str`.'
tests: '`tests/test_greet.py` asserts the greeting.'
docs: ''
out_of_scope: ''
risks: None worth naming.
```

```yaml title=".torve/specs/S-0001/decisions.yaml"
# yaml-language-server: $schema=../../schemas/decisions.json
decisions:
  - id: D-1
    grade: ASSUMED
    text: The greeting is `Hello, <name>`, with no trailing punctuation
    paths: [greet.py, tests/test_greet.py]
    consequence: callers format the sentence themselves
```

```yaml title=".torve/specs/S-0001/phasing.yaml"
# yaml-language-server: $schema=../../schemas/phasing.json
phasing:
  - phase: 1
    title: A greeting function
    intent: Add `greet` and its test. Settles D-1.
    scope: [greet.py, tests/test_greet.py]
    acceptance:
      - python3 -m unittest tests.test_greet
    character: routine
```

The fields that drive planning:

- **The decision's `grade`.** Says what an agent may do when reality
  disagrees with the row. `LOCKED` halts, `ASSUMED` departs and logs, and
  `OPEN` decides and logs.
- **The phase's `scope`.** The only paths a task may change; the `scope`
  gate holds it to them.
- **The phase's `acceptance`.** The commands a task must pass.

`torve spec check` validates the corpus. Commit the document: `torve plan`
reads only what is committed.

## Mint and run a task

```bash
torve plan S-0001               # preview: one task, T-0001
torve plan S-0001 --no-dry-run  # writes .torve/tasks/T-0001/contract.yaml
```

The fake agent replays a scripted scenario inside the sandbox, so the whole
loop runs before any model is involved. `greet-scenario.yaml`:

```yaml
attempts:
  - writes:
      greet.py: |
        def greet(name: str) -> str:
            return f"Hello, {name}"
      tests/test_greet.py: |
        import unittest

        from greet import greet


        class TestGreet(unittest.TestCase):
            def test_greet(self):
                self.assertEqual(greet("Ada"), "Hello, Ada")
```

```bash
torve run T-0001 --agent fake --scenario greet-scenario.yaml
```

```text
T-0001: ready after 1 attempt(s)
  claimed -> running: attempt 1 dispatched
  running -> gated: agent exited 0; gates running
  gated -> reviewed: gates green; review not configured
  reviewed -> ready: committed dea1b9d321; pushed=False; pr deferred; ...
```

Without `--scenario`, the fake agent writes one marker file outside the
task's scope. The `scope` gate convicts every attempt, and the task
escalates at the attempt ceiling. That is a quick way to watch a gate refuse
work.

An escalated task keeps its worktree and run state until
`torve reap --escalated` clears them.

`torve merge T-0001` lands the candidate on the base branch. Two commits
appear:

- `torve(T-0001): A greeting function — attempt 1 green`, the task's commit;
- `torve(T-0001): landing of attempt 1`, which records the landing under
  `.torve/specs/S-0001/execution/`.

## A real agent

A seat joins three files to a model. These steps set up a Claude Code seat.

**1. Build the image.** No image is published to a registry yet. From a
checkout of Torve at the release you installed:

```bash
TAG=2.1.283 just image claude   # tags claude-sandbox:2.1.283, the Claude Code version the definition pins
```

**2. Copy the definitions.** Copy `sandboxes/base` and `sandboxes/claude`
into your repository under `.torve/sandbox/`. `torve doctor` refuses an
image that has no reviewed definition beside the configuration that names
it.

**3. Write the harness, provider and seat files.** Two files describe the
harness and the provider. Then add the seat and the broker to
`config.yaml`.

```yaml title=".torve/harnesses/claude.yaml"
# yaml-language-server: $schema=../schemas/harness.json
adapter: subscription
image: claude-sandbox:2.1.283
equip_root: ~/.claude/skills
kinds: [skill, plugin, mcp, hook, agent]
env:
  CLAUDE_PERMISSION_MODE: bypassPermissions
api: [anthropic]
```

```yaml title=".torve/providers/anthropic.yaml"
# yaml-language-server: $schema=../schemas/provider.json
name: anthropic
key_env: CLAUDE_CODE_OAUTH_TOKEN

routes:
  anthropic:
    base_url: https://api.anthropic.com

models:
  claude-opus-5-5:
    context_window: 200000
    max_tokens: 32000
```

```yaml title=".torve/config.yaml (added)"
tiers:
  executor:
    harness: claude
    model: claude-opus-5-5
    provider: anthropic

broker:
  adapter: local

providers:
  default: [anthropic]
```

Each block does one thing:

- **`providers.default`.** Lists every provider a run may reach. A provider
  outside it is refused at dispatch.
- **`key_env`.** Names the variable that holds the credential, never the
  value. With `broker.adapter: local`, the runner keeps the key and the
  sandbox reaches the provider through a loopback route the broker serves,
  so no key enters the sandbox.

**4. Run it.**

```bash
export CLAUDE_CODE_OAUTH_TOKEN=...   # from `claude setup-token`
torve doctor                         # every line should read ✓
torve run <task>
```

`torve doctor` names the harness, the profile, the model and the provider of
every seat. When the broker is on, it says so.

## What next

[Operating the engine](operating.md) covers the rest:

- the resident manager, `torve manager serve`, and served nights;
- what an escalation names, and why one pauses new work;
- landing a document as one pull request.

The manager needs the Postgres store: `pip install 'torve[postgres,migrate]'`,
`TORVE_PG_DSN` naming the database, `torve migrate --all`, and
`store.adapter: postgres`.

This repository's own `.torve/` is a complete working configuration.
