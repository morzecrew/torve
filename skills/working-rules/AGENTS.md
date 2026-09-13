<!-- torve:managed skills/working-rules — rendered from the corpus; do not edit by hand -->

## Decisions governing `skills/working-rules/`

### S-0067/D-3 — `ASSUMED` (A session is briefed, and the working rules have one source)

The working rules live once, at `skills/working-rules/`, reaching a sandbox as a declared equipment item and a session through its skill root; `build_prompt` names the skill instead of inlining it

- Paths: `skills/working-rules/**` `src/torve/adapters/agent/harness.py` `.torve/agents/**`
- Consequence: the best short statement of how to work in this repository stops being readable only by opening the engine's source, and both modes read one text that a diff can refuse

### S-0067/D-6 — `ASSUMED` (A session is briefed, and the working rules have one source)

The working-rules skill states that a scope naming a module names that module's test file

- Paths: `skills/working-rules/**`
- Consequence: the one rule that produced every measured lint refusal on the hand-minted path is written where a session reads it, not only where a lint reports it

### S-0067/D-7 — `ASSUMED` (A session is briefed, and the working rules have one source)

The working-rules skill states that a task's scope does not name the changelog; the entry is written afterwards, from the landing records, once the work it describes has been reviewed

- Paths: `skills/working-rules/**`
- Consequence: the same exact literal stops appearing in most of the board's allow-sets, where it makes every pair of tasks naming it clash and serialise; and an entry stops describing an implementation that review may still change

### S-0067/D-8 — `ASSUMED` (A session is briefed, and the working rules have one source)

The working-rules skill states that a test may not assume it is the only one on the machine: what it creates on a shared daemon it finds by its own name or root, never by a literal other tests also use

- Paths: `skills/working-rules/**`
- Consequence: the suite can run in parallel, which is measured at 4.3x, and a test that breaks the rule fails as a bug rather than as a flake

<!-- /torve:managed -->
