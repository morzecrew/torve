---
id: "0052"
title: The lane as a manager leg
status: accepted
implementation: none
depends_on: ["0006", "0044"]
informed_by: ["0019", "0051"]
supersedes: []
superseded_by: null
amended_by: ["A-130"]
owner: misery7100
description: >-
  Restoring the landing half of an unattended session: the serialized lane becomes a leg of the manager's pass under an opt-in switch, and a conflict disposes of itself the way the retired loop's did.
schema_version: 1
---

# RFC 0052 — The lane as a manager leg

- **Scope:** The landing half of an unattended run. Covers the merge lane as
  a leg of the manager's pass, the switch that arms it, and the automatic
  conflict disposal that the retired standing loop performed. It does **not**
  restore the post-landing publish — base push, branch republish, pull-request
  close-out — which left with the same loop and is named in §8 with what it
  would cost.
- **Related:** RFC 0006 (the lane, and D-6.2 / D-6.12 / D-6.13, whose
  implementation this restores — A-115), RFC 0019 A-105 (what the loop took
  with it), RFC 0044 D-44.5 (the pass this becomes a leg of), RFC 0051
  (the leg pattern this follows), `src/torve/application/lane.py`,
  `src/torve/application/residency.py`.
- **Origin:** Measured, 2026-09-06: the v2 manager has dispatched five
  attempts across one task, and produces candidates it cannot land.

---

## 1. Summary

A manager pass runs a task to `ready` and stops. Landing is `torve merge`,
which a person types. So an unattended session produces a pile of candidates
and lands none of them, which makes it a session nobody can leave.

This makes the lane a leg of the pass, under a switch that is off by default —
restoring D-6.2's opt-in — and gives a conflict the disposal the retired loop
performed: capture what the attempt should see next, re-queue it, and let the
board carry it.

## 2. Motivation

Measured on this repository at `80096f8`: the board holds 273 tasks, 204 of
them `ready`, and **the manager has dispatched five attempts across one
task**. The 204 landings were imported from git trailers, not executed here.

What stops a session from being left alone is not dispatch — that works — but
what happens after `ready`. The lane exists, is tested, and lands correctly;
it simply has no caller inside the loop that produces its input. RFC 0019
A-105 removed the caller when it retired the tick, and A-115 recorded D-6.2,
D-6.12 and D-6.13 as unimplemented in consequence.

An unattended run therefore ends in one of two ways: the poison ceiling, or a
queue of candidates nobody merged. Both look like the engine stopping.

## 3. Current state

Verified at `80096f8`:

- **The lane is whole.** `application/lane.py` `process_lane(...)` takes
  `ci`, `approvals_required`, `require_review`, `quiet_window_s` and an
  optional `on_conflict`, and returns a `LaneResult` per candidate. Its
  tests cover the unmoved base, the moved base, the conflict and the
  refusals. `torve merge` is a thin renderer over it.
- **Nothing calls it in a loop.** `on_conflict` has no production caller at
  all: the tick's `_capture_for_revision` was its only one, and it is
  deleted. `torve merge` passes nothing, which is D-6.10's manual behaviour
  and correct for a person.
- **`promotion.auto_merge` is gone.** Deleted as a knob nothing could read
  (A-110), so restoring the leg restores its switch too rather than
  reviving a field.
- **The pass has the shape for it.** `residency.once` already takes
  `standing` and `relay` legs as injected callables (A-106, RFC 0051
  D-51.5), so a lane leg is the third of a kind rather than a new idea.
- **`review.feedback_from` is gone too** (A-110), so the disposal's
  forge-thread capture cannot be restored as it was; §5.3 says what is
  restored instead.

## 4. Goals / Non-goals

**Goals.** A session that dispatches can also land, without a person typing
between the two. A conflict disposes of itself rather than parking the board.
Landing stays exactly as serialized, gated and refusable as it is today.

**Non-goals.** No change to what the lane does — this document adds a caller,
not a policy. No post-landing publish. No parallel landing: one lander is
this document's premise, not its problem.

## 5. Design

### 5.1 The leg, and where it sits

```python
Lane = Callable[[], Awaitable[list[str]]]   # the task ids landed
```

Injected like `standing` and `relay`, and running **after** the relay and
**before** the mint. The order is the same argument each time: what a pass
does first is the work already owed. A candidate that went green an hour ago
is owed its landing more than a contract nobody has minted is owed its board
row — and landing first means the mint that follows sees a base that already
moved, which is the state the dependency rule reads.

Unlike the relay, the lane **is** stopped by a pause. Landing is not
delivering what is owed; it is advancing the repository, and a pause says
nobody has capacity to look at what advancing produces.

### 5.2 The switch is opt-in, and it is D-6.2's

`promotion.auto_merge: false` returns, with its original meaning and its
original default. Off, a pass never lands and `torve merge` is unchanged. On,
the leg is wired and every existing refusal still applies — CI, approvals,
review, the quiet window — because the leg calls the same `process_lane` a
person calls.

The switch is on `promotion`, beside the criteria it gates, rather than on
`loop`: it is a statement about landing policy, and it was one before the
loop that read it existed.

### 5.3 A conflict disposes of itself

`on_conflict` gets a caller again (D-6.12, D-6.13). The disposal is what the
tick's was, minus the half that cannot be restored:

- **Restored:** the candidate's diff is captured into the task's feedback
  record before the branch is superseded, so the next attempt sees what it
  collided with; the branch is kept; the task is re-queued through the
  escalation the lane already raises (D-6.10), which the operator resolves
  or the disposal resolves for it.
- **Not restored:** the pull request's review threads. Their allow-list
  (`review.feedback_from`) was deleted with the loop (A-110) and their
  capture rode the same hook. RFC 0005 D-5.12's forge half stays
  unimplemented, and this document does not pretend otherwise.

D-6.12's bound is unchanged and is why this is safe to automate: the disposal
re-queues **only when the base tip has moved** since the last attempt. A
conflict against a base that has not moved is not a race, it is the same
conflict, and re-queueing it is a loop.

### Alternatives considered

**Land from the worker, at the end of an attempt.** Rejected: landings
serialize and workers do not. Two workers finishing together would both try
to advance one base, which is the throughput wall arriving as a race
condition rather than as a queue.

**A separate lander process.** The same argument as RFC 0051 §5.4: a process
whose only job is draining a queue that another process visits every pass is
a second thing to run and supervise, for no throughput anybody needs while
one base serializes everything anyway.

**Leave it manual.** Honest, and it is the status quo — but it makes "run the
engine unattended" false advertising, and the engine's own dogfood record
(five attempts, one task) is what that produces.

## 6. Tests

- The leg lands what the lane would land, and the pass reports it: a green
  candidate on an unmoved base is landed by a pass with the switch on, and
  is not by a pass with it off.
- Order: the lane runs after the relay and before the mint, asserted the way
  the standing leg's position is.
- A pause stops the lane and does not stop the relay — the two legs differ
  here, and the difference is the design.
- The disposal captures before the branch is superseded, and re-queues only
  when the base moved; a conflict against an unmoved base escalates and stays
  escalated.
- Every existing lane refusal still refuses when the caller is the leg: CI
  red, approvals short, review missing, quiet window unexpired.

## 7. Docs

`pages/docs/operating.md` gains the switch and what an unattended session
does at `ready`. `pages/docs/architecture/execution.md` gains the landing
half of the pass. No new page.

## 8. Out of scope

- **The post-landing publish.** Base push, candidate republish and
  pull-request close-out left with the loop (A-105). Restoring them is a
  forge conversation — the adapter methods are still there and named — and
  it belongs to RFC 0006's own rebuild rather than to the leg that lands.
- **Parallel landing.** One base, one lander; `distribution.md` item 2 is
  the wall and this document does not move it.
- **The review-thread half of the disposal.** Named in §5.3, owed by
  RFC 0005.

## 9. Risks

- **An unattended session that lands badly is worse than one that lands
  nothing.** Mitigated by the switch being off by default and by every
  existing refusal applying unchanged — the leg adds a caller, not a policy.
- **The pass grows a second kind of I/O.** It already writes to a database
  and reads a filesystem; now it also rewrites a git base. A lane that hangs
  hangs the pass, and would present as "the manager stopped".
- **The disposal loops.** D-6.12's moved-base bound is the whole defence, and
  the test that pins it is the one to read first when a task starts
  re-queueing itself.

## 10. Unresolved questions

- **Whether a pass should land more than one candidate.** `process_lane`
  already walks the queue; whether a pass should drain it or take one is a
  question about how long a pass may hold the base, and one live session
  answers it.
- **What the leg does when the base has moved under it mid-pass.** The lane
  handles it per candidate today; whether a pass should re-read before each
  landing is the same question one level up.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-52.1 | `LOCKED` | The lane becomes a leg of the manager's pass; it is a caller, and changes nothing about what the lane does | `src/torve/application/residency.py` | Every refusal, gate and serialization the manual lane enforces applies unchanged, because it is the same function |
| D-52.2 | `LOCKED` | `promotion.auto_merge` returns with its original default of false; a pass with it off never lands | `src/torve/config/runconfig.py` | D-6.2's opt-in is restored rather than reinvented, and an upgrade changes nothing for anyone |
| D-52.3 | `ASSUMED` | The lane runs after the relay and before the mint, and a pause stops it | `src/torve/application/residency.py` | Landing advances the repository, which is what a pause is a statement about; delivering what is already owed is not |
| D-52.4 | `ASSUMED` | The conflict disposal is restored without its forge-thread half, which stays owed by RFC 0005 D-5.12 | `src/torve/application/lane.py` `src/torve/cli/manager.py` | The next attempt sees the diff it collided with; it does not see the review threads, and the corpus says so rather than implying otherwise |
| D-52.5 | `LOCKED` | The disposal re-queues only when the base tip moved (D-6.12, unchanged) | `src/torve/application/lane.py` | A conflict against an unmoved base is the same conflict, and re-queueing it is a loop |
| D-52.6 | `OPEN` | Whether a pass lands one candidate or drains the queue. Settled by the first unattended session | — | — |

## 12. Phasing

```yaml
- phase: 1
  title: the lane leg and its switch
  intent: >-
    `promotion.auto_merge` returns with its original default of false, and `residency.once` gains a `lane` leg injected the way `standing` and `relay` are — after the relay, before the mint, stopped by a pause. The composition root wires it from configuration to the same `process_lane` the manual verb calls, with the same CI, approvals, review and quiet-window arguments, so no refusal changes. A pass with the switch off behaves exactly as it does today, which is what makes this safe to land before anybody turns it on.
  scope:
    - "src/torve/application/residency.py"
    - "src/torve/config/runconfig.py"
    - "src/torve/cli/manager.py"
    - "tests/test_residency.py"
    - "pages/docs/operating.md"  # A-130
  acceptance:
    - "uv run pytest tests/test_residency.py tests/test_lane.py"
    - "uv run lint-imports --config pyproject.toml"
    - "uv run torve rfc check"
  depends_on: []
- phase: 2
  title: the conflict disposes of itself
  intent: >-
    The leg passes an `on_conflict` that captures the superseded candidate's diff into the task's feedback record before the branch is replaced, so the next attempt sees what it collided with, and re-queues only when the base tip has moved since the last attempt. The forge-thread half is deliberately absent and stays owed by RFC 0005. What must not change is the manual lane: `torve merge` passes no disposal and escalates as it always has.
  scope:
    - "src/torve/application/lane.py"
    - "src/torve/application/feedback.py"
    - "tests/test_lane.py"
  acceptance:
    - "uv run pytest tests/test_lane.py tests/test_feedback.py"
    - "uv run torve gates run"
    - "uv run torve rfc check"
  depends_on: [1]
```

## Amendments

### A-130 — 2026-09-06 — §7's documentation was owed by no phase
**Found by the reviewer of T-0281.** §7 assigns the switch's operator
documentation to `pages/docs/operating.md` and the pass's landing half to
`pages/docs/architecture/execution.md`. Neither phase's `scope` in §12
names `pages/` at all, so no minted contract could write either page
without leaving its scope — the documentation was assigned to a document
and owed by nobody. An operator arming unattended landing had only this
RFC to read.

**Changed:** phase 1's scope gains `pages/docs/operating.md`, and the
section written for the switch that landed with it is authored here rather
than deferred to a phase that cannot reach it. This is an authoring defect
of the kind the skill's rule 3a already names for decision rows — a
deliverable whose paths fall outside every phase's scope is the same
mistake one level up, and the check that catches it does not exist yet.
