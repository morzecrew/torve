---
id: "0007"
title: Planner and context
status: draft
depends_on: ["0003"]
informed_by: []
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  Minting tasks from an approved RFC, projecting execution facts back into planning sessions, and the read-only MCP surface.
schema_version: 1
---

# RFC 0007 — Planner and context

- **Scope:** The planner module: minting tasks from an approved RFC, projecting execution facts back into a planning session, and the read-only MCP surface. Excludes any model call inside the engine, permanently.
- **Inherits:** D-1, D-2, D-2a, D-25 from RFC 0001

---

## 1. Why last

The planner projects accumulated facts. With no accumulation there is nothing to project, and the module degenerates into a task-file generator that a shell script would do better.

Ship it when `torve context` would have something to say that a person could not recall unaided.

## 2. Asymmetry by write authority

| Module | Reads | Writes | Invokes models |
| --- | --- | --- | --- |
| `torve.executor` | tasks, gates, decisions | task state, attempts, gate results, findings, telemetry | yes — to produce diffs and findings, never to decide state |
| `torve.planner` | everything | new tasks only, from an approved RFC | **no, ever** |

The planner has no verb that touches a running aggregate. It cannot cancel, reprioritise, restart or re-scope anything in flight. This is what prevents it from growing into an autonomous orchestrator — not policy, but the absence of a capability.

Two application services over one domain and one store, not two services with two databases.

## 3. `torve plan`

A deterministic transformation of an approved document into tasks:

- parse the Phasing section into a task set
- inherit relevant decisions with their grades and declared paths
- verify the dependency graph is acyclic
- verify `scope.allow` sets within a phase do not intersect
- refuse anything that is not a committed, reviewed document

`--dry-run` by default. No model call at any point.

## 4. `torve context`

The primary artefact, and not a plan — a projection of facts into a form a planning session can consume:

- tasks by state, with the phase they belong to
- escalations grouped by reason, with resolution times
- execution-log divergences, ready to become decision-table rows
- per-gate hit rates and durations
- cost and iterations by task, against `config_hash`

Three things fall out almost free:

**RFC amendments stop being copy-paste.** Divergence entries are data; the projection emits candidate decision-table rows with references to the log entries that produced them. The author accepts or rejects. Append-only is preserved and nothing is retyped.

**Gate accumulation becomes observable.** Grouping escalations by reason shows that five of seven shared a cause. A human still writes the gate — but *what* to write comes from data rather than recollection. This is the answer to the objection that gates must be added by hand: they must, but not blindly.

**Phase N+1 sees how phase N ended.** An `ASSUMED` decision that execution contradicted twice should not enter the next phase unchanged.

## 5. MCP as the read surface

Copying `torve context` output into a session by hand is exactly the manual transfer this design removes. The planner therefore exposes a **read-only** MCP server over its projections.

This does not weaken D-2: the direction of control is inverted from the dangerous case. A human-supervised session *pulls* facts; the engine never invokes a model to decide anything. The human still writes and commits the RFC, and `torve plan` still refuses anything uncommitted.

Two constraints, enforced at wiring rather than by instruction:

- **No write tools are exposed.** Queries only; `torve plan` is not among them.
- **No agent in an execution sandbox gets this server.** It is for the planning session on a human's machine. An executor that can read other tasks' escalations is an executor that can rationalise its way out of its own scope.

## 6. The loop, closed

```text
torve context  →  [human + expensive model]  →  RFC commit (reviewed)
                                                  ↓
                                             torve plan  →  tasks
                                                  ↓
                                             torve run   →  facts
                                                  ↓
                                             torve context ...
```

No point in the loop passes a decision from machine to machine without a human signature, and no point requires a human to move data by hand. That combination is the whole objective.

## 6a. Cold start

Everything here assumes a task inherits graded decisions. Existing repositories have none — they have years of implicit agreement. Two paths, and both are needed.

**Greenfield is the easy case.** No decisions exist and they are created as work proceeds. This requires only that an empty decision list be *legal and explicit*: `decisions: []` present in the contract, never absent, so `decisions-reported` can distinguish "none apply" from "the field was forgotten".

**Existing repositories get a `DecisionSource` port.**

```python
class DecisionSource(Protocol):
    def standing(self, repo: str, paths: list[str]) -> list[InheritedDecision]: ...
```

| Adapter | Deterministic | Source |
| --- | --- | --- |
| `RfcDirectory` | yes | decision tables already written by `rfc-writer` — a markdown table parse, nothing more |
| `ExecutionLog` | yes | `resolved` and `departed` entries are decisions someone already made under pressure |
| `Constitution` | yes | one hand-written standing-decisions document per repository |

Non-deterministic extraction from unstructured sources — reading a codebase and proposing decisions — runs **outside** the engine, as a skill, in a supervised session, and emits a document a human commits. Identical in shape to `torve context`, and for the same reason: it keeps D-2 intact. No adapter in this port ever calls a model.

The realistic sequence is lazy: start with `RfcDirectory` over what already exists, let `ExecutionLog` accumulate the rest, and write a `Constitution` only for the decisions that keep being rediscovered.

## 7. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-7.1 | `LOCKED` | No model calls inside the planner, for any reason | `src/torve/planner/**` | The place where they will try to creep in; violation shows up as a new dependency |
| D-7.2 | `LOCKED` | `torve plan` accepts only a committed, reviewed document | `src/torve/planner/**` | The human signature in the loop |
| D-7.3 | `LOCKED` | The MCP surface is read-only and never given to an executing agent | `src/torve/planner/**` | A writable planning surface is autonomous planning by another route |
| D-7.4 | `OPEN` | Output format of `torve context` | `src/torve/planner/**` | Markdown, JSON, or both — decided by use |
| D-7.5 | `LOCKED` | An empty decision list is legal but must be explicit | `src/torve/gates/decisions_reported.py` `src/torve/domain/**` | Distinguishes "none apply" from "field forgotten" |
| D-7.6 | `LOCKED` | `DecisionSource` adapters are deterministic; model-assisted extraction runs outside the engine | `src/torve/planner/**` | Keeps D-2 intact while still bootstrapping brownfield repositories |

## 8. Exit criteria

- `torve plan` mints a phase from a real RFC with no manual editing of the result.
- `torve context` surfaces a gate candidate that had not been noticed by hand.
