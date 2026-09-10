"""Runtime port over Docker — the sanctioned fallback beside OpenSandbox
(S-0003/D-3). The container is the unit of lifecycle: destroying it kills
everything inside, including grandchildren that called setsid (S-0001/D-12).

The workspace is bind-mounted at the spec's workdir, so `sync_out` is a
no-op. The container runs as the invoking user so files it writes into the
mount stay reapable without privileges, and PID 1 is `sleep <timeout>` so the
platform itself bounds the lifecycle (S-0003/runtime) even if the runner dies.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path

from torve.adapters.runtime.plugins import seed_files
from torve.application.ports import (
    PROXY_ENV,
    ExecResult,
    SandboxHandle,
    SandboxInfo,
    SandboxSpec,
)
from torve.base import naming
from torve.base.shell import truncate
from torve.config.runconfig import CACHE_MOUNT, sealed_broker_port

# ----------------------- #


class DockerError(RuntimeError):
    pass


# ....................... #


# The toolchain cache homes pointed at the derived-cache volume (S-0035/D-4):
# subdirectory on the mount -> environment variable. The roster is the one
# measured to warm across attempts — each entry is a cache the tool
# demonstrably honors by variable, and a cache whose deletion changes
# nothing but wall clock (S-0035/D-1's doctrine, checked by a conformance case
# running the same battery cold and warm).
CACHE_HOMES = (("uv", "UV_CACHE_DIR"), ("mypy", "MYPY_CACHE_DIR"), ("ruff", "RUFF_CACHE_DIR"))


# ....................... #


def cache_home_env(mount: str = CACHE_MOUNT) -> dict[str, str]:
    """The sandbox environment pointing the toolchain caches at *mount*."""

    return {var: f"{mount}/{name}" for name, var in CACHE_HOMES}


# ....................... #


# The PROXY_ENV convention is forwarded only when a network mode was chosen —
# a host-loopback proxy address is reachable under "host" and poison under
# the default bridge. Sealed mode (S-0021/D-3) replaces it: the sandbox joins
# the internal network the broker attaches to, so the only reachable
# address is the broker, and the proxy env points at it — never at the
# host's own proxy, which would be a path nobody meant to leave open.
SEALED_PROXY_ENV = ("http_proxy", "https_proxy", "all_proxy")


# ....................... #


class DockerRuntime:
    def __init__(
        self, docker_bin: str = "docker", network: str = "", docker_mode: str = ""
    ) -> None:
        self.docker = docker_bin
        self.network = network  # "" = daemon default; "host" shares the host stack
        # "socket" mounts the host daemon into every sandbox (S-0017/docker-inside-the-sandbox,
        # S-0017/D-9/D-17.10): host-equivalent capability, an explicit
        # per-repository opt-in. The image supplies the docker CLI.
        self.docker_mode = docker_mode

    # ....................... #

    def _run(
        self, *args: str, timeout: float | None = None, stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.docker, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            input=stdin,
        )

    # ....................... #

    def _network_is_internal(self, network: str) -> bool:
        """Sealed-mode detection: a network created with --internal has no
        external connectivity, so the only host-side address on it is its
        gateway — the broker's seat. `host` is a network mode, not a
        network, and inspects as absent; the default bridge inspects as
        not internal. A missing network is not sealed (docker run will
        name it loudly)."""

        proc = self._run("network", "inspect", "--format", "{{.Internal}}", network)

        return proc.returncode == 0 and proc.stdout.strip().lower() == "true"

    # ....................... #

    def _network_gateway(self, network: str) -> str:
        proc = self._run(
            "network", "inspect", "--format", "{{(index .IPAM.Config 0).Gateway}}", network
        )

        if proc.returncode != 0 or not proc.stdout.strip():
            raise DockerError(
                f"sealed network {network}: cannot resolve its gateway — "
                f"{proc.stderr.strip() or 'no gateway reported'}"
            )

        return proc.stdout.strip()

    # ....................... #

    @staticmethod
    def _mounts_cache(spec: SandboxSpec) -> bool:
        """A spec carrying the fixed cache mount is the whole signal: the
        runner composed the slot-suffixed volume name, the decision that a
        warm tier's sandboxes point their toolchains at it lives here
        (S-0035/D-4), and shadow runs simply never carry the mount (S-0035/D-3)."""

        return CACHE_MOUNT in spec.volumes.values()

    # ....................... #

    def _run_args(self, spec: SandboxSpec, workspace: Path) -> list[str]:
        """The `docker run` argument list for a spec — pure, so the mount
        and env wiring is testable without a daemon."""

        mount_mode = ":ro" if spec.workspace_read_only else ""

        args = [
            "run",
            "-d",
            "--init",
            "--name",
            spec.name,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-v",
            f"{workspace.resolve()}:{spec.workdir}{mount_mode}",
            "-w",
            spec.workdir,
        ]

        if "HOME" not in spec.env:
            # The container runs as the invoking uid with no passwd entry, so
            # HOME is `/` — and every tool that caches under ~ (uv, pip, git,
            # npm) dies on permissions. A writable container-local default
            # fixes the class; a spec or command-level HOME still wins.
            args += ["-e", "HOME=/tmp"]

        if self.docker_mode == "socket":
            sock = "/var/run/docker.sock"
            args += ["-v", f"{sock}:{sock}"]

            # The container runs as the invoking uid; the socket's owning
            # group must ride along or the CLI cannot open it.
            with contextlib.suppress(OSError):
                args += ["--group-add", str(os.stat(sock).st_gid)]

        if self.network:
            args += ["--network", self.network]

            if self._network_is_internal(self.network):
                # Sealed containment (S-0021/D-3): the sandbox's only reachable
                # address is the broker at the internal network's gateway,
                # and its egress is the broker's declared pass-through — the
                # proxy env points there, and the broker's own address is
                # excluded so the agent's direct provider traffic is not
                # proxied through the broker itself.
                gateway = self._network_gateway(self.network)
                proxy = f"http://{gateway}:{sealed_broker_port(self.network)}"

                for name in SEALED_PROXY_ENV:
                    for variant in (name, name.upper()):
                        args += ["-e", f"{variant}={proxy}"]

                for variant in ("no_proxy", "NO_PROXY"):
                    args += ["-e", f"{variant}=127.0.0.1,localhost,{gateway}"]

            else:
                for name in PROXY_ENV:
                    for variant in (name, name.upper()):
                        if variant in os.environ:
                            args += ["-e", variant]

        if self._mounts_cache(spec):
            # The adapter points the toolchain cache homes at the mount
            # (S-0035/D-4). Emitted before the spec's own env — which docker
            # resolves last-wins over earlier duplicates, but these are
            # skipped for any name the spec already carries, so an explicit
            # env on the spec still decides.
            for name, path in cache_home_env().items():
                if name not in spec.env:
                    args += ["-e", f"{name}={path}"]

        for key, value in spec.labels.items():
            args += ["--label", f"{key}={value}"]

        for key, value in spec.env.items():
            args += ["-e", f"{key}={value}"]

        for name in spec.env_passthrough:
            # Name only: docker reads the value from the invoking environment,
            # so the secret never transits torve or the spec (S-0001/D-13).
            args += ["-e", name]

        for volume, mount in spec.volumes.items():
            args += ["-v", f"{volume}:{mount}"]

        args += [spec.image, "sleep", str(int(spec.timeout_s))]

        return args

    # ....................... #

    def create(self, spec: SandboxSpec, workspace: Path) -> SandboxHandle:
        proc = self._run(*self._run_args(spec, workspace))

        if proc.returncode != 0:
            raise DockerError(proc.stderr.strip() or "docker run failed")

        handle = SandboxHandle(id=proc.stdout.strip(), name=spec.name)

        if self._mounts_cache(spec):
            # A named volume Docker creates on first use is root-owned, and
            # this container runs as the invoking uid — a derived cache that
            # cannot be written is worse than no cache at all. Take
            # ownership of the mount once, from inside, as root; the chown
            # is idempotent, and it lands on the volume, so every later
            # sandbox over the same slot finds it writable.
            #
            # Its failure is the one this adapter used to swallow — the
            # single docker call whose result went unread, which is exactly
            # the condition the paragraph above says must not stand. A
            # root-owned cache then surfaced as `uv sync` and mypy failing
            # under the battery, which the runner books as gate-red
            # convictions feeding rung selection and the poison ceiling,
            # rather than as the infrastructure failure it is (T-0243).
            chown = self._run(
                "exec",
                "-u",
                "root",
                handle.id,
                "chown",
                f"{os.getuid()}:{os.getgid()}",
                CACHE_MOUNT,
                timeout=60,
            )

            if chown.returncode != 0:
                raise DockerError(
                    chown.stderr.strip()
                    or f"could not take ownership of the derived cache at {CACHE_MOUNT}"
                )

        self._seed_plugins(handle, spec)

        return handle

    # ....................... #

    def _seed_plugins(self, handle: SandboxHandle, spec: SandboxSpec) -> None:
        """The profile's plugins, in the shape this harness's own installer
        reads (S-0061/D-6).

        Written as root into the image's seed, which the tier command copies
        into HOME: the seed is where the harness looks, and writing it here
        means the declaration and the clone the image already carries cannot
        disagree about which ref is installed. Nothing is fetched — the
        clones are the image's, pinned at build time.
        """

        for path, text in seed_files(spec.image, spec.plugins).items():
            written = self._run(
                "exec",
                "-u",
                "root",
                "-i",
                handle.id,
                "sh",
                "-c",
                f"mkdir -p $(dirname {path}) && cat > {path} && chmod a+r {path}",
                stdin=text,
                timeout=60,
            )

            if written.returncode != 0:
                raise DockerError(
                    written.stderr.strip() or f"could not seed the harness plugin file {path}"
                )

    # ....................... #

    def exec(self, handle: SandboxHandle, command: str, timeout_s: float) -> ExecResult:
        import time

        started = time.monotonic()

        try:
            proc = self._run("exec", handle.id, "sh", "-c", command, timeout=timeout_s)

        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or b"").decode(errors="replace") if exc.stdout else ""

            return ExecResult(
                exit_code=None,
                output=truncate(output + f"\n[hard timeout after {timeout_s:.0f}s]"),
                duration_s=time.monotonic() - started,
            )

        return ExecResult(
            exit_code=proc.returncode,
            output=truncate((proc.stdout or "") + (proc.stderr or "")),
            duration_s=time.monotonic() - started,
        )

    # ....................... #

    def sync_out(self, handle: SandboxHandle, workspace: Path) -> None:
        """Bind mount: the workspace already holds what the sandbox wrote."""

    # ....................... #

    def destroy(self, handle: SandboxHandle) -> None:
        self._run("rm", "-f", "-v", handle.id)

    # ....................... #

    def destroy_by_id(self, sandbox_id: str) -> None:
        self._run("rm", "-f", "-v", sandbox_id)

    # ....................... #

    def resolve_image(self, image: str) -> str | None:
        # `.Id` is the content identity of the local image — it covers
        # locally-built images, which have no RepoDigest until pushed.
        proc = self._run("image", "inspect", "--format", "{{.Id}}", image)

        if proc.returncode != 0:
            return None

        return proc.stdout.strip() or None

    # ....................... #

    def list_torve_sandboxes(self) -> list[SandboxInfo]:
        proc = self._run(
            "ps",
            "-a",
            "--filter",
            f"label={naming.LABEL_TASK}",
            "--format",
            "{{.ID}}\t{{.Names}}\t{{.Labels}}",
        )

        if proc.returncode != 0:
            raise DockerError(proc.stderr.strip() or "docker ps failed")

        infos: list[SandboxInfo] = []

        for line in proc.stdout.splitlines():
            parts = line.split("\t")

            if len(parts) != 3:
                continue

            labels = dict(pair.split("=", 1) for pair in parts[2].split(",") if "=" in pair)
            infos.append(SandboxInfo(id=parts[0], name=parts[1], labels=labels))

        return infos
