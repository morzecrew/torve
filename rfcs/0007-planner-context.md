---
id: "0007"
title: Planner and context
status: draft
implementation: none
depends_on: ["0003", "0016"]
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
- **Inherits:** D-1, D-2, D-25, D-31 from RFC 0001

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

`torve plan` mints `implement` tasks only; review tasks are minted by the runner at `gated` (RFC 0005 §1.1). Each contract's `intent` paragraph (D-1.7) comes from the phase entry, or is written by hand for a standalone task.

### 3.1 Admission

Before minting anything, `torve plan <id>` refuses on any of:

- **the document is not `accepted`** — a `draft` has no settled decisions to inherit
- **any `depends_on` target is not `accepted`** — see below
- **the document is `superseded`**, or `superseded_by` is set
- **a cycle exists** in the `depends_on` graph reachable from this document

Each refusal exits 3 (configuration error, RFC 0011) and names the offending document and edge. `informed_by` is not checked: it is a reading hint and carries no constraint.

**Why an unsettled dependency is a hard refusal.** A document's decision table inherits rows from the documents it depends on, and inheritance copies the grade at mint time. Inheriting `LOCKED` from a `draft` hands an executor a grade that may change tomorrow — which is exactly what D-A.4 and the copy-grade-at-write-time rule exist to prevent. The refusal is not procedural tidiness; it protects the guarantee the whole conflict protocol rests on.

### 3.2 One RFC at a time

`torve plan` takes exactly one identifier. It does not accept a set, a subgraph, or `--all`.

Planning several documents at once produces a large batch of tasks inheriting decisions from documents that will still be amended, which is precisely the drift this system exists to remove. Plan one, ship its phase, then plan the next — the readiness gate in §3.1 is what makes that sequence enforceable rather than merely advised.

### 3.3 Supersession after minting

A document may become `superseded` after its tasks were minted. Those tasks inherit decisions that no longer stand.

`torve plan --reconcile` marks every non-terminal task minted from a superseded document, escalating each with reason `stale_inheritance`. It does not delete or rewrite them: what to do with in-flight work is a human decision, and the runner's job is to make the situation visible rather than to resolve it.

## 3a. Format validation

The RFC format is what `torve plan` consumes, so the package owns it. Validation does not depend on a skill being installed.

```
torve rfc check [PATH...]     # schema, decision table, links, cycles
torve rfc index               # regenerate INDEX.md
torve rfc graph               # depends_on edges and decision inheritance
```

### Checks

**Frontmatter:** required fields present; `status`, `kind` and `implementation` from their vocabularies; `id` matches the filename; `schema_version` present.

**Decision table:** present; every row graded; `paths:` on every `LOCKED` row; identifiers unique within the document and never reused after removal.

**Links:** every `depends_on`, `informed_by` and `supersedes` target resolves; the `depends_on` graph is acyclic; no document inherits from one that is not `accepted` (D-A.10).

**Rot:** `paths` globs match something in the repository; no source path with a line number appears anywhere in the body — those are stale at the first refactor above them.

Each failure names the document, the row or field, and what was expected. Exit 3 (RFC 0011): a malformed document is a configuration error.

### The `rfc-valid` gate

```yaml
- name: rfc-valid
  run: "torve rfc check"
  input: worktree
  state: shadow          # enters per D-2.18; promotion per D-2.23
  origin: rfc/0007
  timeout: 20
```

A **product gate**, not a self-development one (RFC 0015 §6.4): any repository keeping specifications for `torve plan` needs it. It ships in the package and has no dependency beyond `torve` itself.

`torve rfc index --check` runs in the same gate — regenerate and fail if the result differs from what is committed, the same discipline as a lockfile.

Sabotage cases: an ungraded decision row, a `LOCKED` row with no `paths`, a duplicated identifier, a two-document `depends_on` cycle, a document inheriting from a `draft`, and a hand-edited `INDEX.md`.

### What stays in the skill

The package checks form; the skill teaches content — how to grade honestly, how to choose `paths`, how to phrase a consequence, when an amendment is required instead of an edit, and what deserves an RFC at all. None of that is checkable, and all of it is what makes a document worth having.

Same split as `flag-dont-flip` and its gate.

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

**The programme view.** Alongside task-level facts, `torve context` renders the RFC graph: which documents are accepted, which of their phases have shipped, what became plannable when the last one closed, and where a `depends_on` cycle exists. This is where the graph is genuinely useful. It answers "what can be taken on next", which is currently a question held in someone's head.

**Implementation state.** For each accepted document, its `implementation` assertion (D-A.11) alongside the derived progress of each phase — `planned`, `in_flight`, `blocked`, `shipped`. Progress is computed on demand from task states and stored nowhere (D-A.12); per phase, not per document, because phase-level is the granularity at which decisions get made. Where the assertion and the derivation disagree — `complete` asserted while a phase is `blocked` — say so. That disagreement is usually either a forgotten assertion or a task nobody triaged, and both are worth surfacing.

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

## 6b. Out of scope

**Compatibility with foreign spec-driven formats.** Excluded, and not for effort reasons.

Spec Kit, OpenSpec and similar tools generate phased specifications. They do not produce graded decisions, declared paths, per-task scope, or identifiers a divergence log can cite — so a document bridged from one of them would carry nothing to inherit, and `torve plan` would mint tasks with an empty `decisions` list. The anti-drift contour would not degrade; it would be absent.

Universal compatibility would cost exactly the thing that makes this engine worth having.

The format's surface is narrow by construction: it terminates at the planner. `decisions-reported` reads `InheritedDecision` from the contract; the runner, gates, review, merge and telemetry see task contracts only. Keeping it that way is what keeps a different source possible at all (D-7.18) — a grade is a human judgement about how expensive a decision is to reverse, and it cannot be derived from a document where nobody made that judgement, by any mechanism, ever.

**Reopened when:** a project that already keeps specifications in another format adopts Torve and will not rewrite them. The `DecisionSource` port is the extension point; an adapter is a day's work, and it cannot be deterministic — decisions absent from a document are extracted outside the engine, by a skill, and accepted by a human.

## 7. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-7.1 | `LOCKED` | No model calls inside the planner, for any reason | `src/torve/planner/**` | The place where they will try to creep in; violation shows up as a new dependency |
| D-7.2 | `LOCKED` | `torve plan` accepts only a committed, reviewed document | `src/torve/planner/**` | The human signature in the loop |
| D-7.3 | `LOCKED` | The MCP surface is read-only and never given to an executing agent | `src/torve/planner/**` | A writable planning surface is autonomous planning by another route |
| D-7.4 | `OPEN` | Output format of `torve context` | `src/torve/planner/**` | Markdown, JSON, or both — decided by use |
| D-7.5 | `LOCKED` | An empty decision list is legal but must be explicit | `src/torve/gates/decisions_reported.py` `src/torve/domain/**` | Distinguishes "none apply" from "field forgotten" |
| D-7.6 | `LOCKED` | `DecisionSource` adapters are deterministic; model-assisted extraction runs outside the engine | `src/torve/planner/**` | Keeps D-2 intact while still bootstrapping brownfield repositories |
| D-7.7 | `LOCKED` | `torve plan` refuses unless the document and every `depends_on` target are `accepted` | `src/torve/application/planner.py` | Inheriting a grade from a draft breaks the copy-at-write-time guarantee |
| D-7.8 | `LOCKED` | `torve plan` takes exactly one document; no sets, no subgraphs, no `--all` | `src/torve/application/planner.py` | Batch planning inherits from documents still being amended — the drift this system removes |
| D-7.9 | `ASSUMED` | `informed_by` is unenforced | `rfcs/**` | A reading hint that blocks is an irritant with no protection attached |
| D-7.10 | `LOCKED` | Tasks minted from a document that later becomes `superseded` escalate as `stale_inheritance`; the engine does not rewrite them | `src/torve/application/planner.py` | What to do with in-flight work is a human decision |
| D-7.11 | `ASSUMED` | `torve context` renders the RFC graph, not only task state | `src/torve/application/planner.py` | The graph's real use is human, not scheduling |
| D-7.12 | `LOCKED` | The RFC format is owned by the package; validation does not depend on an installed skill | `src/torve/config/rfc_parse.py` | Two parsers and no definition is how the planner and the validator drift apart |
| D-7.13 | `LOCKED` | `Grade`, `Status` and `Kind` are defined once in `domain/rfc.py` and imported everywhere | `src/torve/domain/rfc.py` | A duplicated vocabulary eventually gains a member in one copy only |
| D-7.14 | `ASSUMED` | `rfc-valid` is a product gate, shipped in the package | `src/torve/cli/rfc.py` | Any repository writing specs for `torve plan` needs it |
| D-7.15 | `ASSUMED` | `torve context` reports asserted `implementation` beside derived per-phase progress, and flags disagreement | `src/torve/application/planner.py` | The disagreement is the informative part |
| D-7.16 | `LOCKED` | `torve rfc check --with-store` is opt-in; the default check needs no database | `src/torve/config/rfc_parse.py` | `rfc-valid` is a product gate and must run without infrastructure |
| D-7.17 | `LOCKED` | Foreign spec formats are out of scope; `DecisionSource` is the extension point if that changes | `src/torve/application/ports.py` | A bridged document carries nothing to inherit, so the anti-drift contour is absent rather than degraded |
| D-7.18 | `LOCKED` | Only `torve plan`, `torve rfc *` and `RfcDirectory` know the RFC format | `src/torve/config/rfc_parse.py` `src/torve/cli/rfc.py` `pyproject.toml` | The format terminating at the planner is what keeps a different source possible at all |

## 8. Exit criteria

- `torve plan` mints a phase from a real RFC with no manual editing of the result.
- `torve context` surfaces a gate candidate that had not been noticed by hand.
