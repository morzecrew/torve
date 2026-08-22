---
id: "0005"
title: Review as a run
status: draft
implementation: none
depends_on: ["0003", "0004"]
informed_by: []
supersedes: []
superseded_by: null
amended_by: []
retired: ["D-5.5"]
owner: Lev Litvinov
description: >-
  Independent automated review as a second run role: isolation rules, the finding contract, calibration, and replacing third-party PR reviewers.
schema_version: 1
---

# RFC 0005 — Review as a run

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
| D-5.1 | `LOCKED` | Review is a run with `role: review`, not a distinct subsystem | `src/torve/review/**` | Inherits budgets, cancellation, telemetry; reversing duplicates all of it |
| D-5.2 | `LOCKED` | The reviewer gets a read-only workspace and no forge credential; the runner posts comments | `src/torve/review/**` | An agent that can fix-and-approve is not a reviewer |
| D-5.3 | `LOCKED` | The reviewer never receives the author's session trace | `src/torve/review/**` | Otherwise it audits reasoning, not the change |
| D-5.4 | `ASSUMED` | Findings with unlocatable evidence are discarded automatically | `src/torve/review/**` `src/torve/gates/decisions_reported.py` | Shared with the execution-log check; remove if it discards true positives |
| D-5.6 | `LOCKED` | A seeded-defect corpus gates every prompt or model change | `src/torve/review/**` | Prompt tuning without it is guesswork |
| D-5.7 | `ASSUMED` | Third-party reviewer removal requires shadow-mode numbers, not preference | — | Four-step sequence in §7 |
| D-5.8 | `ASSUMED` | Reviews on pull requests without a task run in degraded mode and are told so | `src/torve/review/**` | Prevents invented specifications |
| D-5.9 | `LOCKED` | Review is minted as a task with `role: review` and `targets`, sharing the contract shape | `src/torve/domain/task.py` | A third role must not require a new mechanism |
| D-5.10 | `LOCKED` | A review task has no `acceptance`; the gate is skipped for the role | `src/torve/gates/acceptance.py` | Its output is findings, not an exit code |
| D-5.11 | `LOCKED` | Review tasks are minted by the runner at `gated`, never by the planner | `src/torve/application/runner.py` | Review follows execution; the planner would have to predict it |

D-5.5 (`Inference`-port default) was removed 2026-08-22 with charter A-11; the identifier is retired, never reused (D-A.4).

## 10. Exit criteria

- Review runs produce `Attempt` records indistinguishable in shape from implementation runs.
- Seeded-defect corpus passing.
- Two weeks of shadow-mode comparison against the incumbent, with blocker precision and escape rate recorded.
