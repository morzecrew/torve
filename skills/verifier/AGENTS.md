<!-- torve:managed skills/verifier — rendered from the corpus; do not edit by hand -->

## Decisions governing `skills/verifier/`

### S-0116/D-6 — `ASSUMED` (An independent verifier) — implementation: none

A shipped skill, `torve:verifier`, briefs the verify role: tests from the rows and the intent alone, at least one per settled row citing it, imports inside test bodies, properties where a row is quantified over inputs, held-out properties under `.torve/tmp/holdout/`, no implementation, and a halt when the intent names no public surface. `torve init --starter` writes a verify profile that equips it.

- Paths: `skills/verifier/**` `src/torve/cli/init.py`
- Consequence: a verify attempt is told what an independent test is, in the same place every other role is told its rules

<!-- /torve:managed -->
