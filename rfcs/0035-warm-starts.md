---
id: "0035"
title: Warm starts
kind: design
status: accepted
depends_on: ["0017", "0027"]
informed_by: ["0004", "0033", "0034"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
implementation: complete
description: >-
  The cold-start tax priced and removed where it is safe: a lockfile-keyed baked venv layer, per-slot derived-cache volumes that never enter replays, and the attempt clock scaled per tier.
schema_version: 1
---

# RFC 0035 — Warm starts

- **Scope:** Three bounded changes to attempt economics: a dependency
  layer baked into the battery image keyed to the lockfile
  (`.torve/sandbox/battery/Dockerfile`), an opt-in per-worker-slot
  derived-cache volume mounted by the runtime for live runs only
  (`src/torve/config/runconfig.py`, `src/torve/application/runner.py`),
  and per-tier overrides for the attempt clock
  (`src/torve/config/runconfig.py` TierConfig, read where the runner and
  review lane take their timeouts). Deliberately not covered: any state
  that an agent authors (D-17.7 stands untouched), any warm state in
  shadow replays or evals (they measure the cold truth), and prompt or
  session caching (the broker's and providers' business, RFC 0021).
- **Related:** RFC 0017 (thin images D-17.8, per-slot volumes §2,
  memory policy D-17.7), RFC 0027 (tier configuration evolution), RFC
  0033 (published images carry the same layers), RFC 0004 §6 (the
  telemetry that measures this).
- **Origin:** The 0034 execution chain, 2026-09-01: every attempt of a
  five-surface refactor paid a cold `uv` venv build and cold
  mypy/basedpyright caches before the battery even started; the default
  1200s `agent_timeout` then killed two attempts whose useful work had
  barely begun — deepseek-v4-flash's *green* attempts on the same task
  ran ~900s. The per-attempt wall clocks landed the same day make the
  tax measurable per attempt across the whole department.

---

## 1. Summary

An attempt's first minutes are spent recreating the same derived state
every time: the project venv from an unchanged lockfile, type-checker
caches over a tree that is 99% identical to the last attempt's. This
document removes that tax where removal is provably safe — state that is
*derived from committed inputs and always safe to delete* — and leaves
every other warmth rule intact: images stay thin of harness state,
executors stay memoryless, and replays stay cold. Beside it, the attempt
clock stops being one global number: a tier may carry its own, because
the heavy rung exists precisely for work that needs more of everything.

## 2. Motivation

Measured on this repository, 2026-09-01 (per-attempt wall clocks, RFC
0004 telemetry):

- T-0213's six flash attempts ran 897–1115s wall; the post-review retry
  hit the 1200s cap with `cost: null` — spend invisible, work lost.
  The sonnet retry under the same cap was killed at 19 minutes into an
  attempt whose worktree showed live test runs.
- Every one of those attempts began with `uv` building the venv from
  scratch and mypy/basedpyright walking 100 source files with empty
  caches — a 3–5 minute tax that produces byte-identical results from
  identical committed inputs, seven times over for one task.
- The department has recorded 266 live attempts; at three minutes of
  redundant toolchain work per attempt the tax is roughly 13 hours of
  wall clock and sandbox time to date, before the campaign's replays.

## 3. Current state

Verified at drafting time:

- `.torve/sandbox/battery/Dockerfile` is thin per D-17.8: python, git,
  uv, the docker CLI — no dependency layer; `uv run pytest` inside an
  attempt resolves and builds the venv first.
- `RuntimeConfig.sandbox_timeout: float = 1800` and
  `agent_timeout: float = 1200` (`src/torve/config/runconfig.py`)
  are global; no tier-level override exists. The 0034 chain needed the
  operator to raise both globally in the run configuration.
- Per-slot volumes exist for auth (`auth_volume`/`auth_mount` on
  TierConfig, `{auth_volume}-{worker_slot}` naming, D-4.2) — the
  mount-per-slot mechanism this document reuses is already proven.
- `run_shadow` drives attempts through the same hooks as a live run;
  nothing currently distinguishes what a replay may mount — the
  distinction lands here.

## 4. Goals / Non-goals

**Goals**

- An attempt's toolchain state is warm when its committed inputs are
  unchanged, cold the moment they change, and never a correctness
  input: deleting all warm state must only ever cost time.
- Shadow replays and evals keep measuring the cold truth.
- The heavy rung gets a bigger clock without the operator editing
  global configuration mid-incident.

**Non-goals**

- Agent memory or session state across attempts — D-17.7 stands; a
  cache the agent can read as *history* is memory, so only toolchain
  caches qualify.
- Harness state in images — D-17.8 stands; a dependency layer is
  project state, not harness state, and the distinction is written
  into D-35.1.
- Provider-side prompt caching — already live, RFC 0021's territory.

## 5. Design

### 5.1 The baked dependency layer

The battery image gains one layer:

```dockerfile
COPY pyproject.toml uv.lock /opt/torve/project/
RUN cd /opt/torve/project && uv sync --all-extras --no-install-project
ENV UV_PROJECT_ENVIRONMENT=/opt/torve/project/.venv
```

`uv sync` inside an attempt then starts from a populated environment and
reconciles only the delta — an unchanged lockfile costs seconds, a
changed one rebuilds exactly what changed. The layer is keyed to the
lockfile by Docker's own layer cache, and D-17.1 already folds the image
digest into `config_hash`, so a dependency bump is a visible regime
change with no new machinery. Published images (RFC 0033) carry the same
layer; a stock upstream image still works — the layer is a convenience,
never a requirement (D-33.1's spirit).

### 5.2 The derived-cache volume

A tier may opt into a warm cache:

```yaml
tiers:
  executor:
    cache_volume: torve-cache        # "" (default) = cold, as today
```

The runtime mounts `{cache_volume}-{worker_slot}` at a fixed mount
point and the sandbox environment points the toolchain at it
(`UV_CACHE_DIR`, `MYPY_CACHE_DIR`, and the basedpyright/ruff cache
homes). Slot-scoped like auth volumes: two concurrent workers never
share a cache. The volume holds derived data only — the mount point is
outside the workspace, nothing in it is readable as project history,
and the doctrine test is D-35.2: *deleting the volume must change
nothing but wall clock*. Shadow replays and evals never mount it
(D-35.3) — `run_shadow`'s hooks pass the exclusion, and an eval
comparing warm arms would be measuring the cache, not the candidate.

### 5.3 The tier clock

TierConfig gains two optional overrides:

```yaml
tiers:
  executor.heavy:
    agent_timeout: 3600
    sandbox_timeout: 4200
```

Absent means the RuntimeConfig global, exactly as today. The runner and
the review lane read the resolved tier's values — the reviewer seat may
therefore carry its own clock too, which A-78's battery-running reviews
will want. A timeout on a tier the task never runs under is inert; the
attempt's telemetry row already stamps the tier that ran, so the clock
in force is always derivable from the record.

### Alternatives considered

- **Warm the workspace itself between attempts** (keep the worktree,
  caches in place) — rejected: an attempt that inherits the previous
  attempt's tree inherits its mistakes; restart-from-base is the
  poison-ceiling doctrine's foundation, not a performance bug.
- **One shared cache volume for all slots** — rejected: cross-slot
  contention and a poisoned-cache blast radius spanning workers, to
  save volumes that cost nothing.
- **Scale the timeout by declared character (RFC 0034)** — rejected
  for now: the tier is already the routing unit and 0034's characters
  route *to* tiers; a second timeout source would give one attempt two
  masters.

## 6. Tests

Image: a build-time assertion that the layer's venv resolves
(`uv sync --check` style) and a conformance case proving an attempt with
an unchanged lockfile performs no package downloads. Volume: runtime
adapter tests for slot-suffixed naming and the fixed mount; a shadow
test pinning that replays receive no cache mount even when the tier
names one. Clock: runconfig resolution tests (tier override wins, absent
falls to global) and a runner test that the resolved value reaches the
agent context. The wall-clock telemetry needs no new tests — it is the
measurement, already landed.

## 7. Docs

The sandbox provisioning page gains the warm/cold table: what is warm
(dependency layer, derived caches), what is never warm (agent state,
replay runs), and the delete-is-always-safe rule. 0017's D-17.8 gains a
dated note pointing here rather than a rewording — thin-of-harness-state
was always the rule, and the dependency layer does not touch it.

## 8. Out of scope

- Warm state for opensandbox runtimes — the volume mechanism is
  Docker-shaped; the opensandbox adapter refuses `cache_volume` loudly
  until a server-side analog exists (named as the escape hatch).
- Cache eviction policy — a derived cache is safe to delete, so the
  operator's `docker volume rm` is the eviction policy until size is a
  measured problem.
- Baking the repowise index or any per-repo intelligence into images —
  the campaign's candidate images stay experiment-local (D-27.7 governs
  their promotion).

## 9. Risks

- **A poisoned cache misleads a run** — bounded by D-35.2's rule:
  toolchain caches are validated by content hashes upstream (uv) or
  invalidated by mtime/config (mypy); the failure mode is a slow
  attempt, and the remedy is deletion. Accepted.
- **The dependency layer drifts from the lockfile** — impossible by
  construction: the layer is rebuilt whenever the copied lockfile
  changes, and the digest change is a recorded regime change.
- **Warm live runs vs cold replays skews comparisons** — deliberate
  and documented: replays measure the candidate, not the cache, and
  the wall-clock telemetry stamps both so the difference is visible,
  never hidden.

## 10. Unresolved questions

- Which cache homes beyond uv/mypy earn a place at the mount
  (basedpyright's, ruff's, pytest's) — implementation measures each
  and logs the roster it settles on.
- Whether the review lane's timeout should default to a fraction of
  the executor's rather than the global — settled when A-78's
  battery-running reviews produce their first wall-clock data.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-35.1 | `LOCKED` | Warm state is derived state: only artifacts rebuilt deterministically from committed inputs (the dependency layer from the lockfile, toolchain caches from the tree) may cross attempts, and deleting all of it may change nothing but wall clock | `.torve/sandbox/battery/Dockerfile` `src/torve/application/runner.py` | Anything an agent authors stays per-attempt (D-17.7 untouched); a cache that fails the delete test is memory and is refused |
| D-35.2 | `ASSUMED` | The battery image bakes a lockfile-keyed dependency layer (`uv sync` at build, `UV_PROJECT_ENVIRONMENT` fixed); a lockfile change rebuilds the layer and the digest change is the visible regime change (D-17.1) | `.torve/sandbox/battery/Dockerfile` | — |
| D-35.3 | `LOCKED` | Shadow replays and evals never mount warm state: a tier's `cache_volume` is ignored under `shadow=True`, so replays measure the cold truth and an eval never compares caches | `src/torve/application/shadow.py` `src/torve/application/runner.py` | Live-vs-replay wall clocks differ by the warm delta by design; the telemetry stamps both |
| D-35.4 | `ASSUMED` | `cache_volume` on a tier opts into a slot-suffixed derived-cache volume at a fixed mount outside the workspace, with toolchain cache homes pointed at it; empty (the default) is cold exactly as today | `src/torve/config/runconfig.py` `src/torve/adapters/runtime/docker.py` | — |
| D-35.5 | `ASSUMED` | The opensandbox runtime refuses a tier naming `cache_volume` loudly until a server-side analog exists — never a quiet cold fallback | `src/torve/adapters/runtime/opensandbox.py` | — |
| D-35.6 | `ASSUMED` | TierConfig gains optional `agent_timeout` and `sandbox_timeout`; absent falls through to the RuntimeConfig globals; the runner and the review lane read the resolved tier's values | `src/torve/config/runconfig.py` `src/torve/application/runner.py` `src/torve/application/review.py` | The clock in force is derivable from the attempt row's stamped tier |

## 12. Phasing

Phase 1's units are disjoint and parallel: the clock is pure
configuration plumbing, the layer is pure image work. Phase 2 is the
volume — the largest surface and the only one touching replay
semantics.

```yaml
- phase: 1
  title: the tier clock
  intent: >-
    Optional agent_timeout and sandbox_timeout on TierConfig (D-35.6),
    absent falling through to the RuntimeConfig globals; the runner's
    attempt hook and the review lane read the resolved tier's values,
    so the heavy rung and the reviewer seat carry their own clocks.
    Resolution tests pin override-wins and absent-falls-through, and a
    runner test pins the resolved value reaching the agent context.
  scope:
    - src/torve/config/runconfig.py
    - src/torve/application/runner.py
    - src/torve/application/review.py
    - tests/test_runconfig.py
    - tests/test_runner.py
    - tests/test_review_run.py
  acceptance:
    - uv run pytest tests/test_runconfig.py tests/test_runner.py tests/test_review_run.py
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
- phase: 1
  title: the baked dependency layer
  intent: >-
    The battery image bakes the lockfile-keyed dependency layer
    (D-35.2): pyproject and uv.lock copied, uv sync --all-extras
    --no-install-project at build, UV_PROJECT_ENVIRONMENT fixed so an
    attempt's uv run reconciles the delta instead of rebuilding. The
    layer is a convenience, never a requirement — a stock image still
    works. A conformance case proves an unchanged lockfile performs no
    package downloads inside an attempt.
  scope:
    - .torve/sandbox/battery/Dockerfile
    - tests/test_sandbox_images.py
  acceptance:
    - uv run pytest tests/test_sandbox_images.py
    - uv run torve rfc check
- phase: 2
  title: the derived-cache volume
  intent: >-
    cache_volume on TierConfig (D-35.4): slot-suffixed naming like the
    auth volume, fixed mount outside the workspace, toolchain cache
    homes (uv, mypy, and the roster implementation settles per §10)
    pointed at it by the runtime adapter. Shadow replays and evals
    never mount it (D-35.3) — a shadow test pins the exclusion even
    when the tier names a volume. The opensandbox adapter refuses the
    field loudly (D-35.5). Deleting the volume changes nothing but
    wall clock (D-35.1) — asserted by a conformance case running the
    same battery cold and warm.
  scope:
    - src/torve/config/runconfig.py
    - src/torve/application/runner.py
    - src/torve/application/shadow.py
    - src/torve/adapters/runtime/docker.py
    - src/torve/adapters/runtime/opensandbox.py
    - tests/test_runconfig.py
    - tests/test_runner.py
    - tests/test_shadow.py
    - tests/test_sandbox_images.py
  acceptance:
    - uv run pytest tests/test_runconfig.py tests/test_runner.py tests/test_shadow.py tests/test_sandbox_images.py
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
  depends_on: [1]
```
