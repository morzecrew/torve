<!-- torve:managed skills/spec-writer — rendered from the corpus; do not edit by hand -->

## Decisions governing `skills/spec-writer/`

### S-0060/D-8 — `LOCKED` (A source is a file, and the contract names it)

A document's `kind` stays `design` or `convention`: it answers what prose an accepted document owes and nothing else, so a bug worth a document is a design whose motivation is the defect, an audit's standing rules are a convention, and where the work came from is the source

- Paths: `src/torve/domain/vocabulary.py` `src/torve/config/spec.py` `skills/spec-writer/**`
- Consequence: One axis per field; the corpus never grows a second way to say provenance
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0106/D-4 — `ASSUMED` (A decision is judged before the pull request)

A row's `check` may name a property test: a Hypothesis test stating the decision over generated inputs. `hypothesis` becomes a direct dev dependency. The spec-writer skill says when a row gets a property (a rule over inputs: parsers, matchers, state transitions, round trips), when an example test is the honest check, and that the phase settling the row writes it.

- Paths: `pyproject.toml` `skills/spec-writer/SKILL.md`
- Consequence: a decision is checked over the inputs it governs, not over the one example its author thought of

### S-0115/D-8 — `ASSUMED` (Every decision shows its evidence)

The working rules and the spec-writer skill tell an implementer to cite, in each test's docstring, the row it proves, and `pages/docs/operating.md` documents the gate and the table's columns.

- Paths: `skills/working-rules/SKILL.md` `skills/spec-writer/SKILL.md` `pages/docs/operating.md`
- Consequence: the citations the gate reads are written on purpose rather than by habit

### S-0116/D-13 — `ASSUMED` (An independent verifier)

The operating guide and the torve skill's night reference say how to turn the pilot on, read a held-out failure and handle a disputed test, and the spec-writer guide asks a phase's intent to name the public surface its tests drive.

- Paths: `pages/docs/operating.md` `skills/torve/references/night.md` `skills/spec-writer/**`
- Consequence: a phase written for a verifier names what to test, so the verifier does not invent an API

<!-- /torve:managed -->
