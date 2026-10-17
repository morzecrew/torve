<!-- torve:managed sandboxes/base — rendered from the corpus; do not edit by hand -->

## Decisions governing `sandboxes/base/`

### S-0110/D-4 — `ASSUMED` (A second adopter runs torve from a tool install) — implementation: none

`just images` stamps every image with the version setuptools-scm renders for the tree it builds, the number hatch-vcs gives the host's torve, through `SETUPTOOLS_SCM_PRETEND_VERSION`; `torve doctor` runs each seat image's `torve --version` and goes red when it differs from the host's.

- Paths: `bake.hcl` `justfile` `sandboxes/base/Dockerfile` `src/torve/cli/doctor.py`
- Consequence: a sandbox running another torve than the host is a red check before a night

### S-0110/D-5 — `ASSUMED` (A second adopter runs torve from a tool install) — implementation: none

The base image installs CPython 3.13 and 3.14 into its read-only interpreter directory, and torve's own environment stays on 3.13.

- Paths: `sandboxes/base/Dockerfile`
- Consequence: a project on Python 3.14 runs its gates in a seat image without deriving one

<!-- /torve:managed -->
