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

### S-0062/D-10 — `ASSUMED` (Equipment is declared, and the harness is told how to take it)

For a harness that takes the `skill` kind the prompt stops naming `.torve/skills/`; for one that does not, `materialize` and the prompt's paragraph stand unchanged. All three harnesses this repository builds take it — claude by flag, dsh at `.agents/skills`, mimo at `.mimocode/skill/` (S-0063/D-16) — so the paragraph stands for none of them.

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/application/skills.py`
- Consequence: a loaded skill is loaded, not described — and a harness with no skill channel keeps the only mechanism it has

### S-0063/D-1 — `LOCKED` (The image knows how to equip itself)

A sandbox image carries `/opt/torve/run`, which invokes its harness; a manifest carries no command template and `command` is refused by name.

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/config/agents.py`
- Consequence: the shell that knows how to start a harness lives beside the harness, and a seat cannot be misconfigured into a model that silently would not work
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-2 — `LOCKED` (The image knows how to equip itself)

The engine speaks to every image through one set of environment variables — `TORVE_PROMPT`, `TORVE_MODEL`, `TORVE_EQUIPMENT`, `TORVE_OUTPUT`, and `TORVE_BROKER_URL`/`TORVE_BROKER_TOKEN` where a broker is in force.

- Paths: `src/torve/application/ports.py` `src/torve/adapters/agent/harness.py`
- Consequence: a new harness is a new image and never a new template language, and every adapter fills one shape
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-5 — `ASSUMED` (The image knows how to equip itself)

Broker placeholders retire into `TORVE_BROKER_URL` and `TORVE_BROKER_TOKEN`; the refusal moves from "the string names a placeholder" to "this seat needs a broker".

- Paths: `src/torve/adapters/agent/harness.py`
- Consequence: a run-scoped token stops being spliced into a shell string, and a seat whose provider the broker does not route is still refused before dispatch

## Invariants holding over `src/torve/adapters/agent/`

- **S-0061/I-1**: No configuration key reaches the prompt before the charter's base working rules — prompt_extras appends, and nothing replaces.
  - Paths: `src/torve/adapters/agent/harness.py` `src/torve/config/agents.py`
  - Check: `uv run pytest tests/test_agents.py -k base_rules`
- **S-0063/I-1**: No harness manifest carries a shell line, and no engine code substitutes into one.
  - Paths: `src/torve/config/agents.py` `src/torve/adapters/agent/harness.py`
  - Check: `uv run pytest tests/test_agents.py -k no_shell`

<!-- /torve:managed -->
