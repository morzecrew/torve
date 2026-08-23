---
id: "0019"
title: The standing loop
kind: design
status: draft
implementation: none
depends_on: ["0003", "0006", "0008"]
informed_by: ["0005", "0007", "0017"]
supersedes: []
superseded_by: null
amended_by: []
retired: []
owner: Lev Litvinov
description: >-
  The bounded tick that makes the team standing: drain one queued task,
  process the lane under its existing approval switch, project the board,
  and stop — cadence delivered by the environment, never a resident
  daemon. Intake pauses while the escalation queue is non-empty, because
  a queue nobody triages must stop the machine, not the person.
schema_version: 1
---

# RFC 0019 — The standing loop

- **Scope:** How the engine runs without a human turning the crank: the
  tick verb, what one tick does and in what order, how the next task is
  selected, when intake pauses, when the lane may land, concurrency and
  reentry, and what a tick records. Excludes parallel execution (gated by
  RFC 0006's D-6.5 and untouched here), planning (RFC 0007 mints the
  work this loop drains), and any change to what a single run does.
- **Inherits:** D-1, D-2, D-6 from RFC 0001; D-3.1, D-3.4 from RFC 0003;
  D-6.2, D-6.5, D-6.8, D-6.9 from RFC 0006; D-8.1 from RFC 0008.

---

## 1. The condition in D-3.1 is met

RFC 0003 locked v1 to one task, synchronous, no daemon, with its own exit
clause: *parallelism only after the single path is boring*. The single
path is now boring in the only sense that matters — a task travels
sandbox → gates → review → provenance → pull request → CI-gated lane →
tracker → notifier with no step requiring improvisation, demonstrated
live end to end. What remains starved is everything that needs volume:
RFC 0006 §7 wants ten landings and two weeks of resolution times, RFC
0009 §5 wants replay volume, RFC 0005 §7 wants a shadow period. All of
them wait on the same bottleneck, and it is not the engine — it is that
nothing happens between operator invocations.

This RFC does **not** cash in the parallelism clause. It cashes in the
smaller thing the clause implies must come first: the engine keeps
working — one task at a time, exactly as today — when nobody is typing.
D-3.1's row stands unamended; a tick is one synchronous invocation of
the machinery that already exists.

## 2. A tick, not a daemon

The loop is a bounded verb: `torve tick` performs one pass and exits.
Cadence belongs to the environment — cron, a CI schedule, a systemd
timer — exactly as RFC 0005's pull-request trigger settled for events:
the engine holds no resident consumer; the environment delivers.

The daemon was considered and rejected. A resident process needs
supervision, restart policy, log rotation, and a liveness story — a
second operational surface for a system whose whole posture is "the
store is the authority and every invocation is inspectable." A dead cron
entry is visible as silence in the telemetry (§8); a wedged daemon is a
zombie that looks alive. The scheduler the operating system already
ships is better tested than any loop this repository would write.

## 3. What one tick does

Fixed order, every tick, each leg skippable only by configuration or by
having nothing to do:

1. **Reap** — collect terminal footprints first, so the tick starts from
   a true picture of what is running and the lane sees no corpses.
2. **Poll** — read the board's commands and apply them as intents.
   Human words land before machine work: a `retry` posted overnight must
   re-queue before this tick's dispatch selects, and an operator's
   refusal-answer belongs on the thread before anything else moves.
3. **Dispatch** — select at most **one** queued task (§4) and run it to
   its terminal state, synchronously, through the existing runner —
   review minting, gates, provenance and the pull request included.
4. **Lane** — process ready candidates, only under the approval switch
   (§5b).
5. **Sync** — project run state onto the tracker and relay, notifier
   effects included. The board reflects the tick's end state, not its
   middle.

A tick with nothing to do at any leg does nothing there and says so
(§8). The order is a decision (D-19.3), not an accident: reap before
dispatch keeps the scope-overlap check honest; poll before dispatch lets
a human's overnight intent win the slot; sync last makes the board a
postcondition.

## 4. What "queued" means

The loop drains work; it never creates it (D-19.8). A task is queued
when all of the following hold, readable from the file system alone:

- a contract exists under the tasks directory with role `implement` or
  `revert` — reviews stay runner-minted (RFC 0005 D-5.11), and the
  planner tier's contracts are not executable work;
- no run state exists for it — a task that ran and reached any state,
  terminal or not, is not re-dispatched by the loop; re-entry into the
  queue is a human act (`retry` via the board, or a re-mint);
- every `depends_on` entry has landed or is `ready` — the same rule
  dispatch already enforces, checked here so the loop does not burn its
  one slot on a refusal it can predict.

Selection among queued tasks is by ascending task id — deterministic,
arguable from a directory listing, and free of a priority field that
would become a second planner. One dispatch per tick is a bound, not a
tuning knob: spend per unit time is then the cadence times one task
budget, and the operator sets cadence where they set every other
schedule.

## 5. Two budgets the loop must respect

**a. Attention (D-19.5).** RFC 0006 named escalation queue age the
primary alert because a queue nobody triages looks identical to success
from inside the runner. A standing loop makes that failure mode worse:
it can manufacture escalations faster than a person clears them. So
intake pauses — dispatch skips, with a recorded reason — while the
escalation queue holds at least `loop.pause_escalations` runs
(default 1). Every other leg keeps running: poll may apply the `retry`
that clears the queue, the lane may land what is already clean, sync
keeps the board and the notifier current. The queue may drain during a
pause; it may not grow by the loop's own hand.

**b. Approval (D-19.6).** In the local regime the operator invoking
`torve merge` is the recorded approval. A scheduler invoking the lane is
nobody's approval — so the tick's lane leg runs only when
`promotion.auto_merge` is true. That switch has existed since RFC 0006
precisely for this moment (D-6.2: off by default, opt-in per repository
and task class); the loop adds no second knob and grants itself nothing.
With auto-merge off, ready candidates accumulate, the board shows them,
and landing stays a human act.

## 6. Concurrency and reentry

One tick at a time per root (D-19.2): the tick takes a lock file under
the engine's state directory, and an invocation that finds it held exits
as a clean no-op — recorded, exit code success, nothing done. Cron
overlap is a certainty over enough weeks; it must be boring.

A stale lock — holder dead, age past the configured tick budget — is
broken loudly: the takeover is an engine event naming the stale holder,
never a silent steal. Below the lock, RFC 0006's D-6.9 still stands:
dispatch keys durable runs by task and generation, so even a broken
double-fire converges on a single claim. The lock is the mechanism;
convergence is the backstop.

Crash mid-tick needs no recovery protocol of its own: every leg is the
existing machinery with its existing crash story — the reaper collects,
the store's leases expire, the outbox redelivers, the next tick starts
from the file system's truth.

## 7. Configuration

```yaml
loop:
  pause_escalations: 1   # intake pauses while the queue holds this many
  tick_budget: 3600      # seconds; a lock older than this is stale
```

Nothing else. There is no `enabled` flag — scheduling the verb is the
enablement, exactly as not scheduling it is the off switch. Dispatch
count per tick is doctrine (one), not configuration; cadence lives in
the scheduler's file, reviewed like any other operational change.

## 8. Observability

Every tick appends one engine event: what each leg did, what it skipped
and why — `paused: escalation queue at 2`, `lane: auto_merge off,
3 ready` — and `noop: true` when the whole pass moved nothing. Health
questions then have telemetry answers: a healthy idle system shows a
heartbeat of honest noops; a dead scheduler shows silence; a stalled
intake shows the pause reason repeating. The distinction between quiet
and dead is the entire point of recording noops (D-19.7).

`torve status` gains nothing new: the loop has no state of its own to
show — the store already is the state, which is what "the tick never
creates work" buys.

## 9. Risks

- **Runaway spend.** Bounded by one dispatch per tick times cadence,
  each run under its existing task budget; the pause threshold stops
  intake at the first unhandled failure by default. The loop cannot
  spend faster than the scheduler fires it.
- **Approval dilution.** The lane leg is inert without the existing
  opt-in (D-19.6); nothing else in a tick lands anything.
- **Silent stall.** Mitigated by noop heartbeats and the pause reason in
  every event; the operator's alert remains queue age (D-6.8), now with
  a mechanical response attached.
- **Forge rate limits.** A tick's forge traffic is bounded (one run's
  pushes, one sync, one poll); cadence is the throttle. Not designed for
  further here — the failure is loud (adapter errors), and tuning
  belongs to operations.
- **The loop as planner.** The standing failure mode of agent systems —
  the machine inventing its own backlog. Structurally excluded: the
  tick reads contracts and commands, both minted by humans or by the
  planner a human invoked (D-19.8).

## 10. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-19.1 | `LOCKED` | The standing loop is a bounded tick (`torve tick`), never a resident daemon; cadence is delivered by the environment | `src/torve/application/loop.py` `src/torve/cli/tick.py` | One operational surface; a dead scheduler is visible silence, a daemon is a zombie that looks alive |
| D-19.2 | `LOCKED` | One tick at a time per root, held by a lock file; an overlapping fire exits as a recorded no-op; a stale lock is broken loudly, and D-6.9's converging dispatch remains the backstop below it | `src/torve/application/loop.py` | Cron overlap is a certainty; it must be boring |
| D-19.3 | `ASSUMED` | Tick order is fixed: reap, poll, dispatch, lane, sync | `src/torve/application/loop.py` | Human intents precede machine work; the board is a postcondition of the tick, not a snapshot of its middle |
| D-19.4 | `ASSUMED` | Queued = contract with executable role, no run state, dependencies satisfied; selection by ascending id; at most one dispatch per tick, as doctrine not configuration | `src/torve/application/loop.py` | Deterministic from the file system alone; spend is bounded by cadence; a priority field would be a second planner |
| D-19.5 | `LOCKED` | Intake pauses while the escalation queue holds `loop.pause_escalations` runs (default 1); every other leg keeps running — the queue may drain, not grow, by the loop's hand | `src/torve/application/loop.py` `src/torve/config/runconfig.py` | D-6.8 made mechanical: a queue nobody triages stops the machine, not the person |
| D-19.6 | `LOCKED` | The tick's lane leg runs only under `promotion.auto_merge`; otherwise ready candidates accumulate for the operator | `src/torve/application/loop.py` | A scheduler is nobody's approval; D-6.2's opt-in is exactly this switch and no second knob is added |
| D-19.7 | `ASSUMED` | Every tick appends one engine event recording each leg's action or skip reason, `noop: true` when nothing moved | `src/torve/application/loop.py` `src/torve/application/telemetry.py` | Quiet and dead must be distinguishable from telemetry alone |
| D-19.8 | `LOCKED` | The tick never creates work: it drains contracts and commands minted by humans or by the human-invoked planner | `src/torve/application/loop.py` | The machine must not invent its own backlog; growth stays a human act |

## Phasing

```yaml
- phase: 1
  title: The tick
  intent: |
    torve tick as one bounded pass over existing machinery: reap, poll,
    dispatch of at most one queued task selected by the file-system rule,
    the lane under promotion.auto_merge, tracker sync last. The tick
    lock with loud stale-break, the pause threshold on the escalation
    queue, the loop configuration block, and the per-tick engine event
    with honest noops. No new run semantics anywhere — every leg is a
    call into what already exists.
  scope:
    - "src/torve/application/**"
    - "src/torve/cli/**"
    - "src/torve/config/**"
    - "tests/**"
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
    - "uv run lint-imports"
    - "uv run torve rfc check"
```

## 11. Exit criteria

- A seeded backlog of at least three contracts drains on a schedule with
  no operator commands between them, each landing carrying provenance
  indistinguishable from a hand-cranked run's.
- An escalation pauses intake within one tick; clearing it through the
  board resumes intake without any restart or reconfiguration.
- Two deliberately overlapping fires produce exactly one tick and one
  recorded no-op.
- With auto-merge off, a scheduled tick leaves a ready candidate
  unlanded and visible on the board.
- A week of tick events in which noops, pauses and work are
  distinguishable by query alone — and RFC 0006 §7's landing count
  measurably advanced by loop-landed tasks.

## Amendments

*(none yet)*
