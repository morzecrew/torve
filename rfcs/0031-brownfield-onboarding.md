---
id: "0031"
title: Brownfield onboarding
status: accepted
implementation: complete
depends_on: ["0004", "0030"]
informed_by: ["0007", "0017", "0022"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  First contact with a repository that has no corpus: a read-only agentless
  survey replaying merged history through the gate battery, and the
  baseline standing-decisions document extracted outside the engine that
  turns its silence into governance.
schema_version: 1
---

# RFC 0031 — Brownfield onboarding

- **Implementation state:** complete (judged 2026-09-01). Phase 1 executed as T-0198 (the survey, landed e2fac4c); phase 2 as T-0199 (the bootstrap skill, landed de19e3d). D-31.6's document shape waits on the first real adoption, as written.
- **Scope:** What torve can do for a repository it has never run in, before
  that repository trusts it with anything. Covers `torve survey` — an
  agentless, read-only replay of the last N landings on the default branch
  through the gate battery, clone-at-landing with the parent as the gate
  base, reporting per landing and per gate what would have fired and what
  stayed silent for want of a corpus — and the baseline path: a skill,
  outside the engine, that reads the survey and the repository in a
  supervised session and drafts one standing-decisions document a human
  reviews, commits and accepts, which then governs newly minted work through
  RFC 0030's standing inheritance. Excludes agent execution in the target
  repository, any ingestion of foreign spec formats (RFC 0007 §6b stands
  whole), packaging surfaces such as a CI action, and any retroactive
  conviction of the existing tree.
- **Related:** [`0004`](0004-agents-tiering.md) §5 (shadow mechanics,
  D-4.7) · [`0007`](0007-planner-context.md) §6a–§6b ·
  [`0030`](0030-the-document-threshold.md) §5.1 ·
  `src/torve/adapters/workspace/git.py` · `src/torve/gates/runner.py` ·
  `src/torve/gates/contract.py` · `src/torve/config/manifest.py`
- **Origin:** The adoption research of 2026-08-31. Two of its findings are
  load-bearing here: the proven brownfield entry for process tooling is a
  ratchet against a generated baseline, never absolute day-one enforcement;
  and the converting first contact is work already done for the adopter —
  a read-only report on their own history — not a concept to learn. RFC
  0007 §6a named `Constitution` and `ExecutionLog` sources and condition-
  gated them on a brownfield adopter; this document is that condition's
  design, built so the first adopter meets a finished path.

---

## 1. Summary

`torve survey --last N` clones each of a repository's last N landings at
bounded depth, runs the gate battery over each with its parent as the base,
and prints one report: which gates would have fired on work that already
merged, and which recorded nothing because the inputs a corpus provides —
contracts, logs, decisions — do not exist there. The report's silence is the
sales pitch for the second half: a bootstrap skill, running outside the
engine in a supervised session, drafts a single standing-decisions document
from the survey and the repository; a human edits, commits and accepts it.
From acceptance, RFC 0030's standing inheritance carries its rows into every
document-less contract, and the battery convicts only work minted after —
a ratchet, not a purge.

## 2. Motivation

Every guarantee this engine makes is demonstrated only where a corpus
already exists — this repository and the lab. A repository with none gets
nothing on day one: no decisions to inherit (RFC 0007 §6a's two remaining
sources are unbuilt), no gate evidence, and no way to see what the engine
would have caught except by adopting it first. That inversion — trust
before evidence — is exactly backwards for a system whose whole claim is
evidence before trust.

The evidence that the deterministic battery is demonstrable without an
agent already exists in our own records: the replay campaign convicted
frontier-model attempts on real tasks (`decisions-reported` on the T-0019
replay's first attempt, the evidence-past-EOF convictions in the T-0027
era), and those convictions were deterministic reads of a diff and a log —
no model in the loop. A gate that convicts on replayed history is the one
artefact a stranger's repository can verify about this engine at zero cost
and zero trust.

## 3. Current state

- `ShadowWorkspace.create(task_id, parent_sha)`
  (`src/torve/adapters/workspace/git.py`) builds a self-contained truncated
  clone with no refs beyond the requested sha (D-4.7); `parent_of` and
  `diff_range` are beside it. The mechanics need a clone-at-landing variant,
  not a new idea.
- The battery runs over a worktree with an explicit base
  (`run_gates`, `src/torve/gates/runner.py`); gate inputs are
  `worktree | diff | task | log` (`src/torve/domain/attempt.py`), and
  `gates/contract.py` already defines the no-task outcome — task- and
  log-input gates skip cleanly where no contract exists.
- The product battery ships in the package with manifest defaults
  (`src/torve/config/manifest.py`, `BUILTINS`); `rfc-valid` set the product-
  gate precedent (D-7.14).
- Skills ship as wheel data and materialize per role
  (`src/torve/application/skills.py`); rfc-writer is the authoring
  precedent for a skill that emits corpus documents.
- RFC 0030 §5.1 defines how an accepted standing-decisions document reaches
  document-less contracts.

## 4. Goals / Non-goals

**Goals**

- A stranger's repository gets a truthful report on its own merged history
  from one read-only command with zero credentials.
- The report's corpus-shaped silence has a named next step that ends in a
  human-accepted document, never an engine-written one.
- Governance arrives as a ratchet: new work convicted, existing tree left
  alone.

**Non-goals**

- **Agent execution in the target.** The survey runs gates, not agents: no
  sandbox, no model, no cost, no consent problem. Live execution is what
  the adopter graduates to, not what first contact costs.
- **Foreign format ingestion.** RFC 0007 §6b stands whole; a Spec Kit or
  OpenSpec corpus is not read by anything here. The bootstrap skill writes
  *this* corpus's format, and the human accepting it is the judgement a
  bridge cannot carry.
- **A quality score for the surveyed repository.** Per-gate counts with
  denominators, per landing — populations, not a number (the charter §8a
  instinct, applied to someone else's repository where it matters more).
- **Fleet or hosting concerns.** One repository, one invocation, operator's
  machine. RFC 0024 aggregates; a CI packaging surface is named in §8.

## 5. Design

### 5.1 `torve survey`

```
torve survey [--last N] [--branch NAME] [--format json]
```

For each of the last N landings on the branch (first-parent walk, default
branch, N default 20): resolve `parent = sha^`, build a truncated
clone-at-landing via the shadow workspace mechanics, run the battery with
`gates_base = parent`, collect per-gate outcomes, remove the clone. No task
contract and no log exist, so task- and log-input gates record their
no-task skip — and the report prints that skip as the finding it is:

```
landing a1b2c3d  fired: no-test-tampering        silent (no corpus): decisions-reported, self-audit, scope
```

Summary block: per-gate fired/clean/skipped counts over the window with
denominators, then one line naming what a corpus would add — the gates that
skipped every landing and the inheritance layer behind them. Exit 0 always:
a survey is a measurement, and a red history is a successful measurement of
a red history (the D-4.13 shadow-exit doctrine, applied to first contact).

With no `.torve/gates.yaml` in the target, the shipped product battery runs
under manifest defaults; a target that has one is surveyed with its own.
The survey writes nothing into the target — the report goes to stdout or
the path the operator names.

### 5.2 The bootstrap skill

`skills/corpus-bootstrap/` — shipped as wheel data beside rfc-writer, run
in a supervised session, never by the engine. Input: the survey report and
the repository. Output: one draft document in this corpus's format holding
standing decisions — graded honestly (mostly `ASSUMED`; `LOCKED` only where
the repository's own history shows a boundary being defended), every row
with paths, no phasing. The skill teaches extraction; the human edits,
commits and accepts, exactly as RFC 0007 §6a required: model-assisted
extraction outside the engine, deterministic reading inside it. From
acceptance, RFC 0030 standing inheritance does the rest — no new engine
surface exists for the baseline at all.

### 5.3 The ratchet

Nothing convicts retroactively. The survey reports history; the baseline
governs contracts minted after its acceptance; the existing tree is never
in scope. This is the entry's load-bearing property: absolute day-one
enforcement is the documented adoption killer for every process tool that
tried it, and a gate that convicts a thousand existing files convicts the
adoption instead.

### Alternatives considered

- **Replay with agents (`run_shadow`) as first contact.** Rejected: needs
  harness config, credentials and money before any trust exists, and the
  demonstrable value — deterministic conviction — needs none of it. Agent
  replay is the second date, not the first.
- **Engine-generated baseline.** Refused under D-2/D-7.6: a grade is a
  human judgement about reversal cost, and nothing extracts it
  deterministically from a repository where nobody made that judgement.
- **Surveying working-tree state instead of history.** A linter does that;
  the engine's differentiating claim is about *changes* — what the battery
  says about merges that already happened is evidence a linter cannot
  produce.

## 6. Tests

- Survey over a fixture repository with planted landings: fired, clean and
  no-corpus-skip outcomes each represented; first-parent walk pinned
  against a merge-heavy history; the no-`.torve` default-manifest path and
  the has-manifest path both covered.
- Read-only property: the target tree byte-identical after a survey run.
- Report as value (JSON), not rendered text, per D-18.1's discipline.
- The skill ships with its own checklist fixture the way rfc-writer's
  template is exercised — a sample survey report in, a checkable document
  shape out.

## 7. Docs

README gains an "onboarding an existing repository" section that is the
wedge's script: survey, read, bootstrap, accept, and what changes at each
step. The pages/ site carries the honest caveat that survey findings on
history are directional evidence about the battery's fit, not a verdict on
the repository.

## 8. Out of scope

- **A CI packaging surface** (survey as a GitHub Action on agent PRs) —
  named as the escape hatch once the engine's own surfaces stabilise;
  premature while the CLI contract is still moving.
- **`ExecutionLog` as a standing source** — RFC 0007 §6a's third source
  accumulates only after the engine runs somewhere, which is after this
  document's path has been walked; it waits for the evidence it reads.
- **Multi-repository survey aggregation** — RFC 0024's fleet view reads
  roots the engine operates; extending it to surveyed strangers can ride a
  later amendment there.

## 9. Risks

- **The survey underwhelms on clean history.** A disciplined repository
  yields few firings and a wall of no-corpus skips. Accepted and designed
  for: the skip lines are the finding — they show the governance layer that
  does not exist — and the report says so rather than apologising.
- **House gates mistaken for universal judgement.** `source-layout` and
  kin encode this corpus's conventions; fired on a stranger's history they
  read as noise. Mitigated: the default survey battery is the product
  gates; house-convention gates run only where a target's own manifest
  names them.
- **The bootstrap skill over-grades.** A generated draft full of `LOCKED`
  rows would make RFC 0030's threshold refuse everything and poison the
  entry. The skill's doctrine is the mitigation (LOCKED needs defended-
  boundary evidence), and RFC 0022's calibration report is the correction
  loop once real tasks run.
- **Trust laundering.** A survey report could be waved as certification.
  The §7 caveat and the no-score non-goal are the answer written down
  before it is needed.

## 10. Unresolved questions

- D-31.6 (below): one baseline document or several by area; the first real
  adoption settles it.
- Whether `--last N` should walk landings by count or by time window;
  execution decides against real histories and logs the choice.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-31.1 | `LOCKED` | The survey is agentless and read-only: no model call, no sandbox, no credentials, nothing written into the target beyond the report the operator names; exit 0 on any completed measurement | `src/torve/application/survey.py` `src/torve/cli/survey.py` | First contact must cost zero trust; an agent or a write in it converts the wedge into the commitment it exists to precede |
| D-31.2 | `LOCKED` | Baseline extraction runs outside the engine as a skill; the engine only ever reads the committed, human-accepted document, through RFC 0030's standing inheritance and nothing else | `skills/corpus-bootstrap/**` | D-2 and D-7.6 unchanged at the exact point they would be most convenient to bend; no new engine surface for the baseline exists to drift |
| D-31.3 | `LOCKED` | The baseline governs contracts minted after its acceptance only; no gate convicts the existing tree or its history retroactively | `src/torve/application/survey.py` `skills/corpus-bootstrap/**` | The ratchet is the entry's survivable property; day-one absolute enforcement converts findings into a reason to uninstall |
| D-31.4 | `ASSUMED` | Replays are truncated clones at the landing sha with the first parent as gate base, via the shadow workspace mechanics; task- and log-input gates record their no-task skip and the report prints that silence as the corpus's absence made visible | `src/torve/adapters/workspace/git.py` `src/torve/application/survey.py` | The skip line is the pitch: deleting it hides the one thing the survey exists to show |
| D-31.5 | `ASSUMED` | A target with no gates manifest is surveyed with the shipped product battery under manifest defaults; a target with one is surveyed with its own | `src/torve/config/manifest.py` `src/torve/application/survey.py` | House-convention gates firing on a stranger's history would read as noise and indict the survey, not the history |
| D-31.6 | `OPEN` | Whether the baseline is one document or several by area, and its naming convention; the first real adoption settles it and the bootstrap skill records the shape it chose | `skills/corpus-bootstrap/**` | Guessing the shape before an adopter exists is the speculation §6b refused for formats |

## 12. Phasing

```yaml
- phase: 1
  title: the-survey
  intent: >-
    torve survey: first-parent walk of the last N landings, truncated
    clone-at-landing via the shadow workspace mechanics, the battery run
    with the parent as base, per-gate outcomes collected and the clone
    removed; no-corpus skips reported as findings, product battery under
    manifest defaults when the target has none, read-only throughout,
    exit 0 on completed measurement (D-31.1, D-31.4, D-31.5). Tests pin
    the fixture-history outcomes, the read-only property and the JSON
    report shape.
  scope:
    - "src/torve/application/survey.py"
    - "src/torve/cli/survey.py"
    - "src/torve/adapters/workspace/git.py"
    - "tests/test_survey.py"
  acceptance:
    - "uv run pytest tests/test_survey.py"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run ruff check ."
    - "uv run lint-imports"
  depends_on: []
- phase: 2
  title: the-bootstrap-skill
  intent: >-
    skills/corpus-bootstrap shipped as wheel data: the extraction doctrine
    (mostly ASSUMED, LOCKED only on defended boundaries, paths on every
    row, no phasing), the survey report as input, one draft document in
    this corpus's format as output for a human to edit, commit and accept
    (D-31.2, D-31.3, D-31.6). A sample survey report and the checkable
    output shape ride the skill as its fixture.
  scope:
    - "skills/corpus-bootstrap/**"
    - "tests/test_skills.py"
  acceptance:
    - "uv run pytest tests/test_skills.py"
    - "uv run torve rfc check"
  depends_on: [1]
```
