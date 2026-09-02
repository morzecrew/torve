---
id: "0042"
title: The composition root and the substrate runtime
status: draft
depends_on: ["0008", "0015", "0019"]
informed_by: ["0003", "0012", "0024", "0032", "0041"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  One composition root instead of per-verb wiring, and the substrate's
  runtime machinery adopted where torve hand-rolls it — the enabling move
  for any deployment shape beyond "the operator's shell", with the
  resident-server question named and demand-gated, not smuggled.
schema_version: 1
---

# RFC 0042 — The composition root and the substrate runtime

- **Scope:** Two workstreams with one purpose — making the engine
  assemblable by something other than a CLI verb. First, a single
  composition root: the dependency wiring now duplicated across
  `src/torve/cli/run.py`, `src/torve/cli/tick.py` and
  `src/torve/cli/fleet.py` (whose `_build_deps` docstring documents the
  duplication as debt) moves to one module the verbs consume. Second,
  measured adoption of substrate machinery torve hand-rolls beside its
  own store facade: quiesce at tick shutdown, the durable recovery step,
  and — investigation-gated — forze's outbox integration under the
  tracker relay. The resident engine process (a server that owns cadence)
  is *not* built here: §5.4 names the collision with RFC 0019's
  tick-not-a-daemon doctrine and leaves the reopening to the owner as a
  graded decision. No layering changes: the composition root lives in the
  `cli` package, which already legally imports adapters (RFC 0015 §2.1).
- **Related:** RFC 0015 §2.1 (layering this deliberately does not touch),
  RFC 0019 §2 (the daemon rejection §5.4 respects), RFC 0008 (the tracker
  outbox, D-8.2), RFC 0012 (the forze pin — substrate adoption is regime
  material), RFC 0041 (the deployment shape this wiring must serve);
  `src/torve/cli/run.py`, `src/torve/cli/tick.py`,
  `src/torve/cli/fleet.py`, `src/torve/application/taskstore.py`,
  `src/torve/application/tracker.py`; installed packages
  `forze.application.execution`, `forze_kits.integrations.*`.
- **Origin:** The 2026-09-02 architecture review. The trigger sentence is
  in the tree: `_build_deps` — "the wiring a solo tick already does,
  rebuilt here because that module is not this task's to change."

---

## 1. Summary

Torve's business logic already lives behind ports in `application/`;
what the CLI hoards is the *assembly* — which adapters, wired how, per
verb, three times. This document extracts one composition root the verbs
share, then adopts the substrate machinery the assembly reveals torve is
hand-rolling: forze already ships a durable-function runner (in use), a
recovery lifecycle step, a quiesce plane, an outbox integration and a
scheduler, and torve's own `taskstore` docstring says the store is
constructed directly "until the forze runtime is adopted". Adoption here
is in-process and per-invocation — the tick stays a tick. The resident
server that would own cadence is the one piece this document refuses to
smuggle: it contradicts a reasoned LOCKED-grade doctrine (RFC 0019 §2)
and gets a named decision for the owner instead of a quiet phase.

## 2. Motivation

- **The duplication is confessed in the tree.** `run_cmd` builds broker,
  store, vcs, workspace, agents and the sizing front door inline
  (~200 lines); `tick_cmd` rebuilds the same legs for `TickDeps`;
  `fleet.py` rebuilds them a third time in `_build_deps`, with a
  docstring apologizing for it. Three copies drift three ways — and the
  untested-hotspot list is exactly these files (`cli/run.py`,
  `cli/fleet.py`, `cli/sandbox.py`, `cli/review.py`).
- **Every future deployment shape needs the same extraction.** A serve
  write path (RFC 0032 defers one), a night-window scheduler, a resident
  engine, RFC 0041's remote profile — each would today copy the wiring a
  fourth, fifth, sixth time or import a CLI verb's internals.
- **The substrate has already been adopted halfway.** `TaskStore` rides
  forze's `DurableFunctionRunner` for the attempt loop — lease
  heartbeat, fenced terminal writes, `claim_abandoned` recovery — while
  beside it torve hand-rolls what forze also ships: the tracker relay is
  a hand-built outbox keyed `(task_id, state, attempt)` (D-8.2) in a
  four-fix bug-magnet file, tick shutdown has no quiesce discipline, and
  recovery is invoked ad hoc by the reaper rather than as the substrate's
  lifecycle step. Half-adoption pays integration cost without collecting
  the machinery.

## 3. Current state

Verified against the tree and installed packages at drafting time:

- `forze` 's installed surface: `DurableFunctionRunner` (in use via
  `taskstore.py`), `DurableScheduler` with cron ids,
  `durable_recovery_background_lifecycle_step`,
  `forze_kits.integrations.quiesce` (`QuiescePlane`, `QuiesceReport`),
  `forze_kits.integrations.outbox`, consumer lifecycles, `stored_file`,
  secrets, realtime. Torve imports the durable slice only.
- `taskstore.py`'s docstring: torve constructs the store directly
  "until the forze runtime is adopted" — the adoption this document
  scopes was anticipated by the integration's author.
- Layering (RFC 0015 §2.1, enforced by `lint-imports`): `application`
  may not import `adapters`; `cli` may. A composition root therefore
  lives in `cli` with zero layering change — the point is one builder,
  not a new layer.
- The tracker outbox is hand-rolled in
  `src/torve/application/tracker.py` (D-8.2), 4 bug fixes in 90 days.
  Whether forze's outbox contract fits it is *unverified* — graded
  accordingly (D-42.5).
- RFC 0019 §2 rejected the resident daemon with reasons that still
  hold: the store is the authority, every invocation is inspectable, a
  dead cron entry is visible as silence where a wedged daemon is not.
  `torve serve` is already resident but read-only by its own LOCKED
  scope (RFC 0032).

## 4. Goals / Non-goals

**Goals**

- One place that knows how to turn `(root, config)` into runnable deps;
  verbs shrink to argument parsing, front-door checks and rendering.
- The wiring becomes testable once, retiring the untested-hotspot
  cluster's common cause.
- Substrate machinery replaces hand-rolled equivalents only where the
  fit is verified, behind the same ports, with conformance unchanged.
- The resident-server question is on the record with its doctrine
  collision, so reopening it is one decision, not an archaeology dig.

**Non-goals**

- No new layer and no `lint-imports` change — assembly lives where
  adapter imports are already legal.
- No behaviour change to any verb — this is `safe-refactor` material:
  same deps, built in one place.
- No scheduler adoption — cadence stays the environment's (RFC 0019);
  a night window is cron plus a config today, and internalizing it is
  exactly the §5.4 decision, not a rider.
- No multi-host anything — RFC 0041 D-41.1 stands.

## 5. Design

### 5.1 The composition root

`src/torve/cli/assembly.py`: builders that close over `(root, config)`
and return the dep bundles the application layer already defines —
`RunDeps`, `TickDeps`, intake and review legs, the store/broker/vcs/
workspace/runtime constructors behind them. The existing bundles stay
the contract; assembly is the one producer. `run_cmd`, `tick_cmd` and
the fleet's per-root loop consume it; `_build_deps` and its docstring
apology are deleted. Front-door policy (role dispatchability, sizing
refusal, oversize override) stays in the verbs — it is per-verb
behaviour, not wiring.

### 5.2 Quiesce and recovery as substrate steps

Tick shutdown runs the quiesce plane over what the tick started, and
the recovery invocation the reaper performs ad hoc becomes the
substrate's recovery step invoked at tick start — same semantics,
substrate-owned edge cases (the pre-death cancel landing without
invoking the body is already forze's, per the taskstore docstring).
Both are in-process and per-invocation: nothing becomes resident.

### 5.3 The tracker outbox, investigated

One timeboxed investigation task: map D-8.2's staging semantics
(keyed effects, at-least-once relay, the poll/apply command loop) onto
`forze_kits.integrations.outbox`. If the fit is real, a migration
phase replaces the hand-rolled staging under the same `Tracker` port
and the same tests; if it is not, the finding lands in the task log
and the hand-rolled outbox stays with a decision row recording why.
The bug-magnet grade of `tracker.py` is the argument for trying and
the argument for not forcing it.

### 5.4 The resident engine, named and not built

A server that owns cadence (forze's `DurableScheduler` under a
long-lived process, the natural home for provider off-peak windows and
RFC 0032's deferred write path) collides with RFC 0019 §2's reasoned
rejection of the daemon. This document takes no side: D-42.6 records
the question, its entry condition (an amendment to RFC 0019, the
owner's), and what becomes possible on each answer. Until then the
composition root is deliberately shaped so a resident entrypoint would
be one new consumer, not a rewiring.

### Alternatives considered

- **Assembly in `application/`** — rejected: it would need adapter
  imports, which RFC 0015 §2.1 forbids there; relaxing layering to
  save one import line inverts the priorities.
- **A plugin/registry DI container** — rejected: three verbs and one
  future entrypoint do not justify indirection; plain builder
  functions are greppable and typed.
- **Adopting the scheduler now, cron-compatibly** — rejected: it
  silently decides §5.4's question, and cron already delivers cadence
  including windows; the adoption would be motion without need.

## 6. Tests

Assembly: builder tests asserting each bundle's composition against a
fixture config — the tests the three copies never had; verb tests
assert behaviour unchanged (same deps observable through the fake
agent scenario path). Quiesce/recovery: tick tests pin shutdown
draining and start-time recovery equivalence with today's reaper-driven
path. Outbox: the existing tracker suite is the acceptance bar for any
migration — it passes unchanged or the migration does not land.

## 7. Docs

None user-facing for the extraction (behaviour-preserving). The
substrate-adoption phase notes which forze integrations are in service
— the forze pin's regime meaning (RFC 0012, A-6) already covers the
upgrade story. D-42.6's question is documented in this file alone
until the owner answers it.

## 8. Out of scope

- The resident engine profile and any scheduler adoption — gated on
  D-42.6's owner decision amending RFC 0019.
- The serve write path — RFC 0032 excluded it; it becomes cheap after
  §5.1 but remains its own document with its own security posture.
- forze `stored_file` for artifact transport — relevant only to
  multi-host futures; inspect before inventing, when that document
  exists.
- Broader forze surface (sagas, realtime, secrets, consumers) — no
  present hand-rolled counterpart; adopting machinery without a
  displaced equivalent is how frameworks colonize codebases.

## 9. Risks

- **A behaviour-preserving refactor of untested hotspots.** The risk
  the extraction exists to retire is also its hazard. Mitigated by
  sequencing: assembly tests land with the extraction, verbs keep
  their end-to-end scenario tests, and the diff per verb is
  deletion-shaped.
- **The outbox migration destabilizes a bug magnet.** Mitigated by
  the investigation gate and the unchanged-suite acceptance bar; the
  no-go outcome is a legitimate, recorded result.
- **Substrate adoption reads as pressure to answer §5.4.** Countered
  in the text: every adoption here is per-invocation; the decision row
  exists precisely so the resident question is answered once, in the
  open.

## 10. Unresolved questions

- D-42.6 — the resident engine (owner, via RFC 0019 amendment or its
  refusal).
- Whether the fleet's per-root loop consumes assembly directly or
  through a thinner per-root handle — implementation decides against
  the actual shape of `TickDeps` reuse across roots, and logs.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-42.1 | `LOCKED` | One composition root in `src/torve/cli/assembly.py` produces every dep bundle; verbs consume it and no verb builds adapters inline again | `src/torve/cli/assembly.py` `src/torve/cli/run.py` `src/torve/cli/tick.py` `src/torve/cli/fleet.py` | A fourth copy of the wiring is a review reject; every future entrypoint (serve write path, resident engine, remote profile) starts as one new consumer |
| D-42.2 | `LOCKED` | The extraction is behaviour-preserving and layering-preserving: same bundles, same ports, no `lint-imports` change, front-door policy stays in the verbs | `src/torve/cli/assembly.py` | RFC 0015 §2.1 stands untouched; anyone needing assembly below `cli` is asking the §5.4 question, not a wiring question |
| D-42.3 | `ASSUMED` | Tick shutdown runs forze's quiesce plane over what the tick started; recovery becomes the substrate's step at tick start, replacing the ad hoc invocation with identical semantics | `src/torve/application/loop.py` `src/torve/application/taskstore.py` | — |
| D-42.4 | `ASSUMED` | Substrate machinery is adopted only where it displaces a hand-rolled equivalent behind an existing port; adoption without displacement is refused | `src/torve/application/taskstore.py` | — |
| D-42.5 | `OPEN` | Whether forze's outbox integration fits D-8.2's staging semantics — settled by a timeboxed investigation task; fit → migration under the unchanged tracker suite, no fit → the finding and the hand-rolled outbox stay, recorded | `src/torve/application/tracker.py` | — |
| D-42.6 | `OPEN` | The resident engine (a long-lived process owning cadence via the substrate scheduler — the home for off-peak windows and the serve write path) contradicts RFC 0019 §2; building it requires the owner amending that doctrine first. This row is the reopening's address | `src/torve/application/loop.py` | Until answered, cadence stays external (cron), and nothing in this document's phases may start a resident process |

## 12. Phasing

Phase 2 waits on phase 1 because quiesce wiring rides the extracted
assembly. Phase 3 is investigation-gated by its own decision.

```yaml
- phase: 1
  title: the composition root
  intent: >-
    src/torve/cli/assembly.py: builders producing RunDeps, TickDeps
    and the intake/review legs from (root, config); run_cmd, tick_cmd
    and the fleet loop consume them; fleet._build_deps is deleted
    (D-42.1). Behaviour-preserving and layering-preserving (D-42.2):
    front-door checks stay in the verbs, bundles and ports unchanged.
    Assembly builder tests land with the extraction; verb scenario
    tests pin behaviour unchanged.
  scope:
    - src/torve/cli/assembly.py
    - src/torve/cli/run.py
    - src/torve/cli/tick.py
    - src/torve/cli/fleet.py
    - tests/test_cli.py
    - tests/test_fleet_cli.py
    - tests/test_run_loop.py
  acceptance:
    - uv run pytest tests/test_cli.py tests/test_fleet_cli.py tests/test_run_loop.py
    - uv run lint-imports
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
  depends_on: []
- phase: 2
  title: quiesce and recovery as substrate steps
  intent: >-
    Tick shutdown drains through forze's quiesce plane; the recovery
    invocation moves from the reaper's ad hoc call to the substrate's
    recovery step at tick start with identical semantics (D-42.3,
    D-42.4). Per-invocation only — nothing resident (D-42.6 stands).
    Tick tests pin drain-on-shutdown and recovery equivalence against
    the current reaper-driven baseline.
  scope:
    - src/torve/application/loop.py
    - src/torve/application/taskstore.py
    - src/torve/application/reaper.py
    - tests/test_tick.py
    - tests/test_reaper.py
    - tests/test_taskstore.py
  acceptance:
    - uv run pytest tests/test_tick.py tests/test_reaper.py tests/test_taskstore.py
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
  depends_on: [1]
- phase: 3
  title: the tracker outbox investigation
  intent: >-
    The timeboxed D-42.5 investigation: map D-8.2's staged-effect
    semantics onto forze_kits.integrations.outbox and record the
    verdict in the task log. On fit, the follow-up migration replaces
    the hand-rolled staging under the same Tracker port with the
    tracker suite passing unchanged as the acceptance bar; on no-fit,
    the hand-rolled outbox stays and the decision row gains the
    finding. This phase is the investigation; the migration mints only
    from its verdict.
  scope:
    - src/torve/application/tracker.py
    - tests/test_tracker.py
    - tests/test_outbox.py
  acceptance:
    - uv run pytest tests/test_tracker.py tests/test_outbox.py
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
  depends_on: [1]
```
