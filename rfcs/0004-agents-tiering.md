---
id: "0004"
title: Agent adapters and tiering
status: accepted
implementation: partial
depends_on: ["0003"]
informed_by: []
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  Real agent adapters behind the `Agent` port, tiering economics, shadow runs, and the telemetry that makes harness choice measurable.
schema_version: 1
---

# RFC 0004 — Agent adapters and tiering

- **Implementation state:** phases executed 2026-08-22 (T-0021–T-0023 — the `HarnessAgent` mechanism behind the tier mapping, provider routing enforced at dispatch, `torve shadow` replay); live since 2026-08-23 with a deepseek harness tier implementing and reviewing real work on the lab repository. Outstanding: the §8 exit criteria — fifteen shadow runs with cost and iterations recorded, a two-adapter comparison on the same tasks, and a gate set adjusted from that evidence — which are operator campaign work over the shipped machinery, not engine gaps.
- **Scope:** Real agent adapters behind the `Agent` port, the tiering economics, shadow runs against completed work, and the telemetry that makes harness choice measurable. Excludes review (0005) and merging (0006).
- **Inherits:** D-2, D-4, D-4b, D-16 from RFC 0001

---

## 1. Adapters

The adapters differ only in how authentication and the harness reach the process.

| Adapter | Auth | Notes |
| --- | --- | --- |
| `FakeAgent` | none | from RFC 0003; stays the primary test path |
| `ApiAgent` | key in env, ephemeral sandbox | all cheap executors |
| `HarnessAgent` | key in env, harness in the sandbox | reads `AGENTS.md` and `SKILL.md`, so the existing skill library carries over unchanged; emits a session trace |
| `SubscriptionAgent` | auth volume **per worker slot** | see §2 |

`tier` in the task maps to an adapter in `torve.yaml`. The concern does not leak further into the design.

Two constraints on harness-backed adapters: run them **inside** the sandbox, and never embed a harness SDK in the engine process — an in-process harness collapses the trust boundary the whole design rests on.

## 2. Subscription authentication

Auth volume per **worker slot**, not per task. Task sandboxes are ephemeral; slots are stable, so one interactive login per slot survives any number of runs. Mount read-write, because token refresh writes to it. One volume per slot avoids races when two workers refresh concurrently.

Do not mount the host config directory: it drags settings, MCP servers and plugin state along, and on at least one host platform the desktop client removes the credentials file that Linux containers depend on. Note also that a credentials file alone may be insufficient — some CLIs need a minimal companion config or they treat the sandbox as a fresh install and re-prompt.

Subscription seats are per person and rate-limited. Under tiering this is mostly moot: the subscription is used interactively during planning, where a human is present anyway, and autonomous workers are cheap API models. Check the plan's current terms before building throughput on a seat.

## 3. Tiering

Expensive model plans, cheap models execute; the plan is the handoff artefact. One expensive audit session against many cheap execution passes.

The caveat matters as much as the pattern: a plan written by a weak model is a weak plan. Economising on the audit phase removes the point of the arrangement.

## 4. Why the `Agent` port earns its existence

Harness choice is not neutral. A controlled comparison ran one model across eight harnesses on thirty tasks and produced between fourteen and twenty completions; a separate comparison found roughly a threefold difference in tokens for the same task. The harness accounts for a large share of the outcome.

That makes it an empirical question about **your** tasks, answerable only from per-attempt cost and iterations-to-green. The port plus telemetry turns harness selection from taste into measurement — and swapping one is a config change, not a migration.

**A session trace is not gate evidence.** A harness log records what the model saw; a gate records what the code did, computed where the agent could not influence it. An agent claiming green tests without running them produces a flawless log of that claim. D-3 stands regardless of how good harness logging is, and a gate implemented as a harness plugin does not count — it runs inside the agent's trust boundary.

Traces are still worth capturing: `trace_ref` on the `Attempt` turns triage of an escalation from archaeology through container logs into replay.

## 5. Shadow runs

Before any live use: replay ten to fifteen already-completed tasks from their parent commit, never merging, and compare against what actually shipped.

This produces the first honest cost and iteration numbers at zero risk, and it is where the gate set is tuned — not before.

## 6. Telemetry, staged

The analytics contract is where this ends, not where it starts.

| Stage | Storage | Move on when |
| --- | --- | --- |
| 1 | JSONL file | start here |
| 2 | DuckDB over the same files | a query needs window functions or joins |
| 3 | analytics port | more than one consumer reads it |
| 4 | ClickHouse behind the same port | volume or concurrent writers make a file untenable |

DuckDB reads JSONL directly, so stage 1 → 2 is a change of reader, not a migration.

Mandatory from the first record, because none can be reconstructed: `schema_version`, `config_hash`, and decisions **denormalised** into the record rather than referenced.

Per-gate health rides the same records (RFC 0002 §7.6, added by A-8): hit rate, bypass count, flake rate, duration p50/p95 and first-attempt pass rate are all derivable from attempt telemetry — no separate collection path, reviewed quarterly.

The two hand-entered fields — `human_minutes` and `rework_after_review` — are added after merge and live in a separate `ReviewFeedback` record keyed by task id. Appending is easy; updating a row in an append-only store is not.

## 6a. Three measurement defects to fix before trusting a number

**`config_hash` does not catch model drift.** Providers update a model behind a stable name, so two runs with an identical hash can be two different models, and every before/after comparison silently breaks. Record whatever version string the provider returns alongside the hash, and pin where pinning is offered. Where neither is available, treat that model's history as a single uncontrolled regime and say so.

**Shadow runs can see the future.** Replaying a completed task from its parent commit leaks the answer if the agent can reach later refs — the fix is already in the repository's history. Shadow worktrees get truncated history and no access to refs beyond the parent commit, or the numbers are flattering fiction.

**Baseline is a quasi-experiment, not an A/B.** Tasks before and after are different tasks, done under different conditions. This supports direction ("iterations fell") and not magnitude ("40% faster"). Write that down now, because the first attractive number will otherwise become a promise to someone.

## 6b. Provider routing and data boundaries

Repository contents, fixtures and diffs leave the building for whichever provider an adapter is pointed at. There is currently no policy about which, and that is a gap rather than an oversight.

```yaml
providers:
  default: [cheap-vendor-a]
  repositories:
    payments-core:
      allow: [vendor-eu-only]
      deny_reason: "customer data in fixtures"
  never_send:
    - "**/fixtures/production-*"
    - "**/*.pem"
```

Enforced at dispatch, before a sandbox exists — a task whose repository has no permitted provider for its tier escalates as a configuration error rather than quietly falling back. Combined with the secret-scan gate, this covers both directions: what must not land, and what must not leave.

## 7. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-4.1 | `LOCKED` | Harnesses run inside the sandbox; no harness SDK in the engine process | `src/torve/adapters/**` | Otherwise the trust boundary collapses |
| D-4.2 | `ASSUMED` | Subscription auth is per worker slot, never per task, never the host config directory | `src/torve/adapters/**` | Refresh races and host-client interference |
| D-4.3 | `ASSUMED` | Harness selection is decided by measurement on own tasks, not by reputation | `.torve/config.yaml` | Requires `config_hash` from RFC 0002 |
| D-4.4 | `LOCKED` | Shadow runs precede any live loop | `src/torve/application/shadow.py` | The only risk-free source of baseline comparison |
| D-4.5 | `ASSUMED` | Telemetry starts as JSONL; record shape is fixed from record one | `src/torve/application/telemetry.py` | Storage reversible, shape not |
| D-4.6 | `LOCKED` | Provider version is recorded alongside `config_hash`; unpinnable models are one uncontrolled regime | `src/torve/application/telemetry.py` | Otherwise every before/after comparison is unfounded |
| D-4.7 | `LOCKED` | Shadow worktrees carry truncated history and no later refs | `src/torve/adapters/workspace/git.py` | Leaking the answer invalidates the only risk-free measurement |
| D-4.8 | `LOCKED` | Provider allow/deny per repository, enforced before a sandbox exists | `src/torve/config/runconfig.py` | Data leaves the building; silence is not a policy |
| D-4.9 | `ASSUMED` | One harness mechanism serves api, harness and subscription tiers — three auth routes, one command template; telemetry records the adapter that RAN, never what was configured. Added by execution 2026-08-22 — see .torve/tasks/T-0021 | `src/torve/adapters/agent/harness.py` | — |
| D-4.10 | `ASSUMED` | `never_send` is dispatch-time withholding: bytes leave the worktree before the sandbox exists and return after sync-out; workspaces that carry their own history rely on the truncation guarantee instead, since deletion cannot unsend history. Added by execution 2026-08-22 — see .torve/tasks/T-0021 | `src/torve/application/runner.py` | — |
| D-4.11 | `ASSUMED` | The session trace lands beside the worktree, one file per attempt; metadata parses from the trailing JSON line, and absence is recorded as null, never invented. Added by execution 2026-08-22 — see .torve/tasks/T-0021 | `src/torve/adapters/agent/harness.py` `src/torve/base/naming.py` | — |
| D-4.12 | `ASSUMED` | Worker slot identity is configuration — one slot per worker process; auth volumes key on it. Added by execution 2026-08-22 — see .torve/tasks/T-0021 | `src/torve/config/runconfig.py` | — |
| D-4.13 | `ASSUMED` | A shadow run's exit code measures completion of the measurement; the replay's outcome lives in the comparison record, so a red replay is a successful measurement. Added by execution 2026-08-22 — see .torve/tasks/T-0022 | `src/torve/cli/shadow.py` `src/torve/application/shadow.py` | — |
| D-4.14 | `ASSUMED` | Shipped-commit discovery: the Torve-Task trailer first, then a subject-only scan (bodies quote task ids and shadow the true commit); shadow infrastructure derives every name from `shadow-<task-id>`. Revisit the subject fallback when history is squashed. Added by execution 2026-08-22 — see .torve/tasks/T-0022 | `src/torve/adapters/workspace/git.py` `src/torve/base/naming.py` | — |
| D-4.15 | `ASSUMED` | The shadow comparison diffs against the parent sha by construction; an agent committing inside its self-contained clone is legal behaviour, not escape. Added by execution 2026-08-22 — see .torve/tasks/T-0022 | `src/torve/adapters/workspace/git.py` | — |
| D-4.16 | `ASSUMED` | Sandbox egress follows the host only under an explicit network opt-in, which also forwards the proxy vocabulary; where egress control matters the OpenSandbox vault remains the answer. Added by execution 2026-08-22 — see .torve/tasks/T-0023 | `src/torve/adapters/runtime/docker.py` `src/torve/config/runconfig.py` | — |
| D-4.17 | `ASSUMED` | The proxy convention is one vocabulary across runtimes, owned by the ports module; per-host egress mode is repository configuration, never a code default. Added by execution 2026-08-22 — see .torve/tasks/T-0024 | `src/torve/application/ports.py` `src/torve/adapters/runtime/docker.py` | — |

## 8. Exit criteria

- Fifteen shadow runs completed with cost and iterations recorded.
- At least two adapters exercised against the same tasks, with a measured difference.
- Gate set adjusted from shadow-run evidence rather than from expectation.
