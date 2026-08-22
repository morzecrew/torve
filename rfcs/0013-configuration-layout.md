---
id: "0013"
title: Configuration layout
status: accepted
implementation: complete
depends_on: ["0016"]
informed_by: ["0002", "0011"]
supersedes: []
superseded_by: null
amended_by: ["A-16"]
owner: Lev Litvinov
description: >-
  Where Torve's files live in a consuming repository: the .torve/ directory,
  the gates/config split, resolution rules, and what belongs in neither file.
schema_version: 1
---

# RFC 0013 — Configuration layout

Where Torve's files live in a repository, and why they are split the way they are.

Not urgent — the split already works. This records the reasoning and the one decision worth making before the file set grows.

## 1. Layout

```
.torve/
  config.yaml        runner: adapters, tiers, runtime, budgets, promotion, rfcs.path (A-16)
  gates.yaml         gate manifest
  tasks/             T-0142/contract.yaml + T-0142/log.yaml   (A-12)
  skills/            human-path copies only; the runner does not read these
```

*Note 2026-08-22 — charter A-12 replaced the parallel `tasks/` and `logs/` trees with one directory per task (`tasks/T-0142/` holding `contract.yaml` and, when anything was written, `log.yaml`). The move itself is pending execution; the flat layout resolves as legacy fallback meanwhile.*

Repository root stays clean. In a consuming project these files sit beside a dozen other tools' dotfiles, and the root is scarce real estate — a tool that claims two top-level names for itself is a tool being presumptuous.

**Migrating from root-level `torve.yaml` and `gates.yaml` costs one move per repository plus a fallback.** Do it before the file set grows past two, not after.

## 2. Why two files and not one

The instinct is that one file is tidier. The split is not about tidiness — the two have **different consumers and different lifecycles.**

| | `gates.yaml` | `config.yaml` |
| --- | --- | --- |
| Read by | `torve gates run` in CI | the runner |
| Requires | nothing — no store, no sandbox, no agent | Postgres, sandbox runtime, agent credentials |
| Lives in | **every** consuming repository | only where the engine actually runs |
| Changes | often, as gates accumulate from observed leaks | rarely |

Merging them would mean a repository that only wants gates has to carry engine configuration it cannot use — which breaks the property RFC 0002 exists to deliver, that the first increment is useful standing alone.

The lifecycle difference matters too: gate manifests are edited constantly in the first months while the set accumulates, and runner configuration is not. One file mixes two kinds of diff in one history.

## 3. Resolution rules

Decide these before the first multi-repository run, because that is when the questions arrive.

- **`gates.yaml` is always local to the repository being checked.** Never inherited, never merged from a parent. The gates that apply are the ones that repository declares.
- **`config.yaml` is read from where the runner was launched**, not from the repository under work. A repository being operated on does not get to configure the engine operating on it — that would let a task's own repository widen its budgets or change its agent tier.
- **No merging of layers.** No `~/.torve/config.yaml` overlaid with a project one overlaid with environment variables. Layered configuration makes "why did this run behave that way" unanswerable, and reproducing an attempt six months later is a stated requirement (A-4).
- **Overrides are explicit flags**, not implicit files: `--config PATH`, `--gates PATH`.

The second rule is the security-relevant one and is worth stating in the file itself, not just here.

## 4. Shared conventions

Both files:

- Carry `schema_version` at the top, like every other artefact in the corpus.
- Are validated by a Pydantic model at load. A malformed manifest **exits 3** per the CLI contract — configuration error, not infrastructure failure, not a traceback.
- Fail on unknown keys rather than ignoring them. A typo in a gate name should be an error, not a silently skipped check.

That last one is worth being strict about: a gate manifest with `sope:` instead of `scope:` that loads successfully is a repository running with one fewer check than anyone believes.

## 5. What does not belong in either

- **Task contracts** — `.torve/tasks/`, one file each. If scope globs or acceptance commands start appearing in `gates.yaml`, that is contract data leaking into the manifest.
- **Secrets** — never. Credentials reach the runtime through vault injection (D-4b); a token in `config.yaml` is a token in git.
- **Anything the engine generates.** Attempts, gate results, findings and telemetry go to the store (A-4). The only generated artefact in `.torve/` is the divergence log, which is there because it arrives in a pull request as evidence.

## 6. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-13.1 | `ASSUMED` | All Torve files live under `.torve/`; nothing at repository root | `src/torve/config/layout.py` `.torve/**` | One move per repository; do it before the set grows |
| D-13.2 | `LOCKED` | `gates.yaml` and `config.yaml` stay separate | `src/torve/config/manifest.py` `src/torve/config/runconfig.py` | Merging forces gate-only repositories to carry engine configuration and breaks RFC 0002's standalone property |
| D-13.3 | `LOCKED` | `config.yaml` is read from the runner's location, never from the repository under work | `src/torve/config/runconfig.py` | Otherwise a worked-on repository can configure the engine working on it |
| D-13.4 | `LOCKED` | No layered configuration; overrides are explicit flags | `src/torve/config/runconfig.py` `src/torve/cli/**` | Layering makes an attempt unreproducible |
| D-13.5 | `LOCKED` | Unknown keys are an error, not ignored | `src/torve/config/manifest.py` `src/torve/config/runconfig.py` | A typo must not silently remove a check |
| D-13.6 | `ASSUMED` | Both files validated by Pydantic, exit 3 on malformed input | `src/torve/config/manifest.py` `src/torve/config/runconfig.py` | Matches the CLI contract's configuration-error code |
| D-13.7 | `LOCKED` | `rfcs.path` is a single path with a default of `rfcs/`; never a list or a glob. Added by amendment A-16 2026-08-22 | `src/torve/config/runconfig.py` | Two roots mean two counters and a colliding identifier at the first merge |
| D-13.8 | `ASSUMED` | `.gitignore` covers only what the engine generates (telemetry, materialized skills, tmp); reviewed artefacts under `.torve/` stay tracked. Added by execution 2026-08-22 — see .torve/tasks/T-0012 | `.gitignore` | — |
| D-13.9 | `ASSUMED` | D-13.1 governs the files a consuming repository carries for Torve; the product's own source directories (`skills/` as wheel data) are outside its scope. Added by execution 2026-08-22 — see .torve/tasks/T-0012 | `src/torve/config/layout.py` | — |
| D-13.10 | `ASSUMED` | `--gates` is the documented override; `--manifest` stays a compatibility alias. Added by execution 2026-08-22 — see .torve/tasks/T-0012 | `src/torve/cli/gates.py` | — |

## Amendments

### A-16 — 2026-08-22 — corpus path (amends §1, §3)

**Found in use.** `config.yaml` had no setting for where the specification corpus lives, so `rfcs/` was effectively hard-coded. A repository keeping specifications for `torve plan` may reasonably put them elsewhere. *(The source patch numbered this A-14; that was taken, so it lands as A-16 per charter D-A.5. The patch's citation for the resolution rule named a `D-F.*` decision, a family that does not exist in this corpus — the rule here is D-13.3.)*

**Changed:** `config.yaml` gains

```yaml
rfcs:
  path: rfcs        # default; usually absent
```

**One path only** — not a list, not a glob, not several roots. Numbering is continuous across a corpus, so two roots mean two independent counters and a colliding identifier at the first merge. Specifications that genuinely need two locations are two corpora with two configurations.

**Unchanged:** everything else about resolution. The setting is read from the runner's `config.yaml` per D-13.3, not from the repository under work — a repository being operated on does not get to relocate the corpus the engine reads.

**Also edits:** 0001 (A-15).
