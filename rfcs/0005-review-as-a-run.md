---
id: "0005"
title: Review as a run
status: accepted
implementation: partial
depends_on: ["0003", "0004"]
informed_by: []
supersedes: []
superseded_by: null
amended_by: ["A-32"]
retired: ["D-5.5"]
owner: Lev Litvinov
description: >-
  Independent automated review as a second run role: isolation rules, the finding contract, calibration, and replacing third-party PR reviewers.
schema_version: 1
---

# RFC 0005 — Review as a run

- **Implementation state:** phases 1–3 executed 2026-08-22 (T-0038 finding/role mechanics, T-0039 the review run, T-0040 degraded mode and the seeded corpus — measured green live with a deepseek reviewer); the forge leg executed 2026-08-23 (T-0053 — `torve review pr` as the §4 trigger: skip rules, one review per head, Torve-Task trailer mapping or degraded input, findings posted back by the runner through the SCM port; demonstrated live on the lab against an organic pull request no agent wrote). Outstanding: the §7 replacement sequence and the two-week shadow comparison, which need an incumbent reviewer to shadow
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
| D-5.2 | `LOCKED` | The reviewer gets a read-only workspace and no forge credential; the runner posts comments | `src/torve/application/review.py` `src/torve/adapters/runtime/**` | An agent that can fix-and-approve is not a reviewer |
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

D-5.5 (`Inference`-port default) was removed 2026-08-22 with charter A-11; the identifier is retired, never reused (D-A.4).

*(Paths relocated 2026-08-22 at acceptance, while draft: the design predates RFC 0015's source tree — there is no `review/` package; review logic lives in `application/review.py` beside the runner, the corpus under `.torve/review-corpus/`, per the D-32 relocation precedent.)*

## Phasing

*(Added 2026-08-22 at acceptance. The forge-facing legs — pull-request triggers, comment posting, the §7 replacement sequence and the two-week shadow — need a remote and stay operator/deferred work; the phases below are what a repository with no forge can build and verify.)*

```yaml
- phase: 1
  title: The finding and the role's mechanics
  intent: |
    Findings become a domain type and the review role becomes real in the
    contract: Task gains targets, a review task refuses acceptance commands
    by validation, the acceptance gate is skipped for the role rather than
    passed with an empty list, and evidence location becomes a check that
    discards findings citing coordinates nothing can resolve.
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
- phase: 2
  title: The review run
  depends_on: [1]
  intent: |
    Review runs through the pipeline: input assembled from the diff, the
    target's contract, inherited decisions and gate results — never the
    author's trace; the workspace mounts read-only and the reviewer holds
    no credential beyond its tier's; findings parse from the agent's
    output, unlocatable evidence is discarded before anyone sees it, a
    surviving blocker escalates the target as blocker_finding and
    everything else is recorded on the attempt; the runner mints and
    drives the review task when its target's gates go green, replacing
    the review-not-configured bridge — off by default in configuration.
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
- phase: 3
  title: Degraded mode and the seeded corpus
  depends_on: [2]
  intent: |
    Reviews without a contract run in degraded mode and are told so
    explicitly, so no specification is invented; the seeded-defect corpus
    becomes a repository artefact under .torve/review-corpus/ with a
    command that replays every case through the reviewer tier and reports
    which expected findings were caught — the regression harness that
    gates every prompt or model change.
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
```

## 10. Exit criteria

- Review runs produce `Attempt` records indistinguishable in shape from implementation runs.
- Seeded-defect corpus passing.
- Two weeks of shadow-mode comparison against the incumbent, with blocker precision and escape rate recorded.

## Amendments

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
