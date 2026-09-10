<!-- torve:managed skills/spec-writer — rendered from the corpus; do not edit by hand -->

## Decisions governing `skills/spec-writer/`

### S-0060/D-8 — `LOCKED` (A source is a file, and the contract names it)

A document's `kind` stays `design` or `convention`: it answers what prose an accepted document owes and nothing else, so a bug worth a document is a design whose motivation is the defect, an audit's standing rules are a convention, and where the work came from is the source

- Paths: `src/torve/domain/vocabulary.py` `src/torve/config/spec.py` `skills/spec-writer/**`
- Consequence: One axis per field; the corpus never grows a second way to say provenance
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

<!-- /torve:managed -->
