---
id: "0005"
title: Review as a run
status: accepted
implementation: partial
depends_on: ["0003", "0004"]
informed_by: []
supersedes: []
superseded_by: null
amended_by: ["A-32", "A-41", "A-75", "A-78", "A-79", "A-114", "A-135", "A-136", "A-137", "A-139"]
retired: ["D-5.5"]
owner: Lev Litvinov
description: >-
  Independent automated review as a second run role: isolation rules, the finding contract, calibration, and replacing third-party PR reviewers.
schema_version: 1
---

# RFC 0005 — Review as a run

- **Implementation state:** phases 1–3 executed 2026-08-22 (T-0038 finding/role mechanics, T-0039 the review run, T-0040 degraded mode and the seeded corpus — measured green live with a deepseek reviewer); the forge leg executed 2026-08-23 (T-0053 — `torve review pr` as the §4 trigger: skip rules, one review per head, Torve-Task trailer mapping or degraded input, findings posted back by the runner through the SCM port; demonstrated live on the lab against an organic pull request no agent wrote). T-0088 executed 2026-08-26 (a capture replaces the record — including with nothing: an empty capture clears the stale briefing and reply addresses an earlier revision round left; the T-0056 watch item closed). Outstanding: the §7 replacement sequence — the incumbent exists (CodeRabbit on the lab) and the step-1 two-week comparison window opened 2026-08-26; the ledger so far reads complementary classes, not redundancy
- **Scope:** Independent automated review, implemented as a second role of the same run pipeline rather than a special case; its isolation rules, output contract, trigger paths, calibration, and how its quality is measured. Covers replacing a third-party pull-request reviewer. Excludes human review policy and promotion rules, which belong to RFC 0006.
- **Inherits:** D-2 (models produce data, config decides consequences), D-3, D-4, D-22 from RFC 0001

---

## 1. The reframing

Earlier drafts treated review as "a gate that happens to call a model", and then had to explain why that did not violate D-2. The explanation was strained, and the strain was a signal.

**Review is not a gate. It is a run with `role: review`.** Same pipeline, same sandbox, same lease, same `Attempt` record, same telemetry. What differs is the input, the output type, and the write permissions.

| | `role: implement` | `role: review` |
| --- | --- | --- |
| Input | task, decisions, worktree | diff, task, decisions, gate results |
| Output | a diff | `Finding[]` |
| Workspace | read-write | **read-only** |
| SCM | may push its branch | **nothing** |
| Produces | code | a document |

This collapses a special case into a parameter, and it makes review inherit everything already built: budgets, poison ceilings, cancellation, cost accounting, trace references, escalation. Nothing about review needs its own lifecycle.

It also settles D-2 cleanly. A review run invokes a model, and the model's output is **data** — findings with severities. Whether a finding stops the work is decided by configuration, not by the model. The model never causes a transition.

## 2. What makes review independent rather than ceremonial

1. **A different model from the author's.** Same provider and same model shares the author's blind spots. Cross-model is a condition of value, not a refinement.
2. **A clean session with no access to the author's history.** The reviewer receives the diff, the task, the inherited decisions and the gate results. It does **not** receive the author's session trace or reasoning — otherwise it audits an argument instead of a change.
3. **A sandbox with no repository write access.** The reviewer physically cannot fix-and-approve. It produces a document, nothing else.
4. **Structured output**, not prose (RFC 0001 §3, `Finding`).
5. **`evidence` is mechanically verified.** A finding whose quoted evidence cannot be located in the diff or a gate log is discarded before a human sees it. Same mechanism as the execution log's evidence check — one implementation, two consumers.

   **Be precise about what this buys.** It eliminates fabricated *coordinates*, not fabricated *claims*: a model can cite a real `file.py:42` and describe something that is not there. The filter is cheap and worth having, but the only real defence against the second failure is measurement — the seeded-defect corpus and blocker precision in §6. Any wording suggesting this check removes hallucination is overstated.

Consequence is config: any surviving `blocker` → `escalated` with reason `blocker_finding`; everything else becomes comments for a human to weigh.

**The runner posts the comments, not the agent.** Findings come back as data and the runner renders and posts them through the `SCM` port. The reviewer keeps no forge credential at all, which is D-4b applied where it is easiest to forget.

### 1.1 The review contract

*(Added 2026-08-22, with charter A-11.)*

A review is minted as a task, using the same contract shape with a different role:

```yaml
id: T-0143
role: review
targets: [T-0142]
intent: |
  Review T-0142's diff against its contract and inherited decisions.
scope:
  allow: []                    # writes nothing
decisions: <inherited from T-0142>
budget: { iterations: 1, wallclock: 10m, tokens: 120k }
tier: reviewer
```

`targets` already exists for `role: revert` (RFC 0010), so a third role needs no new mechanism — the contract shape is parameterised by role and that is all.

**A review task has no `acceptance`.** Its output is `Finding[]`, not an exit code. This is a property of the role, not an omission: `implement` is judged by green commands, `review` by findings whose evidence resolves. The `acceptance` gate is skipped for this role rather than passed with an empty list.

**Who mints it.** `torve plan` mints `implement` tasks from an RFC. The **runner** mints the review task when its target reaches `gated`. The planner has no knowledge of review and needs none — review is a consequence of execution, not of specification.

## 3. One implementation

*(Rewritten 2026-08-22, with charter A-11; the section previously offered an `Inference`-port default beside a sandboxed harness, and D-5.5 with it — both removed, identifier retired per D-A.4.)*

The reviewer runs through `Agent`, like every other run. A reviewer reached through a separate port stops being a run: no sandbox, no contract, no budget, no cancellation, no `Attempt`, no `trace_ref`, no role-scoped skills, no place in telemetry — the special case D-5.1 removed, back wearing a port. Its adapter is chosen by `tier: reviewer` in the usual way, and the cross-model requirement (D-5.1, §2) is met by pointing that tier at a different vendor than `executor`.

## 4. Triggers

A board-driven review runs after `gated`. Replacing a third-party pull-request reviewer needs one more path: **review on pull-request open and update**, including on pull requests no agent wrote.

```yaml
review:
  on: [task_gated, pr_opened, pr_synchronized]
  skip_if:
    - draft
    - changed_files: 0
    - author: dependabot
  debounce: 90s        # collapse rapid pushes into one review
```

A pull request without a task contract reviews with a degraded input — no `scope`, no inherited decisions — and the reviewer is told so explicitly, so it does not invent a specification to check against. Spec-drift findings are simply unavailable in that mode, which is honest: drift is meaningless without a spec.

Debounce matters more than it looks. Without it, a developer pushing three fixups in two minutes pays for three reviews and gets three near-identical comment threads — the single fastest way to make people mute the bot.

*Execution note 2026-08-23 (T-0053):* the trigger landed as `torve review pr N` — the forge's own event delivery (a CI job on `pull_request`, a webhook handler, or an operator) invokes it; the engine holds no resident event consumer. The 90s debounce is therefore translated, not implemented literally: one review per head, through a `pr-reviews` ledger — rapid pushes collapse into whatever head is current when the trigger fires, and a head reviews at most once. `skip_if` landed as always-on draft/zero-changed-files skips plus a `skip_authors` list; the timed debounce becomes meaningful only with a resident consumer. The trigger never mutates task state — blockers on a task-gated run escalate on that path; this one reports to the pull request.

## 4a. The revision loop *(added by A-32, 2026-08-24)*

Review that cannot change the next attempt is ceremony. When a commander
re-queues a task with `retry`, the apply step — before it deletes the
stale branch — captures two things into an engine record beside the
contract: the previous candidate's diff, and the review threads its pull
request accumulated from allow-listed logins (`review.feedback_from`; an
empty list turns the loop off, and a stranger's comment never reaches an
agent). The re-run starts in a fresh worktree, but its prompt carries the
record: your previous attempt produced this, reviewers said that —
revise, do not re-invent.

Threads travel **verbatim and whole**: reviewer formats are incompatible
(one bot's severity is an emoji header, another's is an image's alt
text), so parsing them is an adapter zoo that rots with every vendor
redesign — the agent reads markdown. Replies ride along because they
carry resolution ("fixed in …"), and each comment stays attributed so a
later eval can ask which reviewer earns its seat. Only `path:line`-
anchored review comments are captured, never top-level summaries; the
record is size-capped and a truncation is written into it, not silently
absorbed. An escalation with no branch or pull request captures nothing,
honestly.

Containment is the existing three layers, unchanged: the allow-list at
intake, the feedback quoted as untrusted review data under a contract
that still governs, the full gate battery and the human's sha-bound
approval on what lands. The feedback channel can steer an attempt; it
can never steer a landing. And revision spend stays behind the human
act: nothing auto-retries because a bot commented.

## 5. Calibration

The failure mode of every automated reviewer is noise, and noise is fatal: a reviewer that is ignored is worse than none, because it consumes budget and creates the appearance of coverage.

**The reviewer must be able to say "clean".** If the prompt demands findings, it produces them, always. This contradicts `self-audit`, which holds that on a substantial branch, finding nothing indicates a shallow audit — and both are right about different things. `self-audit` describes an author excavating their own work, where "nothing" is suspicious. This reviewer sees a small diff after green gates, where clean is the normal, frequent outcome. **State the difference in the prompt explicitly**, or it inherits the wrong calibration and manufactures work.

Severity discipline:

- `blocker` — the change is wrong, unsafe, or contradicts a `LOCKED` decision. Stops the run.
- `major` — a defect a reviewer would insist on before merge.
- `minor` / `nit` — preferences. Rate-limited to a small number per review, or dropped entirely once telemetry shows they are never acted on.

## 6. Measuring the reviewer — the actual advantage

Because review is a run, it produces an `Attempt`: cost, duration, model, `config_hash`, and its findings. That makes reviewer quality a measured quantity rather than a vibe.

| Metric | Meaning |
| --- | --- |
| Blocker precision | share of blockers a human agreed with after triage |
| Escaped defects | defects found in human review or production that the reviewer saw and missed |
| Comment action rate | share of non-blocking findings that led to a change |
| Cost per review | against the same model and prompt version |
| Noise rate | findings discarded for unlocatable evidence |

**This is what a third-party reviewer cannot give you.** Its prompt, model and thresholds are not yours; you cannot A/B two configurations on your own repositories, cannot correlate its findings with your escape rate, and cannot tune severity to your team's tolerance. Here, `config_hash` makes every change to prompt, model or thresholds a comparable regime.

**Reviewer regression corpus.** Symmetrical to the gate sabotage suite: a set of pull requests with known seeded defects — an off-by-one, a swallowed exception, a `LOCKED` decision quietly contradicted, a test weakened to pass. The reviewer must catch them, and a change to prompt or model that drops one is a regression. Without this, prompt tuning is guesswork with a good feeling attached.

## 7. Replacing the third-party reviewer

Not a switch — a sequence, and the third-party stays on until the numbers justify removing it.

1. **Shadow.** Both run; the third-party's comments post, yours are recorded but not posted. Compare on the same pull requests for two weeks.
2. **Post non-blocking.** Yours posts as comments, cannot block. Watch the comment action rate.
3. **Blocking.** Blockers escalate. Third-party is muted but kept enabled.
4. **Remove.** Only once blocker precision and escaped defects are at least as good, over a real sample.

Steps 1–2 cost only tokens and are the whole basis for deciding whether step 4 is honest.

## 8. Risks

- **Noise, and the muting it causes.** Mitigated by evidence verification, severity limits, debounce, and permission to be clean. Watch the action rate; if it falls below a threshold, cut severities rather than tuning prose.
- **Cross-model requirement versus budget.** The reviewer is a second model on every change. If the tiering budget cannot carry it, review non-trivial diffs only — but say so in configuration rather than letting it silently degrade.
- **Reviewing without a spec.** On pull requests with no task, the strongest finding class is unavailable. Do not compensate by letting the reviewer infer a specification; an inferred spec produces confident findings against a standard nobody agreed to.
- **Review debt.** Automated review does not reduce the human bottleneck, it feeds it. RFC 0006 owns that.

## 9. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-5.1 | `LOCKED` | Review is a run with `role: review`, not a distinct subsystem | `src/torve/application/review.py` `src/torve/application/runner.py` | Inherits budgets, cancellation, telemetry; reversing duplicates all of it |
| D-5.2 | `LOCKED` | The reviewer works in a disposable copy of the target worktree and holds no forge credential; the diff under judgment is composed before the copy exists, nothing written in the copy survives the review, and the runner posts comments (reworded by A-78; was: read-only workspace) | `src/torve/application/review.py` `src/torve/adapters/runtime/**` | An agent that can fix-and-approve is not a reviewer |
| D-5.3 | `LOCKED` | The reviewer never receives the author's session trace | `src/torve/application/review.py` | Otherwise it audits reasoning, not the change |
| D-5.4 | `ASSUMED` | Findings with unlocatable evidence are discarded automatically | `src/torve/application/review.py` `src/torve/gates/decisions_reported.py` | Shared with the execution-log check; remove if it discards true positives |
| D-5.6 | `LOCKED` | A seeded-defect corpus gates every prompt or model change | `.torve/review-corpus/**` `src/torve/cli/review.py` | Prompt tuning without it is guesswork |
| D-5.7 | `ASSUMED` | Third-party reviewer removal requires shadow-mode numbers, not preference | — | Four-step sequence in §7 |
| D-5.8 | `ASSUMED` | Reviews on pull requests without a task run in degraded mode and are told so | `src/torve/application/review.py` | Prevents invented specifications |
| D-5.9 | `LOCKED` | Review is minted as a task with `role: review` and `targets`, sharing the contract shape | `src/torve/domain/task.py` | A third role must not require a new mechanism |
| D-5.10 | `LOCKED` | A review task has no `acceptance`; the gate is skipped for the role | `src/torve/gates/acceptance.py` `src/torve/domain/task.py` | Its output is findings, not an exit code |
| D-5.11 | `LOCKED` | Review tasks are minted by the runner at `gated`, never by the planner | `src/torve/application/runner.py` | Review follows execution; the planner would have to predict it |
| D-5.12 | `ASSUMED` | Retry captures revision feedback before the candidate is superseded: the previous candidate's diff and the pull request's `path:line`-anchored review threads from `review.feedback_from` logins — verbatim, whole threads, attributed, size-capped with recorded truncation; an empty allow-list turns the loop off. Added by amendment A-32 2026-08-24. *Amended by A-37 2026-08-25 (registered on RFC 0010, D-10.10): the branch is no longer deleted at requeue — the next attempt's leased force-push supersedes it; capture-first stands unchanged* | `src/torve/application/feedback.py` `src/torve/adapters/vcs/git.py` | A stranger's comment must never reach an agent; a parsed format rots with every vendor redesign |
| D-5.13 | `ASSUMED` | A re-run whose task carries a feedback record gets it in the sandbox and its prompt names it as untrusted review data under a contract that still governs — revise, not restart; scope, gates and the sha-bound approval are unchanged, and revision spend stays behind the human retry. Added by amendment A-32 2026-08-24 | `src/torve/application/runner.py` `src/torve/adapters/agent/harness.py` | The feedback channel steers attempts, never landings |
| D-5.14 | `ASSUMED` | The landing answers the review threads its revision consumed: capture retains each thread's reply address, and the tick's landing leg posts one reply per captured root — composed from records, saying what the loop did (captured, revised, landed as this sha) and never what the finding deserves; each reply carries its idempotency marker so a replay is absorbed at the destination, a failed answer waits for the next tick, and an unconsumed record answers nothing. Added by amendment A-41 2026-08-25 | `src/torve/application/feedback.py` `src/torve/adapters/vcs/git.py` `src/torve/cli/tick.py` | A reviewer whose finding vanishes into a merged pull request stops reading; the loop must close its own conversations |
| D-5.15 | `ASSUMED` | *(amended A-135, corrected A-137: the ledger admits a blocker from the pull-request trigger, which escalates and revises nothing; task-gated blockers stay out.)* Non-blocking findings get a ledger, not a lifecycle: `torve context` gains "Findings awaiting the operator" — every kept finding from a landed target's review, marked possibly_addressed when a later contract's text cites the review's task id (D-7.24's possibly_landed discipline applied to findings); the engine still mints nothing from a finding, and the operator triages the ledger in batch — per-finding instant minting is a habit, never a requirement. Added by amendment A-75 2026-09-01 | `src/torve/application/projections.py` | A finding recorded into telemetry and read by nobody is a review that ran for nothing; a ledger keeps the operator honest without making the engine decide work exists (D-2) |
| D-5.16 | `ASSUMED` | *(narrowed A-139: acceptance commands only — the copy is staged without `.git`, so the battery cannot run there.)* Inside its disposable copy the reviewer may execute the target's acceptance commands and gates; command output it cites is evidence like any path:line, and execution spends the review attempt's own budget and timeout — a battery too slow for the review window is a finding about the battery, never a license to extend the review. Added by amendment A-78 2026-09-01 | `src/torve/application/review.py` | A reviewer that can only read judges tests by their text; one that runs them reports what the change actually does |
| D-5.17 | `ASSUMED` | `review.blocks_at` names the severity at or above which a kept finding stops a promotion, default `major`; the finding keeps the grade the reviewer gave it and configuration decides what stops (D-2). Added by amendment A-136 2026-09-08 | `src/torve/application/review.py` `src/torve/config/runconfig.py` | Three consecutive reviews graded a pass-killing defect `major` and nothing `blocker`, so the bar that stopped a promotion sat above every severity the reviewer assigns |

D-5.5 (`Inference`-port default) was removed 2026-08-22 with charter A-11; the identifier is retired, never reused (D-A.4).

*(Paths relocated 2026-08-22 at acceptance, while draft: the design predates RFC 0015's source tree — there is no `review/` package; review logic lives in `application/review.py` beside the runner, the corpus under `.torve/review-corpus/`, per the D-32 relocation precedent.)*

## Phasing

*(Added 2026-08-22 at acceptance. The forge-facing legs — pull-request triggers, comment posting, the §7 replacement sequence and the two-week shadow — need a remote and stay operator/deferred work; the phases below are what a repository with no forge can build and verify.)*

```yaml
- phase: 1
  title: The finding and the role's mechanics
  intent: >-
    Findings become a domain type and the review role becomes real in the contract: Task gains targets, a review task refuses acceptance commands by validation, the acceptance gate is skipped for the role rather than passed with an empty list, and evidence location becomes a check that discards findings citing coordinates nothing can resolve.
  scope:
    - "src/torve/domain/**"
    - "src/torve/gates/**"
    - "tests/**"
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
    - "uv run lint-imports"
    - "uv run torve rfc check"
  depends_on: []
- phase: 2
  title: The review run
  intent: >-
    Review runs through the pipeline: input assembled from the diff, the target's contract, inherited decisions and gate results — never the author's trace; the workspace mounts read-only and the reviewer holds no credential beyond its tier's; findings parse from the agent's output, unlocatable evidence is discarded before anyone sees it, a surviving blocker escalates the target as blocker_finding and everything else is recorded on the attempt; the runner mints and drives the review task when its target's gates go green, replacing the review-not-configured bridge — off by default in configuration.
  scope:
    - "src/torve/application/**"
    - "src/torve/adapters/**"
    - "src/torve/config/**"
    - "tests/**"
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
    - "uv run lint-imports"
    - "uv run torve rfc check"
  depends_on: [1]
- phase: 3
  title: Degraded mode and the seeded corpus
  intent: >-
    Reviews without a contract run in degraded mode and are told so explicitly, so no specification is invented; the seeded-defect corpus becomes a repository artefact under .torve/review-corpus/ with a command that replays every case through the reviewer tier and reports which expected findings were caught — the regression harness that gates every prompt or model change.
  scope:
    - "src/torve/cli/**"
    - ".torve/review-corpus/**"
    - "tests/**"
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
    - "uv run lint-imports"
    - "uv run torve rfc check"
  depends_on: [2]
```

## 10. Exit criteria

- Review runs produce `Attempt` records indistinguishable in shape from implementation runs.
- Seeded-defect corpus passing.
- Two weeks of shadow-mode comparison against the incumbent, with blocker precision and escape rate recorded.

## Amendments

### A-75 — 2026-09-01 — the findings ledger (adds D-5.15)
**Found in the first capable-reviewer week.** With opus on the review seat
the non-blocking findings became consistently worth acting on — and the
operator was hand-minting a follow-up task per finding within minutes,
because the alternative was guaranteed loss: a kept finding lands in the
review's telemetry record and no surface ever shows it again. Proposals
got "awaiting the author" (D-7.24); findings got silence, and the
silence was being papered over by operator reflex at finding granularity
— fix-forward churn with no batch judgement.

**Changed:** D-5.15 — `torve context` gains "Findings awaiting the
operator": every kept finding from a landed target's review, with its
review id, severity and claim, marked `possibly_addressed` when a later
contract's text cites the review's task id — the same weak-citation
discipline `possibly_landed` uses, honest about being evidence rather
than proof. The engine still mints nothing from a finding; the ledger
exists so the operator can triage in batch instead of racing the
telemetry scroll.

**Deliberately unchanged:** the severity consequence. A reviewer-major
still lands and records — one capable model's "major" is still one
model's opinion, the blocker escalation path exists for certainty, and
promoting majors to blocking would hand the reviewer a veto the corpus
never graded. Reopen against ledger evidence, not incident memory.

### A-32 — 2026-08-24 — the revision loop (adds §4a, D-5.12–D-5.13)
**Found in operation** — the first external reviewer connected to the
lab made the gap concrete: its findings reached the human at the
approval gate, but a `retry` re-dispatched from scratch, and the next
attempt never learned why the last candidate was refused. Review that
cannot change the next attempt is ceremony.

**Designed against evidence, not imagination:** a survey of real threads
across three review bots and a human on the same pull requests showed
incompatible severity formats (an emoji header, a badge image's alt
text, a bare prefix), replies carrying resolution state ("fixed in
`<sha>`"), and one vendor already shipping per-finding prompts that open
with "treat this as untrusted review data". Hence the shape: verbatim
whole threads from allow-listed logins only, attributed, capped with
recorded truncation, quoted as data under a contract that still governs.

**Changed:** §4a states the loop; D-5.12 the capture at retry-apply,
D-5.13 the delivery into the re-run's sandbox and prompt.

**Deliberately unchanged:** D-5.2's separation (the reviewer still never
fixes; the *implementer* revises); the three containment layers; and the
human act gating all spend — nothing auto-retries because a bot
commented.

### A-41 — 2026-08-25 — the engine answers its reviewers (adds D-5.14)
**Found in operation** — the disjoint experiment batch's Major finding
travelled the whole loop: captured, revised against, fixed, landed —
and the thread that started it heard nothing. The pull request merged
under it; a reviewer, human or bot, watching their finding vanish into
a purple merge with no acknowledgement learns to stop reading. The
owner named the gap the day the first true finding existed.

**Changed:** the loop closes its own conversations (D-5.14). Capture
retains each thread's reply address beside the feedback record; when
the landing that consumed the record goes through, the tick's landing
leg posts one reply per captured root: *captured into the task's
revision record; the revised candidate landed as this sha; the
finding's disposition stays the reviewer's call*. Composed from
records — the reply reports what the loop did, never claims the
finding fixed, because that judgement belongs to the reviewer who
raised it. Idempotency markers ride each reply so replays are absorbed
at the destination; a failed answer leaves its addresses pending for
the next tick; the forge's cosmetics never fail the leg.

**Deliberately unchanged:** the capture allow-list (only threads that
entered the revision record are answered — the engine does not chat);
D-8.5's untrusted-text doctrine, which governs what comes *in*, not
this outbound record; and the human acts — approve, revise — that
create the relationship the reply reports.

### A-78 — 2026-09-01 — The reviewer may execute — in a copy nothing survives
A reviewer that can only read judges tests by their text; the operator
asked for representative feedback — the reviewer running the battery it
is judging. The separation D-5.2 exists for ("an agent that can
fix-and-approve is not a reviewer") is not reading versus running: it is
that nothing the reviewer does can alter what lands. So the boundary
moves from the filesystem to the lifecycle:

- **D-5.2 is reworded** (was: "The reviewer gets a read-only workspace
  and no forge credential; the runner posts comments"): *The reviewer
  works in a disposable copy of the target worktree and holds no forge
  credential; the diff under judgment is composed before the copy
  exists, nothing written in the copy survives the review, and the
  runner posts comments.* The consequence stands unchanged.
- **D-5.16 (`ASSUMED`, added)**: Inside its copy the reviewer may
  execute the target's acceptance commands and gates; command output it
  cites is evidence like any `path:line`, and the execution spends the
  review attempt's own budget and timeout — a battery too slow for the
  review window is a finding about the battery, not a license to extend
  the review. Paths: `src/torve/application/review.py`.
- The review prompt drops "the workspace is read-only" and says what is
  now true: the copy is disposable, running the acceptance commands is
  allowed and encouraged, and no edit made in the copy reaches anyone.

Deliberately unchanged: D-5.3 (no author trace), D-5.4 (unlocatable
evidence discarded), the runner-posts-comments half of D-5.2, and the
reviewer's lack of any forge credential.

### A-79 — 2026-09-02 — The staged diff — the reviewer reads, the prompt points
The original prompt embedded the whole diff — the right shape for a
tool-less reviewer, and a bomb once diffs carry vendored bulk: T-0228's
pagination diff (a rebuilt 270KB bundle) first blew the executing
shell's argument limit and then, piped, the model's own context —
"Prompt is too long", two unparseable-review escalations on a green
change. A-78 removed the design's premise: a reviewer with tools and a
workspace copy does not need its input pre-chewed.

So the diff moves from the prompt to the copy: `run_review` writes the
composed diff to `.torve/tmp/review.diff` inside the staged copy —
after composition, before the sandbox, so D-5.2's order guarantee is
untouched (the judgment input predates anything the reviewer can
write). The prompt keeps the contract, decisions, gate results and
acceptance commands, names the staged path as the thing to read first,
and carries only a short head of the diff as orientation. The reviewer
pulls hunks and surrounding tree context lazily — something the inline
shape could never offer — and skims vendored or generated bulk by
filename instead of paying context for it. The evidence rule (D-5.4)
is the guard against a reviewer that skips the reading: findings that
do not locate are discarded, and the reviewer measurement corpus
watches for empty-handed reviews of substantial diffs.

### A-114 — 2026-09-05 — What phases 1-3 left, and what the loop's retirement took
**Judged in the pass A-113's new flag started.** The programme view reports
every declared phase of this document shipped, and `partial` is still the
right assertion — but for a different reason than the header gives, and the
list of what is owed has grown since it was written.

**Still owed, as the header says:** §7's replacement sequence. The
comparison window opened 2026-08-26 and the ledger reads complementary
classes rather than redundancy, which is a finding about the incumbent and
not about this engine. D-5.7 says removal needs numbers; the numbers are
not in yet.

**Newly owed, and this is the part the header cannot know:** two decisions
lost their implementation to the standing loop's retirement (A-105, A-110),
and A-110 named the code without naming them.

- **D-5.14 is unimplemented.** It says the landing answers the review
  threads its revision consumed, and names *the tick's landing leg* as what
  posts the replies. That leg is deleted. `answer_captured_threads` remains
  on the forge adapter with no caller — A-110 lists it among the five kept
  deliberately — so the capability is one call site away, on whatever lands
  next.
- **D-5.12 is half-implemented.** Its blocker path is alive and is RFC
  0043's: a surviving blocker's claims and the convicted diff reach the next
  attempt in-run. Its forge path is gone — the pull request's threads were
  captured by the lane's automatic conflict disposal, and the allow-list it
  read (`review.feedback_from`) was deleted as a knob nothing could act on.

Both are recorded here rather than regraded. An `ASSUMED` decision whose
carrier was deleted by a *different* document's retirement is not a decision
that turned out wrong; it is one waiting on a rebuild that has somewhere to
go. What would be wrong is a corpus that reads as though the capability
still exists.

**Changed:** `implementation: partial` stands, now with three things owed
rather than one.

### A-135 — 2026-09-08 — the ledger hid the severity it most needed to show (amends D-5.15)
**Found by triaging the ledger itself.** D-5.15 gave non-blocking findings
a ledger and excluded blockers, on the premise recorded in the code as "a
blocker escalates its target and never lands beside it". That premise holds
on the task-gated path. It is false on the pull-request path, whose own
docstring says so: *"Task state is never mutated here — blockers on a
task-gated run escalate on that path; this one reports."*

So a blocker found on a pull request escalated nothing, its target landed
like any other, and the ledger then dropped it for being a blocker. It
appeared on no surface at all. This repository had accumulated **fourteen**
of them — the highest severity the reviewer can assign, recorded in
telemetry, read by nobody. D-5.15's own rationale is the sentence that
condemns this: *"A finding recorded into telemetry and read by nobody is a
review that ran for nothing."*

**Changed (D-5.15, `ASSUMED`, departed per the grade):** the ledger carries
a blocker whose target never escalated `blocker_finding`. One that did was
handed to a person by the lifecycle D-5.15 defers to and stays out — five
of this repository's nineteen, correctly. The discriminator is the
escalation record rather than the review record, because it is the
escalation that says whether anything stopped.

**What this did not change.** The engine still mints nothing from a
finding, and the operator still triages in batch (D-2). A blocker in the
ledger is a blocker that stopped nothing, which is a fact about the
engine's own handling and not an instruction to it.

**Read this beside A-136's severity bar.** These fourteen escaped because
nothing stopped them; the `major` findings escaped because the bar that
stops sat above every severity the reviewer actually assigns. Same hole,
two different halves of the same mechanism.

### A-136 — 2026-09-08 — what stops a promotion is configuration's, not the reviewer's (adds D-5.17)
**Found by three consecutive reviews of this engine's own work.** T-0283
found a leg that shipped the next phase's deliverable; T-0284 found a leg
whose construction took the whole manager down; T-0285 found a decode error
that abandoned every candidate behind the one it tripped on. All three were
real, all three were graded `major`, and `major` did not stop a promotion —
so all three promoted. Two of them would have taken down a running pass.

The reviewer uses `major` for "this must be fixed" and reserves `blocker`
for what it judges must stop the work. ~~On this corpus it has assigned
`blocker` nineteen times and never once for a defect that stopped
anything.~~ *(Struck 2026-09-08 by A-137: false. All nineteen stopped
something — five escalated and fourteen were revised in-run. The claim came
from A-135's mistaken reading and is not load-bearing for this decision,
which rests on three `major` findings that promoted and were verified
directly.)*

**Added — D-5.17 (`ASSUMED`):** `review.blocks_at` names the severity at or
above which a kept finding stops a promotion, defaulting to `major`. The
finding keeps the grade the reviewer gave it, in the record and in every
reading; what stops a promotion is configuration's to decide, which is D-2
exactly. `blocks_at: blocker` restores every reading before this one.

Deliberately not a prompt change. Re-teaching a model where its own bar sits
is a guess about calibration; a knob is mechanical, testable and reversible,
and it leaves the reviewer's judgement intact to be read later.

This matters now in a way it did not before RFC 0052: with
`promotion.auto_merge` armed, an unattended pass would have landed all
three.

### A-137 — 2026-09-08 — A-135 was wrong about what it had found (corrects A-135, amends D-5.15)
**Found by triaging the fourteen it surfaced.** Every one is FIXED. Not one
was an escape.

A-135 read "no `blocker_finding` escalation" as "nothing stopped this". It
does not. The task-gated path revises a blocker *inside* the run and only
escalates once the revision budget is spent, so a blocker fixed on the
first revision escalates nothing and its target lands — handled, silently.
That is what all fourteen were. One of them is fixed in
`.github/workflows/publish.yml` under a comment naming the exact `set -u`
failure the finding described: somebody read that blocker and acted on it.

So the filter A-135 shipped surfaced fourteen resolved findings as awaiting
the operator, which is the opposite of the ledger's purpose. Corrected.

**The hole A-135 aimed at is real and has never fired.** `review_pull_request`
reports and never touches task state, so a blocker found there escalates
nothing, revises nothing, and its target lands — genuinely invisible. But
both paths called `run_review` and wrote the identical record, so nothing
downstream could tell them apart, and zero of this corpus's nineteen
blockers came from that path.

**Changed:** the review record carries its `trigger`, and the ledger admits
a blocker only from `pull_request`. An absent trigger is the task-gated
default, so every record written before this stays out. This is the second
option the finding itself offered — *"mark the PR path's record so the
filter's premise holds"* — and it is the right one, because the
discriminator has to be which lifecycle applied, not whether one of that
lifecycle's outcomes happens to have been reached.

**What this cost and what it is worth.** A wrong fix, shipped, and caught
one commit later by reading the very findings it produced. The triage that
caught it is the same triage that made the ledger worth fixing: 26 of 35
open `major` findings still true, and 14 of 14 blockers already handled.
The two numbers together are the actual finding — **`blocker` is acted on
and `major` is not** — which is the case for D-5.17 far better than the
sentence A-135 gave it.

### A-139 — 2026-09-09 — the reviewer is promised only what its copy can run (narrows D-5.16)
**Found in the finding ledger against T-0227, and again by the reviewer
itself.** D-5.16 gave the reviewer the target's acceptance commands *and*
its gate battery to execute in the copy. The copy is staged with `.git`
excluded (D-5.2), so `torve gates` and every gate context fail at the door
with "not a git repository" — the promise was refused on every review this
document has ever produced, and the reviewer spent part of its one bounded
attempt discovering it. T-0285 filed it as a `nit` against its own review;
A-131 records the same wall from the executor's side.

**Changed (narrowing, not departing):** the prompt offers acceptance
commands, which run, and says plainly that the battery does not — it runs
outside the sandbox on the candidate, after the attempt. D-5.16's argument
is untouched: *"a reviewer that can only read judges tests by their text"*
still holds for the half that works.

**What is deliberately not decided here.** Whether the reviewer *should* be
able to run the battery is a real question with two real answers — seed the
copy with a throwaway repository, or copy `.git` shallowly so the battery
diffs against the true merge base — and the second puts a repository inside
the disposable copy, which D-5.2's isolation argument may refuse. That is
its own document. This amendment only stops the engine promising something
it refuses, which cost every review a little and taught nothing.
