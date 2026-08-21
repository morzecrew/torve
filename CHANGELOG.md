# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- RFC corpus 0001–0010 under `rfcs/` with index.
- `torve` package: the RFC 0002 gates-library increment.
- Six builtin gates (`scope`, `acceptance`, `no-test-tampering`,
  `decisions-reported`, `self-audit`, `secrets`) plus shell gates via
  `gates.yaml`; cheapest-first ordering with fail-fast on blocking failures.
- `flaky` gate outcome with per-command counters and a reviewed quarantine
  list; `Torve-Bypass` commit-trailer bypass, counted and logged, with the
  secrets gate exempt.
- `torve gates run` / `torve gates check` (17-case sabotage suite) and
  `torve size`; JSONL telemetry stamped with `config_hash` and denormalised
  decisions.
- Dogfood wiring for this repository: `gates.yaml`, task contract
  `tasks/T-0002.yaml`, execution log `logs/T-0002.md`, GitHub Actions CI.
- Runner core (RFC 0003 phase 1): `torve run` — one task, synchronous, exit
  code is the outcome — with the state machine and enumerated escalation
  reasons, git-worktree workspaces, and JSON run state beside the worktree.
- Two Runtime adapters behind one "workspace in, changed files out" contract:
  Docker (bind mount, `--init`, platform-bounded lifecycle) and OpenSandbox
  (tar sync over the files API; SDK as the `torve[opensandbox]` extra), with a
  shared conformance battery.
- `FakeAgent` scripted scenarios (always sandboxed), `torve status`, and
  `torve reap` — convention-driven sweep that expires stale runs as
  `lease_expired`, proven after `kill -9`.
- Shell gates in `torve run` execute in a fresh sandbox the agent never
  touched, via a new executor seam in the gate runner; `torve.yaml` carries
  runner configuration.
- Durability (RFC 0003 phase 2): the attempt loop runs as one durable
  function over forze's run store — real leases, fenced terminal writes,
  cancellation riding the lease heartbeat, and recovery via
  `claim_abandoned`. Mock store for tests and simulation, Postgres for real
  runs (`torve[postgres]`), with torve-owned SQL migrations applied by `torve migrate substrate`.
- `torve cancel` (cooperative, fail-closed on backend capability) and a
  durable reap path that replaces the heartbeat heuristic under Postgres.
- Deterministic simulation (forze_dst): the real attempt loop and real
  TaskStore driven concurrently under one master seed set — four invariants,
  four reachability targets, and four deliberately broken twins the oracle
  must catch.

- Corpus amendments A-1..A-6 (`rfcs/AMENDMENTS.md`) applied: execution logs
  are YAML (`logs/<task-id>.yaml`, single-use `scripts/migrate_logs.py`,
  gate accepts YAML only), gate implementations carry their amendment names
  (`decisions_reported.py`, `no_test_tampering.py`, `gates/sabotage.py`),
  D-27 reworded as a git↔store boundary, and the charter records that agents
  do not communicate (D-31).
- Three specialised skills ship with the package (`skills/`, A-3/D-9.7):
  `flag-dont-flip`, `rfc-writer`, `ratchet-what-you-build` — materialized
  role-scoped into the sandbox at dispatch; no install command by design.
- Hardened `rfc_index.py` (shipped with the specialised `rfc-writer`):
  requires a Paths column, paths on every `LOCKED` row, and unique decision
  identifiers; the corpus decision tables gained Paths columns throughout.

- Migrations per `rfcs/0012-migrations.md`: owner-grouped SQL histories
  (`migrations/{torve,substrate,telemetry}/`), `yoyo-migrations` behind the
  `torve[migrate]` extra (lazy import, exit code 3 with the install hint),
  migrations shipped as wheel package data, `torve migrate <target>` with
  `--all`/`--status`, `torve doctor` enforcing the `FORZE_VERSION` pin, and
  the conformance battery run against fresh *and* populated databases in CI.
  Forward-only, checksummed; `torve store provision` is replaced outright.

- Charter A-12 and 0003 A-13 executed: one directory per task —
  `.torve/tasks/T-nnnn/` holding `contract.yaml` and, once anything was
  written, `log.yaml` — with a three-level fallback chain in
  `config/layout.py` (per-task dir, flat `.torve` layout, root legacy) and
  `task_dir()` as the retention unit (D-A.13). All fourteen existing tasks
  moved via `git mv`; `.torve/logs/` is gone. A missing log is an empty
  log to every reader (D-3.21): `decisions-reported` runs the silence
  check over a synthesized empty document — a touched `LOCKED` area still
  convicts — and `self-audit` passes on absence instead of demanding a
  file into existence (retiring its T-0002 presence check; the
  written-log drift_count claim survives). The FakeAgent writes its log
  at the canonical path, created by its first entry (D-3.20), with a
  Docker end-to-end test proving an entry written before a `kill` is on
  disk. Sabotage suite at 28 cases.
- RFC validation moved into the package (0007 §3a, charter A-15, 0013 A-16):
  `torve rfc check | index | new | graph` with vocabularies defined once in
  `domain/rfc.py` (D-7.13) and the format owned by `config/rfc_parse.py`
  (D-7.12) — the skill's `rfc_index.py` script is deleted and the skill
  teaches content only. New checks: corpus-directory contents with routing
  messages (D-A.18), `depends_on` cycles, non-accepted inheritance
  surfaced as a warning (D-A.10), line-number citations of real paths, and
  the `kind` vocabulary. Numbering is derived as max+1 with no counter
  file and no way to fill a hole (D-A.17, D-A.19); `rfcs.path` in
  `config.yaml` names the one corpus location (D-13.7). The `rfc-index`
  gate is replaced by the shipped `rfc-valid` product gate (D-7.14,
  shadow, origin rfc/0007); a malformed corpus exits 3 per 0007 §3a.
- Charter decomposition (A-17): the corpus conventions extracted to
  RFC 0016 — nineteen `D-A.*` decisions and amendments A-7/A-9/A-10/
  A-14/A-15 relocated with identifiers preserved, plus the prose those
  rules never had; the charter keeps only engine decisions, and `D-A.*`
  is closed to new members. 0007, 0011, 0013 and 0015 gain
  `depends_on: ["0016"]`. `torve rfc check` gains duplicate-heading
  detection and corpus-wide decision-citation resolution (a warning,
  pending the retired-identifier convention), both fence-aware.
- RFC 0015 (source tree structure) adopted and executed: `src/torve` is
  layered — `base/`, `domain/` (task, attempt, states aggregates),
  `application/` (ports, run loop, services), `adapters/<port>/<technology>`,
  `gates/` (standalone, importing only domain/base/config), `config/`,
  `cli/` (one module per verb group) — with every move a `git mv`. The
  layer rules are import-linter contracts in pyproject wrapped by the new
  `layering` gate (worktree input, origin rfc/0015); three dependency
  inversions made them hold: the run loop and reaper receive a store
  factory instead of importing the adapter, forze context wiring moved into
  the TaskStore facade, and `config_hash` moved to `application/telemetry`.
  `torve/__init__.py` is a curated lazy front door (PEP 562) — `import
  torve` no longer touches the runner, adapters or CLI. `gates/base.py` is
  `gates/contract.py`; the D-15.5 module-naming rule (`models`/`utils`/
  `helpers`/`common`/`base` forbidden) joins the `source-layout` gate with
  a sabotage case; layering's own red/green cases live in the repository
  test suite because it is a self-development gate and `import-linter`
  stays a dev dependency (D-15.10).
- RFC 0011 (CLI contract) executed: the CLI is Typer plus Rich (D-11.1,
  closing the Click departure logged at RFC promotion); every
  result-producing command takes `--format json` and emits the persisted
  record shape — `run`/`status` emit the RunState record, `gates run` the
  telemetry record (D-11.2/D-11.3); exit codes 0–5 are the escalation
  vocabulary projected in `torve.domain` with a completeness test (D-11.4,
  `doctor` failures now exit 3, `torve run` distinguishes escalated/2,
  infra/4 and exhausted/5); `--plain` global flag implied by `CI`, non-TTY
  or json, `NO_COLOR` honoured (D-11.5); results on stdout, diagnostics on
  stderr (D-11.6); `torve reap --dry-run` reports without touching anything.
  Presentation polish stays deferred (D-11.8).
- RFC 0013 (configuration layout) executed: every Torve file lives under
  `.torve/` — `gates.yaml`, `config.yaml` (renamed from root `torve.yaml`),
  `tasks/`, `logs/` — with the legacy root-level names resolving as a
  fallback (`src/torve/layout.py`, D-13.1); overrides are the explicit
  `--gates` and `--config` flags, never a second file merged over the first
  (D-13.4); a malformed manifest or runner configuration exits 3 per the
  CLI contract's configuration-error code (D-13.6). This repository took
  the one-move migration, and the sabotage rig seeds the canonical layout.
- RFC 0014 (source file layout, `kind: convention`) adopted: two 27-character
  separators (structural dash, rhythmic dot) extracted from forze; the
  checkable half ships as the `@source-layout` builtin over the diff
  (separator form, post-import dash, dash ceiling, dash labels, label-free
  dots) with five red sabotage cases and a green twin; the whole `src` tree
  swept once (post-import dashes in 33 modules, banners normalized); ruff
  selection matched to forze (adds RUF/ASYNC/C4/ISC/PIE/T20, RUF001-003
  ignored for typographic docstrings); the gate enters `gates.yaml` at
  `shadow` with `origin: rfc/0014`, and the T-0010 corpus-validator gate is
  renamed `rfc-index`.
- Gate lifecycle (amendment A-8 to RFC 0002, D-2.18–D-2.23): §7 added with
  the state machine `proposed → shadow → blocking → quarantined → retired`,
  the five filters, the implementation/activation split, health metrics and
  retirement signals. The `Gate` model's `blocking: bool` is replaced by
  required `state` plus required `origin` (and optional `added`) while no
  manifest exists outside this repository; shadow and quarantined failures
  are recorded but never touch the exit code; bypasses apply only to
  blocking-state gates. The starting set is backfilled (`origin:
  structural`, `self-audit` at `shadow`), and `source-layout` — the corpus
  validator as a gate — enters at `shadow` as the lifecycle's first test.
- Document conventions (amendment A-7, charter D-A.1–D-A.8) applied to the
  repository: MIGRATIONS/CLI-contract/configuration-layout promoted to RFCs
  0011–0013 (identifiers renumbered to `D-11.*`/`D-12.*`/`D-13.*`), the
  skill-specialisation guide moved to `ops/`, `pages/` created for derived
  documentation, amendments dispersed into their primary targets'
  `## Amendments` sections (AMENDMENTS.md deleted), YAML frontmatter on all
  thirteen RFCs, `INDEX.md` generated by `rfc_index.py` and CI-checked,
  the validator hardened per the conventions' checklist (exact table
  header, corpus-unique identifiers, LOCKED globs must match files in
  accepted RFCs, dependency and amendment cross-checks), and task logs
  pinned with `repo`/`base_sha` from T-0009 on.
- Pyright strict as a second blocking checker: `[tool.pyright]` in
  pyproject makes the editor's strict mode repo-canonical, the 91
  Unknown-propagation findings over `src` are fixed (typed yaml/SDK
  boundaries via `cast`, annotated container inits, named providers for
  the store deps, `import_module` for the optional yoyo/opensandbox
  imports), and `uv run basedpyright src` joins CI and the acceptance
  fallback at 0 errors.
- Strict typing as a house gate: `mypy --strict` over `src` (0 errors),
  `py.typed` in the wheel, typed against forze's `DurableRunStorePort` /
  `DurableFunctionHandler` contracts; pytest configuration hardened
  (`--strict-markers --strict-config`, `pytest-timeout` safety net,
  src+tests pythonpath). `uv run mypy src` joins CI and the acceptance
  fallback in `gates.yaml`.

### Changed

- `requires-python` is now `>=3.13,<3.15` (the forze substrate's floor).
- `config_hash` now includes the Torve package version and the pinned forze
  version (D-9.8, from `migrations/substrate/FORZE_VERSION`) — both upgrades
  are regime changes telemetry must see.
