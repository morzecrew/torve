---
id: "0041"
title: Remote sandboxes
status: accepted
depends_on: ["0003", "0017", "0021", "0033"]
informed_by: ["0004", "0024", "0035"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  The OpenSandbox runtime matured into a first-class remote execution
  target — live-server conformance, registry-delivered images, measured
  workspace transfer, and a broker the sandbox can actually reach — so
  agent sessions and gate batteries run on machines that are not the
  operator's.
schema_version: 1
---

# RFC 0041 — Remote sandboxes

- **Scope:** Everything between "the OpenSandbox adapter was verified live
  once" and "an operator points `sandbox.domain` at a remote server and
  the engine just works": an opt-in live-server conformance leg beside the
  existing stub battery, the image path from `torve sandbox build` to a
  registry the server pulls from, transfer telemetry for the tar-seed
  round trip the adapter already performs, and a remote-reachable broker
  endpoint mode. Touches `src/torve/adapters/runtime/opensandbox.py`,
  `src/torve/adapters/broker/local.py`, `src/torve/cli/sandbox.py`,
  `src/torve/config/runconfig.py`, tests. The orchestrator stays singular
  and host-local: worktrees, landing, telemetry and every projection keep
  their per-root authority (RFC 0024). Deliberately not covered:
  multi-host orchestrators (§8), multi-tenancy (charter §9, permanently),
  sealed-mode egress for remote sandboxes (§5.4 names why it cannot
  translate and what stands in for it), and subscription-billed harnesses
  (D-4.2's auth volumes have no OpenSandbox counterpart — refused today,
  refused after this).
- **Related:** RFC 0003 §4.1 (the platform this adopts and why), RFC 0017
  (provisioning, digest doctrine D-17.1, docker-mode refusal D-17.10),
  RFC 0021 (broker; sealed mode D-21.3, in-process thread D-21.10),
  RFC 0033 (published harness images — the registry half that already
  shipped), RFC 0035 (attempt economics the transfer numbers feed);
  `src/torve/adapters/runtime/opensandbox.py`,
  `tests/opensandbox_stub.py`, `~/.config` OpenSandbox server notes.
- **Origin:** The 2026-09-02 architecture review: provider happy-hour
  windows and gate batteries that outgrow the operator's machine both
  reduce to "run the compute elsewhere", and the adapter was built for
  that and then parked at "deferred until a server is routinely
  available" — a self-hosted server now runs at `localhost:5266`.

---

## 1. Summary

The expensive halves of an attempt — the agent session and the shell-gate
battery — already execute behind the `Runtime` port, and the OpenSandbox
adapter is remote by construction: an HTTP files/commands API, a tar seed
in, a base64 pipe out, no bind mounts, platform-enforced timeouts, label
enumeration for the reaper. What is missing is not architecture but
finishing: the conformance battery has never run against a real server in
CI, images must reach a registry the server can pull, nobody measures
what the tar round trip costs, and the broker — the one piece of run
infrastructure a sandbox must call back into — listens on loopback or on
a Docker network gateway, neither of which exists from a remote machine.
This document closes those four gaps. After it, a heavy gate battery is
the server pool's problem and the orchestrator's contribution to an
attempt is git plumbing, deterministic gates and file I/O.

## 2. Motivation

- **Compute location is the whole cost story.** Provider off-peak windows
  make overnight execution 50–75% cheaper on metered tiers, and overnight
  execution wants a machine that is not the operator's laptop. Separately,
  gate batteries grow with the consuming repository: a framework shipping
  dozens of integrations needed a CI test matrix that strained hosted
  runners — under torve that entire battery runs inside the `-gates`
  sandbox (`_run_gates_in_worktree`, D-3.8: same image as the attempt),
  so sandbox placement, not orchestrator hardware, decides whether it is
  feasible.
- **The adapter is finished enough to be almost usable.** Its own
  docstring records a live verification (tar-seed round trip, platform
  timeout collecting a probe sandbox) and then defers "full integration
  until a server is routinely available". One is routinely available.
- **Every remaining gap is named in the code.** `resolve_image` returns
  `None` for anything but a digest-pinned reference "until the
  live-server integration teaches this adapter to ask the registry";
  `build_image` refuses with "push to a registry the server can pull
  from"; the proxy-env comment concedes reachability "is the server's
  networking, not ours"; docker mode is refused "until the live-server
  integration decides what the server can offer" (D-17.10). This
  document is that live-server integration.

## 3. Current state

Verified against the tree at drafting time:

- The conformance battery runs the OpenSandbox adapter against an
  in-process SDK emulation (`tests/opensandbox_stub.py`) and Docker
  against the real daemon, asserting one contract for both. No test has
  network access to a real server.
- `OpenSandboxConfig` is two fields: `domain` (default
  `localhost:5266`) and `api_key_env`. Remote is already "change the
  domain" — nothing else in configuration knows where sandboxes run.
- Both sandboxes of an attempt go through the port: the agent session
  (`AgentContext.runtime`) and the gate battery (`_SandboxExecutor` in
  `_run_gates_in_worktree`, a separate `-gates` sandbox over the same
  image per D-3.8). Deterministic gates (scope, secrets, contract
  checks) run in the orchestrator process and are cheap.
- The workspace crosses as a tar minus `.git` (a worktree's gitfile
  must never leave the host) at create, and back as a base64 pipe at
  `sync_out`. Nothing records what either leg costs.
- `create` refuses `spec.volumes` — subscription adapters' auth volumes
  (D-4.2) have no server-side counterpart; OpenSandbox credentials
  belong to its vault (RFC 0003 §4.1). Env passthrough resolves
  host-side at the API boundary.
- The broker is an in-process thread of the runner (D-21.10). Endpoint
  mode binds loopback; sealed mode (D-21.3) binds the gateway of an
  `--internal` Docker network and authenticates the pass-through leg by
  that topology. A remote sandbox can reach neither, and the sealed
  trust argument — "the network is the run's private envelope" — has no
  meaning across the open internet.
- RFC 0033 (published harness images) is complete: harness images are
  published and digest-pinned. The consuming repository's battery image
  is built locally by `torve sandbox build` via the Docker runtime and
  never pushed.

## 4. Goals / Non-goals

**Goals**

- `sandbox.domain: <remote-host>` is sufficient configuration for live
  runs on API-metered tiers, and the conformance battery proves the
  contract against a real server, on demand, in CI.
- Every image reference in a run config resolves on the server: pushed
  by the operator's build verb, pinned by digest per D-17.1.
- The tar round trip is a recorded number per attempt, so the transfer
  tax is measured before anyone optimizes it (RFC 0035's discipline).
- Provider routes work from a remote sandbox through the broker with
  the run token, without provider keys entering the sandbox.

**Non-goals**

- Multi-host orchestrators — the orchestrator stays one process on one
  host; this document exists to make that host's weight irrelevant.
- Sealed-mode parity for remote sandboxes — topology auth does not
  translate (§5.4); remote runs get provider routes and a refused
  pass-through leg until someone designs a cryptographic equivalent.
- Subscription-billed harnesses on OpenSandbox — the volume refusal
  stands; remote capacity serves metered tiers, which is exactly the
  population off-peak windows discount.
- Docker-in-sandbox — D-17.10's refusal stands; a battery driving
  containers keeps the Docker runtime.

## 5. Design

### 5.1 Live conformance

A third conformance leg: the same contract battery, against a real
server named by `TORVE_OPENSANDBOX_TEST_DOMAIN` (skipped when unset —
the `test_postgres_integration.py` pattern). It must additionally
assert the two behaviours the stub cannot vouch for: platform-enforced
timeout actually collects a sandbox, and `list_torve_sandboxes` /
`destroy_by_id` see and kill by label across connections (the reaper's
whole remote story). A red live leg with a green stub leg is a stub
defect finding — the stub is corrected, never the assertion.

### 5.2 Images reach the registry

`torve sandbox build` gains `--push <registry-ref>`: build via the
Docker runtime as today, push, and print the digest-pinned reference
the run config should carry. `resolve_image` on the OpenSandbox runtime
keeps its current honesty — digest-pinned references resolve, bare tags
record as unresolved regimes (D-17.1's "recorded as unresolved, never
invented") — and the docs stop treating that as a stopgap: on a
pull-from-registry platform, the digest-pinned reference *is* the
resolution. No registry client is added to the adapter.

### 5.3 The transfer, measured

The adapter records seed and sync-out cost — bytes and seconds for
each leg — surfaced through the attempt row beside `wall_time_s`
(additive keys, D-4.6's absent-stays-absent for the Docker runtime,
which mounts and transfers nothing). Optimization is explicitly
deferred until these numbers exist: the candidate levers (excluding
gitignored build artifacts from the seed, warm seed layers) are RFC
0035's territory and want its paired-measurement discipline.

### 5.4 The broker, reachable

Remote endpoint mode: the broker thread binds a configured address
(`broker.bind`, default loopback preserving today's behaviour), and
the runtime composes the sandbox's proxy env from the *advertised*
address (`broker.advertise`, for the NAT/hostname split) instead of
the Docker gateway derivation. Trust does not regress:

- Provider routes already authenticate by the run-scoped token
  (RFC 0021 §5.1) — that survives the trip unchanged; keys stay in the
  broker's environment on the orchestrator.
- The sealed pass-through leg authenticates by network topology, which
  does not exist remotely; in remote endpoint mode the pass-through
  leg is refused loudly, exactly as sealed mode refuses undeclared
  destinations. A run that needs non-provider egress remotely is a
  run that waits for a token-authenticated pass-through design —
  named as the escape hatch, not built.
- Transport security between sandbox and broker is TLS or a private
  network, and which one is the operator's deployment choice — the
  engine ships plaintext-capable and the docs say plainly what that
  means (§7). A wrong default here would be theater: the engine
  cannot know the operator's network.

### Alternatives considered

- **Broker adjacent to the sandbox server** (a broker process per
  server host, keys distributed to it) — rejected for now: it trades
  one reachability problem for a key-distribution problem and a second
  lifecycle to operate; the in-process thread doctrine (D-21.10) keeps
  dying with the run. Returns if broker round-trip latency proves
  material.
- **Provider keys in the sandbox env for remote runs** — rejected
  outright: it is the exact regression RFC 0021 exists to prevent, and
  "remote" makes the exposure worse, not more excusable.
- **Skipping the transfer telemetry and optimizing the seed now** —
  rejected: 0035 measured before it removed, and the seed may prove
  negligible against a 900-second attempt.

## 6. Tests

The live leg (§5.1) is the centrepiece; env-gated, documented as
operator-runnable against any server. Unit: `--push` invokes the Docker
runtime's build then a push and prints the pinned reference; transfer
numbers land on the attempt row for OpenSandbox and stay absent for
Docker; `broker.advertise` reaches the sandbox proxy env verbatim; the
remote pass-through refusal names the destination. The stub gains
whatever behaviour the live leg catches it lacking.

## 7. Docs

A deployment page: pointing `sandbox.domain` at a remote server, the
registry push flow, the broker bind/advertise pair, and the transport
paragraph — plaintext broker traffic on an untrusted network exposes
prompts and diffs, choose TLS or a private link. The subscription-tier
limitation is stated where tiers are documented, with the reason
(D-4.2's volumes), not as a bare refusal.

## 8. Out of scope

- **Multi-host orchestrators** — per-root file authority (RFC 0024)
  makes that a regime change (git-as-transport or store migration);
  this document deliberately maximizes what one orchestrator can do so
  that question arrives as late as possible.
- **A token-authenticated pass-through leg** — the sealed guarantee
  rebuilt cryptographically; named in §5.4 as what remote non-provider
  egress waits for.
- **Sandbox-server autoscaling, pooling, placement** — the server's
  business; torve speaks the API and stays ignorant of capacity.
- **Windows/scheduling** — cadence belongs to the environment
  (RFC 0019); off-peak operation is cron plus a night config, and
  nothing here needs to know the hour.

## 9. Risks

- **The live leg needs infrastructure CI does not have.** Mitigated:
  env-gated and skippable, like the Postgres leg; the risk is it rots
  unrun. Countered by making it operator-runnable in one command and
  part of the deployment page's checklist.
- **Transfer tax surprises on large repositories.** That is what §5.3
  measures before anyone commits to remote-by-default; a repository
  whose seed dominates its attempts keeps the Docker runtime locally.
- **Plaintext broker traffic on a hostile network.** Named in docs
  with the concrete exposure; the engine cannot pick the operator's
  transport, and pretending a default solves it would be worse.
- **Reaper blind spots against a remote server** (another operator's
  sandboxes, label collisions). The naming convention already scopes
  by root; the live leg asserts label-scoped enumeration, and
  destroy-by-id never sweeps what enumeration did not return.

## 10. Unresolved questions

- Whether `resolve_image` should learn to query the registry for a
  tag's digest (turning bare tags into controlled regimes) or bare
  tags stay honestly unresolved — settled by how often bare tags
  actually appear in remote configs once the push flow exists.
- The transfer-telemetry field names and whether they belong inside
  the agent block or beside it — implementation aligns them with
  RFC 0038's layout when both land, and logs.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-41.1 | `LOCKED` | The orchestrator stays one host-local process with per-root file authority; this document scales compute by moving sandboxes, never by moving the engine | `src/torve/adapters/runtime/opensandbox.py` | Multi-host orchestration is a separate future document with a data-plane story; nothing here may leak engine state off-host |
| D-41.2 | `LOCKED` | Provider keys never enter a sandbox, local or remote: remote runs reach providers only through the broker's token-authenticated routes | `src/torve/adapters/broker/local.py` | The D-21 doctrine survives the network hop; any shortcut that ships keys to a sandbox env is refused on review |
| D-41.3 | `ASSUMED` | Live conformance is a third env-gated leg (`TORVE_OPENSANDBOX_TEST_DOMAIN`) asserting the stub's contract plus platform timeout collection and label-scoped enumeration; a live/stub disagreement is a stub defect | `tests/opensandbox_stub.py` `tests/test_sandbox_images.py` | — |
| D-41.4 | `ASSUMED` | `torve sandbox build --push` publishes the battery image and prints the digest-pinned reference; the OpenSandbox runtime resolves digest-pinned references and records bare tags as unresolved (D-17.1) | `src/torve/cli/sandbox.py` `src/torve/adapters/runtime/opensandbox.py` | — |
| D-41.5 | `ASSUMED` | Seed and sync-out cost (bytes, seconds) are recorded per attempt for transferring runtimes, absent for mounting ones; seed optimization is demand-gated on these numbers under RFC 0035's paired-measurement discipline | `src/torve/adapters/runtime/opensandbox.py` `src/torve/application/telemetry.py` | — |
| D-41.6 | `ASSUMED` | Remote endpoint mode: `broker.bind` and `broker.advertise` replace the Docker-gateway derivation for OpenSandbox runs; the pass-through leg is refused in remote endpoint mode until a token-authenticated design exists; transport security is the operator's deployment choice, documented plainly | `src/torve/adapters/broker/local.py` `src/torve/config/runconfig.py` | — |
| D-41.7 | `ASSUMED` | Subscription-billed tiers stay Docker-only (the volume refusal stands); remote capacity serves metered tiers | `src/torve/adapters/runtime/opensandbox.py` | Off-peak windows and remote capacity target the same population; nobody builds a vault workaround casually |

## 12. Phasing

Phase 1's two units are disjoint and parallel. Phase 2 waits on the
live leg because the broker work is exactly the part a stub cannot
vouch for.

```yaml
- phase: 1
  title: live conformance and transfer telemetry
  intent: >-
    The env-gated live-server leg (D-41.3): the existing contract
    battery against TORVE_OPENSANDBOX_TEST_DOMAIN plus the two
    live-only assertions — platform timeout collects a sandbox, and
    label-scoped enumeration with destroy-by-id works across
    connections. Seed and sync-out bytes/seconds recorded per attempt
    for the OpenSandbox runtime, absent for Docker (D-41.5). Stub
    corrected wherever the live leg catches it lying.
  scope:
    - src/torve/adapters/runtime/opensandbox.py
    - src/torve/application/telemetry.py
    - tests/opensandbox_stub.py
    - tests/test_sandbox_images.py
  acceptance:
    - uv run pytest tests/test_sandbox_images.py
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
- phase: 1
  title: the image push path
  intent: >-
    torve sandbox build --push (D-41.4): build via the Docker runtime
    as today, push to the named registry reference, print the
    digest-pinned reference for the run config. The docs stop calling
    digest-only resolution a stopgap: on a pull platform the pinned
    reference is the resolution.
  scope:
    - src/torve/cli/sandbox.py
    - tests/test_cli.py
  acceptance:
    - uv run pytest tests/test_cli.py
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
- phase: 2
  title: the reachable broker
  intent: >-
    Remote endpoint mode (D-41.6): broker.bind and broker.advertise in
    configuration, the runtime composing the sandbox proxy env from
    the advertised address instead of the Docker gateway derivation,
    provider routes keeping the run token unchanged (D-41.2), and the
    pass-through leg refused loudly in remote endpoint mode with the
    destination named. The deployment page documents bind/advertise
    and the transport paragraph.
  scope:
    - src/torve/adapters/broker/local.py
    - src/torve/config/runconfig.py
    - src/torve/adapters/runtime/opensandbox.py
    - tests/test_broker.py
    - tests/test_broker_sealed.py
  acceptance:
    - uv run pytest tests/test_broker.py tests/test_broker_sealed.py
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
  depends_on: [1]
```
