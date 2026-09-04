# The execution model

One task, one run, one worktree. The runner claims the task, cuts a worktree
from `main`, and drives attempts until the work lands, a budget runs out, or
a human is needed.

![Run lifecycle](../assets/diagrams/run.svg)

## The attempt loop

Ceilings are checked **before** dispatch (poison ceiling, iteration budget,
wallclock). Each attempt runs the tier's agent in a fresh sandbox over the
**same worktree** — a gate-red attempt leaves its diff in place, so the next
attempt revises rather than rebuilds. The agent exits, the battery runs, and
the outcome routes:

- **Gates red** → history fact, next attempt, same worktree. The tier may
  name `retry_variants` per gate axis (RFC 0034): a conviction on a
  `functional` gate can re-dispatch on a heavier rung.
- **Gates green** → the reviewer lane (below).
- **Three convictions** → `poison_ceiling`, operator triage.

The sandbox is disposable and identical per attempt (image digest is part of
the measured regime, D-17.1); the worktree is the only continuity. Warm
starts come from a baked dependency layer and a per-slot cache volume
(RFC 0035) — never shared across slots, never present in replays.

## The reviewer lane

When gates go green the runner mints a review task (D-5.11) and drives one
reviewer attempt in a **disposable copy** of the worktree (A-78) — the
reviewer executes the battery itself, reads the staged diff (A-79), and
returns findings as data. Unlocatable evidence is discarded before anyone
sees it (D-5.4).

- **No blockers** → the landing commit, with the task trailer and the
  decisions it inherited.
- **A surviving blocker** → today: immediate `blocker_finding` escalation.
  RFC 0043 (accepted 2026-09-04, unbuilt) changes this to a bounded in-run
  revision: the blockers and the convicted diff travel in the RFC 0005 §4a
  feedback record, the loop continues in the same worktree, and only a
  blocker that survives the revision budget escalates.

## Escalation is the interface to humans

The escalation vocabulary is a closed enum — extending it is an RFC
amendment, because an extensible enum makes telemetry incomparable across
time. An escalated run keeps *everything* (worktree, state, traces) for
triage; terminal runs are swept by `torve reap`.

Operator triage landings are a recognized pattern with a paper trail: the
operator repairs a *form* defect (a log's YAML quoting, an unstaged file the
reviewer named), re-runs the battery, and lands with a disclosed
`— operator triage landing` commit carrying the task trailer. Content is
never changed under this signature.

## What the last queue taught (2026-09-04)

Thirteen contracts landed; every escalation in the final stretch was a
*form* failure on green work:

| Task | Conviction | Repair |
| --- | --- | --- |
| T-0244 | central new file written, never staged — reviewed diff imported a module it did not contain | operator staged it |
| T-0245 | one unquoted YAML scalar made the whole log unparseable; three sonnet attempts convicted identically | operator quoted it |
| T-0246 | investigation task: only artifact is the log, agent never staged it, diff judged empty | operator staged it |

Two engine lessons pinned by these: the reviewed diff counts tracked changes
only (an investigation task **cannot** land unless its log is staged), and
the compliance-grammar gates are now the dominant poison-ceiling cause —
six form convictions against zero functional ones in the measurement window
(D-34.9).
