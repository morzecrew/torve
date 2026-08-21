# Torve

A specification-and-gate layer for a standing agent team.

Torve turns a reviewed specification into machine-checkable work, runs agents
against it under deterministic gates, and refuses to let anything land that
cannot prove it did what it was told.

## Contents

```text
rfcs/                      the design corpus
```

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

## Ship order

0002 → 0003 → 0004 → 0005 → 0006 → 0007. Each is useful standing alone;
0008, 0009 and 0010 slot in after 0003 as needed. 0010 must land before 0006
puts anything on a branch.

## Status

Draft. Three open decisions across the corpus, all waiting on code rather than
discussion: the output format of `torve context`, which tracker ships first,
and whether review runs get their own branch.
