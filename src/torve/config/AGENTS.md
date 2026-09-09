<!-- torve:managed src/torve/config — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/config/`

### D-53.1 — `LOCKED` (RFC 0053 — The item model and the rebuilt corpus)

The specification the engine reads is one pydantic model in `domain/spec.py`, `extra="forbid"`; every reader — planner, importer, check, health, show, intake lint, standing inheritance — consumes the model and never a parser's rows

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py`
- Consequence: Six consumers stop re-shaping rows; a field the author wrote cannot be dropped on the way to the record; D-7.17 stands because the *format* still terminates at `config/` while the model may cross
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-53.2 — `LOCKED` (RFC 0053 — The item model and the rebuilt corpus)

Markdown stays the authoring surface and the decision table stays a table (D-A.3); typed additions are fenced YAML blocks the model validates, unknown keys refused

- Paths: `rfcs/**` `src/torve/config/spec.py`
- Consequence: No migration of any existing document to load; the probe's YAML-per-document root is refused with its reason in §5.2
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-53.3 — `ASSUMED` (RFC 0053 — The item model and the rebuilt corpus)

Five fenced kinds: `decision-details`, `invariants`, `alternatives`, `questions`, `changes`; prose sections become `DesignSection`s keyed by heading slug and are typed no further

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py`
- Consequence: Typing an argument fragments it; what is typed is what was already a list

### D-53.4 — `LOCKED` (RFC 0053 — The item model and the rebuilt corpus)

A row's grade or paths change only through `torve rfc amend`, which records the typed diff with the prior value in a `changes` fence under the amendment heading; a grade or paths mismatch against the last recorded change fails `rfc check`. A text-only mismatch is editorial drift: a warning, re-stamped by `torve rfc fix` with the before and after recorded, never an `A-n`

- Paths: `src/torve/config/rfc_emit.py` `src/torve/config/spec.py`
- Consequence: The prior value exists at exactly one moment and is kept on both lanes; a typo costs one command, a regrade costs an amendment, and 12 of 14 already lost by hand-editing are the last
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-53.7 — `ASSUMED` (RFC 0053 — The item model and the rebuilt corpus)

A row whose every glob matches nothing on an accepted, implemented document is path rot: reported by `rfc check`, retired by `amend --retire --reason path-rot` (or `check --fix-rot`), recorded as `decision.retired` on import; never automatic on load, never a red on the document

- Paths: `src/torve/application/decisions.py` `src/torve/config/rfc_emit.py`
- Consequence: 27 rows today, 8 `LOCKED`, stop rendering as governance while governing nothing

### D-53.10 — `LOCKED` (RFC 0053 — The item model and the rebuilt corpus)

The next document number derives as the maximum plus one over the corpus path **and** the archive (D-A.17 extended); identifiers are never reused (D-A.19 unchanged)

- Paths: `src/torve/config/spec.py`
- Consequence: A gap after the archive is a gap, not a free number
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-54.14 — `ASSUMED` (RFC 0054 — Decisions as gates and the projections beside the code)

The default review skill set is `[reading-isnt-proof, ratchet-what-you-build]`

- Paths: `src/torve/config/runconfig.py`
- Consequence: Two skills that declare the role reach it

### D-55.4 — `LOCKED` (RFC 0055 — Standing decisions)

Agents hold no provider credentials; the broker injects them at its own boundary, routes providers at the wire and meters spend; every configuration file carries variable names, never values

- Paths: `src/torve/adapters/broker/**` `src/torve/config/runconfig.py` `.torve/config.yaml`
- Consequence: A sandbox that is compromised leaks nothing it was not given; a committed file never holds a secret
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.5 — `LOCKED` (RFC 0055 — Standing decisions)

Agents do not communicate; executor memory is off by default, per slot when on, and never mounted in a shadow run

- Paths: `src/torve/application/shadow.py` `src/torve/config/runconfig.py`
- Consequence: Shared memory is a communication channel with a euphemism; a remembered replay is an incomparable number
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.14 — `ASSUMED` (RFC 0055 — Standing decisions)

The gate manifest lives in the consuming repository at `.torve/gates.yaml`; builtins are named `@name`; every entry carries `state` and `origin`; a new gate enters at `shadow` and is promoted on soak evidence

- Paths: `.torve/gates.yaml` `src/torve/config/manifest.py`
- Consequence: Promotion is a separate act with a number behind it, never a default

### D-55.30 — `ASSUMED` (RFC 0055 — Standing decisions)

Everything Torve owns in a consuming repository lives under `.torve/`; the gate manifest and the run configuration are separate files; run configuration is read from where the runner was launched and never from the repository under work

- Paths: `.torve/config.yaml` `.torve/gates.yaml` `src/torve/config/runconfig.py` `src/torve/config/layout.py`
- Consequence: A worked-on repository cannot configure the engine working on it

### D-55.31 — `ASSUMED` (RFC 0055 — Standing decisions)

Providers a repository's contents may reach are enforced at dispatch, before a sandbox exists; an empty default denies every real provider

- Paths: `src/torve/config/runconfig.py` `src/torve/application/dispatch.py`
- Consequence: Silence is not a policy; it is a closed door

### D-55.32 — `ASSUMED` (RFC 0055 — Standing decisions)

A tier resolves through profiles in the operator's own configuration directory, never the repository under work; a missing profile or an unknown skill refuses rather than falls back

- Paths: `src/torve/config/runconfig.py` `src/torve/application/skills.py`
- Consequence: Resolution is fail-closed throughout

### D-55.33 — `ASSUMED` (RFC 0055 — Standing decisions)

Configuration routes by nature — identity in the image, task context in the workspace, secrets as environment names, knobs in the command, state on the slot volume — one item, one channel

- Paths: `src/torve/config/runconfig.py` `src/torve/adapters/agent/harness.py`
- Consequence: A second channel for a secret is a leak; a repository-carried harness config is an injection surface

### D-55.34 — `ASSUMED` (RFC 0055 — Standing decisions)

A stdio MCP server is image content; a remote MCP endpoint is an egress destination under provider routing; no execution sandbox is given a read surface onto the record

- Paths: `src/torve/config/runconfig.py` `.torve/sandbox/**` `src/torve/cli/mcp.py`
- Consequence: Files in the worktree carry task context; reach into the record is the planner's alone

### D-56.1 — `LOCKED` (RFC 0056 — Structure for everything)

A document is one YAML file, `rfcs/NNNN-slug.yaml`, in the `Document` model's own shape and key order; loading is the model's validator plus the corpus checks; `schema_version` 2, and 1 is refused

- Paths: `src/torve/config/spec.py` `src/torve/domain/spec.py`
- Consequence: The markdown parser, `load_fences`, the heading and table regexes are deleted; every reader of the corpus is unchanged
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-56.4 — `LOCKED` (RFC 0056 — Structure for everything)

Every writer — `amend`, `fix`, `retire`, `archive`, `new` — mutates the model and writes it through one serializer; comments are not preserved and `check` refuses one outside the schema header line; `fmt` survives as `--check` only

- Paths: `src/torve/config/rfc_emit.py` `src/torve/cli/rfc.py`
- Consequence: There is no second renderer to drop a field; the `character:` defect closes by construction
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-56.9 — `LOCKED` (RFC 0056 — Structure for everything)

With a store configured, `plan` mints into the record and writes no file; dispatch projects `.torve/tasks/<id>/contract.yaml` into the worktree, gitignored; the log written there is imported after the attempt; without a store the files are the record as today

- Paths: `src/torve/application/planner.py` `src/torve/application/session.py` `src/torve/config/layout.py`
- Consequence: The board is the only place a task is; the file exists for the attempt that reads it
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

<!-- /torve:managed -->
