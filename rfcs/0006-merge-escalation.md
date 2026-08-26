---
id: "0006"
title: Merge train and escalation policy
status: accepted
implementation: partial
depends_on: ["0003"]
informed_by: ["0005"]
supersedes: []
superseded_by: null
amended_by: ["A-35", "A-42"]
owner: Lev Litvinov
description: >-
  Serialized landing of candidates, promotion criteria, escalation routing, and how human attention is budgeted.
schema_version: 1
---

# RFC 0006 — Merge train and escalation policy

- **Implementation state:** phases 1–2 executed 2026-08-23 (T-0041 prevention/kill/engine sight; T-0042 the serialized lane as `torve merge`); A-26 executed 2026-08-23 (T-0043 — a conflicted landing escalates the run, D-6.10); the CI leg executed 2026-08-23 (T-0048 — `promotion.require_ci` consults the remote's latest run per workflow before landing, demonstrated live on the lab: a refusal on real red CI, the landing after the green rerun). the notifier executed 2026-08-23 (T-0051 — interrupt-class escalations page `tracker.notify` through the outbox as issue assignment plus @mention, exactly once per escalation event; D-6.11, closing RFC 0003 D-3.18; demonstrated live on the lab: a real `blocker_finding` escalation assigned and mentioned the operator). the approvals and quiet-window fields executed 2026-08-24 (T-0060 — sha-bound approvals on the run state, an approval of a superseded tip counting for nothing; the quiet window clocked from the tip's committer age; RFC 0008's `approve` supplies approvals from the board, T-0061). Outstanding: the §7 exit criteria accrue with dogfood use (ten landings, two weeks of resolution times) — the calendar, not work
- **Scope:** How candidates land, in what order, and how human attention is budgeted. Covers the serialized merge lane, promotion criteria, escalation routing, and parallelism limits. Excludes conflict resolution, which stays permanently out of scope.
- **Inherits:** D-1, D-6 from RFC 0001

---

## 1. The correction this document exists for

"The engine does not merge" is right about responsibility and silent about sequencing. Three independently green pull requests merged back to back can each invalidate the next one's CI, so green at review time does not imply green after landing.

**`ready` is a serialized lane, not a set.** One candidate at a time rebases onto the current base, waits for current-head CI, and lands. Only this state is capped; earlier states share the global pool so workers stay busy while candidates queue.

Two refinements worth copying rather than rediscovering:

- **Skip redundant validation on a clean rebase.** If the branch rebases with no source files changed and already passed its gates, re-running the full local gate buys no new signal — required current-head CI is the enforcement. If the rebase touches source, resolves conflicts, or leaves validation state unknown, the full gate runs again.
- **Poll CI with backoff against lightweight endpoints.** The rate-limit budget is shared with the agents themselves.

What does not change: the engine never resolves a conflict and never merges without the configured approval. A merge conflict is an escalation with reason `merge_conflict`. Serialization orders candidates; it does not assume authority.

*Amended by A-26 2026-08-23 (registered on the charter):* the escalation is literal. A conflicted rebase aborts, the branch stays exactly as measured, and the run transitions `ready → escalated` with reason `merge_conflict` — the queue's age (D-6.8) starts counting the moment a landing fails, not when a human happens to read the lane report. The `ready → escalated` edge is opened in the charter's §4 table by the same amendment; `ready` stays terminal to the engine everywhere else. Resolution is the standard escalated fork: re-queue to re-run the task against the moved base, or abandon when a human landed the work by hand.

*Execution note 2026-08-23 (T-0048):* the rebase path releases the candidate's engine worktree first — the run's own worktree pins the task branch, and git refuses a second checkout; a `ready` run's worktree is disposable, its work lives on the branch. A-26's merge-before-reap ordering concerns state files; the lane owns the worktree half itself.

*Execution note 2026-08-23 (T-0052):* the cleanliness guard scopes to landed content — the engine's own record files (task directories under `.torve/tasks/`, the manifest's telemetry file, the outbox pair) never block the lane, and a refusal names the offending paths. The first standing-team run surfaced the papercut: the runner-minted review contract lands untracked in the root checkout, and a blanket guard made every landing demand a human commit of engine bookkeeping first. The reviewed regime files (`gates.yaml`, `config.yaml`, the sandbox and vendored-skill trees) stay refusable dirt.

## 2. Prevention beats ordering

Two tasks whose `scope.allow` sets intersect must not run concurrently. That check happens **before dispatch** (RFC 0002 §6), which is strictly better than resolving the collision after both have produced work.

The merge train handles what gets through anyway — base moving under a long task, shared lockfiles, generated artefacts.

## 3. Promotion

```yaml
promotion:
  require:
    - gates: green
    - review: no_blocker_findings
    - ci: green_on_current_head
    - approvals: 1
  quiet_window: 30m        # no new pushes since the last review
  auto_merge: false        # default; opt in per repository, never globally
```

**Review freshness is relative to current head.** An approval that predates the last push is not an approval of what would land. Pushing resets the quiet window.

**Auto-merge stays off by default.** Where enabled, restrict it to a named task class with high test coverage — not a global switch.

*Execution note 2026-08-24 (T-0060/T-0061):* `approvals` and `quiet_window` landed as flat fields beside `auto_merge` and `require_ci` (the sketch's `require:` list shape was already departed from at T-0048), both zero-disabled. An approval is recorded on the run state as {actor, sha, at} by a commander's `/torve approve` (RFC 0008 D-8.9's authorization applies); the lane counts only approvals of the branch tip as measured at lane start. The quiet window is plain seconds — no duration parser exists and one knob does not justify one.

*Execution note 2026-08-23 (T-0048):* `green_on_current_head` reads the LATEST run per workflow for the head sha — a re-run supersedes the run it replaces, and a stale failure must not veto a green rerun; a red run of a different workflow still does. A base push invalidates in-flight merge-ref runs, so a lane consulting pull-request-event CI should expect one retrigger after the base moves.

## 4. Human attention is the scarce resource

Automated review feeds the bottleneck; it does not relieve it. Three agents produce pull requests faster than one person reads them, and the system quietly converts throughput into review debt.

So attention is budgeted as explicitly as the gates:

| Class | Route |
| --- | --- |
| Green gates, no findings, within scope, small diff | queue for batch review in a window |
| Any `blocker` finding | escalate, notify |
| `locked_conflict` | escalate, notify — needs a decision, not a fix |
| `poison_ceiling`, `budget_exhausted` | escalate, batch |
| `gate_infrastructure_failure` | notify the owner of the harness, not the task author |

**Review windows, not continuous interruption.** Two fixed slots a day beats a notification stream, and the batching is what keeps the practice sustainable.

**Parallelism raises only when escalation rate is low.** The limiting number is not worker capacity, it is how many pull requests a human can read carefully in a day. Raise one dimension at a time: first task classes, then workers, then repositories.

## 5. Escalation is a first-class outcome

An escalated task is not a failure of the system; it is the system working. The metric to watch is not escalation count but **escalation resolution time** — a queue of stuck tasks nobody triages is the real failure, and it looks identical to success from the runner's side.

Every escalation carries: reason from the enumerated vocabulary, `trace_ref`, gate results, and the execution-log entries that led to it. Enough to decide without opening a terminal.

## 5a. Blocked dispatch must be visible

Overlapping `scope.allow` sets prevent concurrent dispatch (§2). In a repository where most tasks touch a shared route registry or config file, that collapses parallelism toward one and turns the system into an expensive sequential runner.

Rather than design for this now, make it observable and interruptible:

- A refusal to dispatch is logged with its cause — `blocked_by_overlap: T-0139 on packages/api/routes.ts` — never a silent wait.
- Telemetry counts blocked dispatches per path, so "top contended paths" is a query rather than a hunch.
- `torve kill <task-id>` force-terminates a run: sandbox destroyed, lease released, task escalated with reason `killed`.

If the top contended path turns out to dominate, the answers available then are a dedicated serialized lane for declared hotspots, or moving hotspot edits into their own phase. Neither needs deciding today.

## 5b. Engine health

Attempt telemetry describes the work. Nothing so far describes the engine, which means its failures arrive by human report rather than by graph.

Cheapest viable option, and the recommended one: an `EngineEvent` record written through the same telemetry path as `Attempt`. A second observability system is a second system to operate. If the substrate ships a metric catalog and dashboard stack, that becomes the stage-3 destination behind the same port; OpenTelemetry export is a later option, not a starting point.

| Signal | Why |
| --- | --- |
| Queue depth by state | the shape of the backlog |
| Lease reclaim rate | workers dying silently |
| Sandbox create/destroy failures | runtime health |
| Reaper orphan count | cleanup regressions |
| Outbox relay lag and retry depth | projection and notification falling behind |
| `gate_infrastructure_failure` rate | gates broken versus code broken |
| Kill-by-timeout rate | agents ignoring cancellation |
| Burn rate against daily cap | budget trajectory |

Plus `torve doctor` as a preflight: credentials present, sandbox reachable, store migrated, gates resolvable.

**The signal that matters most is not in the table: escalation queue age.** A queue nobody triages looks identical to success from the runner's side, and is the failure mode most likely to actually happen here.

## 6. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-6.1 | `LOCKED` | `ready` is a serialized lane; only this state is capped | `src/torve/application/lane.py` `src/torve/cli/merge.py` | Otherwise concurrent merges invalidate each other |
| D-6.2 | `LOCKED` | Auto-merge off by default, opt-in per repository and task class | `src/torve/config/runconfig.py` | Reversing this is how review debt becomes invisible |
| D-6.3 | `ASSUMED` | Review freshness is measured against current head; a push resets the window | `src/torve/application/lane.py` | — |
| D-6.4 | `ASSUMED` | Escalations are batched into review windows except blockers and locked conflicts | `src/torve/application/projections.py` | Tune the split once resolution times are known |
| D-6.5 | `ASSUMED` | Parallelism increases only when escalation resolution time is stable | — | The bottleneck is a person, not the pool |
| D-6.6 | `LOCKED` | Blocked dispatch is logged with cause and counted per path; `torve kill` always available | `src/torve/application/runner.py` `src/torve/cli/run.py` | Contention must be diagnosable before it is designed for |
| D-6.7 | `ASSUMED` | Engine health rides the existing telemetry path as `EngineEvent` | `src/torve/application/telemetry.py` | A second observability stack is a second thing to operate |
| D-6.8 | `LOCKED` | Escalation queue age is the primary alert | `src/torve/application/telemetry.py` | The failure that is invisible from inside the runner |
| D-6.9 | `ASSUMED` | Dispatch keys durable runs by task and generation, so concurrent dispatches of one task converge on a single store claim instead of racing the engine's state-file guard. Added by execution 2026-08-21 | `src/torve/application/taskstore.py` | The simulation surfaced idempotent claim convergence as the stronger mutual-exclusion mechanism |
| D-6.10 | `LOCKED` | A conflicted landing escalates the run — `ready → escalated`, reason `merge_conflict`; the branch is left exactly as measured. Added by amendment A-26 2026-08-23 (registered on the charter). Amended by A-35 2026-08-24: in the standing loop the escalation's standard disposal is applied in place — the branch is captured for the revision loop (RFC 0005 §4a) and the run re-queued, at most once per base tip (D-6.12); resolution stays human — re-queue or abandon — wherever the loop cannot dispose: a repeat conflict against an unmoved base, or the operator's manual lane | `src/torve/domain/states.py` `src/torve/application/lane.py` `src/torve/cli/tick.py` | Otherwise a candidate that cannot land is invisible to the escalation queue, whose age is the primary alert |
| D-6.11 | `ASSUMED` | Interrupt-class escalations (§4's notify and harness-owner routes) produce exactly one delivered notification through the outbox — issue assignment plus @mention of the configured `tracker.notify` login; assignment is best-effort, the mention is the notification; batch stays board-visible only, and an empty login keeps the notifier inert. Closes RFC 0003 D-3.18. Added by execution 2026-08-23 — see .torve/tasks/T-0051 | `src/torve/application/tracker.py` `src/torve/adapters/tracker/github.py` | A queue nobody triages looks identical to success; the interrupt class must reach a person without a polling habit |
| D-6.12 | `ASSUMED` | The lane's automatic conflict disposal is bounded by progress: it re-queues only when the base tip differs from the last conflicted base this run recorded — landings are the only source of new conflicts, so the bound is structural — and a repeat conflict against an unmoved base escalates for a human. The operator's manual lane never auto-disposes. Added by amendment A-35 2026-08-24 | `src/torve/application/lane.py` `src/torve/cli/tick.py` | An automatic requeue without a progress bound is a spin loop wearing doctrine |
| D-6.13 | `ASSUMED` | The conflict probe precedes the prompt: a candidate short of approvals whose base has moved is probed read-only (`git merge-tree`, no worktree, no ref moves — the merge verdict is the rebase verdict under one commit per attempt), and a provably conflicting tip is never offered for approval — the A-35 disposal fires at probe time, D-6.12's progress bound and manual-lane exemption included; a clean probe prompts exactly as before, the approval honoured through the landing's mechanical rebase (D-6.3 untouched). Added by amendment A-42 2026-08-25 | `src/torve/application/lane.py` `src/torve/adapters/vcs/git.py` | Approve-twice: the human approved a tip, watched it conflict seconds later, and was asked again — the burn was discoverable before the ask |

## Phasing

*(Added 2026-08-23 at acceptance, with the path relocation to RFC 0015's
tree — no `lane/` package exists. The forge-shaped legs stay deferred with
the forge: CI polling with backoff, the approvals/quiet-window promotion
fields, and the notifier the outbox feeds (deferred from RFC 0003 D-3.18,
deferred again here — there is no channel to notify until one exists;
*execution note 2026-08-23 (T-0051):* the channel now exists — RFC 0008's
tracker — and the notifier landed through it, D-6.11). In
the local regime the operator invoking the lane is the configured approval,
and the gate battery is current-head CI.)*

```yaml
- phase: 1
  title: Prevention, kill, and engine sight
  intent: |
    What must exist before a lane is safe to run: dispatch refuses a task
    whose scope intersects an active run's, logged with its cause and
    counted per path through a new EngineEvent record on the existing
    telemetry path; torve kill force-terminates one run — sandbox
    destroyed, state escalated as killed; and the escalation queue's age
    becomes visible in the context projection, because a queue nobody
    triages looks identical to success from inside the runner.
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
- phase: 2
  title: The serialized lane
  depends_on: [1]
  intent: |
    Ready is a lane, not a set: torve merge processes candidates one at a
    time — a task branch whose base has not moved lands as it was measured;
    one whose base moved is rebased and its gate battery re-run over the
    rebased tree before landing; a conflict escalates as merge_conflict
    and the lane moves on. The engine never resolves a conflict, and the
    operator's invocation is the recorded approval. Auto-merge stays off
    by default in configuration.
  scope:
    - "src/torve/application/lane.py"
    - "src/torve/adapters/**"
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

## 7. Exit criteria

- Ten tasks landed through the serialized lane with no post-merge CI break attributable to ordering.
- Escalation resolution time recorded for two weeks.

## Amendments

### A-35 — 2026-08-24 — a conflicted landing re-queues through the revision loop (amends D-6.10, adds D-6.12)

**Found in operation** — twice in one evening the owner approved a
candidate and watched it escalate `merge_conflict` seconds later: a
same-file sibling had landed first, the sha-bound approval burned with
the superseded tip, and the disposal was a human `/torve retry` whose
every consequence was already mechanical — capture the branch for the
revision loop, delete it, re-queue. Ceremony, not judgement.

**Changed:** the standing loop applies that disposal itself. The
conflict still escalates — the record and the queue-age alarm stand —
and is then re-queued in place: feedback captured (RFC 0005 §4a),
branch dropped, run queued, at most once per base tip (D-6.12). The
re-run's fresh candidate prompts for its own sha-bound approval as
always: the feedback channel steers attempts, never landings.

**Deliberately unchanged:** the engine never resolves a conflict — the
rebase still aborts with the branch exactly as measured; approvals stay
sha-bound and human, a burned approval re-asked, never assumed; and the
operator's manual lane escalates exactly as before, because a person
running it by hand is present to decide. The deeper reorder — probing
the rebase before requesting approvals, so a doomed tip is never
offered for approval at all — is left to a future amendment.

### A-42 — 2026-08-25 — the probe precedes the prompt (adds D-6.13)

**Found in operation** — the deferred half of A-35, promised in its
own closing paragraph. Through two live batches the pattern repeated:
the commander approves a candidate, a same-file sibling has landed
first, the sha-bound approval burns with the superseded tip seconds
later, and the re-run asks for a fresh one. The disposal was
automatic; the burned ask was not. Every one of those conflicts was
knowable before the prompt went out.

**Changed:** the lane asks the question first (D-6.13). A candidate
short of approvals whose base has moved is probed with
`git merge-tree --write-tree` — read-only, no worktree, no ref moves,
and exact for single-commit candidates (RFC 0010 D-10.8) — and a
provably conflicting tip is never offered for approval: the A-35
disposal fires at probe time, capture and re-queue included, under
D-6.12's progress bound. The prompt the human sees is now always for
a tip whose landing no known conflict can void.

**Deliberately unchanged:** a clean probe changes nothing — the prompt
goes out and the approval is honoured through the landing's mechanical
rebase, exactly D-6.3's regime, so a stale-but-compatible tip still
costs one approval, not two; the landing-time rebase and its own
conflict disposal, which still guard the same-pass race the probe
cannot see; and the manual lane, which never probes — an operator
running `torve merge` by hand is present to read the refusal.
