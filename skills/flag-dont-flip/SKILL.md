---
name: flag-dont-flip
description: When executing a torve task contract against graded decisions — what to do when reality contradicts a decision or the contract cannot be built as written: halt with `torve abort`, or depart from a row that licenses it and say what it should say.
roles: [implement, revert]
---

> **Specialisation.** Derived from `agent-skills/flag-dont-flip`, specialised for
> artefacts that Torve parses. Divergence from upstream is expected and
> intentional — **do not reconcile**. Improvements of general value flow
> upstream, not the reverse.

# Flag, Don't Flip

When reality contradicts a decision, **report the contradiction; do not quietly
pick the other branch.** The decision was made by someone with context you do
not have. Acting on the contradiction without saying so leaves the codebase
disagreeing with its own specification, with nothing to say when or why.

There are two things to say, and only two: a halt and a departure. Work that
keeps to its rows says nothing — touching a governed file owes no entry.

## The grade decides the action

| Grade | On contradiction | Never |
|---|---|---|
| `LOCKED` | **Halt** with `torve abort --row <id>`. The owner decides. | Proceed, even when the alternative is obviously better. |
| `ASSUMED` | **Depart.** Record it, build the better option, carry on. | Halt. You were licensed to decide this. |
| `OPEN` | **Depart.** Settle it, record what you chose and why, carry on. | Halt, or hand back half an implementation. |

Two symmetric failures: flipping a lock leaves the specification fiction, and
halting on an assumption costs the round trip grading exists to avoid.

## Halting

```console
$ torve abort --row <id> --claim "..." --evidence "path:line — ..."
```

One halted entry per row cited, for the task and attempt you are running. A
halt on a `LOCKED` row goes to the owner; any other goes back to the author as
underspecified. **A justified abort is a successful attempt**: once it is
recorded, stop. State it plainly, never soften it into a flip wearing a
disclaimer, and never amend the specification from inside a task — the claim
is the amendment's starting point.

**Underspecification is a halt, not a question.** You run unattended: there is
nobody to hand a plan to. Plan internally, then build. If the plan needs three
or more load-bearing decisions the contract does not settle, it is not
executable: abort citing no row, and name the missing decisions in the claim.
Fewer than three: decide them and carry on. A contingent file is in reach:
edit it when the change needs it. A file outside the scope and the contingent
list that the change cannot avoid is the same finding: name it and halt.

## Departing

A departure is written before the code it describes, and its proposal says
what the row should say instead. How to record one is `working-rules`.

## What an entry must say

- **Evidence must be locatable by someone else**: `path:line`,
  `path:start-end`, or a backticked command with its output. A sentence is a
  claim; unlocatable evidence is refused.
- **The citation leads, prose follows after ` — `.** Everything before the
  first ` — ` is the citation; extra citations go in the prose.
  - wrong: `src/a.py — the guard` (no line number)
  - wrong: `src/a.py:10-20; src/b.py:5 — the guard` (one citation leads)
  - right: `src/a.py:10-20 — the guard; src/b.py:5 is its caller`
- **The grade is the grade the task carries now,** never re-read from the
  current specification.
- **The class answers: could this have been known before code existed?**
  `discovery`: no. `spec-gap`: yes, the specification was silent. `drift`:
  yes, it covered this and it was built otherwise — a defect. `irreducible`:
  neither; stop and spike.
- Entries are append-only: a wrong entry gets a later entry saying so.
