---
id: "0055"
title: Standing decisions
kind: design
status: accepted
implementation: complete
depends_on: []
informed_by: ["0053"]
supersedes: []
superseded_by: null
amended_by: ["A-157"]
owner: misery7100
description: >-
  The standing baseline for this repository after RFC 0053's archive: the rows that govern the tree as it is, drafted through the brownfield lane from the survey, the gate record and the archive, for the owner to grade and accept.
schema_version: 1
---

# RFC 0055 — Standing decisions

- **Scope:** The baseline for this repository — the standing rows the tree,
  the gate record and the archived corpus imply, drafted in a supervised
  session through the corpus-bootstrap skill (D-31.2). From acceptance these
  rows govern work minted after it, never the tree as it stands and never
  its history (D-31.3). No phasing: this document is not a plan. Every row
  carries paths read from the tree; every row's provenance points into the
  archive through `decision-details`, so "why" is one `torve rfc show`
  away and the words that argued it are never repeated here.
- **Related:** `archive/rfcs/` (fifty-two documents, every identifier still
  resolving), `.torve/telemetry.jsonl` (745 attempt rows, the conviction
  record), `.torve/gates.yaml`, `pyproject.toml` (the import-linter
  contracts), RFC 0053 (the model and the archive), RFC 0054 (the backends
  these rows will render into).
- **Origin:** Drafted 2026-09-09 by the planning session as the supervised
  act RFC 0053 phase 4 reserves, from three evidence sources the skill's
  doctrine names two of: the tree, the survey of the last forty landings on
  `main` (§2), and — the source a stranger's repository never has — the
  gate record of 745 attempts and the archived rows a human already graded.

---

## 1. Summary

RFC 0053 archived a corpus of 597 rows in fifty-two documents, half of
them never cited and a third never inherited. This document is what stands
in their place: roughly sixty rows, grouped by the boundary each one
guards, each naming the paths it governs today and citing the archived row
or document it descends from.

The grading follows the skill's doctrine and departs from it in one
declared way. `LOCKED` is given only where the repository's own history
shows the boundary being defended: a gate that convicted attempts and saw
the corrections land (scope 32 times, decisions-reported 61,
user-facing-text 28, layering 2, source-layout 4), or a boundary the tree
holds as structure with a check that has never let it slip (the five
import-linter contracts, the sabotage battery in CI, the authority table on
the record). Everything else is `ASSUMED`, including rows the archive held
as `LOCKED` for a month: the archived grade is provenance, not evidence,
and the acceptance edit is where the owner's judgement re-enters. No row is
`OPEN`; the four questions the extraction could not settle are in §3, named.

The departure: the skill extracts rows from fired gates and corpus gaps
alone, and would produce five rows here. This repository is not a
stranger's — it has an engine, a record and a graded corpus behind it — so
the rows below also draw on the code's own structure and the archive, and
§2 says which source each group rests on. D-31.6 leaves the baseline's
shape to the first adoption; this is the first adoption, and the shape it
chose is one document with provenance in the fence.

## 2. The evidence

**The gate record.** Over 745 attempt rows, the battery convicted:
`decisions-reported` 61, `coverage-delta` 58 (shadow), `scope` 32,
`rfc-valid` 29, `user-facing-text` 28, `acceptance` 19, `self-audit` 8
(shadow), `source-layout` 4 (shadow), `layering` 2. Every conviction was
followed by a correction that landed green under the same gate — the
attempt loop's whole design — which is the doctrine's strongest evidence
for a defended boundary. The gates that never convicted (`secrets`,
`no-test-tampering`) hold boundaries visible in the tree (`.env` ignored,
names-never-values in every configuration file, tests under `tests/`),
which is the doctrine's second kind of evidence.

**The tree.** Five layers under `src/torve/` with five import-linter
contracts over the whole package; a gate manifest naming a sabotage twin for
every entry and a CI job that proves each can fail; an event vocabulary of
twenty-one kinds with an authority table refusing a writer that may not;
adapters one directory per port; configuration that carries variable names
and never values.

**The archive.** 535 archived rows still name paths that exist. The rows
below are the subset that a contract minted tomorrow would need: the ones
that say what an executor may not do, where a boundary lies, or what a
reader may rely on. A row that described an increment's design rather than
a standing rule stays in the archive, resolvable and retired.

**The survey.** `torve survey --last 40` over `main` was running when this
draft was written and its findings are appended to this section when it
lands; the extraction expects it to confirm the gate record above (the
replay runs the same battery over the same landings) and to add nothing the
record does not already show.

## 3. Questions the extraction could not settle

- **Whether `secrets` and `no-test-tampering` deserve `LOCKED`.** Neither
  has ever convicted; both hold boundaries the tree shows. The doctrine
  admits a consistent clean record with structure as lock evidence, and the
  draft grades them so. The owner may prefer `ASSUMED` until one fires.
- **Whether the typing floor is a standing row or a CI fact.** `mypy
  --strict` and `basedpyright` strict over `src/` block CI and the
  acceptance fallback; nothing in the archive graded it. Drafted `ASSUMED`
  as an invariant with its check.
- **Whether RFC 0053's own rows should be restated here.** They stand in
  the corpus path already and are inherited by path intersection; restating
  them would be the duplication D-A.4 forbids. Not restated.
- **Whether the baseline is one document or several.** One, per the
  skill's recorded shape; the rows below are grouped so a later split by
  area is a cut, not a rewrite.

## Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-55.1 | `LOCKED` | Models never decide what work exists or whether it is finished; the runner executes state transitions from facts — exit codes, gate outcomes, approvals — and an agent reports observations that never cause a transition | `src/torve/application/runner.py` `src/torve/application/manager.py` `src/torve/domain/states.py` | A reviewer's severity is data whose consequence configuration sets; the planner invokes no model at all |
| D-55.2 | `LOCKED` | Gates execute outside the agent session against the working tree it leaves behind; an agent cannot report a gate outcome | `src/torve/gates/**` `src/torve/application/runner.py` | The battery is the acceptance; a green the agent claims is not one |
| D-55.3 | `LOCKED` | The sandbox is the unit of lifecycle; the engine never executes agent code on the host | `src/torve/adapters/runtime/**` `src/torve/application/session.py` | Killing a worker costs its lease and nothing else |
| D-55.4 | `LOCKED` | Agents hold no provider credentials; the broker injects them at its own boundary, routes providers at the wire and meters spend; every configuration file carries variable names, never values | `src/torve/adapters/broker/**` `src/torve/config/runconfig.py` `.torve/config.yaml` | A sandbox that is compromised leaks nothing it was not given; a committed file never holds a secret |
| D-55.5 | `LOCKED` | Agents do not communicate; executor memory is off by default, per slot when on, and never mounted in a shadow run | `src/torve/application/shadow.py` `src/torve/config/runconfig.py` | Shared memory is a communication channel with a euphemism; a remembered replay is an incomparable number |
| D-55.6 | `ASSUMED` | Escalation reasons are a closed enum mapped to exit codes; `locked_conflict` and `underspecified` are halts on working judgement, one indicting the code and the other the contract | `src/torve/domain/states.py` `src/torve/cli/options.py` | A new reason is an amendment and a code path, never a free string |
| D-55.7 | `LOCKED` | A task contract is immutable once minted: a changed contract is a new task, a re-mint is a version and never a transition, and a task in flight is never re-minted | `src/torve/domain/task.py` `src/torve/application/planner.py` | An executor's contract cannot drift out from under it; `torve plan` refuses to re-mint over minted phases and leaves the decision to a person |
| D-55.8 | `ASSUMED` | Contracts are derived artefacts committed under `.torve/tasks/`; the task directory is the importer and the record is what dispatch reads | `.torve/tasks/**` `src/torve/application/taskstore.py` | Reproducible from a sha, refusable in a diff; the file is authored, the record is read |
| D-55.9 | `LOCKED` | A contract copies its rows' grade, text and paths at mint time; a grade is never resolved at read time | `src/torve/application/planner.py` `src/torve/domain/task.py` | A regrade never rewrites the judgement of a task that ran under the old grade |
| D-55.10 | `LOCKED` | Silence is a finding: a `LOCKED` row whose declared paths a diff touches without a log entry fails `decisions-reported` | `src/torve/gates/decisions_reported.py` `.torve/tasks/**` | Sixty-one convictions and their corrections are the record of this boundary being held |
| D-55.11 | `ASSUMED` | A divergence entry is written only through `torve log divergence`, validated by the gate's own checks at intake, with located evidence; `drift_count` is derived, never declared | `src/torve/application/divergence.py` `src/torve/cli/log.py` `.torve/tasks/**` | A hand-edited log is refused by the same code that gates it |
| D-55.12 | `ASSUMED` | An empty decision list on a contract is legal and explicit; "none apply" is distinct from "field forgotten" | `src/torve/gates/decisions_reported.py` `src/torve/domain/task.py` | The document-less lane is governed by standing inheritance, not by silence |
| D-55.13 | `ASSUMED` | Evidence must locate — a `path:line` inside the repository or a command with its output — and a finding or entry whose evidence does not locate is discarded unread | `src/torve/gates/evidence.py` | Locating eliminates fabricated coordinates, not fabricated claims; the reviewer and the log share the rule |
| D-55.14 | `ASSUMED` | The gate manifest lives in the consuming repository at `.torve/gates.yaml`; builtins are named `@name`; every entry carries `state` and `origin`; a new gate enters at `shadow` and is promoted on soak evidence | `.torve/gates.yaml` `src/torve/config/manifest.py` | Promotion is a separate act with a number behind it, never a default |
| D-55.15 | `LOCKED` | Every gate names a sabotage twin that proves it can fail; a manifest that names any twin refuses a twinless entry; `torve gates check` runs the battery in CI | `src/torve/gates/sabotage.py` `.torve/gates.yaml` `.github/workflows/ci.yml` | A gate nobody has seen fail is decoration; the battery carries zero grandfather exceptions |
| D-55.16 | `ASSUMED` | Every gate runs on every pass, cheapest first, never short-circuiting; the retry rung reads outcome and axis only, never a trace or a gate's output | `src/torve/gates/runner.py` `src/torve/application/session.py` | A replay of the rows reproduces the tier choice exactly; the severity ladder stays meaningful |
| D-55.17 | `LOCKED` | Scope is a contract: `allow` and `deny` globs make a touch outside them a red gate, and tasks whose allow sets intersect are never dispatched in parallel | `src/torve/gates/scope.py` `src/torve/application/manager.py` | Thirty-two convictions and their corrections; a collision is refused at dispatch, never discovered at merge |
| D-55.18 | `LOCKED` | Secrets never enter the tree; the `secrets` gate reads added lines and is the one gate no bypass trailer can lift | `src/torve/gates/secrets.py` `.gitignore` | The boundary has never been crossed in 745 attempts and the tree holds it as structure |
| D-55.19 | `ASSUMED` | An existing test is edited only under the contract's licence; adding a test is an addition, editing one needs scope | `src/torve/gates/no_test_tampering.py` `tests/**` | A green earned by weakening the suite is a red |
| D-55.20 | `ASSUMED` | Acceptance is a list of shell commands, exit 0 meaning satisfied, run in the battery sandbox over a worktree without a repository; a command that needs git cannot be an acceptance command | `src/torve/gates/acceptance.py` `src/torve/application/intake.py` | The lint refuses such a command at mint rather than after three attempts |
| D-55.21 | `ASSUMED` | A bypass is a `Torve-Bypass: <gate>: <reason>` commit trailer, counted per gate in telemetry | `src/torve/gates/runner.py` `src/torve/application/telemetry.py` | Every waiver is a recorded fact with a name on it |
| D-55.22 | `LOCKED` | User-facing strings carry no corpus coordinates: whoever runs the command has no corpus to resolve them | `src/torve/gates/user_facing_text.py` `src/torve/cli/**` | Twenty-eight convictions and their corrections; the audience rule is a gate, not a style |
| D-55.23 | `LOCKED` | Five layers — `base`, `domain`, `application`, `adapters`, `cli`, beside `gates` and `config` — with import directions enforced by import-linter over the whole package; the `layering` gate blocks | `src/torve/**` `pyproject.toml` | The hexagon is visible in the tree and mechanically held |
| D-55.24 | `LOCKED` | `gates` imports only `domain`, `base` and `config`; the gates-only install stands alone | `src/torve/gates/**` `pyproject.toml` | The first shippable increment keeps shipping alone |
| D-55.25 | `LOCKED` | Adapters never import each other and are organised `adapters/<port>/<technology>.py` | `src/torve/adapters/**` | Swapping one adapter is never a rewrite of another |
| D-55.26 | `LOCKED` | The specification format terminates at the planner: gates, runtime adapters and agent adapters never import its owner | `pyproject.toml` `src/torve/gates/**` `src/torve/adapters/**` | Format containment cannot break quietly; the contract is the only thing a gate reads about a task |
| D-55.27 | `ASSUMED` | No module is named `models`, `utils`, `helpers`, `common` or `base` below the package root; modules are named for what they hold, and the `source-layout` gate reads it | `src/torve/**` | Names that admit anything accumulate everything |
| D-55.28 | `ASSUMED` | A module named after a CLI verb lives under `cli/` and holds parsing and rendering only; presentation never crosses inward | `src/torve/cli/**` | Logic and presentation stay separable where they are hardest to separate |
| D-55.29 | `ASSUMED` | Application code reaches stores only through injected factories; substrate wiring lives in the facade and `config_hash` beside the telemetry it stamps | `src/torve/application/**` | Anything derived below a port is invisible to simulation |
| D-55.30 | `ASSUMED` | Everything Torve owns in a consuming repository lives under `.torve/`; the gate manifest and the run configuration are separate files; run configuration is read from where the runner was launched and never from the repository under work | `.torve/config.yaml` `.torve/gates.yaml` `src/torve/config/runconfig.py` `src/torve/config/layout.py` | A worked-on repository cannot configure the engine working on it |
| D-55.31 | `ASSUMED` | Providers a repository's contents may reach are enforced at dispatch, before a sandbox exists; an empty default denies every real provider | `src/torve/config/runconfig.py` `src/torve/application/dispatch.py` | Silence is not a policy; it is a closed door |
| D-55.32 | `ASSUMED` | A tier resolves through profiles in the operator's own configuration directory, never the repository under work; a missing profile or an unknown skill refuses rather than falls back | `src/torve/config/runconfig.py` `src/torve/application/skills.py` | Resolution is fail-closed throughout |
| D-55.33 | `ASSUMED` | Configuration routes by nature — identity in the image, task context in the workspace, secrets as environment names, knobs in the command, state on the slot volume — one item, one channel | `src/torve/config/runconfig.py` `src/torve/adapters/agent/harness.py` | A second channel for a secret is a leak; a repository-carried harness config is an injection surface |
| D-55.34 | `ASSUMED` | A stdio MCP server is image content; a remote MCP endpoint is an egress destination under provider routing; no execution sandbox is given a read surface onto the record | `src/torve/config/runconfig.py` `.torve/sandbox/**` `src/torve/cli/mcp.py` | Files in the worktree carry task context; reach into the record is the planner's alone |
| D-55.35 | `ASSUMED` | Sandbox images are digest-pinned inputs to a run; an image the eval ledger has not measured refuses dispatch unless the rebuild hatch is set, and the dispatch records the unmeasured digest | `.torve/sandbox/**` `src/torve/application/dispatch.py` `.torve/evals.jsonl` | Numbers from different images are never silently compared |
| D-55.36 | `ASSUMED` | Skills ship as package data under `skills/` and are materialised role-scoped into the sandbox; a vendored skill lives under `.torve/skills-vendor/`; a name in both refuses, a name in neither is a configuration error | `skills/**` `.torve/skills-vendor/**` `src/torve/application/skills.py` | What an agent knows is versioned and named, never ambient |
| D-55.37 | `LOCKED` | A fact is an event of a closed kind vocabulary with an authority table; an agent may write only `divergence.recorded` and `message.sent`; no update command exists on the record | `src/torve/domain/events.py` `src/torve/application/eventlog.py` | An audit trail that can be edited cannot be trusted; the authority table refuses before any store sees the write |
| D-55.38 | `ASSUMED` | The manager holds nothing between passes and rebuilds its view from the record; a worker holds a lease and nothing else | `src/torve/application/manager.py` `src/torve/application/worker.py` `src/torve/application/residency.py` | A restart is a re-read, never a recovery procedure |
| D-55.39 | `ASSUMED` | Retirement of a decision is recorded, never inferred from a row that stopped appearing; a second `decision.recorded` on a subject is a version, `supersedes` names a different decision | `src/torve/application/decisions.py` | Absence cannot tell a retirement from a broken table |
| D-55.40 | `ASSUMED` | A landing is recorded from the `Torve-Task` trailer on the commit; git log is the surviving record and the importer mints landed history from it | `src/torve/adapters/vcs/git.py` `src/torve/application/runner.py` | A session-authored landing enters the record the same way an agent's does |
| D-55.41 | `ASSUMED` | Every attempt appends one telemetry row stamped with the `config_hash` of the regime it ran under; absent counts stay absent, never zero; the verdict is engine-derived and never model output | `src/torve/application/telemetry.py` `.torve/telemetry.jsonl` `.torve/regimes/**` | Numbers from different regimes are never silently compared; a mute row is a mute row |
| D-55.42 | `ASSUMED` | Every read of the log pages; no reader folds a truncated prefix as if it were the whole | `src/torve/application/eventlog.py` | The board once decided dispatch over a third of its tasks missing |
| D-55.43 | `ASSUMED` | Migrations are owner-grouped, forward-only SQL under `migrations/`; the substrate is pinned by `FORZE_VERSION`, which `torve doctor` enforces and `config_hash` digests | `migrations/**` `src/torve/application/migrate.py` `src/torve/cli/doctor.py` | A substrate surface change fails at the pin bump, not in production |
| D-55.44 | `ASSUMED` | `torve plan` is deterministic and invokes no model; it mints from exactly one accepted, committed document, copies that document's whole table, and refuses to re-mint over minted phases | `src/torve/application/planner.py` `src/torve/cli/plan.py` | What to do with existing tasks is a human decision |
| D-55.45 | `ASSUMED` | Nothing inherits from a document that is not `accepted`; a contract without a document inherits every accepted row whose declared paths intersect its `scope.allow` | `src/torve/application/planner.py` `src/torve/application/intake.py` | The document-less lane is governed; a draft's rows never stand |
| D-55.46 | `ASSUMED` | A draft passes the deterministic lint before adoption — non-empty intent and acceptance, in-tree globs, a module's test file allowed beside it, no git in acceptance, pairwise-disjoint scopes — and adoption mints identifiers under the engine lock | `src/torve/application/intake.py` `src/torve/application/enginelock.py` | A defect the lint names at drafting costs nothing; the same defect at attempt three cost 2410 seconds once |
| D-55.47 | `ASSUMED` | A contract the size estimate calls `too_large` routes to a decomposition run rather than an attempt; children stay inside the parent's scope, decomposition depth is at most two, and the parent survives as the integration task | `src/torve/application/sizing.py` `src/torve/application/intake.py` | Oversized work is split at drafting time, where the tree is in view |
| D-55.48 | `ASSUMED` | The poison ceiling is checked before dispatch and reaching it escalates, never retries; a red gate pass retries under the ladder's next rung | `src/torve/application/dispatch.py` `src/torve/application/session.py` `.torve/config.yaml` | An attempt is spent on a chance, never on a certainty |
| D-55.49 | `LOCKED` | Review is a second run role in its own sandbox; the reviewer never receives the author's trace; findings are one JSON document with located evidence, a blocker escalates the target and the rest go to the operator's ledger | `src/torve/application/review.py` | Reviewer independence is from the author's reasoning; engine facts about the diff are not the author's |
| D-55.50 | `LOCKED` | The engine never resolves a merge conflict and never lands without the configured approval; landings serialize through one lane per base | `src/torve/application/lane.py` `src/torve/application/residency.py` | A conflict against an unmoved base is the same conflict, and re-queueing it is a loop |
| D-55.51 | `ASSUMED` | A landing commit carries `Torve-Task`, `Torve-Attempt`, `Torve-Agent`, `Torve-Config` and `Torve-Decisions` trailers; provenance is written by the runner, never by the agent | `src/torve/application/runner.py` | What landed, under which regime, inheriting which rows, is readable from git alone |
| D-55.52 | `ASSUMED` | Documentation under `pages/` is written independently of the corpus — never generated from it, never contradicting an accepted row — and `INDEX.md` is generated and drift-checked, never hand-edited | `pages/**` `rfcs/INDEX.md` | Two axes, versioned differently; a lockfile-grade index cannot drift |
| D-55.53 | `ASSUMED` | Commit messages follow gitmoji plus Conventional Commits with the type taken from the mapping file, checked by the skill's script; a body states why, in at most twenty non-blank lines | `.agents/skills/gitmoji-conventional/**` | Release tooling reads the type; `git blame` reads the body |
| D-55.54 | `ASSUMED` | CI is the battery: `torve gates check` proves every gate can fail and `torve gates run --base origin/main` is the acceptance of every push; a human pull request runs the manifest's fallback commands | `.github/workflows/ci.yml` `.torve/gates.yaml` | There is no second definition of green |
| D-55.55 | `ASSUMED` | The web bundle is vendored under `src/torve/_web/` and the release job rebuilds it from `web/` and fails when the committed bundle differs | `src/torve/_web/**` `web/**` `.github/workflows/publish.yml` | A stale bundle cannot ship silently |
| D-55.56 | `ASSUMED` | Cadence belongs to the manager's pass; there is no resident scanning loop, and the standing legs — maintenance, relay, lane — run inside the pass under their switches | `src/torve/application/residency.py` `src/torve/cli/manager.py` | One process to run and supervise; a pause stops what advances the repository |
| D-55.57 | `ASSUMED` | Standing maintenance is a committed contract under `.torve/standing/` with a trigger kind, cooldown, `max_open` and `strike_limit`, evaluated deterministically | `.torve/standing/**` `src/torve/application/standing.py` | A job that fires is a fact with a bound, never a daemon's habit |
| D-55.58 | `ASSUMED` | The review corpus under `.torve/review-corpus/` grows from post-merge escapes, and reviewer regressions are measured by replaying it | `.torve/review-corpus/**` `src/torve/application/review.py` | A reviewer's calibration is a measured quantity |
| D-55.59 | `ASSUMED` | Strict typing is a floor over `src/`: `mypy --strict` and `basedpyright` strict block CI and the acceptance fallback; tests, scripts and skills carry no type floor | `src/torve/**` `pyproject.toml` | A substrate surface change fails at the type check |
| D-55.60 | `ASSUMED` | Every attempt runs on a worktree seeded with the base sha pinned host-side, the role's skills materialised, and the divergence log seeded; the session trace is captured to `.torve/traces/` and its content enters no prompt and drives no control flow | `src/torve/application/session.py` `src/torve/adapters/agent/harness.py` `.torve/traces/**` | What an agent saw is reconstructible; what it reasoned is never an input to another agent |

```yaml decision-details
- id: D-55.1
  cites: [D-2, D-38.2]
- id: D-55.2
  cites: [D-3]
- id: D-55.3
  cites: [D-4]
- id: D-55.4
  cites: [D-4b, "0021"]
- id: D-55.5
  cites: [D-31, D-17.7]
- id: D-55.6
  cites: [D-11.4, A-21, A-22]
- id: D-55.7
  cites: [A-4, "0049"]
- id: D-55.8
  cites: [A-4, "0049"]
- id: D-55.9
  cites: [D-7.22, D-22.2]
- id: D-55.10
  cites: [D-32]
- id: D-55.11
  cites: [D-2.10, "0045"]
- id: D-55.12
  cites: [D-7.5]
- id: D-55.13
  cites: [D-2.10]
- id: D-55.14
  cites: [D-2.5, D-2.18]
- id: D-55.15
  cites: [D-36.3]
- id: D-55.16
  cites: [D-34.4, D-34.5]
- id: D-55.17
  cites: [D-1.7]
- id: D-55.18
  cites: [D-2.8]
- id: D-55.20
  cites: [A-131]
- id: D-55.22
  cites: [D-11.11]
- id: D-55.23
  cites: [D-15.1, D-15.8]
- id: D-55.24
  cites: [D-15.2]
- id: D-55.25
  cites: [D-15.3, D-15.4]
- id: D-55.26
  cites: [D-7.17]
- id: D-55.27
  cites: [D-15.5, D-14.11]
- id: D-55.28
  cites: [D-15.6, D-18.2]
- id: D-55.29
  cites: [D-15.12]
- id: D-55.30
  cites: [D-13.3]
- id: D-55.31
  cites: [D-4.8]
- id: D-55.32
  cites: ["0028", "0029"]
- id: D-55.33
  cites: [D-17.4]
- id: D-55.34
  cites: [D-17.6]
- id: D-55.35
  cites: ["0017", "0027"]
- id: D-55.36
  cites: [D-9.12]
- id: D-55.37
  cites: [D-44.2]
- id: D-55.38
  cites: ["0044"]
- id: D-55.39
  cites: [D-47.1, D-47.2]
- id: D-55.40
  cites: [D-10.4]
- id: D-55.41
  cites: [D-4.6, D-38.2]
- id: D-55.42
  cites: [A-99]
- id: D-55.43
  cites: ["0012"]
- id: D-55.44
  cites: [D-7.22, D-49.1]
- id: D-55.45
  cites: [D-A.10, D-30.5]
- id: D-55.46
  cites: [D-20.3, A-131, A-132]
- id: D-55.47
  cites: ["0026"]
- id: D-55.48
  cites: ["0034"]
- id: D-55.49
  cites: [D-5.3]
- id: D-55.50
  cites: [D-6, D-52.5]
- id: D-55.51
  cites: ["0010"]
- id: D-55.52
  cites: [D-A.1a, D-A.6]
- id: D-55.54
  cites: ["0002"]
- id: D-55.55
  cites: [D-32.5]
- id: D-55.56
  cites: [A-105, A-106]
- id: D-55.57
  cites: ["0023"]
- id: D-55.58
  cites: ["0036"]
- id: D-55.60
  cites: [D-17.4, "0039"]
```

```yaml invariants
- id: I-55.1
  statement: The five layer contracts hold over the whole package
  paths: [src/torve/**, pyproject.toml]
  check: uv run lint-imports --config pyproject.toml
- id: I-55.2
  statement: The corpus and its archive check clean
  paths: [rfcs/**, archive/**]
  check: uv run torve rfc check
- id: I-55.3
  statement: Every gate in the manifest can be made to fail
  paths: [.torve/gates.yaml, src/torve/gates/**]
  check: uv run torve gates check
- id: I-55.4
  statement: The typing floor over src holds
  paths: [src/torve/**]
  check: uv run mypy src && uv run basedpyright src
- id: I-55.5
  statement: The suite is green
  paths: [src/torve/**, tests/**]
  check: uv run pytest
```

## Amendments

### A-157 — 2026-09-09 — the survey landed, and confirms the reading

**`torve survey --last 40` over `main`, window `fbf5e0d`..`ab832c3`.**
Forty landings replayed through the battery. `scope`, `secrets`,
`rfc-valid`, `source-layout`, `user-facing-text` and `layering` measured
clean on all forty. `coverage-delta` fired on eight (it is `shadow`) and
was flaky on one. `acceptance` fired on two: `80096f8` and `a1d0096` — the
second is RFC 0053 phase 3's own landing, whose tree carried the parity
test T-0295 corrected two commits later, which is the doctrine's
defended-boundary evidence for D-55.54 exactly as the record already
showed it. `no-test-tampering`, `decisions-reported` and `self-audit`
skipped all forty with the no-task skip, and the report's `corpus_adds`
names exactly those three: the rows D-55.10, D-55.11 and D-55.19 give
them what to measure once work is minted, as §1 expected. Nothing in the
survey adds or regrades a row.

```yaml changes
- subject: the-evidence
  field: survey
  before: running
  after: "40 landings; 6 gates clean 40/40; coverage-delta fired 8 (shadow); acceptance fired 2 (both corrected); 3 no-task skips = corpus_adds"
```
