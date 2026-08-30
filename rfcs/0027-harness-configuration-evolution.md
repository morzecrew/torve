---
id: "0027"
title: Harness configuration as measured evolution
kind: design
status: accepted
implementation: none
depends_on: ["0004", "0017", "0020", "0021"]
informed_by: ["0009", "0022", "0023"]
supersedes: []
superseded_by: null
amended_by: []
retired: []
owner: Lev Litvinov
description: >-
  Capturing the performance a well-configured harness offers without a
  runtime configurator: contract-selected tier variants, a configuration
  drafting run fed harness telemetry, and adoption gated on paired replay
  measurement — configuration stays committed, hashed and human-signed.
schema_version: 1
---

# RFC 0027 — Harness configuration as measured evolution

- **Scope:** How harness configuration improves over time. Covers tier
  variants a contract may select deterministically, a configuration drafting
  run — RFC 0020's machinery pointed at the configuration surfaces — whose
  fact feed carries harness telemetry populations, configuration-change lint,
  and the measurement obligation that lets an adopted candidate displace the
  incumbent only through a paired shadow replay verdict. Settles, as a
  refusal, the question that motivated it: whether a model should configure
  the harness at run time. Excludes prompt-side channels (skills and task
  context stay RFC 0009's subject), sandbox provisioning mechanics (RFC 0017
  unchanged), and broker policy (RFC 0021 unchanged).
- **Related:** [`0017`](0017-sandbox-provisioning.md) §2–§3 · [`0004`](0004-agents-tiering.md) §5 ·
  [`0020`](0020-intake-and-the-drafting-run.md) §5 · `src/torve/config/runconfig.py` ·
  `src/torve/application/intake.py` · `src/torve/application/evals.py` ·
  `.torve/sandbox/`
- **Inherits:** D-4.1, D-4.6 from RFC 0004; D-9.13, D-9.14 from RFC 0009;
  D-17.1, D-17.3, D-17.4, D-17.8 from RFC 0017; D-20.2, D-20.3 from
  RFC 0020; D-21.5, D-21.8 from RFC 0021.

---

## 1. Summary

A properly configured harness — the right permission profile, model overlay,
baked configuration file — measurably outperforms a default one; the roster's
own images already prove it in miniature, where the dsh definition bakes a
model overlay precisely because "the pin is identity". The tempting general
answer is a configurator: let a model tune the harness per run. This document
refuses that answer once, in a graded row, and builds the version that keeps
the measurement stack alive: variants a contract selects from committed
configuration, a drafting run that proposes configuration changes from the
telemetry the engine already writes, adoption through the ordinary human
signature, and a rule that no candidate configuration displaces the incumbent
without a paired replay verdict. The degree of freedom is real; it lives in
the corpus and the sandbox definitions, where it is reviewed, hashed and
comparable — never in the run.

## 2. Motivation

- **The boost is real and currently hand-harvested.** Every configuration
  improvement on the roster to date — the deepseek-chat overlay, the
  permission-mode env in tier commands, `HOME=/tmp`, the network opt-in —
  came from an operator noticing something in a trace. The noticing is the
  expensive part, and the telemetry that would systematise it is already
  written: per-tier cost, escalation reasons, and the unparseable-review
  streak that once took a whole batch to surface.
- **A runtime configurator would unplug the stack that justifies it.**
  `config_hash` is the identity every comparison joins on: shadow replays
  (RFC 0004 §5), skill evals (D-9.14 denormalises skills into every record),
  spec-quality attribution (RFC 0022), broker regimes (D-21.8). Configuration
  generated per run makes every run its own regime — the claim "the
  configurator helps" becomes unmeasurable by the act of deploying it.
- **The doctrine already points one way and lacks the mechanism.** D-17.4
  routes configuration channels by nature and D-17.1 makes the image digest
  the regime's identity; what is missing is any path by which configuration
  *improves* other than an operator's memory. RFC 0022 built the analogous
  reader for specifications; harness configuration is the surface with
  telemetry and no reader-to-proposal loop.
- **Per-task-class configuration is wanted and unreachable.** `Task.tier` is
  a literal of three seats, and `tier_for` resolves exactly those; a task
  that would benefit from a long-context variant or a stricter permission
  profile has no way to say so, even though the `tiers` mapping is an open
  dictionary that already validates unknown keys as configuration errors.

## 3. Current state

Verified against the tree, not from memory:

- `TierConfig` (`src/torve/config/runconfig.py`) carries adapter, command,
  model, provider, image, `api_key_env` and `auth_volume`; `tiers` is
  `dict[str, TierConfig]` with a missing key a loud configuration error, and
  the brokered regime already forbids `api_key_env` on routed tiers (D-21.1
  enforcement landed with RFC 0021 phase 1).
- `Task.tier` is `Literal["planner", "executor", "reviewer"]`
  (`src/torve/domain/task.py`) — the seat, not a variant.
- Sandbox definitions live committed under `.torve/sandbox/<name>/`, built by
  `torve sandbox build`, digest-resolved into `config_hash` at dispatch
  (RFC 0017, live since T-0036/T-0037). The dsh definition bakes
  `deepseek-chat.yml` with a comment stating the doctrine this document
  generalises.
- `execution_facts` (`src/torve/application/intake.py`) already injects
  escalation queues, contended paths and recent landings into drafter
  prompts — the fact-feed seam exists and this document widens what flows
  through it.
- The broker (RFC 0021 phases 1–2 landed) meters usage at the wire; D-21.5
  makes broker-measured spend authoritative where present, which is what
  makes a configuration-improvement claim about cost checkable at all.
- The eval ledger shape (`src/torve/application/evals.py`, D-9.15's
  arm-comparison records) is the house form for "two regimes, same tasks,
  direction not magnitude".

## 4. Goals / Non-goals

**Goals**

- A deterministic, hashed way for a task class to run under different
  harness configuration.
- Configuration proposals produced from telemetry populations instead of
  operator memory.
- Every adopted configuration change comparable to its incumbent by
  construction.
- The runtime-configurator question closed in a row rather than relitigated
  per session.

**Non-goals**

- **Tuning prompts, skills or task context.** The prompt side already has
  its evolution loop (RFC 0009: materialisation, attribution, evals) and is
  the *sanctioned* per-task degree of freedom. This document is about the
  harness side only, and the boundary is D-17.4's channel routing.
- **A configuration search service.** No grid runner, no optimiser. The
  drafting run proposes one reviewed change at a time; the replay battery
  measures it. Volume may motivate more later; evidence first.
- **Cross-harness abstraction.** A variant configures one named tier. No
  attempt to express "the same setting" across dsh, opencode and claude —
  RFC 0004 already refused a harness SDK, and a portable setting vocabulary
  is that SDK wearing configuration syntax.

## 5. Design

### 5.1 Tier variants

`tiers` grows variant entries under dotted names — `executor.long-context`,
`reviewer.strict` — each a full `TierConfig`. A contract gains an optional
`tier_variant`; resolution is `tiers["{seat}.{variant}"]`, refused loudly
when absent, falling back to nothing: naming a variant that does not exist
is a configuration error, not a default. The seat literal on `Task.tier` is
unchanged — a variant refines a seat, never invents one, and role semantics
(review isolation, planner containment) key on the seat exactly as today.

Because variants are ordinary tier entries in committed configuration, they
join `config_hash` through the existing tiers digest with no new mechanism:
two tasks on different variants are two regimes, visibly, which is the whole
point.

### 5.1a The attempt ladder

The 0022–0024 campaign measured where an executor's time goes: the first
attempt is the build — 13–19 minutes, two-thirds of generated tokens
thinking under `--effort xhigh` — while retries inherit the worktree and
answer gate feedback in 2–8 minutes. Paying maximum deliberation on every
attempt is paying for the retry's certainty at the build's price.

A tier entry (variant or seat) may name `retry_variant: <seat.variant>`:
the attempt after a gate-red dispatch resolves that variant instead of the
one the previous attempt ran under. One rung, not a list — a ladder taller
than two settings is a measurement question (D-27.7), not a configuration
shape. The record already carries the truth: each attempt's telemetry row
stamps the tier actually resolved, and the two variants are two digests.
Escalating *effort* (`high` building, `xhigh` retrying) is the first
intended use; escalating *model* (sonnet building, opus retrying) is the
same mechanism the day a measurement justifies it.

### 5.2 The configuration drafting run

A draft-role run over the configuration surfaces, through the intake
machinery (D-20.2's loop, D-20.3's adoption): the drafter reads the
configuration tree — `.torve/sandbox/**`, the tier blocks — plus a fact feed,
and proposes an ordinary task contract whose scope is confined to those
surfaces. Adoption mints it; the standing loop executes, reviews and lands it
like any task. No second adoption path, no config-specific verb: a
configuration change is a task, and everything the corpus built for tasks —
gates, review, approval, provenance — applies unmodified.

The fact feed is `execution_facts` widened with harness populations, all from
existing records: per-tier attempt and escalation counts by reason, per-tier
cost with broker-measured spend preferred where present (D-21.5) and the
self-reported number named as such where not, unparseable-review counts by
tier, and the current definitions' digests. The feed states RFC 0004's
quasi-experiment caveat the way RFC 0022 prints it: direction, never
magnitude.

### 5.3 Configuration-change lint

Deterministic, refusal by name, run as the drafting gate: the proposed diff
is confined to configuration surfaces; the resulting configuration parses
under the schema; every image whose definition changed builds clean; and
`torve doctor` passes over the result. Building at lint time does not touch
D-17.3 — that row forbids building *mid-run* on the dispatch path; a drafting
run building a candidate image to prove it builds is the same act as an
operator running `torve sandbox build` before adopting.

### 5.4 The measurement obligation

Landing a configuration change updates the definition; it does not yet make
the change the department's regime. The incumbent digest and the candidate
digest are both real, and the candidate earns the default through a paired
replay: the same tasks replayed under both digests — RFC 0004 §5's machinery,
recorded in the eval ledger's arm shape — with the verdict a human reads
before flipping the default. Until the verdict exists, the incumbent stands.
The ledger entry cites both digests and both regimes, which D-21.8 and
D-17.1 make unambiguous identifiers.

The obligation is deliberately placed at *displacement*, not at landing: a
definition may land, sit as a named variant, and accumulate organic evidence
before anyone runs the battery. What it may not do is silently become what
the department runs.

### 5.5 The refusal

No run ever executes under configuration generated for that run, and no
agent ever writes harness configuration its own run consumes. The first
clause is the measurement argument made permanent; the second is the
containment argument: sandbox definitions and tier blocks are read from the
root at dispatch, never from the worktree under work, so a task's content —
including injected content the agent was fed — cannot steer the permission
mode, network, or image of any run, its own included. The worktree-carried
channels (skills vendor, task context) remain exactly the channels D-17.4
already licenses: the repository instructing the agent about work.

### Alternatives considered

- **A runtime configurator agent.** The performance argument is real and the
  cost is structural: per-run configuration is per-run regimes, which
  forfeits shadow comparison, evals, and spec-quality attribution in one
  move; and a configurator inside the run is a privilege-escalation surface —
  whatever steers the agent steers the sandbox's own containment. Refused in
  D-27.1/D-27.2, with this paragraph as the standing answer to the next
  proposal.
- **Recording generated configuration into the hash instead.** Honest but
  useless: every run its own regime *accurately labelled* is still every
  comparison with a denominator of one.
- **A config-specific adoption path** (drafter emits a diff, operator
  applies). One decision fewer for the drafter, one parallel path more for
  the corpus to maintain, and it loses the review-and-gates coverage that
  making it an ordinary task gets for free. Rejected for reuse.
- **Doing nothing** (operator keeps harvesting traces by hand). This is
  today, it produced every improvement so far, and it does not scale past
  the operator's attention — which is the same shape RFC 0024 names for
  ticking and RFC 0022 names for specifications.

## 6. Tests

Variant resolution: dotted lookup, loud refusal on unknown, seat semantics
unchanged, variant participating in the hash (two variants, two hashes).
Lint: out-of-surface diff refused; unparseable configuration refused; a
definition that fails to build refused; doctor consulted. Fact feed: harness
populations assembled from seeded telemetry with broker-measured spend
preferred and self-reported labelled. Measurement: ledger entry carries both
digests; the displacement flow refuses without a verdict entry. Refusal:
configuration resolved from root while the worktree carries a hostile
`.torve/sandbox/` edit — the run's spec provably built from root state.

## 7. Docs

The sandbox provisioning page gains the variant naming convention and the
displacement rule, worded with RFC 0022's honesty: a replay verdict supports
direction, never magnitude. The dsh definition's in-file comment — the pin
is identity — is promoted from folklore to the page.

## 8. Out of scope

- **Automatic displacement.** A green verdict is evidence; flipping the
  default is the human's act, same shape as every adoption in this corpus.
  Reopened by nothing short of an amendment.
- **Scheduled recalibration.** "Harness version moved, rerun the battery" is
  a standing-maintenance trigger and belongs to RFC 0023's mechanism once
  that document is built; this document only guarantees the battery is
  runnable on demand.
- **Broker policy proposals.** The drafting run may notice cost populations;
  proposing routing or budget changes is RFC 0021's configuration and rides
  the same task path, but its lint and consequences are that document's
  subject.
- **Fleet-wide configuration.** Trust classes and per-repository capability
  are RFC 0024's manifest; a variant is per-root configuration like all
  configuration (D-13.3 direction unchanged).

## 9. Risks

- **Variant sprawl.** Cheap variants invite one per mood, fragmenting the
  populations every comparison needs. Mitigations: variants are reviewed
  configuration with a named owner like everything committed; the fact feed
  reports per-variant populations with denominators, so a variant nothing
  uses is visible; pruning is an ordinary configuration task.
- **The drafting run proposes plausibly and wrongly.** A model reading cost
  populations will sometimes propose confident nonsense. Contained by
  construction: lint bounds the surfaces, review reads the diff, adoption is
  human, and displacement waits on a replay verdict — four gates, none of
  them the proposer.
- **The measurement obligation gets waived under time pressure.** The first
  urgent fix (a harness release breaks headless mode) will make the paired
  replay feel like ceremony. The displacement rule deliberately does not
  block *landing* a fix — the incumbent-vs-candidate distinction exists
  exactly so an urgent definition change can land and run while the verdict
  accrues behind it.
- **Reading the refusal as distrust of models.** D-27.1 is not about model
  quality; a perfect configurator per run still destroys comparability. The
  row's consequence says so to keep the future argument on the right ground.

## 10. Unresolved questions

- The replay battery's composition — which tasks, how many, refreshed how
  often. RFC 0004 §5 practice suggests a handful of real landed tasks;
  execution proposes from the first live campaign (D-27.8).
- Whether variant selection belongs on the contract alone or also on a
  document's phasing entries, so a plan can mint a whole phase onto a
  variant. Contract-only is the smaller first shape; the phasing extension
  is one field if wanted.
- Where the displacement verdict is recorded beyond the eval ledger —
  whether the sandbox definition directory carries a pointer to the verdict
  that installed it, which would make `torve doctor` able to name an
  unmeasured default.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-27.1 | `LOCKED` | No run executes under configuration generated for that run: harness configuration is committed, adopted and hashed before any run consumes it | `src/torve/config/runconfig.py` `src/torve/application/telemetry.py` | Per-run configuration is per-run regimes — shadow comparison, evals and spec-quality attribution all lose their denominators in one move, however good the configurator |
| D-27.2 | `LOCKED` | No agent writes harness configuration its own run consumes: sandbox definitions and tier blocks resolve from the root at dispatch, never from the worktree under work | `src/torve/application/runner.py` `src/torve/config/runconfig.py` | Whatever steers the agent must not steer the sandbox's own containment; the worktree channels stay task-context only per D-17.4 |
| D-27.3 | `ASSUMED` | Tier variants are dotted entries in the `tiers` mapping (`seat.variant`), selected by an optional contract field, refused loudly when absent; the seat literal and role semantics are unchanged | `src/torve/config/runconfig.py` `src/torve/domain/task.py` | A variant refines a seat and rides the existing tiers digest into the hash — per-task-class configuration with zero new identity mechanism |
| D-27.4 | `ASSUMED` | The configurator is a drafting run producing an ordinary task through intake and adoption; no configuration-specific adoption path exists | `src/torve/application/intake.py` | A configuration change as a task inherits gates, review, approval and provenance for free; a parallel path would maintain all four twice |
| D-27.5 | `ASSUMED` | The fact feed widens `execution_facts` with per-tier populations — attempts, escalations by reason, cost with broker-measured spend preferred and self-reported labelled, unparseable-review counts, current digests — with the quasi-experiment caveat printed | `src/torve/application/intake.py` `src/torve/application/projections.py` | The noticing is the expensive part today; the populations exist in records nobody assembles, and an unlabelled self-reported cost would launder the measured subject's testimony |
| D-27.6 | `ASSUMED` | Configuration-change lint is deterministic: diff confined to configuration surfaces, schema parse, changed images build clean, doctor green; building at lint is not a D-17.3 violation | `src/torve/application/intake.py` | Refusal by name at the drafting gate, and the mid-run-build prohibition stays about the dispatch path where it belongs |
| D-27.7 | `LOCKED` | A candidate configuration displaces the incumbent default only through a paired replay verdict recorded in the eval ledger citing both digests; landing a change never silently changes the department's regime | `src/torve/application/evals.py` `.torve/sandbox/**` | The obligation sits at displacement so urgent fixes land freely — what is forbidden is the unmeasured default, not the unmeasured variant |
| D-27.8 | `OPEN` | The replay battery's composition and refresh cadence; execution proposes from the first live campaign | `src/torve/application/evals.py` | A battery too small proves nothing and too large never runs; the first campaign's cost is the only honest sizing input |
| D-27.9 | `ASSUMED` | Prompt-side channels remain the per-task degree of freedom: skills and task context evolve under RFC 0009, and this document adds no per-task harness variation beyond variant selection | `src/torve/application/skills.py` | Most of a "configured harness" gain is prompt-side and already instrumented; duplicating that freedom on the harness side would split one question across two measurement systems |
| D-27.11 | `ASSUMED` | A tier may name `retry_variant`, one rung: the attempt after a gate-red resolves the named variant; every attempt's telemetry row stamps the tier it actually ran under | `src/torve/config/runconfig.py` `src/torve/application/runner.py` | The build attempt and the feedback attempt are measurably different work; one committed rung keeps the regime enumerable where a free ladder would explode the hash space |
| D-27.10 | `OPEN` | Whether a sandbox definition records a pointer to the verdict that installed it as default, letting `torve doctor` name an unmeasured default | `.torve/sandbox/**` `src/torve/cli/doctor.py` | The displacement rule is only as visible as its records; execution decides whether the pointer earns its file |

## Phasing

```yaml
- phase: 1
  title: tier-variants
  intent: |
    Variant entries under dotted names in the tiers mapping, an optional
    contract field selecting one, loud refusal on an unknown variant, seat
    semantics untouched, and the variant riding the existing tiers digest
    into config_hash — two variants provably two regimes. The refusal rows
    land here too: configuration resolves from the root at dispatch, never
    from the worktree, pinned by test. retry_variant, one rung: the
    attempt after a gate-red resolves the named variant, each attempt's
    telemetry stamping the tier it actually ran under.
  scope:
    - "src/torve/config/**"
    - "src/torve/domain/**"
    - "src/torve/application/**"
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
  title: configuration-drafting-run
  intent: |
    The drafting run over the configuration surfaces: execution_facts
    widened with per-tier populations (broker-measured spend preferred,
    self-reported labelled, caveat printed), configuration-change lint —
    surface-confined diff, schema parse, images build, doctor green — and
    the proposal flowing through intake and adoption as an ordinary task
    with no configuration-specific path.
  scope:
    - "src/torve/application/**"
    - "src/torve/cli/**"
    - "tests/**"
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
    - "uv run lint-imports"
  depends_on: [1]
- phase: 3
  title: displacement-and-the-paired-replay
  intent: |
    The measurement obligation: paired replays of the same tasks under
    incumbent and candidate digests through the shadow machinery, verdicts
    in the eval ledger citing both digests, and displacement of the
    default refused without one — while landing and running a candidate as
    a named variant stays free. Doctor learns to name the default's
    verdict where recorded.
  scope:
    - "src/torve/application/**"
    - "src/torve/cli/**"
    - ".torve/sandbox/**"
    - "tests/**"
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
    - "uv run lint-imports"
  depends_on: [2]
```

## 12. Exit criteria

- One task landed under a contract-selected variant, its telemetry carrying
  a distinct regime from the seat's default.
- One configuration change proposed by the drafting run from a telemetry
  population, adopted, landed and visible as a digest change — with the
  proposal's cited population checkable in the fact feed.
- One displacement completed through a paired replay verdict, and one
  displacement attempt refused for lacking a verdict.
- The refusal demonstrated: a worktree carrying a hostile sandbox-definition
  edit while the run's spec provably resolves from root state.

## Amendments
