# RFCs

Design proposals for Torve.

## Allocating a number

The next free number is **0011**. Before creating an RFC, glance at the table
below (or `ls` this directory) and take the next unused integer — numbers
collide when minted in parallel. Update this table in the same change.

Filename: `NNNN-kebab-title.md`. Keep the `# RFC NNNN — Title` H1 and the
number in the filename in sync.

## Index

| # | Title | Status | One-line routing description |
|---|---|---|---|
| [0001](0001-torve-charter.md) | Torve: charter | 📝 Draft | Domain model, state machine, ports, and the graded-decision contract every child RFC inherits; deliberately excludes anything shippable. |
| [0002](0002-gates-library.md) | Gates as a library | 🚧 In progress | The gate contract, the starting gate set, sabotage verification, and packaging gates as a pip-installed CI dependency — the first shippable increment. |
| [0003](0003-runner-isolation.md) | Runner and isolation | 🚧 In progress | `torve run` for one task synchronously: sandbox lifecycle, lease and cancellation, reaper, and the simulation harness that proves the state machine. |
| [0004](0004-agents-tiering.md) | Agent adapters and tiering | 📝 Draft | Real agent adapters behind the `Agent` port, tiering economics, shadow runs, and the telemetry that makes harness choice measurable. |
| [0005](0005-review-as-a-run.md) | Review as a run | 📝 Draft | Independent automated review as a second run role: isolation rules, the finding contract, calibration, and replacing third-party PR reviewers. |
| [0006](0006-merge-escalation.md) | Merge train and escalation policy | 📝 Draft | Serialized landing of candidates, promotion criteria, escalation routing, and how human attention is budgeted. |
| [0007](0007-planner-context.md) | Planner and context | 📝 Draft | Minting tasks from an approved RFC, projecting execution facts back into planning sessions, and the read-only MCP surface. |
| [0008](0008-tracker-projection.md) | Tracker projection | 📝 Draft | Any task tracker as a presentation surface: outbound projection over the outbox, restricted inbound commands, no authoritative state in the board. |
| [0009](0009-skills-evals.md) | Skills and evals | 📝 Draft | Skill routing per role, versioned distribution, trigger collision, and the eval loop that retires skills that do not earn their tokens. |
| [0010](0010-vcs-provenance-revert.md) | VCS, provenance and revert | 📝 Draft | How agent work becomes commits and pull requests, provenance trailers, signing at the runner boundary, and revert as a task role. |

## Status legend

- 📝 **Draft** — proposed, not started
- 🚧 **In progress** — partially shipped
- ✅ **Complete** — fully shipped
- ❌ **Rejected / withdrawn**
