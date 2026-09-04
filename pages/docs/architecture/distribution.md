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
| the scheduler is whoever invoked `torve run` | a tick on cron, one node | a resident manager per partition, restart-transparent because its whole view is a fold over the record (D-44.5) |
| two writers tear the JSONL store | serial queues, by necessity | Postgres behind the document port; the mock stays for tests |
| assignment state lives in the runner | a killed run needed a reaper to notice | a worker holds a lease and nothing else; the manager reclaims it from the record when it goes quiet (D-44.6) |
| the tick's dispatch rules are the only ones | a filesystem scan | the same rules over the record — and the two now share one implementation |
| one manager, one repository | a process and a hand-typed partition per repo | one resident process over the operator's fleet manifest, which now names the board each root mints onto |
| the corpus is only readable by re-parsing it | every reader walked `rfcs/` and re-parsed each document | the corpus is imported into the record; decisions are versioned subjects answered by query |

## Still single-node

| # | Assumption | Where | Breaks when |
| --- | --- | --- | --- |
| 1 | worktrees on the local filesystem are the work surface | runner, workspace adapter | executors run on remote sandboxes with no shared filesystem |
| 2 | landings serialize through one `main` on one clone | merge lane, operator chain | any second lander — and this is the throughput wall, not a bug |
| 3 | the tracker outbox is a directory, relayed inside the tick | tracker, outbox | the tracker runs anywhere but the repository host |
| 4 | run state and telemetry are files on the host | reaper, lane, `torve status`, planning projections | a second node needs an answer the record can give and these cannot |
| 5 | the broker binds loopback routes into local sandboxes | broker adapter | remote sandboxes — RFC 0041 added bind and advertise for exactly this |
| 6 | host proxy and `.env` passthrough shape egress | run configuration, docker adapter | a fleet node with different egress |
| 7 | attempt budgets reset per dispatch | runner | re-dispatch across nodes multiplies the reset |

Items 3 and 4 are the same shape as everything already crossed off: a reader
that consults a file where the record could answer. They are not blocked on
design — RFC 0044 §12 names them — they are blocked on the migration, because
a reader moved onto the record today would find an empty one on any run the
manager did not dispatch.

## How a second repository gets served

One process, one manifest, one log. The operator's fleet manifest already
listed every repository, in a deterministic order, with a trust class each
and one attention budget across all of them; it now also names the partition
a repository's contracts are minted onto.

```yaml
# ~/.config/torve/fleet.yaml
repositories:
  - root: ~/GitLibrary/Morze/torve
    trust: own
    partition: morzecrew/torve
  - root: ~/work/atlas
    trust: reviewed
    partition: acme/atlas
```

```console
$ torve fleet serve --dsn "$TORVE_PG_DSN"
```

The partition is declared there and never derived, for the reason the trust
class is: a repository under work configures nothing about the engine that
works on it, and a repository that chose its own partition could mint onto a
board it was never given. Deriving it from the git remote is convenient and
wrong for a repository with no remote, with two, or with one that changed.

A round surveys every queue, decides one pause for the fleet total, then
gives each repository a single pass under its own trust class. A repository
with no partition, or whose configuration asks for more than its class
allows, or that fails outright, is recorded and the round carries on — a
manager that stopped serving four healthy repositories because a fifth was
broken would be worse than one that says so.

**Rounds are serial.** One long attempt delays every other partition's next
pass by its whole duration. Running partitions at once is the obvious next
move and is deliberately not built: it needs a seat allocator that will not
run two workers under one cache volume, a broker per worker, and a decision
about how many attempts an operator wants running unattended. It is worth
doing when someone feels the delay, not before.

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
