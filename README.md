# Torve

A specification-and-gate engine for a standing agent team.

Torve turns a reviewed specification into machine-checkable task contracts,
runs coding agents against them in sandboxes under deterministic gates, and
refuses to let anything land that cannot prove it did what it was told. It is
not an orchestrator: dispatch, worktrees and merge trains are solved
elsewhere. What Torve adds is the layer above — decisions with teeth, scope
as a contract, and a closed loop from execution facts back into planning.

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

## Install

```bash
pip install torve                # gates and runner
pip install 'torve[postgres]'    # durable cross-process run store
pip install 'torve[mcp]'         # the planning session's read surface
pip install 'torve[opensandbox]' # the OpenSandbox runtime adapter
```

Python 3.13+. Agents run in Docker sandboxes; the engine itself never
executes agent code on the host.

## Quickstart: gates in CI

The smallest useful install is one CI step — no runner, no store, no agents:

```bash
torve gates run --base origin/main   # all gates; the exit code is the outcome
torve gates run --format json        # machine-readable results
torve gates check                    # sabotage suite: prove each gate can fail
```

Configuration lives in `.torve/gates.yaml`. Builtin gates: `scope`,
`acceptance`, `no-test-tampering`, `decisions-reported`, `secrets`,
`self-audit`; anything else is a shell command in the manifest. Gates that
need a task contract report `skipped` without one — never a silent green.

## Running work

```bash
torve plan 0021                  # mint task contracts from an accepted spec
torve intake "add rate limiting to the fetch path"   # or draft from prose
torve adopt T-0140               # accept the drafts; ids are minted here
torve run T-0142                 # one task, synchronously, sandboxed
torve merge                      # land ready candidates, serialized
torve tick                       # one bounded pass of the standing loop
```

`plan` is deterministic — no model call ever happens inside the engine.
`intake` runs a drafting agent in a read-only sandbox whose gate is a
contract lint; a human adopts or refuses. `tick` is the standing team: poll
the board, land what is approved, reap, dispatch one task, sync — then exit.
Cadence belongs to cron or a systemd timer, never to a resident daemon.

Review is a second run role: a reviewer agent, isolated from the executor,
whose findings gate the merge lane. `torve review pr` reviews forge pull
requests; `torve review corpus` replays a seeded-defect corpus so reviewer
regressions are measurable.

## Observing

```bash
torve status                     # run records
torve context                    # the planning report: tasks, escalations,
                                 # proposals, gate health, cost by regime
torve mcp                        # the same projections as a read-only MCP server
torve doctor                     # configuration and environment checks
torve shadow T-0142              # replay landed work for harness comparison
torve rfc check                  # validate the specification corpus
torve rfc show D-6.8             # resolve any corpus identifier
```

Every attempt appends one telemetry record stamped with a `config_hash` of
the regime it ran under — gates, skills, model, image digests — so numbers
from different regimes are never silently compared.

## Configuration

Everything lives under `.torve/` in the consuming repository: `gates.yaml`
(the gate manifest) and `config.yaml` (runtime adapter, agent tiers, store,
budgets, promotion policy). Agent harnesses are configured per tier — the
command line, the model, the sandbox image — and provider credentials reach
the engine as environment variable *names*, never values. With the egress
broker enabled, a sandbox holds no provider key at all: the broker injects
credentials at its own boundary, enforces provider routing at the wire, and
meters spend mid-run.

A tier can also name a profile instead of spelling out its adapter, model
and command inline: `profile: <name>` resolves against a file in the
operator's own config directory (`~/.config/torve/agents/<name>.yaml`,
`$XDG_CONFIG_HOME` when set) — never the repository under work — and any
fields set locally on the tier win over the profile's. For example:

```yaml
tiers:
  executor:
    profile: claude-sonnet
```

with `~/.config/torve/agents/claude-sonnet.yaml` holding the adapter,
model and command that tier runs with. `torve doctor` prints which tiers
resolved through a profile.

## Design corpus

The full design lives in `rfcs/` as a numbered, cross-checked RFC corpus —
the same specifications Torve plans and builds itself from. Start with
`rfcs/0001-torve-charter.md`; `rfcs/INDEX.md` routes the rest.

## License

See [LICENSE](LICENSE).
