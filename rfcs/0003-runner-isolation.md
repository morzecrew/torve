# RFC 0003 — Runner and isolation

- **Status:** 📝 Draft — depends on 0002 shipping
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

   Each scenario keeps a deliberately broken twin — remove the ceiling check, drop the lease — that the oracle must catch and reproduce. A simulation that cannot fail proves nothing.

4. **Gate sabotage suite** — inherited from RFC 0002.

Layer 3 proves the engine cannot lie about concurrency; layer 4 proves the gates cannot lie about correctness. Both verified by keeping something broken around on purpose.

**Caveat that shapes the code:** simulation exercises handlers over the ports, so anything derived below a port — triggers, generated columns, enriched read views — is invisible to it and can produce false positives. Keep scope-overlap computation, gate ordering and escalation classification in Python above the port, where mock and real agree.

## 7. Storage

Mock for tests and simulation. **Postgres for any real run** — not a preference, a property of the substrate: the self-hosted durable tier ships on Postgres.

Migrations belong to the adapter, never the domain, and cover only Torve's own document tables. Substrate tables (outbox, run store, idempotency) have their own provisioning path; do not reinvent it.

Because all three aggregates are immutable and carry `schema_version` (D-22), migrations are almost always additive — old rows are read by the old shape, no backfill, nothing to rewrite. That is a direct consequence of having no update commands, not a coincidence.

## 8. Decisions

| # | Grade | Decision | Consequence |
| --- | --- | --- | --- |
| D-3.1 | `LOCKED` | v1 is one task, synchronous, no daemon | Parallelism only after the single path is boring |
| D-3.2 | `ASSUMED` | `FakeAgent` is the first adapter built | Depart only if it delays a working loop by more than a day |
| D-3.3 | `ASSUMED` | `Runtime` is OpenSandbox, with Docker retained as a fallback | Depart if operating the server outweighs the vault and egress controls |
| D-3.4 | `LOCKED` | Names for ports, databases, volumes and sandboxes derive from the task id | Cleanup by convention depends on it entirely |
| D-3.5 | `ASSUMED` | Simulation is the primary concurrency-verification tool; each invariant ships with a reachability target and a broken twin | Depart if the harness cannot see the invariants |
| D-3.6 | `ASSUMED` | Mock for tests, Postgres for real runs | Substrate property, not a choice |

## 9. Exit criteria

- Fake-agent suite green.
- Simulation sweep passes with every reachability target fired and every broken twin caught.
- `torve reap` provably cleans up after a `kill -9` mid-run.
- One task taken end to end to an open pull request, with all artefacts persisted.
