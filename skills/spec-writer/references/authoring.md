# Writing a document: anatomy and style

The shape of the document and how its prose behaves. Read this before
writing a new one; `SKILL.md` carries the decisions that outlive it.

## Anatomy

Read `references/spec-template.md` before writing and start from `torve
spec new`. A document is a directory of YAML files: `document.yaml` carries
the header facts (`status`, `implementation`, `depends_on`, `owner`,
the typed prose — `summary`, `motivation`, `current_state`, `goals`,
`non_goals`, `tests`, `docs`, `out_of_scope`, `risks` — the `design` list and
the extras, each keyed and headed by its key, and the author's typed lists;
`decisions.yaml` carries the rows the machine reads and `phasing.yaml` the
planner's list. The
first section is the scope paragraph — what this document covers *and what
it deliberately does not*, with the code and documents it relates to and
where it was debated — the paragraph a reader uses to decide whether to
read the rest.

**The anatomy.** The keys, in reading order:

1. **Summary** — what ships, in a few sentences
2. **Motivation** — the problem, with evidence from the actual codebase
3. **Current state** — what exists today, verified against the code, not from memory
4. **Goals / Non-goals** — explicit both ways
5. **Design** — the core; one section per workstream or component, in order (there is no container section: the heading is the key, the number the position), with real signatures/schemas/code blocks where they pin the design. Where a choice was contested, record the rejected alternative and why it lost in the `alternatives` list — the trade-off, not just the rejection — and never restate a typed list as a fence or a table inside a section
6. **Tests** — how the design is verified
7. **Docs** — what documentation ships with it
8. **Out of scope** — named and *reasoned*: each item says why it's excluded and what would change that
9. **Risks** — honest failure modes, including risks of the document being misread
10. **Unresolved questions** — prose for what must be settled before the design counts as locked vs. what implementation is free to settle; the `questions` list carries each one by identifier

The rows are the `decisions` list, each carrying a **grade** (see `SKILL.md`); this is what makes pickup cheap and re-litigation unnecessary. Where a decision constrains the future non-obviously, its `consequence` says so — the consequences of one decision are the context of the next. Decisions the document deliberately leaves to implementation belong there too, graded `OPEN`, rather than being left out. The `phasing` list is what lands first and what is gated on what.

**Scale to the document's weight.** A small design-lock document needs only the scope section, a design section, non-goals and the rows. Don't pad a two-page document to twelve sections; don't collapse a system-wide proposal into three. Keep section numbering contiguous for whatever subset is used.

## Style

- **Ground every claim in the code.** "Current state" and "Motivation" cite files, line-level facts, and measured numbers — link them with relative paths. An RFC that argues from memory is a fiction with headings.
- **Record decisions with their why — and their cost.** The rows are the contract; `rationale` and the body carry the reasoning. Rejected alternatives get their trade-off in `alternatives` (an alternative recorded with its trade-off stays rejected; one recorded as merely "rejected" gets re-proposed). A decision that closes a door later says so in its `consequence`.
- **Timely beats polished.** A rough document that exists beats a perfect one that doesn't (Oxide's RFD rule: "timely rather than polished"). Draft prose may be rough; the scope paragraph and the rows may not.
- **Be honest about limits.** If a mechanism is deferred, gated, or known-incomplete, say so in the document rather than letting the reader discover it. Fail-closed wording ("refused", "raises", "deliberately unscheduled") beats optimistic vagueness.
- **Dense beats long.** Prefer one load-bearing paragraph over three thin ones. This applies inside the document; the summary's first sentence is governed by the opposite instinct — one routing sentence.
- **Prose is prose.** Markdown inside a section body is welcome — fences, links, emphasis — and nothing parses it. Type what was already a list; never fragment an argument into fields, and never carry a typed list's content as prose: `check` refuses a section that does.
