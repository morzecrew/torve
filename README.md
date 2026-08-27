# Torve

A specification-and-gate layer for a standing agent team.

Torve turns a reviewed specification into machine-checkable work, runs agents
against it under deterministic gates, and refuses to let anything land that
cannot prove it did what it was told.

## Contents

```text
rfcs/                      the design corpus; INDEX.md is generated (D-A.6),
                           amendments live inside their target RFCs (D-A.5)
                           `ops/` and `pages/` are conventions, not standing
                           directories: they exist when they hold something
                           (D-A.1, D-A.1b)
src/torve/                 the gates library and runner (RFC 0002 + 0003),
                           layered per RFC 0015: base/ domain/ application/
                           adapters/ gates/ config/ cli/ — enforced by the
                           layering gate (import-linter contracts)
skills/                    specialised skills shipped with the package (A-3)
.torve/                    every Torve file, root stays clean (RFC 0013):
  gates.yaml               this repository's own gate manifest
  config.yaml              runner configuration (runtime adapter, store, ceilings)
  tasks/                   one directory per task: contract.yaml and log.yaml
                           (A-1, A-12), logs pinned to a base_sha (D-A.7)
```

## Gates (RFC 0002, shipped here)

```bash
pip install torve            # or: uv sync inside this repository
torve gates run --base origin/main          # all gates; exit code is the outcome
torve gates run --only scope,acceptance
torve gates run --format json               # GateResult records for ingestion
torve gates check                           # the sabotage suite
torve size .torve/tasks/T-0002.yaml         # pre-dispatch size estimate
```

One CI step per repository, `.torve/gates.yaml` (legacy root `gates.yaml`
still resolves). Builtin gates:
`scope`, `acceptance`, `no-test-tampering`, `decisions-reported`, `self-audit`,
`secrets`; anything else is a shell command in the manifest. On a
`torve/T-nnnn` branch the task contract in `.torve/tasks/` is discovered
automatically; without one, task-input gates report `skipped`, never a silent
green. Every run appends a JSONL telemetry record stamped with
`config_hash`.

## Runner (RFC 0003 phase 1, shipped here)

```bash
torve run T-0142                 # one task, synchronous, exit code is the outcome
torve run T-0142 --agent fake --scenario demo.yaml
torve reap --dry-run             # sweep orphaned sandboxes and worktrees
torve status --format json       # persisted run records, one JSON document

```

The run loop: claim → git worktree → sandbox → agent → gates → `ready` or
`escalated`, with an enumerated escalation vocabulary and a poison ceiling
checked before dispatch. Everything runs in a sandbox — even the fake agent —
and shell gates execute in a fresh sandbox the agent never touched. Two
runtime adapters behind one contract: Docker, and OpenSandbox
(`pip install 'torve[opensandbox]'`).

The attempt loop executes as one durable function over the forze run store:
real leases, fenced terminal writes, `torve cancel` riding the lease
heartbeat, recovery via `claim_abandoned`. Mock store in-process by default;
`store.adapter: postgres` (`pip install 'torve[postgres]'`, then
`torve migrate substrate`) for cross-process durability. A deterministic
simulation drives the real loop and store concurrently under seeded
interleavings — invariants, reachability targets, and deliberately broken
twins the oracle must catch.

## Reading order

Start with the charter. Every other document inherits its decisions and none
re-decides them; a child RFC that needs a charter decision changed writes an
amendment against 0001 rather than contradicting it locally.

| Document | Increment |
| --- | --- |
| `0001-torve-charter.md` | domain, state machine, ports, charter decisions, stopping rules |
| `0002-gates-library.md` | gates as an installed package in CI — ships first, no runner needed |
| `0003-runner-isolation.md` | `torve run` against a fake agent, sandboxed |
| `0004-agents-tiering.md` | real adapters, tiering economics, shadow runs, telemetry |
| `0005-review-as-a-run.md` | independent review as a second run role; replacing third-party reviewers |
| `0006-merge-escalation.md` | serialized merge lane, promotion, human attention budget |
| `0007-planner-context.md` | `torve plan`, `torve context`, read-only MCP, cold start |
| `0008-tracker-projection.md` | any task tracker as a presentation surface |
| `0009-skills-evals.md` | skill routing, distribution, and evals that retire skills |
| `0010-vcs-provenance-revert.md` | how work lands, how history explains itself, how it is undone |
| `0011-cli-contract.md` | output contract, exit codes, non-TTY behaviour — decided before anything parses them |
| `0012-migrations.md` | owner-grouped SQL migrations; the forze pin; the conformance battery as the gate |
| `0013-configuration-layout.md` | where Torve's files live in a consuming repository, and why two files |

## Ship order

0002 → 0003 → 0004 → 0005 → 0006 → 0007. Each is useful standing alone;
0008, 0009 and 0010 slot in after 0003 as needed. 0010 must land before 0006
puts anything on a branch.

## Status

Draft. Three open decisions across the corpus, all waiting on code rather than
discussion: the output format of `torve context`, which tracker ships first,
and whether review runs get their own branch.
