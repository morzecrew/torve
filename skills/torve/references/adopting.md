# Bringing torve to a new repository

Adopt one repository at a time, and start with daytime runs you watch before
serving any night.

## 1. Install and initialise

- Add torve as a development dependency pinned to a minor version, with the
  extras the store needs, for example `torve[postgres,migrate]>=0.N,<0.N+1`.
- `torve init --starter` writes the schemas, the ignore rules, a gate
  manifest, a configuration and the `torve` skill stub. It never overwrites a
  file that exists.

## 2. Gates

- Gates run inside the sandbox image, not on the host, so each command needs
  the toolchain the image carries.
- Replace the starter's commented test gate with the repository's real test,
  lint and typecheck commands.
- Prove them green on the base before any agent runs:
  `torve gates run --base origin/main`. A suite that is red for environment
  reasons (a missing system library, a test that needs a database or the
  network, a proxy variable) is fixed or excluded first. Otherwise every
  attempt inherits the red.

## 3. The sandbox image

The starter names a plain image. Real runs use torve's harness image. When
the repository needs more (a system library, another toolchain), derive an
image from torve's with `FROM` rather than copying its definition. Copied
definitions drift behind the image they came from.

## 4. Store and seats

- The store is Postgres with one partition per repository (`org/repo`). The
  configuration names the environment variable that holds the DSN, never the
  DSN itself. Run `torve migrate --all`.
- `tiers` name the executor and reviewer seats. A reviewer from a different
  model family than the executor catches what the executor's own family
  misses. `providers` lists who may receive the repository's code.
- `torve doctor` checks every seat, image and store before a night spends an
  attempt finding out.

## 5. Landing

- `promotion.landing: pull_request`, `promotion.unit: document` and `scm.repo`
  give one pull request per document, opened as a draft and made ready at its
  last phase.
- `auto_merge: true` lets the served manager run the landing lane on each
  pass. Merging into the base stays a person's act.
- `threads` turns on the review-thread leg for the bots the repository uses.
- Start with the review findings recorded, not blocking (`blocks_at: blocker`).

## 6. The first document: standing decisions

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

## 7. The first run

Pick a small, well-specified feature document with two or three phases. Plan
it, then run one served pass in the daytime and watch it end to end:

```bash
torve manager serve <partition> --passes 1
```

Read every escalation as a lesson about the contract, then serve a night.
