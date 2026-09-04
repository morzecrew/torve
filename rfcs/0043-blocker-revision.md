---
id: "0043"
title: Blocker revision
status: draft
depends_on: ["0005"]
informed_by: ["0038"]
supersedes: []
superseded_by: null
amended_by: []
owner: misery7100
description: >-
  A surviving review blocker feeds a bounded in-run revision attempt in the
  same worktree — carried by the RFC 0005 §4a feedback record — before it
  escalates the run.
schema_version: 1
---

# RFC 0043 — Blocker revision

- **Scope:** The attempt loop's response to a surviving review blocker
  (`src/torve/application/runner.py`), the rendering of internal reviewer
  findings into the existing revision-feedback record
  (`src/torve/application/feedback.py`, `src/torve/application/review.py`),
  and one new knob on `ReviewConfig` (`src/torve/config/runconfig.py`). No
  contract changes: the escalation vocabulary, the review lane's mechanics,
  the poison ceiling and the worktree lifecycle are all untouched. What
  changes is *when* `blocker_finding` fires — after a bounded revision
  attempt instead of immediately.
- **Related:** RFC 0005 §4a (the revision loop this reuses), RFC 0038
  (per-attempt verdict rows that make the two reviews auditable), RFC 0001
  §4 (poison ceiling), `src/torve/application/runner.py`,
  `src/torve/application/feedback.py`.
- **Origin:** Operator observation, 2026-09-04: T-0250 and T-0253 each
  escalated on a mechanical blocker (an untracked file; a wrong host
  derivation) with a gate-green diff in the worktree — and the re-dispatch
  rebuilt everything from an empty tree, duplicating a full attempt's tokens
  and 30–60 minutes per blocker.

---

## 1. Summary

A review blocker today escalates the run immediately; the operator reaps and
re-dispatches, and the fresh attempt starts from scratch. This RFC gives the
run one bounded revision attempt first: the reviewer's blockers and the
convicted candidate diff are written into the RFC 0005 §4a feedback record,
the loop continues in the same worktree, and the agent revises instead of
rebuilding. A blocker that survives revision escalates exactly as today. Gate
convictions already retry in-run; this closes the same loop for review
convictions.

## 2. Motivation

Two escalations in one night made the asymmetry concrete:

- T-0250 attempt 1: gate-green diff, blocker T-0272 — the agent wired
  `torve.cli.why` but never `git add`ed the file. The fix is one command; the
  re-dispatch was a full re-execution (~30 min, a second review, a second
  gate battery).
- T-0253 attempt 1: gate-green diff, blocker T-0276 — one host-derivation
  call site read the wrong config precedence. Same shape: a targeted patch
  redone as a rebuild.

A blocking *gate* failure gets `continue` in `_attempt_loop`
(`src/torve/application/runner.py`) — next attempt, same worktree, diff
preserved, poison ceiling counting. A *review* blocker takes
`state.escalate(BLOCKER_FINDING)` and returns; the worktree is reaped and the
next dispatch sees only the escalation's history text. The engine already
knows how to carry review critique into a retry — the RFC 0005 §4a feedback
record does exactly this for forge review threads — but the internal
reviewer's findings never use it.

## 3. Current state

Verified against the tree at `fc84404`:

- `_attempt_loop` (`src/torve/application/runner.py`): gate red appends a
  history fact and `continue`s; `_apply_review` returning False stops the
  loop — `review_hook_fn` escalated `BLOCKER_FINDING` with the blockers'
  claims joined into the detail text (capped 300 chars).
- `run_review` (`src/torve/application/review.py`) drives one reviewer
  attempt in a disposable copy (D-5.2/A-78, D-5.16) and returns findings as
  data; unlocatable evidence is discarded before the runner sees it (D-5.4).
- `feedback_file(root, task_id)` → `.torve/tasks/<id>/feedback.md`
  (`src/torve/application/feedback.py`): rendered threads + superseded diff,
  capped at 24,000 bytes with truncation recorded (D-5.12). At dispatch the
  runner plants a copy at `<worktree>/.torve/feedback.md` and the prompt
  names it as untrusted review data (D-5.13). Today only the forge adapter
  writes it, from allow-listed PR threads (A-32, A-52).
- The runner already re-mints a fresh review task per invocation
  (`mint_review_task`), so a revision attempt's review gets its own id and
  its own RFC 0038 verdict row with no extra work.
- `ReviewConfig` (`src/torve/config/runconfig.py`): `on`, `skip_authors`,
  `feedback_from` — no retry knob.

## 4. Goals / Non-goals

**Goals**

- A mechanical blocker costs a revision, not a rebuild: same worktree, prior
  diff and findings in front of the agent.
- The consequence stays configuration-decided (D-2): a knob bounds the
  revisions; zero restores today's behavior exactly.
- Both reviews stay auditable: per-attempt verdict rows (RFC 0038) name what
  each review convicted and what the revision changed.

**Non-goals**

- Resuming an *escalated* run into its kept worktree — that is the
  resume-from-gated engine-repair track, a different lifecycle change.
- Reconciling cumulative ceilings across re-dispatches (RFC 0026 §5.5 gap,
  noted in 0037 §3) — this RFC only spends attempts inside one run.
- Any change to reviewer mechanics, finding shapes, or the discard rule
  (D-5.4) — the reviewer neither knows nor cares that its verdict feeds a
  revision.

## 5. Design

### 5.1 The loop keeps going

In `review_hook_fn`, a surviving blocker no longer escalates while the run
has revision budget. Instead the hook:

1. renders the blockers and the convicted candidate diff into the feedback
   record (§5.2) at `feedback_file(root, task.id)`,
2. appends a history fact — `review blocker (revision n of m): <review_id>:
   <claims, capped>` — so the run's timeline reads the same way gate-red
   facts do,
3. signals the loop to `continue` rather than land.

The next iteration dispatches into the same worktree, plants the record, and
the existing prompt rule (D-5.13) frames it. The poison ceiling and the
`iterations`/`wallclock` budgets are checked at the top of the loop exactly
as before — a revision is an attempt, nothing about the ceiling changes.

When the revision budget is spent, the hook escalates `BLOCKER_FINDING` with
today's detail text. `outcome.unparseable` (infrastructure) and the broker's
budget refusal (cost) keep their immediate escalations — neither is
something a revision can fix.

### 5.2 The record carries the reviewer's findings

`render_feedback` already takes `threads: [{path, line, comments: [{author,
body}]}]`. Internal blockers map onto it without a schema change: each
finding becomes one thread — `path`/`line` from its evidence citation,
`author` the review id, `body` the claim plus evidence. The superseded diff
is the convicted candidate's patch (the runner holds it as
`last_pass["patch"]`). The cap and truncation honesty (D-5.12) apply
unchanged.

The record is written root-side, so it outlives the run: if the revision
also fails and the task escalates, the *next* dispatch — after operator
triage — still plants the accumulated record. The from-scratch rebuild
disappears even across an escalation.

### 5.3 The knob

```yaml
review:
  on: [task_gated]
  blocker_revisions: 1   # default; 0 = escalate immediately (today's behavior)
```

One integer on `ReviewConfig`, counted per run, spent only by surviving
blockers. The default is 1: the T-0250/T-0253 class gets exactly one
targeted repair; anything the revision cannot fix is contract-class trouble
and belongs with the operator. Values above 1 are legal but interact with
the poison ceiling — the ceiling still wins.

### Alternatives considered

- **Escalate, but let re-dispatch reuse the kept worktree.** Preserves the
  operator checkpoint on every blocker, but resuming into an escalated
  worktree is a lifecycle change (reap semantics, lease fencing, stale-base
  detection) an order of magnitude larger than continuing a live loop —
  and the operator's actual triage on T-0250/T-0253 was "relaunch and hope".
- **Match blocker text to detect repeats and escalate early.** Text matching
  is brittle across reviewer phrasings; the revision budget bounds the same
  risk with one integer.
- **Write the findings into the prompt instead of the record.** The record
  already exists, is capped, is named untrusted by the prompt, and survives
  the run for later dispatches; a second carrier would duplicate it.

## 6. Tests

- Loop: a blocker with budget left continues (same worktree, history fact,
  no escalation); a blocker with budget spent escalates `blocker_finding`;
  `blocker_revisions: 0` reproduces today's transitions byte-for-byte;
  unparseable and budget-refusal escalations unaffected by budget.
- Record: blockers render as threads with the review id as author; the
  convicted diff rides along; the cap truncates with the notice (existing
  `test_feedback.py` families extended).
- Ceiling interaction: revisions spend attempts; a revision that would pass
  the poison ceiling escalates `poison_ceiling`, not `blocker_finding`.
- Verdict rows: two reviews on one run yield two rows naming their attempts
  (extends `test_review_run.py`).

## 7. Docs

`ReviewConfig` docstring gains the knob and its D-2 framing; RFC 0005 §4a
gains a dated cross-reference note (amendment on acceptance, not an edit).

## 8. Out of scope

- Feedback-record reuse for *gate* retries — gates already converge without
  critique; adding it is speculation until a measured need.
- Reviewer-side awareness of revisions (e.g. "verify the prior blockers
  first") — the review prompt stays verdict-oriented; a revision's review
  is just a review.

## 9. Risks

- **A revision burns tokens on an unfixable blocker.** Bounded at one by
  default; the escalation then carries two convictions' context, which is
  better triage material than one.
- **The record leaks reviewer text into an executor prompt.** Already the
  case for forge threads under D-5.13's untrusted framing; internal
  reviewer text is no more privileged.
- **Double review cost per blocker.** Real, and accepted: a review is
  cheap relative to a rebuilt attempt (T-0250's re-dispatch ran a full
  second review anyway).

## 10. Unresolved questions

- None load-bearing. Whether `blocker_revisions` should ever default above 1
  is a calibration question for the projection after a few weeks of rows.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-43.1 | `LOCKED` | A surviving review blocker spends the run's `blocker_revisions` budget (default 1) as in-run attempts in the same worktree before escalating `blocker_finding`; the poison ceiling and task budgets count every revision, unchanged | `src/torve/application/runner.py` | Escalation timing changes; anything watching for `blocker_finding` on first conviction now sees it one attempt later by default |
| D-43.2 | `LOCKED` | The revision's critique travels only in the RFC 0005 §4a feedback record — blockers rendered as threads (review id as author, claim + evidence as body), the convicted diff as the superseded diff, cap and truncation honesty unchanged | `src/torve/application/feedback.py`, `src/torve/application/review.py` | One carrier for all revision context; a second channel (prompt injection, env) is a design violation, not an option |
| D-43.3 | `ASSUMED` | `blocker_revisions: 0` restores the pre-0043 transition sequence exactly — configuration decides the consequence (D-2), and the conservative setting is always reachable | `src/torve/config/runconfig.py` | — |
| D-43.4 | `ASSUMED` | Unparseable verdicts and broker budget refusals keep immediate escalation — infrastructure and cost are not revisable defects | `src/torve/application/runner.py` | — |
| D-43.5 | `ASSUMED` | The record is written root-side (`.torve/tasks/<id>/feedback.md`) so a post-escalation re-dispatch inherits the accumulated critique with zero new mechanism | `src/torve/application/feedback.py` | — |
| D-43.6 | `OPEN` | Whether the revision attempt's history fact should carry a structured marker (for the calibration projection to count revisions vs first attempts) or the prose fact suffices — settled by whoever builds the first consumer | `src/torve/application/runner.py` | — |

## 12. Phasing

```yaml
- phase: 1
  title: the blocker revision loop
  intent: >-
    A surviving review blocker no longer escalates while the run has
    revision budget: the runner renders the blockers and the convicted
    candidate diff into the RFC 0005 §4a feedback record at the task's
    root-side feedback path, appends a revision history fact, and continues
    the attempt loop in the same worktree; the record is planted and framed
    by the existing D-5.13 mechanics, the poison ceiling and task budgets
    count revisions unchanged, and a blocker surviving the spent budget —
    or an unparseable verdict or broker budget refusal at any point —
    escalates exactly as today. ReviewConfig gains `blocker_revisions`
    (default 1; 0 restores the pre-0043 sequence byte-for-byte).
  character: structural
  scope:
    - src/torve/application/runner.py
    - src/torve/application/review.py
    - src/torve/application/feedback.py
    - src/torve/config/runconfig.py
    - tests/test_runner.py
    - tests/test_review_run.py
    - tests/test_feedback.py
  acceptance:
    - uv run pytest tests/test_runner.py tests/test_review_run.py tests/test_feedback.py
    - uv run torve gates check
    - uv run torve rfc check
  depends_on: []
```
