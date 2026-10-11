# Serving a night

A night is `torve manager serve <partition> --night`: a loop that imports
contracts, claims one task at a time, runs it, and lands what goes green. It
runs under the `night:` terms in the configuration and stops on a budget, the
wall-clock end, an empty board or a named escalation class.

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
5. The documents are accepted and committed on `main`. When their contracts
   are fresh, run one import pass first, because a night refuses to open on a
   board with nothing it can start:
   `torve manager serve <partition> --no-dispatch --passes 1`.
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
- `torve night show <partition>` reports the night so far.
- Check that the process is alive with `ps -eo pid,args | grep "[m]anager serve"`.
- Leave a running attempt alone. To tell it something, use
  `torve manager note <partition> <task> "<what it should know>"`. The agent
  reads notes; nothing interrupts it.

## Stop

The night stops itself. To stop it early, kill the manager by its PID. The
cost is the lease on the attempt in flight. That task is reclaimed later, and
a task whose work had already landed stays landed.

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
