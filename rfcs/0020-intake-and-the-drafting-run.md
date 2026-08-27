---
id: "0020"
title: Intake and the drafting run
status: accepted
implementation: partial
depends_on: ["0003", "0007", "0008"]
informed_by: ["0005", "0019"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  A commander's free-form request becomes lint-checked draft task contracts through a sandboxed drafting run; a human adopts or refuses, and ids are minted only at adoption.
schema_version: 1
---

# RFC 0020 — Intake and the drafting run

- **Implementation state:** phase 1 executed 2026-08-27 (T-0089 — the `draft`
  role on the planner tier's seat, the drafting run with its draft-lint loop,
  the contract lint standalone as `torve lint-contract`, and `torve adopt`
  minting ids under the tick lock; D-20.8 settled at `intake.max_drafts: 4`).
  Outstanding: phase 2 (board intake) and the demand-gated phase 3.
- **Scope:** How a request in prose becomes one or more task contracts without
  a human writing YAML: a new run role (`draft`) executed by the existing
  runner machinery, a deterministic contract lint that gates its output, an
  adoption step that is the human signature, and — in its second phase — the
  board as the intake surface with the existing verb machinery extended by
  `adopt`. Touches `src/torve/application/intake.py` (new),
  `src/torve/domain/task.py` (role vocabulary), `src/torve/application/tracker.py`
  and `src/torve/cli/**`. No change to `torve plan`, the gates, the lane, or
  the landing policy. Excludes autonomous backlog generation and RFC authoring
  by the engine, permanently.
- **Related:** RFC 0005 (review as a run — the pattern this copies), RFC 0007
  (the deterministic planner this deliberately does not touch), RFC 0008
  (verbs and authorization), `src/torve/application/review.py`,
  `src/torve/application/planner.py`, `src/torve/application/tracker.py`.
- **Origin:** dogfood operation 2026-08-24..26 — every lab contract was
  hand-minted by the operator's session; see §2 for what that cost.

---

## 1. Summary

A new role, `draft`, turns a commander's free-form request into draft task
contracts. The drafting run executes under the runner exactly as a review
does — sandboxed, budgeted, telemetered, its output parsed as data — and its
gate is a deterministic contract lint: schema validity, scope hygiene,
within-batch disjointness. Drafts carry no task ids; ids are minted at
adoption, atomically with the commit that makes the contracts real. Adoption
is a human act — `torve adopt` in phase 1, `/torve adopt` on the intake
issue's thread in phase 2, with `/torve revise` re-running the drafter
against captured thread feedback. The planner module stays exactly as
RFC 0007 locked it: no model call inside it, ever.

## 2. Motivation

The charter names specification, not execution, as the bottleneck: "a human
writes every issue, and nothing learned during execution flows back into the
next one" (RFC 0001 §1). RFCs 0002–0019 built the downstream half; every
contract in the system is still authored by hand. Three weeks of dogfood
put numbers on what that costs:

- **A contract defect burns the full escalation machinery.** T-0113's
  hand-written contract mandated a new test file for functions added to an
  existing module; the executor's correct instinct fought the contract three
  straight attempts, burned the poison ceiling, and paused a nine-task batch
  behind the escalation. The defect was mechanical — checkable before any
  sandbox ever started — but nothing checks a hand-minted contract.
- **Id assignment races the runner.** Hand-minting reads the next free id and
  writes a file; the runner mints review contracts off the same counter
  within the same minute. Two collisions in one day (T-0101, T-0103 stolen
  mid-mint) forced an operator rule — "mint fast" — that is a race window
  wearing a procedure.
- **The operator's session is a single point of specification.** Every
  request, however small, waits for one human to transcribe intent into YAML
  with correct globs and acceptance commands. The transcription is the least
  interesting and most error-prone step in the whole loop.

RFC 0007 §6 draws the loop as `torve context → [human + expensive model] →
committed spec`. This RFC mechanizes the bracket for the small case — a
standalone request that needs no RFC amendment — while keeping the property
that section declares non-negotiable: no decision passes machine to machine
without a human signature.

## 3. Current state

Verified against the code, not memory:

- `Task.role` is `Literal["implement", "review", "revert"]`
  (`src/torve/domain/task.py`); review-role shape constraints live beside it.
- Review-as-a-run (`src/torve/application/review.py`) is the working
  precedent for model judgment entering the engine without deciding anything:
  runner-minted task, sandboxed attempt, output parsed as data
  (`parse_findings` — the last JSON document wins, unparseable recorded and
  never invented as clean), consequence applied by configuration.
- `torve plan` (`src/torve/application/planner.py`) is deterministic: it
  refuses non-accepted documents, parses Phasing into contracts, checks
  dependency acyclicity and within-phase scope disjointness
  (`globs_intersect`). `next_task_number` is the racing counter from §2.
- The board verb vocabulary is `COMMANDS = ("retry", "abandon", "unblock",
  "approve", "revise")` (`src/torve/application/tracker.py`), commander-gated
  by D-8.9; A-40's `revise` and A-41's feedback capture
  (`src/torve/application/feedback.py`) already carry reviewer threads into a
  re-run — the exact machinery an intake revision needs.
- Hand-minted standalone contracts ship `decisions: []` (explicit-empty,
  D-7.5); decision inheritance exists only through `torve plan`'s
  deterministic copy from an accepted document.

## 4. Goals / Non-goals

**Goals**

- A commander states intent in prose; the system produces contracts that
  would have passed the operator's own review, and a human adopts them with
  one act.
- Contract defects of the T-0113 class are caught by a lint before a sandbox
  ever starts.
- The id-assignment race is closed structurally, not procedurally.
- The intake thread supports the same conversational loop code review got:
  feedback in, revised drafts out, on the same thread.

**Non-goals**

- **Autonomous backlog generation.** The system never invents its own work;
  intake is always a human request. This is the planner-authority boundary of
  RFC 0007 §2, kept by construction.
- **RFC authoring or amendment by the engine.** A request that needs new
  decisions or touches an existing RFC's territory is refused back to the
  human loop of 0007 §6; the drafter writes contracts, never specifications.
- **Replacing `torve plan`.** Phased RFC work keeps its deterministic minter;
  intake serves the standalone-request case 0007 §3 already acknowledged as
  "written by hand".
- **Customer or anonymous intake.** Requests come from configured commanders
  (D-8.9's list); widening the population is a policy decision for a later
  amendment, not a default.

## 5. Design

### 5.1 The drafting run

A new role `draft` joins the vocabulary. The drafting task is minted by the
engine when a request arrives (CLI in phase 1, board in phase 2), carries the
request text as its intent, and executes under the ordinary runner: sandbox,
tier, budget, telemetry, state machine — nothing new. The workspace mounts
read-only, like a review: the drafter reads the tree to write honest globs
and acceptance commands, and can write nothing.

The drafter's input is the request text, the repository tree, and the file
inventory of prior contracts (as examples of shape, not authority). Its
output is one JSON document:

```json
{"drafts": [{"ref": "DRAFT-1",
             "intent": "...",
             "scope": {"allow": ["src/lab/foo.py", "tests/test_foo.py"], "deny": []},
             "acceptance": ["python3 -m unittest discover -s tests -v"],
             "depends_on": []}],
 "rationale": "one paragraph: how the request decomposed, what was excluded"}
```

Parsed with the same discipline as findings (D-5.4's sibling): the last JSON
document with a `drafts` key wins; unparseable is recorded as a red attempt,
never invented as empty. `depends_on` between drafts uses `DRAFT-n` refs,
rewritten at adoption.

The drafting tier is its own seat (`drafter`) in the tiering config, like
`reviewer` — the model that writes contracts well is not necessarily the one
that reviews or implements well, and its cost is metered separately.

### 5.2 The contract lint

The lint is the drafting run's gate hook — deterministic, engine-side, no
model. A red lint is a red attempt; the run iterates within its budget like
any implement attempt against red gates. Checks, all naming the offending
draft and field:

- **Schema:** each draft validates against the task model (minus id); intent
  non-empty; acceptance non-empty and shell-parseable.
- **Scope hygiene:** `allow` non-empty; every glob either matches the tree or
  is a creatable path (no glob that can never match); `deny` does not
  swallow `allow`.
- **Batch disjointness:** pairwise `globs_intersect` across the request's
  drafts is empty — the drafts must be dispatchable in parallel, the same
  rule `torve plan` enforces within a phase.
- **The learned rules.** The T-0113 rule ships first: a draft whose allow
  includes an existing module must also allow that module's existing test
  file(s). The rule set is expected to grow from escalation evidence; each
  rule cites the escalation that produced it.

The lint also runs standalone as `torve lint-contract <path>` so hand-minted
contracts get the same protection — the operator path stays legal and gets
safer.

### 5.3 Adoption

Adoption is the human signature, and the only moment ids exist. On
`torve adopt <draft-task-id>` (phase 1) or a commander's `/torve adopt`
(phase 2), the engine — under the tick lock, in one motion — reads the next
free id once per draft, rewrites `DRAFT-n` refs, writes the contract files,
and commits them as engine records on base. The loop then dispatches them
exactly as it dispatches hand-minted work; there is no special path after
adoption, and nothing between id assignment and commit for a concurrent
minter to race.

Decisions: a request may name a governing RFC. At adoption — not at drafting
— the named document's decision rows are copied with their grades by the
same deterministic path `torve plan` uses. The drafter may *propose*
applicability in its rationale; it never assigns a grade and never writes a
decision row. A request naming no RFC adopts with `decisions: []`, explicit.

Refusal is `torve abandon` / `/torve abandon` on the drafting task —
standard verb, no new machinery.

### 5.4 Board intake (phase 2)

A commander files a request as an issue labeled `torve.intake`; the tracker
poll mints the drafting task targeting it. When drafts go green through the
lint, they project onto the issue thread — the contracts in full, plus the
rationale — with the standard authority footer (the run store owns the
truth; the board is a projection). The thread then speaks the existing
grammar:

- `/torve adopt` — adopt every draft as §5.3.
- `/torve revise` — capture the thread's feedback (A-40/A-41's machinery,
  reused verbatim: capture-first, refusal leaves the drafts standing) and
  re-queue the drafting task; the next attempt sees the feedback file.
- `/torve abandon` — refuse the request; the drafting task closes.

`adopt` joins `COMMANDS` with the same authorization gate as every verb.
On a non-draft task it is refused with the reason in the reply, symmetric
with `approve`'s role check.

### 5.5 What this deliberately does not touch

`torve plan` and the planner module keep every LOCKED row of RFC 0007:
D-7.1 (no model calls inside the planner) survives because the drafter runs
under the *runner*, exactly as the reviewer does — the planner module gains
only deterministic lint helpers it already half-owns (`globs_intersect`).
D-7.2's human signature moves nowhere: it is now called adoption. The state
machine, landing policy, and review path are untouched; a drafting task is
never landed and never approved, like a review (D-8.14's sibling).

### Alternatives considered

- **A model call inside `torve plan`.** Rejected without discussion —
  D-7.1 is LOCKED, and the review precedent shows the run boundary does the
  same job without breaching it.
- **Drafts as pull requests of contract files.** Rejected: a PR carries merge
  semantics and a branch lifecycle, while a draft needs adopt-or-refuse
  semantics and no branch; worse, contract files on a branch re-open the id
  race the moment two requests draft concurrently. The board thread is the
  reading surface the corpus already committed to.
- **Ids at drafting time with a reservation ledger.** Rejected: a ledger that
  reserves ids for drafts a human may refuse leaks ids on every refusal and
  adds a cleanup obligation; minting at adoption costs one rewrite of
  `DRAFT-n` refs and closes the race outright.

## 6. Tests

- Lint unit family: each check red and green, the T-0113 rule against a real
  tree fixture, batch disjointness including the empty-allow-contends rule.
- Parse family: drafts document extraction mirroring `parse_findings` tests —
  chatter, ANSI, unparseable recorded.
- Adoption: id minting under contention (two adoptions racing the counter
  under the lock), `DRAFT-n` dependency rewrite, decisions copy from an
  accepted fixture RFC, refusal on a draft naming a non-accepted document.
- Tracker: `adopt` authorization, role refusal on non-draft tasks, the
  revise capture path against a fake tracker.
- A fake-agent drafting run end to end: request in, lint-green drafts out,
  adoption commits, loop dispatches — the conformance shape RFC 0003's
  suites established.

## 7. Out of scope

- **Execution-facts context in the drafter's input** (recent escalations,
  contended paths, cost data). The projection exists (`torve context`) and
  wiring it in is a later phase's enrichment once drafting quality data says
  what the drafter actually lacks; premature context is prompt surface for
  no measured gain.
- **Multi-repository intake.** One root per request until the portfolio
  question (a future RFC) is answered for the loop as a whole.
- **Drafting RFC amendments.** Named in §4; the escape hatch is the human
  session loop of 0007 §6, which this RFC feeds but does not replace.

## 8. Risks

- **The drafter writes plausible-but-wrong acceptance commands** — commands
  that pass trivially or test nothing. The lint checks shape, not meaning;
  the mitigation is the adoption read (a human sees the full contract, not a
  summary) and the same gates that judge every implement attempt. Accepted:
  a bad contract adopted by a human is today's failure mode with fewer
  keystrokes, not a new one.
- **Verb-surface growth.** `adopt` is the sixth verb; each addition costs
  board-grammar teaching. Mitigated by strict symmetry with existing verbs
  (authorization, reply-on-thread, role refusal) — no new interaction shape.
- **The lint's learned rules drift into policy.** A rule set that grows
  unreviewed becomes a shadow specification. Mitigated: each rule cites its
  escalation, and rules land as ordinary reviewed commits.

## 9. Unresolved questions

- The decomposition ceiling (how many drafts one request may yield) — config
  knob with a small default; the number is an implementation decision
  (D-20.8).
- Disposal of an adopted or abandoned drafting task's state — likely
  identical to a review task's reap path; implementation confirms against
  the reaper's actual rules (D-20.10).

## 10. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-20.1 | `LOCKED` | A draft never dispatches without a human's adoption; adoption is RFC 0007 §6's signature, relocated but never removed | `src/torve/application/intake.py` `src/torve/application/tracker.py` | Remove it and the engine plans autonomously — the boundary 0007 §2 exists to prevent |
| D-20.2 | `LOCKED` | Drafting is a run under the runner — role `draft`, its own tier seat, read-only workspace — never a planner-module model call; D-7.1 stands untouched | `src/torve/application/intake.py` `src/torve/application/runner.py` `src/torve/domain/task.py` | The review precedent (0005) is the load-bearing argument that the run boundary suffices; breaching D-7.1 instead forfeits it |
| D-20.3 | `LOCKED` | Draft output is data gated by the deterministic contract lint; red lint is a red attempt; unparseable output is recorded, never treated as empty | `src/torve/application/intake.py` | A draft a human sees has already passed every mechanical check — the T-0113 class dies before the board |
| D-20.4 | `LOCKED` | Task ids are minted at adoption, atomically with the contract commit under the engine lock; drafts carry request-local `DRAFT-n` refs | `src/torve/application/intake.py` | Closes the id race hit twice in dogfood (T-0101, T-0103); reservation ledgers rejected in §5.3's alternatives |
| D-20.5 | `ASSUMED` | Intake authorization is the commander list (D-8.9); requests arrive as `torve.intake`-labeled issues in phase 2, CLI in phase 1 | `src/torve/application/tracker.py` `src/torve/cli/**` | — |
| D-20.6 | `ASSUMED` | The intake thread is a revision loop: `/torve revise` captures thread feedback and re-runs the drafter through the A-40/A-41 machinery, reused not re-implemented | `src/torve/application/tracker.py` `src/torve/application/feedback.py` | A second capture mechanism would drift from the first; reuse is the guard |
| D-20.7 | `ASSUMED` | Adopted contracts commit as engine records on base and dispatch through the ordinary loop; no post-adoption special path | `src/torve/application/intake.py` `src/torve/application/loop.py` | A parallel dispatch path is a second loop to keep honest |
| D-20.8 | `OPEN` | The decomposition ceiling per request — the knob's default and where it lives; settled by the first weeks of drafting telemetry | `src/torve/config/runconfig.py` | — |
| D-20.9 | `ASSUMED` | Decision inheritance at adoption is a deterministic copy from a named accepted document, by the planner's existing path; the drafter proposes applicability in prose only and never writes a row or a grade | `src/torve/application/intake.py` `src/torve/application/planner.py` | A model-assigned grade is a human judgement faked; D-A.4's copy-at-write-time guarantee would silently rot |
| D-20.10 | `OPEN` | Disposal of a consumed drafting task (adopted or abandoned) — expected to follow the review task's reap path; the executor confirms and logs | `src/torve/application/reaper.py` | — |

## 11. Phasing

```yaml
- phase: 1
  title: the-drafting-run-and-the-lint
  intent: >-
    The role, the run, the lint, and CLI adoption: role vocabulary gains
    "draft"; a drafting run executes a request from the command line into
    lint-gated draft documents persisted under the task's directory; the
    contract lint ships with the schema, scope-hygiene, batch-disjointness
    and T-0113 checks and is invocable standalone; torve adopt mints ids
    under the lock, rewrites DRAFT-n refs, copies decisions from a named
    accepted document, and commits the contracts as engine records.
  scope: ["src/torve/application/intake.py", "src/torve/domain/task.py",
          "src/torve/config/runconfig.py", "src/torve/cli/**",
          "tests/**"]
  acceptance: ["uv run pytest -q", "uv run torve rfc check"]
  depends_on: []
- phase: 2
  title: board-intake
  intent: >-
    The board becomes the intake surface: torve.intake-labeled issues mint
    drafting tasks; lint-green drafts project onto the thread with rationale
    and the authority footer; adopt joins the verb vocabulary with standard
    authorization and role refusal; revise re-runs the drafter against
    captured thread feedback; abandon refuses the request.
  scope: ["src/torve/application/tracker.py", "src/torve/application/intake.py",
          "src/torve/cli/tick.py", "tests/**"]
  acceptance: ["uv run pytest -q"]
  depends_on: [1]
- phase: 3
  title: context-enrichment
  intent: >-
    Demand-gated: the drafter's input gains selected torve context
    projections (contended paths, recent escalations by reason) once
    drafting telemetry shows what the drafter lacks; the lint's learned-rule
    set gains its documented growth path, each rule citing the escalation
    that produced it.
  scope: ["src/torve/application/intake.py", "src/torve/application/projections.py",
          "tests/**"]
  acceptance: ["uv run pytest -q"]
  depends_on: [2]
```
