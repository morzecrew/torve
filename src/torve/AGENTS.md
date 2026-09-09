<!-- torve:managed src/torve — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/`

### S-0055/D-23 — `LOCKED` (Standing decisions)

Five layers — `base`, `domain`, `application`, `adapters`, `cli`, beside `gates` and `config` — with import directions enforced by import-linter over the whole package; the `layering` gate blocks

- Paths: `src/torve/**` `pyproject.toml`
- Consequence: The hexagon is visible in the tree and mechanically held
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-27 — `ASSUMED` (Standing decisions)

No module is named `models`, `utils`, `helpers`, `common` or `base` below the package root; modules are named for what they hold, and the `source-layout` gate reads it

- Paths: `src/torve/**`
- Consequence: Names that admit anything accumulate everything

### S-0055/D-59 — `ASSUMED` (Standing decisions)

Strict typing is a floor over `src/`: `mypy --strict` and `basedpyright` strict block CI and the acceptance fallback; tests, scripts and skills carry no type floor

- Paths: `src/torve/**` `pyproject.toml`
- Consequence: A substrate surface change fails at the type check

## Invariants holding over `src/torve/`

- **S-0055/I-1**: The five layer contracts hold over the whole package
  - Paths: `src/torve/**` `pyproject.toml`
  - Check: `uv run lint-imports --config pyproject.toml`
- **S-0055/I-4**: The typing floor over src holds
  - Paths: `src/torve/**`
  - Check: `uv run mypy src && uv run basedpyright src`
- **S-0055/I-5**: The suite is green
  - Paths: `src/torve/**` `tests/**`
  - Check: `uv run pytest`

<!-- /torve:managed -->
