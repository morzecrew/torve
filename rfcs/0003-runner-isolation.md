---
id: "0003"
title: Runner and isolation
status: accepted
implementation: partial
depends_on: ["0002"]
informed_by: []
supersedes: []
superseded_by: null
amended_by: ["A-6", "A-13", "A-18"]
owner: Lev Litvinov
description: >-
  `torve run` for one task synchronously: sandbox lifecycle, lease and cancellation, reaper, and the simulation harness that proves the state machine.
schema_version: 1
---

# RFC 0003 — Runner and isolation

- **Implementation state:** phases 1–2 shipped 2026-08-21 (T-0003 runner core and both runtime adapters; T-0004 durable store facade with leases/fencing/cancellation, recovery-driven reap, DST simulation with broken twins). Outstanding: live OpenSandbox server integration, the pull-request leg (needs a remote), transactional notifications (deferred to RFC 0006)
- **Scope:** `torve run` for a single task, synchronously: workspace, sandbox, dispatch, gates, artefacts, reaper, and the simulation harness that proves the state machine correct. Uses a fake agent only. Excludes real agents (0004), review (0005), merging (0006), planning (0007).
- **Inherits:** D-1, D-3, D-4, D-4b, D-5, D-5a, D-22 from RFC 0001

---

## 1. The increment

```bash
torve run T-0142                 # one task, synchronous, exit code is the outcome
torve run T-0142 --agent fake    # engine tests, zero model calls
torve reap                       # sweep orphaned sandboxes, worktrees, databases
torve status
```

No daemon, no queue, no dashboard, no parallelism. **Make the single path reliable before adding a second one** — debugging races in a system whose sequential path is still wrong is how a month disappears.

## 2. Deliberately smaller than it wants to be

The temptation, understanding the architecture whole, is to build eight plugin slots on day one. Don't. First binary: one task, synchronously, state in a JSON file beside the worktree. Parallelism, real storage and escalation arrive when the single run is boring.

## 3. Fake agent first

`FakeAgent` replays a scripted scenario — write these files, exit with this code. It is the **first adapter implemented**, not the last, because it makes the entire runner testable without spending a token, and it separates "is the runner correct" from "is the agent good".

Scenarios cover: clean success, gate failure then success, budget exhaustion, crash mid-run, a `LOCKED` conflict written to the log, and a process that ignores cancellation.

## 4. Isolation

The sandbox is the unit of lifecycle. Killing it kills everything inside, including grandchildren that called `setsid` — which agent CLIs do, and which no amount of process-group handling in the parent reliably survives.

**Everything addressable derives from the task id.** Never search for a free port at runtime; two workers race for the same one.

```text
offset          = hash(task_id) % 100
api_port        = 4000 + offset
db_name         = task_0142
compose_project = t0142
worktree        = .wt/T-0142
labels          = torve.task=T-0142, torve.run=<uuid>
```

- **Database:** template database plus `CREATE DATABASE … TEMPLATE`. Migrations run once into the template, not per worker.
- **`.env`:** materialised by the runner from a template with the derived port and database substituted. No production secrets enter a worktree.
- **Build caches:** shared layer read-only, write layer per task.

### 4.1 Runtime

The `Runtime` port is filled by OpenSandbox rather than raw Docker, for four capabilities this RFC would otherwise hand-roll or accept as risk:

- **Credential Vault** injects credentials into outbound requests without exposing real secrets to the workload. The most important of the four: an agent can push to a remote without ever holding a token that could be exfiltrated. Retrofitting this later means every token an agent has seen is already suspect — hence D-4b is `LOCKED`.
- **Per-sandbox egress control** with an ingress gateway, enforced by the platform rather than a startup script inside the container.
- **Strong isolation options** — gVisor, Kata, Firecracker microVM — which move the boundary beyond "shared kernel, trusted repositories only". Not a goal here; the door stops being nailed shut.
- **Sandbox-level timeout** at creation, so the platform bounds the lifecycle.

Cost, stated plainly: this is a server plus a daemon, not a library. For one synchronous run on a laptop, raw Docker is less machinery. The vault is what justifies adopting early anyway. Keep the port thin enough that Docker remains a working fallback.

### 4.2 Reaper

Narrower than in earlier drafts, since sandboxes expire on their own, but not removable: worktrees, template databases and volumes still need cleanup.

**Convention-driven, not tracked.** A scheduled sweep enumerates by label and prefix, cross-references live leases, and destroys anything without one. This survives a crash of the runner itself, which PID tracking does not. Without it, disk and stale resources accumulate — always, not sometimes.

## 5. Lease and cancellation

`TaskStore` is a facade over the substrate's durable run store (D-5). What that buys:

- **Lease expiry, not process liveness, detects a dead worker**, and survives a runner crash.
- **Cancellation is cooperative on the ask, fenced on the landing.** Asking twice changes nothing; a stale worker cannot cancel a run out from under its new owner; if the holder dies carrying the request, recovery lands it without invoking the body.
- **Backends declare cancellation support explicitly** and the runner refuses a request it cannot deliver — the escalation path fails closed rather than silently.

Consequence for §3 and RFC 0004: observation latency is one heartbeat, and a body that never awaits is bounded only by maximum run duration. Every agent process gets its own hard timeout on top of the cooperative request.

Notifications are staged through the outbox in the same transaction as the state change, so "escalated but nobody was told" is not a reachable state.

## 5a. Context assembly

*(Added by charter amendment A-11 2026-08-22.)*

What the runner writes into a sandbox before dispatch. Every layer is versioned, owned, and part of `config_hash`.

| Layer | Source | Supplies |
| --- | --- | --- |
| Intent | `intent` on the contract | what is being changed and why |
| Constraints | `scope`, `decisions`, `budget` | boundaries, and the conflict protocol |
| Criterion | `acceptance` | what "done" is judged by |
| How things are done here | role-scoped skills (RFC 0009) | behaviour |
| What exists here | the repository's `AGENTS.md` | stack, conventions, how to run tests |
| Provenance | `rfc`, `phase` | where to look if the contract is not enough |

**This is the prompt, decomposed into versioned parts.** The difference from prompting a harness by hand is not that there is less of it — it is that each part has an owner, is checked, and registers in `config_hash`, rather than being rewritten freehand every time.

**The composition is fixed and belongs to the runner.** Nothing else may add to it: not the agent, not the tracker, not a repository-local override. A context that varies silently makes two attempts incomparable and every telemetry conclusion unfounded.

Not included, deliberately: other tasks' state, other tasks' escalations, the divergence logs of adjacent work. An executor that can read them is an executor that can argue its way out of its own scope (D-2a).

Also not included, deliberately (A-18): **the source specification document**. The contract is its projection, and an executor that reads the original is reading rejected alternatives — arguments for the decisions that did not survive — along with the phasing of adjacent tasks. Where a rejected alternative genuinely bears on a task, it belongs in `decisions` as a graded row, not as a document to browse.

## 6. Tests

Four layers, the first three free of model calls.

1. **Domain and state machine** — pure unit tests, frozen clock, mock store. Every transition, escalation reason, ceiling, lease expiry, reclaim.
2. **Runner against `FakeAgent`** — full loop including sandbox lifecycle, timeouts, reaper, gate ordering, artefact persistence. Fast, always green.
3. **Deterministic simulation** — the harness over the operation registry with mock deps. One master seed parametrises interleaving, injected faults, latency, generated inputs and crash points; on a violation the workload is minimised to the smallest failing case and a seed replays it exactly. This replaces the concurrency tests this RFC would otherwise specify by hand.

   | Invariant (must always hold) | Reachability (must sometimes fire) |
   | --- | --- |
   | No task reaches `ready` with a blocking gate not green | a gate failed after another had passed |
   | No two workers hold the same task | a lease expired mid-attempt |
   | Attempt count never exceeds the ceiling | a crash landed between increment and dispatch |
   | Every `escalated` task has exactly one delivered notification | the crash landed between state write and relay |
   | No duplicate PR for one task | a retry followed a push whose result was lost |
   | An entry written before a crash is on disk (A-13) | the crash landed between an entry write and the end of the run |

   Each scenario keeps a deliberately broken twin — remove the ceiling check, drop the lease — that the oracle must catch and reproduce. A simulation that cannot fail proves nothing.

4. **Gate sabotage suite** — inherited from RFC 0002.

Layer 3 proves the engine cannot lie about concurrency; layer 4 proves the gates cannot lie about correctness. Both verified by keeping something broken around on purpose.

**Caveat that shapes the code:** simulation exercises handlers over the ports, so anything derived below a port — triggers, generated columns, enriched read views — is invisible to it and can produce false positives. Keep scope-overlap computation, gate ordering and escalation classification in Python above the port, where mock and real agree.

## 7. Storage

Mock for tests and simulation. **Postgres for any real run** — not a preference, a property of the substrate: the self-hosted durable tier ships on Postgres.

Migrations belong to the adapter, never the domain. *Amendment 2026-08-21 (A-6):* the original claim here — that substrate tables have their own provisioning path — was inferred and false: the substrate documents schemas in adapter docstrings and ships no migrations. **Torve owns migrations for substrate tables** (outbox, inbox, run store, step store, schedules, idempotency, distributed locks) as well as for its own document tables — one set, Postgres only. The schema contract is enforced by test, not by file: the differential conformance battery runs the same properties against the mock and a real Postgres, and running it against the migrated database is a **required gate**. Substrate schema versions are pinned alongside the forze version — a forze upgrade that changes a schema becomes a migration task, not a silent `pip install -U` — and the pin joins `config_hash`.

Because all three aggregates are immutable and carry `schema_version` (D-22), migrations are almost always additive — old rows are read by the old shape, no backfill, nothing to rewrite. That is a direct consequence of having no update commands, not a coincidence.

## 8. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-3.1 | `LOCKED` | v1 is one task, synchronous, no daemon | `src/torve/application/runner.py` `src/torve/cli/**` | Parallelism only after the single path is boring |
| D-3.2 | `ASSUMED` | `FakeAgent` is the first adapter built | `src/torve/adapters/agent/fake.py` | Depart only if it delays a working loop by more than a day |
| D-3.3 | `ASSUMED` | `Runtime` is OpenSandbox, with Docker retained as a fallback | `src/torve/adapters/runtime/docker.py` `src/torve/adapters/runtime/opensandbox.py` | Depart if operating the server outweighs the vault and egress controls |
| D-3.4 | `LOCKED` | Names for ports, databases, volumes and sandboxes derive from the task id | `src/torve/base/naming.py` | Cleanup by convention depends on it entirely |
| D-3.5 | `ASSUMED` | Simulation is the primary concurrency-verification tool; each invariant ships with a reachability target and a broken twin | `tests/dst_world.py` `tests/test_dst.py` | Depart if the harness cannot see the invariants |
| D-3.6 | `ASSUMED` | Mock for tests, Postgres for real runs | `src/torve/adapters/store/durable.py` | Substrate property, not a choice |
| D-3.7 | `ASSUMED` | Runner configuration lives in `torve.yaml` at the repository root, reviewed like `gates.yaml` but on its own cadence; RFC 0004's tier mapping joins it there. Added by execution 2026-08-21. *(Relocated by D-13.1 2026-08-22: `.torve/config.yaml`, with the root name read as a fallback; substance unchanged.)* | `src/torve/config/runconfig.py` `src/torve/config/layout.py` | Keeps gate manifest and runner knobs on separate release cadences |
| D-3.8 | `ASSUMED` | `torve run` executes shell gates in a fresh sandbox from the same image over the same worktree; pure gates run in the engine. Added by execution 2026-08-21 | `src/torve/application/runner.py` | An agent-staged PATH shim cannot fake a gate outcome |
| D-3.9 | `ASSUMED` | Until RFC 0005 ships, the runner auto-transitions gated → reviewed with the recorded fact "review not configured"; the transition table stays unchanged. Added by execution 2026-08-21 | `src/torve/application/runner.py` | Review slots in without a state-machine change |
| D-3.10 | `ASSUMED` | v1 liveness is a heartbeat in the JSON state file; the reaper escalates stale non-terminal runs as `lease_expired`. Replaced by real leases in T-0004. Added by execution 2026-08-21 | `src/torve/application/reaper.py` | The kill -9 exit criterion holds before the durable store exists |
| D-3.11 | `ASSUMED` | The Runtime contract is "workspace in, changed files out": Docker satisfies it by bind mount, OpenSandbox by tar-over-files-API sync; the conformance battery asserts the contract, not the mechanism. Added by execution 2026-08-21 | `src/torve/application/ports.py` `src/torve/adapters/**` | Server-side runtimes fit the same port as local ones |
| D-3.12 | `ASSUMED` | The opensandbox SDK ships as the optional extra `torve[opensandbox]`; the adapter import-guards it. Added by execution 2026-08-21 | `pyproject.toml` | Consuming repositories do not pay for an adapter they do not use |
| D-3.13 | `ASSUMED` | Torve owns the DDL and migrations for the substrate tables it uses, applied by `torve migrate substrate` *(command renamed per 0012-migrations.md; was `torve store provision`)*; the conformance battery against the migrated database is a required gate (A-6). Added by execution 2026-08-21 | `src/torve/adapters/store/durable.py` | The substrate documents schemas but ships no provisioning path |
| D-3.14 | `ASSUMED` | Durable status maps to task state as: COMPLETED wraps every engine verdict (ready and escalated alike), FAILED is an unhandled engine exception, CANCELLED is escalation `killed`, TIMED_OUT is `budget_exhausted`. Added by execution 2026-08-21 | `src/torve/application/runner.py` | The store records that the run finished deciding, not what it decided |
| D-3.15 | `ASSUMED` | Under the in-process mock store the reaper keeps the v1 heartbeat heuristic; under Postgres the lease is the liveness authority via `claim_abandoned`. Added by execution 2026-08-21 | `src/torve/application/reaper.py` | Cross-process durability requires Postgres (D-3.6), stated in configuration |
| D-3.16 | `ASSUMED` | `torve reap --force` is the one deliberate use of an unfenced terminal write — an operator override so a stuck system is always drainable. Added by execution 2026-08-21 | `src/torve/application/taskstore.py` | Fencing protects runs from stale workers, not from operators |
| D-3.17 | `ASSUMED` | Cancel observation latency is one lease heartbeat plus the current port call, bounded by the agent hard timeout. Added by execution 2026-08-21 | `src/torve/application/runner.py` | §5's "a body that never awaits" caveat, made concrete |
| D-3.18 | `ASSUMED` | Transactional notifications and the delivered-notification simulation invariant land with RFC 0006, where the Notifier policy lives. Added by execution 2026-08-21 | `src/torve/application/taskstore.py` | Until then escalations are visible through `torve status` only |
| D-3.19 | `LOCKED` | Context composition is fixed, owned by the runner, and part of `config_hash`; nothing outside the runner may extend it. Added by charter amendment A-11 2026-08-22 *(the source patch numbered this D-3.7, already taken)* | `src/torve/application/runner.py` | A context that varies silently makes attempts incomparable |
| D-3.20 | `LOCKED` | A log file is created by its first entry; entries are flushed as written. Added by amendment A-13 2026-08-22 *(the source patch numbered this D-3.8, already taken)* | `src/torve/application/runner.py` `src/torve/gates/decisions_reported.py` | Writing at end-of-run from memory loses entries on any abnormal termination |
| D-3.21 | `LOCKED` | A missing log and an empty log are equivalent to every reader. Added by amendment A-13 2026-08-22 *(the source patch numbered this D-3.9, already taken)* | `src/torve/gates/decisions_reported.py` `src/torve/gates/self_audit.py` | Otherwise the runner needs a decision about writing, and that decision is a bug waiting to happen |

## 9. Exit criteria

- Fake-agent suite green.
- Simulation sweep passes with every reachability target fired and every broken twin caught.
- `torve reap` provably cleans up after a `kill -9` mid-run.
- One task taken end to end to an open pull request, with all artefacts persisted.

## Amendments

### A-6 — 2026-08-21 — substrate schema provisioning is ours (amends §7)

**Found in implementation.** §7 stated that substrate tables have their own provisioning path. They do not — forze documents its schemas in docstrings and ships no migrations. The claim was inferred, never verified.

**Changed:** Torve owns migrations for the substrate tables it uses (run store, step store, and later outbox/inbox/schedules/idempotency/locks) as well as for its own document tables. One set, Postgres only — multi-backend was already moot under D-3.6. The full design is RFC 0012. *(Revised 2026-08-21: the runner is yoyo over raw SQL steps; alembic is rejected — torve has no sqlalchemy models for it to work from.)*

**The schema contract is enforced by test, not by file.** The differential conformance battery runs the same properties against the mock and a migrated real Postgres; running it against the migrated database is a required gate (D-12.9).

**Rejected — generating migrations inside forze:** it is a backend engine, not a migration generator, and generated migrations need review regardless.

**Consequence, and the real cost of this finding:** substrate schema versions are pinned alongside the forze version (`migrations/substrate/FORZE_VERSION`); the pin joins `config_hash`, and a forze upgrade that changes a substrate schema is a migration task in Torve, not a silent `pip install -U`.

### A-13 — 2026-08-22 — logs are created by writing (amends the execution-log handling, charter §6/A-1)

**Found in design review.** Skipping empty log files is worthwhile — most tasks produce no divergence and an empty `entries:` list is noise — but the obvious implementation is dangerous: writing the file at the end of a run from state held in memory loses entries whenever the run crashes, is killed or times out. "Skipped an empty file" would become "lost a non-empty one". *(The source patch numbered this A-11 and its decisions D-3.8/D-3.9; all were taken, so they land as A-13 and D-3.20/D-3.21 per D-A.4/D-A.5.)*

**Changed:** the log file is created by its first entry and by nothing else. Entries are appended and flushed as they are made. There is no code path in which the runner decides whether to write a log.

**Consequences:** absence and emptiness are equivalent to every reader; `decisions-reported` treats a missing file as an empty log; the silence check is unaffected, since a missing file where a `LOCKED` area was touched is a violation exactly as a file without the matching entry would be.

**Verified by:** a simulation scenario in which the agent writes an entry and then crashes — the entry must be on disk (§6 table).

### A-18 — 2026-08-22 — the source document does not enter the sandbox (amends §5a)

**Found in specialising the `flag-dont-flip` skill for autonomous execution.** Its Torve copy told the executor to produce a plan, stop, and read the RFC's rejected-alternatives sections first. Both instructions come from the interactive world upstream serves — a human at the other end of a checkpoint — and both fail here: an executor that plans and stops produces no diff, gives the gates nothing to run against, and dies on wall-clock or the poison ceiling; a deadlock presenting as a mysterious timeout. The checkpoint did not disappear — it moved earlier and became `torve plan`, D-7.7's readiness refusal, `SizePolicy` and the reviewed contract.

**Changed:** §5a's exclusion list gains the source specification document. The contract is the document's projection — intent, scope, graded decisions, acceptance — and everything the executor is owed. Rejected alternatives are temptations, not constraints: they hand an executor the material for arguing that the rejected option is better *in this case*, precisely the reasoning `LOCKED` forecloses. The document also carries the phasing of adjacent tasks, which D-2a deliberately keeps out of reach. Where a rejected alternative genuinely bears on a task, it becomes a graded decision row — the judgement sits with the author deciding what an executor needs, not with the executor deciding what to read. If a contract proves insufficient, the fix is the projection, never handing over the original behind its back.

**In the skill:** the plan gate is replaced by "underspecification is a halt, not a question" — the readiness threshold survives (three or more load-bearing unsettled decisions means the contract is not executable), but the response is a `kind: blocked` / `action: halted` / `class: spec-gap` entry and an escalation, never a plan awaiting an approval that cannot arrive. Fewer than three: decide, log each as `UNLISTED` with its owed proposal, carry on. Upstream `agent-skills/flag-dont-flip` keeps both instructions for its interactive reader — divergence intended, do not reconcile.

**Verified by:** the end-to-end scenario in which a well-formed contract produces a diff (the deadlock's signature is a run ending with no diff and no `blocked` entry), and a context check that a dispatched sandbox resolves `rfc` as provenance only — the runner never reads or copies the document it names.
