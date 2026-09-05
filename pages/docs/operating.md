# Operating the engine

What to type, what reads what, and which verbs no longer exist. The
architecture pages explain *why* the parts are separated; this one is the
surface an operator actually touches.

## Secrets

Configuration names an environment variable; the value lives in `.env`,
which is never committed. The console script reads that file before
dispatching, so the eight names this repository needs are set once rather
than exported into every shell.

The environment always wins. `TORVE_PG_DSN=... torve status` means what it
says — a file on disk does not overrule a name you typed. Only what the
shell has not set is filled in.

## The verbs

| Verb | What it does |
| --- | --- |
| `torve plan <rfc>` | mint task contracts from an accepted document — deterministic, no model call |
| `torve intake "<request>"` | draft contracts from prose in a read-only sandbox; a human adopts or refuses |
| `torve adopt <task>` | the human signature: ids are minted here, under the engine lock |
| `torve run <task>` | one task, synchronously, sandboxed — the exit code carries the outcome |
| `torve manager serve <partition> --dsn …` | the resident manager: import contracts, claim one task at a time, execute, record |
| `torve fleet serve` | the same, over every repository the manifest names, one attention budget across all of them |
| `torve merge` | land ready candidates, serialized. Lands and stops — it does not push the base |
| `torve approve <task>` | approve a candidate's **current tip**; a push after it approves nothing |
| `torve reap` | sweep sandboxes, worktrees and finished run state. `--escalated` also discards escalations you have dealt with by hand |
| `torve status` / `why` / `context` | the reports. See below for which carrier answers |
| `torve manager board <partition>` | every contract this partition owns and what became of it |
| `torve gates run` / `check` | the battery, and the sabotage suite that proves a gate can fail |
| `torve rfc check` / `amend` / `index` | the corpus surface |

**Retired, and not coming back by that name:** `torve tick` and
`torve fleet tick`. The standing loop they drove is abandoned (RFC 0019
A-105); the manager runs its legs. Nothing scheduled the tick anyway.

## Which carrier answers a report

`why`, `status` and `context` read either the record or this host's files,
and **naming a partition is what selects the record**:

```bash
torve status                                   # this host's run-state files
torve status --partition morzecrew/torve       # the partition's board
torve context --partition morzecrew/torve      # tasks and attempts from the record
```

One rule is automatic, and it runs in the safe direction: a record that
turns out not to hold the run falls back to the files, never the reverse. A
run from before the record existed left a state file and no log, so an
empty record means *ask the files*, not *nothing ever ran*.

`torve context` prints which carrier answered above its first count, and
the JSON envelope carries a `sources` block. Read it before quoting a
figure. On this repository the record holds 10 attempt rows where the files
hold 636 — both true, and only one of them answers "what has this cost".

`torve serve` and `torve mcp` take the same two options and pass them into
every reader, per request — so a dashboard left running shows the board as
it is, not as it was at boot.

And `--dsn` is optional: it defaults to the DSN your configuration names,
which `.env` has already put in the environment. `--partition` alone is
enough.

## Running a pass without spending

```bash
torve manager serve <partition> --dsn "$TORVE_PG_DSN" --no-dispatch --passes 1
```

Imports contracts and releases expired leases; claims nothing. This is the
pass to run after changing contracts, when you want the board to catch up
without a worker taking the first thing it finds there — which on a full
board is a real agent and real money.

## The escalation queue is a pause

A pass mints nothing while this root's escalation queue is at
`loop.pause_escalations` (default 1), counting both carriers — a task
escalated under v1 and one the manager escalated are both a person's turn.
The queue may drain during a pause; it may not grow.

So an escalation nobody triages stops new work. That is the design, and it
is worth knowing before wondering why a board went quiet: check
`torve status`, resolve it with `torve manager resolve`, or discard the
footprint with `torve reap --escalated`.
