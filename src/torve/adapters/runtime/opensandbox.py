"""Runtime port over OpenSandbox (D-3.3) — the platform the RFCs adopt for
its credential vault, per-sandbox egress control, strong isolation options and
platform-enforced timeout (RFC 0003 §4.1).

OpenSandbox is a server with a files/commands API and no bind mounts, so this
adapter satisfies the "workspace in, changed files out" contract by syncing:
a tar of the workspace (minus `.git` — a worktree's gitfile must never leave
the host) travels in through the files API at create, and back out through a
base64 pipe at `sync_out`. Labels ride the sandbox's metadata, which is what
lets the reaper enumerate by convention.

The real SDK (opensandbox 0.1.15) nests its surface: `ConnectionConfigSync`
lives under `opensandbox.config`, `WriteEntry` and `SandboxFilter` under
`opensandbox.models`, and the sync sandbox manager is `SandboxManagerSync`.
`CommandsAdapterSync.run` takes no `timeout` kwarg — the call is
`run(command, opts=RunCommandOpts(timeout=..., working_directory=...))`,
which is also how this adapter changes into the sandbox's workdir, replacing
a hand-rolled `cd {workdir} &&` shell prefix.

The SDK ships as the optional extra `torve[opensandbox]`; without it, or
without a reachable server, construction fails with an instructive error
rather than a stack trace. Verified live against a self-hosted
opensandbox-server: `create`'s tar-seed round-trip worked against the real
server first try, and the platform-enforced sandbox timeout collected a
probe sandbox on schedule. Full integration is otherwise deferred until a
server is routinely available — the conformance battery runs this adapter
against an in-process SDK emulation (see tests/opensandbox_stub.py) and
Docker against the real daemon, asserting the same contract for both.
"""

from __future__ import annotations

import base64
import io
import os
import tarfile
import time
from datetime import timedelta
from importlib import import_module
from pathlib import Path, PurePosixPath
from typing import Any

from torve.application.ports import (
    PROXY_ENV,
    ExecResult,
    SandboxHandle,
    SandboxInfo,
    SandboxSpec,
)
from torve.base import naming
from torve.base.shell import truncate
from torve.config.runconfig import OpenSandboxConfig

# ----------------------- #

_IMPORT_HINT = (
    "the opensandbox SDK is not installed — install the extra: pip install 'torve[opensandbox]'"
)


# ....................... #


def _sdk() -> Any:
    try:
        module = import_module("opensandbox")
        # Submodule imports attach as attributes of the package (Python import
        # semantics), so `module.config` / `module.models` resolve below
        # regardless of what opensandbox's own __init__.py re-exports.
        import_module("opensandbox.config")
        import_module("opensandbox.models")

    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(_IMPORT_HINT) from exc

    return module


# ....................... #


def _workspace_tar(workspace: Path) -> bytes:
    buffer = io.BytesIO()

    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for entry in sorted(workspace.rglob("*")):
            rel = entry.relative_to(workspace)

            if rel.parts and rel.parts[0] == ".git":
                continue

            tar.add(entry, arcname=str(rel), recursive=False)

    return buffer.getvalue()


# ....................... #


def _extract_tar(data: bytes, workspace: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            # tar -C dir . yields ./-prefixed names; normalize before judging.
            parts = [p for p in PurePosixPath(member.name).parts if p != "."]

            if not parts or parts[0] == ".git" or ".." in parts or member.name.startswith("/"):
                continue  # never let the sandbox rewrite git metadata or escape

            tar.extract(member, workspace, filter="data")


# ....................... #


def _exec_result(execution: Any, started: float) -> ExecResult:
    exit_code = getattr(execution, "exit_code", None)
    logs = getattr(execution, "logs", None)
    parts: list[str] = []

    for stream in ("stdout", "stderr"):
        entries: list[Any] = getattr(logs, stream, None) or []

        for entry in entries:
            parts.append(getattr(entry, "text", str(entry)))

    return ExecResult(
        exit_code=exit_code,
        output=truncate("".join(parts)),
        duration_s=time.monotonic() - started,
    )


# ....................... #


class OpenSandboxRuntime:
    def __init__(
        self, config: OpenSandboxConfig, sdk: Any | None = None, docker_mode: str = ""
    ) -> None:
        if docker_mode:
            # RFC 0017 §2a, D-17.10: refused in any mode until the
            # live-server integration decides what the server can offer.
            raise ValueError(
                "the opensandbox runtime refuses docker access in any mode — "
                "use the docker runtime for a repository whose battery "
                "drives containers"
            )

        self._sdk = sdk or _sdk()
        api_key = os.environ.get(config.api_key_env, "")
        self._connection = self._sdk.config.ConnectionConfigSync(domain=config.domain, api_key=api_key)
        self._live: dict[str, tuple[Any, str]] = {}  # handle id -> (sdk sandbox, workdir)

    # ....................... #

    def create(self, spec: SandboxSpec, workspace: Path) -> SandboxHandle:
        if spec.volumes:
            raise RuntimeError(
                "OpenSandbox has no per-slot auth volumes — subscription adapters "
                "need the Docker runtime (D-4.2); OpenSandbox credentials belong "
                "to its vault (RFC 0003 §4.1)"
            )

        # Passthrough resolves here, at the API boundary — the last host-side
        # point before the value must exist. This is where the vault would sit.
        passthrough = {
            name: os.environ[name] for name in spec.env_passthrough if name in os.environ
        }

        # The proxy convention rides along like it does for Docker under a
        # network opt-in — but whether the address is *reachable* from a
        # server-side sandbox is the server's networking, not ours: this
        # only guarantees the sandbox sees the same variables the runner did.
        proxies = {
            variant: os.environ[variant]
            for name in PROXY_ENV
            for variant in (name, name.upper())
            if variant in os.environ
        }

        sandbox = self._sdk.SandboxSync.create(
            spec.image,
            connection_config=self._connection,
            timeout=timedelta(seconds=spec.timeout_s),
            env={**proxies, **passthrough, **spec.env},
            metadata={**spec.labels, "torve.name": spec.name},
        )

        payload = base64.b64encode(_workspace_tar(workspace)).decode()
        # A path inside the freshly created sandbox container, not on this
        # host — there is no local tempdir race to have.
        staging = f"/tmp/torve-ws-{spec.name}.b64"  # nosec B108
        sandbox.files.write_files([self._sdk.models.WriteEntry(path=staging, data=payload)])

        seed = sandbox.commands.run(
            f"mkdir -p {spec.workdir} && base64 -d {staging} "
            f"| tar xzf - -C {spec.workdir} && rm {staging}"
        )

        if getattr(seed, "exit_code", 0) not in (0, None):
            sandbox.destroy()
            raise RuntimeError(f"workspace seed failed in sandbox: {_exec_result(seed, 0).output}")

        handle = SandboxHandle(id=str(sandbox.id), name=spec.name)
        self._live[handle.id] = (sandbox, spec.workdir)

        return handle

    # ....................... #

    def _sandbox(self, handle: SandboxHandle) -> tuple[Any, str]:
        try:
            return self._live[handle.id]

        except KeyError:
            raise RuntimeError(f"sandbox {handle.id} is not held by this runtime process") from None

    # ....................... #

    def exec(self, handle: SandboxHandle, command: str, timeout_s: float) -> ExecResult:
        sandbox, workdir = self._sandbox(handle)
        started = time.monotonic()

        execution = sandbox.commands.run(
            command,
            opts=self._sdk.models.execd.RunCommandOpts(
                timeout=timedelta(seconds=timeout_s), working_directory=workdir
            ),
        )

        return _exec_result(execution, started)

    # ....................... #

    def sync_out(self, handle: SandboxHandle, workspace: Path) -> None:
        sandbox, workdir = self._sandbox(handle)
        execution = sandbox.commands.run(f"tar czf - -C {workdir} . | base64 -w0")
        result = _exec_result(execution, time.monotonic())

        if result.exit_code not in (0, None):
            raise RuntimeError(f"workspace sync_out failed: {result.output}")

        _extract_tar(base64.b64decode(result.output.strip()), workspace)

    # ....................... #

    def destroy(self, handle: SandboxHandle) -> None:
        entry = self._live.pop(handle.id, None)

        if entry is not None:
            entry[0].destroy()
        else:
            self.destroy_by_id(handle.id)

    # ....................... #

    def destroy_by_id(self, sandbox_id: str) -> None:
        with self._sdk.SandboxManagerSync.create(connection_config=self._connection) as manager:
            manager.kill_sandbox(sandbox_id)

    # ....................... #

    def resolve_image(self, image: str) -> str | None:
        # The server pulls from a registry; a digest-pinned reference carries
        # its identity in the name. Anything else is honestly unresolved
        # until the live-server integration teaches this adapter to ask the
        # registry.
        if "@sha256:" in image:
            return "sha256:" + image.rsplit("@sha256:", 1)[1]

        return None

    # ....................... #

    def build_image(self, context: Path, tag: str) -> str:
        raise RuntimeError(
            "the opensandbox runtime cannot build images — build with the docker "
            "runtime and push to a registry the server can pull from"
        )

    # ....................... #

    def list_torve_sandboxes(self) -> list[SandboxInfo]:
        with self._sdk.SandboxManagerSync.create(connection_config=self._connection) as manager:
            paged = manager.list_sandbox_infos(self._sdk.models.SandboxFilter(states=["RUNNING"]))

        found: list[SandboxInfo] = []

        # PagedSandboxInfos is a pydantic model: iterating it yields
        # (field, value) tuples, not rows — the rows are .sandbox_infos.
        # ponytail: first page only; follow pagination when a fleet outgrows
        # one page of live sandboxes.
        for info in paged.sandbox_infos:
            metadata = dict(getattr(info, "metadata", None) or {})

            if naming.LABEL_TASK not in metadata:
                continue

            found.append(
                SandboxInfo(
                    id=str(info.id),
                    name=metadata.get("torve.name", str(info.id)),
                    labels=metadata,
                )
            )

        return found
