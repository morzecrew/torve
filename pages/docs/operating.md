# Operating the engine

What to type, what reads what, and which verbs no longer exist. The
architecture pages explain *why* the parts are separated; this one is the
surface an operator actually touches.

## Secrets

Configuration names an environment variable; the value lives in `.env`,
which is never committed. The console script reads that file before
dispatching, so the eight names this repository needs are set once rather
than exported into every shell.

The environment always wins. `TORVE_PG_DSN=... torve status` means what it
says — a file on disk does not overrule a name you typed. Only what the
shell has not set is filled in.

## The verbs

| Verb | What it does |
| --- | --- |
| `torve plan <spec>` | mint task contracts from an accepted document — deterministic, no model call |
| `torve source new <kind> <slug>` | file a source that is not a document — an audit, an incident, a review, an ask |
| `torve source list [--check]` | every filed source; `--check` also resolves the source each contract names |
| `torve intake "…" --source <id>` | draft against a request and record what asked — an audit, an incident, an ask |
| `torve intake "<request>"` | draft contracts from prose in a read-only sandbox; a human adopts or refuses |
| `torve adopt <task>` | the human signature: ids are minted here, under the engine lock |
| `torve run <task>` | one task, synchronously, sandboxed — the exit code carries the outcome |
| `torve manager serve <partition> --dsn …` | the resident manager: import contracts, claim one task at a time, execute, record |
| `torve fleet serve` | the same, over every repository the manifest names, one attention budget across all of them |
| `torve merge` | land ready candidates, serialized. Lands and stops — it does not push the base |
| `torve approve <task>` | approve a candidate's **current tip**; a push after it approves nothing |
| `torve reap` | sweep sandboxes, worktrees and finished run state. `--escalated` also discards escalations you have dealt with by hand |
| `torve status` / `why` / `context` | the reports. See below for which carrier answers |
| `torve manager return <task>` | send a reviewed candidate back for revision; `--note` briefs the next attempt |
| `torve manager board <partition>` | every contract this partition owns and what became of it |
| `torve gates run` / `check` | the battery, and the sabotage suite that proves a gate can fail |
| `torve spec check` / `list` / `amend`, `torve init` | the corpus surface |

**Retired, and not coming back by that name:** `torve tick` and
`torve fleet tick`. The standing loop they drove is abandoned (S-0019
S-0019/A-8); the manager runs its legs. Nothing scheduled the tick anyway.

## Which carrier answers a report

`why`, `status` and `context` read either the record or this host's files,
and **naming a partition is what selects the record**:

```bash
torve status                                   # this host's run-state files
torve status --partition morzecrew/torve       # the partition's board
torve context --partition morzecrew/torve      # tasks and attempts from the record
```

One rule is automatic, and it runs in the safe direction: a record that
turns out not to hold the run falls back to the files, never the reverse. A
run from before the record existed left a state file and no log, so an
empty record means *ask the files*, not *nothing ever ran*.

`torve context` prints which carrier answered above its first count, and
the JSON envelope carries a `sources` block. Read it before quoting a
figure. On this repository the record holds 10 attempt rows where the files
hold 636 — both true, and only one of them answers "what has this cost".

`torve serve` and `torve mcp` take the same two options and pass them into
every reader, per request — so a dashboard left running shows the board as
it is, not as it was at boot.

And `--dsn` is optional: it defaults to the DSN your configuration names,
which `.env` has already put in the environment. `--partition` alone is
enough.

## The corpus and its archive

A specification is a directory, `.torve/specs/S-NNNN/`, of five YAML
files split by who writes each: `document.yaml` (the author: the header
facts, the prose as typed keys — summary, motivation, current state,
goals, non-goals, a keyed design list, tests, docs, out of scope, risks —
plus at most eight extra sections, then alternatives and questions),
`phasing.yaml` (the author, read by the planner: the phasing and the
contract example), `decisions.yaml` (the author, stamped by the tool: the rows,
the invariants, the retired identifiers), `amendments.yaml` (written by
`torve spec amend` and `spec fix`, never by hand) and `execution/` (one
file per landing, `<task>-<attempt>-<instant>.yaml`, written once: what
each task found, so two candidates of one document never write the same
line). Every time the engine writes is one instant, `YYYY-MM-DDTHH:MM:SSZ`. A file that is absent is an
empty list; a section's heading is its key. Each file's first line names
its schema under `.torve/schemas/`, which `torve init` writes from the
models — with the contract's, the log's, the configuration's and the
manifest's beside them, and the schema line added once to `config.yaml`
and `gates.yaml` — so an editor with a YAML language server validates a
row as it is typed; `torve spec check` and `torve doctor` redden when one
lags. `torve init` also writes `.torve/.gitignore` with the patterns for
what torve alone writes (the task directory, the pack, the streams, run
state), appending a missing one below your own lines, so an adopting
repository ignores the right files without copying a block. That line is the only
comment a file may carry; any other is a check problem — a row that needs
a note needs a `rationale`. So is a section restating a typed list as a
fence or a table: the list exists once.

Every item has one identifier: the document is `S-NNNN`, and each of its
rows, invariants, questions, amendments, phases and prose sections is
`S-NNNN/D-n`, `S-NNNN/I-n`, `S-NNNN/Q-n`, `S-NNNN/A-n`, `S-NNNN/P-n` or
`S-NNNN/<key>`. Inside its own files a document writes its own items by
the local half alone (`id: D-3`); code, prose and logs cite the global
form, and `torve spec check` resolves every citation it finds in the
tracked source and docs. What stood before this grammar is answered by
`.torve/archive/identifiers.yaml`: `torve spec show` given an identifier in
the old shape names the row it became.

`.torve/specs/` holds what stands: the documents whose rows contracts
inherit. `.torve/archive/` beside it holds what once stood, every
directory and identifier kept, each document `superseded` and naming what
stands for it now. `torve spec archive NUMBER --superseded-by NNNN` is the
only way a document gets there, and it moves nothing unless the corpus
without the document checks clean. The corpus path is `specs.path` in the
runner's configuration; the archive and the schemas are its siblings.

Nothing inherits from the archive, and nothing about it is lost: `torve
spec show S-0044/D-12` answers from it and says archived, the check resolves a
citation into it, and the record holds every archived row as retired with
the archive as the reason. The next document number counts the archive,
so a number is never reused. There is no index file: `torve spec list` is
the index, and `torve spec render NNNN` writes a markdown page for a
person when one is wanted — the one markdown writer, never the source.

Every verb that changes a document writes it through one serializer:
`torve spec new "Title"` creates the smallest document that checks, `add-
decision` appends a row, `amend NUMBER --title T --row D-n --grade G`
(or `--path`, `--text`, `--retire --reason R`) records the typed diff with
the prior value on the amendment and re-stamps the row; a grade or paths
edited by hand afterwards is a check problem, a text edited by hand is a
warning that `torve spec fix S-NNNN/D-n "…"` re-stamps as editorial. A hand-
written document is legal as it stands — `torve spec fmt` reports what
differs from the serializer's form and writes nothing. `torve spec check`
also names rows whose declared paths match nothing in the tree; `--fix-
rot` retires them.

## Running a pass without spending

```bash
torve manager serve <partition> --dsn "$TORVE_PG_DSN" --no-dispatch --passes 1
```

Imports contracts and releases expired leases; claims nothing. This is the
pass to run after changing contracts, when you want the board to catch up
without a worker taking the first thing it finds there — which on a full
board is a real agent and real money.

## Getting told

An interrupt-class escalation is delivered once, by a leg of the manager's
pass, to whatever destination is configured:

```yaml
notify:
  adapter: webhook          # none | webhook
  url_env: TORVE_NOTIFY_URL # the variable holding the URL, never the URL
  attempts: 5               # deliveries before the queue parks one
```

`none` is the default and is a choice, not a blank: a repository that has
not picked a destination sends nothing on purpose. The queue is the log —
an escalation with no settled delivery recorded against it — so a manager
killed mid-page redelivers rather than losing it, and the escalation's own
event id rides the wire as an `Idempotency-Key` for a destination that
knows what to do with one.

Batch-class escalations never page. Paging on everything is how a pager
stops being read.

## The escalation queue is a pause

A pass mints nothing while this root's escalation queue is at
`loop.pause_escalations` (default 1), counting both carriers — a task
escalated under v1 and one the manager escalated are both a person's turn.
The queue may drain during a pause; it may not grow.

So an escalation nobody triages stops new work. That is the design, and it
is worth knowing before wondering why a board went quiet: check
`torve status`, resolve it with `torve manager resolve`, or discard the
footprint with `torve reap --escalated`.

## Unattended landing

A pass does not land anything by default. `torve merge` is the recorded
approval, a human act, and that is the whole of the safety story here — a
manager with the switch off behaves exactly as it did before the landing
leg existed.

```yaml
promotion:
  auto_merge: true    # off by default; arms the pass's landing leg
```

Armed, each pass runs the **same** serialized lane the verb runs, with the
same arguments this configuration already feeds it. It adds a caller, not
a policy: every refusal below still refuses, and nothing is gated twice.

| Setting | What it refuses |
| --- | --- |
| `promotion.require_ci` | a candidate whose branch tip is not green on the remote. Needs `scm.repo` to name that remote |
| `promotion.require_review` | a candidate whose producing run recorded no concluded review |
| `promotion.approvals` | a candidate short of approvals **on its current tip** — a push after an approval approves nothing |
| `promotion.quiet_window` | a candidate whose branch moved more recently than the window |

**The switch does not go on alone.** `auto_merge: true` with all four
criteria at their off values is refused when the configuration loads, naming
the field and the four settings that would answer it. The bar is one
criterion, deliberately: the refusal is there to catch the combination nobody
meant to write, not to pick a landing policy for you. This file is read once,
by a process that then runs unattended, so a warning would print to a
terminal nobody is watching.

`require_ci` needs a remote to be green on, and nothing is pushed from here
yet, so **review and approvals are the two criteria available today**. The
arming order that does not depend on anything being decided later:

1. Set `promotion.require_review` (and `approvals` if you want a second pair
   of eyes on the tip) with `auto_merge` still **off**.
2. Run `torve merge --dry-run` over the ready queue and read what the lane
   would refuse. Nothing lands; the refusals are the point.
3. Only then arm `auto_merge`, and read `torve doctor` once more.

`torve doctor` states what is armed before anything depends on it. One line
names which criteria a served manager would land without, and says when the
landing leg is off that no landing runs at all — a statement, not a verdict,
so it never turns doctor red. A second line appears only when a standing job
has been refused instantiation, with how many times and on what: a mechanism
blocked for a reason nobody has read is worse than one that is absent,
because the absence is at least visible.

A conflict is reported and left for a human. The lane never resolves one,
and it does not resolve one differently because a pass called it.

**A pause stops landing.** Unlike the notification relay — which delivers
what is already owed and so runs regardless — landing advances the
repository, and a pause is a statement that nobody has capacity to look at
what advancing produces.

**Landing is not publishing.** The lane moves the base locally and stops;
pushing it, and republishing the candidate, stay yours.
