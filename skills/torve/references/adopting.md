# Bringing torve to a new repository

Adopt one repository at a time, and start with daytime runs you watch before
serving any night.

## 1. Install torve as a tool

Install torve as a uv tool, not as a project dependency: torve's pins and the
application's never belong in one lock.

```bash
uv tool install 'torve[postgres,migrate]==X.Y.Z'
```

Every command torve writes or ships for a host or a sandbox calls bare
`torve`, so the tool's install directory is the only thing on `PATH` that
matters. `torve --version` says which release is installed.

## 2. Initialise, and name the release

```bash
torve init --starter       # schemas, ignore rules, a configuration, gates and profiles
```

`torve init` writes the JSON Schemas for every file torve reads and the ignore
rules for what it alone writes, and never overwrites a file that exists.
`--starter` adds a configuration, a gate manifest and the role profiles.

In `.torve/config.yaml`, set `engine: "X.Y.Z"` to the release installed above.
`engine` means at least that release and below the next minor; a command that
loads the configuration under a torve outside the range stops, naming the
installed version, the range and the install command for a release inside it.
`torve --version`, `torve guide` and `torve doctor` still run. A configuration
naming no `engine` loads under any version.

Upgrading later is the same two moves over again: install the release, raise
`engine`. `torve guide torve upgrading` states the rest.

## 3. A nested layout

A repository whose Python project sits in a subdirectory names it once, in
`.torve/gates.yaml`:

```yaml
layout:
  root: app              # the project's directory; the source root is app/src
  carried: [migrations]  # top-level directories that ride with any change
```

Then every check a root project gets — the source-layout gate, the citation
scan, the spec-quality frontier, the coverage fallback and the `tests`
defaults — reads under `app/`, and `migrations/` rides with a change the way
`tests` and `.torve` already do. A project at the repository root declares
nothing and changes nothing.

## 4. Gates

- Gates run inside the sandbox image, not on the host, so each command needs
  the toolchain the image carries.
- Replace the starter's commented test gate with the repository's real test,
  lint and typecheck commands.
- Prove them green on the base before any agent runs:
  `torve gates run --base origin/main`. A suite that is red for environment
  reasons (a missing system library, a test that needs a database or the
  network, a proxy variable) is fixed or excluded first. Otherwise every
  attempt inherits the red.

## 5. The sandbox image

The starter names a plain image. Real runs use torve's harness image, which
carries CPython 3.13 and 3.14. Derive an image only for what the base lacks —
a system library, another toolchain:

```dockerfile
FROM torve-base:latest
RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev
```

Copied definitions drift behind the image they came from; a `FROM` line adds
without copying. `torve doctor` runs each seat image's `torve --version` and
goes red when a rebuilt image runs another torve than the host's.

## 6. Equip the shipped hooks, and move the old ones to CI

The starter's implement profile equips the shipped hooks:

```yaml
- kind: hook
  source: torve:hooks/implement
```

torve's own implement profile and the starter's both equip them, so an upgrade
brings its hooks and no adopter carries a copy. The hooks are the pre-write,
read and finish checks an executing agent runs inside its sandbox.

The repository's own git hooks do not run on torve's work: every git command
torve runs carries `core.hooksPath=/dev/null`, so no repository hook fires on
a commit, rebase, checkout or push torve makes. Move what those hooks checked
into the gate manifest or into CI, where the check runs against a tree torve
did not write from configuration an agent could edit.

## 7. Store and seats

- The store is Postgres with one partition per repository (`org/repo`). The
  configuration names the environment variable that holds the DSN, never the
  DSN itself. Run `torve migrate --all`.
- `tiers` name the executor and reviewer seats. A reviewer from a different
  model family than the executor catches what the executor's own family
  misses. `providers` lists who may receive the repository's code.
- `torve doctor` checks every seat, image and store before a night spends an
  attempt finding out.

## 8. Landing

- `promotion.landing: pull_request`, `promotion.unit: document` and `scm.repo`
  give one pull request per document, opened as a draft and made ready at its
  last phase.
- `auto_merge: true` lets the served manager run the landing lane on each
  pass. Merging into the base stays a person's act.
- `threads` turns on the review-thread leg for the bots the repository uses.
- Start with the review findings recorded, not blocking (`blocks_at: blocker`).

## 9. The first document: standing decisions

A repository with no corpus starts with one document of standing decisions: the
next `S-NNNN/` directory, titled "Standing decisions".
`torve survey --last 40 --format json` replays recent landings through the
gates and reports which gates fired, which stayed clean and which were
skipped for want of a contract. Draft the document from that report and from
the repository's own README, decision records and layout:

- **Mostly `ASSUMED`.** A grade is the owner's judgement about the cost of
  reversal, and nobody has made it yet. A draft that comes back mostly
  `LOCKED` reads as a takeover and gets refused whole.
- **`LOCKED` only on defended boundaries.** The history must show the
  boundary being defended: a crossing that was corrected, or a layout held
  consistently over time. A single firing is `ASSUMED` evidence.
- **Paths on every row**, read from the tree. A row without paths governs
  nothing.
- **No phasing.** This document says how the repository is governed. Work to
  do goes in later documents.

The owner edits the grades, commits the document and accepts it. Only then
does any contract inherit its rows.

## 10. The first run

Pick a small, well-specified feature document with two or three phases. Plan
it, then run one served pass in the daytime and watch it end to end:

```bash
torve manager serve <partition> --passes 1
```

Read every escalation as a lesson about the contract, then serve a night.