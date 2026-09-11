"""Runtime port over OpenSandbox (S-0003/D-3) — the platform the specifications adopt for
its credential vault, per-sandbox egress control, strong isolation options and
platform-enforced timeout (S-0003/runtime).

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
probe sandbox on schedule. The conformance battery asserts this adapter's
contract on three legs: against an in-process SDK emulation (see
tests/opensandbox_stub.py), against the Docker daemon for the twin
adapter, and — when TORVE_OPENSANDBOX_TEST_DOMAIN names a server —
against that real one, where the two assertions the stub cannot vouch for
join the contract: platform timeout collecting a sandbox, and label-scoped
enumeration with destroy-by-id across connections (S-0041/live-conformance). A
live/stub disagreement is a stub defect, never a relaxed assertion.

Both legs of the sync round trip book their bytes and seconds to the
attempt's transfer ledger (S-0041/the-transfer-measured) — the seed at `create`, the pipe
at `sync_out` — so the remote tax is a measured number before anyone
optimizes it.

Under remote endpoint mode (`broker.bind`, optionally `broker.advertise` —
S-0041/the-broker-reachable) the sandbox's proxy env is composed from the broker's
advertised address instead of forwarded from the runner: the host's loopback
and bridge-gateway addresses mean nothing to a sandbox on another machine,
so the configured address takes the derivation's place, the broker itself
and loopback are excluded from proxying so the token-authenticated provider
routes speak to the broker directly, and everything the proxy convention
reaches is refused loudly at the broker — the pass-through leg is a
sealed-mode mechanism, and this runtime's remote runs have no sealed
topology to authenticate it.
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
from urllib.parse import urlsplit

from torve.adapters.runtime.plugins import seed_files
from torve.application.ports import (
    PROXY_ENV,
    ExecResult,
    SandboxHandle,
    SandboxInfo,
    SandboxSpec,
)
from torve.application.telemetry import record_transfer
from torve.base import naming
from torve.base.shell import truncate
from torve.config.runconfig import CACHE_MOUNT, OpenSandboxConfig

# ----------------------- #

_IMPORT_HINT = (
    "the opensandbox SDK is not installed — install the extra: pip install 'torve[opensandbox]'"
)

# The proxy trio the broker's advertised address is composed into — the
# explicit-proxy egress of a run is the broker's; the rest of PROXY_ENV
# stays whatever the runner's environment says.
BROKER_PROXY_ENV = ("http_proxy", "https_proxy", "all_proxy")


def _advertised_proxy_env(proxy: str) -> dict[str, str]:
    """The sandbox's proxy env in remote endpoint mode: egress that follows
    the proxy convention travels to the broker at its advertised address,
    and the broker's own host and loopback are excluded so the
    token-authenticated provider routes — which dial the broker directly —
    are not proxied through the broker itself. The same wiring sealed mode
    composes from the internal network's gateway and the name-derived port;
    here the configured address stands in for the two network facts."""

    env: dict[str, str] = {}

    for name in BROKER_PROXY_ENV:
        for variant in (name, name.upper()):
            env[variant] = proxy

    broker_host = urlsplit(proxy).hostname or ""
    excluded = "127.0.0.1,localhost" + (f",{broker_host}" if broker_host else "")

    for variant in ("no_proxy", "NO_PROXY"):
        env[variant] = excluded

    return env


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
            # S-0017/docker-inside-the-sandbox, S-0017/D-10: the refusal stands — the live-server
            # integration (S-0041) weighed it and kept docker-in-sandbox a
            # non-goal; a battery driving containers uses the Docker runtime.
            raise ValueError(
                "the opensandbox runtime refuses docker access in any mode — "
                "use the docker runtime for a repository whose battery "
                "drives containers"
            )

        self._sdk = sdk or _sdk()
        api_key = os.environ.get(config.api_key_env, "")
        self._connection = self._sdk.config.ConnectionConfigSync(
            domain=config.domain, api_key=api_key
        )
        # Resolved once at configuration load from broker.bind/broker.advertise
        # (remote endpoint mode); empty means no remote broker — the proxy
        # env keeps being forwarded from the runner, as before.
        self._broker_proxy = config.remote_broker_proxy
        self._live: dict[str, tuple[Any, str]] = {}  # handle id -> (sdk sandbox, workdir)
        # handle id -> task the sandbox is labelled with, the key both of the
        # attempt's transfer legs book their ledger entry under.
        self._transfer_tasks: dict[str, str] = {}

    # ....................... #

    def create(self, spec: SandboxSpec, workspace: Path) -> SandboxHandle:
        if CACHE_MOUNT in spec.volumes.values():
            # S-0035/D-5: refused loudly until a server-side analog exists —
            # never a quiet cold fallback, which would let a run measure a
            # different regime than the tier configured.
            raise RuntimeError(
                "the opensandbox runtime refuses a tier's cache_volume — there is "
                "no server-side analog for the slot-suffixed derived-cache volume "
                "yet, and falling back to cold silently would measure a different "
                "regime than the tier you configured; drop cache_volume or use the "
                "docker runtime for warm tiers"
            )

        if spec.volumes:
            raise RuntimeError(
                "OpenSandbox has no per-slot auth volumes — subscription adapters "
                "need the Docker runtime (S-0004/D-2); OpenSandbox credentials belong "
                "to its vault (S-0003/runtime)"
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
        # Under remote endpoint mode the broker's advertised address
        # replaces them for the proxy trio: the runner's loopback means
        # nothing to a sandbox on another machine, and the broker is the
        # address the run composed its routes against.
        proxies = {
            variant: os.environ[variant]
            for name in PROXY_ENV
            for variant in (name, name.upper())
            if variant in os.environ
        }

        if self._broker_proxy:
            proxies.update(_advertised_proxy_env(self._broker_proxy))

        sandbox = self._sdk.SandboxSync.create(
            spec.image,
            connection_config=self._connection,
            timeout=timedelta(seconds=spec.timeout_s),
            env={**proxies, **passthrough, **spec.env},
            metadata={**spec.labels, "torve.name": spec.name},
        )

        # The seed leg, timed and counted (S-0041/D-5): host-side tar, the
        # base64 payload through the files API, and the in-sandbox unpack
        # are one cost with one number. A spec with no task label cannot be
        # attributed to any attempt row, so it books nothing — visibly.
        task_id = spec.labels.get(naming.LABEL_TASK)
        seed_started = time.monotonic()
        payload = base64.b64encode(_workspace_tar(workspace)).decode()
        # A path inside the freshly created sandbox container, not on this
        # host — there is no local tempdir race to have.
        staging = f"/tmp/torve-ws-{spec.name}.b64"  # nosec B108
        sandbox.files.write_files([self._sdk.models.WriteEntry(path=staging, data=payload)])

        seed = sandbox.commands.run(
            f"mkdir -p {spec.workdir} && base64 -d {staging} "
            f"| tar xzf - -C {spec.workdir} && rm {staging}"
        )
        seed_seconds = time.monotonic() - seed_started

        if task_id is not None:
            # The money and seconds spent moving bytes are spent even when
            # the unpack fails; book before the failure branch destroys.
            record_transfer(task_id, seed_bytes=len(payload), seed_seconds=seed_seconds)

        if getattr(seed, "exit_code", 0) not in (0, None):
            sandbox.destroy()
            raise RuntimeError(f"workspace seed failed in sandbox: {_exec_result(seed, 0).output}")

        # The equipment mount (S-0062/D-5). This server has no bind mounts, so
        # what docker does with `-v ...:ro` this does by sending the bytes and
        # taking the write bit away — the same guarantee by a different route,
        # which is the split the two adapters exist for.
        for host, mount in spec.readonly_binds.items():
            bind_staging = f"/tmp/torve-bind-{abs(hash(mount))}.b64"  # nosec B108
            sandbox.files.write_files(
                [
                    self._sdk.models.WriteEntry(
                        path=bind_staging,
                        data=base64.b64encode(_workspace_tar(Path(host))).decode(),
                    )
                ]
            )
            bound = sandbox.commands.run(
                f"mkdir -p {mount} && base64 -d {bind_staging} | tar xzf - -C {mount} "
                f"&& rm {bind_staging} && chmod -R a-w {mount}"
            )

            if getattr(bound, "exit_code", 0) not in (0, None):
                sandbox.destroy()

                raise RuntimeError(
                    f"equipment mount failed in sandbox: {_exec_result(bound, 0).output}"
                )

        handle = SandboxHandle(id=str(sandbox.id), name=spec.name)
        self._live[handle.id] = (sandbox, spec.workdir)

        if task_id is not None:
            self._transfer_tasks[handle.id] = task_id

        # The profile's plugins in this harness's own shape (S-0061/D-6),
        # through the files API rather than a shell: the same refusal for an
        # unseedable harness, raised before the attempt believes it has them.
        rendered = seed_files(spec.image, spec.plugins)

        if rendered:
            sandbox.files.write_files(
                [
                    self._sdk.models.WriteEntry(path=path, data=text)
                    for path, text in sorted(rendered.items())
                ]
            )

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
        leg_started = time.monotonic()
        execution = sandbox.commands.run(f"tar czf - -C {workdir} . | base64 -w0")
        result = _exec_result(execution, leg_started)

        if result.exit_code not in (0, None):
            raise RuntimeError(f"workspace sync_out failed: {result.output}")

        raw = result.output.strip()
        _extract_tar(base64.b64decode(raw), workspace)

        # Read, never consumed (T-0274): an attempt syncs out twice — the
        # harness recovers its trace file, then the run loop takes the whole
        # workspace — and popping here booked the first transfer and zeroed
        # the second, usually larger one. `destroy` owns the cleanup.
        task_id = self._transfer_tasks.get(handle.id)

        if task_id is not None:
            # Wire bytes of the base64 pipe out, seconds through the local
            # unpack — the same leg the run loop will pay for on the row.
            record_transfer(
                task_id,
                sync_out_bytes=len(raw),
                sync_out_seconds=time.monotonic() - leg_started,
            )

    # ....................... #

    def destroy(self, handle: SandboxHandle) -> None:
        entry = self._live.pop(handle.id, None)
        self._transfer_tasks.pop(handle.id, None)

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
        # The server pulls from a registry, so a digest-pinned reference
        # carries its identity in the name — on a pull platform the pinned
        # reference is the resolution, not a stand-in for one (S-0041
        # §5.2). Anything else resolves to nothing and records as an
        # unresolved regime: never invented (S-0017/D-1).
        if "@sha256:" in image:
            return "sha256:" + image.rsplit("@sha256:", 1)[1]

        return None

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
