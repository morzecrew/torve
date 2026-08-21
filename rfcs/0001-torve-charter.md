# RFC 0001 — Torve: charter

- **Status:** 📝 Draft — architecture baseline for RFCs 0002–0007
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
| `Inference` | forze inference contract (RFC 0005) |
| `Notifier` | outbox destination |
| `ContextRead` | read-only MCP (RFC 0007) |
| `Tracker` | outbound projection, restricted inbound commands (RFC 0008) |
| `DecisionSource` | importing standing decisions into new tasks (RFC 0007) |
| `SizePolicy` | pre-dispatch size estimate and post-hoc calibration (RFC 0002) |
| `PromotionPolicy` | what may land without a human (RFC 0006) |
| `Vcs` | commit, branch, sign, revert (RFC 0010) |

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
| `Telemetry`, `Inference`, `Notifier` | gates — declarative config, not an interface |
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

**Execution log format** — `logs/<task-id>.md`, one file per task, append-only, one fenced YAML block per entry:

````markdown
```divergence
decision: D-3
grade: LOCKED
kind: contradicted        # contradicted | departed | resolved | blocked
at: 2026-08-20T11:04:12Z
attempt: 2
claim: sessions cannot live in Redis; this deployment has no Redis service
evidence: infra/compose.yaml:1-40 — no redis service defined
action: halted            # halted | departed | decided
```
````

Grade and action legality — the conflict protocol as a checkable table:

| Grade | Legal action | Illegal |
| --- | --- | --- |
| `LOCKED` | `halted` | anything else |
| `ASSUMED` | `departed` | `halted` (over-caution) |
| `OPEN` | `decided` | `halted` |

Two rules that make the log worth keeping: **grade is copied at write time, never resolved at read time** — a log that rewrites its own past is not an audit trail; and **silence is a finding** — the gate fires on the absence of an entry in an area a locked decision declares, because a quiet workaround is not detectable while a missing report trivially is.

## 7. Decisions

| # | Grade | Decision |
| --- | --- | --- |
| D-1 | `LOCKED` | Two modules over one domain; executor is the sole writer of task state, planner is read-only plus task minting |
| D-2 | `LOCKED` | Models never decide **what work exists** or **whether it is finished**. The planner invokes no model at all. Execution and review invoke models, but their output is data and the consequence is set by config. Stated precisely: a reviewer model does choose a `severity`, and configuration chooses what a severity does — so severity calibration is a **measured quantity** (RFC 0005 §6), not a trusted one |
| D-3 | `LOCKED` | Gates execute outside the agent session; an agent cannot report a gate outcome |
| D-4 | `LOCKED` | The sandbox is the unit of lifecycle; nothing runs on a host |
| D-4b | `LOCKED` | Agents never hold real credentials; outbound secrets are injected by the runtime's vault |
| D-5 | `LOCKED` | `TaskStore` is a thin facade over the substrate's durable run store, not hand-written |
| D-5a | `LOCKED` | The lifecycle is not modelled as a durable workflow; the run store is a leased queue with recovery |
| D-6 | `LOCKED` | The engine never resolves conflicts and never merges without the configured approval |
| D-21a | `LOCKED` | The execution-log format is defined here; one file per task, append-only, grade copied at write time |
| D-22 | `LOCKED` | Three aggregates, each with domain, create and read models and **no update command** |
| D-25 | `LOCKED` | The differentiator is the specification layer, not the execution runtime |
| D-7 | `ASSUMED` | Python on the forze substrate; Go rejected |
| D-8 | `ASSUMED` | Pydantic is the single contract source |
| D-16 | `ASSUMED` | `schema_version` on every persisted aggregate |
| D-26 | `ASSUMED` | Build our own runtime rather than adopt the adjacent one; time-box a teardown of three of its mechanisms first |
| D-27 | `LOCKED` | No contract, decision or execution log ever moves from git into a database |
| D-28 | `ASSUMED` | The engine gets a weekly time budget with a named owner; three consecutive overruns mean maintenance mode |

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
