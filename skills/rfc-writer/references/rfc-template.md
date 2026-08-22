# RFC template

Copy the skeleton below into `NNNN-kebab-title.md`. Replace `<placeholders>`, delete the guidance blockquotes, and drop sections the RFC doesn't need (keep numbering contiguous). A minimal design-lock RFC keeps: header block, Design, Non-goals, Decisions.

---

```markdown
---
id: "NNNN"
title: <Title>
status: draft
depends_on: []
informed_by: []
supersedes: []
superseded_by: null
amended_by: []
owner: <name>
description: >-
  <One sentence, ~200 chars: which design this is, not what it decided.
  Becomes the generated index's routing line.>
schema_version: 1
---

# RFC NNNN — <Title>

- **Scope:** <One dense paragraph: what this RFC covers and what it deliberately
  does not. New packages/modules touched, contract changes (or "no contract
  changes"), the boundary of the blast radius. This is the paragraph a reader
  uses to decide whether to read the rest.>
- **Related:** <Links to the code being touched (relative paths into the repo),
  other RFCs by number, prior art, external docs.>
- **Discussion:** <Optional — link to the PR / issue / thread where the design
  was or is being debated. Delete if none.>
- **Origin:** <Optional — where the design was ported or generalized from:
  a sibling project, a spike, a production incident. Delete if none.>

---

## 1. Summary

<What ships, in a few sentences. Write it last, once the design has settled.>

## 2. Motivation

<The problem, with evidence: measured numbers, real failure cases, links to the
code paths that hurt. If the motivation can't cite anything concrete, question
whether the RFC is needed.>

## 3. Current state

<What exists today, verified against the code — not from memory. Name the
files, ports, schemas involved. Surprising verified facts ("zero pyproject
edits needed — confirmed against line ~218") belong here; they save the
implementer a re-investigation.>

## 4. Goals / Non-goals

**Goals**

- <...>

**Non-goals**

- <Explicit exclusions with a reason each — "not X, that is Y's job". Non-goals
  prevent scope creep during implementation and re-litigation after.>

## 5. Design

<The core of the document. One subsection per workstream or component
(### 5.1, ### 5.2, …). Pin the design with real artifacts — signatures,
schemas, wire formats, config shapes — in code blocks; prose alone drifts.
State failure semantics explicitly (what raises, what is refused, what fails
closed). Where a decision was contested, keep one sentence on the rejected
alternative and why it lost; when the choice shaped the design, give it an
`### Alternatives considered` subsection that states each alternative's
trade-off, not just its rejection — that is what stops re-litigation.>

### 5.1 <Component / workstream>

<...>

## 6. Tests

<How the design is verified: new suites, conformance families, what parity is
asserted, what is explicitly not tested and why.>

## 7. Docs

<What documentation ships with the change, and any doc claims that must be
worded carefully (e.g. migration honesty, threat-model caveats).>

## 8. Out of scope

- <Each item: what is excluded, why, and what would change that ("named as the
  escape hatch, not built"). Different from Non-goals: these are adjacent
  things a reader might assume are included.>

## 9. Risks

- <Honest failure modes — technical risks, misreading risks ("X read as
  security theater"), operational risks. Each with the mitigation or the
  explicit acceptance.>

## 10. Unresolved questions

- <What must be settled before the design counts as locked, vs. what
  implementation is free to settle. Name each unknown and who/what resolves
  it. An empty section is a claim — only make it if true.>

## 11. Decisions

| # | Grade | Decision |
| --- | --- | --- |
| 1 | `LOCKED` | <One decision per row, self-contained, with the load-bearing rationale compressed in — and, where a decision constrains the future non-obviously, its consequence ("locks us to X; changing later means Y"). This table is the contract: pickup should require reading it, not re-deriving it.> |
| 2 | `ASSUMED` | <Believed correct but not load-bearing. Execution may depart from it if building proves it wrong, and logs the departure in its task log.> |
| 3 | `OPEN` | <Deliberately delegated to implementation. Say what the question is and what would settle it; the executor decides and logs the decision. An absent row is not `OPEN` — it is silence, and silence gets filled by whoever arrives first.> |
| 4 | `ASSUMED` | <A row execution proposed and the author accepted. Ends with its provenance: Added by execution 2026-08-14 — see logs/T-0142.md (D-3, attempt 2).> |

## 12. Phasing

<What lands first, what is gated on what, what is demand-gated. Prose is fine
for a document nobody plans to mint; for `torve plan` to consume it, carry a
fenced YAML block — a list of units, each with a phase number, a title, one
intent paragraph (what changes and why — never steps), the scope it may
touch, its acceptance commands, and the phase numbers it waits on. Several
entries may share a phase number: they run in parallel, so their scopes must
not intersect.>

```yaml
- phase: 1
  title: the-first-unit
  intent: >-
    One paragraph: what changes and why.
  scope: ["src/thing/**", "tests/**"]
  acceptance: ["make test"]
  depends_on: []
- phase: 2
  title: the-follow-up
  intent: >-
    What this phase changes, and why it waits for phase 1.
  scope: ["src/other/**"]
  acceptance: ["make test"]
  depends_on: [1]
```
```

---

## Notes on filling it in

- **An existing corpus outranks this template.** If the project's RFCs already
  use a different section set or numbering (say, Decisions at §10), match the
  corpus — `§NN` cross-references must stay unambiguous across the directory.
  This skeleton is for directories without an established shape.
- **Header ↔ filename sync:** the `NNNN` in the H1 and the frontmatter `id`
  must match the filename; the H1 title must equal the frontmatter `title`.
- **Status lives in frontmatter** (charter D-A.2): `draft` until reviewed and
  depended on, then `accepted`; `superseded` only alongside `superseded_by`.
  Nuance ("phases 1–2 shipped; outstanding: …") goes in a body
  `- **Implementation state:**` line, not into the status value — an annotated
  true status beats a clean false one, and the queryable field stays clean.
- **Amendments over rewrites:** once an RFC leaves Draft, the decision table
  is append-only — a reversed decision gets a new row citing the row it
  reverses, not an edit. History someone relied on stays readable. A change of
  mind by the *author* may carry a dated note in the status line or the
  affected section; what **execution** found does not, since that already lives
  in the task's log and reaches the RFC as an appended row citing its
  entry. Restating the log's narrative here guarantees the two disagree later.
- **Index row:** generated, never written (charter D-A.6) — `torve rfc
  index` builds INDEX.md from frontmatter, and `torve rfc check` fails CI
  when it drifts. The frontmatter `description` is the routing line: one sentence
  saying which design this is, so a reader knows whether to open the file.
  What it decided belongs in §1 and §11, and the index never carries history.
  Target 200 characters, ceiling 300.
