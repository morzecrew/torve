<!-- torve:managed src/torve/adapters/runtime — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/runtime/`

### S-0055/D-3 — `LOCKED` (Standing decisions)

The sandbox is the unit of lifecycle; the engine never executes agent code on the host

- Paths: `src/torve/adapters/runtime/**` `src/torve/application/session.py`
- Consequence: Killing a worker costs its lease and nothing else
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0062/D-5 — `ASSUMED` (Equipment is declared, and the harness is told how to take it)

The equipment cache mounts read-only into the sandbox, one directory per item, and the image's own `/opt/torve/equip` composes whatever its harness needs from the manifest at the mount root (S-0063/D-3, S-0063/D-12).

- Paths: `src/torve/adapters/runtime/**` `src/torve/application/ports.py`
- Consequence: nothing inside an attempt can edit what it was equipped with, and the cache stays derived state that deleting costs only wall clock

### S-0063/D-11 — `LOCKED` (The image knows how to equip itself)

`torve sandbox build` and `stage` retire, and `RuntimePort.build_image` with them; the verb keeps `list` and `digest`, and `just images` is the build.

- Paths: `src/torve/cli/sandbox.py` `src/torve/application/ports.py` `src/torve/adapters/runtime/**`
- Consequence: the engine loses its last way to build an image, which is the rule S-0017/D-3 already stated and could not enforce while a verb of its own did it
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-12 — `LOCKED` (The image knows how to equip itself)

The equipment manifest is `manifest.json` at the root of the read-only equipment mount, named by `TORVE_EQUIPMENT` alone — never a second variable, never a file in the workspace.

- Paths: `src/torve/application/session.py` `src/torve/adapters/runtime/**`
- Consequence: the attempt cannot rewrite the description of what it was equipped with, and one variable names one root rather than two disagreeing about which is authoritative
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0111/D-1 — `ASSUMED` (A task gets a Docker daemon of its own) — implementation: none

`runtime.docker: nested` gives each sandbox of a role in `runtime.nested.roles` a Docker daemon in a privileged side container from `runtime.nested.image`, a pinned dind image. The sandbox joins the side container's network namespace and reaches the daemon at `tcp://127.0.0.1:2375`.

- Paths: `src/torve/config/runconfig.py` `src/torve/adapters/runtime/docker.py` `src/torve/application/ports.py` `.torve/schemas/**`
- Consequence: a task's containers, ports and volumes are its own, and published ports answer on `localhost`

### S-0111/D-2 — `ASSUMED` (A task gets a Docker daemon of its own) — implementation: none

The side container carries the sandbox's labels, mounts the worktree at the sandbox's workdir and the worker slot's cache volume at `/var/lib/docker`, and uses `runtime.nested.registry_mirrors`. Once its daemon answers, it removes the containers, volumes and networks a previous task left, keeping images. Destroying the sandbox destroys it.

- Paths: `src/torve/adapters/runtime/docker.py`
- Consequence: nothing a task starts outlives it, and what it pulled stays pulled for the slot

### S-0111/D-3 — `ASSUMED` (A task gets a Docker daemon of its own) — implementation: none

A side container whose `docker info` does not answer within `runtime.nested.ready_s` seconds (default 60) fails the sandbox's creation as a runtime error, and both containers are removed.

- Paths: `src/torve/adapters/runtime/docker.py`
- Consequence: a sandbox that asked for Docker either has it or does not run; a Docker-bound test never skips green

### S-0111/D-6 — `ASSUMED` (A task gets a Docker daemon of its own) — implementation: none

In a nested sandbox's environment, a proxy whose host is loopback is rewritten to the side container's network gateway. `torve doctor` checks, from a bridge container, that the rewritten proxy answers whenever any role is nested: the side daemon of every nested sandbox, the gates included, pulls its images through it.

- Paths: `src/torve/adapters/runtime/docker.py` `src/torve/cli/doctor.py`
- Consequence: an attempt in a nested sandbox reaches its model, or the doctor says why not

## Invariants holding over `src/torve/adapters/runtime/`

- **S-0062/I-1**: No equipment is fetched while an attempt is running — every fetch is host-side, before the sandbox exists.
  - Paths: `src/torve/application/equipment.py` `src/torve/adapters/runtime/**`
  - Check: `uv run pytest tests/test_equipment.py -k host_side`

<!-- /torve:managed -->
