---
id: "0001"
title: "Torve: charter"
status: accepted
implementation: partial
depends_on: []
informed_by: []
supersedes: []
superseded_by: null
amended_by: ["A-1", "A-4", "A-5", "A-11", "A-12", "A-17", "A-21", "A-22"]
owner: Lev Litvinov
description: >-
  Domain model, state machine, ports, and the graded-decision contract every child RFC inherits; deliberately excludes anything shippable.
schema_version: 1
---

# RFC 0001 — Torve: charter

- **Scope:** Defines what Torve is, the domain model, the state machine, the port boundaries, the task and decision contracts, and the decisions every child RFC inherits. Deliberately excludes anything shippable: gates, runner, adapters, review, merge and planner each have their own document.
- **Related:** [`forze`](https://github.com/morzecrew/forze) 0.6 · [`agent-skills`](https://github.com/morzecrew/agent-skills) · OpenSandbox · `digitaldrywood/detent` (prior art)
- **Supersedes:** the monolithic draft of 0001, split here into a charter plus six phase documents

---

## 1. What Torve is

A layer that turns a reviewed specification into machine-checkable work, runs agents against it under deterministic gates, and refuses to let anything land that cannot prove it did what it was told.

**Not an orchestrator.** Board-driven orchestration is solved — worktree per task, dispatch, PR, merge train, dashboards all exist in adjacent systems. Those scale *execution*. The bottleneck is *specification*: a human writes every issue, and nothing learned during execution flows back into the next one.

Torve's contribution is three things nobody adjacent has:

1. **Graded decisions with a conflict protocol.** A task inherits its RFC's decisions carrying `LOCKED` / `ASSUMED` / `OPEN`, and the grade dictates what the executor does when reality contradicts the plan: halt, depart and log, or decide and log.
2. **Scope as a machine-checked contract.** `allow` / `deny` globs make "touched something it shouldn't have" a red gate, and make conflicting tasks undispatchable in parallel rather than collidable at merge.
3. **A closed loop.** Execution facts project back into the session where the next phase is specified.

**Why this serves the goal.** A standing team is not a queue of workers; it is an organisation with shared knowledge of decisions already made. What makes a new engineer productive is onboarding into what was decided and why. Graded decisions plus an append-only execution log are that onboarding, machine-readable, replayed into every task automatically.

## 2. Document map

Each child RFC is a shippable increment. Ship in order; each is useful standing alone.

| RFC | Increment | Ships |
| --- | --- | --- |
| 0002 | Gates as a library | `torve gates` in CI, no runner, no store |
| 0003 | Runner and isolation | `torve run` against a fake agent, sandboxed |
| 0004 | Agent adapters and tiering | real agents, shadow runs, telemetry |
| 0005 | Review as a run | replaces proprietary PR reviewers |
| 0006 | Merge train and escalation | serialized landing, attention budget |
| 0007 | Planner and context | `torve plan`, `torve context`, MCP |
| 0008 | Tracker projection | any board as a presentation surface |
| 0009 | Skills and evals | what agents know, versioned and measured |
| 0010 | VCS, provenance and revert | how work lands and how it is undone |

**Decisions are inherited downward, never re-decided.** A child RFC cites `D-n` from this document. If a child needs a charter decision changed, it writes an amendment against 0001 and gets it reviewed — it does not contradict it locally. This is the same rule the engine enforces on tasks, applied to its own specification.

## 3. Domain

Modeling an entity on this substrate means a family of four Pydantic models — domain (identity, revision, invariants), create command, update command (merge-patch, all fields optional), read model (may add computed fields or hide some) — wired into a document spec. They are four contracts, not four copies; the divergence is the point.

**Torve's application is unusual: no aggregate has an update command.**

| Aggregate | Domain | Create | Update | Read |
| --- | --- | --- | --- | --- |
| `Task` — the contract | ✓ | ✓ (minted by `torve plan`) | **none** | ✓ |
| `Attempt` — one run of any role | ✓ | ✓ (written once, at the end) | **none** | ✓ |
| `ReviewFeedback` — the human fields | ✓ | ✓ (after merge) | **none** | ✓ |

The absence is the design. A task contract that can be patched is a contract that drifts out from under an executor mid-run — **a changed contract is a new task**, minted from a re-reviewed RFC. An attempt that can be edited is an audit trail that cannot be trusted. The only genuinely mutable state — run status, lease, owner, attempt counter — is not modelled here at all; it belongs to the durable run store (§5).

`GateResult`, `Finding` and `InheritedDecision` have no independent identity or lifecycle and stay value objects.

```python
class Task(Document):
    schema_version: int
    id: TaskId
    rfc: str | None
    phase: int
    role: Literal["implement", "review"]        # see RFC 0005
    intent: str                                  # one paragraph: what changes and why — never steps (A-11)
    depends_on: list[TaskId]
    scope: Scope                                 # allow/deny globs
    acceptance: list[str]                        # shell commands; exit 0 == satisfied
    decisions: list[InheritedDecision]
    budget: Budget                               # iterations, wallclock, tokens
    tier: Literal["planner", "executor", "reviewer"]

class InheritedDecision(BaseModel):
    id: str                                      # D-3, matching the RFC's table
    grade: Literal["LOCKED", "ASSUMED", "OPEN"]
    text: str
    paths: list[str] = []                        # declared area; enables the silence check

class Attempt(Document):
    schema_version: int
    task_id: TaskId
    role: Literal["implement", "review"]
    n: int
    worker_id: str
    started_at: datetime
    finished_at: datetime | None
    gate_results: list[GateResult]
    findings: list[Finding]
    outcome: Outcome
    cost: Cost
    trace_ref: str | None                        # harness session trace, if any
    config_hash: str                             # gates + skills + tier mapping in force

class Finding(BaseModel):
    severity: Literal["blocker", "major", "minor", "nit"]
    kind: Literal["spec-drift", "correctness", "test-gap", "security", "style"]
    location: str                                # file:line
    claim: str
    evidence: str                                # must be locatable in diff or gate log
```

Read models earn their place by diverging: `TaskRead` adds `is_dispatchable` and `blocked_by`, `AttemptRead` adds `duration` and `gates_passed` — both as computed fields **in Python, above the port**, because anything derived below a port is invisible to simulation.

*Amendment 2026-08-21 (A-4):* task contracts are **derived artefacts**, lockfile-grade — `torve plan` mints them mechanically, and "reviewed intent" overstated it. They belong in git for two reasons stronger than review: **reproducibility** (an attempt pins to a sha, so reconstructing why something landed retrieves exactly the contract the agent saw — replayable in a way no database row is) and **refusability** (the test is not "a human wrote this" but "a human can see it in a diff and refuse it"). The store holds only a reference: `task_id` plus sha.

## 4. State machine

```text
queued → claimed → running → gated → reviewed → ready
                      │        │         │
                      └────────┴─────────┴──→ escalated → (human) → queued | abandoned
```

1. **Transitions are executed by the runner from facts, never by a model.** Facts are exit codes, gate outcomes, PR status, approval status. An agent reports observations; it never causes a transition.
2. **`ready` is not `merged`.** The engine stops at mergeable (RFC 0006).
3. **Poison ceiling.** Attempts increment on entry to `running` and are checked before dispatch. Ceiling reached → `escalated`, never another retry.
4. **Escalation reasons are enumerated:** `budget_exhausted`, `poison_ceiling`, `locked_conflict`, `merge_conflict`, `blocker_finding`, `gate_infrastructure_failure`, `lease_expired`.
5. **`locked_conflict` is terminal by design, not an error** — the one case where a task stops on working code.

*Amendment 2026-08-21 (D-29, D-30):* the vocabulary above is completed by `cost_anomaly` (§5.2) and `killed` (RFC 0006 §5a) and closed as one enum; and `claimed → escalated` is a legal transition, covering a runner that dies between claim and first dispatch.

*Amendment 2026-08-22 (A-21):* `underspecified` joins the vocabulary — a contract whose execution would require inventing three or more load-bearing decisions halts as a specification defect (0003 A-18), separable in telemetry from `locked_conflict`; both are halts on working judgement, but one indicts the contract and the other the code.

*Amendment 2026-08-22 (A-22):* `stale_inheritance` joins the vocabulary — a non-terminal task minted from a document that later became superseded carries decisions that no longer stand (0007 §3.3); `torve plan --reconcile` makes the situation visible by escalating it, and what to do with the in-flight work stays a human decision.

## 5. Ports

| Port | Backed by |
| --- | --- |
| `TaskStore` | forze durable run store — enqueue, begin, renew, complete, fail, `claim_abandoned`, fenced cancellation |
| `Workspace` | git worktree |
| `Runtime` | OpenSandbox (RFC 0003) |
| `Agent` | fake, API, harness, subscription (RFC 0004) |
| `GateRunner` | shell in the task sandbox (RFC 0002) |
| `SCM` | `gh` CLI |
| `Telemetry` | JSONL → analytics contract (RFC 0004) |
| `Notifier` | outbox destination |
| `ContextRead` | read-only MCP (RFC 0007) |
| `Tracker` | outbound projection, restricted inbound commands (RFC 0008) |
| `DecisionSource` | importing standing decisions into new tasks (RFC 0007) |
| `SizePolicy` | pre-dispatch size estimate and post-hoc calibration (RFC 0002) |
| `PromotionPolicy` | what may land without a human (RFC 0006) |
| `Vcs` | commit, branch, sign, revert (RFC 0010) |

*The `Inference` port was removed by A-11 2026-08-22: a reviewer reached through a separate port stops being a run. The reviewer goes through `Agent` like every other run (RFC 0005).*

`Workspace` (local git) and `SCM` (remote forge) stay separate — merging them binds the domain to GitHub.

`TaskStore` is **not written by hand**. The substrate's self-hosted durable tier already provides lease semantics, recovery of abandoned runs, and cancellation that is cooperative on the ask and fenced on the landing — so a stale worker cannot cancel a run out from under its new owner, and if the holder dies carrying the request, recovery lands it without invoking the body. Getting this wrong by hand is the default outcome.

Consequence for agent adapters: cancellation is observed within one heartbeat interval, and a body that never awaits is bounded only by maximum run duration. Agent processes need their own hard timeout, not merely a cooperative request.

### 5.1 Where abstraction is allowed

Ports are expensive: each is an interface, a mock, a conformance expectation and a place for behaviour to diverge. The rule that keeps this from becoming a framework:

**Abstract what is replaced wholesale. Never abstract what must stay identical for data to be comparable.**

| Port justified | Deliberately not a port |
| --- | --- |
| `Agent`, `Runtime`, `TaskStore`, `Tracker`, `Vcs` | the state machine — pluggability destroys D-5a |
| `DecisionSource`, `SizePolicy`, `PromotionPolicy` | the escalation-reason vocabulary — an extensible enum makes telemetry incomparable across time |
| `Telemetry`, `Notifier` | gates — declarative config, not an interface |
| | the execution-log format — a second format means two parsers and eventual divergence |

When unsure, ask whether two implementations would ever run in the same organisation. If not, it is configuration.

### 5.2 Global budget

Per-task budgets bound one task. They do not bound the system: a defect that loops fifty tasks at three dollars each costs a hundred and fifty before anyone notices.

```yaml
budget:
  daily_usd: 40
  weekly_usd: 150
  on_exhausted: halt_dispatch      # in-flight tasks finish; nothing new starts
  anomaly: p95_task_cost           # a task above this escalates as cost_anomaly
```

`halt_dispatch` rather than kill: work already paid for should finish and be inspectable. `cost_anomaly` joins the enumerated escalation reasons.

## 6. Task contract and execution log

The task contract (`tasks/T-nnnn.yaml`) and gate manifest (`gates.yaml`) live in git and arrive in pull requests, because they are reviewed artefacts. Everything generated — attempts, gate results, findings, telemetry — goes through ports into a store.

Pydantic models are the single source of truth; YAML is their serialization. No hand-maintained JSON Schema until a non-Python consumer exists.

**Execution log format** — `logs/<task-id>.yaml`, one file per task, append-only, one `entries:` list *(amended by A-1 2026-08-21: serialization moved from markdown with fenced blocks to YAML; every rule below is unchanged)*:

```yaml
schema_version: 1
task: T-0142
repo: morzecrew/torve
base_sha: 7f3a91c8e2b4d6a1f0c3   # evidence resolves against this commit (D-A.7)
created_at: 2026-08-20T10:58:03Z
drift_count: 0            # the declared claim, checked against entries classed drift
entries:
  - decision: D-3
    grade: LOCKED
    kind: contradicted    # contradicted | departed | resolved | blocked
    at: 2026-08-20T11:04:12Z
    attempt: 2
    claim: sessions cannot live in Redis; this deployment has no Redis service
    evidence: infra/compose.yaml:1-40 — no redis service defined
    action: halted        # halted | departed | decided
    notes: |
      Prose lives inside the entry, never in a sibling document.
```

Grade and action legality — the conflict protocol as a checkable table:

| Grade | Legal action | Illegal |
| --- | --- | --- |
| `LOCKED` | `halted` | anything else |
| `ASSUMED` | `departed` | `halted` (over-caution) |
| `OPEN` | `decided` | `halted` |

Two rules that make the log worth keeping: **grade is copied at write time, never resolved at read time** — a log that rewrites its own past is not an audit trail; and **silence is a finding** — the gate fires on the absence of an entry in an area a locked decision declares, because a quiet workaround is not detectable while a missing report trivially is.

*Amendment 2026-08-21 (D-21b):* the legality table governs contradictions. An entry carries `kind` as above, or the `flag-dont-flip` skill's `class` (`discovery | spec-gap | drift | irreducible`), or both — the axes are orthogonal: `kind` records what happened to the decision, `class` diagnoses whether the design process failed. A `kind: resolved` close-out with `action: decided` attests compliance in an area a `LOCKED` decision declares, which is how the silence check is satisfiable without a conflict.

## 7. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-1 | `LOCKED` | Two modules over one domain; executor is the sole writer of task state, planner is read-only plus task minting | `src/torve/**` | — |
| D-2 | `LOCKED` | Models never decide **what work exists** or **whether it is finished**. The planner invokes no model at all. Execution and review invoke models, but their output is data and the consequence is set by config. Stated precisely: a reviewer model does choose a `severity`, and configuration chooses what a severity does — so severity calibration is a **measured quantity** (RFC 0005 §6), not a trusted one | `src/torve/**` | — |
| D-3 | `LOCKED` | Gates execute outside the agent session; an agent cannot report a gate outcome | `src/torve/gates/runner.py` `src/torve/application/runner.py` | — |
| D-4 | `LOCKED` | The sandbox is the unit of lifecycle; nothing runs on a host | `src/torve/adapters/**` | — |
| D-4b | `LOCKED` | Agents never hold real credentials; outbound secrets are injected by the runtime's vault | `src/torve/adapters/**` | — |
| D-5 | `LOCKED` | `TaskStore` is a thin facade over the substrate's durable run store, not hand-written | `src/torve/application/taskstore.py` | — |
| D-5a | `LOCKED` | The lifecycle is not modelled as a durable workflow; the run store is a leased queue with recovery | `src/torve/application/taskstore.py` | — |
| D-6 | `LOCKED` | The engine never resolves conflicts and never merges without the configured approval | `src/torve/application/runner.py` | — |
| D-21a | `LOCKED` | The execution-log format is defined here; one file per task, append-only, grade copied at write time. *(Amended by A-1 2026-08-21: serialization is YAML, `logs/<task-id>.yaml`; substance unchanged.)* | `.torve/tasks/**` `src/torve/gates/decisions_reported.py` | — |
| D-22 | `LOCKED` | Three aggregates, each with domain, create and read models and **no update command** | `src/torve/domain/**` | — |
| D-25 | `LOCKED` | The differentiator is the specification layer, not the execution runtime | `src/torve/**` | — |
| D-7 | `ASSUMED` | Python on the forze substrate; Go rejected | `pyproject.toml` | — |
| D-8 | `ASSUMED` | Pydantic is the single contract source | `src/torve/domain/**` | — |
| D-16 | `ASSUMED` | `schema_version` on every persisted aggregate | `src/torve/domain/**` | — |
| D-26 | `ASSUMED` | Build our own runtime rather than adopt the adjacent one; time-box a teardown of three of its mechanisms first | — | — |
| D-27 | `LOCKED` | Git and the store are a boundary, not a prohibition: git holds what should be (contracts, manifests, RFCs, decision tables, logs — diffable, sha-pinned, reviewed), the store holds what happened (runs, leases, attempts, results, telemetry). The engine may project git-held artefacts into the store for querying, one-way and read-only; the store is never authoritative for them. *(Reworded by A-4 2026-08-21 from "nothing ever moves from git into a database".)* | `.torve/tasks/**` `.torve/tasks/**` `rfcs/**` | — |
| D-28 | `ASSUMED` | The engine gets a weekly time budget with a named owner; three consecutive overruns mean maintenance mode | — | — |
| D-21b | `LOCKED` | A log entry carries `kind` (contradicted / departed / resolved / blocked), or the skill's `class`, or both; a `kind: resolved` close-out with `action: decided` is the legal attestation of compliance in a touched `LOCKED` area. Added by execution 2026-08-21 | `.torve/tasks/**` `src/torve/gates/decisions_reported.py` | — |
| D-29 | `ASSUMED` | The escalation vocabulary is §4's list plus `cost_anomaly` (§5.2), `killed` (RFC 0006 §5a), `underspecified` (added by amendment A-21 2026-08-22) and `stale_inheritance` (added by amendment A-22 2026-08-22), fixed in one closed enum in the domain module; any further addition is an RFC amendment, never a code change. Added by execution 2026-08-21 | `src/torve/domain/states.py` | — |
| D-30 | `ASSUMED` | `claimed` may transition to `escalated`: a runner that dies between claim and first dispatch needs a legal exit, and the durable store's `claim_abandoned` recovery lands on the same edge. Added by execution 2026-08-21 | `src/torve/domain/states.py` | — |
| D-31 | `LOCKED` | Agents do not communicate; the runner coordinates. What an agent may touch and what was already decided are copied into its contract; what others are doing is the runner's knowledge for overlap-free dispatch, never the agent's. Falsifiable: revisit only if telemetry shows tasks escalating with "insufficient context about adjacent work". Added by amendment A-5 2026-08-21 | `src/torve/application/ports.py` `src/torve/application/runner.py` | — |
| D-21c | `ASSUMED` | The YAML log carries a top-level `drift_count` scalar checked against entries classed `drift`; revising the claim edits the scalar (git history preserves prior claims) while `entries` stays append-only. Added by execution 2026-08-21 | `.torve/tasks/**` `src/torve/gates/decisions_reported.py` | — |
| D-21d | `ASSUMED` | Bypass records live in a separate top-level `bypasses:` list in the same log file, appended structurally (parse, append, dump); append-only means items are never removed or edited, not that bytes are only appended. Added by execution 2026-08-21 | `.torve/tasks/**` `src/torve/gates/runner.py` | — |
| D-32 | `ASSUMED` | Every corpus decision table carries a Paths column naming the module areas a decision governs; `LOCKED` rows must carry paths, enforced by the shipped `rfc_index.py`. For RFCs not yet built the globs name the intended module and are refined when it exists. Added by execution 2026-08-21 | `rfcs/**` `src/torve/config/rfc_parse.py` | — |
| D-33 | `ASSUMED` | Two strict typecheckers gate `src` as blocking commands — `mypy src` and `basedpyright src` — with `[tool.pyright]` in pyproject as the repo-canonical strict config every editor reads; tests, scripts and skills carry no type floor. Added by execution 2026-08-21 | `pyproject.toml` `.github/**` `.torve/gates.yaml` | — |
| D-34 | `ASSUMED` | The TaskStore facade and store adapters are typed against forze's exported contracts (`DurableRunStorePort`, `DurableFunctionHandler`, `JsonDict`), so a forze upgrade that changes the durable surface fails typecheck at the pin bump rather than surfacing as adapter behaviour. Added by execution 2026-08-21 | `src/torve/application/taskstore.py` `src/torve/adapters/store/durable.py` | — |
| D-35 | `ASSUMED` | Typing pattern at data boundaries: parsed-YAML documents are cast to `dict[str, Any]` at exactly one boundary per reader and stay typed inward; optional dependencies load via `import_module` (explicit Any), never from-imports of partially-unknown names; container initialisers are always annotated. Added by execution 2026-08-21 | `src/torve/**` | — |
| D-A.7 | `LOCKED` | Task logs carry `repo` and `base_sha` | `.torve/tasks/**` | Makes evidence resolvable against the commit the agent actually saw |
| D-1.7 | `LOCKED` | A task contract carries an `intent` paragraph stating what changes and why; it never carries steps. Added by amendment A-11 2026-08-22 | `src/torve/domain/task.py` `.torve/tasks/**` | Without it an executor infers intent from acceptance commands, which is guessing; with steps in it, the plan gate becomes theatre |
| D-A.13 | `LOCKED` | One directory per task holding contract and log; path resolution lives in one module. Added by amendment A-12 2026-08-22 | `src/torve/config/layout.py` `.torve/tasks/**` | Retention, sharding and pairing all follow from it; scattering path construction makes any later move a hunt |
| D-A.14 | `LOCKED` | Task deletion is supported; no code assumes a contract is present on disk. Added by amendment A-12 2026-08-22 | `src/torve/**` | Retention later collides with code that assumes the file is always there, which is a refactor rather than a feature |
| D-A.15 | `LOCKED` | Deletion requires prior promotion of `resolved` and `departed` entries into decision tables. Added by amendment A-12 2026-08-22 | `.torve/tasks/**` `rfcs/**` | That promotion is the only unique information a log carries |
| D-1.8 | `ASSUMED` | A terminal task's contract and log are historical records: a corpus reorganisation never edits them, and identifier preservation is what keeps their citations resolving. Added by execution 2026-08-22 — see .torve/tasks/T-0017 | `.torve/tasks/**` | — |

The nineteen corpus-convention decisions that lived here (D-A.1 – D-A.12, D-A.16 – D-A.20) moved to RFC 0016 with their identifiers preserved (A-17). D-A.7 and D-A.13 – D-A.15 stay: they concern task directories, logs and retention, which are engine artefacts.

### 7.1 Ownership

Nothing in this corpus works without names attached, and at this team size that is not bureaucracy — an unnamed duty does not exist.

| Duty | Meaning |
| --- | --- |
| Harness owner | receives `gate_infrastructure_failure`, owns the gate package and its sabotage suite |
| Escalation triage | works the escalation queue during the day's review windows |
| Budget owner | notified on `cost_anomaly` and on daily-cap halt |
| Charter owner | the only person who merges amendments to this document |

One person may hold several. What matters is that each is a name in a file, reviewed like anything else.

## 8. Risks

- **The engine becomes a second product.** The realistic failure mode for a small team. Mitigation: a named owner and an explicit weekly time budget, decided before 0003 starts.
- **Human review becomes the bottleneck.** Parallel workers outpace one reader; the system quietly produces review debt. Addressed in 0006.
- **Gate theatre.** Checks that cost minutes and catch nothing. Addressed in 0002 via the sabotage suite and per-gate hit counts.
- **This document read as licence to automate planning.** The planner is where model calls will try to creep in. D-2 is `LOCKED` and its violation shows up in review as a new dependency.
- **A second infrastructure dependency** (OpenSandbox). Health indicators are strong but neutrality is younger than the code; re-run `dependency-diligence` before 0003, and keep `Runtime` thin enough that raw Docker remains a fallback.

## 8a. Stopping

This is a tool built for one team's own use, not a product. It therefore gets no success threshold and no kill metric — measuring an internal tool against a target invents a standard nobody asked for. What it gets instead is a bound on the input and a guarantee that stopping is always cheap.

### Time budget

The scarce resource is attention, not money.

```yaml
budget:
  engine_hours_per_week: 6
  owner: <name>                 # §7.1 charter owner
  on_overrun: |
    Three consecutive weeks over budget puts the project into maintenance
    for the remainder of the current increment: bug fixes only, no new scope.
```

Nothing to compute and nothing to instrument — either the hours went in or they did not. The rule exists so that overrun becomes a decision rather than a quarter that felt strange in retrospect.

### Reversibility as a design requirement

Stopping is cheap only if nothing valuable is trapped inside the engine. That is currently true and must stay true — it is a requirement, not an accident:

| Artefact | Lives in | Survives deletion of Torve |
| --- | --- | --- |
| Gates | the consuming repository's CI | yes |
| Task contracts, gate manifests | git | yes |
| RFCs and decision tables | git, markdown | yes |
| Execution logs | git, markdown | yes |
| Skills | a versioned package | yes |
| Telemetry | JSONL / analytics store | yes |
| The runner | the engine | **no — and that is the only thing** |

**Therefore: no contract, decision or log ever moves out of git into a database.** The first "it would be more convenient to store task contracts in Postgres" is the change that breaks this, and it will sound reasonable when it arrives. Refuse it. The store holds what the engine generates; git holds what a human reviews. That boundary is what makes every stage a resting point rather than a commitment — abandoning after 0002 leaves gates running in CI, after 0003 a reproducible isolated runner, and so on.

### Pre-recorded doubts

Not metrics, and not thresholds — four qualitative signals written down now so that a later explanation of why everything is fine has something to answer to. Revisit them once a month, alone, in five minutes: debugging Torve has taken more time than product work for three weeks running; the escalation queue has not been empty once in a month; only one person can start and repair the system; and the last three things it caught would have been caught by a human reviewer anyway.

## 9. Out of scope, permanently

- **Model-side planning and decomposition.** The exact non-determinism this project removes.
- **Automatic conflict resolution.** Silently grants a model the right to rewrite other people's work.
- **Multi-tenancy.** One team, trusted operators.

## Amendments

### A-1 — 2026-08-21 — log serialization (amends D-21a, §6)

**Found in implementation.** Extracting entries from markdown required a regular expression — a reliable sign the data was in the wrong container. Task contracts were already YAML; the log being markdown was an inconsistency with no reason behind it.

**Changed:** `logs/<task-id>.md` with fenced ```divergence``` blocks → `logs/<task-id>.yaml`, one `entries:` list. Prose stays inside the entry, in `notes:` — a sibling `.md` beside the `.yaml` was considered and rejected as two sources of truth in the one artefact that exists to have exactly one. JSONL was rejected as materially worse to read in a pull request during escalation triage.

**Unchanged — the substance of D-21a stands in full:** one file per task, append-only, grade copied at write time, silence is a finding, evidence must be locatable.

**Migration:** a single-use `scripts/migrate_logs.py`; the gate accepts YAML only — dual-format support is two code paths forever, so compatibility lived in the converter and died with it.

**Also edits:** RFC 0003 §7 (example), the shipped `flag-dont-flip` skill (format section).

### A-4 — 2026-08-21 — git and store: a boundary, not a prohibition (amends D-27)

**Found in review.** D-27 read as "nothing ever moves from git to a database", which raised a fair question: how does anything query execution history, then? The decision was written as a ban when it is a division of authority.

**The boundary:** git holds what should be (task contracts, gate manifests, RFCs, decision tables, divergence logs — diffable, sha-pinned, reviewed); the store holds what happened (run state, leases, attempts, gate results, findings, telemetry). The engine **may** index git-held artefacts into the store for querying; what is forbidden is the reverse — making the store authoritative for them. One direction: git → store, read-only projection.

**Also clarified — task contracts are derived artefacts**, lockfile-grade: `torve plan` mints them mechanically. They belong in git for reproducibility (an attempt is pinned to a sha; six months later the contract the agent saw is retrievable) and refusability (a human can see it in a diff and refuse it). The store holds only `task_id` plus sha.

### A-5 — 2026-08-21 — agents do not communicate; the runner coordinates (records D-31)

**Raised in review:** if execution facts live in a store, how do agents become aware of what other agents are doing? **They do not, by design.** What an agent may touch is copied into its contract; what has already been decided is copied there too, with grades; what others are doing is known to the runner, which uses it to avoid dispatching overlapping tasks. Quality comes from every agent receiving a complete, isolated, non-overlapping contract — not from agents sharing knowledge. Knowledge accumulates as facts in the store and is read once per phase by a human with an expensive model, who writes the next contracts.

**Falsifiable prediction:** if this model is wrong, tasks escalate with "insufficient context about adjacent work". Until that appears in telemetry, no change.

### A-11 — 2026-08-22 — task intent, and removal of the Inference port (amends §3, §5)

**Found in implementation.** Two defects surfaced together while wiring the reviewer. *(The source patch numbered this A-8 and its context-assembly decision D-3.7; both were taken, so they land as A-11 and D-3.19 per D-A.4/D-A.5.)*

The task model carried no field stating what a task is *for*: `scope` says where, `acceptance` says how it will be judged, `decisions` says what binds it — and the change itself appeared nowhere. An executor was left inferring intent from acceptance commands, which is guessing.

Separately, the `Inference` port contradicted D-5.1 in 0005. A reviewer reached through a separate port is not a run, and loses sandbox, budget, cancellation, `Attempt` and telemetry with it.

**Changed:** `Task` gains `intent: str` — one paragraph on what changes and why, never steps (D-1.7). The `Inference` port is removed from the port table and from §5; the reviewer runs through `Agent` like every other run, with the cross-model requirement met by pointing `tier: reviewer` at a different vendor.

**Unchanged:** D-5.1 stands, and is the reason for the second change rather than a casualty of it. Everything else about review in 0005 is unaffected.

**Also edits:** 0002 §4 (acceptance skipped for `role: review`), 0003 §5a and D-3.19 (context assembly), 0005 §1.1/§3/decisions, 0007 §3.

### A-12 — 2026-08-22 — task directories and retention (amends §6)

**Found in use.** `tasks/` and `logs/` grew as parallel trees with only a matching filename relating them, so retention would have had to delete from two places and sharding would have had to be decided twice. *(The source patch numbered this A-10; that was taken, so it lands as A-12 per D-A.5.)*

**Changed:** one directory per task — `.torve/tasks/T-0142/` containing `contract.yaml` and, when anything was written, `log.yaml`. If sharding is ever needed it happens by RFC or phase, never by date, and is one function in `config/layout.py` plus a migration — path resolution lives there and nowhere else (D-A.13).

**Added:** task deletion is a supported operation. A contract holds no unique information once its work has landed — the `Attempt` in the store carries `task_id`, `config_hash` and the sha, and the contract is recoverable from the commit that introduced it. Deletion requires that `resolved` and `departed` entries have first been promoted into decision tables, and that no non-terminal task references the directory.

**Consequence to observe now:** no code may assume a contract is present on disk. A task is resolved by `task_id` plus sha through the store; the contract is read from git only when needed, and its absence is tolerated. The retention mechanism itself stays unbuilt — its threshold will be chosen from real volume.

**Not changed:** logs stay in git. Both reasons still hold — evidence arrives in a pull request beside the diff, and `base_sha` keeps it resolvable.

**Also edits:** 0003 (amendment A-13, logs created by writing), 0013 §1 (layout diagram note).

### A-17 — 2026-08-22 — corpus conventions extracted to RFC 0016 (amends §7, §10)

**Found in review.** Half this document's amendments and forty-five per cent of its decisions were about keeping a corpus of documents rather than about Torve. The mismatch is categorical: those rules apply to a repository with no engine in it. They lived here because A-7 had nowhere else to put them, and every later clarification about numbering or indexing then amended the document that defines what Torve is. *(The source patch numbered this A-16; that was taken by 0013's corpus-path amendment, so it lands as A-17 per D-A.5.)*

**Changed:** nineteen `D-A.*` decisions and amendments A-7, A-9, A-10, A-14 and A-15 move to RFC 0016, keeping their identifiers exactly. The §10 prose explaining how amendments work is a convention and moves with them; the `## Amendments` container section stays, here and in every document.

**Deliberately not moved:** D-A.7, D-A.13, D-A.14 and D-A.15. They carry the `D-A.` prefix but concern task directories, logs and retention — engine artefacts. The prefix records when a decision was written, not where it belongs, and the split is by subject. Renumbering was rejected: it would break citations in 0007, 0011, 0013 and 0015 and violate D-A.4.

**Closed:** `D-A.*` takes no new members. New decisions use `D-<rfc-number>.<n>`.

**Also edits:** 0007, 0011, 0013, 0015 gain `depends_on: ["0016"]`.

### A-21 — 2026-08-22 — `underspecified` joins the escalation vocabulary (amends §4, D-29)

**Found in specialising `flag-dont-flip` for autonomous execution (0003 A-18).** The rewritten skill halts when a contract needs three or more load-bearing decisions invented — a specification defect, reported rather than papered over. The halt had no reason of its own: riding `locked_conflict` conflates two different indictments. Both are halts on working judgement, but a `locked_conflict` says the code contradicts a settled decision, while an underspecified contract says the *document* was not ready — and the whole point of separable telemetry (D-29's closed enum exists for comparability) is that those two populations answer different questions: the first measures drift, the second measures planning quality.

**Changed:** `underspecified` joins the enum, projected to exit 2 with the rest of the human-decision family (0011 D-11.9). The triage response differs from every other member: the fix is an amendment to the source document and a re-mint, never a retry of the task.

**Deliberately unchanged:** the threshold stays in the skill (three or more load-bearing gaps), not in the engine — the engine cannot count what a contract fails to say; it can only record that an agent said so, which is exactly the D-2 division.

**Also edits:** 0011 D-11.9 (the projection row).

### A-22 — 2026-08-22 — `stale_inheritance` joins the escalation vocabulary (amends §4, D-29)

**Found in executing 0007 §3.3.** `torve plan --reconcile` marks every non-terminal task minted from a document that later became superseded: those tasks inherit decisions that no longer stand, and running them anyway executes a specification nobody stands behind. The situation had no reason of its own — it is not `locked_conflict` (no code contradicts anything), not `underspecified` (the document was complete when it minted), and riding either would make three different planning-quality signals one indistinguishable population.

**Changed:** `stale_inheritance` joins the enum, projected to exit 2 with the human-decision family (0011 D-11.9). Like `underspecified`, the triage response is document-side: re-mint from the superseding document, or abandon — never retry, and the engine never rewrites the minted contract (D-7.10).

**Deliberately unchanged:** the planner still has no verb that touches a running aggregate (0007 §2); `--reconcile` records a fact about a task's inheritance, it does not cancel, re-scope or resolve anything — what to do with in-flight work is a human decision.

**Also edits:** 0011 D-11.9 (the projection row).
