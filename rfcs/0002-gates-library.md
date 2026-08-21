---
id: "0002"
title: Gates as a library
status: accepted
depends_on: ["0001"]
informed_by: []
supersedes: []
superseded_by: null
amended_by: ["A-2", "A-8"]
owner: Lev Litvinov
description: >-
  The gate contract, the starting gate set, sabotage verification, and packaging gates as a pip-installed CI dependency — the first shippable increment.
schema_version: 1
---

# RFC 0002 — Gates as a library

- **Implementation state:** library, six gates, sabotage suite and CI shipped in this repository (2026-08-21); exit criteria outstanding: a second consuming repository and two weeks of telemetry
- **Scope:** The gate contract, the starting gate set, how gates are verified, and packaging them as a dependency repositories install into CI. No runner, no store, no sandbox, no agents. Excludes anything requiring `torve run`.
- **Inherits:** D-3 (gates run outside the agent session), D-8, D-21a from RFC 0001
- **Related:** RFC 0001 §6 (task contract, execution log) · `flag-dont-flip` · `ratchet-what-you-build`

---

## 1. Why this ships first

Gates already exist in these repositories — as ad-hoc scripts inside CI configuration, copied between projects, drifting apart. Nothing about them requires an engine.

Packaging them yields three things immediately, before a line of runner exists:

- **Copy-paste between repositories stops.** A fix to `scope` ships once, as a version bump, instead of being applied four times and forgotten in a fifth.
- **The gate becomes a versioned artefact** rather than an inline shell fragment nobody reviews.
- **Baseline metrics start accumulating.** Per-gate hit counts on human and agent PRs alike — the only data that later answers whether any of this helped, and it can only be captured before the rest is introduced.

## 2. Delivery

```bash
pip install torve
torve gates run --base origin/main          # all gates
torve gates run --only scope,acceptance
torve gates check                           # the sabotage suite
```

One CI step per repository. `gates.yaml` at the repository root *(since RFC 0013: `.torve/gates.yaml`, root read as fallback)*. Exit code is the gate outcome; `--format json` emits `GateResult` records for later ingestion.

## 3. The gate contract

```python
class Gate(BaseModel):
    name: str
    run: str | Literal["@task.acceptance"]
    input: Literal["worktree", "diff", "task", "log"]
    state: Literal["shadow", "blocking", "quarantined"]   # §7.3; required, a boolean cannot express shadow or quarantine
    origin: str                                           # structural | leak/<task> | rfc/<id> — why this gate exists
    added: date | None
    timeout: timedelta
```

*Amended by A-8 2026-08-21: `blocking: bool` replaced by `state`, and `origin` added, before any manifest existed outside this repository — past that point this is a migration rather than an edit.*

`input` is what makes gates portable across projects: `scope` consumes a diff and knows nothing about the language, `typecheck` consumes a worktree, `decisions-reported` consumes the log plus the task. The gate receives its input prepared and returns a `GateResult`.

Execution rules:

- **Ordered cheapest first, fail-fast on the first failure of a `blocking`-state gate.** A thirty-minute e2e never runs on a diff that fails typecheck.
- **`shadow` and `quarantined` gates run regardless**, including after a blocking failure — their output is measurement and triage material, and it never affects the exit code (§7.3).
- **Every result is persisted** with name, exit code, duration, sha, truncated output and a log reference. A green with no artefact does not count.
- **Diffs are computed against `git merge-base`**, not against current base — otherwise `scope` reddens on other people's work that landed mid-task.

## 4. Starting set

Each targets a structural property, so none requires mining past pull requests first.

| Gate | Input | State | Origin | Catches |
| --- | --- | --- | --- | --- |
| `scope` | diff | blocking | structural | files outside `allow` or inside `deny` |
| `acceptance` | worktree | blocking | structural | completion claimed on red |
| `no-test-tampering` | diff | blocking | structural | tests edited where the task did not license it |
| `decisions-reported` | log + task | blocking | structural | `LOCKED` area touched with no log entry, or an illegal action for the grade |
| `self-audit` | worktree | shadow | structural | author-side blind spots |

`review` is a gate in spirit but a run in mechanics — see RFC 0005.

*Amendment 2026-08-21 (A-2):* every gate implementation lives in the package (`src/torve/gates/`), never in a skill directory — `decisions-reported` in particular is `src/torve/gates/decisions_reported.py`, not a script shipped with `flag-dont-flip`. A gate in a skill directory is per-repository copy-paste, the exact thing this increment removes. The skill keeps one line naming its enforcing gate; the gate reports that an entry is missing, the skill teaches when one should have been written.

**Everything else accumulates from evidence.** Each time a defect reaches review, either a gate appears or a written decision records that this class is caught by humans. Deriving gates from actual leaks beats designing a set up front, and it is why this list is short.

## 5. Gates are themselves verified

`gates/sabotage/` holds one deliberately bad diff per gate. CI applies each and asserts the corresponding gate goes red. A gate that cannot be shown to fail is not a check.

This is not optional decoration: without it, a gate that silently stops working looks identical to a gate that never fires because the code is clean. Run the suite on every change to the gate package, and once per sprint against the consuming repositories. The suite runs continuously, not once at authoring time, because it is what distinguishes a dead gate from a clean codebase (§7.7).

Precedent from the `decisions-reported` validator, which ships with four sabotage cases — silence, illegal action, unlocatable evidence, and a valid log — and is only trusted because all four were observed to behave correctly.

## 6. `scope` in detail

The cheapest gate and the one that catches the most common drift.

```yaml
scope:
  allow: ["packages/api/**", "tests/api/**"]
  deny:  ["**/migrations/**", "packages/core/types.ts"]
```

`deny` wins over `allow`. An empty `allow` means unconstrained, which should be rare and visible in review of the task.

Second use, free once the first exists: **overlap detection**. Two tasks whose `allow` sets intersect must not run concurrently. Checking this before dispatch prevents the conflict; the merge train (RFC 0006) only resolves the ordering of ones that got through.

## 6a. Three outcomes gates need beyond pass and fail

**Flakes.** `acceptance` runs real test suites and real test suites flake. Today a flake is a failed attempt and costs a life from the poison ceiling. It needs its own outcome: a gate that fails and then passes on immediate re-run is `flaky`, does not consume an attempt, and increments a flake counter per command. Commands above a threshold enter a quarantine list and stop blocking until fixed. This is not a risk to monitor — it is a first-week certainty.

**Escape hatch.** Gates are fail-closed, and sometimes wrong: a `scope` too narrow, an acceptance command broken by infrastructure. With no sanctioned way past, an unsanctioned one appears — someone comments the gate out of CI, and now nobody knows. Per `escape-hatch-policy`:

```yaml
bypass:
  requires: human_signature      # never an agent
  reason: mandatory              # free text, recorded
  logged_to: logs/<task-id>.yaml   # same append-only log
  metric: bypass_count_by_gate
```

A bypass that is counted is data — it tells you which gate is miscalibrated. A bypass that does not exist becomes a workaround you find out about later.

*Amendment 2026-08-21 (A-3):* the bypass record's shape is stated here and only here — no skill is involved, because **the signer is always a human** and humans read RFCs, not skills. The record: the gate name, a mandatory free-text reason, the signer's identity (the authoring of a reviewed commit carrying the `Torve-Bypass: <gate>: <reason>` trailer, per D-2.11), the commit sha, and a timestamp — appended to the task's execution log and counted per gate (`bypass_count_by_gate`, the `bypass-count` metric). A gate bypassed repeatedly is a signal about the gate, not the person.

**Secrets.** One more gate, and it belongs in the starting set precisely because its failure class is irreversible once landed: a secret scanner over the diff, blocking, no bypass. Cheap to run, and unlike other gates a miss cannot be fixed by a follow-up commit.

## 6b. Task size

Too large a task drifts; too small drowns in overhead. This is the most common way systems like this fail, so it gets a seam rather than a constant.

```python
class SizePolicy(Protocol):
    def estimate(self, task: Task) -> SizeVerdict: ...      # before dispatch
    def observe(self, attempt: Attempt) -> None: ...        # calibration after
```

Two sides deliberately: the real signal is retrospective (`iterations-to-green`), and an estimator with no feedback never calibrates.

- v1 adapter — `StaticThresholds`: file count in `allow`, number of acceptance commands, presence of more than one module.
- later — `HistoricalPercentile` over telemetry: predicted iterations from similar past tasks.

Rules of thumb until data exists: iterations-to-green consistently above three means tasks are too large; sandbox time below half of wall-clock time means they are too small to be worth the machinery.

## 7. Gate lifecycle

### 7.1 Where gates come from

Three sources, and they behave differently.

**Structural.** Derived from the task contract with no experience required — `scope`, `acceptance`, `decisions-reported`, `no-test-tampering`, `secrets`. They check properties of the contract rather than knowledge of a particular codebase, which is why they exist on day one and why the starting set in §4 is short.

**Distilled from leaks.** A defect reached review or production. Per `distill-the-rule` there are exactly two outcomes: a gate appears, or a written decision records that this class is caught by humans. "We will remember" is not one of them. This is the main growth path.

**Derived from convention documents.** A document states a machine-checkable rule and a gate falls out of it — `source-layout` from the document conventions (charter A-7), `bypass-count` from §6a.

### 7.2 Five filters before writing one

1. **Deterministic?** If it needs judgement it is a reviewer, not a gate (RFC 0005).
2. **Cheaper in seconds than the miss costs?** A ten-minute check for a rare defect does not pay.
3. **Can it go red?** If no diff can be written that fails it, it is not a check.
4. **Does it have an input?** One of `worktree`, `diff`, `task`, `log`. No data, no gate, however desirable.
5. **Will it name what it caught?** Per RFC 0011 §5, "gate failed" is not acceptable output.

### 7.3 States

```
proposed → shadow → blocking → quarantined → retired
              ↑         │
              └─────────┘   after a material tightening
```

**`proposed`** — written, sabotage case written, not yet in any manifest.

**`shadow`** — runs and reports, blocks nothing. Its false-positive rate is measured here, before it is given the power to stop work. This mirrors the shadow period for the reviewer in RFC 0005 §7, and for the same reason.

**`blocking`** — promoted against criteria, not against confidence:

- ran over at least N real changes (start with 30)
- fired at least once
- false positives below threshold on human assessment
- p95 duration acceptable for its position in the ordering
- sabotage case green

**A gate that never fired in shadow is either unnecessary or broken, and only the sabotage suite distinguishes the two.** Sabotage red plus real-world silence means the gate is dead and nobody knew.

**`quarantined`** — flaking, or calibration has drifted. Does not block, is not removed, so the decision is made on data rather than in irritation.

**`retired`** — removed from the manifest, implementation and sabotage case deleted together.

### 7.4 Two independent tracks

A gate has two lives, and conflating them causes trouble:

| | Where | Governs |
| --- | --- | --- |
| Implementation | the `torve` package | the code, its sabotage case, its version |
| Activation | the repository's `gates.yaml` | whether it is on, in which state, with what parameters |

The same gate may be `blocking` in one repository and `shadow` in another. This is necessary: rolling a new gate out to ten projects simultaneously is how ten teams become annoyed at once.

### 7.5 Manifest entry

```yaml
- name: no-test-tampering
  state: blocking            # shadow | blocking | quarantined
  origin: leak/T-0142        # leak/<task> | rfc/<id> | structural
  added: 2026-08-14
  input: diff
  timeout: 30s
```

`origin` costs one line and answers "why do we have this" for as long as the gate exists. Without it, a gate is eventually removed for the wrong reason or kept for no reason.

`state` in the manifest, not `blocking: true` — a boolean cannot express shadow or quarantine, and those are where a gate spends its most informative periods.

### 7.6 Health

Per gate, all derivable from attempt telemetry (§8):

| Metric | Reading |
| --- | --- |
| hit rate | how often it fires |
| bypass count | high means the gate is miscalibrated, not that people are careless |
| flake rate | from the `flaky` outcome in §6a |
| duration p50/p95 | its cost |
| first-attempt pass rate | how well the paired skill is working |

Review quarterly. A gate nobody has looked at in a year is a gate nobody can justify.

### 7.7 Retirement

Signals:

- **No fires over a long window, sabotage green.** The defect class stopped occurring or became structurally impossible — types, architecture. Retire.
- **No fires, sabotage red.** The gate is broken, not the code clean. Fix, do not retire. *This distinction is the reason the sabotage suite runs continuously rather than once at authoring time.*
- **High bypass count.** Narrow its scope or remove it.
- **Long quarantine for flakiness.** Rewrite or remove.
- **Superseded by an earlier check.** What a type checker now catches need not be caught at runtime.

**Retirement requires the same evidence as adoption.** Without that rule, the first difficult sprint removes half the checks under the banner of unblocking delivery.

Retiring a gate retires its sabotage case and shrinks or removes its paired skill (RFC 0009 D-9.5) in the same change — otherwise an instruction survives for a rule that no longer exists.

### 7.8 Tightening is a new gate

Materially tightening an existing gate produces a different gate as far as telemetry is concerned, and before/after comparisons cannot cross that boundary. `config_hash` (§8) captures it, but a substantial tightening should also go back through `shadow`.

## 8. Telemetry from day one

Even without a store, each run appends one JSONL record. Three fields must be right from the first line, because none can be reconstructed later:

- `schema_version`
- `config_hash` — a digest of `gates.yaml`, the skill set, and the tier mapping in force. Without it, "did this gate pay for itself" is unanswerable, because there is no way to tell which runs were under which regime.
- decisions **denormalised into the record**, not referenced — a cross-store join is exactly what will not work at this stage.

## 9. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-2.1 | `LOCKED` | Gates ship as an installed package, not copied scripts | `pyproject.toml` `src/torve/gates/**` | The whole point of this increment |
| D-2.2 | `LOCKED` | Every gate has a sabotage case; no case, no gate | `src/torve/gates/sabotage.py` | Enforced in the package's own CI |
| D-2.3 | `ASSUMED` | The starting set is the five above and grows from observed leaks | `src/torve/gates/**` | Deliberately not exhaustive |
| D-2.4 | `ASSUMED` | Telemetry is JSONL until a query demands otherwise | `src/torve/telemetry.py` | Storage is reversible, record shape is not |
| D-2.5 | `ASSUMED` | Gate manifests live in the consuming repository, not in the package | `.torve/gates.yaml` | Depart if the same manifest gets duplicated across every repository anyway |
| D-2.6 | `LOCKED` | `flaky` is a distinct outcome that does not consume an attempt | `src/torve/shell.py` | Otherwise flakes silently eat the poison ceiling |
| D-2.7 | `LOCKED` | Bypass requires a human signature, a mandatory reason, a log entry and a counter | `src/torve/runner.py` `src/torve/context.py` | An uncounted bypass becomes an invisible workaround |
| D-2.8 | `LOCKED` | Secret scanning blocks and cannot be bypassed | `src/torve/gates/secrets.py` | The one failure class that a follow-up commit cannot repair |
| D-2.9 | `ASSUMED` | Task size is a port with pre-dispatch estimate and post-hoc calibration | `src/torve/sizing.py` | Static thresholds first; telemetry-driven later |
| D-2.10 | `ASSUMED` | `self-audit` is a deterministic log-presence check (input `log`): the execution log must exist and carry a `Drift count` line; agent-side audits arrive with RFC 0004. Added by execution 2026-08-21 | `src/torve/gates/self_audit.py` | Departs from §4's `worktree` input until a runner exists |
| D-2.11 | `LOCKED` | The bypass signature is a `Torve-Bypass: <gate>: <reason>` commit trailer; the record carries the commit's author, is counted per gate and appended to the task log; secrets stays exempt. Added by execution 2026-08-21 | `src/torve/runner.py` `src/torve/context.py` | Makes D-2.7 checkable in engine-less CI |
| D-2.12 | `ASSUMED` | A test edit is licensed when the file falls inside the task's `scope.allow`; adding a new test file is never tampering. Added by execution 2026-08-21 | `src/torve/gates/no_test_tampering.py` | Defines the licence `no-test-tampering` checks |
| D-2.13 | `ASSUMED` | On runs with no task contract, task- and log-input gates report a recorded `skipped`, never a silent green. Added by execution 2026-08-21 | `src/torve/gates/base.py` | The degraded mode RFC 0005 §4 names, applied to gates |
| D-2.14 | `ASSUMED` | The secret scanner is a built-in high-confidence pattern set; false positives are suppressed only via `secrets.allow_patterns` in the reviewed manifest. Added by execution 2026-08-21 | `src/torve/gates/secrets.py` | Reviewed configuration, not a run-time bypass |
| D-2.15 | `ASSUMED` | The quarantine list is a reviewed manifest key, maintained from flake telemetry until the RFC 0003 store automates the threshold. Added by execution 2026-08-21 | `src/torve/manifest.py` | Keeps §6a's quarantine honest without a store |
| D-2.16 | `ASSUMED` | The scope gate implicitly allows the task's own contract and log files, and nothing else. Added by execution 2026-08-21 | `src/torve/gates/scope.py` | Prevents the log-writing deadlock in narrowly-scoped tasks |
| D-2.17 | `ASSUMED` | Gate cost for cheapest-first ordering is the declared timeout, ascending, manifest order breaking ties. Added by execution 2026-08-21 | `src/torve/runner.py` | Replace with measured p50 duration once telemetry accumulates |
| D-2.18 | `LOCKED` | A gate enters service through `shadow` before it may block. Added by amendment A-8 2026-08-21 | `.torve/gates.yaml` `src/torve/runner.py` | A miscalibrated blocking gate teaches people to route around it, and that becomes habit |
| D-2.19 | `LOCKED` | Every manifest entry carries `origin` and `state`. Added by amendment A-8 2026-08-21 | `src/torve/models.py` `.torve/gates.yaml` | Provenance is unrecoverable later; a boolean cannot express shadow or quarantine |
| D-2.20 | `LOCKED` | Implementation and activation are separate tracks; a gate may be in different states per repository. Added by amendment A-8 2026-08-21 | `src/torve/models.py` `.torve/gates.yaml` | Simultaneous rollout across repositories is how a gate gets rejected everywhere at once |
| D-2.21 | `LOCKED` | No fires plus a red sabotage case means broken, not unnecessary. Added by amendment A-8 2026-08-21 | `src/torve/gates/sabotage.py` `tests/test_sabotage.py` | The two look identical without the suite; this is why it runs continuously |
| D-2.22 | `LOCKED` | Retirement requires the same evidence as adoption. Added by amendment A-8 2026-08-21 | `.torve/gates.yaml` | Otherwise the first hard sprint removes half the checks |
| D-2.23 | `ASSUMED` | Promotion criteria: 30 real changes, at least one fire, acceptable false positives and p95, sabotage green. Added by amendment A-8 2026-08-21 | `.torve/gates.yaml` | Numbers tuned once real rates are known |

## 10. Exit criteria

- Five gates green in CI in at least two repositories, applied to human pull requests as well as agent ones.
- Sabotage suite passing, and observed to fail when a gate is deliberately broken.
- Two weeks of JSONL telemetry with `config_hash` populated — the baseline RFC 0004 will compare against.

## Amendments

### A-2 — 2026-08-21 — gate implementations belong to the package (amends §4)

**Found in implementation.** `log_check.py` was shipped inside the `flag-dont-flip` skill directory, so every repository installing the skill got its own copy — precisely the cross-repository copy-paste this RFC exists to remove.

**Changed:** gate implementations live in `src/torve/gates/` (`scope.py`, `decisions_reported.py`, `no_test_tampering.py`, `secrets.py`, `sabotage.py`), not in skill directories. The skill keeps one line naming its enforcing gate and loses its `scripts/` directory.

**A skill is not replaced by its gate.** The gate reports that an entry is missing; it cannot say when one should have been written. `flag-dont-flip` retains the plan gate, the readiness gate, the unlisted-decision rule, and how to phrase `claim` and `evidence`. Per D-9.5: the gate is the source of truth, the skill is how it is passed on the first attempt.

### A-8 — 2026-08-21 — gate lifecycle (adds §7, D-2.18 – D-2.23)

**Found in consolidation.** How a gate comes into existence, when it is allowed to block, and when it is removed was spread across six documents and complete in none of them. The load-bearing property (D-3: a gate runs where the agent cannot influence it) had a lifecycle nobody had written down.

**Added:** §7 — sources (structural / distilled from leaks / derived from convention documents), the five filters before writing a gate, the state machine `proposed → shadow → blocking → quarantined → retired`, the implementation/activation split, the manifest entry shape, health metrics, retirement signals, and the rule that a material tightening goes back through `shadow`. The sections that followed renumbered (§7 Telemetry → §8, Decisions → §9, Exit criteria → §10).

**Changed in §3:** the `Gate` model's `blocking: bool` is replaced by required `state`, plus required `origin` and optional `added` — done while no manifest existed outside this repository, so it is an edit rather than a migration. §4's starting set carries `origin: structural` throughout, with `self-audit` entering at `shadow` (it was the one non-blocking gate, which is what shadow is).

**Identifier note:** the consolidating instruction numbered these decisions D-2.10–D-2.15, but those identifiers were already taken by the T-0002 acceptance rows; per D-A.4 identifiers are never reused, so the lifecycle rows are D-2.18–D-2.23.

**Also edits:** RFC 0004 §6 (per-gate health metrics among the telemetry fields), RFC 0009 D-9.5 (retiring a gate shrinks or removes its paired skill in the same change), RFC 0011 §5 (cross-referenced from filter 5).
