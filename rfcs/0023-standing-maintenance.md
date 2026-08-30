---
id: "0023"
title: Standing maintenance
kind: design
status: accepted
implementation: complete
depends_on: ["0007", "0019", "0020"]
informed_by: ["0001", "0002", "0006", "0012"]
supersedes: []
superseded_by: null
amended_by: []
retired: []
owner: Lev Litvinov
description: >-
  Work that recurs on a condition rather than on a plan: a committed contract
  template plus a deterministic trigger the tick evaluates and instantiates —
  the machine recognising a condition a human already decided to answer,
  never inventing a backlog.
schema_version: 1
---

# RFC 0023 — Standing maintenance

- **Scope:** How recurring maintenance enters the queue without the engine
  creating work. Defines the standing contract (a reviewed, committed template
  plus a deterministic trigger predicate), how the tick evaluates predicates
  and instantiates contracts through the existing adoption path, and the
  bounds that keep the one growing leg from outpacing triage. Excludes any
  model in trigger evaluation or contract generation, any predicate that is
  not a pure function of committed inputs plus the repository's own tools,
  prioritisation of any kind, and inbound network feeds — which belong to
  RFC 0021's egress question before they belong here.
- **Related:** [`0019`](0019-standing-loop.md) §3–§5 · [`0020`](0020-intake-and-the-drafting-run.md) §5.3 ·
  `src/torve/application/loop.py` · `src/torve/application/intake.py` ·
  `src/torve/config/layout.py`
- **Inherits:** D-2, D-1.7 from RFC 0001; D-7.1, D-7.2 from RFC 0007;
  D-19.1, D-19.5, D-19.8 from RFC 0019; D-20.3 from RFC 0020.

---

## 1. Summary

A standing contract is a file in git: an intent paragraph, a scope, an
acceptance battery, and a deterministic predicate that says when the work is
due. A human writes it once, in a diff someone can refuse. Thereafter the tick
evaluates the predicate — in a sandbox, with no agent, exit code as the answer
— and when it holds, mints one ordinary task contract through the same path
adoption already uses. The machine never decides that work exists; it
recognises a condition a human already decided to answer, which is the same
move `torve plan` makes over a reviewed document, with a predicate where the
phase list was.

## 2. Motivation

D-19.8 is `LOCKED` and correct: the tick never creates work, because the
standing failure mode of agent systems is the machine inventing its own
backlog. It is also the reason the engine cannot do an IT department's daily
bread — lockfile drift, an advisory against a pinned package, a test that has
started flaking, a pin that expires — none of which arrives as a phase of an
RFC and all of which recurs.

Today those enter one way: a human notices and runs `torve intake`. That has
two costs and one defect.

- **The human is the trigger.** A forgotten check is an unpatched dependency,
  and forgetting is silent. Every other failure mode in this corpus is loud by
  design; this one is not.
- **Intake pays a drafting run for a known answer.** RFC 0020's drafter reads
  the tree and writes contracts with a model. For "refresh the lockfile", the
  contract is the same every time and the model is being paid to rediscover
  it.
- **The instances are incomparable.** A drafted contract differs slightly on
  every firing — different globs, different acceptance wording — so a
  population of "the lockfile task" can never be compared across months. RFC
  0022's readings need the opposite.

The shape that resolves it is already in the corpus. `torve plan` mints tasks
mechanically from a document a human reviewed and committed; D-7.2's human
signature lives upstream, in the document, not in the minting. A standing
contract is that arrangement with a predicate in place of the phasing block.

## 3. Current state

- `src/torve/application/loop.py` selects queued tasks by reading the file
  system: a contract exists, no run record names it, dependencies have landed.
  There is no leg that produces a contract.
- `src/torve/application/intake.py` mints drafting tasks and, on adoption,
  allocates ids under the tick lock and commits contract files as engine
  records. That is the whole instantiation mechanism this document needs; it
  is reached today only from a human request.
- RFC 0007's `inherit_decisions` (post-A-47) is the single reader that copies
  a document's decision rows with grades onto a contract, used by both
  `torve plan` and adoption. A standing contract naming a document inherits
  through it and nowhere else.
- The escalation pause (D-19.5) already suppresses the dispatch leg while the
  queue holds unhandled failures. Nothing else in a tick is bounded by triage.
- RFC 0002 §7.6 already derives per-gate flake rate from attempt telemetry, so
  the second standing contract in §5.6 has its input already computed.

## 4. Goals / Non-goals

**Goals**

- Recurring maintenance runs without a human remembering.
- Every instance of a recurring job is byte-comparable with the last, so it
  forms a population.
- The condition that fires the work is reviewable in a diff, versioned, and
  refusable — the same standard the corpus holds every other input to.
- The one leg that can grow the backlog is bounded by the same attention
  budget that bounds everything else.

**Non-goals**

- **Autonomous backlog generation.** RFC 0020 already refused it and this
  document does not reopen it. A predicate that requires a fresh intent per
  firing is a planner in disguise; §5.5 makes that falsifiable rather than
  merely asserted.
- **Prioritisation.** Selection stays ascending task id (D-19.4). A standing
  instance is an ordinary task and waits its turn; a priority field here would
  be the second planner RFC 0019 refused.
- **Scheduling.** A predicate answers "is this due", never "is it Tuesday".
  Cadence belongs to the scheduler that fires the tick, exactly as D-19.1
  settled.
- **Replacing intake.** A novel request is still a human's prose through
  RFC 0020. Standing contracts cover only work whose answer was already
  written down.

## 5. Design

### 5.1 The standing contract

One file per recurring job, under `.torve/standing/<name>.yaml`, committed and
reviewed:

```yaml
name: lockfile-drift
trigger:
  kind: command                    # command | path-digest
  run: "uv lock --check"           # non-zero exit means the condition holds
intent: >-
  Refresh the dependency lockfile against the constraints in pyproject.toml
  and confirm the suite passes under the refreshed pins, so that a drifted
  lock is corrected while the change is one commit wide.
scope:
  allow: ["uv.lock"]
  deny: []
acceptance:
  - "uv sync --locked"
  - "uv run pytest"
decisions_from: "0012"             # optional; inherited at mint time
cooldown_hours: 168
max_open: 1
```

Everything below `trigger` is a task contract minus its id, and is validated by
RFC 0020's contract lint — the same lint, unchanged, so a standing contract
cannot carry a defect the drafting run would have been refused for. The file
being in git is the human signature: someone decided, in a diff, that this
condition deserves this response.

### 5.2 The predicate is a fact, evaluated where facts are evaluated

A predicate runs under the ordinary runner's sandbox against a read-only
workspace, with no agent — the drafting run's isolation minus the model. Exit
code is the answer: non-zero means due. This is D-3's rule applied to the one
new input: a check runs outside an agent session, and no model is consulted
about whether work exists.

Two kinds ship, and the second exists because the first cannot express
everything cheaply:

- `command` — a shell line in the sandbox, as above.
- `path-digest` — the digest of a set of paths, compared against the digest
  recorded when the job last fired. Due when it differs. This covers "the
  advisory file changed", "the pinned base image reference moved" and their
  siblings without a tool having to exist.

**A predicate that errors is not a trigger.** Any outcome that is not a clean
exit is an engine event of the `gate_infrastructure_failure` family and the
standing contract is skipped for that tick. The whole leg fails closed toward
*not* creating work, because the asymmetry is real: a missed firing is a delay
and a spurious firing is spend plus a contract someone must triage.

### 5.3 Instantiation is minting, not planning

On a firing predicate the tick mints through the path RFC 0020 §5.3 already
uses: under the tick lock, in one motion, read the next free id, write the
contract file, commit it as an engine record on base. `decisions_from`, if
present, inherits that document's rows with their grades through
`inherit_decisions` and nothing else; absent, the contract carries
`decisions: []`, explicit as RFC 0007 §6a requires.

The instance records its origin — `standing: lockfile-drift` — so every later
report can attribute it, and so RFC 0022's populations can treat repeated
instances of one job as one population rather than as unrelated tasks.

### 5.4 Bounds, because this is the leg that can grow

Four, in the order they apply:

1. **The escalation pause first.** A tick paused under D-19.5 evaluates no
   predicates at all. A queue nobody triages must not also be a queue that
   grows — this is the existing rule, and the standing leg is placed inside it
   rather than beside it.
2. **`cooldown_hours` and `max_open` per job.** A job with a live instance
   does not fire again; a job that fired within its cooldown does not fire
   again. Both are read from the ledger, which is the same run-record oracle
   the loop already consults.
3. **`loop.standing_max_per_tick`, default 1**, across all jobs — the same
   doctrine as one dispatch per tick, for the same reason: spend per unit time
   stays cadence times a known bound.
4. **Self-disabling on repeated failure.** A job whose instances have failed
   to land `standing.strike_limit` times consecutively (default 3) stops
   firing and says so in an engine event naming the job. A predicate that
   fires forever is a predicate that is wrong, and grinding is the worst
   available response.

There is no `enabled` flag. An empty `.torve/standing/` is the off switch and
deleting a file is the disable, exactly as scheduling the verb is RFC 0019's
enablement.

### 5.5 D-19.8 restated, and made falsifiable

The tick still creates no work. It evaluates a predicate a human committed and
instantiates a response that human wrote. The intent paragraph is fixed at
authoring time and is identical in every instance — that is not an incidental
property, it is the test.

**Falsifiable prediction, in the style of RFC 0001's A-5:** if this model is
wrong, standing contracts will start needing their intent rewritten per firing
— an operator editing `intent` before adopting, or instances escalating
`underspecified` because the fixed paragraph did not fit the day's condition.
The first time that happens for a job, the job was a planner in disguise and
the honest response is to delete it and route the work through RFC 0020's
intake, not to add a template variable. Until it appears, no change.

### 5.6 First customers, in order

1. **Lockfile drift** — the example above; the predicate is a tool the
   repository already runs.
2. **Flake quarantine** — RFC 0002 §7.6 already derives per-gate flake rate
   from attempt telemetry; the predicate is a threshold read over that, and
   the response is a contract to quarantine or fix the command.
3. **Expiring pins** — image digests and skill versions that have aged past a
   configured window; `path-digest` covers the moved-reference case directly.

Advisory and CVE feeds are the obvious fourth and are deliberately not here: a
feed is an inbound network dependency with its own trust question, and it
belongs after RFC 0021 has settled what a run may talk to.

### Alternatives considered

- **A cron entry per job calling `torve intake`.** Its trade is that it works
  today with no new mechanism, at the price of a drafting run per firing
  (paying a model to rediscover a known contract) and instances that differ
  every time, which makes the population useless to RFC 0022. Kept as the
  fallback for one-off jobs nobody expects to recur.
- **A `recurring: true` flag on an ordinary contract.** Its trade is a much
  smaller diff, at the price of the contract having no way to say *when* — the
  condition ends up in the scheduler's crontab, outside review, which is the
  one place this corpus refuses to keep decisions.
- **A model-evaluated predicate ("is this dependency worth bumping?").**
  Refused under D-2 without further discussion. A model may not decide what
  work exists, and a predicate is exactly that decision.
- **Instantiating drafts for a human to adopt rather than contracts
  directly.** Its trade is an extra human signature per firing, at the price
  of making the mechanism useless for its purpose: a maintenance job that
  needs a human every time is the human-as-trigger arrangement this document
  exists to remove. The signature moved upstream, into the committed file,
  which is where RFC 0007 already puts it.

## 6. Tests

The simulation harness RFC 0003 built for the state machine covers the leg
without a live repository: a fixture root with standing files, a stubbed
predicate outcome per tick, and assertions on what was minted. The cases that
matter are the refusals — a predicate that errors mints nothing; a paused tick
evaluates nothing; a job at `max_open` mints nothing; a job inside its
cooldown mints nothing; a job at the strike limit disables itself and says
which. One end-to-end case on the lab repository proves the whole path: a real
drift, one instance, landed by the loop, no second firing inside the cooldown.

Sabotage discipline applies to the predicate runner as it does to gates: a
predicate that cannot fail red is a predicate that is not being evaluated, so
each kind gets a red-on-demand case.

## 7. Docs

`.torve/standing/` joins the RFC 0013 layout diagram with a one-line
description. The reader's guide says plainly what a standing contract is for
and what it is not: a job whose intent needs to change per firing does not
belong here, and that sentence needs to arrive before someone's first
template variable, not after.

## 8. Out of scope

- **Inbound feeds.** Advisories, release watchers, anything that polls a
  network service. Named as the fourth customer; gated on RFC 0021 settling
  what a run may talk to and on who holds the credential for the feed.
- **Cross-repository standing jobs.** One root, one set of standing contracts.
  A fleet-level job is RFC 0024's question and would need an answer for which
  root the instance lands in.
- **Dependency-graph-aware bumping.** Choosing *which* dependency to bump is a
  decision; the standing contract answers "the lock has drifted" and the
  executor does the work under an ordinary contract with ordinary gates.
- **Templating the intent.** Named here as the thing not built, because §5.5
  makes it the signal that the mechanism was wrong rather than a feature
  request.

## 9. Risks

- **The backlog grows faster than triage.** The reason for four independent
  bounds and for placing the leg inside the escalation pause rather than
  beside it. The residual risk is a badly chosen predicate that fires
  correctly and often; the strike limit does not catch that, because the
  instances land. Mitigation is review of the file, which is why it is in git.
- **Read as permission to automate planning.** The nearest thing in this
  corpus to the failure D-19.8 exists to prevent, and the document will be
  cited by whoever next wants a machine-generated backlog. §5.5's falsifiable
  test is the answer, and it is deliberately a test the mechanism can fail.
- **Predicate cost.** Every standing file is a sandboxed command per tick, so
  ten jobs at a five-minute cadence is a real load. Mitigated by cooldown
  short-circuiting evaluation entirely and by the per-tick bound; if it stops
  being enough, predicates get their own cadence, which is a knob this
  document deliberately does not ship first.
- **A green predicate on a broken tool.** `uv lock --check` exiting non-zero
  because `uv` is missing would fire forever. The strike limit is the backstop;
  fail-closed on error is the front line.

## 10. Unresolved questions

- Whether the ledger of firings belongs in the telemetry stream or in its own
  file. Telemetry is host-local and gitignored, and a cooldown that resets on a
  fresh clone is a surprise; a committed ledger is a file the engine writes to
  git on every firing, which the corpus has so far only done for contracts.
- Whether `max_open` should count escalated instances. An escalated instance
  is unresolved work, which argues yes; a permanently escalated instance would
  then disable the job silently, which argues no and for the strike limit
  instead.
- The right default for `standing.strike_limit`. Three is a guess; the first
  job to hit it will say whether it was impatient.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-23.1 | `LOCKED` | A standing contract is a committed, reviewed file under `.torve/standing/`; the human signature is the file in a diff, never a per-firing approval | `.torve/standing/**` `src/torve/config/layout.py` | Moves the signature upstream exactly as RFC 0007 does for phasing; a per-firing approval would restore the human-as-trigger arrangement this removes |
| D-23.2 | `LOCKED` | A trigger predicate is deterministic, runs in a sandbox with no agent, and answers by exit code; no model participates in deciding that work is due | `src/torve/application/standing.py` | D-2 and D-7.1 unchanged: a model that decides work exists is the exact non-determinism this project removes |
| D-23.3 | `LOCKED` | A predicate that errors mints nothing and records an engine event; the leg fails closed toward not creating work | `src/torve/application/standing.py` | A missed firing is a delay; a spurious firing is spend plus a contract someone must triage — the asymmetry decides the default |
| D-23.4 | `LOCKED` | Instantiation uses RFC 0020 §5.3's adoption path unchanged — id under the tick lock, contract committed as an engine record — and inherits decisions only through `inherit_decisions` | `src/torve/application/standing.py` `src/torve/application/intake.py` | One minting path; a second would reopen the id race that §5.3 closed structurally |
| D-23.5 | `LOCKED` | A standing instance's intent is fixed at authoring time and identical across firings; per-firing intent is not templated, and needing it means the job was a planner in disguise | `.torve/standing/**` `src/torve/application/standing.py` | The falsifiable test of §5.5 — the mechanism must be able to fail it, or D-19.8 was weakened rather than preserved |
| D-23.6 | `ASSUMED` | Four bounds, in order: the escalation pause suppresses evaluation entirely; per-job `cooldown_hours` and `max_open`; `loop.standing_max_per_tick` default 1; self-disable after `standing.strike_limit` consecutive non-landings | `src/torve/application/loop.py` `src/torve/config/runconfig.py` | The one leg that grows the backlog must be bounded by the same attention budget as everything else, and a predicate that fires forever must stop rather than grind |
| D-23.7 | `ASSUMED` | No enable flag: an empty `.torve/standing/` is off and deleting a file is the disable | `src/torve/config/layout.py` | RFC 0019's doctrine — scheduling the verb is the enablement — applied one level in; a second switch is a second place to look |
| D-23.8 | `ASSUMED` | Two predicate kinds ship, `command` and `path-digest`; a third arrives with a job that needs it and not before | `src/torve/application/standing.py` | Predicate kinds are a language, and a language grown speculatively is one nobody can read |
| D-23.9 | `ASSUMED` | A standing contract's body is validated by RFC 0020's contract lint, unchanged | `src/torve/application/intake.py` | A hand-written template must not be able to carry a defect a drafted contract would have been refused for |
| D-23.10 | `ASSUMED` | Each instance records its originating job, so repeated firings form one population for RFC 0022 | `src/torve/application/standing.py` `src/torve/application/telemetry.py` | Comparability is the second reason for fixed intent, and it is lost silently if the link is not recorded |
| D-23.11 | `OPEN` | Where the firing ledger lives — the host-local telemetry stream or a committed file — and whether `max_open` counts escalated instances; execution decides both and logs them | `src/torve/application/standing.py` | A cooldown that resets on a fresh clone surprises; a committed ledger makes the engine write to git on every firing. Both costs are real and neither is knowable before the first job runs for a month |

## Phasing

```yaml
- phase: 1
  title: standing-contracts-and-the-command-predicate
  intent: |
    The standing contract format under .torve/standing/, validated by the
    existing contract lint; the command predicate evaluated in a sandbox
    with no agent, exit code as the answer, any error minting nothing; the
    tick's standing leg placed inside the escalation pause and bounded by
    cooldown, max_open and loop.standing_max_per_tick; instantiation
    through the adoption path with decisions inherited only through
    inherit_decisions, each instance recording its originating job.
  scope:
    - "src/torve/application/standing.py"
    - "src/torve/application/loop.py"
    - "src/torve/config/**"
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
  title: path-digest-predicate-and-self-disable
  intent: |
    The second predicate kind and the last bound: path-digest compares a
    digest of declared paths against the digest recorded at the last
    firing, covering moved references without requiring a tool to exist;
    self-disable stops a job after strike_limit consecutive non-landings
    with an engine event naming it. The first two standing contracts land
    in this repository under the mechanism — lockfile drift and flake
    quarantine — which is what turns the exit criteria from a demo into
    use.
  scope:
    - "src/torve/application/standing.py"
    - ".torve/standing/**"
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

- A committed lockfile-drift standing contract fires exactly once against a
  real drift, mints one contract, lands it through the loop with no operator
  command, and does not fire again inside its cooldown.
- A predicate made to error produces an engine event and no task.
- A tick paused by the escalation queue evaluates no predicate, with the skip
  reason in the tick event.
- A job made to fail three times consecutively disables itself, the event
  naming the job.
- Two firings of the same job, months apart, whose contracts differ only in
  id — the comparability D-23.5 exists for, demonstrated rather than asserted.

## Amendments
