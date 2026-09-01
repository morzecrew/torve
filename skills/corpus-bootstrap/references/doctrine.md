# The extraction doctrine, in detail

The SKILL.md carries the four rules; this file is the reasoning behind them
and the discriminations they need to apply them.

## Why the baseline is graded, not generated

A grade is a human judgement about reversal cost: how expensive it would be
to reopen this row. Nothing extracts that deterministically from a
repository where nobody has made the judgement — the engine's own doctrine
says so, and the baseline exists because the engine refuses to make that
judgement itself. What the extraction can do is lay out the evidence the
survey produced and let the human grade against it. The draft's honesty is
its credibility: a draft that arrives mostly `ASSUMED` reads as a proposal;
one that arrives mostly `LOCKED` reads as a takeover.

## The three grades and what belongs in a baseline

| Grade | In the baseline? | Why |
| --- | --- | --- |
| `ASSUMED` | The default. | Believed correct, not load-bearing; the acceptance edit can correct it cheaply. |
| `LOCKED` | Only on defended-boundary evidence. | Reopening is expensive — the repository's own history showed someone defending the line. |
| `OPEN` | Never. | An OPEN row is a decision *deferred*; a deferred decision does not stand. If extraction surfaces a genuinely open question (the survey cannot produce one — it measures, it does not ask), it belongs in the draft's prose, named, so the human settles or delegates it explicitly. A standing OPEN row would delegate the baseline's own rows to whoever mints first, invisibly. |

## Defended-boundary evidence (the LOCKED test)

A `LOCKED` row claims the repository defends this boundary. The evidence,
in descending strength:

1. **A firing followed by a correction landing.** The gate fired on landing
   N; landing N+1 (or a later one in the window) removed the violation and
   measured clean. History shows the boundary being defended *and* the
   defense succeeding. This is the strongest evidence, and the only kind
   that turns a firing into a lock.
2. **A consistent clean record with the boundary visible in the tree.**
   The gate never fired across the window, and the tree shows the boundary
   as structure: code lives under `src/`, tests under `tests/`, a
   manifest constrains the scope. The layout is deliberate because the
   history holds it.

What is **not** defended-boundary evidence:

- A single firing with no correction in the window. The boundary exists as a
  check but the violation landed and stayed — `ASSUMED`.
- A firing on the *last* landing of the window. There is no history after it
  to show defense — `ASSUMED`, and the draft can say so.
- A clean gate alone. Clean measures nothing wrong; it does not show anyone
  defending anything — no row follows at all.

Corollary: a baseline with several `LOCKED` rows from a short window is
over-grading. Defended boundaries are the exception, and the draft's LOCKED
rows should be few enough that a reader can see the history behind each.

## Paths on every row

A pathless row is never standing (the inheritance layer reads only rows with
declared paths), so a pathless row in the baseline is decoration: it
governs nothing and the silence check skips it. Every row names the globs it
governs, read from the actual tree — `src/**` only if `src/` exists and is
the code home; `tests/**` only if the tests live there. Paths are
gitignore-style globs (the same dialect the gates match). When a row governs
the corpus's own files — task logs, the manifest — the globs are
`.torve/tasks/**` and `.torve/gates.yaml` style paths, named as they exist.

## No phasing

The baseline has no `## Phasing` section, and no section that could be read
as one. The engine mints tasks from Phasing sections; the baseline is not a
plan and D-31.2 leaves the engine no surface for it. If the adoption needs
sequenced work, that is a design document's job, written after the baseline
accepts — never a phase inside it.

## The survey's four outcomes, decoded

| Outcome | Meaning | Row follows? |
| --- | --- | --- |
| `fail` / `error` / `bypassed` (fired) | A boundary was crossed in merged history. | Yes — candidate row, graded by defended-boundary evidence. |
| `pass` / `flaky` (clean) | Measured nothing wrong. | No — the boundary is already held. |
| `skipped`, `no_corpus: true` | No task contract existed; the gate could not measure. | Yes — `ASSUMED` row giving the gate something to measure once work is minted. |
| `skipped`, `no_corpus: false` | Fail-fast "not run" after a blocking failure, or acceptance's structural no-commands skip. | No — not a corpus gap. |

The `summary.corpus_adds` list is the report's own voice: the gates that
never measured a single landing and whose silence is the no-task skip. Every
name on it is a candidate `ASSUMED` row. The report names them; the draft
gives each a row with paths.
