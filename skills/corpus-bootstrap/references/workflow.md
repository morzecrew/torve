# The supervised-session workflow

The extraction is a session, not a script: read, extract, draft, hand off.
Everything the engine does with the result happens after the human accepts
it, and none of it is this skill's doing.

## Inputs

1. **The survey report** — the JSON `torve survey --format json` wrote over
   the target's history. If the operator ran the text view, re-run with
   `--format json`; the extraction works from the JSON (the text view is a
   rendering, and a rendering loses the `no_corpus` flag).
2. **The repository** — the target's working tree, with history. `git log`
   on the files the report names is the second evidence source; the survey
   replay is read-only and cannot tell you whether a firing was corrected —
   the history can.

## Step 1 — read the survey

Work through the report's landings newest-first. For each landing, note:

- which gates **fired** and the files they named (`gate.output` carries the
  paths);
- which gates **skipped with `no_corpus: true`** — the corpus gaps, which
  the summary's `corpus_adds` already collected;
- which gates **skipped with `no_corpus: false`** — fail-fast "not run"
  after a blocking failure, or acceptance's structural skip. These are not
  findings.

## Step 2 — read the repository

For every fired gate:

- `git log --oneline -- <the files the gate named>` — was the violation
  corrected later? That correction is the defended-boundary evidence.
- Look at the tree: does the boundary exist as structure (a `src/` home, a
  `tests/` directory, a `.torve/` manifest)? The globs for the row are read
  from here.

## Step 3 — extract candidate rows

One candidate row per finding:

- a fired gate with a correction behind it → `LOCKED` candidate;
- a fired gate without one → `ASSUMED` candidate;
- every name on `summary.corpus_adds` → `ASSUMED` candidate (the row that
  gives the gate something to measure once work is minted).

Skip clean gates entirely: a clean gate measured nothing wrong, and a row
for it would be decoration.

## Step 4 — grade and write

- Apply the doctrine: mostly `ASSUMED`, `LOCKED` only on defended-boundary
  evidence, paths on every row, no phasing, no `OPEN` rows.
- Write the draft from `references/draft-template.md`, keeping the shape:
  frontmatter, H1, a short summary of what the survey found, the Decisions
  table, nothing else.

## Step 5 — place the draft

The draft is one document per adoption, named `NNNN-standing-decisions.md`
in the corpus directory (`rfcs/` by default) — the shape this skill records.
Allocate the number with `torve rfc new "Standing decisions"` when the
engine is available (the operator ran the survey, so it is): it derives the
next free number, instantiates the template and regenerates the index, and
the corpus stays mechanically consistent. Then replace the template body
with the extracted table and keep `status: draft`.

## Step 6 — the hand-off

The extraction is done when the draft is written. The human then:

1. **Edits** — grading, wording, paths. This is the real judgement; the
   extraction is evidence, not verdict.
2. **Commits** — the draft becomes a committed document.
3. **Accepts** — `status: accepted` in the same commit (or the review that
   follows it). Acceptance is what makes the rows stand.

From acceptance, the engine's standing inheritance copies every row whose
declared paths intersect a contract's scope into that contract at mint time,
and the battery convicts only work minted after — never the tree as it
stands and never its history (D-31.3). The skill has no further part, and
the engine has no surface for the baseline at all.

## The fixture

`fixtures/survey-report.json` and `fixtures/0001-standing-decisions.md` ride the
skill as its fixture: the sample report in, the checkable output shape out.
An extraction run over the sample should reproduce that shape — the report's
corpus gaps (`no-test-tampering`, `decisions-reported`, `self-audit`) as
`ASSUMED` rows, the corrected secrets firing as the one `LOCKED` row, the
uncorrected scope firing as an `ASSUMED` row.
