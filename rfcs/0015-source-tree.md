---
id: "0015"
title: Source tree structure
kind: convention
status: accepted
implementation: complete
depends_on: ["0016"]
informed_by: ["0002", "0003", "0014"]
supersedes: []
superseded_by: null
amended_by: ["A-19"]
owner: Lev Litvinov
description: >-
  The package layout of src/torve — layers, permitted import directions,
  module naming, adapter organisation, and the layering gate that enforces
  the enforceable half.
schema_version: 1
---

# RFC 0015 — Source tree structure

- **Scope:** The package layout of `src/torve` — layers, their permitted import directions, module naming, adapter organisation, and the gate that enforces the parts that can be enforced. Includes the migration from the current flat tree. Excludes within-file layout (RFC 0014) and anything about the CLI's behaviour (RFC 0011).
- **Related:** RFC 0002 (the gates library must stay importable without the runner) · RFC 0014 (within-file layout) · `forze` (the layer names this mirrors)

---

## 1. What is wrong now

Eighteen modules sit flat at the package root beside two subpackages. Three specific problems follow, and the third is the one with teeth.

**Nothing expresses the layers.** `adapters/` exists, but there is no `domain/` or `application/` — `domain.py` and `models.py` sit at the root next to `cli.py`. A hexagonal design whose directory tree does not show the hexagon teaches every reader, human or agent, that the layering is decorative.

**Names collide in meaning.** `domain.py` beside `models.py`; `run.py`, `runner.py`, `runstate.py` and `runconfig.py`; `layout.py` beside `manifest.py`. A reader cannot predict which file holds what, so they open three. An agent, which cannot open three cheaply, guesses — and writes to the wrong one.

**Nothing prevents a layer violation.** RFC 0002 ships gates as a standalone library that runs in CI with no store, no sandbox and no runner. A flat root means `from torve.taskstore import ...` inside a gate is one autocomplete away, and nothing would fail. The standalone property of the first increment is currently protected by nobody noticing.

---

## 2. Layers

Five, mirroring the substrate's names so that two codebases read by the same people do not differ in vocabulary.

```
src/torve/
  base/            lowest; dependency-free helpers
  domain/          models, invariants, state machine — no I/O
  application/     ports, the run loop, services
  adapters/        implementations of ports
  gates/           the standalone checking library
  config/          on-disk manifest models and path resolution
  cli/             presentation
  migrations/      package data (RFC 0012)
  py.typed
```

### 2.1 Permitted imports

```
base        → everyone
domain      → application, adapters, gates, config, cli
application → adapters, cli
adapters    → cli only (wiring)
gates       → cli only
config      → application, adapters, cli
```

Read as: `domain` imports only `base`; `application` imports `domain` and `base`; `adapters` import `application` (for the port they implement), `domain` and `base`; `cli` imports anything.

**The load-bearing edge: `gates` may import `domain`, `base` and `config` — and nothing else.** No `application`, no `adapters`. That is what keeps `pip install torve` plus `torve gates run` working in a repository with no database and no sandbox, which is the entire promise of RFC 0002. Today that promise rests on nobody making a mistake; §6 makes it rest on a check.

`adapters` never import each other. A Docker runtime that reaches into the git workspace adapter is two adapters welded together, and swapping either becomes a rewrite.

---

## 3. Target tree

```
src/torve/
  __init__.py                curated lazy front door (§5)
  py.typed

  base/
    shell.py                 subprocess execution, timeouts, process groups
    naming.py                names derived from a task id (D-3.4)

  domain/
    task.py                  Task, Scope, Budget, InheritedDecision
    attempt.py               Attempt, GateResult, Finding, Cost
    feedback.py              ReviewFeedback
    states.py                TaskState, Outcome, EscalationReason

  application/
    ports.py                 every Protocol
    runner.py                the run loop
    reaper.py
    sizing.py                SizePolicy (RFC 0002 §6b)
    skills.py                role-scoped skill resolution
    telemetry.py
    planner.py               plan and context (RFC 0007, later)

  adapters/
    agent/
      fake.py
      api.py
      harness.py
      subscription.py
    runtime/
      docker.py
      opensandbox.py
    workspace/
      git.py
    vcs/
      git.py
    store/
      durable.py
    telemetry/
      jsonl.py

  gates/
    contract.py              the Gate protocol and result types
    scope.py
    acceptance.py
    no_test_tampering.py
    decisions_reported.py
    secrets.py
    self_audit.py
    source_layout.py
    layering.py              §6
    sabotage.py              the suite runner

  config/
    manifest.py              gates.yaml model
    runconfig.py             config.yaml model
    layout.py                .torve/ path resolution

  cli/
    main.py                  Typer app assembly
    run.py
    gates.py
    migrate.py
    status.py
    doctor.py

  migrations/
    torve/postgres/
    substrate/postgres/
```

### 3.1 Adapters get a directory per port, not a name prefix

`runtime_docker.py` and `runtime_opensandbox.py` are a flat namespace imitating structure. It does not scale: there are already two runtimes and there will be four agents.

`adapters/<port>/<technology>.py` makes the port the directory and the technology the file, so "which ports have adapters" is answered by `ls` and adding a third runtime adds a file rather than another prefix.

### 3.2 Resolving the ambiguous names

| Now | Goes to | Because |
| --- | --- | --- |
| `domain.py` + `models.py` | `domain/` split by aggregate | two names for one concept; split by aggregate matches D-22 |
| `run.py` | `cli/run.py` | it is a command |
| `runner.py` | `application/runner.py` | it is the loop |
| `runstate.py` | `domain/states.py` | the state machine is domain |
| `runconfig.py` | `config/runconfig.py` | it is an on-disk file model |
| `layout.py` | `config/layout.py` | `.torve/` path resolution |
| `manifest.py` | `config/manifest.py` | `gates.yaml` model |
| `context.py` | `cli/` if it is the command, `application/planner.py` if it is the logic | see the rule below |
| `shell.py`, `naming.py` | `base/` | dependency-free helpers |
| `gates/base.py` | `gates/contract.py` | see D-15.5 |

**Rule for the `context.py` case, and every future one like it:** a module named after a CLI verb belongs in `cli/` and contains argument parsing and output rendering only. The logic it calls lives in `application/` under a name describing what it does. If a single file currently holds both, splitting it is the point of the move, not a side effect.

---

## 4. Module naming

- **No `models.py`, `utils.py`, `helpers.py`, `common.py`, `base.py`.** These are names that attract unrelated code, because anything can be argued into them. A module is named for what it holds; if that name cannot be written, the module has no single subject and should not exist yet.
- **One gate, one file** (D-14.8), named for the gate, matching the name in `gates.yaml`.
- **Adapter files are named for the technology**, never for the port — the port is already the directory.
- **Singular for a subject, plural only for a genuine collection.** `domain/task.py` holds the `Task` family; `gates/` is a collection.

`base.py` is called out specifically because `gates/base.py` exists today. It holds the gate protocol and result types, so it is `contract.py` — a name that says what is inside and cannot absorb the next unrelated helper.

---

## 5. The front door

`torve/__init__.py` becomes a curated lazy front door in the substrate's style: a `name -> canonical module` mapping resolved through PEP 562, with `__all__` derived from it.

Two reasons beyond consistency. It keeps `import torve` cheap, which matters because `torve gates run` in CI should not import the runner. And the export table is a readable statement of the package's public surface — the deep paths stay reachable, but the curated set is what a newcomer sees.

---

## 6. Enforcement — the `layering` gate

The layer rules in §2.1 are the reason this document exists, and a rule that lives only in prose is an open finding under `ratchet-what-you-build`.

### 6.1 Contracts, not a hand-written checker

The check is `import-linter`, declared in `pyproject.toml`, rather than forty lines over `ast`.

The decisive reason is **transitive edges**. A hand-written check reads imports in changed files and sees direct violations only. If `gates/scope.py` imports `config/manifest.py` and that imports `application/ports.py`, no direct violation exists and gate isolation is broken anyway. `import-linter` builds the full dependency graph and catches it.

```toml
[[tool.importlinter.contracts]]
name = "Layers"
type = "layers"
layers = [
    "torve.cli",
    "torve.adapters",
    "torve.application",
    "torve.domain",
    "torve.base",
]

[[tool.importlinter.contracts]]
name = "Gates stand alone"
type = "forbidden"
source_modules = ["torve.gates"]
forbidden_modules = ["torve.application", "torve.adapters"]

[[tool.importlinter.contracts]]
name = "Adapters are independent"
type = "independence"
modules = [
    "torve.adapters.agent",
    "torve.adapters.runtime",
    "torve.adapters.workspace",
    "torve.adapters.vcs",
    "torve.adapters.store",
]
```

`gates` and `config` sit beside the main stack rather than inside it — they do not fit a total order, so they get their own contracts. One `layers` contract is not enough and that is expected, not a workaround.

Note that lazy imports inside function bodies are ordinary edges to `import-linter`. That is the behaviour we want — a layer cannot be crossed by deferring the import — but it is worth knowing before someone tries it and is surprised.

### 6.2 The gate wraps the tool

```yaml
- name: layering
  run: "lint-imports --config pyproject.toml"
  input: worktree
  state: shadow
  origin: rfc/0015
  timeout: 30s
```

The gate stays an entry in the manifest — provenance, state, telemetry, participation in `config_hash` — and calls the tool rather than reimplementing it. The same shape as `acceptance`: a gate as a wrapper around a command.

**`input: worktree`, not `diff`.** The graph is built over the whole package, which is the entire reason for choosing the tool.

Sabotage cases, one per contract: a domain module importing an adapter, a gate importing the task store, and a runtime adapter importing the workspace adapter. Enters at `shadow` per D-2.18 like any other gate.

### 6.3 Module naming goes elsewhere

D-15.5 — no `models.py`, `utils.py`, `helpers.py`, `common.py`, `base.py` — is not about imports and `import-linter` cannot see it. It moves to the `source-layout` gate (RFC 0014 §9), where file-level layout rules already live: one regex over the changed paths, one more sabotage case.

### 6.4 Two sets of gates, and they are not the same set

This document surfaces a distinction RFC 0002 does not currently draw.

| Set | Examples | Ships | Dependency |
| --- | --- | --- | --- |
| **Product gates** | `scope`, `acceptance`, `decisions_reported`, `secrets` | in the package, to consuming repositories | none beyond `torve` |
| **Self-development gates** | `layering`, `sabotage` | in this repository only | dev-only (`import-linter`) |

`layering` checks Torve's own source layers. It has no meaning in a consuming repository, and `import-linter` must not appear in the base install — it belongs in dev dependencies, unlike every gate that ships.

Without this line, a check on Torve's internals eventually arrives in a user's `torve gates run`, and they are entitled to wonder why.

## 7. Migration

Mechanical, one commit, no behaviour change.

1. Create the directories and move files per §3.2. Use `git mv` so history follows.
2. Fix imports — mostly mechanical; `ruff` will find what breaks.
3. Split `context.py` if it holds both a command and its logic.
4. Rename `gates/base.py` to `contract.py`.
5. Rewrite `__init__.py` as the lazy front door.
6. Add the `import-linter` contracts and the `layering` gate at `shadow`, then read what it reports. **Expect violations** — that report is the point of the step, not a failure of it.
7. Fix them, then promote to `blocking`.
8. Sweep for RFC 0014 compliance in the moved files while they are already open.

**Do this before any task contract references a path under `src/torve/`.** Moving files invalidates `scope.allow` globs in minted contracts, and a scope gate that reddens because the tree moved is the fastest way to teach people that the scope gate is noise.

Step 6 before step 7 deliberately: run the check first and learn how far the current code is from the rule, rather than assuming and fixing blind.

---

## 8. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-15.1 | `LOCKED` | Five layers — `base`, `domain`, `application`, `adapters`, plus `gates`, `config`, `cli` — with the import directions in §2.1 | `src/torve/**` | A hexagonal design whose tree hides the hexagon teaches that the layering is decorative |
| D-15.2 | `LOCKED` | `gates` may import only `domain`, `base` and `config` | `src/torve/gates/**` `pyproject.toml` | This is what keeps RFC 0002's standalone increment standing alone |
| D-15.3 | `LOCKED` | Adapters never import each other | `src/torve/adapters/**` `pyproject.toml` | Two welded adapters make swapping either a rewrite |
| D-15.4 | `LOCKED` | Adapters are organised `adapters/<port>/<technology>.py` | `src/torve/adapters/**` | A name prefix is a flat namespace imitating structure and does not scale |
| D-15.5 | `LOCKED` | No module named `models`, `utils`, `helpers`, `common` or `base`; enforced by the `source-layout` gate, not by `layering` | `src/torve/gates/source_layout.py` | Names that admit anything accumulate everything; this is a path rule, not an import rule |
| D-15.6 | `LOCKED` | A module named after a CLI verb lives in `cli/` and holds parsing and rendering only | `src/torve/cli/**` | Otherwise presentation and logic fuse at the point where they are hardest to separate |
| D-15.7 | `ASSUMED` | `__init__.py` is a curated lazy front door | `src/torve/__init__.py` | Keeps `import torve` cheap for the gates-only path |
| D-15.8 | `LOCKED` | §2.1 is enforced by `import-linter` contracts wrapped in the `layering` gate, over the whole package | `pyproject.toml` `.torve/gates.yaml` | A diff-scoped hand-written check misses transitive violations, which is how gate isolation breaks |
| D-15.9 | `ASSUMED` | The move happens before any task contract references a path under `src/torve/` | `.torve/tasks/**` | Moving files invalidates minted `scope.allow` globs |
| D-15.10 | `LOCKED` | Product gates and self-development gates are distinct sets; `import-linter` stays a dev dependency | `pyproject.toml` `.torve/gates.yaml` | A check on Torve's internals must never reach a consuming repository's gate run |
| D-15.11 | `ASSUMED` | The layer definition outranks the rename table: the gate-input builder and gate runner live in `gates/`, the attempt loop is `application/runner.py`, file-backed RunState is application code, and the target tree reads as the installed view where it conflicts with RFC 0012; the contracts are authoritative — gates may be imported by application and cli. Added by execution 2026-08-22 — see .torve/tasks/T-0014 | `src/torve/gates/**` `src/torve/application/**` | — |
| D-15.12 | `ASSUMED` | Application reaches stores only through injected factories (`StoreFactory` on RunDeps; `reap` takes one); forze wiring lives in the facade and `config_hash` beside the telemetry it stamps. Added by execution 2026-08-22 — see .torve/tasks/T-0014 | `src/torve/application/**` | — |

## 9. Exit criteria

- Tree matches §3; no module remains at the package root except `__init__.py` and `py.typed`.
- `layering` gate at `blocking`; `lint-imports` clean, its three sabotage cases red on demand.
- `python -c "import torve"` does not import `application` or `adapters`.
- `torve gates run` works in a repository with no database, no sandbox and no agent credentials.

## Amendments

### A-19 — 2026-08-22 — the RFC format stays at the planner (amends §6.1, §6.2)

**Found while answering an interoperability question.** Foreign spec-driven formats (Spec Kit, OpenSpec) stay out of scope — they produce no graded decisions, so a bridged document carries nothing to inherit (0007 §6b, D-7.17). The option of a different specification source stays open only while the format stays where it is: if a gate or a runtime starts parsing RFC documents directly, interoperability closes, and it closes silently. The rule needed stating so it is not weakened by a plausible-sounding exception later.

**Changed:** §6.1's contract set gains a fourth rule, enforcing 0007's D-7.18:

```toml
[[tool.importlinter.contracts]]
name = "The RFC format stays at the planner"
type = "forbidden"
source_modules = ["torve.gates", "torve.adapters.runtime", "torve.adapters.agent"]
forbidden_modules = ["torve.config.rfc_parse"]
```

§6.2's note: this contract is format containment — a gate or runtime that parses specification documents directly is how the containment breaks, and it breaks quietly. Sabotage case: a gate importing `config.rfc_parse`.

**Unchanged:** everything else about the layering gate. The `rfc-valid` gate keeps calling `torve rfc check` — that is the CLI consuming the format at the planner's side of the line, not a gate parsing documents.
