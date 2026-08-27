---
id: "0021"
title: The egress broker
kind: design
status: draft
implementation: none
depends_on: ["0003", "0004"]
informed_by: ["0001", "0013", "0017"]
supersedes: []
superseded_by: null
amended_by: []
retired: []
owner: Lev Litvinov
description: >-
  Credential custody and outbound traffic for a sandbox: the agent holds no
  provider key and the broker injects, routes and meters at the wire — closing
  D-4b under Docker today, with the OpenSandbox vault as one adapter rather
  than a prerequisite.
schema_version: 1
---

# RFC 0021 — The egress broker

- **Scope:** How provider credentials reach a model and how a sandbox's
  outbound traffic is constrained. Introduces a `Broker` port between the
  sandbox and every model or remote-MCP provider: the sandbox holds no
  credential, the broker injects it, enforces the routing RFC 0004 §6b
  already declares, and meters spend at the wire. Extends RFC 0003 §4
  (runtime), RFC 0004 §6b (provider routing) and RFC 0017 §3 (configuration
  channels). Excludes egress for git and package installation, which keep
  their existing path until the sealed mode of §5.2 needs them declared;
  excludes any change to gates, review isolation or the workspace.
- **Related:** [`0017`](0017-sandbox-provisioning.md) §3 · [`0004`](0004-agents-tiering.md) §6b ·
  `src/torve/adapters/runtime/docker.py` · `src/torve/config/runconfig.py` ·
  `src/torve/application/ports.py`
- **Inherits:** D-4, D-4b, D-31 from RFC 0001; D-3.3 from RFC 0003; D-4.1,
  D-4.2, D-4.6, D-4.8 from RFC 0004; D-13.3 from RFC 0013; D-17.4, D-17.6,
  D-17.10 from RFC 0017.

---

## 1. Summary

A host-side broker owns every provider credential and every outbound
connection a run makes to a model. Tiers stop naming `api_key_env`; the
broker's configuration names it once. The sandbox is pointed at the broker's
loopback endpoint, so the agent process never holds a key it could leak, log
or spend outside its budget. The broker enforces provider routing at the
wire, meters usage from the provider's own responses, and refuses requests
past the run's budget — turning `cost_anomaly` from a post-mortem into a
stop. Two modes: `endpoint` closes credential custody and metering on the
default bridge; `sealed` adds containment by putting the sandbox on an
internal Docker network whose only reachable address is the broker.

## 2. Motivation

D-4b is `LOCKED` and reads: agents never hold real credentials; outbound
secrets are injected by the runtime's vault. The plumbing honours it and the
outcome does not.

- `TierConfig.api_key_env` carries names, never values, and
  `SandboxSpec.env_passthrough` carries names too — but the Docker adapter
  forwards each name so the daemon resolves the *value* from the invoking
  environment. The variable lands in the container. The agent process holds a
  live provider key for the length of the run.
- `runtime.network` defaults to `""` — the daemon's default bridge, which is
  full outbound internet. The only other value on the roster is `host`, which
  trades away more isolation, not less. There is no configuration in which a
  sandbox cannot reach an arbitrary destination.
- `route_provider` in `src/torve/config/runconfig.py` is a dispatch-time
  assertion over configuration. Nothing observes where the sandbox actually
  connects, so RFC 0004 §6b's routing is a promise about intent rather than a
  property of the run.
- D-17.6 classifies a remote MCP endpoint as an egress destination under
  provider routing. No mechanism makes that classification bite.
- `AgentResult.cost_usd` is the harness's own report of its own spend. The
  `cost_anomaly` reason therefore rests on the measured subject's testimony,
  read after the run is over.

Composed, those five say the same thing: the security and economic properties
the corpus already decided are, in the shipped regime, descriptions of a
configuration file rather than facts about a process. RFC 0017 §2 says what
OpenSandbox adds — "the vault, egress control" — it adds at execution time.
Waiting for a server that does not yet exist leaves a `LOCKED` charter
decision unmet indefinitely, which is a worse outcome than building the
mechanism where it can be built.

## 3. Current state

Verified against the tree, not from memory:

- `never_send` is enforced by lifting matching files out of the worktree for
  the duration of the attempt (`_withhold_never_send` / `_restore_never_send`
  in `src/torve/application/runner.py`). This is the strong half of the data
  boundary and this document does not touch it: files that never enter the
  workspace cannot leave by any channel, which is better than any packet
  filter. The network half simply does not exist.
- `PROXY_ENV` in `src/torve/application/ports.py` already carries the standard
  proxy variables into a spec, for the case of a host-side proxy or VPN. The
  convention the broker needs is therefore already in the port.
- The four-harness roster runs on configuration alone (RFC 0017 §1), and every
  harness on it takes a base-URL or endpoint setting through the tier command
  — which is why the `endpoint` mode of §5.2 needs no TLS interception.
- Telemetry records `agent` (adapter, model, provider version) per run and
  nothing about connections.

## 4. Goals / Non-goals

**Goals**

- No provider credential inside any sandbox, under either runtime adapter.
- Provider routing enforced where the connection happens, not only where the
  configuration is read.
- One usage number per run that the measured subject did not produce.
- A budget that can stop a run in progress rather than explain it afterwards.
- The OpenSandbox vault becomes an adapter of this port when a server exists,
  not a precondition for any of the above.

**Non-goals**

- **A general-purpose egress firewall.** The broker constrains provider
  traffic; `sealed` mode constrains everything else by declaration, not by
  inspection. Deep packet policy is somebody else's product.
- **Replacing `never_send`.** Withholding files is stronger than filtering
  packets and stays exactly as it is.
- **Auditing prompt content.** The broker sees request bodies and must not
  keep them: a stored prompt archive is a new secret store and a new leak
  surface. Counts and metadata only (D-21.7).
- **Subscription-harness authentication.** OAuth flows on the slot volume
  (D-4.2) are unchanged; they are session state, not an outbound credential
  the engine issues.

## 5. Design

### 5.1 The port

```python
class Broker(Protocol):
    name: str                     # "local" | "opensandbox" | "none"

    def open(self, run: str, routing: Routing, budget: Budget) -> BrokerHandle: ...
    def close(self, handle: BrokerHandle) -> BrokerUsage: ...
```

`BrokerHandle` carries what the sandbox needs and nothing else: a base URL per
routed provider, and a per-run bearer token the broker issued and will revoke
at `close`. `BrokerUsage` carries request count, token counts per provider
where the provider reports them, wall time, and the count of refusals by
cause. The handle's fields reach the sandbox through the channel RFC 0017 §3
already assigns to operator non-secret knobs — inline in the tier command —
because a broker URL and a run-scoped token are exactly that: not identity,
not task context, not a standing secret.

Adapters:

| Adapter | What it is |
| --- | --- |
| `local` | A process the runner starts on loopback for the life of the run; holds the real keys, read from its own environment |
| `opensandbox` | The server's vault and egress control behind the same port, when a server exists |
| `none` | Today's behaviour, named explicitly: keys pass through, no metering, no wire routing |

`none` is the default in this document's first phase and remains legal
forever: a repository the operator runs on their own machine against their own
key may reasonably decline the extra process. What is not legal is `none` by
accident — `torve doctor` reports the broker adapter in force and says plainly
that `none` leaves D-4b unmet.

### 5.2 Two modes, because custody and containment are different problems

**`endpoint` (phase 1).** The broker is a reverse proxy, one route per
configured provider. The tier command's endpoint setting is substituted with
the broker's URL; `api_key_env` on a brokered tier must be empty and a
non-empty one is a refused configuration, not a warning — a second channel for
a secret is the D-17.4 failure. The sandbox keeps the default bridge, so it
can still reach the internet; what it cannot do is take a key with it. This
closes custody and metering and is honest about not closing exfiltration.

**`sealed` (phase 2).** The sandbox joins a user-defined Docker network
created `--internal` — a native daemon feature meaning containers on it reach
each other and nothing outside — with the broker attached to the same network.
Every outbound need is then declared: providers are proxied as in `endpoint`,
and anything else the run legitimately requires (a package index, the forge)
is a named host the broker will `CONNECT` to without inspecting. An
undeclared destination fails at DNS or at connect, loudly, and the run
escalates as a configuration error rather than silently succeeding through a
path nobody meant to leave open.

Sealed mode is what makes RFC 0017 D-17.10's sentence checkable: a repository
the operator does not trust as their own shell can be run at all, because the
capability it is granted is enumerable.

### 5.3 Routing enforced twice, deliberately

`route_provider` stays exactly where it is. A dispatch-time refusal is cheap,
happens before a sandbox exists, and produces the error message a human can
act on. The broker is the second line: a request for a provider this run is
not routed to is refused at the wire and recorded as an engine event naming
the run, the provider and the tier. Both, because the first is the check that
explains and the second is the check that is true. A divergence between them
— a wire refusal for a provider dispatch allowed — is itself a defect report
about the configuration reader.

### 5.4 Metering, and the number the subject did not write

The broker records usage per run from the provider's own responses. Where an
adapter also reports `cost_usd`, both are recorded and the broker's is
authoritative; a divergence past a configured tolerance appends an engine
event. An agent under-reporting its own spend is precisely the failure the
number exists to catch, and until now the corpus had no way to notice it.

Budget enforcement moves to the same place. `Budget.tokens` on the task
contract becomes a bound the broker holds: requests past it are refused, the
attempt fails on the refusal, and the run escalates `cost_anomaly` — in
progress, on the run that overspent, rather than in a report about last week.

### 5.5 What this does not change

Gates never read broker output as evidence (D-3's boundary is untouched: the
broker is infrastructure, not a gate). Review isolation is unchanged. The
workspace, skills materialisation and the image are untouched — a brokered run
and an unbrokered one differ in the tier command line and in nothing else the
agent can see. `config_hash` gains the broker adapter name and the routing
block, because two runs under different egress regimes are two regimes
(D-17.1's rule, one artefact over).

### Alternatives considered

- **Wait for the OpenSandbox vault.** Its trade is that a `LOCKED` charter
  decision stays unmet for as long as the server does not exist, and that the
  first repository the operator does not fully trust cannot be taken on at
  all. The broker inverts the dependency: the vault becomes an adapter of a
  port that already works, which is also the cheaper integration when the
  server arrives.
- **A TLS-intercepting forward proxy as the default.** It would cover
  harnesses with hard-coded endpoints, at the cost of a CA in every image —
  harness identity drift (RFC 0017 §2), a permanent debugging surface, and a
  trust anchor inside a container running someone else's model. Kept only as
  the mechanism `sealed` uses for pass-through hosts, where `CONNECT` without
  interception is enough.
- **Per-request short-lived provider tokens.** Only some providers issue them,
  so it would be a per-provider mechanism giving a property the broker gives
  uniformly with no provider-specific code.
- **Metering by parsing harness output.** That is the current arrangement and
  it is the subject reporting on itself; the whole point of §5.4 is that the
  number comes from somewhere the agent does not control.

## 6. Tests

The conformance battery RFC 0003 already runs against both runtime adapters
gains a broker family, asserted identically for `local` and for the
OpenSandbox stub: a brokered run's sandbox environment contains no name from
the broker's key set; a request to an unrouted provider is refused with the
recorded cause; usage is non-zero and recorded; a run whose token budget is
exhausted mid-attempt escalates `cost_anomaly`. Sealed mode adds one case an
integration test can assert cheaply: a sandbox on the internal network fails
to reach a host that is not declared, and the failure names the destination.

Sabotage-suite discipline (RFC 0002 §7) applies to the refusals: a broker that
never refuses anything is indistinguishable from an absent broker, so each
refusal path gets a red-on-demand case.

## 7. Docs

The `.torve/config.yaml` reference gains the `broker` block. RFC 0017 §3's
channel table gains one row — the broker handle as an operator non-secret knob
— and its "an operator secret" row is amended by this document's execution to
say that under a broker the secret has no sandbox-side channel at all. The
threat-model wording must stay honest: `endpoint` mode closes custody, not
exfiltration, and the documentation says so in the same sentence that
introduces it.

## 8. Out of scope

- **The OpenSandbox broker adapter.** Named as the third adapter and
  deliberately unbuilt: it is condition-gated on a live server exactly as RFC
  0003's integration is. The port exists so that work is an adapter and not a
  redesign.
- **Egress policy for git and package installs in `endpoint` mode.** They keep
  the default bridge. Declaring them is what `sealed` mode is for, and pulling
  it forward would make phase 1 a network project.
- **Advisory and feed inputs.** A CVE feed or a release watcher is an inbound
  network dependency with its own trust question; it belongs to RFC 0023's
  triggers and is refused here.
- **Rate limiting and retry policy.** The broker is positioned to do both and
  will not: retries are the harness's business, and a broker that retries
  changes the attempt's semantics invisibly.

## 9. Risks

- **The broker becomes a second product.** The charter's named failure mode.
  Mitigation: the port is four methods, the local adapter is a proxy with an
  allowlist and a counter, and everything else on the wish list is in §8.
- **Read as security theatre.** `endpoint` mode does not stop a determined
  agent from exfiltrating the workspace over the open bridge, and a reader who
  believes otherwise is worse off than before. Mitigated only by wording:
  every document that mentions the mode states its ceiling in the same breath.
- **A single point of failure in the run path.** A broker that dies takes the
  run with it. Accepted: the failure is loud, the run escalates
  `gate_infrastructure_failure`-class, and the alternative — falling back to
  direct keys on error — would be a security property that disappears exactly
  when something is wrong.
- **Metering that quietly disagrees with the invoice.** Providers report
  usage inconsistently. The number is for comparison between runs, not for
  accounting; RFC 0004 §6a's quasi-experiment warning applies unchanged.

## 10. Unresolved questions

- Whether `sealed` mode's declared pass-through hosts belong in the broker
  block or in `runtime` — the same item is arguably egress policy and
  arguably sandbox provisioning. Settled by whichever file the first real
  sealed repository makes unreadable.
- Whether the local adapter should be a subprocess or an in-process thread of
  the runner. A subprocess survives a runner crash long enough to be reaped
  and is separately debuggable; a thread has no lifecycle of its own. Left
  `OPEN` — the conformance battery does not care and execution will.
- What tolerance makes a broker-versus-adapter cost divergence worth an event.
  It cannot be chosen before there are two weeks of pairs to look at.

## 11. Decisions

> Numbering note: the charter's `D-21a`–`D-21d` are the execution-log family,
> written before `D-A.*` closed, and are unrelated to this document's
> `D-21.n`. The corpus rule is mechanical — RFC number to prefix — and
> renumbering either family would orphan citations (D-A.4).

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-21.1 | `LOCKED` | A brokered tier names no credential: `api_key_env` must be empty and a non-empty one is a refused configuration, not a warning | `src/torve/config/runconfig.py` `src/torve/application/runner.py` | One item, one channel (D-17.4); a fallback path for the key is the leak the broker exists to remove |
| D-21.2 | `LOCKED` | The broker is a port with adapters (`local`, `opensandbox`, `none`); the OpenSandbox vault is an adapter of it, never a prerequisite for it | `src/torve/application/ports.py` `src/torve/adapters/broker/**` | D-4b becomes reachable under the runtime that exists today, and the server integration becomes an adapter rather than a redesign |
| D-21.3 | `ASSUMED` | Two modes: `endpoint` (reverse proxy per provider, default bridge) closes custody and metering; `sealed` (internal Docker network shared with the broker) adds containment and requires every other destination declared | `src/torve/adapters/runtime/docker.py` `src/torve/config/runconfig.py` | Custody and containment have different costs and different readiness; conflating them would delay the cheap half behind the expensive one |
| D-21.4 | `LOCKED` | Provider routing is enforced twice: `route_provider` at dispatch for the message, the broker at the wire for the fact; a wire refusal for a dispatch-allowed provider is a defect report about the configuration reader | `src/torve/config/runconfig.py` `src/torve/adapters/broker/**` | RFC 0004 §6b currently describes intent; only the wire makes it a property of the run |
| D-21.5 | `ASSUMED` | Broker-measured usage is authoritative where it exists; an adapter's self-reported `cost_usd` is recorded beside it and a divergence past tolerance is an engine event | `src/torve/application/telemetry.py` `src/torve/adapters/broker/**` | A number produced by the measured subject cannot detect the subject under-reporting |
| D-21.6 | `ASSUMED` | The task's token budget is held by the broker and enforced mid-run: requests past it are refused and the run escalates `cost_anomaly` | `src/torve/adapters/broker/**` `src/torve/application/runner.py` | A budget checked after the fact is a report, not a budget |
| D-21.7 | `LOCKED` | The broker keeps counts and metadata, never request or response bodies | `src/torve/adapters/broker/**` | A prompt archive is a new secret store and a new leak surface, created by the component built to reduce both |
| D-21.8 | `ASSUMED` | The broker adapter name and the run's routing join `config_hash`; two runs under different egress regimes are two regimes | `src/torve/application/telemetry.py` | D-17.1's rule, one artefact over: a comparison across an unrecorded regime change is unfounded |
| D-21.9 | `ASSUMED` | `none` stays legal and is the first phase's default, but `torve doctor` names it and states plainly that it leaves D-4b unmet | `src/torve/cli/doctor.py` `src/torve/config/runconfig.py` | Opting out must be a decision someone can be shown making, not a silent default |
| D-21.10 | `OPEN` | Whether the `local` adapter is a subprocess or an in-process thread of the runner; execution decides and logs it | `src/torve/adapters/broker/**` | A subprocess is separately reapable and debuggable; a thread has no lifecycle of its own. The conformance battery is indifferent, so the implementer's evidence is better than this table's guess |
| D-21.11 | `OPEN` | Where `sealed` mode's declared pass-through hosts live — the broker block or `runtime` | `src/torve/config/runconfig.py` | The item is arguably egress policy and arguably sandbox provisioning; the first real sealed repository will make one of the two files unreadable, and that is the answer |

## Phasing

```yaml
- phase: 1
  title: broker-port-and-endpoint-mode
  intent: |
    The Broker port and its local adapter in endpoint mode, so that a run's
    provider credential never enters a sandbox: the broker holds the keys,
    exposes one loopback route per routed provider, issues a run-scoped
    token, and the tier command is substituted with the broker's URL. A
    brokered tier naming api_key_env is refused. Wire-side routing refusal,
    per-run usage metering from provider responses, mid-run budget refusal
    escalating cost_anomaly, the broker adapter and routing in config_hash,
    and torve doctor naming the adapter in force. The `none` adapter makes
    today's behaviour explicit and stays the default for this phase.
  scope:
    - "src/torve/adapters/broker/**"
    - "src/torve/application/**"
    - "src/torve/config/**"
    - "src/torve/cli/**"
    - "tests/**"
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
    - "uv run lint-imports"
    - "uv run torve rfc check"
  depends_on: []
- phase: 2
  title: sealed-mode-containment
  intent: |
    Containment on top of custody: the sandbox joins an internal Docker
    network shared with the broker, so no destination is reachable except
    through it, and every non-provider host the run legitimately needs is
    declared and CONNECTed without inspection. An undeclared destination
    fails loudly and the run escalates as a configuration error rather than
    succeeding through a path nobody meant to leave open. This is what makes
    D-17.10's trust sentence checkable for a repository the operator does
    not trust as their own shell.
  scope:
    - "src/torve/adapters/runtime/**"
    - "src/torve/adapters/broker/**"
    - "tests/**"
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
    - "uv run lint-imports"
  depends_on: [1]
```

## 12. Exit criteria

- A brokered run whose sandbox environment demonstrably contains no provider
  key, shown from inside the sandbox, with the run green.
- A tier pointed at a provider the repository denies: refused at dispatch with
  the routing message, and — with the dispatch check disabled for the
  experiment — refused again at the wire with the event recorded.
- One run with both a broker-metered usage figure and an adapter-reported
  `cost_usd` in its record, the divergence visible.
- One run stopped mid-attempt by the token budget, escalating `cost_anomaly`,
  with the refusal in the broker's counts.
- *(phase 2)* A sealed run that cannot reach an undeclared host, fails
  loudly, and passes once the host is declared.

## Amendments
