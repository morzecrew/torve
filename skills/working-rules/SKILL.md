---
name: working-rules
description: When executing a torve task contract — where the files to change are, which tests are yours to edit, running the acceptance once, and how to stop or depart when the contract cannot hold.
roles: [implement, review, revert]
---

# Working Rules

How work is done under a torve contract, in whatever repository the contract
belongs to. A sandbox receives this as a declared equipment item under
`.torve/skills/`; a session executing a contract by hand reads it with
`torve guide working-rules`.

**Nothing here outranks the contract.** The contract names the decisions, the
scope and the acceptance commands; this is how to work one, not what to build.

## Read the targets

The first message carries the files this task changes and the tests paired
with them, whole. Start from them: do not open them again, and do not survey
the tree before the first edit. The contingent files are named beside them;
open one only when the change reaches it. A name defined elsewhere is one grep
of `.torve/context/symbols.txt` away, and `torve spec show <identifier>`
prints a row with its consequence.

**Issue independent reads and greps in one message.** A read whose target
depends on what a previous read said is still a second message.

## Tests

Edit inside the contract's scope only. A test outside it is read-only: run it,
never change it. Adding a test inside the scope is an addition; a green earned
by weakening a test is a red.

**A test may not assume it is the only one on the machine.** What it creates
on a shared daemon — a container, a volume, a network, a directory under a
shared root — it finds again by its own name, never by a literal another test
also uses.

A test that proves a decision row cites it in its own docstring: the row's
full identifier, `S-NNNN/D-n`, as a whole token. A module or class docstring
proves nothing for the tests beneath it.

## The acceptance

**Run the contract's acceptance commands as written, once, after your edits.**
No `tail`, `grep` or other filter around them: the battery runs the same
commands outside the session, and the in-session run is for your own
confidence. A red is the exception: fix, then run once more.

## When the contract cannot hold

Halt with `torve abort`, as `flag-dont-flip` says, and stop. A departure from
an `ASSUMED` or `OPEN` row is recorded before you build it, with what the row
should say instead:

```console
$ torve log divergence <task> \
    --decision D-5 --grade ASSUMED --kind departed --class discovery \
    --claim "..." --evidence "src/a.py:12 — ..." --action departed \
    --proposal "..."
```

The verb takes several rows: repeat the options, and the nth `--decision`
goes with the nth of every other one. The engine writes and stages the log;
never edit `.torve/tasks/<task>/log.yaml` by hand. Work that keeps to its rows
records nothing.

## Finishing

Gates run outside your session, against the working tree you leave behind. Exit
0 when you consider the work complete.
