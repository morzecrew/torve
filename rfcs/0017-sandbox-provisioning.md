---
id: "0017"
title: Sandbox provisioning and harness configuration
status: accepted
implementation: none
depends_on: ["0003", "0004"]
informed_by: ["0013", "0016"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  How a sandbox image comes to exist and how a harness's configuration reaches
  it: images as digest-pinned inputs to the run, five configuration channels
  routed by nature, and the policy lines for MCP servers and persistent memory.
schema_version: 1
---

# RFC 0017 — Sandbox provisioning and harness configuration

- **Scope:** How sandbox images are defined, built and identified; how a
  harness's configuration, tools and state reach the sandbox. Extends
  RFC 0003 §4 (the runtime) and RFC 0004 §1/§6 (adapters, telemetry).
  Excludes review (0005) and planning (0007).
- **Inherits:** D-4, D-4b, D-31 from RFC 0001; D-4.1, D-4.6, D-4.8 from
  RFC 0004; D-13.3 from RFC 0013.

---

## 1. Two gaps, found by measurement

The first shadow-run campaign (2026-08-22) surfaced both problems this
document settles.

**Images are ambient.** The four harness images were hand-built in a
temporary directory and addressed by mutable tag. One of them was rebuilt
twice in a single afternoon under the same name, and nothing in `config_hash`
noticed — a silent regime change of exactly the kind D-4.6 exists to catch,
except the drifting artefact was the sandbox rather than the model. A tag
answers "what does this name point at right now"; a measurement needs "what
was this sandbox, exactly, when the number was produced".

**Harness configuration has no doctrine.** Provider blocks, permission
presets, MCP definitions, credentials and session state each ended up
wherever the day's experiment put them. It worked — four harnesses run on
configuration alone — but "wherever it landed" is how a secret ends up in an
image layer or a repository ends up configuring the engine that works on it
(the D-13.3 failure, one level down).

## 2. The image is an input, not an environment

A sandbox image is part of the regime a run belongs to, with the same
standing as the gate manifest and the model version:

- **The digest is the identity.** At dispatch the runtime resolves the
  configured image reference to its content digest; the digest joins
  `config_hash` and rides the attempt record. Two runs under the same tag
  but different digests are two regimes, and the records say so.
- **Definitions are reviewed artefacts.** Each image the repository uses is
  defined under `.torve/sandbox/<name>/` (a Dockerfile and whatever it
  copies), versioned and reviewed like the gate manifest — `torve.yaml`
  names images, the definitions say what the names mean.
- **`torve sandbox build [name]`** builds a definition and reports the
  digest; `torve doctor` goes red when a configured image does not exist or
  its definition directory is missing. Building stays an operator action —
  the engine never builds images mid-run, because a build is a regime change
  and regime changes are decisions.
- **Images are thin.** Base runtime, the harness, `git`, `uv` — identity
  only. Everything task-specific arrives via the workspace at dispatch;
  anything baked deeper is invisible to review and to the hash of the work.

**OpenSandbox is the same doctrine over a registry.** The server cannot see
local tags, so definitions publish (`torve sandbox build --push`) and
configuration names registry references; the digest rule is
runtime-independent. What OpenSandbox adds — the vault, egress control — it
adds at execution time, not at image identity time.

## 3. Configuration routes by nature

Five channels exist, and every configuration item belongs to exactly one,
decided by what the item *is*:

| The item is… | Channel | Examples |
| --- | --- | --- |
| harness identity | baked into the image | provider blocks, permission presets, stdio MCP definitions |
| task context | the workspace at dispatch | contract prompt, `AGENTS.md`, skills materialized per role (D-9.7) |
| an operator secret | env passthrough, names only (D-4b) | `DEEPSEEK_API_KEY`, OAuth tokens |
| operator non-secret knobs | inline in the tier command | model flags, `HOME`, endpoint URLs |
| session state | the per-slot volume (D-4.2) | subscription credentials, token refresh, memory if enabled |

Two rules fall out. A secret never has a second channel: not in an image
layer, not in a committed file, not in a repository the agent can read. And
the repository under work configures nothing about the harness that works on
it (D-13.3 extended one level down): a repo-carried harness config file is a
prompt-injection surface wearing a config extension.

Should a harness ever need per-tier configuration *files* that are neither
identity nor task context, the channel is `tier.home` — a directory
materialized into the sandbox at dispatch the way skills are. No harness on
the current roster needs it; it is specified so the day one does, the answer
is not "wherever it lands".

## 4. MCP servers

A stdio MCP server is a program the harness runs — that is harness identity,
and it lives in the image, versioned by the digest like everything else.

A remote MCP endpoint is an egress destination: repository contents flow to
it exactly as they flow to a model provider. It therefore falls under
provider routing (D-4.8) — named in the runner's configuration, allowed or
denied per repository, enforced at dispatch — and is never configured from
the repository under work. An MCP server that both runs locally and calls
out (a proxy) is classified by where the data goes: remote.

## 5. Persistent memory

Memory is where three standing decisions meet, and all three push the same
way:

- **D-31:** agents do not communicate. A memory store readable by more than
  one slot is a communication channel with a euphemism. Memory, where it
  exists, is per-slot — one slot, one volume, no sharing.
- **Measurement (0004 §6a):** a memory-carrying agent's run N depends on
  runs 1..N−1. Shadow replays exist to produce comparable numbers, so a
  shadow run never mounts memory — the replay measures the harness, not the
  accumulated season.
- **The contract is the interface (D-1.7):** an executor that needs run N−1
  to understand run N has a contract that failed to say what changes and
  why. Memory off is the default for executor tiers; enabling it is a
  per-tier decision made knowingly, and it lives on the slot's volume.

## 6. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-17.1 | `LOCKED` | The image digest is the sandbox's identity: resolved at dispatch, joined into `config_hash`, recorded on the attempt | `src/torve/application/telemetry.py` `src/torve/adapters/runtime/**` | A mutable tag makes every before/after comparison unfounded — the D-4.6 failure, one artefact over |
| D-17.2 | `ASSUMED` | Image definitions live under `.torve/sandbox/<name>/`; `torve sandbox build` builds and reports the digest; `torve doctor` reds on a configured image that does not exist | `src/torve/cli/**` `.torve/sandbox/**` | Ambient images are unreviewable regime changes |
| D-17.3 | `LOCKED` | The engine never builds images mid-run; building is an operator action | `src/torve/application/**` | A build is a regime change, and regime changes are decisions |
| D-17.4 | `LOCKED` | Configuration routes by nature — identity in the image, task context in the workspace, secrets as env names, knobs in the command, state on the slot volume; one item, one channel | `src/torve/config/runconfig.py` | A second channel for a secret is a leak; a repo-carried harness config is an injection surface |
| D-17.5 | `ASSUMED` | `tier.home` is the per-tier file channel, materialized at dispatch like skills; deferred until a harness needs it | `src/torve/config/runconfig.py` `src/torve/application/skills.py` | Specified now so the first need does not improvise |
| D-17.6 | `LOCKED` | stdio MCP servers are image content; remote MCP endpoints are egress destinations under provider routing (D-4.8), never configured from the repository under work | `src/torve/config/runconfig.py` | Repository contents flow to MCP endpoints exactly as to providers |
| D-17.7 | `LOCKED` | Memory is off for executor tiers by default; enabled memory is per-slot on the slot's volume, never shared between slots (D-31), and never mounted in a shadow run | `src/torve/application/shadow.py` `src/torve/config/runconfig.py` | Shared memory is agent communication; remembered shadow runs are incomparable numbers |
| D-17.8 | `ASSUMED` | Images are thin: base runtime, harness, `git`, `uv`; everything task-specific arrives via the workspace | `.torve/sandbox/**` | What is baked deeper is invisible to review and to the hash of the work |

## 7. Exit criteria

- The current four-harness roster rebuilt from committed definitions under
  `.torve/sandbox/`, digests visible in attempt records.
- `torve doctor` observed red on a configured-but-absent image.
- One shadow replay whose record's image digest differs after a deliberate
  rebuild — the drift the hash now catches, demonstrated.
