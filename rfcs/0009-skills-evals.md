---
id: "0009"
title: Skills and evals
status: accepted
implementation: partial
depends_on: ["0004"]
informed_by: []
supersedes: []
superseded_by: null
amended_by: ["A-3", "A-25"]
owner: Lev Litvinov
description: >-
  Skill routing per role, versioned distribution, trigger collision, and the eval loop that retires skills that do not earn their tokens.
schema_version: 1
---

# RFC 0009 — Skills and evals

- **Scope:** How agent skills are selected, versioned, distributed across repositories, and measured. Covers trigger collision, per-role skill sets, and the eval loop that decides whether a skill earns its tokens. Excludes writing individual skills.
- **Inherits:** D-3 (skills are convention, gates are enforcement), D-25 from RFC 0001
- **Related:** `agent-skills` · `skill-creator` · `distill-the-rule` · `ratchet-what-you-build`

---

## 1. Why this needs a document

Skills are load-bearing: they are how an agent passes a gate on the first attempt instead of the third, and `config_hash` already treats the skill set as part of the regime a measurement belongs to. Yet nothing in this corpus says which skills load for which role, what happens when several fire at once, or how one is retired.

That is half the quality story living outside the specification.

**The framing that keeps it honest:** a skill is probabilistic and a gate is not. A skill never provides a guarantee, however well written. Its job is to reduce iterations, and iterations are measurable — so a skill's value is an empirical claim, not a matter of taste.

## 2. Three clusters, very different returns

The existing library divides cleanly, and the divisions have different economics.

| Cluster | Examples | Verdict |
| --- | --- | --- |
| **Mechanical format** | changelog conventions, commit format, docstring style | Candidates for linters and hooks. A full lookup table burned into context on every run is a script's job. Low uplift, constant cost. |
| **Taste and refactoring** | naming, nesting, composition, premature optimisation | Widely-known practice that frontier models largely have. Marginal uplift plausibly near zero; the cluster most likely to fail an eval. |
| **Verification discipline** | reproduce-then-fix, reading-isn't-proof, self-audit, failure-path-review, ratchet-what-you-build | **The valuable one.** These encode behaviours models do not exhibit by default — not fixing before reproducing, claiming done without running. High uplift because the fight is against default behaviour, not ignorance. |

Consequence: cluster C is effectively a verification harness in prose, and it maps directly onto the gates in RFC 0002. Grow it; put A behind hooks; make B prove itself.

## 3. Trigger collision is the real cost

Two dozen skills with broad triggers means several fire on one diff — naming, self-documenting-code and nesting all match any code review. That multiplies context and can produce contradictory instructions, and neither effect is visible without measurement.

Two mitigations, in order of preference:

1. **Role-scoped sets.** A skill declares which roles it applies to; an implement run never loads review skills, and vice versa. Cheap, and removes most collisions outright.
2. **Umbrella skills with on-demand references.** One `code-review` entry point that decides which `references/` to pull in, rather than N sibling skills each competing to trigger.

```yaml
skills:
  path: .torve/skills
  sets:
    implement: [reproduce-then-fix, reading-isnt-proof, flag-dont-flip, self-audit]
    review:    [failure-path-review, fewer-tests-more-proof]
    revert:    [reproduce-then-fix]
  max_in_prompt: 6
```

`max_in_prompt` is a hard ceiling, not a target. When more match, the set is truncated by declared priority and **the truncation is recorded on the attempt** — otherwise a skill silently stops applying and the telemetry lies.

## 4. Distribution

Same problem as gates, same answer: skills ship as a versioned package, not copied directories. A lockfile pins the set per repository, and `config_hash` incorporates it.

The engine reads skills but does not interpret them — they are passed to the agent adapter, which places them where its harness expects. Portability matters here: the `SKILL.md` convention is honoured by several harnesses, which is what keeps the library from being locked to one vendor.

## 4a. Vendored skills

*Added by amendment A-25 2026-08-22.*

The package ships only the skills Torve parses (A-3), which leaves the rest of a team's library — review checklists, stack conventions, everything in `agent-skills` — with no road into the sandbox. The road is the same one the sandbox definitions took (RFC 0017 §2): **committed, reviewed artefacts in the repository**.

A repository vendors skills under `.torve/skills-vendor/<name>/`, each a directory with a `SKILL.md`. Role sets in the runner configuration name them exactly as they name shipped skills, and `materialize` resolves each name against package data and the vendor directory together; a name unknown to both stays a configuration error, exit 3.

Three lines hold the shape:

- **A name present in both places is a configuration error, refused** — never shadowed in either direction. This structurally enforces the A-3 boundary: a vendored variant of `flag-dont-flip` would drift against the gate that parses its output, which is the exact failure A-3 exists to prevent.
- **The vendored tree's content digest joins `config_hash`.** An edited vendored skill is a regime change and the records say so — the image-digest doctrine (RFC 0017, D-17.1) applied to prompt-side inputs.
- **This is the task-context channel** (RFC 0017 §3): vendored skills are reviewed repository content instructing the agent about the *work*, which is allowed; what stays forbidden is the repository configuring the *harness* that works on it (D-17.4). Skills materialize per role at dispatch exactly as shipped skills do — a review role gets its review set, an implementation role its own.

Vendoring is a copy, pinned by review: updating from an upstream library like `agent-skills` is a diff someone reads, not a submodule that moves under the run. If a lockfile-driven sync ever earns its keep, it arrives as tooling around this layout, not as a second channel.

## 5. Evals

The question "is this skill worth its tokens" has a machine answer, and the tooling to produce it already exists in the skill repository.

Per skill:

- 5–10 prompts that **should** trigger it, 3–5 that should not — trigger precision and recall.
- With-skill versus without-skill runs on the same tasks: iterations-to-green, gate failures on first attempt, tokens consumed.
- A retirement threshold: a skill whose without-skill baseline matches its with-skill result is deleted, not kept for comfort.

Two skills' worth of guidance applies to this process itself. `ratchet-what-you-build`: an eval that cannot fail proves nothing, so keep a deliberately mis-triggering prompt in the suite. `distill-the-rule`: when a leak recurs, the choice is a new gate or a new skill — prefer the gate, because a gate is enforcement and a skill is a hope.

**Run the taste cluster first.** It is the largest, the least likely to survive measurement, and deleting it is the cheapest quality improvement available.

## 6. Skills and gates are the same rule, twice

Every rule that matters should exist in both forms, with a fixed division of labour:

> **The gate is the source of truth. The skill is how the gate is passed on the first attempt.**

A skill without a gate is a convention. A gate without a skill is a gate that fails a lot and costs iterations. Both, or the rule is not really enforced.

This also gives a retirement path in the other direction: once a gate exists and its first-attempt pass rate is high, the corresponding skill may be shrinkable to a sentence.

## 7. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-9.1 | `LOCKED` | Skills are role-scoped; a run loads only its role's set | `src/torve/application/skills.py` | The cheapest fix for trigger collision |
| D-9.2 | `LOCKED` | `max_in_prompt` is enforced and truncation is recorded on the attempt | `src/torve/application/skills.py` | A silently dropped skill makes telemetry wrong |
| D-9.3 | `LOCKED` | Skills ship as a versioned package with a per-repository lockfile, included in `config_hash` | `src/torve/application/telemetry.py` | Otherwise measurements are not comparable across repositories |
| D-9.4 | `ASSUMED` | A skill that does not beat its without-skill baseline is deleted | — | Applies first to the taste cluster |
| D-9.5 | `LOCKED` | Where a rule can be either, prefer the gate and keep the skill only to reduce iterations. *(Amended by A-8 2026-08-21: retiring a gate shrinks or removes its paired skill in the same change — otherwise an instruction survives for a rule that no longer exists.)* | `src/torve/gates/**` `skills/**` | Enforcement over hope |
| D-9.6 | `ASSUMED` | Mechanical-format skills migrate to hooks and linters | — | Depart where a hook cannot express the rule |
| D-9.7 | `LOCKED` | A skill whose format Torve parses ships with Torve, specialised: skill and gate are one unit of versioning. Three qualify — `flag-dont-flip`, `rfc-writer`, `ratchet-what-you-build` — and the boundary stays narrow (the test: does Torve parse what the skill produces?). On the engine path the runner writes the role-scoped set into the sandbox from package data at dispatch; nothing is installed into repositories. Added by amendment A-3 2026-08-21 | `skills/**` `src/torve/application/skills.py` | Versioned apart, the gate tightens and the skill does not know |
| D-9.8 | `LOCKED` | `config_hash` includes the Torve package version (A-3) and the pinned forze version (A-6), alongside the gate manifest and the agent-skills lockfile — supersedes the D-9.3 composition and the T-0002 decision to record the toolchain beside the hash. Added by amendments A-3/A-6 2026-08-21 | `src/torve/application/telemetry.py` | Upgrading either silently changed the regime and telemetry did not notice |
| D-9.9 | `ASSUMED` | The eval set includes the underspecification regression: a contract with three unsettled load-bearing decisions must halt with a `blocked` entry, never an implementation — the case most likely to regress when the skill is trimmed for length. Added by execution 2026-08-22 — see .torve/tasks/T-0018 | `skills/flag-dont-flip/SKILL.md` | — |
| D-9.10 | `ASSUMED` | Shipped skills teach the canonical artefact layout in lockstep with the engine; a skill teaching a retired path teaches agents to write files nothing reads. Added by execution 2026-08-22 — see .torve/tasks/T-0018 | `skills/**` | — |
| D-9.11 | `ASSUMED` | Vendored skills live under `.torve/skills-vendor/<name>/`, committed and reviewed; role sets resolve names against package data and the vendor directory together, and a name unknown to both is a configuration error. Added by amendment A-25 2026-08-22 | `src/torve/application/skills.py` `.torve/skills-vendor/**` | The team's library reaches the sandbox as reviewed content, not ambient copies |
| D-9.12 | `LOCKED` | A skill name present in both package data and the vendor directory is a configuration error, refused — never shadowed in either direction. Added by amendment A-25 2026-08-22 | `src/torve/application/skills.py` | A vendored variant of a parsed-format skill drifts against its gate — the A-3 failure reintroduced |
| D-9.13 | `LOCKED` | The vendored tree's content digest joins `config_hash`; an edited vendored skill is a visible regime change. Added by amendment A-25 2026-08-22 | `src/torve/application/telemetry.py` | The image-digest doctrine applied to prompt-side inputs |

## 8. Exit criteria

- Role-scoped sets configured and truncation events visible in telemetry.
- Evals run for the whole library, with at least one skill retired on evidence.
- Every cluster-C skill paired with a gate, or an explicit written decision that this class is caught by humans.

## Amendments

### A-3 — 2026-08-21 — skills whose format Torve parses ship with Torve (adds D-9.7)

**Found in implementation.** A skill and its gate encode one rule in two forms. Versioned separately, they drift: the gate tightens in the package, the skill in another repository does not know, and agents write to the old rule and redden on every task.

**Changed:** a skill and its gate are **one unit of versioning**. Skills whose output Torve parses ship with the package: `flag-dont-flip` (parsed by `decisions_reported`), `rfc-writer` (parsed by `RfcDirectory` and `rfc_index`), `ratchet-what-you-build` (parsed by `sabotage`). Everything else stays in `agent-skills`. `escape-hatch-policy` was on the list and removed on review — same word, different concept; three skills move, not four.

**The boundary is narrow and should stay narrow.** The test: *does Torve parse what this skill produces?* Without that line, most of the verification cluster gets pulled in and Torve ends up owning fifteen skills instead of three.

**Distribution: none required on the engine path.** The runner writes the role-scoped skill set into the sandbox from package data at dispatch time; nothing is checked into the consuming repository, so nothing can drift, and the skill version is the Torve version by construction. No bespoke installer.

**Consequence for D-9.3:** `config_hash` includes the Torve package version, not only the `agent-skills` lockfile — otherwise upgrading Torve silently changes the regime and telemetry does not notice.

### A-25 — 2026-08-22 — vendored skills reach the sandbox as reviewed content (adds §4a, D-9.11–D-9.13)

**Found asking how the rest of the library gets in.** A-3 drew the boundary — Torve ships only what it parses — but left everything on the far side of it (review checklists, stack conventions, the `agent-skills` library) with no road into the sandbox.

**Changed:** §4a — the sandbox-definition doctrine applied to skills. Vendored skills are committed under `.torve/skills-vendor/<name>/` and resolve by name beside shipped skills at materialization; a collision with a shipped skill is refused, structurally protecting the A-3 unit-of-versioning; the vendored tree's digest joins `config_hash`. The channel is task context (RFC 0017 §3) — repository content instructing the agent about the work, which is allowed, as distinct from the repository configuring the harness, which is not (D-17.4).
