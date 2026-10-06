# Torve

A specification-and-gate engine for a standing agent team.

Torve turns a reviewed specification into machine-checkable task contracts,
runs coding agents against them in sandboxes under deterministic gates, and
refuses to let anything land that cannot prove it did what it was told.

Three ideas carry the design:

- **Graded decisions.** A task inherits its specification's decisions, each
  graded `LOCKED`, `ASSUMED` or `OPEN`. The grade dictates what an executor
  does when reality disagrees: halt, depart and log, or decide and log. A
  gate reads the log, so a silent workaround is a red build.
- **Scope as a contract.** Allow/deny globs make "touched something it
  shouldn't have" a failing gate rather than a review comment, and make
  overlapping tasks undispatchable in parallel rather than collidable at
  merge.
- **A closed loop.** Attempts, costs, escalations and review findings
  project back into the planning session that writes the next contracts —
  as a CLI report and as a read-only MCP server.

## Status

0.1 is an alpha with one operator and one adopter so far. It has been run
on Linux with Docker, with Claude Code as the agent seat. The other seats'
images exist and have run far less. Nothing is promised across minor releases yet:
[Stability](https://morzecrew.github.io/torve/latest/reference/stability/)
says what each surface does and does not promise. Report security issues as
[SECURITY.md](SECURITY.md) says.

## Install

```bash
pip install torve                # or: uv tool install torve
pip install 'torve[postgres]'    # the durable run store and the manager's log
pip install 'torve[migrate]'     # `torve migrate`, for that store's schema
pip install 'torve[mcp]'         # the planning session's read surface
pip install 'torve[serve]'       # the loopback dashboard
pip install 'torve[opensandbox]' # the OpenSandbox runtime adapter
```

From a checkout instead: `uv sync`, then `uv run torve ...`.

Upgrading an installed torve is install the release, run `torve init` in
the repository, then `torve doctor` — and `torve migrate --all` when the
release's Upgrade note names a record migration.
[Stability](https://morzecrew.github.io/torve/latest/reference/stability/)
states the steps, what each release's changelog entry promises, and how a
foreign `schema_version` is refused.

Python 3.13 or 3.14, git, and a Docker daemon for anything that runs an
agent. Agents run in Docker sandboxes; the engine never executes agent code
on the host.

## Quickstart

`torve init` writes the JSON Schemas for every file Torve reads, under
`.torve/schemas/`, and the ignore file for what Torve alone writes. It does
not write a configuration or a gate manifest. The smallest useful install is
gates in CI: write `.torve/gates.yaml` and run them.

```bash
torve init
torve gates run --base origin/main   # every gate; the exit code is the outcome
torve gates run --format json        # the same, for a machine
torve gates check                    # the sabotage suite: prove each gate can fail
```

The builtin gates are `scope`, `secrets`, `no-test-tampering`,
`decisions-reported`, `acceptance`, `red-on-base`, `self-audit`,
`source-layout` and `user-facing-text`. Anything else is a shell command in
the manifest. Gates that need a task contract report `skipped` without one,
never a silent green.

[Get started](https://morzecrew.github.io/torve/latest/get-started/) walks the
whole first loop, with a minimal `gates.yaml` and `config.yaml`: a document,
a minted contract, a sandboxed run and a landing.

## Running work

```bash
torve plan S-0001 --no-dry-run   # mint task contracts from an accepted, committed document
torve run T-0001                 # one task, synchronously, sandboxed
torve merge T-0001               # land one ready candidate; omit the id for the whole queue
torve intake "add rate limiting to the fetch path"   # or draft contracts from prose
torve manager serve <partition>  # the resident manager, over a Postgres store
```

`plan` is deterministic and previews by default; no model is ever called
inside the engine. `intake` runs a drafting agent in a read-only sandbox
whose gate is a contract lint, and `torve adopt` is the human signature that
mints what it drafted. `manager serve` is the standing team: it imports
what the repository added, claims one task at a time, executes it, and
records what happened. It holds nothing between passes, so killing it costs
the lease on whatever was in flight and nothing else.

Review is a second run role: a reviewer agent, isolated from the executor,
whose findings gate the merge lane. `torve review pr <number>` reviews a forge
pull request; `torve review corpus` replays a seeded-defect corpus so reviewer
regressions are measurable.

### The store

The manager's event log and the durable run store live in Postgres.
`TORVE_PG_DSN` names the database; it never belongs in a committed file.
`torve migrate --all` applies the schema. From a checkout of this repository,
`just pg-up` starts a local Postgres on `127.0.0.1:15433` and `just migrate`
applies it. `store.adapter: mock` in `.torve/config.yaml` keeps the run store
in-process instead, which is enough for one `torve run` and not for a reaper
that must see another runner's leases.

```bash
torve manager board <partition>                   # what the recorded facts add up to
torve manager note <partition> T-0001 "the flake in test_x is known"
```

## Sandbox images

An agent seat runs in an image built from a definition under `sandboxes/`
in this repository (`claude`, `codex`, `dsh`, `mimo`, `opencode`, and
`battery`, the image this repository's own gates run in). No image is
published to a registry yet: build them
from a checkout with Docker Buildx.

```bash
just image claude                 # tags claude-sandbox:latest
TAG=2.1.283 just image claude     # tags claude-sandbox:2.1.283, the version a harness file names
torve sandbox list                # the definitions and the tag each builds to
torve sandbox digest              # what each tag resolves to on this runtime
```

The engine never builds an image. `torve doctor` checks that an image a seat
names has a reviewed definition beside the configuration: an adopting
repository keeps a copy under `.torve/sandbox/<name>/`.

## Observing

```bash
torve status                     # run records
torve context                    # the planning report: tasks, escalations,
                                 # proposals, gate health, cost by regime
torve ledger                     # cost and attempts per landing, per seat and gate
torve mcp                        # the same facts as a read-only MCP server
torve doctor                     # configuration and environment checks
torve shadow T-0001              # replay landed work for harness comparison
```

Every attempt appends one telemetry record stamped with a `config_hash` of
the regime it ran under — gates, skills, model, image digests — so numbers
from different regimes are never silently compared.

## Configuration

Everything lives under `.torve/` in the repository the runner is launched
from, and every file's first line names its schema:

- `config.yaml`: the runtime, the store, the seats (`tiers`), the broker,
  budgets and the landing policy.
- `gates.yaml`: the gate manifest.
- `harnesses/<name>.yaml`: how a model is reached. That is the adapter and
  the image that is the harness's identity.
- `providers/<name>.yaml`: where a provider is served, and the *name* of the
  variable holding its credential, never the value.
- `agents/<name>.yaml`: what an agent is given. That is its equipment and the
  working rules it appends.

A seat in `config.yaml` joins them and adds what varies per run:

```yaml
tiers:
  executor:
    harness: claude-subscription
    profile: claude-equipped
    model: claude-opus-5-5
    provider: anthropic
```

With `broker.adapter: local`, a sandbox holds no provider key at all: the
broker injects credentials at its own boundary, enforces provider routing
at the wire, and meters spend mid-run. This repository's own `.torve/` is a
working example of every file.

## Design corpus

The design lives in `.torve/specs/` as a numbered, cross-checked corpus of
documents — the same documents Torve plans and builds itself from — with
retired ones under `.torve/archive/`. Each document is a directory `S-NNNN/`
holding up to four YAML files (`document.yaml`, `decisions.yaml`,
`phasing.yaml`, `amendments.yaml`) and an `execution/` directory the landing
writes. `torve spec new` starts one, `torve spec check` validates the corpus,
`torve spec list` routes, `torve spec show S-0006/D-8` resolves any
identifier, and `torve spec render S-0006` writes a page for a person.

The [documentation site](https://morzecrew.github.io/torve/) explains how the
engine works and how to operate it.

## License

See [LICENSE](LICENSE).
