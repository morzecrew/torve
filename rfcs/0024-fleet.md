---
id: "0024"
title: Fleet — one department, many repositories
kind: design
status: accepted
implementation: none
depends_on: ["0008", "0013", "0019"]
informed_by: ["0001", "0004", "0006", "0017", "0021", "0022"]
supersedes: []
superseded_by: null
amended_by: ["A-60"]
retired: []
owner: Lev Litvinov
description: >-
  Running the standing loop over several repositories from one operator's
  attention: an operator-side manifest, a fleet-wide escalation pause, trust
  classes that bind per-repository capability, and aggregation that keeps
  every root authoritative for itself.
schema_version: 1
---

# RFC 0024 — Fleet — one department, many repositories

- **Scope:** How one operator runs the standing loop across more than one
  repository. Covers the operator-side fleet manifest, `torve fleet tick` with
  a fleet-wide attention budget, trust classes that bind the per-repository
  capabilities the corpus already grants unevenly, and read-only aggregation
  across roots. Changes nothing about what one tick does inside a root.
  Excludes multi-tenancy — permanently out of scope by charter §9 — parallel
  execution across roots, and any task that spans repositories.
- **Related:** [`0019`](0019-standing-loop.md) §5–§6 · [`0008`](0008-tracker-projection.md) §6 ·
  [`0013`](0013-configuration-layout.md) §1 · `src/torve/application/loop.py` ·
  `src/torve/config/layout.py`
- **Inherits:** D-27 from RFC 0001; D-4.8 from RFC 0004; D-6.5, D-6.8 from
  RFC 0006; D-8.1 from RFC 0008; D-13.3 from RFC 0013; D-17.10 from RFC 0017;
  D-19.1, D-19.2, D-19.5 from RFC 0019.

---

## 1. Summary

The tick is per-root and so is everything under it: the lock, the run states,
the telemetry, and — the part that matters — the attention budget. An operator
with four repositories has four independent pause thresholds and one
attention, so four unhandled escalations pause four roots and look like four
healthy systems. `torve fleet tick` is one pass over a manifest the operator
keeps: survey every root's escalation queue, decide the pause once for the
fleet, then tick each root in its own regime with that decision passed down.
The manifest also carries a trust class per repository, which is the first
mechanism that makes RFC 0017's "never combined with repositories the operator
does not trust as their own shell" a check rather than a sentence.

## 2. Motivation

Two repositories are already in dogfood — this one and the lab — ticked by
hand or by separate schedules. The design does not scale past that, and it
fails in a specific way rather than a vague one.

- **The attention budget is per-root.** D-19.5 pauses intake while a root's
  escalation queue holds `loop.pause_escalations` runs, because a queue nobody
  triages must stop the machine rather than the person. With N roots the rule
  is enforced N times against a person who exists once. Four roots each holding
  one escalation is four paused-but-otherwise-fine systems and one operator
  with four things to triage and no view that says so.
- **RFC 0006's primary alert is queue age (D-6.8), and it has no fleet form.**
  The alert fires per root, so the oldest item across the fleet is exactly the
  thing nothing reports.
- **The corpus already assumes a population of repositories trusted
  differently.** RFC 0004 §6b keys provider routing by repository name;
  RFC 0017 D-17.10 says socket mode is "never combined with repositories the
  operator does not trust as they trust their own shell". Neither has anywhere
  to write down which repository is which, so both are enforced by the
  operator remembering.
- **RFC 0008 §6 already settled "multiple repositories, one board".** The
  projection side is done. Nothing on the loop side matches it.

## 3. Current state

- `acquire_lock` in `src/torve/application/loop.py` takes `.torve/tick.lock`
  under a root; one tick at a time per root, and nothing above that.
- Run state files are JSON beside the worktree; telemetry is
  `.torve/telemetry.jsonl` per root, host-local and gitignored. There is no
  artefact anywhere that names more than one root.
- `route_provider` takes a repository name and a provider and refuses at
  dispatch. The repository name is a string in the config of the repository
  itself, which means the repository under work supplies its own routing key.
- RFC 0013's layout is deliberately per-repository, and D-13.3 states the rule
  this document needs in the other direction: the repository under work
  configures nothing about the engine that works on it.

## 4. Goals / Non-goals

**Goals**

- One attention budget across every repository the operator runs.
- One command and one schedule entry where there were N.
- A written, operator-side statement of which repositories are trusted how,
  binding the capabilities that already vary by repository.
- One place to read the fleet's escalation queue, oldest first.

**Non-goals**

- **Multi-tenancy.** Charter §9, permanently. This is one operator with
  several repositories, not several operators.
- **Parallel roots.** The fleet ticks serially. Concurrency across roots is
  RFC 0006 §4's parallelism raise — one dimension at a time, after a measured
  escalation rate — and raising two dimensions at once would make the
  measurement meaningless.
- **Cross-repository tasks.** A task belongs to exactly one root. A change
  spanning two repositories is two tasks and a human, which is what it is when
  two people do it.
- **A fleet store.** No database, no aggregated authoritative state. Roots
  stay authoritative for themselves (§5.5).

## 5. Design

### 5.1 The manifest lives with the operator

```yaml
# ~/.config/torve/fleet.yaml
repositories:
  - root: ~/work/torve
    trust: own
  - root: ~/work/lab
    trust: reviewed
attention:
  pause_escalations: 2      # across the fleet, not per root
order: manifest             # manifest | alphabetical — deterministic, never priority
```

It is the one artefact that is *about* repositories rather than in one, and
that is why it cannot live in any of them: D-13.3 says the repository under
work configures nothing about the engine, and a repository declaring its own
trust class is the same failure in its purest form. The manifest is the
operator's file, on the operator's machine, beside their other operational
configuration.

`order` is deterministic and is not a priority field. A fleet that ticks roots
in a chosen order is one config change away from being a scheduler with
opinions, which is the second planner RFC 0019 refused.

### 5.2 `torve fleet tick`

Four legs, and only the second is new behaviour:

1. **Survey.** Read each root's escalation queue — the same run records
   `torve status` reads, no new source.
2. **Decide the pause once.** If the fleet total is at or over
   `attention.pause_escalations`, every root's dispatch leg is suppressed for
   this pass. Every other leg runs everywhere: poll may apply the retry that
   clears the queue, lanes may land what is already clean, sync keeps boards
   current. This is D-19.5 lifted one level with no new semantics — the queue
   may drain by the fleet's hand and may not grow by it.
3. **Tick each root in order**, in the root's own regime and under the root's
   own lock, with the pause decision passed down. A root whose lock is held
   exits as its own recorded no-op exactly as today; the fleet continues.
4. **One fleet event**, appended to each ticked root's telemetry (there is no
   fleet stream — see §5.5), naming the fleet-wide queue total, the pause
   decision, and each root's outcome.

A root that fails hard does not stop the pass: the failure is recorded and the
next root is ticked. A fleet tick that stops at the first bad root would make
one broken repository silently halt every other, which is the failure mode this
document exists to remove, reintroduced one level up.

### 5.3 Trust classes bind capability

`trust` is not documentation. It is the gate on the capabilities the corpus
already grants per repository, checked before a root is ticked:

| Class | Permits | Requires |
| --- | --- | --- |
| `own` | `runtime.docker: socket`, any `network` | — |
| `reviewed` | no socket | an explicit provider allowlist, not the default |
| `untrusted` | no socket, no `network: host` | RFC 0021's broker in sealed mode |

A root whose own configuration asks for more than its class allows is refused
before its tick, with the class and the offending setting named. This is the
first mechanism that makes D-17.10's sentence checkable, and it is
deliberately placed where the repository cannot argue with it.

The three classes are a guess at the useful granularity and are graded
accordingly; what is not a guess is that the classification belongs to the
operator's file.

### 5.4 One board, many roots

RFC 0008 §6 already covers projection across repositories, and the sync leg is
per root and unchanged. Two additions, both small: an escalation notification
names its root, and `torve fleet status` reads every root's run records into
one table sorted by escalation age — which is RFC 0006's primary alert (D-6.8)
given its missing fleet form.

### 5.5 Aggregation is read-only, and there is no fleet store

`torve fleet status` reads; nothing writes across roots. No aggregated
database, no fleet-level state file, no cache. Each root stays authoritative
for itself, which is D-27's direction of authority applied one level up: the
fleet may read every root, and no root's truth ever lives outside it.

This is a reversibility requirement, not an aesthetic one. Charter §8a's table
says deleting Torve must leave every artefact intact, and a fleet store would
be the first thing in this corpus that holds something no single repository
holds — the exact "it would be more convenient to store this centrally" the
charter says will arrive sounding reasonable.

### Alternatives considered

- **N cron entries, one per root.** This is today. Its trade is zero new
  mechanism against the specific failure in §2: independent schedules cannot
  share an attention budget, so the safety property that makes the loop
  standing is enforced N times against one person. Kept as the fallback — a
  fleet tick is one entry replacing N, and deleting it restores N.
- **A fleet daemon.** Rejected for D-19.1's reasons, which do not change one
  level up: a resident process needs supervision, restart policy and a
  liveness story, and a dead cron entry is visible silence while a wedged
  daemon looks alive.
- **A fleet-level lock instead of per-root locks.** Its trade is simplicity
  against correctness: per-root locks already handle the overlap case
  (D-19.2), and a fleet lock would let one hung root block every other's
  reaper and lane, which is worse than the problem it solves.
- **Trust declared in each repository's own config.** Its trade is that the
  file sits next to the settings it governs, at the price of D-13.3 — a
  repository that can declare itself trusted has declared itself trusted.

## 6. Tests

Fixture roots under a temporary directory, each with seeded run records, are
enough for everything except the trust refusals, which need a config per class.
The cases that matter: a fleet whose combined queue crosses the threshold
suppresses dispatch in every root while lanes still land; a root with its lock
held is a recorded no-op and the pass continues; a root that raises does not
stop the pass; a `reviewed` root configured for socket mode is refused before
its tick with the class named; `torve fleet status` orders by escalation age
across roots.

## 7. Docs

An operations page for the manifest — where it lives, what each trust class
permits, and the one-entry crontab that replaces N. The migration note matters
more than usual and must be honest: moving from per-root schedules to a fleet
tick changes when dispatch is suppressed, and an operator who does not notice
will read the first fleet-wide pause as a bug.

## 8. Out of scope

- **Concurrent roots.** Named as the obvious extension and gated on RFC 0006
  §4's measured escalation rate, exactly as in-root parallelism was.
- **A fleet planner or a fleet corpus.** There is no such thing: the planner
  is per corpus and each repository has its own. RFC 0022's report is
  per-corpus for the same reason, and a fleet-level quality view would compare
  populations that share nothing.
- **Remote roots.** Every root is a directory on the operator's machine.
  Repositories on another host are an ssh-and-a-schedule problem, and pulling
  them in would make the fleet a control plane.
- **Per-root cadence.** One pass ticks every root. A root that wants a
  different cadence is a second fleet entry with its own manifest, which costs
  one file and no code.

## 9. Risks

- **The fleet becomes a control plane.** The named failure mode: manifests
  grow hosts, credentials, deploy targets. Mitigation is §8 and the absence of
  any write path across roots.
- **One slow root starves the rest.** Serial ticking means the last root waits
  for every root before it. Accepted at this size and visible in the fleet
  event's per-root timings; the answer if it bites is per-root cadence (§8),
  not concurrency.
- **The shared pause reads as over-caution.** One escalation in a small root
  can suppress dispatch everywhere, which will feel wrong the first time.
  It is the intended behaviour — the budget being shared is the whole point —
  and `attention.pause_escalations` is the knob that expresses how much
  simultaneous triage debt the operator will carry.
- **Trust classes read as security boundaries.** They are capability
  refusals in the operator's own tooling, not isolation. A repository in the
  `untrusted` class is still executing agents on the operator's machine; what
  the class buys is that the capability grant is enumerable and reviewed.

## 10. Unresolved questions

- Whether the fleet event belongs in every ticked root's telemetry (as §5.2
  proposes, keeping the no-fleet-store rule intact) or in an operator-side
  stream beside the manifest, which would be the first host-level record and
  the first thing a `torve fleet status` could read without walking roots.
- Whether a root's own `loop.pause_escalations` should still apply beneath the
  fleet threshold. Both applying is defensible and confusing; the fleet
  overriding is simple and silently changes a root's behaviour when it joins.
- Whether trust classes should be three, or a set of named capabilities the
  manifest grants explicitly. Three is the guess; a capability set is the
  general answer and is more machinery than two repositories justify.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-24.1 | `LOCKED` | The fleet manifest is operator-side and never lives in a repository under work | `src/torve/config/fleet.py` | D-13.3 in its purest form: a repository that can declare its own trust class has declared itself trusted |
| D-24.2 | `LOCKED` | The escalation pause is decided once for the fleet and passed down; every other leg runs in every root | `src/torve/application/fleet.py` `src/torve/application/loop.py` | D-19.5 exists because one person triages; enforcing it per root enforces it N times against a person who exists once |
| D-24.3 | `LOCKED` | No fleet store: aggregation is read-only over roots, and no root's truth ever lives outside it | `src/torve/application/fleet.py` | D-27's direction of authority one level up, and charter §8a's reversibility requirement — a fleet store would be the first artefact no repository holds |
| D-24.4 | `ASSUMED` | Roots tick serially in a deterministic manifest order; `order` is never a priority field | `src/torve/application/fleet.py` `src/torve/config/fleet.py` | Concurrency across roots is RFC 0006 §4's raise, one dimension at a time; a chosen order is a scheduler with opinions |
| D-24.5 | `ASSUMED` | A root that fails or is locked out is recorded and the pass continues | `src/torve/application/fleet.py` | Stopping at the first bad root lets one broken repository silently halt every other — the §2 failure, reintroduced above itself |
| D-24.6 | `ASSUMED` | Trust classes (`own`, `reviewed`, `untrusted`) bind per-repository capability, checked before a root is ticked; a root asking for more than its class allows is refused with the class and the setting named | `src/torve/config/fleet.py` `src/torve/application/fleet.py` | Makes D-17.10 checkable rather than remembered; three classes are a guess at granularity, the operator-side location is not |
| D-24.7 | `ASSUMED` | Per-root locks stay; there is no fleet lock | `src/torve/application/fleet.py` | D-19.2 already handles overlap, and a fleet lock would let one hung root block every other root's reaper and lane |
| D-24.8 | `ASSUMED` | `torve fleet status` orders by escalation age across roots — RFC 0006's primary alert given its fleet form | `src/torve/cli/fleet.py` | The oldest item across the fleet is exactly what per-root alerting cannot report |
| D-24.9 | `LOCKED` | A task belongs to exactly one root; no cross-repository task and no cross-root dependency exists | `src/torve/application/fleet.py` | A change spanning repositories is two tasks and a human, which is what it is when two people do it; a cross-root dependency would need a landing oracle no root owns |
| D-24.10 | `OPEN` | Whether a root's own `loop.pause_escalations` still applies beneath the fleet threshold; execution decides and logs it | `src/torve/application/loop.py` | Both applying is defensible and confusing; the fleet overriding is simple and silently changes a root's behaviour the day it joins a manifest |
| D-24.11 | `OPEN` | Where the fleet event is recorded — every ticked root's telemetry, or an operator-side stream beside the manifest | `src/torve/application/fleet.py` | Per-root keeps D-24.3 unambiguous; an operator-side stream would be the first host-level record and the first thing a status read could use without walking roots |

## Phasing

```yaml
- phase: 1
  title: fleet-tick-and-the-shared-pause
  intent: |
    The operator-side manifest and torve fleet tick: survey every root's
    escalation queue, decide the pause once for the fleet, tick each root
    in deterministic order under its own lock with the decision passed
    down, and record one fleet event. A locked-out or failing root is
    recorded and the pass continues. torve fleet status reads every root
    into one table ordered by escalation age. No writes across roots and no
    fleet store of any kind.
  scope:
    - "src/torve/application/fleet.py"
    - "src/torve/config/fleet.py"
    - "src/torve/cli/fleet.py"
    - "src/torve/cli/main.py"
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
  title: trust-classes-bind-capability
  intent: |
    Trust classes enforced against each root's own configuration before the
    root is ticked, so that the capabilities the corpus already grants
    unevenly — socket mode, host networking, provider routing breadth, and
    RFC 0021's broker mode where it exists — are refused when a repository
    asks for more than its class allows, naming the class and the setting.
    This is what turns D-17.10 from a sentence an operator remembers into a
    refusal an operator reads.
  scope:
    - "src/torve/config/fleet.py"
    - "src/torve/application/fleet.py"
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

- Two roots ticked by one scheduled entry, each landing work in its own
  regime, with the per-root schedules removed.
- An escalation in one root suppresses dispatch in both, while a clean
  candidate in the other still lands in the same pass.
- A root with its lock held is a recorded no-op and the other root ticks.
- A `reviewed` root configured for `runtime.docker: socket` is refused before
  its tick, the message naming the class and the setting.
- `torve fleet status` showing the oldest escalation across the fleet, which
  no per-root command reports.

## Amendments

### A-60 — 2026-08-30 — phase 1's scope admits the subcommand registration (amends §Phasing)

**Found in the first dispatch, three identical reds.** T-0103 went red on
the scope gate three times running on `outside allow: src/torve/cli/main.py`
— four lines registering `torve fleet` on the CLI app, which is the work,
not drift: a new verb does not exist until `main.py` mounts it. The same
mis-drawn-fence shape as A-56, caught the same way, and rfc-writer's rule
that a phase's scope is traced from what the change touches now has its
second exhibit: a phase that ships a new CLI verb always touches `main.py`.

**Changed:** phase 1's scope gains `src/torve/cli/main.py`. The minted
contracts are re-minted — a changed contract is a new task.

**Deliberately unchanged:** phase 2's scope — trust-class refusal lives in
the fleet modules and registers nothing new; and the scope gate itself,
which did exactly its job.
