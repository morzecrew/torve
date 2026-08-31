---
id: 0029
title: Agent equipment
kind: design
status: accepted
depends_on: ["0009", "0027", "0028"]
informed_by: ["0004", "0007", "0026"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  Per-tier skill sets and prompt extras layered over role-scoped defaults, so a named variant becomes a persona — equipped, addressable from a document's phasing, and measured as its own regime by machinery that already exists.
schema_version: 1
---

# RFC 0029 — Agent equipment

- **Scope:** What a configured tier carries into the sandbox beyond its
  wiring: which skills are materialized for it and which working rules ride
  its prompt. Covers two fields on `TierConfig` (`skills`, `prompt_extras`),
  their interaction with the role-scoped sets of RFC 0009 §3 and the profile
  merge of RFC 0028, the `tier_variant` field on a Phasing entry so a
  document can address a persona at authoring time, and the doctor line that
  shows the resolved equipment. No new ports, no new records: measurement
  rides `config_hash` (tiers are already digested resolved) and comparison
  rides RFC 0027's variant machinery unchanged. Excludes any multi-agent
  mechanism inside one task — collaboration remains what the engine already
  is (decomposition fans out disjoint fences, the review lane pairs models
  sequentially) — and excludes engine-side assignment of variants to tasks:
  a variant is named by an author or a drafter in a reviewable document,
  never inferred by the engine from task content.
- **Related:** [`0009`](0009-skills-evals.md) §3 (role-scoped sets, D-9.1) ·
  [`0027`](0027-harness-configuration-evolution.md) §5.1 (variants, D-27.2) ·
  [`0028`](0028-agent-profiles.md) (profiles; merge semantics D-28.4) ·
  `src/torve/application/skills.py` · `src/torve/adapters/agent/harness.py` ·
  `src/torve/config/runconfig.py` · `src/torve/config/rfc_parse.py`
- **Origin:** The operator conversation after RFC 0028 shipped: profiles
  name an agent's wiring, variants make it addressable, but every agent
  still receives the identical skill set for its role and the identical
  prompt — a "persona" is not expressible, so neither is the question
  "does a specialist beat the generalist here", which is exactly the kind
  of question RFC 0027 built its displacement machinery to answer.

---

## 1. Summary

A tier entry (seat or dotted variant, in the repository's config or in a
profile) may carry `skills:` — a full override of the role-scoped skill set,
resolved by the same fail-closed materializer — and `prompt_extras:` — short
working-rule lines appended to the built prompt after the charter's base
rules, which are never removable. A Phasing entry may carry `tier_variant:`,
which `torve plan` copies onto the minted contract, so "the docs phases run
under the copywriter" is a line in a reviewed document. Equipment is part of
the resolved tier, so `config_hash` already separates an equipped variant
into its own regime, and RFC 0027's replay-and-displace machinery already
answers whether the persona earns its name. Doctor prints what each tier
resolved to carry.

## 2. Motivation

Three facts, all verified in code, currently make a persona inexpressible:

- Skill selection is **role**-scoped only: `SkillsConfig.sets` keys on
  `implement`/`review`/`revert`/`author` and `materialize` receives the
  role alone (`src/torve/application/skills.py`) — every executor variant,
  whatever its name, gets the same set.
- The prompt is uniform: `build_prompt` composes the same working rules for
  every tier (`src/torve/adapters/agent/harness.py`); a rule that only one
  kind of work needs ("docstrings follow the house voice") can be added for
  everyone or no one.
- A document cannot address a variant: `PhasingEntry`
  (`src/torve/config/rfc_parse.py`) has no `tier_variant`, so even though
  `Task.tier_variant` and dotted-tier resolution exist since RFC 0027, the
  only way to route a minted phase to a variant is hand-editing contracts
  after `torve plan`.

Meanwhile the measurement side is already finished: telemetry digests the
resolved tier mapping into `config_hash`, and RFC 0027 §5.1 built variants
precisely so "two tasks on different variants are two regimes, visibly".
Equipment is the last missing input; everything downstream of it exists.

## 3. Current state

- `SkillsConfig.sets: dict[str, list[str]]`, defaults per role, materialized
  at dispatch by `materialize(role, dest, sets, vendor_root)` — fail-closed
  on unknown names, refusing a name both shipped and vendored (D-9.12).
- `TierConfig` is flat with `extra="forbid"`; profiles (RFC 0028) merge
  raw-mapping first, local keys win, list fields replace wholesale (D-28.4).
- Variant resolution is `tiers["{seat}.{variant}"]`, loud refusal on unknown
  (D-27.2); `retry_variant` names a one-rung ladder (D-27.11).
- `build_prompt` points the agent at `.torve/skills/` and carries the
  charter working rules (base-sha pin, no corpus coordinates in user-facing
  strings) accreted from execution evidence.

## 4. Goals / Non-goals

**Goals**

- A variant can carry its own skill set and its own extra working rules.
- A document's phasing can name the variant a unit runs under.
- Equipment differences are visible in doctor and measured by the existing
  hash, with zero telemetry changes.

**Non-goals**

- **Engine-side assignment.** The engine never infers a variant from task
  content; classification-by-engine is the engine deciding what work looks
  like, one step from deciding what work exists (D-2). Authors and drafters
  assign, in documents a human reviews.
- **Multi-agent collaboration inside a task.** One task, one fence, one
  sandbox, one attempt remains load-bearing for scope, attribution, review
  and telemetry. Parallel collaboration is decomposition (RFC 0026);
  sequential collaboration is the review lane (RFC 0005) and the retry
  ladder (D-27.11). Nothing here adds a third kind.
- **Per-skill prompt rewriting.** `prompt_extras` appends lines; it does not
  template, reorder or remove the charter rules — those encode incidents,
  and a persona that can shed them is a persona that can re-cause them.
- **A persona registry beyond configuration.** A persona is a named,
  equipped tier entry — committed config or an operator profile. No new
  artefact type.

## 5. Design

### 5.1 Equipment on the tier

```yaml
tiers:
  executor:
    profile: claude-sonnet-xhigh
  executor.copywriter:
    profile: claude-sonnet-xhigh
    skills: ["prose-voice", "keep-a-changelog"]
    prompt_extras:
      - "Docstrings and user-facing text follow the repository's house voice."
```

`TierConfig` gains:

- `skills: list[str] | None = None` — `None` inherits the role-scoped set
  exactly as today; a list **overrides it entirely** (no additive merge —
  additive is how a set drifts from what anyone wrote down). Names resolve
  through the same `materialize` path with the same refusals: unknown name,
  shipped-and-vendored collision. Resolution failure is a dispatch-time
  refusal, before a sandbox exists.
- `prompt_extras: list[str] = []` — lines appended to the built prompt
  after the charter's working rules. Append-only by construction: the base
  rules are not addressable from configuration.

Both fields ride the profile merge unchanged (raw-mapping, local wins,
lists replace — D-28.4 already says wholesale replacement, and equipment is
why that mattered), and both enter `config_hash` through the tiers dump
with no telemetry change — verified against `parts["tiers"]` in
`src/torve/application/telemetry.py`.

### 5.2 Addressing a persona from a document

`PhasingEntry` gains `tier_variant: str = ""`; `torve plan` copies it onto
the minted contract's existing `tier_variant` field. Nothing else moves:
resolution, refusal-on-unknown and the shadow guard are RFC 0027's, already
live in the runner.

```yaml
- phase: 2
  title: user-guide
  intent: >-
    The configuration guide rewritten for operators.
  scope: ["docs/**"]
  acceptance: ["uv run torve rfc check"]
  tier_variant: copywriter
  depends_on: [1]
```

A drafter (intake, decompose) may likewise set `tier_variant` on a draft —
the contract lint already validates the field, and adoption is where a
human sees the assignment. That is the whole "smarter decomposition" story
this document takes on: the drafter is *allowed* to route, in an artefact a
human signs. Whether the drafter routes *well* is a measurement question
that cannot be asked before personas exist; §8 names the follow-up.

### 5.3 Doctor

One line per tier whose equipment differs from its role default:
`tier executor.copywriter: skills [prose-voice, keep-a-changelog] (override), +1 prompt extra`.
No check attached beyond what dispatch already refuses; doctor shows, the
refusals happen at the boundary that owns them.

### 5.4 Measurement, deliberately not built

Nothing new. An equipped variant is a distinct `config_hash` regime today;
RFC 0027's paired replay and displacement gate compare it against the
generalist; RFC 0022's envelope prints its cost history once it has one.
The doctrine line this document adds is only: **start with two personas,
not a cast** — each persona below the observation floor is a name with no
evidence, and a roster of them is taste wearing configuration.

### Alternatives considered

- **Additive skills (`extra_skills`) instead of override.** Rejected: the
  effective set becomes the union of a default defined elsewhere and a
  local addition — unreadable at review time. An override is the whole
  truth in one place; repetition of two names is cheap.
- **Equipment on the profile only (not the tier).** Rejected: profiles are
  host-level and optional; a repository must be able to define a persona in
  its own committed config for the regime to be reviewable in the repo's
  history. The merge handles both homes with one rule.
- **A `personas:` top-level block.** Rejected: it would duplicate the tier
  mapping's resolution, refusal and hash paths for a concept that is
  exactly "a tier entry with two more fields".

## 6. Tests

- `TierConfig` field family: `skills: None` inherits, `[]` equips nothing,
  unknown name refused at dispatch with the materializer's message;
  profile-merge property — a local `skills` list replaces a profile's
  wholesale.
- `build_prompt`: extras appear after the base working rules; base rules
  present regardless of extras.
- `parse_phasing`: `tier_variant` accepted, defaulted empty, copied by
  `torve plan` onto the contract (planner test).
- Hash property: two configs differing only in `prompt_extras` produce
  different `config_hash` — the test is the proof measurement needed no
  new code.

## 7. Docs

README configuration section: one persona example (the §5.1 block) and the
two-personas doctrine sentence. rfc-writer skill: Phasing reference gains
`tier_variant` as an optional key.

## 8. Out of scope

- **Roster-aware decomposition** — the drafter reading persona descriptions
  and routing children by fit. Named as the escape hatch, not built: it
  needs personas with accumulated evidence to route *toward*, and the
  drafter can already set `tier_variant` mechanically (§5.2). When two
  personas have displacement-grade history, an amendment teaches the
  drafter's rules to cite it.
- **Per-persona images or gate sets** — the image is the harness identity
  (D-17.4) and gates are the repository's, not the agent's; both stay
  where they are.
- **Skill authoring** — RFC 0009's pipeline unchanged; equipment selects,
  never defines.

## 9. Risks

- **Persona sprawl**: cheap to name, slow to measure. Mitigated by the
  §5.4 doctrine line and by the observation floor already suppressing
  envelope readings for thin regimes — a persona with no history visibly
  has none.
- **Equipment drift between similar personas**: two variants meant to be
  identical but for one skill can diverge silently over time. The doctor
  line (§5.3) makes the resolved equipment inspectable; the regime
  preimage (D-4.19) makes any historical difference diffable.
- **Prompt extras as a policy side-channel**: a rule that belongs in the
  charter's base set living in one persona's extras applies to one seat
  only. Accepted with the same answer skills already use: what execution
  evidence proves general gets promoted into `build_prompt`, extras are
  for genuinely local voice.

## 10. Unresolved questions

- D-29.6 (below): whether `review`-role skill sets should ever be
  overridable per variant — the reviewer's emptiness is deliberate
  (RFC 0005's reviewer reads, it does not build), and the first proposal
  to equip one should argue with that, not merely configure past it.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-29.1 | `LOCKED` | Equipment lives on the tier entry: `skills` fully overrides the role-scoped set when present, `prompt_extras` appends after the charter's base working rules; base rules are not addressable from configuration | `src/torve/config/runconfig.py` `src/torve/adapters/agent/harness.py` | A persona can specialise but never shed an incident-born rule; making base rules removable later reopens every incident that wrote one |
| D-29.2 | `LOCKED` | Equipped skill names resolve through the existing materializer with its refusals unchanged — unknown or shipped-and-vendored names refuse dispatch before a sandbox exists | `src/torve/application/skills.py` `src/torve/application/runner.py` | A skill that quietly stops applying makes telemetry lie (D-9.2); fail-closed is what lets an equipped regime be trusted |
| D-29.3 | `ASSUMED` | `skills` is a wholesale override, never additive, and rides the profile merge under D-28.4's replace-wholesale rule | `src/torve/config/runconfig.py` | The effective set is readable in one place at review time; an additive union is where equipment drifts from what anyone wrote down |
| D-29.4 | `ASSUMED` | A Phasing entry may carry `tier_variant`, copied verbatim by the planner onto the minted contract; drafters may set it on drafts, and adoption is where a human sees the assignment | `src/torve/config/rfc_parse.py` `src/torve/application/planner.py` | Routing lives in reviewed documents; the engine inferring a variant from task content stays refused (D-2) |
| D-29.5 | `ASSUMED` | Doctor prints resolved equipment per tier that differs from its role default and attaches no check | `src/torve/cli/doctor.py` | Dispatch already owns the refusals; a second, later copy of the same signal would be noise |
| D-29.6 | `OPEN` | Whether `review`-role sets are ever overridable per variant; the reviewer's empty set is deliberate (the reviewer reads, it does not build), and the first equipped-reviewer proposal must argue with that doctrine — evidence from the review lane's findings quality would settle it | `src/torve/config/runconfig.py` | Silence here would get filled by the first convenient configuration |

## 12. Phasing

```yaml
- phase: 1
  title: equipment-on-the-tier
  intent: >-
    TierConfig gains skills (None inherits the role set, a list overrides it wholesale) and prompt_extras (appended after the charter's base working rules, which stay unaddressable); the runner threads the override into materialize and build_prompt appends the extras (D-29.1–D-29.3). Tests pin inheritance, override, the fail-closed unknown-name refusal, the profile-merge replace property, extras-after- base ordering, and the config_hash separation of equipped regimes.
  scope:
    - "src/torve/config/runconfig.py"
    - "src/torve/application/runner.py"
    - "src/torve/adapters/agent/harness.py"
    - "tests/test_runconfig.py"
    - "tests/test_tiering.py"
  acceptance:
    - "uv run pytest tests/test_runconfig.py tests/test_tiering.py"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run ruff check ."
  depends_on: []
- phase: 2
  title: variant-in-the-phasing
  intent: >-
    PhasingEntry gains tier_variant, defaulted empty, copied verbatim by torve plan onto the minted contract (D-29.4); the doctor prints resolved equipment for tiers that differ from their role default (D-29.5). Tests pin parse, mint copy-through, and the doctor line.
  scope:
    - "src/torve/config/rfc_parse.py"
    - "src/torve/application/planner.py"
    - "src/torve/cli/doctor.py"
    - "tests/test_plan.py"
    - "tests/test_doctor.py"
  acceptance:
    - "uv run pytest tests/test_plan.py tests/test_doctor.py"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run ruff check ."
  depends_on: [1]
```
