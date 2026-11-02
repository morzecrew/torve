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
5. The documents are accepted and committed on `main`. For `--night`
   without `--chain`, each one is planned onto the board, because such a
   night refuses to open on a board with nothing it can start:
   `torve plan S-NNNN --no-dry-run --partition <partition>`. A `--chain`
   does not refuse: it idles, and plans accepted documents itself between
   nights — unless its engine imports from the checkout it serves, when
   the plan above is still yours.
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

## Running the chain

`--chain` keeps one process opening nights back to back:
`torve manager serve <partition> --night --chain`. After a night closes it runs
idle passes and opens the next once the open's refusal reads empty — the board
holds something startable — and the UTC day has budget left. A night closed on
a class in `night.stop_on` ends the chain, and `--chain` without `--night` is
refused.

Under `--chain`, `night.budget_usd` and `night.budget_attempts` bound a UTC
day, not one night: each night opens with what the day has left on each axis
as its own budget, and records that remainder in `night.opened`. When an axis
configured above zero has no remainder left, no night opens until the next UTC
midnight. An axis configured as 0 stays unbounded, and `night.minutes` still
bounds each night. A night that crosses midnight keeps the budget it opened
with; what it spends after midnight counts against the new day.

Between nights, with nothing in flight, the chain fetches and — when this
checkout is on the configured base branch with a clean tree — fast-forwards it
to the remote's tip. It then mints, in number order, every accepted document
whose implementation is `none` and which has minted no task, into the board as
`torve plan S-NNNN --partition <partition> --no-dry-run` does, writing no
file. A refused plan is tried again after each mint that succeeds, and one
still refused when a pass mints nothing is recorded once per base commit and
tried again when the base moves. A document accepted only on another branch is
never planned.

A chain whose engine imports from the checkout it serves skips the
fast-forward and the planning and says so once at the start: moving that
checkout would move the code the process is running.

So a document reaches a ready pull request without its owner: the chain mints
it when it is accepted on the base, the night implements it, and the lane
lands each green phase and republishes the document's pull request. These
terms are what make the run survive what its owner would have answered:

```yaml
promotion:
  landing: pull_request      # the thread leg reads a document's pull request
  unit: document
  auto_merge: true           # arms the pass's landing leg
  require_review: true       # at least one criterion must be armed

scm:
  repo: owner/name           # the forge repository the pull request opens against
  open_pr: true              # landing: pull_request needs the forge's pull request

tiers:
  executor:
    revision_variant: executor.strong   # a surviving blocker's revision rung
  executor.strong:                      # a tier like any other, and it must exist
    harness: claude
    model: claude-opus-5-5
    provider: anthropic

threads:
  enabled: true                         # off by default
  bots: [coderabbitai, codeant-ai]      # the forge's review bots: waited for and answered
  approve_comment: "..."                # posted once the wave is green; unset posts nothing
```

`revision_variant` names a configured tier, dotted, and load refuses a value
that resolves to none. When a review blocker survives the executor seat, the
revision attempt runs on that tier in the same run, worktree and critique, so
a blocker that escalates has already had the stronger seat's attempt. Empty,
the default, keeps the revision on the seat.

The thread leg additionally needs `threads.enabled`, the forge's bots in
`threads.bots`, and a landing that reads a document's pull request
(`promotion.landing: pull_request` with `unit: document`); it refuses to load
any other way. `approve_comment` is what the leg posts on the pull request once
every thread a configured bot opened is resolved and the head's checks are
green, keyed by head so it lands once. Every login in `bots` is also what the
night's review wait waits for.

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
  reads notes; nothing interrupts it. To stop it, add `--urgent`: a Claude
  seat under a broker stops at its next tool call, any other seat when its
  session ends. The attempt keeps its tree on the task's branch and escalates
  as `killed`.

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
