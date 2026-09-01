---
id: "0032"
title: The serve surface
kind: design
status: accepted
implementation: complete
depends_on: ["0007", "0022"]
informed_by: ["0005", "0011", "0013", "0018"]
supersedes: []
superseded_by: null
amended_by: ["A-76", "A-77"]
owner: Lev Litvinov
description: >-
  A local read-only dashboard over the projections the engine already computes — `torve serve` behind an extra, loopback-only, polling JSON the CLI already emits, with the frontend shipped as built assets in the wheel.
schema_version: 1
---

# RFC 0032 — The serve surface

- **Implementation state:** complete (judged 2026-09-01). Phase 1 executed as T-0200 (serve backend, landed e43a2d2 after T-0205's LOCKED D-32.1 blocker forced the status projection to be lifted rather than re-derived); phase 2 as T-0201 (the frontend, landed 536c01c, dashboard smoke-tested live); phase 3 as T-0202 (bundle CI, landed 144a090). D-32.5 settles on the first release build.
- **Scope:** A browser surface for the facts the CLI already projects: the
  board, the programme, cost and token shape, gate health, the findings
  ledger, escalations, attempts in flight. Covers the `torve serve` verb,
  the `torve[serve]` extra, the JSON endpoints (projections re-exposed, no
  new derivations), the polling contract, the asset-shipping pipeline
  (frontend built in CI with the organization's `npm-builder` image, built
  assets shipped as wheel package data), and the security posture. Strictly
  read-only in this document: no endpoint mutates engine state, no approve,
  no dispatch, no kill — the write path is named as future work and
  excluded. No changes to any projection's content; where the browser needs
  a shape the CLI does not emit, the projection gains it for both surfaces
  at once.
- **Related:** [`0007`](0007-planner-context.md) §4 (the context
  projection) · [`0022`](0022-specification-quality.md) (report surfaces) ·
  [`0011`](0011-cli-contract.md) · [`0013`](0013-configuration-layout.md)
  D-13.4 · [`0018`](0018-cli-presentation.md) ·
  `src/torve/application/projections.py` · `src/torve/cli/context.py` ·
  [morzecrew/platform-images](https://github.com/morzecrew/platform-images)
  (`npm-builder`)
- **Origin:** A full dogfooding day spent grepping telemetry and reading
  wrapped 6-column tables in a terminal. The facts were all there; the
  friction was the surface.

---

## 1. Summary

`torve serve` starts a loopback HTTP server (starlette + uvicorn behind the
`torve[serve]` extra, lazy-imported like `torve[opensandbox]`) that exposes
the existing projections as JSON and serves a static single-page frontend —
React + shadcn, shipped pre-built inside the wheel, no node at runtime. The
page polls a couple of endpoints every few seconds and renders the board,
programme, cost/token tables, gate health, findings ledger and escalations
live. Judgement stays in the terminal and the corpus: the surface shows, it
never acts.

## 2. Motivation

The projection layer answers every operational question, and the answers
are unreadable at operational tempo: `torve context` wraps its cost table
into fragments at 80 columns, the findings ledger truncates claims,
watching five parallel dispatches means five `torve status` invocations,
and telemetry questions end in `python3 -c` one-liners against JSONL. Every
fact below is already computed; this document adds zero facts and one
surface.

## 3. Current state

- `src/torve/application/projections.py` computes tasks-by-state,
  escalations, proposals, the findings ledger (D-5.15), gate health, cost
  rows, programme progress and the spec-quality sections — on demand,
  stored nowhere (D-A.12). `torve context --format json` already emits it.
- `torve status` reads live run states; the MCP server re-exposes the same
  projections read-only ("nothing here mutates state").
- The wheel already ships non-Python package data (skills as
  `torve/_skills`; migrations) — the asset-shipping pattern exists.
- Extras with lazy imports and instructive import errors exist twice
  (`opensandbox`, `migrate`); `torve[serve]` is the third verse of the
  same song.
- Nothing in torve or forze serves HTTP today (forze 0.6 ships
  application/domain/base/testing — no server primitives; checked).

## 4. Goals / Non-goals

**Goals**

- Every projection the CLI renders, readable in one browser tab, live.
- Zero node, zero build step, zero configuration at runtime:
  `pip install 'torve[serve]' && torve serve`.
- The projection layer stays the single source — the server is a
  re-exposure, never a re-derivation.

**Non-goals**

- **Writes.** No approve, adopt, dispatch, kill or merge from the browser.
  Each of those is a signature (D-2, D-26.4) with lock discipline behind
  it; a mutating dashboard needs auth and a threat model this document
  refuses to carry. Named as future work in §8, not designed here.
- **Remote serving.** Loopback only, no TLS, no auth — because no
  reachability. Exposing it is the operator's deliberate tunnel, not our
  flag.
- **A live push channel.** Polling every few seconds against files this
  small is indistinguishable from SSE at one-operator scale; a push
  channel is complexity waiting for a second operator.
- **Historical analytics.** The dashboard shows the present projection;
  history stays in telemetry and the regime preimages (D-4.19). Charts
  over history arrive when a reader needs them, on the stage-2 storage
  question's own schedule (D-22.5).

## 5. Design

### 5.1 The verb and the extra

`torve serve [--port 7433] [--config …]` — lazy-imports starlette/uvicorn,
refusing without the extra exactly as the opensandbox adapter refuses
without its SDK. Binds `127.0.0.1` unconditionally; there is no
`--host` flag to get wrong. `--config` follows D-13.4.

### 5.2 Endpoints

```text
GET /api/context     -> the full context projection (existing JSON shape)
GET /api/status      -> live run states (the torve status projection)
GET /                -> the shipped index.html; /assets/* the shipped bundle
```

Two endpoints, both thin calls into functions that already exist. Anything
the frontend needs that these lack is added to the projection itself, so
the CLI and the browser can never disagree (one reader, two renderers —
the A-47 discipline applied to surfaces).

### 5.3 The frontend and its shipping

React + shadcn/ui + Tailwind, dark-first, the glassmorphism treatment; a
single page with sections mirroring the context projection's own order —
board, escalations, findings ledger, proposals, gate health, cost and
token shape, programme. Poll interval a few seconds with a visible
"projected at" stamp, because a dashboard that hides its staleness invents
liveness.

Source lives under `web/` in this repository. CI builds it with the
organization's `npm-builder` image and places the bundle at
`src/torve/_web/`, which ships as package data — the exact skills-data
mechanism. A development checkout without a bundle gets an instructive 404
naming the build command. The Python wheel never grows a node dependency;
`npm-builder` stays a CI concern.

### 5.4 Security posture, stated plainly

Loopback bind, read-only handlers, no credentials held or displayed (the
projections already carry names-not-values everywhere — D-4b upstream of
this surface), no request touches the engine lock. The one real risk is a
future contributor adding a convenient POST; D-32.2 exists to make that a
corpus conversation instead of a patch.

### Alternatives considered

- **Terminal TUI (textual).** Solves wrapping, not tempo — five parallel
  runs still mean juggling panes, and the cost table still cannot chart.
  The browser is where a glanceable surface lives.
- **Rich HTML export on a timer.** No liveness, and the export diverges
  from the CLI the first time someone styles it separately.
- **FastAPI.** Starlette alone suffices for two GET routes and static
  files; FastAPI's validation layer validates nothing here.
- **Bundling node at runtime / building on install.** Refused outright:
  install-time builds are the least reproducible moment in packaging, and
  the wheel-data pattern already works twice.

## 6. Tests

- Endpoint family (starlette TestClient, no real socket): `/api/context`
  and `/api/status` return the projection functions' output verbatim;
  loopback-only configuration asserted; missing-bundle 404 is instructive.
- The extra's refusal without starlette installed, mirrored from the
  opensandbox import-guard test.
- Frontend logic stays out of pytest: the bundle is an artifact, its
  correctness is visual, and pretending otherwise buys a jsdom dependency
  to test a poll loop.

## 7. Docs

README gains the serve section: install line, the verb, the loopback
statement, one screenshot. The security posture sentence (§5.4) is copied,
not paraphrased.

## 8. Out of scope

- **The write path** (approve/adopt/kill from the browser) — the escape
  hatch is named: it arrives only with its own RFC carrying auth and the
  lock discipline, never as an endpoint slipped into this one.
- **Multi-root fleet view** — the fleet manifest makes it thinkable;
  wanting it is evidence the single-root page earned its keep first.
- **Push updates (SSE/WebSocket)** — when polling measurably hurts, which
  at one operator it will not.

## 9. Risks

- **The dashboard drifts from the CLI** — mitigated structurally: both
  render the same projection functions, and §5.2 forbids server-side
  derivations.
- **Read as a product surface.** It is an operator's instrument panel;
  the README screenshot should look like one, not like a landing page.
- **The bundle rots in-tree** if committed — preferred shape is
  CI-attached, and if vendored, the CI check compares the bundle hash to
  the source the way INDEX.md is checked against frontmatter.

## 10. Unresolved questions

- D-32.5 (below): whether the bundle is vendored in-repo or CI-attached
  to the wheel build — settled by the first release build, not before.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-32.1 | `LOCKED` | The server re-exposes existing projection functions verbatim and computes nothing of its own; a shape the browser needs is added to the projection, for every surface at once | `src/torve/cli/serve.py` | One reader, many renderers; a server-side derivation is where the dashboard and the CLI start disagreeing |
| D-32.2 | `LOCKED` | v1 is read-only and loopback-only: no mutating endpoint, no host flag, no auth because no reachability; the write path arrives only with its own RFC | `src/torve/cli/serve.py` | A convenient POST added in a patch is an unauthenticated engine-control channel; this row makes it a corpus conversation instead |
| D-32.3 | `ASSUMED` | `torve serve` lives behind the `torve[serve]` extra with starlette and uvicorn, lazy-imported with an instructive refusal — the opensandbox/migrate pattern | `pyproject.toml` `src/torve/cli/serve.py` | Consuming repositories do not pay for a dashboard they do not open |
| D-32.4 | `ASSUMED` | The frontend ships as built assets in wheel package data (`torve/_web`), built in CI by the organization's npm-builder image; runtime and install never touch node | `pyproject.toml` `src/torve/cli/serve.py` | Install-time builds are the least reproducible moment in packaging; the skills-data mechanism already proves the alternative |
| D-32.5 | `OPEN` | Whether the built bundle is vendored in-repo (hash-checked against source like INDEX.md) or CI-attached at wheel build; the first release build settles it | `pyproject.toml` | Guessing before the release pipeline exists would design CI from imagination |
| D-32.6 | `ASSUMED` | The page polls with a visible projected-at stamp; no push channel | `src/torve/cli/serve.py` | A dashboard that hides staleness invents liveness; SSE is complexity waiting for a second operator |

## 12. Phasing

```yaml
- phase: 1
  title: serve-backend
  intent: >-
    The torve serve verb behind the torve[serve] extra: starlette app with /api/context and /api/status re-exposing the existing projections verbatim (D-32.1), loopback-only with no host flag (D-32.2), lazy-imported with an instructive refusal (D-32.3), instructive 404 when no bundle is shipped. Endpoint tests via TestClient, import-guard test mirrored from the opensandbox pattern.
  scope:
    - "src/torve/cli/serve.py"
    - "src/torve/cli/main.py"
    - "pyproject.toml"
    - "tests/test_serve.py"
  acceptance:
    - "uv run pytest tests/test_serve.py"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run ruff check ."
  depends_on: []
- phase: 2
  title: serve-frontend
  intent: >-
    The web/ source tree: React + shadcn single page rendering board, escalations, findings ledger, proposals, gate health, cost and token shape, and programme sections from the two endpoints, polling with a visible projected-at stamp (D-32.6). A local build script produces the bundle into src/torve/_web; committing the bundle is phase 3's CI question, not this phase's.
  scope:
    - "web/**"
    - "src/torve/_web/**"
  acceptance:
    - "uv run pytest tests/test_serve.py"
    - "uv run ruff check ."
  depends_on: [1]
- phase: 3
  title: serve-bundle-ci
  intent: >-
    The npm-builder CI job building web/ into the torve/_web package data on the release path (D-32.4), and the D-32.5 call made from the first real build: vendored-and-hash-checked or CI-attached. The job runs the frontend build and asserts the backend suite stays green against the produced bundle.
  scope:
    - ".github/**"
  acceptance:
    - "uv run ruff check ."
  depends_on: [2]
```

*(Phase 2 split into phases 2–3 on 2026-09-01, pre-mint: sizing routed
the original combined phase `too_large`.)*

## Amendments

### A-76 — 2026-09-01 — the buildless surface (amends §5.3)
**Found the day the first bundle shipped.** The React build produced an
unreadable page — the operator judged it no better than the CLI — and the
rebuild that replaced it needed no framework at all: the whole surface is
one dependency-free HTML file of vanilla JS over the two endpoints, with
tabs, filters, severity badges and live counts. With the framework gone,
the npm-builder toolchain §5.3 prescribed had nothing left to build.

**Changed:** §5.3 in effect — `web/index.html` is the source and the
bundle; `web/scripts/build.sh` copies it into `src/torve/_web/` (D-32.4's
shipping mechanism unchanged), and the serve-bundle job now asserts the
vendored copy matches the source byte-for-byte and runs the backend
suite. D-32.5 is thereby settled as vendored-and-checked — the "first
release build" it waited on turned out to be a copy. The rewrite was
operator work, outside the lane, disclosed here: a design-quality
deliverable the configured executors had already failed once.

**Deliberately unchanged:** every decision row. Read-only, loopback,
projections-verbatim, wheel-shipped — the doctrine held; only the
toolchain died.

### A-77 — 2026-09-01 — the glass dashboard (amends §5.3 again, supersedes A-76's toolchain call)
**Found within hours of A-76.** The buildless page fixed readability and
the operator immediately wanted the rest: real data tables with sorting,
richer filtering, and a surface that looks deliberate. That is a
framework's job after all — the first React attempt failed on design
quality, not on React.

**Changed:** §5.3 in effect — React + Tailwind + TanStack Table under
Vite, eight tabs, sortable columns on every table, per-tab filters and
chips, expandable findings/proposals with evidence blocks, glass panels
over an aurora ground. The build is deterministic per lockfile (verified
by double-build diff), so D-32.5's vendored-and-checked call survives
with `git diff --exit-code` against a fresh CI build replacing the copy
comparison; node stays a CI-and-development concern, the wheel still
ships static data (D-32.4 unchanged). Built as operator work like A-76's
page, disclosed here.

**Deliberately unchanged:** every decision row, again. A-76's lesson
stands as history: the framework was never the problem, and the vanilla
page was the right bridge to knowing what the surface needed.
