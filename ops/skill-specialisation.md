# Skill specialisation guide

How the four skills Torve owns move into this repository, and how they are rewritten so they stop being copies.

Companion to `rfcs/AMENDMENTS.md` A-3.

---

## 1. The point of this, stated once

These are **not forks kept in sync**. They are specialisations that are *supposed* to diverge, and the guide exists so the divergence is deliberate from the first commit rather than discovered later as drift.

`agent-skills/rfc-writer` teaches writing a good RFC — for humans, for any repository, for any purpose. `torve/skills/rfc-writer` teaches writing an **executable input to `torve plan`**: a decision table with `paths:` on every row, phases that mint into tasks, `scope` per task, identifiers the divergence log refers back to. Same ancestry, different job.

The failure mode this guide prevents: a file that starts as a byte-identical copy, sits there for six months, and leaves everyone wondering whether it should be reconciled with upstream. **If the specialised version does not look visibly different at the first commit, the specialisation has not been done.**

---

## 2. What moves

| Skill | Torve parses | Specialisation is |
| --- | --- | --- |
| `rfc-writer` | decision tables, phasing (`RfcDirectory`, `rfc_index`) | substantial |
| `flag-dont-flip` | divergence logs (`decisions_reported`) | already done — see A-1, A-2 |
| `ratchet-what-you-build` | sabotage sets (`sabotage`) | moderate |

`escape-hatch-policy` was previously on this list and has been removed — see §5.4.

Nothing else moves. The test is narrow on purpose: **does Torve parse what this skill produces?** "Check that you actually ran the tests" does not qualify — Torve reads an exit code, not the skill. Widen the test and fifteen skills end up here.

---

## 3. Layout and distribution

```
torve/
  src/torve/
    gates/
      scope.py
      decisions_reported.py
      no_test_tampering.py
      secrets.py
      sabotage.py
  skills/                            # package data, shipped with the wheel
    rfc-writer/
      SKILL.md
      references/{anatomy,writing-style,workflows}.md
      scripts/rfc_index.py           # authoring tool, not a gate — see below
    flag-dont-flip/SKILL.md          # no scripts/ — see A-2
    ratchet-what-you-build/SKILL.md
```

**Why `rfc_index.py` stays in the skill while `log_check.py` moved.** `log_check.py` gates agent output in CI, so it is a gate. `rfc_index.py` is run by an author while writing, so it belongs with the authoring skill. The line is *who runs it and when*, not what language it is in.

### There is no install command

An earlier draft specified `torve skills install` writing into `.torve/skills/`. That was wrong twice over: it reinvents a distribution mechanism the ecosystem already has, and — worse — it creates a checked-in copy that can drift from the package, which is the exact failure A-3 exists to prevent.

Two paths, and only one of them involves installation at all.

**Engine path — the runner composes the context.** During a run, the agent does not "have skills installed". The runner assembles the sandbox and writes the role-scoped skill set into it, resolved from package data at dispatch time. Nothing is checked into the consuming repository, nothing can drift, and the skill version is the Torve version by construction — so skill and gate stay one unit of versioning without any check to enforce it. A-3 is satisfied structurally rather than by policy.

**Human path — use whatever the ecosystem uses.** People also write RFCs and review diffs interactively, outside the engine. For that, `npx skills add`, `gh`, a submodule or a plain copy — whatever is already normal. Do not build a bespoke installer for it; the only thing worth stating is which Torve version the copy came from.

`torve skills check` therefore disappears for the engine path (no copy exists to diverge) and reduces, for the human path, to reporting the version the package ships.

**One consequence worth keeping from the discarded design:** if a general-purpose `rfc-writer` from `agent-skills` sits in a human's environment beside the specialised one, both trigger on the same task and the agent gets two conflicting instruction sets. Inside the sandbox this cannot happen — the runner writes one set. Outside it, it is on the human, and the specialisation header (§4) is what makes the difference visible.

## 4. Mandatory header

Every specialised skill carries this, immediately after the frontmatter:

```markdown
> **Specialisation.** Derived from `agent-skills/<name>`, specialised for
> artefacts that Torve parses. Divergence from upstream is expected and
> intentional — **do not reconcile**. Improvements of general value flow
> upstream, not the reverse.
```

One direction, deliberately: from specialisation to general. `log_check.py`'s evidence-locatability check and the hardened `rfc_index.py` content checks are the sort of thing worth offering upstream. Nothing flows back down.

---

## 5. Per-skill specialisation

### 5.1 `rfc-writer` — the substantial one

Apply the global rules from SKILLS-REFACTOR (description ≤ 300 chars, `roles:`, `gate:`, drop the `Use this skill when` / `Do not use` sections, body ≤ 1,500 tokens).

**Cut from the body** — move to `references/`: RFC anatomy, writing style, workflows. Directory and index conventions, numbering, filenames and statuses become a single line pointing at `rfc_index.py`, since the script enforces them.

**Add — this is what makes it a specialisation, not a trim:**

1. **`paths:` on every decision row.** A decision that governs an area must declare it, or the silence check in `decisions_reported` skips that decision and the strongest anti-drift guarantee quietly does nothing. This is the single most important addition.

   ```markdown
   | # | Decision | Grade | Paths | Consequence |
   | --- | --- | --- | --- | --- |
   | D-3 | Sessions in Redis, not the database | `LOCKED` | `packages/api/session/**` | … |
   ```

2. **Phasing must be mintable.** A phase is not prose about sequence; it is a list of units, each with a name, its dependencies, and the file boundaries it will touch. If `torve plan` cannot derive a task from a phase entry without a human rewriting it, the phase was written wrong.

3. **Non-overlapping scope within a phase.** Tasks in one phase must not share `allow` globs — overlapping tasks cannot run in parallel and the plan silently serialises. Say this while writing, when it is free to fix.

4. **Decision identifiers are permanent.** Divergence logs refer to `D-3` by identifier forever. Renumbering an accepted RFC orphans every log entry that cites it. Append new rows; never renumber.

5. **Amendments, not edits.** An accepted RFC is amended in an `## Amendments` section, never rewritten in place — the divergence logs and telemetry that reference it assume the text they cited still exists.

**Harden `rfc_index.py`** with content checks it does not yet do, per SKILLS-REFACTOR §3.5: decision table present; every row graded; `paths:` present on every `LOCKED` row; identifiers unique and never reused; cited paths exist; `Related` links resolve; phasing entries parse into tasks.

That is what turns the format from convention into enforcement — which the sibling skill `ratchet-what-you-build` demands and this one currently fails.

### 5.2 `flag-dont-flip`

Already specialised. Confirm three things on arrival:

- No `scripts/` directory (A-2 — the gate lives in `src/torve/gates/`).
- `gate: decisions-reported` in the frontmatter.
- The format section shows a YAML log file with an `entries:` list, not fenced markdown blocks (A-1).

### 5.3 `ratchet-what-you-build`

**Add:** `gate: gates-sabotage`. The sabotage suite is the mechanism that proves this skill's own rule, and it should point at it.

**Specialise:** the general version asks "did you build the gate that keeps this from regressing?" The Torve version asks the same and adds the concrete obligation: **a new gate ships with a sabotage case, or it does not ship.** Name the directory (`gates/sabotage/`), the requirement (one deliberately bad diff per gate, asserted red in CI), and the reason — a gate that has never been observed to fail is indistinguishable from a gate that never fires because the code is clean.

**Cut:** the general half-shipped taxonomy — `references/`.

### 5.4 `escape-hatch-policy` — does not move, and this was a category error

Listing it here confused two different things that share a word.

The existing skill is about **API and abstraction design**: there is always a lower layer, expose it deliberately rather than trapping callers, a hatch being used is a signal that the abstraction is wrong. That is general design practice for implementers, it applies far outside Torve, and it belongs in `agent-skills` with `roles: [implement, author]`.

RFC 0002 §6a's **gate bypass** is a different concept: a human signing off on skipping a check. The actor is a human, never an agent (an agent may never sign), so no skill is involved at all — humans read RFCs, not skills. What that policy needs is the record shape and the `bypass-count` gate, both of which live in the package already.

**Net:** three skills move, not four. `escape-hatch-policy` stays upstream unchanged, and the bypass policy stays where it was written.

**Worth generalising from this:** before moving a skill, check that the Torve concept it maps to is genuinely the same concept and not a homonym. The test in §2 — *does Torve parse what this skill produces?* — answers no here: Torve parses bypass records, and bypass records are not produced by this skill.

## 6. Status

Executed 2026-08-21 (task T-0005): the three skills ship specialised in this
directory, `rfc_index.py` is hardened (Paths column, paths on every `LOCKED`
row, unique identifiers) and gates the corpus in CI, and the runner
materializes the role-scoped set into the sandbox at dispatch
(`src/torve/skills.py`). The §7-style afterward checks live as tests —
`tests/test_skills.py` asserts the specialisation headers, the gate lines,
non-identity with upstream, the redden-on-broken rfc_index cases, and the
role-set materialization; `tests/test_manifest.py` asserts `config_hash`
moves with the package version.

What remains normative here for the future: the ownership test in §2 (does
Torve parse what the skill produces?), the no-install rule in §3, the
mandatory header in §4, and the homonym lesson in §5.4.
