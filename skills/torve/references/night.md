# Serving a night

A night is `torve manager serve <partition> --night`: a loop that imports
contracts, claims one task at a time, runs it, and lands what goes green. It
runs under the `night:` terms in the configuration and stops on a budget, the
wall-clock end, a board with no work left or a named escalation class.

## Before

1. The working tree is clean and `main` matches `origin/main`. No git
   worktree holds a `torve/S-*` branch.
2. Load the environment the configuration names: `set -a; . ./.env; set +a`.
   The store's DSN is in the variable `store.dsn_env` names. Blank a stale
   GitHub token (see the rails in `torve guide torve`).
3. `torve doctor`. Every red line is a refusal waiting to happen. Fix it, or
   understand why it does not apply tonight.
4. After upgrading torve, run `torve migrate --status`, then
   `torve migrate --all`.
5. The documents are accepted and committed on `main`, and each one is
   planned onto the board, because a night refuses to open on a board with
   nothing it can start:
   `torve plan S-NNNN --no-dry-run --partition <partition>`.
   The night's import pass reads only the contract files under
   `.torve/tasks/`. It never plans a document, and with a store `plan`
   writes no such file.
6. A configuration made only for the night can live outside the repository
   (`--config <path>`), so the working tree stays clean for the lane.

## Start

Run the manager in the background and send its output to a file:

```bash
torve manager serve <partition> --night --worker night-1 --interval 30 > night.log 2>&1 &
```

For two workers, start a second process with `--slot 1` and its own
`--worker` name. Width pays only when the board holds tasks whose scopes do
not overlap.

## Watch

- `torve manager board <partition> --format json` every few minutes, filtered
  to tonight's tasks. A task moves through queued, claimed or running, gated,
  ready, landed. Escalated or halted means it waits for you.
- When the escalation queue reaches `loop.pause_escalations` every pass is
  paused on purpose: a paused pass mints nothing, runs no standing job and
  opens no review round. It does not stop work already on the board — queued
  tasks are still claimed and run, an in-flight attempt finishes, the
  notification relay pages, and with `promotion.landing: pull_request` the
  lane still lands green candidates on their branches and republishes their
  pull requests. Resolve escalations until the queue is below the threshold
  (see `torve guide torve escalations`) to let new work through again.
- A night does not close as drained while a task is still queued or
  escalated, such as one escalation with the rest waiting on it. It keeps
  polling until its wall-clock end, so the work runs once you resolve the
  escalation, without a new night.
- `torve night show <partition>` reports the night so far.
- Check that the process is alive with `ps -eo pid,args | grep "[m]anager serve"`.
- Leave a running attempt alone. To tell it something, use
  `torve manager note <partition> <task> "<what it should know>"`. The agent
  reads notes; nothing interrupts it.

## With the verify pilot on

When the configuration turns the pilot on (`verify.enabled: true`), a phase
whose document is a `feat` or `fix` and whose scope reaches `src/` mints a
verify task before its implement task. The verifier writes that phase's tests
from the rows and the intent alone and lands them failing; the implementer
that depends on it runs them. Starting, watching and stopping a night are
unchanged — the board just carries one more task per such phase, and its
implement task waits on the verifier.

Read a held-out failure in the implement task's gate output, never from the
attempt that wrote the test: `held-out` reports the failing test's
identifier, its message and the rows it cites, and never the test's source.
A verify task convicted by `verify-tests` — its file does not collect, an
entry row no test cites, or a test that already passes before the
implementation exists — comes back to you the same way.

A disputed verifier test arrives as an `underspecified` escalation: the
implementer halts with a divergence entry naming the test and the row it
claims to decide, and is refused the file, so the test is never edited by the
side that disagrees with it. Take it through `torve guide torve escalations`
as any other `underspecified` halt.

## Stop

The night stops itself. To stop it early, kill the manager by its PID. The
cost is the lease on the attempt in flight. Run `torve reap`: it expires the
dead run, and the next pass on this host escalates the task `lease_expired`
rather than waiting out its lease. Take it through `torve guide torve
escalations`. A task whose work had already landed stays landed.

## The morning

1. `torve night show <partition>` lists what landed, what a gate convicted,
   what the engine ended and what waits on a person.
2. Take each escalation through `torve guide torve escalations`.
3. For each document pull request, read its checks and review threads, then
   follow `torve guide torve review`.
4. `torve reap` clears finished worktrees and sandboxes. Run
   `torve reap --escalated` only for escalations you have already dealt with.
5. When a document's pull request merges, one pull request marks it
   `implementation: complete` and adds its changelog entry.
