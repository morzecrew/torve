---
name: flag-dont-flip
description: When executing a torve task contract against graded decisions — what to do when reality contradicts a decision, when a contract is too underspecified to build, and what a divergence entry must say for the decisions-reported gate.
roles: [implement, revert]
gate: decisions-reported
---

> **Specialisation.** Derived from `agent-skills/flag-dont-flip`, specialised for
> artefacts that Torve parses. Divergence from upstream is expected and
> intentional — **do not reconcile**. Improvements of general value flow
> upstream, not the reverse.

# Flag, Don't Flip

When reality contradicts a decision, **report the contradiction; do not quietly
pick the other branch.** The decision was made by someone with context you do
not have. Acting on the contradiction without recording it leaves the codebase
disagreeing with its own specification, with nothing to say when or why.

How to record an entry, the verb and its options, is in `working-rules`. This
skill is about what to do and what the entry must say.

## The grade decides the action

| Grade | On contradiction | Logged action | Never |
|---|---|---|---|
| `LOCKED` | **Halt.** Write the entry, stop, escalate. | `halted` | Proceed, even when the alternative is obviously better. |
| `ASSUMED` | **Depart.** Write the entry, build the better option, carry on. | `departed` | Halt. You were licensed to decide this. |
| `OPEN` | **Decide.** Write the entry recording the choice and why, carry on. | `decided` | Halt, or hand back half an implementation. |
| `UNLISTED` | **Decide, and owe a row.** The entry carries the proposal it puts back. | `decided` | Treat it as `OPEN`. Nobody looked; a proposal is owed. |

Two symmetric failures: flipping a lock leaves the specification fiction, and
halting on an assumption costs the round trip grading exists to avoid.
Over-caution is a real failure, not a safe default.

## Underspecification is a halt, not a question

You are executing autonomously. There is nobody to hand a plan to, and stopping
to propose one deadlocks the task: no diff, nothing for the gates to run
against, and a run that dies on wall-clock rather than saying anything useful.

Plan internally, then build. Before writing code, work out the files you will
touch, the decision governing each non-trivial choice, and **the decisions your
plan needs that the contract does not settle**.

If that last list has **three or more load-bearing entries**, the contract is
not executable. Halt with one `unlisted` entry of kind `blocked`, class
`spec-gap` and action `halted`, whose claim names the unsettled decisions and
whose proposal says what rows are needed. Its evidence can be a search that
came back empty, as a backticked command with its output.

Fewer than three: decide them, record each as `UNLISTED`, and carry on. That is
what `UNLISTED` is for, and each entry owes a proposal back.

**Inventing the missing decisions silently is the failure this skill exists to
prevent.** A contract that needs three load-bearing inventions is a
specification defect, and reporting it is the correct outcome, not a failure to
complete. The halt escalates as `underspecified`: it indicts the contract, not
the code, and the fix is an amendment and a re-mint, never a retry.

A file outside the contract's scope that the change cannot avoid is the same
kind of finding. Name the file in the entry's claim and its proposal, and halt;
do not edit outside the scope.

## What an entry must say

- **Write the entry before you act.** An entry written afterwards is a
  rationalisation. Entries are append-only: a wrong entry gets a later entry
  saying so, never an edit of the old one.
- **The grade is the grade the task carries now,** never re-read from the
  current specification.
- **Evidence must be locatable by someone else**: `path:line`,
  `path:start-end`, or a backticked command with its output. A sentence is a
  claim, and the claim is where claims go; unlocatable evidence is discarded,
  and a discarded entry counts as none.
- **The citation leads, prose follows after ` — `.** Everything before the
  first ` — ` is read as the citation and nothing else. Extra citations go in
  the prose. Parentheses after the path break the parse, and a path without
  `:line` is not a citation:
  - wrong: `src/a.py:10-20 (the guard); src/b.py:5 (its caller)`
  - wrong: `src/a.py — the guard` (no line number)
  - wrong: `src/a.py:10-20; src/b.py:5 — the guard` (one citation leads)
  - right: `src/a.py:10-20 — the guard; src/b.py:5 is its caller`
- **The class answers: could this have been known before code existed?**
  `discovery`: no, which is healthy. `spec-gap`: yes, the specification was
  silent. `drift`: yes, the specification covered it and it was built
  otherwise, which is a defect and should be zero. `irreducible`: neither;
  stop and spike.
- **Kind `resolved` with action `decided` is the close-out**: the attestation
  of compliance in a touched `LOCKED` area, which the silence check demands an
  entry for. Kind `blocked` licenses only action `halted`.
- **An `unlisted` decision owes a proposal** and takes grade `UNLISTED`.
- **Notes carry prose that belongs beside the entry**, never a sibling
  document.
- Bypass records live in a separate `bypasses:` list in the same file, written
  by the runner from a person's signed trailer. They are not yours.

## Silence is what gets caught

Violating a lock is not mechanically detectable; the absence of an entry in an
area a `LOCKED` decision declares is. When in doubt whether a contradiction is
worth reporting, report it: a surplus entry costs a reader ten seconds, and a
missing one is an unexplained divergence found months later. Compliant work in
a touched `LOCKED` area owes a close-out entry too.

A decision you touched and disagree with still gets an entry; that is what kind
`contradicted` and action `halted` are for. What is never right is saying
nothing.

Halting on a `LOCKED` row is a success. State it plainly ("Halted on D-3 …
needs a human decision"), never soften it into a flip wearing a disclaimer. And
never amend the specification from inside a task: your entry *is* the amendment
proposal, and the author accepts it into the decision table, citing your entry.

The enforcing gate is `decisions-reported`: schema, grade and action legality,
evidence locatability, the drift count, and the silence check over the task's
declared decision paths.
