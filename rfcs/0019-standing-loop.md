---
id: "0019"
title: The standing loop
kind: design
status: accepted
implementation: partial
depends_on: ["0003", "0006", "0008"]
informed_by: ["0005", "0007", "0017"]
supersedes: []
superseded_by: null
amended_by: ["A-27", "A-28", "A-29"]
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

- **Implementation state:** phase 1 executed 2026-08-24 (T-0055 — `torve tick` with the lock, the pause threshold, the selection rule and per-tick events); A-27 executed 2026-08-24 (T-0056 — the lane precedes the reaper); A-28 executed 2026-08-24 (T-0057 — the loop publishes what it lands, the reaper keeps unlanded READY states, the lane adopts identical untracked records; demonstrated live: a two-task backlog drained in four ticks with zero operator intervention — dispatch, CI-gated landing, automatic base push, dependency on the published landing, honest closing noop). Outstanding: the §11 exit criteria accrue with scheduled operation (the week of events, the overlap in the wild, 0006 §7's count)
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

1. **Poll** — read the board's commands and apply them as intents.
   Human words land before machine work: a `retry` posted overnight must
   re-queue before this tick's dispatch selects, and an operator's
   refusal-answer belongs on the thread before anything else moves.
2. **Lane** — process ready candidates, only under the approval switch
   (§5b), and **before the reaper runs**: `ready` is terminal to the
   engine and therefore sweepable, so a reap ahead of the lane destroys
   the lane's own input. This is A-26's merge-before-reap ordering,
   applied inside the tick.
3. **Reap** — collect terminal footprints before dispatch, so the
   scope-overlap check sees a corpse-free picture and the new run
   claims cleanly.
4. **Dispatch** — select at most **one** queued task (§4) and run it to
   its terminal state, synchronously, through the existing runner —
   review minting, gates, provenance and the pull request included. A
   candidate this leg produces lands next tick: its CI could not be
   green mid-tick anyway.
5. **Sync** — project run state onto the tracker and relay, notifier
   effects included. The board reflects the tick's end state, not its
   middle.

A tick with nothing to do at any leg does nothing there and says so
(§8). The order is a decision (D-19.3), not an accident: lane before
reap keeps the lane's input alive; reap before dispatch keeps the
scope-overlap check honest; poll first lets a human's overnight intent
win the slot; sync last makes the board a postcondition.

*Amendment note (A-27, 2026-08-24):* the order as accepted was reap,
poll, dispatch, lane, sync — reap first "so the tick starts from a true
picture". The second live tick disproved it: the reaper swept a READY
candidate's state between the dispatch that produced it and the lane
that would have landed it, exactly the failure A-26's merge-before-reap
rule already named. The list above is the amended order.

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

*Execution note 2026-08-24 (T-0055):* "no run state" is implemented as
"no run *record*" — neither a state file nor a telemetry record naming
the task — because the reaper removes state files, and the literal
reading would re-dispatch the entire reaped history on the first tick
after a sweep. This section's own prose ("a task that ran and reached
any state, terminal or not, is not re-dispatched") is the intent;
telemetry is append-only, survives the reaper, and keeps the rule
readable from the file system alone.

*Amendment note 2026-08-24 (A-29, executed as T-0064):* the record the
previous note settled on is still host truth — state files and telemetry
are unversioned, so a fresh clone holds neither and sees the entire
landed history as queued. Selection therefore asks the repository before
it trusts the host: a task whose landing trailer is already in base
history is never queued, whatever run records the host holds. The oracle
is the same one the reaper (D-19.10) and the dependency check already
consult; it is asked only of tasks the host records would otherwise
select, so the common tick still reads local files alone.

*Execution note 2026-08-24 (T-0059):* the re-entry this section names is
now mechanical end to end: a `QUEUED` run state — the state a board
`retry` leaves behind — is queued, dependencies still checked, and the
retry's apply deletes the task's stale remote branch first (RFC 0008
D-8.10) so the re-run's push is not refused by the previous attempt's
history. Any other existing state remains untouchable by the loop.

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
| D-19.3 | `ASSUMED` | Tick order is fixed: poll, lane, reap, dispatch, sync. *Amended by A-27 2026-08-24 — as accepted the order was reap-first, and the second live tick showed the reaper sweeping the lane's READY input; A-26's merge-before-reap applies inside the tick* | `src/torve/application/loop.py` | Human intents precede machine work; the lane's input outlives the reaper; the board is a postcondition of the tick, not a snapshot of its middle |
| D-19.4 | `ASSUMED` | Queued = contract with executable role, no run state, dependencies satisfied; selection by ascending id; at most one dispatch per tick, as doctrine not configuration. *Amended by A-29 2026-08-24: a task whose landing trailer is already in base history is never queued, whatever run records the host holds — landings are repo truth, run records are host truth, and a fresh clone must not re-run what the repository already knows* | `src/torve/application/loop.py` | Deterministic from the file system alone; spend is bounded by cadence; a priority field would be a second planner |
| D-19.5 | `LOCKED` | Intake pauses while the escalation queue holds `loop.pause_escalations` runs (default 1); every other leg keeps running — the queue may drain, not grow, by the loop's hand | `src/torve/application/loop.py` `src/torve/config/runconfig.py` | D-6.8 made mechanical: a queue nobody triages stops the machine, not the person |
| D-19.6 | `LOCKED` | The tick's lane leg runs only under `promotion.auto_merge`; otherwise ready candidates accumulate for the operator | `src/torve/application/loop.py` | A scheduler is nobody's approval; D-6.2's opt-in is exactly this switch and no second knob is added |
| D-19.7 | `ASSUMED` | Every tick appends one engine event recording each leg's action or skip reason, `noop: true` when nothing moved | `src/torve/application/loop.py` `src/torve/application/telemetry.py` | Quiet and dead must be distinguishable from telemetry alone |
| D-19.8 | `LOCKED` | The tick never creates work: it drains contracts and commands minted by humans or by the human-invoked planner | `src/torve/application/loop.py` | The machine must not invent its own backlog; growth stays a human act |
| D-19.9 | `LOCKED` | The loop publishes what it lands: after at least one landing, the tick's lane leg pushes the base branch to origin, fast-forward only — a refused push is a loud leg error, and no force path exists (D-10.5 untouched). Added by amendment A-28 2026-08-24 | `src/torve/cli/tick.py` `src/torve/adapters/vcs/git.py` | Unpushed landings leave origin stale, and every later dispatch bases on the stale origin and conflicts systematically |
| D-19.10 | `ASSUMED` | The reaper keeps the state file of a READY implement or revert run whose task has not landed on the base; its worktree stays disposable, and review-role READY states remain sweepable. Added by amendment A-28 2026-08-24, amending RFC 0003 D-3.23's sweep scope | `src/torve/application/reaper.py` | READY is terminal to the engine but it is the lane's input; sweeping it before the landing loses the candidate across ticks |
| D-19.11 | `ASSUMED` | The lane adopts byte-identical untracked engine-record files the landing branch carries — the root copy is removed before the fast-forward; any difference in content still refuses. Added by amendment A-28 2026-08-24 | `src/torve/application/lane.py` `src/torve/adapters/vcs/git.py` | The runner's worktree commit carries the task's own contract; an untracked identical copy in the root must not block the landing it belongs to |

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

### A-27 — 2026-08-24 — the lane precedes the reaper inside the tick (amends §3, D-19.3)

**Found in implementation** — by the second live tick. Tick 1 dispatched
a task to `ready` (its lane leg refused on unrelated content dirt); tick
2 opened with the reaper, which collected the READY state and worktree —
`ready` is terminal to the engine and sweepable by design — so the
dispatch leg found nothing queued (the telemetry record correctly blocks
re-dispatch) and the lane found no candidates. The candidate evaporated
between two legs of one tick, with its work stranded on its branch and
pull request.

**Changed:** the tick order is poll → lane → reap → dispatch → sync.
The lane moves ahead of the reaper — A-26's merge-before-reap ordering,
which the corpus had already established for exactly this reason,
applied inside the tick. Reap keeps its place ahead of dispatch, so the
scope-overlap check still sees a corpse-free picture. A candidate
produced by this tick's dispatch lands next tick; its CI could not be
green mid-tick anyway.

**Deliberately unchanged:** poll stays first (human intents precede
machine work) and sync stays last (the board is a postcondition); the
reaper itself is untouched — READY remains sweepable, because the lane
now runs while the state is still there to land.

**Recovery for the swept candidate:** the work was never lost — branch
and pull request survive the state file. Recreating the READY run state
is explicit operator surgery, recorded as such, and the next tick lands
it through the normal path.

### A-28 — 2026-08-24 — the loop publishes, the reaper waits, the lane adopts (adds D-19.9–D-19.11; also edits RFC 0003 D-3.23)

**Found in implementation** — by the first live drain, three defects
with one theme: the scheduled regime removes the operator whose habits
papered over the gaps.

1. **Unpushed landings.** The lane lands on the local base and nothing
   pushed it; dispatch bases worktrees on `origin/main`, which went
   stale, so the next task was implemented against a base missing the
   previous landing and conflicted on rebase — systematically, not by
   bad luck. In the manual regime the operator pushed after
   `torve merge`; the loop has no operator. **Changed:** D-19.9 — after
   at least one landing the tick's lane leg pushes the base,
   fast-forward only, and a refused push is a loud leg error.
2. **The reaper still ate unlanded candidates.** A-27 protects a
   candidate the lane lands in the same tick; one the lane *refused*
   (pending CI, dirt, a transient error) was swept by that same tick's
   reap, because READY is terminal to the engine and sweepable.
   **Changed:** D-19.10 — the reaper keeps the *state file* of a READY
   implement or revert run whose task has not landed on the base; the
   worktree stays disposable (its work lives on the branch), and
   review-role READY states remain sweepable. This narrows RFC 0003
   D-3.23's sweep scope, noted on that row.
3. **The landing collided with its own contract.** The runner's
   provenance commit carries the task's contract inside the worktree;
   the root held the same file untracked, and git refused the
   fast-forward rather than overwrite. **Changed:** D-19.11 — the lane
   removes an untracked root file the incoming landing carries with
   byte-identical content; any difference still refuses. Committing the
   contract from the runner was rejected again for the same reason as
   in T-0052: the engine does not commit into the operator's checkout.

**Deliberately unchanged:** D-10.5 — no force path exists anywhere,
including the new base push; the tick order (A-27) stands; the reaper's
treatment of escalated runs (keep everything for triage) stands.

### A-29 — 2026-08-24 — landings are repo truth, run records are host truth (amends §4, D-19.4)

**Found in implementation** — by the very first tick under the installed
schedule, which ran against a fresh clone of the lab. The T-0055
execution note had already refined "no run state" to "no run record"
because the reaper removes state files; but state files and telemetry
are both host-local and unversioned, so a new checkout holds neither.
That first tick saw the entire landed history — some thirty tasks — as
queued and re-dispatched the oldest. The effect was contained by the
regime's own layers (the agent found the work already on main and
produced an empty candidate; approvals would have refused any landing),
but the failure repeats one wasted dispatch per tick, on every fresh
clone, forever: spend and board noise with no bound.

**Changed:** §4 gains a fourth condition and D-19.4 records it —
selection asks the repository before it trusts the host. A task whose
landing trailer is already in base history is never queued, whatever run
records the host holds; the oracle is the one the reaper (D-19.10) and
the dependency check already consult. Run records still decide
everything short of landing — an attempt that escalated on one host is
that host's business to triage — but a landing is repo truth and
travels with every clone.

**Deliberately unchanged:** the T-0055 refinement stands (telemetry
still guards the reaped-but-unlanded history on the host that ran it);
QUEUED re-entry (T-0059) stands, but the repo check is authoritative
over it — a landed task's re-entry is a revert and a new contract,
never a re-run; and the tick still creates nothing (D-19.8).
