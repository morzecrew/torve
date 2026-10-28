<!-- torve:managed pages/docs — rendered from the corpus; do not edit by hand -->

## Decisions governing `pages/docs/`

### S-0058/D-10 — `ASSUMED` (One grammar and the anatomy)

The skill and its template, the schemas `torve init` writes, the projections beside the code and the operating page follow the grammar and the anatomy in the same phase that changes them

- Paths: `skills/**` `src/torve/application/colocation.py` `src/torve/cli/init.py` `pages/docs/operating.md`
- Consequence: Nothing a harness or a person reads names an identifier the check refuses

### S-0082/D-12 — `ASSUMED` (The arms can be launched)

The operating guide's arm section gains how to launch the arms beside how to read them, and its claim that the bare arm is handed a sentence and a worktree is corrected to what the worktree actually carries

- Paths: `pages/docs/operating.md`
- Consequence: the sentence a reader quotes a year later is the one the code supports, rather than the one the design hoped for

### S-0095/D-2 — `ASSUMED` (A second repository can adopt torve)

The starter manifest is the four structural builtins (`scope`, `secrets`, `no-test-tampering`, `decisions-reported`) blocking, with the repository's test gate a commented entry saying gates run inside the sandbox image; the starter configuration is `runtime.adapter: docker`, `image: python:3.13-slim`, `store.adapter: mock`

- Paths: `src/torve/cli/init.py` `tests/test_cli.py` `pages/docs/get-started.md`
- Consequence: `torve gates run --base main` exits 0 in a repository the starter just set up

### S-0100/D-11 — `ASSUMED` (A session learns torve from the torve it runs)

`pages/docs/operating.md` lists only verbs that exist, documents `night show`, `manager note`, the `log` verbs, `review pr`, `equip` and `spec project`, and describes the prompt S-0073/D-1 builds; the operator skill defers to it for concepts and to `--help` for flags.

- Paths: `pages/docs/operating.md`
- Consequence: the operating page, the skill and the CLI say the same thing once each

### S-0110/D-3 — `ASSUMED` (A second adopter runs torve from a tool install)

A repository installs torve as a uv tool, not as a project dependency, and every command torve writes or ships for a host or a sandbox calls bare `torve`: the stub `init` writes, the shipped hooks, and the operating docs and skill.

- Paths: `src/torve/cli/init.py` `skills/torve/**` `skills/hooks/**` `pages/docs/**`
- Consequence: torve's pins and an application's never meet in one lock

### S-0112/D-8 — `ASSUMED` (A release publishes its seat images)

The adoption and upgrading references and the configuration reference say to pull the published images, name them with `{torve}`, and derive from `ghcr.io/morzecrew/sandbox-base`.

- Paths: `skills/torve/references/**` `pages/docs/**`
- Consequence: an adopter's first image step is a pull, not a copy

### S-0115/D-8 — `ASSUMED` (Every decision shows its evidence) — implementation: none

The working rules and the spec-writer skill tell an implementer to cite, in each test's docstring, the row it proves, and `pages/docs/operating.md` documents the gate and the table's columns.

- Paths: `skills/working-rules/SKILL.md` `skills/spec-writer/SKILL.md` `pages/docs/operating.md`
- Consequence: the citations the gate reads are written on purpose rather than by habit

### S-0116/D-13 — `ASSUMED` (An independent verifier)

The operating guide and the torve skill's night reference say how to turn the pilot on, read a held-out failure and handle a disputed test, and the spec-writer guide asks a phase's intent to name the public surface its tests drive.

- Paths: `pages/docs/operating.md` `skills/torve/references/night.md` `skills/spec-writer/**`
- Consequence: a phase written for a verifier names what to test, so the verifier does not invent an API

<!-- /torve:managed -->
