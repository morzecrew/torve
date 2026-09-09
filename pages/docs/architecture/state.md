# State and truth

Two questions, two answers, and the second one changed.

**What should be?** The repository. Specifications with graded decision
tables, task contracts, source code. A human writes it, a human reviews it,
and nothing an agent does becomes intent without passing through a commit
somebody signed off.

**What happened?** [The record](record.md). Attempts, verdicts, landings,
escalations, divergences, spend. It used to be a durable store holding run
rows beside a git history holding trailers, with each answering part of the
question and neither answering it whole.

![What holds what](../assets/diagrams/state.svg)

## The carriers

Several files still hold execution state. They are not competing answers —
each is a projection of the record with a reason to exist:

| Carrier | Holds | Why it exists |
| --- | --- | --- |
| the event log | every fact, append-only | the record itself |
| `.torve/telemetry.jsonl` | one row per attempt | what the cost, regime and quality projections read; **rendered from the event payload**, so it cannot disagree |
| `.wt/<task>.state.json` | the run the attempt loop is driving | the loop's own aggregate, and the only carrier a run without a store has |
| `.torve/tasks/<id>/contract.yaml` | what a task was asked to do | the authored artefact a human reviews and commits — and the *importer* the mint reads, not something dispatch consults |
| `.torve/tasks/<id>/log.yaml` | the task's divergences | written into the worktree from the record before each gate pass, and landed with the work so the diff carries its own account |
| landing commits | `Torve-Task` trailers | git's own record of completion — the one thing that survives a fresh clone with no store at all |

The rule that keeps them honest: **one record, rendered into carriers**.
Where two carriers hold the same fact, one of them is generated from the
other, and a test says so.

## What outranks what

- **A landing outranks everything.** A dependency is satisfied by a landing
  and by nothing else (S-0019/A-3, S-0019/A-4). A run that reached `ready` without
  landing has told the board nothing it can act on, and a fresh clone with
  no store still knows what landed because git does.
- **The board outranks the host.** Once a task is on the board, its state is
  the record's. The host's own run record decides only whether a contract is
  *minted* — a contract that ran and never landed stays off the board, since
  there is nothing true to record about it. After that, a person who
  requeues an escalation has said the thing that matters, and a file on one
  machine is not entitled to overrule it.
- **The contract outranks the corpus, at mint time.** A task inherits its
  decisions with the grades they had when it was minted. The corpus moving
  under a running task changes nothing about what that task was asked to do.
- **The minted contract outranks the file, once minted.** The mint records
  the contract, and that recorded copy is what dispatch reads and what the
  run is judged against. An edit to the file is not in force until the next
  pass re-mints it — which it does, recording the change, and never while
  the task is in flight. Before this, an edit between the mint and the
  dispatch took effect with nothing written down: the board said one thing
  and the gates enforced another.

## What still reads files, and why

The planning projections read the record now. `torve why`, `torve status`
and `torve context` each take `--partition` (and `--dsn`), and naming a
partition is what selects the record; naming none reads this repository's
own files. One rule is automatic and it runs in the safe direction: a record
that turns out not to hold the run falls back to the files, never the
reverse. A v1 run left a state file and no log, so an empty record means
*ask the files*, not *nothing ever ran*.

Three things still read files whatever you name, and each for its own
reason:

| Reader | Why it is still on files |
| --- | --- |
| the findings ledger | it reports each finding's severity **and its claim**, and the record carries a claim only for a blocker (`blocker.raised`). Moving it would silently drop the text an operator triages by |
| operator feedback | there is no event kind for it. Human minutes and rework are a `torve feedback` file and nothing else |
| the corpus | the corpus *is* files. The decision graph is imported into the record; the documents stay where a human edits them |

The report says which carrier answered. `torve context` prints a
`read from —` line above the first count, and the JSON envelope carries a
`sources` block naming the carrier per block. That line exists because the
numbers are correct about whichever carrier produced them and say nothing
about the other: on this repository the record holds 10 attempt rows where
the files hold 636, and `$10.62` of spend against `$242.87`. Both are true.
Only one of them answers "what has this repository cost".
