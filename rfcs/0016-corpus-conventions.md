---
id: "0016"
title: Specification corpus conventions
kind: convention
status: accepted
implementation: partial
depends_on: []
informed_by: []
supersedes: []
superseded_by: null
amended_by: ["A-7", "A-9", "A-10", "A-14", "A-15", "A-20"]
owner: Lev Litvinov
description: >-
  How a specification corpus is organised, numbered, versioned and validated; applies to a repository with no engine in it. Extracted from the charter with identifiers preserved.
schema_version: 1
---

# RFC 0016 — Specification corpus conventions

- **Scope:** How a specification corpus is organised, numbered, versioned and validated: the three destinations for a document, frontmatter as the structured layer, decision tables, amendments, the generated index, and what may live in the corpus directory. Excludes anything about Torve itself — these rules apply to a repository with no engine in it.
- **Related:** RFC 0007 (`torve plan` consumes what these rules produce) · RFC 0013 (`rfcs.path`) · `rfc-writer`

---

## 1. Why this is separate from the charter

These rules were written into RFC 0001 because there was nowhere else to put them, and the consequences showed up in the numbers: **half the charter's amendments and forty-five per cent of its decisions were about document conventions rather than about Torve.**

The mismatch is categorical, not merely one of volume. **These rules apply to a repository containing no engine at all.** They govern how a corpus of specifications is kept; Torve happens to consume one. Keeping them in the engine's charter meant every clarification about numbering or indexing amended the document that defines what Torve *is*.

They also had no prose. They existed only as decision rows and amendment entries — which is why they were hard to follow and kept needing amendment. §2–§7 below are that missing prose; the decisions themselves are unchanged and carry their original identifiers.

**Identifiers are preserved exactly.** `D-A.7` is still `D-A.7`, now resolving here. Renumbering would break citations in RFCs 0007, 0011, 0013 and 0015 and violate D-A.4, and the `D-A.` prefix was never tied to a document number.

Four `D-A.*` decisions did **not** move — D-A.7, D-A.13, D-A.14 and D-A.15 are about task directories, logs and retention, which are engine artefacts. They stay in the charter. The prefix marks when a decision was written, not where it belongs; the split is by subject.

## 2. Three destinations

| Kind | Where | Read by |
| --- | --- | --- |
| Decisions later work inherits | `rfcs/` | agents and authors; machine-validated |
| Published documentation | `pages/` | users; versioned with releases, written independently |
| One-off procedures | `ops/` | us, until executed |

**The test:** does it settle decisions that later work must inherit? RFC. Does a user need it? `pages/`. Is it done once and then not needed? `ops/`.

If an `ops/` document starts accumulating decisions rather than steps, promote the **decisions** into an existing RFC — not the procedure.

**`pages/` is consistent with the corpus, not derived from it.** Documentation is written independently and in its own voice: a page answers "how do I use this", not "why was this decided". It may not contradict an accepted decision, and it does not restate rationale that lives under a number. The two also move on different axes — RFCs accumulate and carry amendments; a page describes one released version and carries no history. Supporting two releases means two versions of the site, not one page with a history section.

## 3. Frontmatter

Structured facts go in YAML frontmatter; prose goes in the body.

```yaml
---
id: "0016"
title: Specification corpus conventions
kind: convention          # convention | design
status: draft             # draft | accepted | superseded
implementation: none      # none | partial | complete | abandoned
depends_on: []
informed_by: []
supersedes: []
superseded_by: null
amended_by: []
retired: []               # identifiers once defined here — removed, never reusable (A-20)
owner: <name>
schema_version: 1
---
```

`status` describes the document; `implementation` describes the work, and both are judgements. **There is no progress field.** Execution progress is derived from task state, belongs to the store, and a frontmatter copy would diverge the first time a task escalated.

**What the edges mean.** `depends_on` constrains *planning readiness*, not task ordering: a document cannot be planned until its dependencies are `accepted`, because its decision table inherits their rows and grades are copied at mint time. Shipping order lives in a phasing table, not in the graph. `informed_by` constrains nothing — it tells a reader what to read first.

## 4. Decision tables

Decisions stay in markdown, not in frontmatter. A decision row carries prose — the statement and the consequence — which is what people argue about in review and belongs where reading happens. Splitting rows from rationale would create two sources of truth inside one document.

The table parses deterministically because its columns are fixed; what it needs is hard validation rather than another serialization. Identifiers are permanent: divergence logs cite `D-3` forever, so rows are appended and never renumbered.

## 5. Amendment discipline

An accepted RFC is never rewritten in place — divergence logs and telemetry reference text that must still exist. A change to an accepted decision is recorded as an amendment in the `## Amendments` section of the document whose decision it changes (D-A.5), listing secondary edits inside the entry. Numbering is global (`A-1`, `A-2`, …) so an amendment can be cited unambiguously from a log or a commit trailer. Every amendment follows the process this corpus specifies: implementation disagreed with a decision, stopped, and returned to a human — `flag-dont-flip` applied to Torve itself.

This document's own amendments follow, in the unnumbered `## Amendments` container every document keeps.

## 6. The index

`INDEX.md` is generated from frontmatter, never hand-edited, and CI-checked by regenerating and comparing — the same discipline as a lockfile.

It carries **every frontmatter field that aids routing and nothing derived from the store**. Frontmatter sits in the same commit the index is checked against, so `--check` stays deterministic; a store-derived column would make it flake on every task run.

Rows are grouped by `kind`, with documents that are `accepted` but `abandoned` separated out — that pairing is the most hazardous combination in a corpus, since the decisions are still inherited and there is no implementation.

## 7. The corpus directory

One path, configurable, defaulting to `rfcs/`. Never a list, never a glob: numbering is continuous, and two roots mean two counters and a colliding identifier at the first merge.

Only `NNNN-slug.md` and `INDEX.md`, no subdirectories. The check's message routes an offending file to `pages/` or `ops/` rather than only refusing it — without routing, the file lands in the repository root and the mess has moved rather than gone.

**The next number is derived** as the maximum plus one. A counter file is state, two branches diverge it, and resolving that conflict gives two documents one number. A parallel-creation race instead surfaces as a duplicate-`id` failure at merge, which is loud rather than silent.

**Documents are never deleted.** They leave service through `superseded` or `implementation: abandoned`. Amendments, divergence logs and commit trailers cite identifiers, and a reused number silently redirects all of them. Gaps are acceptable; reuse is not.

## 8. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-A.1 | `LOCKED` | A document with a graded decision table is an RFC and gets a number; published documentation goes to `pages/`, one-off procedures to `ops/`. Added by amendment A-7 2026-08-21 | `rfcs/**` `ops/**` `pages/**` | The sorting rule; without it `rfcs/` mixes kinds again |
| D-A.1a | `LOCKED` | A page must not contradict an accepted decision, and must not restate rationale that belongs under a number. Documentation is written independently, not generated from `rfcs/`. *(Reworded 2026-08-21 — see the note under A-7.)* | `pages/**` | Derivation produces pages that answer "why was this decided" to a reader asking "how do I use this" |
| D-A.1b | `ASSUMED` | An `ops/` document is deleted once executed | `ops/**` | A finished procedure kept "for reference" is how the mess restarts |
| D-A.1c | `LOCKED` | Documentation is versioned with releases; `rfcs/` is not. Neither is generated from the other | `pages/**` `rfcs/**` | Two different axes; synchronising them produces a site with an amendment history and a corpus with release branches |
| D-A.2 | `LOCKED` | Structured facts in YAML frontmatter; prose in the body | `rfcs/**` | Status and dependencies must be queryable and checkable |
| D-A.3 | `LOCKED` | Decision tables stay in markdown, hard-validated by `rfc_index.py` | `rfcs/**` `src/torve/config/rfc_parse.py` | Frontmatter would split rows from rationale — two sources of truth in one document |
| D-A.4 | `LOCKED` | Decision identifiers are permanent; append, never renumber | `rfcs/**` | Divergence logs cite them forever |
| D-A.5 | `LOCKED` | Amendments live in an `## Amendments` section of their primary target; numbering is global | `rfcs/**` | An amendment must be visible where the decision is read |
| D-A.6 | `LOCKED` | `INDEX.md` is generated and CI-checked, never hand-edited | `rfcs/INDEX.md` `src/torve/config/rfc_parse.py` | A hand-maintained index drifts, as this repository already showed |
| D-A.8 | `ASSUMED` | Keep the term "RFC" | — | Revisit only if `spec` ever justifies the churn |
| D-A.9 | `LOCKED` | `depends_on` constrains planning readiness; shipping order lives in the phasing table. Added by amendment A-10 2026-08-22 | `rfcs/**` | Conflating the two makes the graph a scheduler, which it is not |
| D-A.10 | `LOCKED` | No document inherits decisions from one that is not `accepted`. Added by amendment A-10 2026-08-22 | `rfcs/**` | A grade copied from a draft is a grade that may change under an executor |
| D-A.11 | `LOCKED` | Frontmatter carries `implementation` as a judgement (one of `none`, `partial`, `complete`, `abandoned`); execution progress is never a frontmatter field. Added by amendment A-9 2026-08-22 | `rfcs/**` | Progress is store-derived and would diverge on the first escalation |
| D-A.12 | `LOCKED` | The index carries every frontmatter field that aids routing, and nothing derived from the store; progress stays a projection and is never committed. Added by amendment A-9 2026-08-22. *(Reworded by A-14 2026-08-22 from "progress never enters INDEX.md" read as general minimalism — the actual concern was store dependence.)* | `rfcs/INDEX.md` `src/torve/config/rfc_parse.py` | Frontmatter is in the same commit the index is checked against; store data would make `--check` flake on every task run |
| D-A.16 | `LOCKED` | One corpus path, configurable as `rfcs.path`, never a list or a glob. Added by amendment A-15 2026-08-22 | `src/torve/config/runconfig.py` `rfcs/**` | Two roots mean two counters and a colliding identifier at the first merge |
| D-A.17 | `LOCKED` | The next number is derived as the maximum plus one, never stored in a counter file. Added by amendment A-15 2026-08-22 | `src/torve/config/rfc_parse.py` | A counter is state; two branches diverge it and the resolution gives two documents one number |
| D-A.18 | `LOCKED` | Only `NNNN-slug.md` and `INDEX.md` in the corpus directory, no subdirectories; the check routes offenders to `pages/` or `ops/`. Added by amendment A-15 2026-08-22 | `rfcs/**` `src/torve/config/rfc_parse.py` | Without routing the file lands in the repository root and the mess has moved rather than gone |
| D-A.19 | `LOCKED` | Documents are never deleted; identifiers are never reused; gaps are acceptable. Added by amendment A-15 2026-08-22 | `rfcs/**` | Amendments, logs and commit trailers cite identifiers, and reuse redirects all of them silently |
| D-A.20 | `ASSUMED` | A filename is not renamed once the document is on the main branch. Added by amendment A-15 2026-08-22 | `rfcs/**` | Links from `pages/`, amendments and commit messages break; a materially different title is usually a new document |
| D-16.1 | `LOCKED` | A retired identifier's row is removed; the document keeps a prose tombstone and lists the id in `retired:` frontmatter, where it stays resolvable and is never redefined. Added by amendment A-20 2026-08-22 | `rfcs/**` `src/torve/config/rfc_parse.py` | Dead rows would pile up at the one surface executors inherit from; without the structured list, every tombstone citation reads as a typo to the checker |

## Amendments

Carried over with the decisions they introduced. Numbering stays global, so citations elsewhere continue to resolve.

### A-7 — 2026-08-21 — document conventions (adds D-A.1 – D-A.8)

**Found in repository review.** `rfcs/` held three kinds of document with nothing expressing the difference: decision-bearing designs, executed procedures, and a hand-maintained index that had already drifted.

**The sorting rule (D-A.1):** a document with a table of graded decisions is an RFC and gets a number; published documentation goes to `pages/` — written independently for users, versioned with releases, consistent with the corpus but not derived from it; one-off procedures go to `ops/` and are deleted once executed. By this rule the migrations, CLI-contract and configuration-layout documents were promoted to RFCs 0011–0013 (their decision identifiers renumbered to `D-11.*`/`D-12.*`/`D-13.*` while nothing referenced them), and the skill-specialisation guide moved to `ops/`.

**Structure (D-A.2, D-A.3, D-A.6):** structured facts — id, status, dependencies, amendments, owner — live in YAML frontmatter; decision tables stay in markdown, hard-validated by `rfc_index.py`; `INDEX.md` is generated from frontmatter and CI-checked like a lockfile.

**Amendments (D-A.5):** each amendment lives in the `## Amendments` section of its primary target with globally-unique numbering; the standalone `AMENDMENTS.md` file was dispersed into targets (A-1/A-4/A-5/A-7 → the charter, A-2 → RFC 0002, A-3 → RFC 0009, A-6 → RFC 0003) and deleted.

**Logs (D-A.7):** a task log pins `repo` and `base_sha`, so `path:line` evidence resolves six months later to the text the agent actually saw — self-contained means complete relative to a commit, not independent of the repository.

**Executed 2026-08-21:** dev-era task logs were deleted after their divergences were promoted into decision tables, and the discovery-phase history was collapsed to a single commit.

*Note 2026-08-21 — documentation is not derived.* D-A.1a was reworded from "links to decisions and never restates them" to state what it always meant: a page must not contradict an accepted decision and must not restate rationale that belongs under a number. Documentation and the corpus answer different questions ("how do I use this" against "why was this decided"), are read by different people, and move on different axes — pages are versioned with releases and carry no history, while RFCs accumulate amendments and delete nothing (new row D-A.1c). The relationship is **consistency, not derivation**: a constraint, not a generation mechanism. The derived-like-`INDEX.md` analogy was misapplied to `pages/`; the index itself stays generated (D-A.6). Where reasoning would genuinely help a reader, a page links to the RFC rather than summarising it.

### A-9 — 2026-08-22 — implementation status (amends the document conventions)

**Found in use.** `status` describes the document's acceptance and nothing describes the work. In particular there was no way to say "accepted, decisions inherited, implementation deliberately dropped" — the options were to misuse `superseded`, which claims a replacement exists, or to leave `accepted` indefinitely, which says nothing.

**Changed:** frontmatter gains `implementation: none | partial | complete | abandoned`. It is a judgement, on the same footing as `status` — `complete` and `abandoned` are human assertions no count of merged tasks can produce. Backfilled across the corpus at adoption, honestly rather than uniformly.

**Deliberately not changed:** no progress field, and no `in_progress` value. Execution progress is derived from task state and belongs to the store under A-4; a frontmatter copy would diverge the first time a task escalated. Progress is projected per phase by `torve context` and is never committed — and never enters `INDEX.md` (D-A.12).

**Also edits:** 0007 §4 (the projection), 0007 decisions D-7.15/D-7.16.

### A-10 — 2026-08-22 — what the frontmatter edges mean (adds D-A.9, D-A.10)

**Found in planning design.** Within a single RFC the graph is handled; between RFCs, `depends_on`, `informed_by` and `supersedes` were read only by `rfc_index.py` for link validation. Nothing said what the edges *constrain*.

**The correction that shapes it:** a dependency between RFCs is not a dependency between tasks. `depends_on` constrains *planning readiness* — a document cannot be planned until its dependencies are `accepted`, because its decision table inherits their rows and grades are copied at mint time (D-A.4). Shipping order is carried by a phasing table, not by the graph. `informed_by` constrains nothing: it tells a reader what to read first, and making it checkable would turn a reading hint into a blocker.

**A document may not inherit decisions from one that is not `accepted` (D-A.10).** A grade copied from a draft is a grade that may change under an executor.

**Known violation at adoption:** RFC 0009 (`accepted`) depends on RFC 0004 (`draft`) — surfaced by this rule, resolution pending review.

**Also edits:** 0007 §3.1–§3.3 and decisions D-7.7–D-7.11.

### A-14 — 2026-08-22 — the index carries the whole frontmatter (amends D-A.12)

**Found in use.** A-9 added `implementation` and nothing surfaced it. D-A.12 read as a general instruction to keep the index minimal, which was an overreach: the actual concern was store dependence, since a store-derived column would make a committed, CI-checked file depend on a database, and a flaking `--check` is one people learn to re-run rather than read. *(The source patch numbered this A-12; that was taken, so it lands as A-14 per D-A.5.)*

**Changed:** the rule is now that the index carries everything from the frontmatter and nothing from outside it. `implementation` and `kind` join the generated columns, alongside status, dependencies and amendment identifiers — the `Amends` column is a list of identifiers, never a summary, because the moment the index describes what an amendment changed it becomes a second, staler account. Rows are grouped by `kind`, with documents that are accepted but abandoned separated into their own section — that pairing is the most hazardous in the corpus (decisions still inherited, no implementation ever coming) and two adjacent columns in a flat table are easy to miss. `informed_by` stays out: it constrains nothing (D-7.9).

**Unchanged:** no store-derived data in the index. Progress remains a projection in `torve context` and is never committed. `--check` stays deterministic, which was the whole point of the original restriction.

### A-15 — 2026-08-22 — corpus location, numbering, and contents (amends the document conventions)

**Found in use.** Three things were unstated: where the corpus lives when it is not `rfcs/`, how the next number is chosen, and what may sit in the directory. The last one had already caused one clean-up. *(The source patch numbered this A-13; that was taken, so it lands as A-15 per D-A.5.)*

**Changed:**

- One configurable path, `rfcs.path`, defaulting to `rfcs/`. One path only — two roots mean two counters and a colliding number at the first merge. Specifications that genuinely need two locations are two corpora with two `.torve/` configurations.
- The next number is derived as the maximum plus one. **No counter file:** a counter is state, two branches diverge it, and resolving that conflict gives two documents the same number. A parallel-creation race instead surfaces as a duplicate-`id` failure at merge, which is loud rather than silent. Resolving that collision means renaming the document merging second, before anything references it — D-A.4 makes identifiers permanent *once a document is on the main branch*, not from the moment of creation, and this is the case that distinction exists for.
- Only `NNNN-slug.md` and `INDEX.md` may live in the directory, with no subdirectories. The check's message routes the offending file to `pages/` or `ops/` rather than only refusing it — without routing, the file lands in the repository root and the mess has simply moved. The check belongs to `torve rfc check`, not `torve doctor`: `doctor` is about environment readiness, this is about corpus correctness. Two companion checks: the filename's numeric prefix must match `id` (slug loosely against `title`), and a filename is not renamed once the document is on the main branch.
- **Documents are never deleted.** They leave service via `superseded` or `implementation: abandoned`. Identifiers are cited by amendments, divergence logs and commit trailers, and a reused number silently redirects all of them. Gaps are acceptable; reuse is not — a new document created in a numbering hole is refused.

**Rejected:** checksums in the index. Git already guarantees content, and `--check` compares the rendering itself, which is strictly stronger and says what diverged rather than only that something did. It also protects against nothing that is left over, and puts a meaningless changed line in every diff.

**Also edits:** 0013 (A-16).

### A-20 — 2026-08-22 — how an identifier retires (adds D-16.1, amends §3, §4)

**Found by the citation-resolution check on its first day.** 0005 retired D-5.5 the correct way for readers — row removed, prose tombstone — and the checker flagged the tombstone's own citation as unresolvable, because nothing structured recorded that the identifier had ever existed. The alternative, keeping retired rows in the table marked dead, was rejected: the table is the surface executors inherit from, and rows existing only to say "don't use me" are noise exactly where density matters most.

**Changed:** frontmatter gains an optional `retired:` list — the identifiers this document once defined, since removed. Three consequences, all checked:

- A retired identifier **resolves**: the citation check treats it as defined, so a tombstone reads clean — which lets unresolvable citations harden from warning to problem, the form in which the check actually catches typos.
- A retired identifier is **never redefined**: a decision table anywhere in the corpus claiming an id from any `retired:` list is a problem (D-A.19 made checkable for the retired case).
- The retirement itself stays prose — the tombstone says *why* the row went; the frontmatter says only *that* it did (D-A.2's split, applied to endings).

Retirement should stay rare: one identifier in sixteen documents so far. The list scales at a line per id, not a row per corpse.
