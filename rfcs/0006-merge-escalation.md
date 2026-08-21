# RFC 0006 — Merge train and escalation policy

- **Status:** 📝 Draft — depends on 0003, informed by 0005
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

| # | Grade | Decision | Consequence |
| --- | --- | --- | --- |
| D-6.1 | `LOCKED` | `ready` is a serialized lane; only this state is capped | Otherwise concurrent merges invalidate each other |
| D-6.2 | `LOCKED` | Auto-merge off by default, opt-in per repository and task class | Reversing this is how review debt becomes invisible |
| D-6.3 | `ASSUMED` | Review freshness is measured against current head; a push resets the window | — |
| D-6.4 | `ASSUMED` | Escalations are batched into review windows except blockers and locked conflicts | Tune the split once resolution times are known |
| D-6.5 | `ASSUMED` | Parallelism increases only when escalation resolution time is stable | The bottleneck is a person, not the pool |
| D-6.6 | `LOCKED` | Blocked dispatch is logged with cause and counted per path; `torve kill` always available | Contention must be diagnosable before it is designed for |
| D-6.7 | `ASSUMED` | Engine health rides the existing telemetry path as `EngineEvent` | A second observability stack is a second thing to operate |
| D-6.8 | `LOCKED` | Escalation queue age is the primary alert | The failure that is invisible from inside the runner |

## 7. Exit criteria

- Ten tasks landed through the serialized lane with no post-merge CI break attributable to ordering.
- Escalation resolution time recorded for two weeks.
