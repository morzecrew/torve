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
| `torve decompose <task>` | split a contract the size estimate calls `too_large`: the drafter runs against the standing contract in a read-only sandbox, and its drafts await `torve adopt`, which mints the children and grows the parent into the integration task |
| `torve adopt <task>` | the human signature: ids are minted here, under the engine lock |
| `torve brief <contract>` | print what dispatch settles before an attempt starts — the lint, the size, the rows, the pack, the battery. Refuses nothing; see below |
| `torve run <task>` | one task, synchronously, sandboxed — the exit code carries the outcome |
| `torve manager serve <partition> --dsn …` | the resident manager: import contracts, claim one task at a time, execute, record |
| `torve fleet serve` | the same, over every repository the manifest names, one attention budget across all of them |
| `torve merge` | land ready candidates, serialized. Lands and stops — it never pushes the base; under `promotion.landing: pull_request` it publishes the candidate's branch and opens its pull request instead of moving the base at all |
| `torve approve <task>` | approve a candidate's **current tip**; a push after it approves nothing |
| `torve manager resolve <partition> <task>` | close an escalation and say how: `--resolution requeued` returns it to the board with its contract refreshed from its document's branch; `abandoned` takes it off and leaves the host state to read; `landed --sha` records a hand finish. See the escalation loop below |
| `torve reap` | sweep sandboxes, worktrees and finished run state. `--escalated` clears what an abandoned or hand-dealt escalation leaves behind — a requeue clears its own task's footprint as it goes (S-0094/D-3, S-0094/D-5) |
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

The print is five things, in the order dispatch works through them:

- **the contract lint** — advisory here. The person reading the output has
  already signed the contract, so a red lint prints and the exit stays zero.
  Dispatch itself refuses a red lint, and `--lint-red` bypasses that refusal
  with the act recorded on the run.
- **the size estimate** — `ok`, `too_small` or `too_large`, with its
  reasons, and it is advice. Dispatch does not consult it (S-0089/D-1): a
  queued implement or revert contract whose dependencies have landed and
  whose scope overlaps nothing in flight is dispatchable whatever its
  size, and `torve run` dispatches it with the verdict's reasons printed
  beside the run as a note. `--oversize` is accepted and ignored, and the
  next minor release removes it. What the estimate still decides is what a
  draft may look like, not what may run: a request whose draft comes back
  `too_large` needs a document, and a decomposition child may not be
  `too_large` (S-0089/D-5) — until a size model measured on the record
  replaces the static one. Splitting a contract when it is the right split
  is an operator's act: `torve decompose <task>`.
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

**A checkpoint survives a recut.** The same trailer marks the tree an
escalation leaves behind — from the review stage, or when the attempt halts
on a locked row (S-0086/D-2, S-0086/A-1). A rerun cuts the task's branch
back to base over it, except for exactly those two escalations, whose next
dispatch continues from the tree they checkpointed (S-0090/D-2). Before the
recut, the branch's tip is kept under `refs/torve/checkpoints/<task>/<sha>`
when the base does not already hold it, never pushed: `git log
refs/torve/checkpoints/T-0020/4d9dfec9` is the work the second attempt did,
however many reruns later.

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

**The third place a tree is committed short of a landing.** An escalation from
the review stage — a surviving blocker, an unreadable verdict, a broker
refusal — used to leave a gate-green tree in the worktree only, because the
candidate is committed after the review. It is now committed first (S-0086/D-2),
under the same trailer and with no landing:

```
torve(T-0390): attempt 2 escalated from review

Torve-Checkpoint: T-0390 attempt 2
```

A commit that fails leaves the escalation exactly as it was and records
`reviewed_tree_uncommitted` — the reason the run stopped is the review's.

**An unreadable verdict is asked twice.** No findings document at all, or one
the schema refused by field, buys one more ask of the same reviewer in the
same staged copy before anything escalates (S-0086/D-1); a readable verdict is
never re-asked. The review record carries `second_ask` and, when there were
two, `agent.trace_refs` for both sessions — so the ledger can count how often
a harness fails to produce a document, and a second unreadable answer
escalates as it did before with both sessions to read.

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

### Where each fact lives

Those reports answer from the record or from this host's files. The other
facts an operator asks about live each in the one place the act that makes
them true writes them:

| You want to know | Look at |
| --- | --- |
| whether a task landed, and the sha it landed at | its landing file, in the base tree or on the document branch's remote tip |
| which tasks a document carries, in landing order | the landing files on the document branch, or the pull request body they build |
| whether a document's pull request is open, merged, closed or a draft | the forge |
| whether a tip passed the completion battery | the `torve/completion` commit status on that tip, in the pull request's checks |
| what a review round covers — branch, target, findings, phases | the round's contract, under `round:`, on the board |
| what each attempt did, what it cost, which gates failed, who holds a task, what escalated | the record — the board, or a report run with `--partition` |
| attempt rows and engine health | `.torve/telemetry.jsonl`, a diagnostic stream (S-0099/D-10); five lane facts still read it until the follow-up to S-0099 lands |

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
`phasing.yaml` (the author, read by the planner: `after`, the phasing and the
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

<!-- the corpus coordinates belong in the corpus, not in a page a reader
     of the tree opens: S-0085/D-1, S-0085/D-2 and S-0085/D-5 govern this. -->
`after` in `phasing.yaml` is the other list of document ids, and it means a
different thing from the header's `depends_on`: `depends_on` is decision
inheritance, `after` is the landed tree this document's work builds on. A
document may name another in both, in either, or in neither. `torve plan`
turns `after` into contract `depends_on`: every task of a phase with no
predecessor inside the document waits on every task minted from each named
document. A named document that is not accepted, or that has no minted tasks
yet, refuses the plan by name; one whose implementation is already complete
has landed and adds no edge. `after` names documents, never phases — a
dependent waits for the whole of the one it names.

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

An amendment reaches the contracts the document already minted through
`torve plan S-NNNN --refresh`, never through a hand edit of a contract:

```bash
torve plan S-0079 --refresh                 # the table: what would be rewritten, and what is left alone
torve plan S-0079 --refresh --no-dry-run    # rewrite them
```

A phase an amendment *adds* to a document whose earlier phases are minted is
minted alone with `--phase`: `torve plan S-0002 --phase 6 --no-dry-run`. Its
`depends_on` edges point at the tasks the phases it names already have; the
document is otherwise refused as already minted, because what to do with the
existing tasks is a person's call.

The document is admitted and derived as a mint derives it, and each phase
already minted has its contract rewritten from the derivation — intent,
scope, acceptance, decisions, character, tier variant — keeping its id, its
edges and the header that names what minted it. No phase is minted: a phase
the document gained is `torve plan`'s, which still refuses a document any of
whose phases is minted. A task that is running, that has landed, or that a
document branch carries is left alone and named with the reason; an
escalated or reaped task with no landing is refreshed: a continued attempt
reads the contract as it stands at that dispatch, so the terms never carry
over from a checkpointed tree even when the tree does (S-0090/D-3). The
requeue in `manager resolve` is the one caller that reads the document as
its branch holds it rather than as this checkout does — the refresh an
escalated amendment used to need by hand is inside the resolve (S-0094/D-1;
see the escalation loop below). Each rewrite is recorded as
`contract_refreshed`
and stamped on the contract; under `--partition` the rewrite goes onto the
board through the same path a mint does.

## Running a pass without spending

```bash
torve manager serve <partition> --dsn "$TORVE_PG_DSN" --no-dispatch --passes 1
```

Imports contracts and releases expired leases; claims nothing. This is the
pass to run after changing contracts, when you want the board to catch up
without a worker taking the first thing it finds there — which on a full
board is a real agent and real money.

It is no longer a step every night needs. `torve manager serve --night` runs
the same import pass before it opens, so the queue the open reads and records
in `night.opened` includes contracts minted since the last pass; a queue still
empty after the import refuses the night as it always did (S-0096/D-3). Reach
for the pass above when you want to look at the board first, not to make a
night work.

## Getting told

An interrupt-class escalation is delivered once, by a leg of the manager's
pass, to whatever destination is configured:

```yaml
notify:
  adapter: webhook          # none | webhook
  url_env: TORVE_NOTIFY_URL # the variable holding the URL, never the URL
  attempts: 5               # deliveries before the queue parks one
```

An ntfy topic is the destination that needs no account and no code — the
delivery is one JSON POST and a phone subscriber reads it. The URL is a
bearer secret in practice, so the committed file names the variable and
the environment carries the value (S-0001/D-13):

```bash
export TORVE_NOTIFY_URL=https://ntfy.sh/<a-topic-only-you-subscribe-to>
```

`none` is the default and is a choice, not a blank: a repository that has
not picked a destination sends nothing on purpose. The queue is the log —
an escalation with no settled delivery recorded against it — so a manager
killed mid-page redelivers rather than losing it, and the escalation's own
event id rides the wire as an `Idempotency-Key` for a destination that
knows what to do with one. The body says what happened in one composed
`text` line — the task, the reason, the detail — beside the structured
fields a destination can key on, `task`, `reason` and `paused` among them.

Batch-class escalations never page. Paging on everything is how a pager
stops being read.

**A paused night is the exception, and it pages whatever the reason.**
While a served pass holds its pause (see the pause section below), the
latest undelivered escalation of each task holding the pause is relayed
whatever its interrupt class — `underspecified` among them — and the
notification says the night is paused (S-0094/D-4). What makes such a
halt worth reading at any hour is not its class but that work stopped,
and the page says so in words. A task holds the pause until it is
resolved, so a standing escalation pages once, on the first paused pass,
not on every pass after it.

## What an escalation names

Every ending that is not a landing carries a reason from a closed
vocabulary. The reason rides on the board row and in the record, and it is
what `torve run`'s exit code reports: a task handed to a person exits 2,
infrastructure that measured nothing exits 4, and a spent budget exits 5.
A red battery (1) and a refused configuration (3) are not escalations and
exit accordingly.

| Reason | What it says | Exit |
| --- | --- | --- |
| `locked_conflict` | an attempt halted on a `LOCKED` row (S-0096/D-1, amending S-0092/D-3): the tree is kept for inspection, the row needs a person's decision, and a retry is not the fix | 2 |
| `merge_conflict` | the landing lane's rebase conflicted; the branch is untouched and waits for a human — the lane never resolves one | 2 |
| `blocker_finding` | review ended with a blocker surviving | 2 |
| `killed` | an operator interrupted the run | 2 |
| `underspecified` | the contract needs three or more load-bearing decisions invented, or a halted attempt cited no `LOCKED` row, whatever class its entry chose (S-0096/D-1, amending S-0092/D-3) — a specification defect: amend the document, never retry. The loop is one amendment and one command: widen the phase on the document's branch, then `torve manager resolve --resolution requeued`, which refreshes the task's contract from the branch before the task goes back on the board (S-0094/D-1); a review round is re-scoped from the same branch the same way (S-0092/D-4, S-0094/D-2). See the escalation loop below | 2 |
| `stale_inheritance` | the document it was minted from was superseded after the mint: re-mint from the superseding one, or abandon | 2 |
| `gate_infrastructure_failure` | the battery broke rather than the work being wrong | 4 |
| `lease_expired` | a claim's lease ran out while its worker was gone; the process that died cannot release itself | 4 |
| `prepare_failed` | the seat's `prepare` command failed before the agent ran — an index that would not build, a cache that would not warm. Nothing about the model was measured, so nothing is convicted and no rung is selected (S-0062/D-7) | 4 |
| `seat_refused` | a seat failed in a way no retry can change, named in the seat's own words: the harness could not be executed (exit 126 or 127), or its envelope reported an API error with a 4xx status other than 429 and zero tokens in, cached and out. No attempt is counted, nothing is convicted or checkpointed (S-0089/D-2) | 4 |
| `budget_exhausted` | the task spent the budget its contract declares | 5 |
| `poison_ceiling` | attempts have reached the configured ceiling — and only attempts a model actually made count toward it (S-0089/D-2) | 5 |
| `cost_anomaly` | the broker refused requests past the run's budget | 5 |

The two rows above the budget ones are the pair that reads alike and is
not alike: `prepare_failed` is a seat that could not get ready, and
`seat_refused` is a seat that ran and was refused before any model turn.
A broken image, an over-long prompt or an unsupported model is named as
what it is in one dispatch, rather than spending the poison ceiling to
discover it; a 429 or a non-zero exit that carried usage is an ordinary
attempt and retries as one.

**A judged escalation continues from its tree.** A requeued
`blocker_finding` or a halt — `locked_conflict`, or the `underspecified` every
other halt now carries (S-0096/D-1) — starts from the checkpoint the
attempt left on the task's branch, not from base (S-0090/D-2): a review
that asked for one file's change costs that change, and a halt answered by
an amendment resumes where it stopped. The requeue reaches that tree by
itself: it first clears the escalated run's host footprint for the task it
resolves — its run-state file, its worktree, its sandbox — and keeps the
checkpoint, so a requeue never refuses over a state file still claiming
the task nor fails as `gate_infrastructure_failure` on what its own
escalation left behind (S-0094/D-3). `torve reap --escalated` is no
longer a step in this loop; it is the sweep for what a resolution
deliberately leaves, since `--resolution abandoned` clears nothing — an
abandoned attempt's worktree and diff stay readable, the checkpoint
commit's own message naming the reason the task stopped, until a person
says otherwise (S-0094/D-5). A landed task has nothing left to continue,
and a gate conviction still restarts from base. A continued attempt is an
ordinary attempt: it counts toward the ceiling and the budgets, its diff
and gates are measured against the original base, and it reads the contract
as it stands at that dispatch (S-0090/D-3).

## The escalation queue is a pause

A pass mints nothing while this root's escalation queue is at
`loop.pause_escalations` (default 1), counting both carriers — a task
escalated under v1 and one the manager escalated are both a person's turn.
The queue may drain during a pause; it may not grow.

So an escalation nobody triages stops new work. That is the design, and it
is worth knowing before wondering why a board went quiet: check
`torve status`, resolve it with `torve manager resolve`, or discard the
footprint with `torve reap --escalated`.

## The escalation loop is an amendment and a command

An escalation that convicts the contract rather than the work —
`underspecified` most often — used to be five acts: widen the phase on the
document's branch; edit the task's contract by hand, because
`torve plan --refresh` derived from this checkout's corpus while the
widened phase stood only on that branch; run `torve reap --escalated`,
because a requeue over the escalated run's leftover host state failed as
`gate_infrastructure_failure`; run `torve manager resolve --resolution
requeued`; restart the night. The loop is now one amendment and one
command (S-0094):

```bash
# amend the phase so the document's branch — torve/S-NNNN — holds
# it when pushed
torve manager resolve <partition> T-0231 --resolution requeued
```

The requeue clears the one task's escalated footprint, fetches, and
refreshes the contract through `refresh_document`, the same derivation a
mint runs, reading the task's document as the remote's copy of its branch
holds it after that fetch and as the checkout holds it when the remote has
no such branch; the rest of the corpus stays the checkout's, as always
(S-0094/D-1). Nobody edits a contract file by hand: a phase widened on the
branch reaches the next attempt as the text the pull request will merge,
which is what the branch is for — and a review round takes its re-scope
from the phasing in the same remote copy, not the checkout's ref, so an
amendment pushed from another checkout reaches a round served here too
(S-0094/D-2). The clear and the requeue are one act now, scoped to the task
the person resolves rather than sweeping every escalated run at once, and
the checkpoint a continued attempt resumes from survives it (S-0094/D-3) —
including the `underspecified` halt itself, which continues from that
checkpoint as a `locked_conflict` does (S-0096/D-1): the amendment widens the
contract the attempt worked against, so the work that stopped on it is kept
rather than re-cut from base.

What the loop deliberately keeps to a person is the two judgements:
widening a phase is an amendment, because the phase is the scope its
owner accepted (S-0092), and a paused night resumes only when its
escalation is resolved, because the pause is the statement that the board
needs somebody. With a destination configured it at least says so out
loud, whatever the reason's class (S-0094/D-4); with none, the night
waits in silence, which remains a choice, not an accident.

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
own `spec`, and cut from the remote's `main` after a fetch at the first landing
onto it — once per pull request, not once per document, since a merged or
deleted branch is cut again rather than landed onto (S-0083/D-5, S-0083/D-18):
two phase-1 candidates of one document cannot leave two branches, and the `S-`
and `T-` namespaces cannot collide.

**The branch is the remote's, and the pass reads it there.** A local ref is
not the branch: a document merged and deleted on the forge can leave one
standing here at its pre-merge tip. Before anything lands onto a document
branch the pass fetches with prune and works from `origin/<branch>`
(S-0091/D-1). A branch the remote has, whose pull request the lane's records
do not say merged, sets the local ref to the remote's tip: a phase lands onto
what the forge holds rather than onto what this checkout remembered. A branch
the remote no longer has, or one whose pull request those records do say
merged, is never landed onto again — nothing deletes it, but its local tip is
kept under `refs/torve/documents/<branch>/<tip>` (S-0083/D-14), the pass
records `lane_document_recut` with the reason, and the branch is cut again
from the remote's `main`. The next phase's pull request therefore carries
only what `main` lacks, not the history a squash merge already folded into
it, and the commits the old branch carried stay reachable from the ref set
aside.

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
  is which commit the document became. A merge does not end the document: a
  phase that lands after it is landed onto a branch cut again from the
  remote's `main` and opens a **new** pull request for the same document
  (S-0091/D-1) — the tasks the merged branch carried read as already landed,
  and what a reviewer of the new one sees is the phase after the merge, not
  that phase and the ones already merged beside it.
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

**A hand commit on the branch is kept, or the landing stops.** Commits the
remote holds on an open document branch that the lane did not make are the
branch having moved, not a branch to overwrite (S-0091/D-2). The lane lands
onto the remote's tip, so the candidate takes the moved-base path the lane
already has: rebased onto those commits in a disposable worktree, its
battery re-run over the rebased tree before anything is published, and a
conflict escalated to a person exactly as above. What you pushed to the
branch therefore survives the next landing or stops it — nothing resets it
away. And the push that publishes the branch leases on the exact commit the
lane fetched (S-0091/D-2): a commit that reached the remote since that fetch
refuses the push rather than being dropped, the local ref goes back where it
stood, and the landing is one the next pass makes again.

An armed pass is handed the same publisher and the same read-back `torve merge`
is handed (S-0083/D-15), so none of this waits for somebody to type the verb.

**Two workers are two processes.** Width is `torve manager serve … --worker w1`
and a second `--worker w2 --slot 1` on the same partition: each claims one task
a pass, a task whose scope clashes with one in flight waits, and the slot names
the second worker's own auth and cache volumes.

**A night opens by clearing its own name.** A worker restarted after a crash
would otherwise find its own stale claims standing in the way, so a night
opened under worker `w1` first releases every in-flight claim held under
that name, recording each release with the reason (S-0089/D-3). That is
safe because two live processes under one worker name are already a
misconfiguration: their claims and their slot's cache volumes would
collide. And a night that still finds nothing dispatchable no longer says
only that the queue is empty — the refusal names each claim still held,
with its holder, its age and when its lease expires, and counts the queued
tasks waiting on dependencies and the ones waiting on scope overlap: what
the operator is waiting on, and until when.

**A claim whose lease expires is not always a task to run again.** The
manager reclaims a claim whose holder went silent past its lease
(S-0044/D-6), and where the repository proves the task's landing — a lane
landing, or a landing file on the base — it records that landing at its sha
instead of releasing the task to the queue (S-0098/D-2): a killed worker's
finished task is not cut a second time, and the document branch it already
moved is not reset. A task with no landing proven is released as before,
and a person's requeue through `torve manager resolve` is untouched — that
is not a lease expiring, and a landed task a person requeued on purpose
still runs (S-0049/A-2).

**A task waits for its document's ready sibling to land.** A task is not
dispatchable while another task naming the same document is `ready` with no
landing recorded (S-0098/D-3): the lane runs first in every pass, so the
wait lasts until the sibling's quiet window ends and it lands, one idle
pass later. No task is cut from a document branch a ready sibling is about
to move — which is what kept a review round's acceptance failing on a fix
its sibling had not landed yet. The wait is not bounded: a sibling that
cannot land escalates out of `ready`, which ends it, and tasks of other
documents are unaffected.

**Phase after phase, unattended.** Under `pull_request` with `unit: document` a
task's worktree is cut from the remote's copy of the document branch when that
branch is on the remote, and from the remote's `main` after a fetch when it is
not (S-0083/D-9, S-0091/D-1) — a phase starts on the tree the previous phase's
landing produced, the moment it landed, and never on a local ref the remote
has moved past or deleted. The battery judges the attempt against that same
tip, which keeps a phase's diff its own work rather than everything the branch
already carries, and a dependency counts as landed when it is on that ref, not
when this checkout holds a commit that says so (S-0091/D-1).

**The pull request is the document's, and a draft until the last phase.** The
lane composes it from the records of every task the branch carries — each
contract's rows with their grades, the gate verdicts, the divergence entries —
and names the phases still to come from the document's own phasing rather than
an estimate (S-0083/D-8, S-0083/D-17); its title counts them ("· 2/3 phases").
A document whose header carries `change` is titled from it instead (S-0087/D-2)
— `👷 ci(fuzz): continuous fuzzing in CI · 1/3 phases` while phases are still to
come, `👷 ci(fuzz): continuous fuzzing in CI` at the last landing, so the subject
the squash merge takes is one line in the repository's own commit format with
nothing to edit. A task-unit pull request wears the same name with the phase's
title as the description (S-0087/D-3), and a document without the field is
titled as today, in every byte.
While phases are still to come it is a draft, and the landing of the last phase
marks it ready once the whole suite has passed over the finished branch — the
two paragraphs below (S-0093/D-1): a person who merges a draft merges knowingly,
since the phases that land afterwards land on a branch behind `main`. Nothing
turns a ready pull request back into a draft; a red suite only ever keeps one
from turning ready.

**A complete document runs the whole suite before it turns ready.** A phase's
acceptance is scoped to the tests its own phase names, and a fast-forward
landing proves the tree is the one those gates measured — and nothing more. So
the landing that leaves a document complete first runs the full battery over
the branch tip in a disposable lane worktree, before the pull request is
published (S-0093/D-1): the same re-run a rebased branch is judged by above,
under no one task's contract — acceptance taken from the manifest's own
fallback commands, under the manifest's own cap, because one cap sizes both
re-runs and a second setting would let them disagree (S-0093/D-5). It runs
once per completion: a landing that leaves phases still to come runs no
battery at all.

Green, the pull request turns ready. Red, the completing landing is
published all the same: the branch tip carries it, and the document's pull
request stays the draft its earlier phases opened, held there while the tip
carries a recorded red (S-0093/D-2, S-0098/D-4). `lane_document_gates_red`
records the task, the tip and the failing summary, so a person reading the
draft sees why it is one; and the round minted about the red is cut from a
branch carrying every phase, including the work the red was about, rather
than from a branch reset to before it. Where the leg reads recorded
findings, the red is also written to the stream as a review finding
on the last landed task: severity major, the failing gates and tests as the
claim, the battery's command as the evidence — exactly the shape the thread
leg's `record` source mints a round from (S-0093/D-3, S-0086/D-4). A
completion earns that one round, no more. While the round is outstanding the
landing waits, reported as `awaiting round`, rather than re-running the suite
every pass; when the round lands a change, or answers the finding without one,
the battery runs again, and a green rerun turns the pull request ready at
last — a flaky test clears itself there, and a break the round can fix is
fixed (S-0093/D-4). A second red reaches a person: the red landing
escalates as a blocker finding, leaves the lane, and no third battery runs. A
document with no landed task a round could be about — a one-phase document
whose first landing turns red — escalates on that first red instead. And the
round is the review leg's: `threads` off, or `record` missing from its
`sources`, means no leg reads recorded findings, so there is no round to wait
for — the red battery escalates the completing task `blocker_finding` at once,
records no finding, and reaches a person on every repository (S-0096/D-2).

**A phase counts from the tree, not from who landed it.** The carried list the
title and the body are composed from is the lane's records of landings on the
branch joined with the landing files the branch tip's tree holds (S-0091/D-3):
a phase finished by hand counts the moment its landing file is committed onto
the branch, and reads as landed in the title's count and in the body's list of
what the branch carries, rather than sitting under "Still to come" until a
person rewrites the pull request. The same rule closes the other direction —
`torve manager resolve <partition> <task> --resolution landed --sha <commit>`
refuses a commit whose tree holds no landing file for that task, naming the
task, the sha and the verb that writes one (`torve log land <task> --commit
<work commit>`), so a hand resolution cannot record a landing the tree does
not carry (S-0065/D-7).

**A reviewer's threads can be answered by the night too.** Off by default, and
a separate switch from `auto_merge`:

```yaml
threads:
  enabled: true              # off by default
  bots: [coderabbitai, codeant-ai]   # whose threads the engine may resolve
  rounds_per_pass: 1         # how many revision rounds one pass may mint
  findings_per_round: 15     # findings packed into one round of a wave
  review_wait: 45            # minutes a night waits for the head's review
  approve_comment: "..."     # posted once the wave is green; unset posts nothing
  sources: [forge, record]   # where findings come from; [forge] by default
```

`bots` is every login the night both waits for and may answer: the review wait
ends once all of them have reviewed the head, and only their threads does the
leg resolve (S-0097/D-4).

It refuses to load with `enabled: true` under any landing but `pull_request`
with `unit: document` (S-0084/D-5): the leg reads a document's pull request, so
a configuration that could only ever fail at the first thread fails while you
are standing at the terminal instead.

A served pass runs it after the landing leg and before the mint, so a round it
mints is on the board the same pass, and a pause stops it exactly as a pause
stops landing (S-0084/D-16). What it does on its turn, per open document:

- **Unresolved threads become findings**, grouped by what they anchor to rather
  than by who wrote them (S-0084/D-3) — three bots on one null check are one
  finding, one task, and one commit replied to all three.
- **One round becomes one implement task**, cut from the document branch and
  landed back onto it by the same lane that lands every other phase
  (S-0084/D-7); a round carries one finding, or the file-disjoint handful a wave
  packs together (S-0097/D-5). The leg never merges, never pushes and never
  force-pushes (S-0084/D-10); its attempts spend the night's budget like any
  other, and `rounds_per_pass` bounds how many it may start a pass.
- **A round is scoped by its document, not by its thread.** The scope is the
  phasing scope of the phases the finding's target task landed — or, for a
  thread on the pull request, which has no target task, the phases whose scope
  covers the file it anchors — plus the round's own log directory, so the
  divergence entries the round owes have somewhere to land (S-0092/D-1; this
  is what "the files the threads anchor" in S-0084/D-7 became). A round stays
  small enough to run beside the phases still in flight; where no phase
  answers the anchor, the anchored files and the tests they bring are the
  scope. And because the phasing is read from the document's branch tip — the
  text the pull request will merge — and not from the checkout, a phase
  widened on the branch by amendment reaches the leg on its next pass
  (S-0092/D-2).
- **The thread text reaches the attempt as evidence, never as instruction**:
  fenced inside the contract's intent, marked as a third-party claim about the
  tree and delimited by a per-run nonce (S-0084/D-8). A thread asking for
  anything but a change to the files in scope — run this, add this secret,
  change CI, merge, approve — is refused as injection before anything is
  minted, escalated to you, and never answered on the forge (S-0084/D-9). The
  collapsed `<details>` blocks a reviewer leaves under its verdict —
  CodeRabbit's analysis scripts are the standing case — and the HTML comments
  a bot leaves as hidden bookkeeping — cubic's `review-run` marker, CodeAnt's
  ids — are removed before that check and before the fence, so neither the
  work log nor the bookkeeping asks anything of the round and neither reaches
  the attempt; the verdict outside them is judged exactly as before
  (S-0092/D-5, S-0097/D-1, amending S-0084/D-9).
- **A thread is answered only after its round landed**, with a reply naming the
  commit the fix landed in or the recorded reason it was not applied
  (S-0084/D-12) — composed from the attempt's divergence entry, never from
  prose an agent wrote for the reviewer (S-0084/D-13).
- **A person's thread is replied to and left** (S-0084/D-11): the engine
  resolves a bot's thread and never yours, so the open threads on a document's
  pull request stay exactly the conversations a person is still having.
- **A recorded finding is answered once per round**, on the stream and as one
  keyed comment on the pull request (S-0086/D-5). The reason it was not applied
  is read from the attempt's log, or from the landing's execution record when
  the repository keeps its contracts on the record and has no root log — a
  round that changes nothing and says why lands as its execution record alone,
  and that is not an empty diff.
- **`serve --task` is the whole pass, and the name filters the claim as it
  filters the mint.** A worker started with `--task T-0028` imports, lands and
  dispatches that contract and no other (S-0098/D-1), so a night serving one
  round leaves every other green candidate on the board for `torve merge` or
  the next unfiltered pass. When the named task is not dispatchable — a
  dependency unlanded, its scope in flight, a sibling of its document ready —
  the pass claims nothing rather than falling back to the queue: an operator
  who names a task to step around a bad row is never served the bad row.
- **One round per finding.** A finding raised again after a landed reply
  already answered it escalates to you instead of being dispatched a second
  time (S-0084/D-14), so a night cannot spend itself arguing with a bot at the
  bot's own re-review rate.
- **A wave is a handful of rounds, not one round per finding.** The findings
  one head raises are minted together as rounds of up to
  `threads.findings_per_round` findings (default 15), grouped by file so no two
  rounds of the wave share a file. A file's findings are never split, so a file
  carrying more than the cap makes an oversize round rather than a split, and
  `rounds_per_pass` still bounds what one pass mints (S-0097/D-5).
- **A thread on a file the engine writes is answered, never composed.** A
  landing record under a document's `execution/` or an `AGENTS.md` the spec's
  projection writes (S-0054) mints no round and escalates nobody: the thread is
  not a change anyone may make on a comment's say-so. The leg replies once with
  a fixed text naming what writes the file and where a fix belongs, and resolves
  the thread when its author is in `threads.bots`; every other `.torve/` anchor
  and everything under `.github/` stays refused as injection (S-0097/D-2).
- **A refusal reaches you once.** `lane_thread_refused` records the thread ids
  it refused and the comment count each carried; a later pass neither refuses
  nor escalates a thread it already refused unless the thread gained a comment
  since (S-0097/D-3).
- **The leg asks a bot for its approval once.** When every thread a login in
  `threads.bots` opened on a head is resolved and the head's checks are green,
  the leg posts `threads.approve_comment` on the pull request, keyed by the head
  sha so it lands once per head; unset posts nothing (S-0097/D-7).

**The night waits for the review wave it published.** A night whose lane
published a document pull request out of draft is not drained while that head's
review wait runs: the bots review what leaves draft, and the leg waits until
every login in `threads.bots` has reviewed the head, or `threads.review_wait`
minutes (default 45) have passed since it was pushed, whichever comes first.
There is nothing to wait for when the leg is off, when no bot is configured,
when the pull request is still a draft, or when the forge reads no push time to
bound the wait by. While the wait runs the leg still answers what already
landed, but mints no new round — so the leg sees the review wave the night
produced instead of a drained night leaving it to a person, and `PrInfo` reads
the logins that reviewed the head and the head's check state in the same call
as its threads (S-0097/D-4).

**A wave costs one push.** While the rounds minted from one head's wave are
queued or running, the lane lands each onto the document branch without
publishing; the landing that leaves none outstanding — the last round done, or
a round whose hold an escalation released — publishes, so the wave reaches the
forge as one push, one re-review by the bots and at most one dismissed approval
(S-0097/D-6).

**The tier's own findings can be a source too.** `sources` names where the leg
reads from, and defaults to `[forge]` — so a configuration written before this
existed changes nothing. Adding `record` reads the task-gated review records of
the tasks an open document branch carries, and turns each finding nobody has
answered yet into the same shape a thread produces (S-0086/D-3): anchored by
its evidence's leading citation, or — when its evidence is a command rather
than a line — by the files its target task's diff touched, as one finding for
that target (S-0086/D-4). Record and forge findings group together, so a bot
and the tier flagging one line are one finding and one round. `record` is
refused at load under any landing but a document's pull request, the leg's
switch off or on.

A finding whose citation lies outside the document's phasing scope mints
nothing and reaches you by name, as an injecting thread does (S-0086/D-4) —
the scope it is judged against is the phasing as the document's branch tip
holds it, so widening a phase on the branch is what admits a finding the
checkout's phasing refused (S-0092/D-2). The branch the leg reads is the
remote's copy after the pass's fetch, not this checkout's ref: a phase
widened and pushed from another checkout reaches the leg before it reaches
this one (S-0094/D-2). And because a recorded finding was
never a thread on the forge, its answer is written to the stream as
`review_finding_answered` — the commit the round landed in, or the recorded
reason it was not applied — and said once as a comment on the document's pull
request, never again (S-0086/D-5).

**A round that halts on its scope gets one widening, and then it is your
turn.** The scope is a bound, not a guess about the fix: an attempt whose fix
needs a file outside it halts and leaves a divergence entry of class
`spec-gap` rather than reaching past the bound. Such a halt cites no `LOCKED`
row, so it escalates `underspecified` and not `locked_conflict` — as every
halt that cites no such row now does, whatever class its entry chose
(S-0096/D-1, amending S-0092/D-3) — and that is exactly the escalation the
retry below acts on; the old name sent operators hunting for a row nobody had
touched. The
leg then makes the one widening that needs no decision: on its next pass it
re-derives the round's scope from the union of the whole document's phasing —
the bound you accepted when you approved it, plus the round's log directory —
and queues the round again, recording the act on the stream as
`lane_round_requeued`. Once per round (S-0092/D-1). A second such halt stays
with you, and the path is the escalation loop above, shortened: amend the
phase on the document's branch — the phasing is what the round reads, and a
round's contract lives in the record, so an edit to the contract file
itself reaches an attempt only by accident — then
`torve manager resolve <partition> <task> --resolution requeued`, which
re-derives the round's scope from the phasing as the branch holds it at the
requeue before the round goes back on the board (S-0092/D-4). The requeue
reads that phasing from the remote's copy after its own fetch (S-0094/D-2)
and clears the halted round's run-state file, worktree and sandbox first
(S-0094/D-3). A spec-gap halt is one of the continuations now (S-0096/D-1):
the next attempt resumes from the checkpoint the halt left — the amendment
re-cuts its scope, not its work — and that checkpointed tree is kept for
reading under `refs/torve/checkpoints/`, as any recut keeps it.

**A round's own review opens no round.** `record` reads no finding from a
review whose target is itself a round the leg minted on this branch —
anything the stream records as a `lane_review_task` event (S-0090/D-1). The
round is still reviewed, and its review still blocks that round's landing
on a blocker as any task's does; what it finds below the blocking grade
stays on the stream as the review row it already is — never a thread, so
never a round. The rounds a document gets are thereby bounded by the
findings on its phases, because a round's check ends the chain rather than
feeding it. The forge source is unchanged: a person's thread on a round's
file is still a round.

**The morning report counts what the night left on the forge.** `torve night
show` prints the night's landings, convictions, endings and waits, and then the
pull requests opened, merged, conflicted and closed inside the window
(S-0080/D-14) beside the documents opened, merged and closed (S-0083/D-16) —
folded from this host's own stream, where the lane records its landings and
read-backs. Beside those, the review threads the window saw, the rounds minted
from them, and the threads answered, refused and escalated (S-0084/D-17):
whether the leg removed your thread work or merely moved it is readable in the
morning, which is the measurement it is accountable to.
