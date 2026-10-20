<!-- torve:managed skills/flag-dont-flip — rendered from the corpus; do not edit by hand -->

## Decisions governing `skills/flag-dont-flip/`

### S-0100/D-6 — `ASSUMED` (A session learns torve from the torve it runs)

How to write a divergence entry lives in working-rules, matching `torve log divergence --help`; flag-dont-flip keeps when to flag and how a grade binds.

- Paths: `skills/working-rules/**` `skills/flag-dont-flip/**`
- Consequence: the two skills an executing agent reads cannot contradict each other on the log

### S-0116/D-8 — `ASSUMED` (An independent verifier)

An implementer that disputes a verifier test halts with a divergence entry naming the test and the row, and never edits the file; `torve:flag-dont-flip` says so.

- Paths: `skills/flag-dont-flip/**`
- Consequence: a wrong test reaches a person as an `underspecified` escalation instead of being quietly rewritten

<!-- /torve:managed -->
