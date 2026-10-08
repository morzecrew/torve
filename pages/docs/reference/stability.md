# Stability

Torve 0.x is an alpha. Any minor release may change anything on this page,
and when it does, the changelog names the change and the migration. A patch
release changes none of it. This page says which surfaces Torve means you to
build on, so that a change to them is announced, and which are internal and
change without notice.

| Surface | What 0.x promises |
| --- | --- |
| CLI verbs and flags | Renamed or removed only in a minor, with a changelog entry |
| Exit codes | The six codes below keep their meaning; a new code is never a reused one |
| `--format json` output | Versioned by `schema_version`; a shape change is announced in the changelog |
| Files Torve reads under `.torve/` | Versioned by `schema_version`; a shape change is announced with its migration |
| Everything else under `.torve/`, the record's tables, Python imports | **Internal.** No promise |

## The CLI

The verbs are what `torve --help` lists, and each verb's flags are what its
own `--help` lists. A flag on its way out stays accepted, and does nothing,
until the next minor release removes it. `torve run --oversize` is the one
in that state now.

The text a person reads is not an interface. Tables, wording, colour and
ordering improve between releases. Anything a machine reads should come from
`--format json`. `--plain` drops colour and live redraw. It is implied in
CI, on a non-TTY stdout, and under `--format json`.

## Exit codes

One taxonomy, shared by every verb. A task's escalation reason determines
its exit code, so a script can branch on the class of failure without
parsing text.

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | A blocking gate is red |
| 2 | Escalated to a person: `locked_conflict`, `merge_conflict`, `blocker_finding`, `killed`, `underspecified`, `stale_inheritance` |
| 3 | Configuration error: a file that does not load, a refused argument, a malformed corpus |
| 4 | Infrastructure: `gate_infrastructure_failure`, `lease_expired`, `prepare_failed`, `seat_refused` |
| 5 | Exhausted: `budget_exhausted`, `poison_ceiling`, `cost_anomaly` |

Codes above 5 are unassigned, and a retired code is never reused.

## JSON output

`--format json` prints exactly one JSON document on stdout. The reporting
verbs (`gates`, `status`, `context`, `ledger`, `spec`, `merge`, `sandbox`,
`doctor` and the rest) put `schema_version` first, at 1 today. A few
refusals do not carry it yet: `torve log divergence` refusing an entry is
one. A configuration error is not JSON. It is a line of text on stderr, and
the exit code says which class of error it was.

## Files Torve reads

Each file Torve reads carries the shape it was written for, and `torve init`
writes the matching JSON Schema under `.torve/schemas/`. An editor with a
YAML language server validates every key as it is typed. Every model
refuses a key it does not know, naming it.

| File | `schema_version` |
| --- | --- |
| `.torve/config.yaml` | 1 |
| `.torve/gates.yaml` | 1 |
| `.torve/agents/<name>.yaml` (profile) | 1 |
| `.torve/harnesses/<name>.yaml` | 1 |
| `.torve/providers/<name>.yaml` | 1 |
| `.torve/specs/S-NNNN/document.yaml` | 4 |
| `.torve/tasks/T-NNNN/contract.yaml` | 2 |
| `.torve/tasks/T-NNNN/log.yaml` (the execution log) | 2 |

A document's `decisions.yaml`, `phasing.yaml` and `amendments.yaml` follow
its `document.yaml` and carry no version of their own.

A `schema_version` this torve does not read is refused where the file is
loaded, not read as current. The refusal names the file, the version found
and the version this build reads, and says which way to move: a newer file
needs a newer torve, an older one needs the conversion its release's
Upgrade note names. An absent line reads as current. `torve doctor` reddens
when `.torve/schemas/` lags the installed engine, and `torve init` brings it
back.

Identifiers are permanent. A document number, a decision `S-NNNN/D-n`, an
amendment `A-n` and a task `T-NNNN` are never reused once minted, and
`torve spec check` refuses a corpus that reuses one.

## Upgrading

One release to the next is five steps, in order:

1. Install the release: `pip install -U torve`, or `uv tool upgrade torve`.
2. Run `torve init` in the repository. It rewrites `.torve/schemas/` from
   the engine you just installed and adds any ignore pattern minted since.
   It touches a configuration or a gate manifest only under `--starter`,
   and only where none exists.
3. Commit what `torve init` changed, so every later run and every CI job
   reads the schemas the installed engine wrote.
4. Run `torve doctor` and read the red lines before running anything. A
   red line for a sandbox image means the image and the definition copied
   under `.torve/sandbox/` no longer agree: copy `sandboxes/<name>` from
   the release into `.torve/sandbox/` again, and rebuild the image from a
   checkout at that release (`just image <name>`).
5. When the release's Upgrade note names a record migration, run
   `torve migrate --all` against the store.

A release that moves a `schema_version` in the table above, or adds a
record migration, carries an Upgrade note in its changelog entry, naming
the files whose shape moved, the versions on either side, and the
conversion. A release whose entry carries no Upgrade note moves neither,
and steps 1 to 4 are the whole upgrade.

## Internal

These change without notice, in any release:

- **Torve's own state.** That is `.torve/telemetry.jsonl`,
  `.torve/feedback.jsonl`, `.torve/traces/`, `.torve/context/`,
  `.torve/regimes/`, `.torve/skills/` and `.torve/tmp/`, which `torve init`
  gitignores, and the task worktrees under `.wt/`.
- **The record.** That is the Postgres tables of the run store and the
  manager's event log. `torve migrate` moves them forward. Read them through
  `torve status`, `torve context`, `torve ledger` and `torve mcp`, not with
  SQL.
- **The Python package.** `import torve` has no declared public API, so any
  module may move or change in a minor.
- **Sandbox images and the toolkit they bake.** A seat names an image by
  tag. What that tag contains is its definition under `sandboxes/`, and the
  regime digest recorded on every attempt says when it moved.
