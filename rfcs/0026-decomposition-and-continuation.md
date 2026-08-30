---
id: "0026"
title: Decomposition and continuation
kind: design
status: draft
implementation: none
depends_on: ["0002", "0003", "0007", "0020"]
informed_by: ["0001", "0005", "0006", "0022"]
supersedes: []
superseded_by: null
amended_by: []
retired: []
owner: Lev Litvinov
description: >-
  What happens when a task is too big for one agent: the size verdict routes
  to a decomposition drafting run whose sub-contracts a human adopts, the
  parent becomes the integration task, and budget-exhausted attempts may
  continue from their own candidate instead of restarting.
schema_version: 1
---

# RFC 0026 — Decomposition and continuation

- **Scope:** What the engine does with work that exceeds one agent's reach.
  Covers the two distinct failures hiding under "too big" — a scope that
  should be several tasks, and one coherent task with a long horizon — and
  gives each its mechanism: a decomposition drafting run over an oversized
  contract, with deterministic lint and human adoption minting the children;
  and continuation attempts that resume from the previous candidate when an
  attempt ends on budget exhaustion rather than conviction. Adds one optional
  contract field (`parent`) and makes the size verdict a route instead of a
  note. Excludes any change to the planner's determinism, to phase authoring
  in RFCs, and to the lane — landing order is carried by dependencies exactly
  as today.
- **Related:** [`0020`](0020-intake-and-the-drafting-run.md) §5 · [`0007`](0007-planner-context.md) §3 ·
  [`0022`](0022-specification-quality.md) §5.3 · `src/torve/application/sizing.py` ·
  `src/torve/application/intake.py` · `src/torve/application/planner.py` ·
  `src/torve/application/runner.py`
- **Inherits:** D-1.7 from RFC 0001; D-2.9 from RFC 0002; D-3.19 from
  RFC 0003; D-7.1, D-7.22, D-7.23 from RFC 0007; D-20.2, D-20.3, D-20.4 from
  RFC 0020.

---

## 1. Summary

Today the size estimate is advisory: `torve plan` computes a verdict per
minted task and the verdict changes nothing. This document makes `too_large`
a route. An oversized contract goes to a decomposition run — RFC 0020's
drafting machinery pointed at a contract instead of a request — whose drafter
proposes sub-contracts, whose lint checks the split deterministically (children
inside the parent's scope, pairwise disjoint or dependency-ordered, the
acceptance battery owned), and whose adoption is the human signature that
mints the children. The parent survives as the integration task: it depends on
every child and runs the full battery last, so "the big thing is done" is an
ordinary landing rather than a synthetic state. Separately, an attempt that
dies of budget rather than conviction may continue from its own candidate
instead of starting over — the narrow lift that stops long-horizon work from
discarding real progress at every ceiling.

## 2. Motivation

- **The verdict already fires and nothing listens.** `sizing.estimate` runs
  on every minted task (`src/torve/application/planner.py`, the `PlannedTask`
  assembly) and has flagged real work — the RFC 0018 mint flagged both its
  phases, and the RFC 0021 phases were re-minted under A-56 because the
  authored scopes underestimated the measured touch surface. Authoring-time
  scope guesses run wrong in one direction.
- **Oversized work fails expensively.** A task whose horizon exceeds an
  attempt burns the full ceiling before escalating — three attempts of real
  spend producing a `poison_ceiling` whose triage answer is "this was three
  tasks". The lab's T-0113 arc showed the adjacent shape: the contract, not
  the code, was the defect, and the attempts fee was paid before a human
  looked.
- **Attempts discard progress by design.** The attempt loop restarts from
  base with only the revision record carried (`feedback.md`). That is correct
  when the previous path was convicted — continuing a wrong turn doubles down
  on it — and wasteful when the previous attempt was simply not finished: the
  work was sound and the clock ran out.
- **Finer phasing is not the answer.** The author can always write smaller
  phases, and A-56 is the evidence that authoring-time decomposition guesses
  wrong even when attempted seriously. The information needed for a good
  split — what the tree actually looks like under the scope — exists at
  drafting time, in a worktree, which is exactly where RFC 0020 already puts
  a drafter.

## 3. Current state

Verified against the tree, not from memory:

- `src/torve/application/sizing.py` grades on three rules of thumb — allow
  globs, acceptance count, top-level modules — and its own docstring records
  (A-49) that the `HistoricalPercentile` arm waits on retrospective
  calibration. RFC 0022's reader now exists and is that calibration's data
  source.
- `src/torve/application/intake.py` carries the whole drafting kit this
  document reuses: `lint_drafts` with tree-aware glob checks, `lint_contract`,
  `mint_intake_task`, `run_intake`, `adopt` with lock borrowing, decision
  inheritance via the corpus parser.
- The planner's `globs_intersect` is the pairwise-disjointness primitive
  (D-7.23's same-phase rule) and is conservative by construction.
- Task contracts carry `depends_on`, `budget` (iterations, wallclock, tokens
  — null on every live contract to date), and no parent field.
- The revision loop (RFC 0005 §4a, D-5.12) carries feedback across attempts;
  the branch survives across attempts (one pull request per task); the
  worktree is cut from base each attempt.

## 4. Goals / Non-goals

**Goals**

- Turn an oversized contract into several right-sized ones without the
  engine deciding what work exists.
- Make "the whole thing landed" an ordinary landing with the parent's own
  battery behind it.
- Stop budget-exhausted attempts from discarding sound partial work.
- Give the size thresholds a calibration source instead of a hunch.

**Non-goals**

- **Automatic splitting.** The planner stays deterministic (D-7.1) and the
  machine never mints work uninvited (D-20.3). Splitting needs judgement,
  judgement means a model, and a model's output enters the queue only through
  adoption.
- **A task tree.** Identifiers stay flat, the state machine is untouched, and
  hierarchy is one optional field plus dependency edges. A nested task store
  would buy projection grouping — which one field buys — at the price of a
  second lifecycle.
- **Continuation as a retry policy.** Continuation is not "try harder"; a
  convicted attempt never continues. The revision loop already owns the
  convicted case.
- **Cross-task shared branches.** Children are ordinary tasks with ordinary
  candidates; nothing lands except through the lane.

## 5. Design

### 5.1 The route

`too_large` stops being a note. At mint (`torve plan`) and at adoption
(intake), a contract whose verdict is `too_large` is not queued for dispatch:
it is marked as awaiting decomposition, and the loop's dispatch leg skips it
the way it skips drafts. The operator override — dispatching an oversized
contract deliberately — is explicit (`torve run <id> --oversize`), because
the verdict is a heuristic and the human outranks it; the override is
recorded on the run.

### 5.2 The decomposition run

A draft-role run, exactly RFC 0020's shape: read-only worktree at base, the
drafter's prompt built from the parent contract (intent, scope, acceptance,
inherited decisions) plus the tree under its scope. The drafter proposes
sub-contracts as a drafts document. Lint — deterministic, refusal by name —
adds four rules to the existing `lint_drafts` battery:

1. The union of children `scope.allow` fits inside the parent's allow —
   checked with the planner's glob machinery; a child escaping the parent
   scope is a scope grant nobody signed.
2. Children are pairwise scope-disjoint, or carry explicit `depends_on`
   edges between them where they overlap (D-7.23's rule, applied inside one
   decomposition).
3. Every parent acceptance command appears on at least one child or on the
   integration task — the battery may be distributed, never dropped.
4. Every child's own size verdict is `ok`. A decomposition that yields an
   oversized child has not decomposed.

Decision inheritance is per-child: each child carries the parent's inherited
rows whose Paths intersect that child's scope, grades as copied on the parent
(mint-time copy, unchanged doctrine).

### 5.3 Adoption and the integration task

Adoption is the human act and the only minting act (D-20.3): children get
identifiers under the tick lock, contracts committed, each carrying
`parent: <parent-id>`. In the same adoption, the parent's contract gains the
children in `depends_on` and keeps its scope and full acceptance battery —
it becomes the integration task, dispatched only after every child lands,
verifying the composed whole and landing whatever integration work the
composition itself needs. Its landing is the decomposition's completion; no
new state, no synthetic roll-up.

A parent that needs no integration work still runs: an agent with a green
tree and nothing to add produces a small or empty diff and the battery is the
point. The empty-diff review interaction is known (a reviewer once blocked an
empty diff as suspicious) and the integration prompt says what the task is,
so the reviewer reads it as verification rather than evasion.

### 5.4 Hierarchy is one field

`parent` is optional contract data used by projections only: `torve context`
and the board group children under their parent, and the parent's thread
shows child landings the way review attempts already ride their target's
thread. Nothing in dispatch, the lane, or the store reads it — ordering is
entirely `depends_on`, which the existing machinery already honours.

Depth is bounded: a child of a child may be decomposed once more, and a
third level is refused. Two human adoption rounds is the budget for being
wrong about size; past that the phasing in the source document was wrong,
which is an amendment, not more machinery.

### 5.5 Continuation attempts

When an attempt ends on **budget exhaustion** — wallclock or token budget,
the run's own limits, not a gate result — the next attempt may continue: its
worktree is cut from the previous attempt's candidate tip instead of base,
and the prompt states plainly that the agent is resuming its own unfinished
work. Three properties hold it in shape:

- **Conviction never continues.** A gate failure, a review blocker, or any
  escalation that judged the work restarts from base through the revision
  loop as today. Continuation is only for work that ran out of room.
- **Measurement is unchanged.** Diffs and gates are computed against the
  original base exactly as now — the in-clone-commit rule already forces
  diff-vs-parent, and a continued attempt is the same case with more commits.
- **The ceiling still counts.** A continuation is an attempt; the poison
  ceiling and budgets apply cumulatively. Continuation changes what an
  attempt starts from, never how many the run gets.

### 5.6 Calibration

The thresholds in `sizing.py` are declared guesses. RFC 0022's per-document
signals — attempts to green, escalations by reason — are the population that
grades them: the exit criteria require at least one threshold regraded citing
a `torve rfc health` population, which is also the `HistoricalPercentile`
arm's entrance evidence (A-49's deferral, honoured rather than reversed).

### Alternatives considered

- **Planner auto-split.** Deterministic splitting is impossible — a split is
  a judgement about coupling — and model-in-the-planner is refused by D-7.1.
  The drafting run is precisely the sanctioned container for that judgement.
- **Finer authored phases.** Available today, kept as the first line, and
  demonstrated insufficient by A-56: the author guesses at scope before the
  tree is in front of anyone.
- **A `programme` state over children.** A synthetic roll-up state would
  need its own transitions, projections and disposal. The integration task
  expresses completion with machinery that exists, and its battery is a
  stronger claim than any derived state.
- **Continuation by default for every attempt.** Rejected: continuing a
  convicted path is how an agent digs in. The conviction/exhaustion split is
  the load-bearing line, and it is cheap to enforce because the escalation
  reason already encodes it.

## 6. Tests

Decomposition lint: each of §5.2's four rules with a red twin (child escapes
scope; overlapping children without an edge; dropped acceptance command;
oversized child). Adoption: children minted with `parent` set, parent's
`depends_on` grown, ids under the lock, double-adoption refused — the
existing intake adoption cases extended, not duplicated. Route: a `too_large`
contract skipped by dispatch, dispatched under the explicit override with the
override recorded. Continuation: budget-exhausted attempt resumes from the
candidate tip and the diff still measures against original base; a convicted
attempt restarts from base; ceilings count continuations. Depth: third-level
decomposition refused.

## 7. Docs

The `rfc-writer` skill's phasing guidance gains one paragraph: phases may be
authored coarse where the split is genuinely unknowable at authoring time,
because the decomposition run exists — with the caveat that every
decomposition costs an adoption round, so known splits still belong in the
document. `flag-dont-flip` needs nothing: a child task's divergence log works
exactly as any task's.

## 8. Out of scope

- **Re-decomposing on escalation.** A child that escalates is triaged like
  any task. Automatically re-splitting troubled work would be the engine
  reshaping the plan mid-flight; the human owns that move.
- **Parallel dispatch changes.** Children are eligible for the existing
  disjoint-scope parallel dispatch exactly as any tasks are; nothing here
  widens it.
- **Cross-document decomposition.** A parent decomposes within its own scope
  and its own source document. Work spanning documents is two phases in two
  documents, as it is today.
- **Budget authoring guidance.** What wallclock or token budget a contract
  should carry is operator knowledge accruing in the field; this document
  only makes the fields consequential.

## 9. Risks

- **Decomposition becomes the default answer.** If every substantial phase
  routes through a drafter, adoption rounds multiply and the human becomes a
  click-through. Mitigations: the route fires only on the verdict, the
  verdict is calibrated (§5.6), and the depth bound caps the recursion.
- **The integration task is a rubber stamp.** If children are truly disjoint
  the parent may have nothing to do, and a reviewer may read an empty diff as
  evasion. Accepted: the battery run over the composed tree is the point, and
  the prompt names the task's nature. If integration tasks prove pure
  ceremony in practice, the verdict is evidence for removing them by
  amendment — recorded here so the future argument has its hook.
- **Continuation inherits a mess.** A budget-exhausted attempt may leave a
  half-refactored tree that a fresh agent continues badly. The continuation
  prompt carries the previous attempt's own trail (its commits and record),
  and the ceiling bounds the total spend either way. If continued attempts
  measurably underperform restarts, the data will say so — both shapes record
  the same telemetry.
- **`parent` drifts into semantics.** One field invites dispatch or lane
  logic to read it someday. D-26.5 grades the projection-only rule; a future
  consumer amends or is refused.

## 10. Unresolved questions

- What the decomposition drafter's prompt includes beyond contract and tree —
  whether the parent document's full decision table rides along or only the
  rows the parent inherited. Execution decides and logs it (D-26.10).
- Whether continuation should carry the previous attempt's trace tail into
  the prompt or only the record and commits. Traces are large and unvetted;
  starting with commits-only is the conservative shape (D-26.11).
- Whether the `too_large` route should apply to hand-minted contracts at
  admission or only to planner and intake mints. Hand-minting is the
  operator's escape hatch and may deserve to stay unrouted.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-26.1 | `LOCKED` | Decomposition never mints: the drafter proposes, deterministic lint refuses by name, and children exist only at human adoption under the tick lock | `src/torve/application/intake.py` `src/torve/application/planner.py` | D-20.3 and D-7.1 applied to splitting; a machine that splits its own work decides what work exists |
| D-26.2 | `LOCKED` | The union of children scopes fits inside the parent's `scope.allow`; a child escaping the parent scope is refused at lint | `src/torve/application/intake.py` | A decomposition that widens scope is a grant nobody signed, wearing a split as a disguise |
| D-26.3 | `ASSUMED` | Children are pairwise scope-disjoint or explicitly dependency-ordered, checked with the planner's glob machinery | `src/torve/application/intake.py` `src/torve/application/planner.py` | D-7.23's rule inside one decomposition; silent overlap serialises in the best case and conflicts in the worst |
| D-26.4 | `ASSUMED` | Every parent acceptance command lands on at least one child or on the integration task; a decomposition may distribute the battery, never drop it | `src/torve/application/intake.py` | The parent's acceptance is the definition of done; a split that loses a command redefines done silently |
| D-26.5 | `ASSUMED` | `parent` is optional contract data read by projections only; dispatch, lane and store never consult it — ordering is `depends_on` alone | `src/torve/domain/task.py` `src/torve/application/projections.py` | One field buys the grouping a task tree would buy, without a second lifecycle; a future semantic consumer amends or is refused |
| D-26.6 | `ASSUMED` | At adoption the parent becomes the integration task: `depends_on` gains every child, scope and full battery stay, and its landing is the decomposition's completion | `src/torve/application/intake.py` | Completion as an ordinary landing with the composed tree verified, instead of a synthetic roll-up state |
| D-26.7 | `ASSUMED` | A `too_large` verdict routes: the contract awaits decomposition and dispatch skips it; the operator override is explicit and recorded on the run | `src/torve/application/loop.py` `src/torve/application/sizing.py` | An advisory verdict that changes nothing has been the state since D-2.9 shipped; a heuristic that blocks silently would be worse, so the override is a first-class verb |
| D-26.8 | `LOCKED` | Continuation fires only on budget exhaustion — wallclock or tokens — never on a gate conviction, review blocker or any judged escalation; convicted work restarts from base through the revision loop | `src/torve/application/runner.py` | The conviction/exhaustion line is what separates resuming sound work from doubling down on a wrong turn |
| D-26.9 | `ASSUMED` | A continued attempt cuts its worktree from the previous candidate tip; diffs, gates and the attempt ceiling are measured exactly as for any attempt, against the original base | `src/torve/application/runner.py` `src/torve/adapters/workspace/git.py` | Continuation changes what an attempt starts from, never what is measured or how many attempts the run gets |
| D-26.10 | `OPEN` | What the decomposition prompt carries beyond the parent contract and the tree under scope; execution decides and logs it | `src/torve/application/intake.py` | The drafts' quality against prompt size is an empirical trade the first live decompositions should settle |
| D-26.11 | `OPEN` | Whether continuation feeds the previous trace tail into the prompt or only the record and commits; execution starts commits-only and logs the evidence | `src/torve/application/runner.py` | Traces are large and unvetted; the conservative shape first, widened only on evidence |
| D-26.12 | `ASSUMED` | Decomposition depth is bounded at two levels; a third is refused with the bound named | `src/torve/application/intake.py` | Two adoption rounds is the budget for misjudging size; past that the source document's phasing was wrong, which is an amendment |
| D-26.13 | `ASSUMED` | Sizing thresholds are regraded from `torve rfc health` populations, and the `HistoricalPercentile` arm enters only on that evidence | `src/torve/application/sizing.py` | A-49 deferred the arm until calibration data existed; RFC 0022 is that data, and guessing new constants would waste it |

## Phasing

```yaml
- phase: 1
  title: decomposition-run-and-adoption
  intent: |
    The too_large route and the decomposition drafting run: an oversized
    contract awaits decomposition and dispatch skips it; a draft-role run
    over a read-only worktree proposes sub-contracts; lint adds the four
    decomposition rules to the existing battery; human adoption mints
    children carrying parent, grows the parent's depends_on, and the
    parent becomes the integration task. Projections group children under
    their parent. Explicit recorded operator override for dispatching
    oversized contracts directly. Depth bounded at two.
  scope:
    - "src/torve/application/**"
    - "src/torve/domain/**"
    - "src/torve/cli/**"
    - "tests/**"
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
    - "uv run lint-imports"
    - "uv run torve rfc check"
  depends_on: []
- phase: 2
  title: continuation-attempts
  intent: |
    Budget-exhausted attempts resume from their own candidate: the next
    worktree cuts from the previous attempt's tip, the prompt states the
    continuation, and diffs, gates and ceilings stay measured against the
    original base exactly as for any attempt. Convicted attempts restart
    from base through the revision loop unchanged. The budget fields on
    contracts become consequential.
  scope:
    - "src/torve/application/**"
    - "src/torve/adapters/**"
    - "tests/**"
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
    - "uv run lint-imports"
  depends_on: [1]
```

## 12. Exit criteria

- One live `too_large` contract decomposed end to end: drafted, adopted,
  children landed, integration task landed with the full parent battery
  green.
- One continuation observed live: a budget-exhausted attempt resumed from
  its candidate and reached green in fewer total attempts than the ceiling.
- A convicted attempt demonstrated restarting from base while continuation
  was configured — the conviction line holding under test and in the field.
- At least one sizing threshold regraded by amendment citing a
  `torve rfc health` population rather than a remembered incident.

## Amendments
