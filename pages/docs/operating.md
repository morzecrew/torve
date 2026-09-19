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
| `torve brief <contract>` | print what dispatch settles before an attempt starts — the lint, the size, the rows, the pack, the battery. Refuses nothing; see below |
| `torve run <task>` | one task, synchronously, sandboxed — the exit code carries the outcome |
| `torve manager serve <partition> --dsn …` | the resident manager: import contracts, claim one task at a time, execute, record |
| `torve fleet serve` | the same, over every repository the manifest names, one attention budget across all of them |
| `torve merge` | land ready candidates, serialized. Lands and stops — it never pushes the base; under `promotion.landing: pull_request` it publishes the candidate's branch and opens its pull request instead of moving the base at all |
| `torve approve <task>` | approve a candidate's **current tip**; a push after it approves nothing |
| `torve reap` | sweep sandboxes, worktrees and finished run state. `--escalated` also discards escalations you have dealt with by hand |
| `torve status` / `why` / `context` | the reports. See below for which carrier answers |
| `torve ledger` | the record folded into rates, per seat, per changed line and per gate. See below for what each one divides |
| `torve eval …` | replay completed tasks as shadow pairs — a skill, a configuration — and land one record; `--report` reads the three-arm table back from the ledger and runs nothing. See below for what an arm is |
| `torve manager return <task>` | send a reviewed candidate back for revision; `--note` briefs the next attempt |
| `torve manager board <partition>` | every contract this partition owns and what became of it |
| `torve gates run` / `check` | the battery, and the sabotage suite that proves a gate can fail |
| `torve spec check` / `list` / `amend`, `torve init` | the corpus surface |

**Retired, and not coming back by that name:** `torve tick` and
`torve fleet tick`. The standing loop they drove is abandoned (S-0019
S-0019/A-8); the manager runs its legs. Nothing scheduled the tick anyway.

## Briefing a contract

A drafted contract passes a contract lint before a human signs anything. A
hand-minted one should get the same protection, and `torve brief <contract>`
is that: it runs what dispatch runs before an agent starts, and prints it
(S-0067/D-1) — before the work, rather than as a conviction after it:

```bash
torve brief T-0387                 # a task id, resolved the way `torve run` resolves one
torve brief contracts/draft.yaml   # a path, for a draft that has no id yet
```

The print is five things, in the order dispatch consults them:

- **the contract lint** — advisory here. The person reading the output has
  already signed the contract, so a red lint prints and the exit stays zero.
  Dispatch itself refuses a red lint, and `--lint-red` bypasses that refusal
  with the act recorded on the run.
- **the size estimate** — `ok`, `too_small` or `too_large`, with its reasons:
  the same verdict that routes a `too_large` contract to decomposition.
- **the rows your scope crosses that the contract has not inherited**
  (S-0067/D-2) — standing rows whose declared paths intersect the scope,
  which is what `decisions-reported` convicts on when the log stays silent
  over them. Named while the answer is still an edit, not a thrown attempt.
- **the context pack** — written to `.torve/context/`, replacing what stood
  there: the same files, built by the same function dispatch calls, where an
  attempt will find them.
- **the battery** — every gate with its axis, its state, and what it convicts
  on; the blocking axes named at the foot.

`--format json` carries the same fields. That the verb refuses nothing is the
design, not an omission: a hand-minted contract is already signed by the
person reading the output, which is the same ground the lint's own advisories
stand on.

## When a conviction routes a repair

A red battery routes one way by default: a fresh attempt from base, the same
prompt, the same contract, the previous reasoning deliberately not
privileged. Measured over this repository's record that is expensive — the
same gate convicted the next attempt of the same task 196 times across 28
tasks: `acceptance` 52, `layering` 41, `user-facing-text` 39,
`decisions-reported` 26, `scope` 14. A **repair** is the other route: the
same task's next attempt, starting from the tree that was convicted and
judged by the convicting gate as well as by everything its contract already
declared.

A repair is a mode of an attempt, not a role and not a task (S-0069/D-1).
Nothing is configured to turn it on and nothing new is minted: the attempt
ledger, the budget, the poison ceiling and the escalation reasons all count
a repair exactly as they count any other attempt.

**Which convictions qualify.** Four gates, fixed in the engine rather than
in configuration: `layering`, `scope`, `user-facing-text` and
`decisions-reported` (S-0069/D-5). Those are the checks that are pure
functions of the tree — a red from one names a property of the diff, not a
verdict on the approach, so the tree is worth repairing rather than
rebuilding. `acceptance` stays outside the set although it is the largest
repeat class: a suite that fails twice may be a tree worth keeping or an
approach worth abandoning, and only the ledger's per-gate repeat counts tell
those apart, so widening the set is a decision with a number behind it. The
red also has to be a conviction — a `shadow` or quarantined failure is a
fact and routes nothing — and the gate has to be one this repository's
manifest declares. When several qualify at once, the most severe axis is
repaired first, in the ladder's own order.

**What the acceptance becomes.** The convicting gate's own command, added to
everything the contract already declared (S-0069/D-3): a builtin gate
contributes the battery's per-gate verb — `torve gates run --only scope` —
and a shell gate its declared command verbatim. Added, never substituted. A
repair that leaves the gate red fails inside the attempt, which is the whole
saving, and one that clears it by breaking another gate still fails. The
contract on disk is untouched — the battery judges the task file as written,
and the addition is rebuilt on every red from the acceptance sealed at
dispatch, so it never accumulates and never leaks into an attempt that was
not routed as a repair.

**Where it starts.** From the convicted attempt's tree, not from base
(S-0069/D-4): the work that was right survives the mistake. Routing commits
whatever that attempt left, so the repair's worktree carries those commits
and an ordinary retry's does not:

```
torve(T-0390): attempt 2 convicted by scope

Torve-Checkpoint: T-0390 attempt 2
```

That commit is kin to the budget checkpoint and is not a landing: same
trailer, no execution record, nothing to mistake for a candidate. If the
commit itself fails the routing still stands and the engine records
`repair_tree_uncommitted` — the tree is the one the repair would carry
anyway, and an infrastructure failure must not replace the conviction.

**Once.** One gate earns one repair per dispatch (S-0069/D-6). A second
conviction on the same gate restores the contract's own acceptance and
routes exactly where it routed before, and the ladder that picks the next
tier is untouched either way. Without that bound a repair is a loop reading
its own failure as its input, and the poison ceiling stops meaning three
failures the same way. Reading a chain: the gate a repair was routed for is
stamped as `repair` on the attempt and rides the agent block of the attempt
record, so a repair is visible in the record rather than inferred from a
prompt — two convictions on one gate show one repair and then the ordinary
path.

**What a repair is handed.** `build_prompt` has a third mode beside
`continuation` and `revision` (S-0069/D-2): a block naming the gate that
convicted the previous attempt, the tail of its output, the paths that
attempt's diff touched and the inherited rows governing those paths, stated
as evidence the contract still outranks — data, not instruction, the same
footing the review threads a revision is handed stand on. The contract
itself does not narrow: same scope, same rows, same declared acceptance.
The mode and the pack function that fills it are both built; no dispatch
path passes a conviction to the prompt yet, so a repair attempt today is an
ordinary prompt run against the convicted tree with the convicting gate's
command in its acceptance.

## Where the working rules live

The rules an attempt works by — where the engine's facts are, which verbs
read and write them, what a scope names, the two rules about tests that
convict most often — live once, in `skills/working-rules/SKILL.md`
(S-0067/D-3), and reach every mode from that one file:

- A sandbox receives it as a declared equipment item: the implement, review
  and revert profiles under `.torve/agents/` name it, so it lands under
  `.torve/skills/` in the attempt's workspace — refusable at load and hashed
  into the regime, like any other piece of equipment.
- A session reads the same directory through its own skill root. In this
  repository that is a symlink into `skills/working-rules/`, so there is no
  second text to drift from the first.

The prompt an attempt carries keeps the bullet that points at the skill
(S-0067/D-4, LOCKED): your role's skills are under `.torve/skills/`, and
every `SKILL.md` there is read before code is written. The pointer stays in
the prompt because a skill nothing points at is a file; the rule bullets
beside it summarize the skill, and neither the summary nor the skill
outranks the contract.

That one file is where the three rules live that most often explain a
refusal a hand-minted contract collects: a scope naming a module names that
module's test file (S-0067/D-6); a scope never names the changelog — the
entry is written afterwards, from the landing records, once review has
settled what the change was (S-0067/D-7); and a test may not assume it is
the only one on the machine — what it creates on a shared daemon it finds by
its own name or root, never a literal another test also uses (S-0067/D-8),
which is a correctness rule once the suite runs in parallel, not a courtesy.

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

What the record derives reaches its reader through the pack or through a verb
— never through a committed file a gate diffs against a fresh render. A
committed artefact is a function of committed inputs: if a clean clone renders
it differently, it does not belong in one (S-0070/D-1, LOCKED).

## What the pack answers over MCP

`torve mcp` serves the read surface to a planning session on this machine,
over stdio. It registers four tools and all four are read-only
(S-0067/D-5, LOCKED): `context`, `show`, `why` — and `pack`.

`pack` takes a task id, and optionally one file name, and answers with the
very files a dispatched sandbox is handed on disk: `index.md`,
`decisions.json`, `gates.json`, `tests.json`, `attempts.json`,
`contended.json`, `schema/*.json`. They are built there by the same pure
function dispatch calls — the builder derives and the caller writes, so a
query materializes nothing: no file on disk moves because you asked.

Until that tool existed the pack was a sandbox fact: a session read it off a
worktree or not at all. Now every session gets the facts a sandbox is handed,
whichever harness it drove up in. Which is why the server still registers
nothing that writes: the refusals are the valuable part of a verb, and behind
a second interface they would only be a second, weaker copy of them.

## What each rate counts

`torve ledger` reads the attempt stream and divides it, printing per seat, per
changed line and per file in scope, and per gate. A denominator a reader has to
guess is one person's measurement and another's argument — three passes over
this record once counted 364, 536 and 252 attempts because each reader invented
its own denominator — so every rate the verb prints is named here with both
sides.

**What is counted at all.** A derived rate counts only attempts that ran a
model (S-0065/D-5, LOCKED). Four buckets are dropped, and their counts are
printed under the tables rather than assumed:

- *fake-adapter* — simulation traffic. It answers to no provider; twenty-four
  such records once went red on every gate in six minutes, and counting
  them lets fixture traffic convict a seat for free.
- *shadow replay* — a replay measures a regime and merges nothing, so it
  lands nothing and belongs to no landing rate.
- *not an attempt* — `engine`, `review` and `intake` rows, and rows with no
  agent block: one task's spend filed under another task's id.
- *unjoinable* — an attempt naming neither a base sha nor a head is a cost
  and a duration attached to nothing. The writer refuses new ones
  (S-0065/D-4); the rows already recorded are excluded from every rate and
  counted out loud.

**A seat, not a tier.** A seat is a tier *and* the image it was pointed at
(S-0065/D-2), printed `tier @ image`; no rate is ever pooled across seats.
One tier name has been aimed at three images over this record, and a figure
blending them describes nothing that exists. A seat the record cannot name
keeps its own row as `unnamed` rather than being merged into a neighbour.

**The denominator every landing rate shares.** A landing is a landing file
in the tree (S-0065/D-7). The other two readers reconcile against it: the
record's landing events are minted from those files, and the lane's
`lane_landed` carries the carrier's verdict rather than tallying beside it,
so a landing the carrier does not hold is visible as a disagreement rather
than as a quietly different number. Files are also the only reader that
answers in a clone with no event store, which is what makes a printed rate
reproducible from a checkout.

| Column | Numerator | Denominator |
| --- | --- | --- |
| `cost/landing` | every cost the seat's counted attempts reported | tasks the seat touched that have a landing file |
| `attempts/landing` | every counted attempt on the seat | the same landed tasks |
| `convictions/landing` | every blocking gate the seat's counted attempts failed | the same landed tasks |
| `duty` | agent wall time inside the seat's counted attempts | the elapsed span of the seat's own attempts, first to last |

The first three numerators are deliberately wider than their denominator:
they ride over everything the seat did, not only the work that landed. A
landing's price includes the abandoned attempts beside it — restricting the
numerator to landed tasks would price a fantasy and hide the spend that
made the landing possible. A conviction is a *blocking* gate that failed:
a shadow gate's red convicts nobody, and a gate `error` is the battery
breaking rather than the work being wrong.

`duty` is the odd one out: it divides by neither landings nor lines, and it is
the one rate whose denominator the specification left open. It counts agent
wall time inside attempts over the elapsed span of that seat's own attempts,
first to last — waiting between its attempts counted against it, waiting
for anyone else not. The rejected reading divides the same seconds by whole
task lifetimes instead, which measures the engine rather than the agent:
the record holds 37 agent-hours beside 296 further hours of idle inside
task lifetimes, so the two answers differ by an order of magnitude.
Because the choice is a choice, both sides ride beside the ratio — per seat
in the text footer, as `wall_time_s` and `span_s` in the JSON.

**The identity every cache figure rests on.** Cache-read tokens are not
something a model uses. They are the whole context, billed again on every
request:

    cache_read_total  =  Σ over requests of (context at that request)

so a cache figure answers anything only when both sides of that sum are on the
record — how many requests, and what each carried. An attempt now records both
sides of itself (S-0075/D-1, LOCKED): it scans its own trace and books the
per-request curve on the row's agent block — first, median, max and sum of the
input context each request carried, with the request count. Where the message
usages name cache fields the requests are read through them; where they name
only `input` — the case on three of the four seats the finding came from — the
curve is reconstructed from the message usages, and the row names which shape
produced it: `with-cache`, `input-only`, or `none` for a stream that carried no
per-request usage at all and so cannot be reconstructed. The reconstructed sum
is then held against the receipt's own final total and the row carries which
way that check fell (`matches_receipt`) — which is what makes the identity
behind a token figure verifiable per attempt instead of trusted. A request is
the message id the stream carries, never the event (S-0075/D-5): 72 assistant
events on one opus trace carried 47 distinct ids, 24 of them repeated up to
three times over one usage object, and counting events would have published a
sum 51% over the receipt; deduplicated, the curve closed against it exactly, at
5,276,251. The same trace on the modelstudio route closes neither way — raw
2,304,380, deduplicated 1,176,528, against a receipt of 2,853,036 — which is
what settled that route's `input` as not the full context (S-0075/Q-1), and a
curve that cannot close says so on its own row. The ledger's cache-read
numerator stays the receipt's own total either way: the curve is the attempt's
account of what that figure was made of, not a second figure to divide.

**The work-shaped rates beside the task-shaped ones.** A landing rate divides
by the task, and a twenty-line change and a four-hundred-line one enter the
same average — which hides the only thing that separates them: the same fleet,
the same week, 153k cache-read tokens per changed line against 16k
(S-0075/D-3, LOCKED). So after the seats table the verb prints a per-line
table, and after that a per-file one.

| Column | Numerator | Denominator |
| --- | --- | --- |
| `cache-read/line` | every cache-read token the seat's counted attempts reported | every changed line the seat's landed tasks committed |
| `wall/line` | agent wall seconds inside the seat's counted attempts | the same lines |
| `calls/line` | every tool call the seat's counted attempts made | the same lines |
| `$/line` | every dollar the seat's counted attempts reported | the same lines |

The lines are additions plus deletions read by `git diff --numstat` between
the newest counted attempt's own base and head, per path: a task's work is one
diff whatever it took to land, so it is sized from the attempt that finished
it, and a task that never landed contributes none — it committed no line,
though its seat's spend for it still rides the numerator. A line of landed
work is thus priced at everything it took to produce it, the same discipline
the landing rates above keep. The tool-call numerator is the count the harness
booked itself, or, where the stream named only the classified profile, the
call count of the burn profile that sorted the attempt's calls by what they
were for (S-0075/D-2). The
per-file table divides the same seat numerators by each path's own lines — an
attempt's spend cannot be attributed across the files it touched, so a path's
figure reads *what one line of this path's work cost if the seat had produced
nothing else* — and the path carrying the cost is the path that says so.

The absences print as absences here too: a numerator no attempt on the seat
reported prints a dash — or `unreported` where the column carries dollars —
never a zero; a burn profile never derived reads the same as a harness that
carried no token counts. And so do a diff that cannot be resolved, a change
that touched only binary files and a change that changed nothing: no
denominator, no rate, and not an infinity. And because a per-line
rate invites swelling the denominator — landing more lines makes every line
look cheaper — `attempts/landing` stays in the seats table beside them.

Per gate, the verb prints runs, wall time spent, convictions, and seconds
per conviction — the pair that says whether a gate is worth what it costs
to run, over the same counted attempts, so a gate's time follows every
exclusion above.

Per-gate wall times break mid-record, where this repository's own suite
went parallel (S-0071/D-1). Two of the thirteen gates run it — `acceptance`
and `coverage-delta`, 98% of all gate wall time — and both spell the
command `uv run pytest` while neither names a worker count, so the count
lives in the project's `addopts`: `-n auto`, one worker per core the
container can see — the host's sixteen today, because no CPU limit is set
on the sandbox. Measured on that machine: the suite from 128s serial to 30s
under `-n 8`, the pair from 267s to about 61s per battery. A wall time
from before that break is not the same gate's reading from after it.
`uv run pytest -n 0` still buys a serial run, and on a shared runner a
declared count beats whatever `auto` takes there.

Two absences print as absences. A seat whose provider carries no price —
a subscription seat genuinely has no per-token cost — reports `unreported`,
never zero (S-0064/D-12), because a zero averages; a rate with nothing to
divide by prints an em dash — not zero, not infinity. And the join from an
attempt to what it was agreed against is stated as a tally in the same
footer: contracts found in the working tree, found in git history at the
attempt's own sha — a task directory deleted under S-0056/D-10 costs a
`git cat-file`, not a fact (S-0065/D-3) — and found nowhere, which is a
fact about attempts that ran before their contract was committed.

Rates, not rows: `torve why` and `torve status` print the attempts and gate
runs themselves, and the exit code reports the read, not the rates'
fortunes — an expensive seat read successfully is a successful read.
`torve ledger --format json` is the same arithmetic as fields — cost
totals, landed-task counts, both sides of the duty ratio, each seat's changed
lines, token and call numerators and per-file entries beside the per-line rates
they divide, every exclusion tally — so a reader can check the division rather
than trust the print.

## What an arm is, how to launch one, and how to read the table

Everything else measured in this guide reads *inside* the apparatus: the
battery judges every attempt, `torve ledger` divides attempts by seat and
by gate, and every eval recorded before the arms varied one of the
configuration's settings within the apparatus — a skill against its
without-skill baseline, a candidate image against the incumbent. An **arm**
is the axis that removes the apparatus instead (S-0074/D-1, LOCKED). An
arm is named by what it runs without, and there are three:

| Arm | What it is given |
| --- | --- |
| `bare` | the task's intent, and nothing else — no inherited rows, no context pack, no working rules, and no battery. A sentence, and a checkout of the repository at the parent commit. |
| `gated` | the same prompt and the same worktree, and the battery judges what comes back. |
| `configured` | today: rows, pack, rules, battery, lane. |

Three arms, not two: a bare-versus-configured difference cannot be
attributed — the battery and the contract are separable and cost different
things — and the middle arm is what says which of the two halves earned its
keep. What an arm removes is a property of the prompt — the bare arm's
prompt is a fourth `build_prompt` mode beside revision, continuation and
repair, and the bare prompt's absence of rows, pack and rules is what the
test asserts (S-0074/D-2) — and of the replay, never something read back
out of a transcript.

**An arm is a property of a replay, never an edit to anything.** The arms
run as shadow replays: a completed task replays from its parent commit in
a truncated clone, and nothing merges — the record is the product, and a
green bare arm lands nothing. A bare run that *could* land its work is
refused before it starts: removing the battery is not a way to merge
(S-0074/D-3, LOCKED). The removal itself travels the same way — the bare
replay swaps its gate pass for one that runs nothing, and the manifest
every other attempt is judged by is never touched; its digest rides the
record unchanged, because a manifest with gates edited out would be a
different regime and the digest would be right to say so.

**What the bare arm's worktree carries** (S-0082/D-2). Not a sentence and an
empty directory: the replay clones the repository at the parent commit, so
the agent still opens every committed file — the source, the tests, this
site, the corpus under `.torve/specs/`, the gate manifest, the vendored
skills. What the arm removes is what the runner would otherwise *write*
into that tree: no projected contract, no materialised skill set under
`.torve/skills/`, no context pack. Nothing the tree already carried is
deleted — a deletion would land in the replay's own diff and be read as
work the arm did. So the attempt's record names the removals that were in
force, `removed: [prompt, contract, skills, context-pack]` and `battery`
beside them on `bare`, with an empty skill list rather than an unfilled
one: a reader of an arm's numbers can tell what the agent could still open
without rediscovering it. The `gated` arm carries the same worktree; only
its battery is back.

**Launching one.** Arm mode is the third mode of `torve eval`, and naming
neither of the other two's arguments is enough to select it (S-0082/D-8):

```bash
torve eval --task T-0390                          # all three arms, the task's own seat
torve eval --task T-0390 --arm bare --arm gated   # narrow to the arms named
torve eval --task T-0390 --tier executor.lean     # every arm on one named seat
torve eval --report                               # read the recorded arms, run nothing
```

`--arm` is repeatable and defaults to all three. It refuses at parse to
combine with a skill argument or with `--image` or `--variant`: one names a
comparison inside the apparatus, the other removes it, and no invocation is
both. `--report` is untouched by any of this.

Which tasks may be named comes from one read, so the refusal and the
pre-flight can never disagree about what is eligible (S-0082/D-3): a task
is eligible when the tree holds a landing that names a commit — that
commit's parent is where the replay starts — and the cost beside it is what
the task cost when it was done for real, summed from the live attempts in
the telemetry ledger. `--arm` without `--task` is refused before any spend,
and the refusal names the eligible tasks rather than only the missing option
(S-0082/D-9); so is a named task no landing names a commit for.

The seat is each task's own tier unless `--tier` names another, and every
arm of one invocation runs on the same seat — a difference between arms is
never a difference between seats (S-0082/D-10). Several tasks naming
different seats is refused until `--tier` picks one. `--tier` here names
the seat the arms run on, not a candidate under measurement.

Then, before the first replay, the verb prints what it is about to do: the
arms, the seat, the tasks, and what each of those tasks' recorded attempts
cost when they were done for real (S-0082/D-11). It adds no ceiling of its
own — an arm run is bounded by the contract's budget and the broker's
mid-run refusal, and by nothing here. This is the largest deliberate spend
behind one command in the engine, and it says so before it starts.

One record lands per invocation, `kind: arm-eval`, its `arms` map keyed by
the arm names and its rows the ones the reading below already reads
(S-0082/D-5). It carries no verdict beside those rows (S-0082/D-6): three
arms are not equally exposed to the same failures, so a boolean over them
is the mean this axis already refused. An invocation that raises partway
through lands the rows it has and says `complete: false` (S-0082/D-7) — a
three-replay run that dies on the third never reads like a finished
two-arm record.

**The rows name their arms, and the ledger is the reading.**
`torve eval --report` prints the table from the eval ledger alone — one
table per task, one row per arm, four columns: `arm`, `state`, `attempts`,
`cost usd`. It runs nothing and needs nothing else: no configuration, no
contracts, no agent. The `state` is the replay's own final state and
`ready` is what counts green. Records whose arms name none of those three
removals — the incumbent/candidate and with/without pairings of the
configuration and skill evals — measure things inside the apparatus and
contribute nothing to this axis. Every row carrying the arm that produced
it is what lets the comparison be recomputed a year later without the
person who ran it — the difference between a measurement and an anecdote.

**Read it per task, or not at all** (S-0074/D-4). The three arms are not
equally exposed to the same failures: a bare arm cannot break a corpus it
was never given, cannot trip a projection it does not render, cannot
violate rows it never inherited — and several of this repository's red
batteries caught its own author rather than an agent, which is a real cost
of the apparatus and also not what the apparatus is for. So the verb's
summary is a distribution — tasks grouped by which arms reached green,
`bare+gated+configured` down to `none` — never a mean, because a mean over
unlike tasks answers a question nobody asked. Each of these is the
finding, with its own row: a task where `bare` shipped what the battery
would have refused, and a task where all three arms landed identically.
And direction, never magnitude: a replay is a quasi-experiment, the arms
compare green first, then attempts, then cost, and one task's three rows
are not a verdict of the apparatus.

One rule belongs beside the reading because nothing can enforce it: tuning
a gate to make an arm look better is the one thing that voids the result
(S-0074). What is measured is the apparatus as it is, taken away whole.

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

`require_ci` needs a remote to be green on, which means a branch that was
pushed — `scm.open_pr` is what publishes each attempt's branch and opens the
task's pull request. With it off nothing leaves this machine, so **review and
approvals are the two criteria available**. The arming order that depends on
nothing being decided later:

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

## Landing as a pull request

What a landing *is* comes from two terms of `promotion`, and neither is ever
inferred from whether a remote exists or from whether the ready candidates
happen to share a document (S-0080/D-1, S-0083/D-1):

```yaml
promotion:
  landing: pull_request   # local (default) | pull_request
  unit: document          # task (default) | document
scm:
  repo: owner/name        # pull_request refuses to load without it
  open_pr: true           # likewise
```

Under `local`, **landing is not publishing**: the lane fast-forwards this
checkout's base and stops, and pushing it stays yours. Under `pull_request`
the base is not moved by this engine at all (S-0080/D-3). Every criterion,
probe and rebase above runs exactly as it runs locally, and in place of the
fast-forward the candidate's branch is pushed under lease and its pull request
opened or refreshed. The criteria decide whether a pull request is opened; the
forge's own rules govern what happens to it afterwards.

`unit` is read only where there is a pull request to be one per, so a `local`
landing ignores it rather than refusing it (S-0083/D-2) — a repository moving
between the modes edits one key, and a `unit` that survives the switch back is
inert rather than wrong.

| `landing` | `unit` | what one pass leaves behind |
| --- | --- | --- |
| `local` | either | the checkout's base fast-forwarded; nothing pushed |
| `pull_request` | `task` | one pull request per task, on `torve/T-NNNN` |
| `pull_request` | `document` | every phase of a document landed onto `torve/S-NNNN`, behind that document's one pull request |

A candidate whose contract names no document — an intake adoption, a standing
row's mint — lands by the task unit whatever `unit` says, and nothing infers a
document for it (S-0083/D-4). The document branch is named from the contract's
own `spec`, and cut once per document from the remote's `main` after a fetch,
at the first landing onto it (S-0083/D-5, S-0083/D-18): two phase-1 candidates
of one document cannot leave two branches, and the `S-` and `T-` namespaces
cannot collide.

**What you are expected to do is merge it.** That is the whole of a person's
part, and in `unit: document` it is one merge for a design of any number of
phases rather than one per phase (S-0083/D-7). Until you do, the engine leaves
the pull request alone except to keep it showing the tree the battery measured.

Closing one without merging is the other answer, and it is read as declining
the whole design: every task the document branch carries is abandoned, none is
re-queued and none escalates for triage (S-0083/D-11). The branch is kept
under every verdict — nobody deletes it (S-0083/D-14) — so the join from each
task to the commits its attempts wrote survives a squash merge that rewrote
them into one.

**What the pass after your answer does.** The engine does not watch the forge;
on a later pass it asks about the pull requests its own records say it has
open, before anything lands onto a branch again — one call per open document
rather than one per phase, and none at all for a pass holding none
(S-0083/D-12).

- **Merged** is the document's landing (S-0083/D-10): one record naming the
  squash commit and every task the branch carried, and not a second landing
  per task — each phase was recorded as landed, in the shape the local lane
  writes, when it landed on the branch (S-0083/D-6), and what the merge adds
  is which commit the document became.
- **Closed** abandons the carried tasks, as above.
- **Still open** is measured against `main` (S-0083/D-13). An unmoved base
  already shows the tree the battery judged, and the pass reports that it
  awaits a person. A base that moved under it — the ordinary case for a branch
  that lives days — is rebased in a disposable worktree, the battery re-run
  over the rebased tree, and the branch republished under lease; bounded once
  per base tip, so a branch against a moving `main` cannot rebase itself in a
  loop. A red battery puts the branch back where it stood. A conflict never
  resolves itself: the rebase aborts, the branch is untouched, and every ready
  task the branch carries escalates as `merge_conflict`.

An armed pass is handed the same publisher and the same read-back `torve merge`
is handed (S-0083/D-15), so none of this waits for somebody to type the verb.

**Phase after phase, unattended.** Under `pull_request` with `unit: document` a
task's worktree is cut from the document branch's tip when that branch exists,
and from the remote's `main` after a fetch when it does not (S-0083/D-9) — a
phase starts on the tree the previous phase's landing produced, the moment it
landed. The battery judges the attempt against that same tip, which keeps a
phase's diff its own work rather than everything the branch already carries.

**The pull request is the document's, and a draft until the last phase.** The
lane composes it from the records of every task the branch carries — each
contract's rows with their grades, the gate verdicts, the divergence entries —
and names the phases still to come from the document's own phasing rather than
an estimate (S-0083/D-8, S-0083/D-17); its title counts them ("· 2/3 phases").
While phases are still to come it is a draft, and the landing of the last phase
marks it ready: a person who merges a draft merges knowingly, since the phases
that land afterwards land on a branch behind `main`. Nothing turns a ready pull
request back into a draft.

**One piece is in the tree with nothing rendering it**: the morning report's
document counts. `torve night show` prints the night's landings, convictions,
endings and waits; the per-document opened, merged and closed counts
S-0083/D-16 asks for — and the pull-request counts they were to sit beside —
are folds with tests over the night's window and no renderer.
