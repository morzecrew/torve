# What does not distribute

The engine now has an actor layer that distributes — a resident manager per
partition, stateless workers, and a record both read from a database. What
is still single-node is the *work surface* and everything that reads files
instead of the record.

This page is the inventory. It is deliberately blunt about which items are
answered, because the previous version of it was the argument that produced
the manager, and an inventory that never crosses anything off is a wish
list.

## Answered

| Assumption | Was | Now |
| --- | --- | --- |
| the scheduler is whoever invoked `torve run` | a tick on cron, one node | a resident manager per partition, restart-transparent because its whole view is a fold over the record (S-0044/D-5). The tick is retired; `torve merge` and `torve reap` keep their verbs |
| two writers tear the JSONL store | serial queues, by necessity | Postgres behind the document port; the mock stays for tests |
| assignment state lives in the runner | a killed run needed a reaper to notice | a worker holds a lease and nothing else; the manager reclaims it from the record when it goes quiet (S-0044/D-6) |
| the tick's dispatch rules are the only ones | a filesystem scan | the same rules over the record, and the scan is deleted — the oversize skip and the already-ran test were the last two to move (S-0019 S-0019/A-8) |
| the planning reports read one host's files | a scan of `.torve/` and `.wt/` | `why`, `status` and `context` answer from the record when a partition is named, and name the carrier they used |
| the partition is typed on every report | a hand-typed partition per repo | the configuration's `scm.repo` names the board a repository's nights are served under |
| the corpus is only readable by re-parsing it | every reader walked `rfcs/` and re-parsed each document | the corpus is imported into the record; decisions are versioned subjects answered by query |
| a worker needs the repository to know what it is running | dispatch read every `contract.yaml` off disk, every pass | the mint carries the contract; the board answers what a partition can start, and the task directory is an importer |

## Still single-node

| # | Assumption | Where | Breaks when |
| --- | --- | --- | --- |
| 1 | worktrees on the local filesystem are the work surface | runner, workspace adapter | executors run on remote sandboxes with no shared filesystem |
| 2 | landings serialize through one `main` on one clone | merge lane, operator chain | any second lander — and this is the throughput wall, not a bug |
| 5 | the broker binds loopback routes into local sandboxes | broker adapter | remote sandboxes — S-0041 added bind and advertise for exactly this |
| 6 | host proxy and `.env` passthrough shape egress | run configuration, docker adapter | a fleet node with different egress |
| 7 | attempt budgets reset per dispatch | runner | re-dispatch across nodes multiplies the reset |

One capability left with the loop rather than moving: after a landing, the
tick pushed the base fast-forward-only, republished the landed candidate
branches and closed a landed pull request the forge had not marked merged.
`torve merge` lands and stops. With `auto_merge` off nobody had been using
that half, so it is deleted rather than ported — if it is wanted it belongs
on the lane, not on a loop nobody schedules.

Item 3 was the tracker's outbox, and it is gone rather than answered: the
whole tracker projection was deleted in September 2026 (S-0008 S-0008/A-5) — it
was inert in every repository torve runs, and 2,600 lines nobody runs and
everybody must maintain is worse than a subsystem that is gone and recorded.
The design survives in its document for whoever rebuilds it.

Item 4 — run state and telemetry as files — is crossed off. `torve why`,
`torve status` and `torve context` answer from the record when a partition
is named and say in the report which carrier answered, and the surface
that re-exposes them — the MCP tool — takes the same partition and reads it
per request (S-0032 S-0032/A-3). `--dsn` defaults to the
DSN the configuration names, so naming the partition is the whole of it.

What stays on files by shape rather than by schedule: the findings ledger,
because the record carries a claim only for a blocker, and operator
feedback, because no event kind carries it. Moving either is a change to
the event vocabulary, not a change of source.

## How a second repository gets served

One manager per partition: each repository runs its own `torve manager serve`
over the same log, under its own configuration. The fleet that ticked every
root in one round left in 0.9.0 with nothing serving more than one repository
through it (S-0122/D-17); serving several at once in one process waits on a
seat allocator, a broker per worker and a decision about how many attempts an
operator wants running unattended.

## The wall that is not a file

Landing throughput (item 2) is the one that does not dissolve by moving a
reader, and it is now the *only* one. Landings serialize within a partition
by design, and that is correct: two agents landing on one branch is a
conflict the engine cannot resolve afterwards.

Multi-repository scaling is partition count, and partitions are now
something the engine actually runs rather than something it could. A single
repository's landing rate stays what it is today, and any answer to that is
a different design, not a refactor.

## How to tell when an item is done

Each of these ends the same way: the file-reading caller is deleted, not
wrapped. The rules they implement are already shared with the record-reading
callers, which was the point of doing that first — deleting a scan whose
rule lives somewhere else is a small change, and deleting one that owns its
rule is a rewrite.
