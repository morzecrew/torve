---
name: working-rules
description: Executing any Torve contract — where the engine's facts are, which verbs read and write them, what a scope names and what it never names, and the two rules about tests that convict most often.
roles: [implement, review, revert]
---

# Working Rules

The one text of how work is done in this repository (S-0067/D-3). A sandbox
receives it as a declared equipment item under `.torve/skills/`; a session
reads the same directory through its skill root. Both read this file.

**Nothing here outranks the contract.** The contract names the decisions, the
scope and the acceptance commands; this is how to work one, not what to build.

## Read the pack first

`.torve/context/index.md` lists what the engine knows about the task — the rows
with their consequences, the battery you will face, the tests over your scope,
your own prior attempts and what convicted them. Read it before writing code.

`torve spec show <identifier>`, `torve spec paths <file>` and `torve spec tests
<identifier>` read the specification from this worktree — a row's consequence,
what governs a path, what proves a row. `torve spec why-not "<words>"` lists the
alternatives already rejected. Each directory's `AGENTS.md` carries the rows
governing it.

`torve log notes` prints anything the engine has to say about this run — a known
flake, a constraint that arrived after you started. It is a poll: nothing
interrupts you, so read it when you are stuck or about to commit. No notes is
the normal case.

**Issue independent reads and greps in one message** (S-0073/D-5). The harness
executes several tool calls from a single message, and orienting is where the
round trips go: the files you already know you need, the greps whose answers do
not depend on each other, go together. Nothing is given up — a read whose target
depends on what a previous read said is still a second message.

## The divergence log

Divergences from the contract's decisions are recorded with:

```console
$ torve log divergence <task> --decision ... --evidence ...
```

as the `flag-dont-flip` skill specifies. The engine writes and pins the log;
**never edit `.torve/tasks/<task>/log.yaml` by hand.** A malformed entry is
refused on the spot, with what to repair — fix it and run the command again.

**Record every divergence in one call** (S-0073/D-7).
The verb takes several rows: repeat the options, and the nth `--decision` goes
with the nth `--grade`, `--claim`, `--evidence` and `--action`. An optional
option — `--kind`, `--class`, `--proposal`, `--notes` — is given once per row or
not at all, so no row borrows its neighbour's. Any row the gate would refuse
leaves the whole call unwritten, so there is no half-landed batch to work out
afterwards.

```console
$ torve log divergence <task> --attempt 2 \
    --decision D-5 --grade ASSUMED --kind resolved --class discovery \
    --claim "..." --evidence "src/a.py:12 — ..." --action decided \
    --decision D-6 --grade ASSUMED --kind resolved --class discovery \
    --claim "..." --evidence "src/b.py:40 — ..." --action decided
```

**`torve log owed` is answered by the finishing check before you stop**
(S-0073/D-7). The Stop hook runs it against the files your diff actually
touches and blocks with the answer attached, so a bookkeeping poll before and
after the entries is a round trip that buys nothing. Run it yourself only when
you want the answer earlier than the stop:

```console
$ torve log owed <task> --touched <each file you changed>
```

It names the `LOCKED` decisions your changes touch that your log has not cited
yet — the same check the gate convicts on. A silent log over a governed file is
the single most common way an attempt is thrown away.

## What a scope names

**A scope naming a module names that module's test file.** This is the rule that
produced every measured lint refusal on this path (S-0067/D-6): a module changed
without its test is a coverage delta the battery reads as a regression, and a
test you cannot edit is a contract you cannot satisfy. If the contract's allow
list names `src/torve/x/y.py`, it names `tests/test_y.py` — and if it does not,
say so in the log before the gate says it for you.

**A scope never names the changelog** (S-0067/D-7). The entry is written
afterwards, from the landing records, once the work it describes has been
reviewed. The same literal in most of the board's allow-sets makes every pair of
tasks naming it clash and serialise, and an entry written from a diff describes
an implementation review may still change.

## Tests share the machine

**A test may not assume it is the only one on the machine** (S-0067/D-8). What it
creates on a shared daemon — a container, a volume, a network, a database, a
directory under a shared root — it finds again by its own name or its own root,
never by a literal another test also uses.

The suite runs in parallel, measured at 4.3x. A test that breaks this rule does
not fail as a flake that someone reruns; it fails as a bug in whichever test the
scheduler happened to interleave it with.

```python
# wrong: two tests racing for one name
container = client.containers.get("torve-test")

# right: the test's own name is the handle
container = client.containers.get(f"torve-test-{uuid4().hex[:8]}")
```

## Writing

User-facing strings — help text, docstrings typer renders, printed output —
carry no corpus coordinates: whoever runs the command has no corpus to resolve
them. State the rule in the string, cite the coordinate in a code comment.

An existing test is edited only under the contract's licence: adding a test is an
addition, editing one needs scope (S-0055/D-19). A green earned by weakening the
suite is a red.

## The acceptance

**Run the contract's acceptance commands as written, once, after your edits**
(S-0073/D-6). They carry their own quiet form, so there is nothing to invent: no
`tail`, no `grep`, no filter wrapped around them to make the output readable. A
filter has never changed a verdict, and the battery runs the same commands again
outside the session — the in-session run is for your own confidence, and one is
enough. A red is the exception: fix, then run once more.

## Finishing

Gates run outside your session, against the working tree you leave behind. Exit
0 when you consider the work complete.
