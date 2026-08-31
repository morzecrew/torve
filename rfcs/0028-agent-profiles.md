---
id: 0028
title: Agent profiles
kind: design
status: accepted
depends_on: ["0004"]
informed_by: ["0013", "0021", "0024", "0027"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  A host-level library of named tier definitions under the operator's config directory, referenced by name from repository configuration and merged before validation — one authoritative copy of each agent incantation instead of a copy per root.
schema_version: 1
---

# RFC 0028 — Agent profiles

- **Scope:** How a tier definition — adapter, command template, model, image,
  retry variant — is named once on the operator's machine and referenced from
  many repository configurations, instead of being pasted into each. Covers
  the profile file format, the resolution and merge semantics inside
  `load_runner_config`, the interaction with `config_hash`, the doctor
  surface, and the fleet trust question the mechanism newly makes askable. No
  contract changes, no new ports, no change to any adapter: everything past
  `src/torve/config/runconfig.py` continues to see a plain resolved
  `TierConfig`, exactly as RFC 0004 §1 demands ("the concern leaks no further
  into the design"). Excludes remote or shared profile distribution, profile
  inheritance chains, and any cross-harness abstraction (refused by RFC 0027
  §Non-goals; a profile names one tier's wiring, it does not translate
  between harnesses).
- **Related:** [`0004`](0004-agents-tiering.md) §1, §6 ·
  [`0013`](0013-configuration-layout.md) D-13.3–D-13.4 · [`0024`](0024-fleet.md) §5.3 ·
  [`0027`](0027-harness-configuration-evolution.md) §5.1 ·
  `src/torve/config/runconfig.py` · `src/torve/config/fleet.py` ·
  `src/torve/application/telemetry.py` (unchanged, but the reason §5.3
  exists) · A-72 on RFC 0004 (the regime preimage — the recovery half of the
  same story)
- **Origin:** The 0022–0027 dogfooding campaign. The working configuration
  that drove ~30 landed tasks lived in a session scratchpad and was destroyed
  twice by session restarts; each rebuild re-typed the same claude and dsh
  incantations from memory. The lab repository carries a third copy, verbatim.

---

## 1. Summary

A tier entry in `.torve/config.yaml` may say `profile: <name>` instead of (or
in addition to) spelling out its fields. The name resolves to
`~/.config/torve/agents/<name>.yaml` on the operator's machine — the same
directory family as the fleet manifest, and off-limits to the repository
under work for the same reason. Resolution is a raw-dict merge before
validation: profile fields fill in, locally written fields win, and the
merged result is validated by the unchanged `TierConfig` model. Everything
downstream — dispatch, the broker validators, `config_hash` — sees the
resolved tier; the profile name itself never reaches telemetry, so two roots
on one profile are measurably one regime and an edited profile is visibly a
new one. `torve doctor` prints which profile each tier resolved from.

## 2. Motivation

The evidence is this repository's own operation:

- The dogfood configuration — a ~90-line YAML whose executor tier alone
  carries a multi-line `export HOME=/tmp CLAUDE_CODE_OAUTH_TOKEN={broker_token}
  ANTHROPIC_BASE_URL={broker_url}; claude -p ...` incantation, and whose
  reviewer tier carries the dsh overlay with `DSH_PRICE_*` and the
  report-usage reporter — existed only in an ephemeral scratchpad and was
  wiped twice. Each loss cost a hand-reconstruction of text that is pure
  boilerplate.
- `~/GitLibrary/Misery7100/torve-remote-lab` duplicates the dsh tier
  definition verbatim. When the report-usage invocation changed (the
  multi-frame zstd fix), the fix had to be applied in two places; nothing
  detects when the copies drift.
- Improvements to a command template are per-copy by construction. The
  base-sha pin rule and the evidence-format fix each rode a skill or prompt
  change precisely because the command-template layer had no single home to
  patch.

Three copies today; RFC 0024's fleet exists to grow the number of roots, and
every new root under the current shape starts by pasting a tier block.

## 3. Current state

Verified against the code:

- `TierConfig` (`src/torve/config/runconfig.py`) is a flat model —
  scalars and one string list — with `extra="forbid"` and an after-validator
  requiring `command` and `provider` for real adapters. Dotted variant
  entries (RFC 0027 §5.1, `executor.long-context`) are ordinary entries in
  the same `tiers` mapping.
- `load_runner_config` (`src/torve/config/runconfig.py`) reads exactly
  one file — `.torve/config.yaml`, or the `--config` override (D-13.4) —
  `yaml.safe_load`s it and hands the raw mapping to
  `RunnerConfig.model_validate`. There is no other configuration source, by
  D-13.3's design.
- `config_hash` (`src/torve/application/telemetry.py`) already digests
  `config.tiers` **post-load** via `model_dump()` — whatever resolution
  happens inside `load_runner_config` is hashed in resolved form with zero
  telemetry changes. Confirmed against `parts["tiers"]`.
- The fleet manifest already establishes the host-side precedent: an
  artefact that is *about* repositories lives on the operator's machine,
  "read from the operator's machine, never from a root the fleet ticks"
  (`src/torve/config/fleet.py`, D-24.1). `enforce_trust` refuses
  socket/network/broker settings by trust class but today says nothing about
  tier command lines.

## 4. Goals / Non-goals

**Goals**

- One authoritative copy of each agent incantation, host-level, named.
- A repository configuration that shrinks to `profile: <name>` plus genuinely
  local overrides.
- Regime comparability preserved exactly: profile content, never profile
  name, is what `config_hash` sees.
- Loud failure when a name resolves to nothing.

**Non-goals**

- **Profile distribution.** No registry, no URL, no sync between machines —
  the library is files in a directory; versioning it is the operator's
  dotfiles problem, and the regime preimage (A-72 on RFC 0004) is what makes
  history recoverable regardless.
- **Cross-harness abstraction.** RFC 0027 refused it and this document does
  not reopen it: a profile is one tier's literal wiring, not a portable
  description of an agent.
- **Per-task profile selection.** The contract selects a *variant*
  (`tier_variant`, D-27.2); which profile a variant's definition came from is
  invisible to contracts on purpose — otherwise a task pins host filesystem
  state.
- **Profile inheritance.** One merge level (profile → local overrides).
  A profile built on a profile re-creates the drift problem inside the
  library that the library exists to end.

## 5. Design

### 5.1 The profile file

One file per profile, `~/.config/torve/agents/<name>.yaml`, where `<name>` is
the filename stem and must match `[a-z0-9][a-z0-9-]*`. The body is a
`TierConfig` body — the same keys, no wrapper:

```yaml
# ~/.config/torve/agents/claude-sonnet-xhigh.yaml
adapter: harness
provider: anthropic
model: claude-sonnet-5
image: torve-battery:latest
command: >-
  export HOME=/tmp CLAUDE_CODE_OAUTH_TOKEN={broker_token}
  ANTHROPIC_BASE_URL={broker_url}; claude -p --model {model} ...
```

A profile file is *not* independently validated as a `TierConfig` — it may
legitimately be partial (an image-and-command skeleton whose `model` each
root sets). Its only intrinsic checks are: it parses as YAML, it is a
mapping, and every key is a `TierConfig` field name (unknown keys are
refused at resolution with the profile path in the message, which is
`extra="forbid"` applied at the merge boundary instead of after it, where
the error would name the wrong file).

Secrets doctrine is unchanged and unweakened: a profile carries environment
variable *names* at most (`api_key_env`), never values (D-4b), and under a
broker adapter the existing D-21.1 validator still refuses a resolved tier
naming any credential — profiles get no exemption because validation runs on
the merged result.

### 5.2 Resolution and merge

`TierConfig` gains one field: `profile: str = ""`. Resolution happens in
`load_runner_config`, on the **raw mapping**, before
`RunnerConfig.model_validate`:

```text
for each entry in raw["tiers"] carrying "profile":
    body   = yaml.safe_load(agents_dir / f"{name}.yaml")   # must be a mapping
    merged = {**body, **entry_without_profile_key}          # local wins
    raw["tiers"][key] = merged                              # profile name kept for doctor
```

Merging raw dicts, not models, is load-bearing: `TierConfig` fields default
to `""`, so a model-level merge cannot distinguish "locally set to empty"
from "never written" — the raw mapping can, by key presence. Shallow merge
suffices because the model is flat; the one list field (`api_key_env`)
replaces wholesale, never concatenates — appending environment names across
two files is how a credential channel gets assembled by accident.

Failure semantics, all at load time, all refusals:

- `profile` names no file → error naming the missing path and listing the
  stems present in the directory.
- The file is unreadable, not YAML, or not a mapping → error naming the file.
- A key in the body is not a `TierConfig` field → error naming the key and
  the file.
- The merged entry fails `TierConfig` validation → the normal pydantic
  error, which now correctly points at the repository's own file since local
  content had the last word.

There is no fallback of any kind: a configuration that references a profile
either resolves it or does not load. Silence is not a policy (§6b of
RFC 0004), and a tier that silently ran on inline defaults because a file
went missing is a regime change nobody chose.

### 5.3 The hash, and why telemetry is untouched

`config_hash` already serialises `config.tiers` after loading
(`src/torve/application/telemetry.py`), so the resolved content — command
template, image, model, everything the profile contributed — is digested
with no telemetry change. Two properties follow and both are the point:

- Two roots referencing `claude-sonnet-xhigh` produce identical `tiers`
  parts: one regime, comparable numbers, which pasted copies only achieve
  until they drift.
- Editing the profile changes the hash of every root that references it on
  its next run: a library edit is visibly a fleet-wide regime change, not a
  silent one.

The `profile` field itself rides along inside `model_dump()`; that is
harmless (same name + same content = same digest; same name + different
content = different digest, and the content part is what carries meaning).

### 5.4 Doctor and the operator surface

`torve doctor` gains one line per tier that resolved through a profile:
`tier executor <- profile claude-sonnet-xhigh (~/.config/torve/agents/claude-sonnet-xhigh.yaml)`.
No check is attached — resolution failures are load errors long before
doctor runs. Doctor does *not* warn about unreferenced profile files: a
library holding spare definitions is a library, not a defect.

### 5.5 Fleet trust — the question this creates

Today a `reviewed` or `untrusted` root's own `.torve/config.yaml` declares
tier command lines that the operator's engine will execute inside a sandbox
it provisions. The sandbox contains the blast radius, and the existing
validators already strip the sharpest edges (no `api_key_env` under broker,
no socket, sealed egress) — but the command template also chooses
`env_passthrough`-adjacent wiring and the image, and D-13.3's spirit is that
the repository under work configures nothing about the engine. Profiles make
the stricter rule *expressible* for the first time: a trust class could
require tiers to be `profile:`-only references with no inline `command` or
`image`. Whether `reviewed` should require it is deliberately left open
(D-28.6) — the fleet has two roots today and zero third-party ones, and a
rule written before the first real `untrusted` root exists would be graded
by imagination.

### Alternatives considered

- **YAML anchors / `<<:` merge keys in one bigger file.** Solves repetition
  only within a single file; the problem is repetition across roots and
  machines-worth of configs. Also invisible to doctor.
- **A `tiers_include: <path>` file include.** More general and strictly
  worse for it: an include splices arbitrary config across the D-13.3
  boundary, where a profile splices exactly one tier body from exactly one
  operator-owned directory. Generality here is the vulnerability.
- **Model-level merge (`model_copy(update=...)`).** Rejected on the
  set-vs-defaulted ambiguity in §5.2; the raw mapping is the only place
  "explicitly written" still exists.
- **Profiles in the repository (`.torve/agents/`).** Rejected: it changes
  copy-per-root to copy-per-root with extra steps, and it hands the
  repository under work the engine-side definition D-13.3 exists to deny it.

## 6. Tests

- Resolution unit family in `tests/test_runconfig.py`: profile fills fields;
  local key wins over profile key; locally-written empty string wins (the
  §5.2 ambiguity, pinned as a test); `api_key_env` replaces, never merges;
  unknown profile name, non-mapping body, and unknown key each refuse with
  the file named.
- Validator interaction: a profile supplying `api_key_env` under a broker
  adapter is refused by the existing D-21.1 validator on the merged result —
  asserting profiles cannot launder a credential channel.
- Hash property test: two configs referencing one profile hash identical
  `tiers` parts; a profile edit changes the digest. Uses `config_hash`
  as-is — the test *is* the proof that telemetry needed no change.
- Doctor: provenance line rendered; absent when no tier uses a profile.

## 7. Docs

README configuration section gains the profile paragraph and one example.
The dogfood configuration migrates to `profile:` references as its own
validation — the operator-visible proof is `.torve/config.yaml` shrinking.

## 8. Out of scope

- **Profile-aware `enforce_trust`** — named as the escape hatch, not built;
  D-28.6 holds the question and real third-party roots settle it.
- **A `torve agents` CLI verb** (list/show profiles) — doctor's provenance
  line covers the operational need; a verb is warranted when the library
  outgrows `ls ~/.config/torve/agents/`.
- **Windows/XDG path variance** — the directory derives from the same
  resolution the fleet manifest uses, whatever that is; this RFC does not
  re-decide path conventions.

## 9. Risks

- **A shared profile becomes a shared blast radius**: one bad edit breaks
  every root's next dispatch. Accepted deliberately — loud simultaneous
  failure at load time is the cheap version of the silent drift it replaces,
  and the regime preimage (A-72) plus the operator's own file history bound
  the recovery cost.
- **Ambient host state enters config meaning**: a committed
  `.torve/config.yaml` no longer fully determines the tier on its own.
  Mitigated by the hash (§5.3) — the *measured* regime is always the
  resolved content — and by refusal-on-missing, which makes the dependency
  impossible to miss.
- **Read as a security boundary**: a profile is a convenience and a D-13.3
  hygiene improvement, not an isolation mechanism; isolation stays with the
  sandbox, the broker and the trust classes. §5.5's framing keeps the claim
  honest.

## 10. Unresolved questions

- D-28.6 (below): whether `reviewed`/`untrusted` trust classes must take
  tiers exclusively from profiles. Settled by the first third-party root,
  not before.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-28.1 | `LOCKED` | Profiles live in the operator's config directory beside the fleet manifest, never in the repository under work; a repository references a profile by name only | `src/torve/config/runconfig.py` | D-13.3 extended to agent definitions: the repo cannot ship or alter an engine-side incantation; moving profiles repo-side later means re-opening the fleet trust model |
| D-28.2 | `LOCKED` | Resolution is a raw-mapping merge inside `load_runner_config`, before validation, locally-present keys winning; everything downstream, `config_hash` included, sees only the resolved `TierConfig` | `src/torve/config/runconfig.py` | Regime identity is content, never name — an edited profile is a new regime on every referencing root; changing to name-based hashing later silently merges regimes and breaks every cross-root comparison |
| D-28.3 | `LOCKED` | Any resolution failure — missing file, non-mapping body, unknown key, invalid merged result — refuses the configuration load; there is no fallback to inline defaults | `src/torve/config/runconfig.py` | A tier that silently ran without its profile is a regime change nobody chose; fail-closed is what lets a profile edit be trusted as fleet-wide |
| D-28.4 | `ASSUMED` | One merge level: profile plus local overrides; no profile-to-profile inheritance, and list fields replace wholesale rather than concatenate | `src/torve/config/runconfig.py` | Inheritance re-creates drift inside the library; concatenating `api_key_env` across files assembles a credential channel by accident |
| D-28.5 | `ASSUMED` | A profile body may be partial and is checked only for key validity at merge time; `TierConfig` validation runs once, on the merged result | `src/torve/config/runconfig.py` | Skeleton profiles (image+command, per-root model) stay expressible; validating profiles standalone would refuse them |
| D-28.7 | `ASSUMED` | Doctor prints per-tier profile provenance and attaches no check; unreferenced profiles are not warned about | `src/torve/cli/doctor.py` | Resolution already fails loudly at load; a doctor warning would be a second, later copy of the same signal |
| D-28.6 | `OPEN` | Whether `reviewed` and `untrusted` trust classes must take tiers exclusively from `profile:` references (no inline `command`/`image`); the first third-party root settles it, and `enforce_trust` is where the rule would land | `src/torve/config/fleet.py` | Written before a real untrusted root exists, the rule would be graded by imagination; left open, the capability question at least has a name |

## 12. Phasing

```yaml
- phase: 1
  title: profile-resolution
  intent: >-
    TierConfig gains the profile field and load_runner_config resolves it: raw-mapping merge from the operator's agents directory before validation, local keys winning, every failure a refusal naming the file (D-28.1–D-28.5). The hash property arrives for free because config_hash already digests resolved tiers; the test family pins the merge semantics, the refusals, the D-21.1 interaction and the hash property.
  scope:
    - "src/torve/config/runconfig.py"
    - "tests/test_runconfig.py"
  acceptance:
    - "uv run pytest tests/test_runconfig.py"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run ruff check ."
  depends_on: []
- phase: 2
  title: doctor-provenance
  intent: >-
    torve doctor prints one provenance line per tier that resolved through a profile (D-28.7), no check attached, and the README configuration section gains the profile paragraph with one example.
  scope:
    - "src/torve/cli/doctor.py"
    - "tests/test_doctor.py"
    - "README.md"
  acceptance:
    - "uv run pytest tests/test_doctor.py"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run ruff check ."
  depends_on: [1]
```
