<!-- torve:managed src/torve/adapters/agent — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/agent/`

### S-0055/D-33 — `ASSUMED` (Standing decisions)

Configuration routes by nature — identity in the image, task context in the workspace, secrets as environment names, knobs in the command, state on the slot volume — one item, one channel

- Paths: `src/torve/config/runconfig.py` `src/torve/adapters/agent/harness.py`
- Consequence: A second channel for a secret is a leak; a repository-carried harness config is an injection surface

### S-0055/D-60 — `ASSUMED` (Standing decisions)

Every attempt runs on a worktree seeded with the base sha pinned host-side, the role's skills materialised, and the divergence log seeded; the session trace is captured to `.torve/traces/` and its content enters no prompt and drives no control flow

- Paths: `src/torve/application/session.py` `src/torve/adapters/agent/harness.py` `.torve/traces/**`
- Consequence: What an agent saw is reconstructible; what it reasoned is never an input to another agent

### S-0059/D-2 — `LOCKED` (One word for the document, and the tree as the record)

Every reader resolves a contract's document by `document_dir(spec_dir, identifier)`; the scope gate's exemption and the runner's decision gates come from the identifier, and the gate context carries the corpus path

- Paths: `src/torve/gates/context.py` `src/torve/gates/scope.py` `src/torve/gates/runner.py` `src/torve/application/decisions.py` `src/torve/adapters/agent/harness.py`
- Consequence: The regexes over the path go; a document that moved is still found
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0059/D-8 — `ASSUMED` (One word for the document, and the tree as the record)

The log's `base_sha` is `base`, the landing's word; the log's schema version is 2 and a log saying `base_sha` reads through a shim; the landing's `commit` stays; `spec new`'s hint and `spec show`'s label name the summary, not a description

- Paths: `src/torve/domain/spec.py` `src/torve/application/**` `src/torve/gates/decisions_reported.py` `src/torve/adapters/agent/harness.py` `src/torve/cli/**`
- Consequence: One word for the commit an attempt built on, in the log and the landing

### S-0060/D-9 — `ASSUMED` (A source is a file, and the contract names it)

The harness prompt names the source, its title and its ref in the line above the decisions, and the pack carries the source's own file beside `decisions.json`

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/application/contextpack.py`
- Consequence: The executor reads what asked, not a slug

<!-- /torve:managed -->
