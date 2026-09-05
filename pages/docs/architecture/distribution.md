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
| the scheduler is whoever invoked `torve run` | a tick on cron, one node | a resident manager per partition, restart-transparent because its whole view is a fold over the record (D-44.5). The tick is retired; `torve merge` and `torve reap` keep their verbs |
| two writers tear the JSONL store | serial queues, by necessity | Postgres behind the document port; the mock stays for tests |
| assignment state lives in the runner | a killed run needed a reaper to notice | a worker holds a lease and nothing else; the manager reclaims it from the record when it goes quiet (D-44.6) |
| the tick's dispatch rules are the only ones | a filesystem scan | the same rules over the record, and the scan is deleted — the oversize skip and the already-ran test were the last two to move (RFC 0019 A-105) |
| the planning reports read one host's files | a scan of `.torve/` and `.wt/` | `why`, `status` and `context` answer from the record when a partition is named, and name the carrier they used |
| one manager, one repository | a process and a hand-typed partition per repo | one resident process over the operator's fleet manifest, which now names the board each root mints onto |
| the corpus is only readable by re-parsing it | every reader walked `rfcs/` and re-parsed each document | the corpus is imported into the record; decisions are versioned subjects answered by query |
| a worker needs the repository to know what it is running | dispatch read every `contract.yaml` off disk, every pass | the mint carries the contract; the board answers what a partition can start, and the task directory is an importer |

## Still single-node

| # | Assumption | Where | Breaks when |
| --- | --- | --- | --- |
| 1 | worktrees on the local filesystem are the work surface | runner, workspace adapter | executors run on remote sandboxes with no shared filesystem |
| 2 | landings serialize through one `main` on one clone | merge lane, operator chain | any second lander — and this is the throughput wall, not a bug |
| 5 | the broker binds loopback routes into local sandboxes | broker adapter | remote sandboxes — RFC 0041 added bind and advertise for exactly this |
| 6 | host proxy and `.env` passthrough shape egress | run configuration, docker adapter | a fleet node with different egress |
| 7 | attempt budgets reset per dispatch | runner | re-dispatch across nodes multiplies the reset |

One capability left with the loop rather than moving: after a landing, the
tick pushed the base fast-forward-only, republished the landed candidate
branches and closed a landed pull request the forge had not marked merged.
`torve merge` lands and stops. With `auto_merge` off nobody had been using
that half, so it is deleted rather than ported — if it is wanted it belongs
on the lane, not on a loop nobody schedules.

Item 3 was the tracker's outbox, and it is gone rather than answered: the
whole tracker projection was deleted in September 2026 (RFC 0008 A-92) — it
was inert in every repository torve runs, and 2,600 lines nobody runs and
everybody must maintain is worse than a subsystem that is gone and recorded.
The design survives in its document for whoever rebuilds it.

Item 4 — run state and telemetry as files — is crossed off for the readers
and open for the surfaces. `torve why`, `torve status` and `torve context`
answer from the record when a partition is named, and say in the report
which carrier answered. The served dashboard and the MCP tool call those
same readers *without* a partition, so a second node reaching them still
gets one host's files. That is a wiring gap rather than a design one, and
it is the smallest thing on this page.

What stays on files by shape rather than by schedule: the findings ledger,
because the record carries a claim only for a blocker, and operator
feedback, because no event kind carries it. Moving either is a change to
the event vocabulary, not a change of source.

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
