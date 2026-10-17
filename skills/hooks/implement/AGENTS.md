<!-- torve:managed skills/hooks/implement — rendered from the corpus; do not edit by hand -->

## Decisions governing `skills/hooks/implement/`

### S-0077/D-1 — `ASSUMED` (The turns the engine could have taken itself)

The acceptance commands are run by the finish on the attempt's behalf, and their output reaches the attempt only when they fail

- Paths: `skills/hooks/implement/finish_check.py`
- Consequence: an attempt stops running the suite to see green, because green is the condition of being allowed to stop

### S-0077/D-2 — `ASSUMED` (The turns the engine could have taken itself)

The finish names the governed rows the diff touches and which of them have no entry, so the attempt writes the entries rather than discovering it owes them

- Paths: `skills/hooks/implement/finish_check.py`
- Consequence: the `log owed` poll before and after the entries stops being two round trips asking a question the engine can answer

### S-0077/D-3 — `LOCKED` (The turns the engine could have taken itself)

Both are reverted if attempts per landing rises, whatever happens to the turn count they were meant to remove

- Paths: `skills/hooks/implement/finish_check.py`
- Consequence: a change that buys turns by making attempts fail more often is caught by the number it damaged rather than defended by the one it moved
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0104/D-12 — `ASSUMED` (A phase's scope bends to the files its change reaches)

This document changes torve's own copy of the pre-write hook; an adopter's copy is refreshed from torve's when it upgrades, and shipping the hook as package equipment is a document of its own.

- Paths: `skills/hooks/implement/scope_guard.py`
- Consequence: the change stays inside torve's tree, and an adopter's hook follows its upgrade

### S-0105/D-3 — `ASSUMED` (One reading of a scope glob)

The pre-write hook reads its globs through a byte-identical copy of the module, `skills/hooks/implement/globs.py`, imported as a sibling the way `read_guard` imports `scope_guard`. `fnmatch` leaves the hook, and `read_guard` and `finish_check` take the same reading through `scope_guard.is_allowed`. A test fails when the copy and the source differ.

- Paths: `skills/hooks/implement/globs.py` `skills/hooks/implement/scope_guard.py`
- Consequence: a write the hook refuses is a write the gate would convict, and the other way round

<!-- /torve:managed -->
