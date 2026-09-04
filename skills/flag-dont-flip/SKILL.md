---
name: flag-dont-flip
description: Executing a task against an RFC with graded decisions — what to do when reality contradicts a decision, and how to write the divergence log that decisions-reported gates.
roles: [implement, revert]
gate: decisions-reported
---

> **Specialisation.** Derived from `agent-skills/flag-dont-flip`, specialised for
> artefacts that Torve parses. Divergence from upstream is expected and
> intentional — **do not reconcile**. Improvements of general value flow
> upstream, not the reverse.

# Flag, Don't Flip

When reality contradicts a decision, **report the contradiction — do not quietly
pick the other branch.** The decision was made by someone with context you do
not have; acting on the contradiction without recording it leaves the codebase
disagreeing with its own specification with nothing to say when or why.

## The grade decides the action

| Grade | On contradiction | Logged action | Never |
|---|---|---|---|
| `LOCKED` | **Halt.** Write the entry, stop, escalate. | `halted` | Proceed — even when the alternative is obviously better. |
| `ASSUMED` | **Depart.** Write the entry, build the better option, carry on. | `departed` | Halt. You were licensed to decide this. |
| `OPEN` | **Decide.** Write the entry recording the choice and why, carry on. | `decided` | Halt, or hand back half an implementation. |
| `UNLISTED` | **Decide, and owe a row.** The entry carries the `proposal:` it puts back. | `decided` | Treat it as `OPEN`. Nobody looked; a proposal is owed. |

Two symmetric failures: flipping a lock leaves the spec fiction; halting on an
assumption costs the round-trip grading exists to avoid. Over-caution is a real
failure, not a safe default.

## Underspecification is a halt, not a question

You are executing autonomously. There is nobody to hand a plan to, and stopping
to propose one deadlocks the task — no diff, nothing for the gates to run
against, and a run that dies on wall-clock rather than saying anything useful.

Plan internally, then build. What the plan is *for* is the third list below.

Before writing code, work out: the files you will touch, the decision row
governing each non-trivial choice, and **the decisions your plan needs that the
contract does not settle**.

If that third list has **three or more load-bearing entries**, the contract is
not executable. Halt:

```yaml
- decision: unlisted
  grade: UNLISTED
  kind: blocked
  class: spec-gap
  at: 2026-08-20T11:04:12Z
  attempt: 1
  claim: retry policy, backoff bounds and dead-letter behaviour are all unsettled;
    any implementation of this contract invents three load-bearing decisions
  evidence: `rg -n "retry|backoff|dead.?letter" rfcs/0009-*.md` — no matches
  action: halted
  proposal: three rows needed before this is executable; see claim
```

Fewer than three: decide them, log each as `UNLISTED`, carry on. That is what
`UNLISTED` is for, and each entry owes a `proposal:` back.

**Inventing the missing decisions silently is the failure this skill exists to
prevent.** A contract that needs three load-bearing inventions is not a contract
you can satisfy — it is a specification defect, and reporting it is the correct
outcome, not a failure to complete. The halt escalates as `underspecified`
(charter A-21): it indicts the contract, not the code, and the fix is an
amendment and a re-mint, never a retry.

## The log: `torve log divergence`

You state an entry; the engine writes the log. One call per entry:

```console
$ torve log divergence T-0142 --attempt 2 \
    --decision D-3 --grade LOCKED \
    --kind contradicted --class spec-gap \
    --claim "sessions cannot live in Redis; no Redis service in this deployment" \
    --evidence "infra/compose.yaml:1 — no redis service is defined" \
    --action halted \
    --proposal "LOCKED — sessions live in Postgres until Redis is provisioned"
```

Everything mechanical belongs to the engine: the file and its YAML quoting,
the timestamp, the pin your evidence resolves against, the drift count, and
staging the log so the gate that judges the diff can see it. You supply the
judgement, which is the part no one else can.

**The entry is checked before anything is written.** A refused entry leaves
the log exactly as it was and prints what to repair — in the same words the
gate would use hours later, which is the point of being told now. Fix the
line and run the command again.

**Never write or edit `.torve/tasks/<task-id>/log.yaml` yourself.** The
engine owns that file. A hand-written log is the failure this verb exists to
remove: it has ended three-attempt runs over a stray character, an evidence
line in the wrong shape, and a file nobody staged.

Write the entry **before** you act; an entry written afterwards is a
rationalisation. Entries are append-only — a wrong entry gets a later entry
saying so, never an edit of the old one. `--grade` is the grade the task
carries now, never re-read from the current spec.

- **`--evidence` must be locatable by someone else**: `path:line`,
  `path:start-end`, or a backticked command with its output. A sentence is a
  claim, and `--claim` is where claims go; unlocatable evidence is discarded,
  and a discarded entry counts as none.
- **The citation LEADS, prose follows after ` — `.** Everything before the
  first ` — ` is read as the citation and nothing else. Extra citations go in
  the prose. Parentheses after the path break the parse, and a path without
  `:line` is not a citation:
  - wrong: `src/a.py:10-20 (the guard); src/b.py:5 (its caller)`
  - wrong: `src/a.py — the guard` (no line number)
  - wrong: `src/a.py:10-20; src/b.py:5 — the guard` (semicolon-joined citations where prose belongs — one citation leads)
  - right: `src/a.py:10-20 — the guard; src/b.py:5 is its caller`
- **`--class` answers: could this have been known before code existed?**
  `discovery` no (healthy) · `spec-gap` yes, spec was silent · `drift` yes, spec
  covered it and it was built otherwise (**a defect** — should be zero) ·
  `irreducible` neither: stop and spike.
- **`--kind resolved` with `--action decided` is the close-out** — the legal
  attestation of compliance in a touched `LOCKED` area, which the silence check
  demands an entry for. `--kind blocked` licenses only `--action halted`.
- **`--decision unlisted` owes a `--proposal`**, and takes `--grade UNLISTED`.
- **`--notes` carries prose that belongs beside the entry** — never a sibling
  document.
- Bypass records (RFC 0002 §6a) live in a separate `bypasses:` list in the
  same file, written by the runner from a human's signed trailer. Not yours.

## Silence is what gets caught

Violating a lock is not mechanically detectable; the absence of an entry in an
area a `LOCKED` decision declares trivially is. When in doubt whether a
contradiction is worth reporting, report it — a surplus entry costs a reader
ten seconds, a missing one is an unexplained divergence found months later.
Compliant work in a touched `LOCKED` area owes a close-out entry too.

Halting on a `LOCKED` row is a success. State it plainly ("Halted on D-3 …
needs a human decision"), never soften it into a flip wearing a disclaimer.
And never amend the spec from inside a task — your entry *is* the amendment
proposal; the author accepts it into the decision table, citing your entry.

The enforcing gate is `decisions-reported` in the Torve package: schema,
grade/action legality, evidence locatability, the drift count, and the silence
check over the task's declared decision paths.
