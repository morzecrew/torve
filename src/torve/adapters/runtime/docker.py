"""Runtime port over Docker — the sanctioned fallback beside OpenSandbox
(D-3.3). The container is the unit of lifecycle: destroying it kills
everything inside, including grandchildren that called setsid (D-4).

The workspace is bind-mounted at the spec's workdir, so `sync_out` is a
no-op. The container runs as the invoking user so files it writes into the
mount stay reapable without privileges, and PID 1 is `sleep <timeout>` so the
platform itself bounds the lifecycle (RFC 0003 §4.1) even if the runner dies.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path

from torve.application.ports import (
    PROXY_ENV,
    ExecResult,
    SandboxHandle,
    SandboxInfo,
    SandboxSpec,
)
from torve.base import naming
from torve.base.shell import truncate
from torve.config.runconfig import sealed_broker_port

# ----------------------- #


class DockerError(RuntimeError):
    pass


# ....................... #


# The PROXY_ENV convention is forwarded only when a network mode was chosen —
# a host-loopback proxy address is reachable under "host" and poison under
# the default bridge. Sealed mode (D-21.3) replaces it: the sandbox joins
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
        # "socket" mounts the host daemon into every sandbox (RFC 0017 §2a,
        # D-17.9/D-17.10): host-equivalent capability, an explicit
        # per-repository opt-in. The image supplies the docker CLI.
        self.docker_mode = docker_mode

    # ....................... #

    def _run(self, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.docker, *args], capture_output=True, text=True, timeout=timeout, check=False
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

    def create(self, spec: SandboxSpec, workspace: Path) -> SandboxHandle:
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
                # Sealed containment (D-21.3): the sandbox's only reachable
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

        for key, value in spec.labels.items():
            args += ["--label", f"{key}={value}"]

        for key, value in spec.env.items():
            args += ["-e", f"{key}={value}"]

        for name in spec.env_passthrough:
            # Name only: docker reads the value from the invoking environment,
            # so the secret never transits torve or the spec (D-4b).
            args += ["-e", name]

        for volume, mount in spec.volumes.items():
            args += ["-v", f"{volume}:{mount}"]

        args += [spec.image, "sleep", str(int(spec.timeout_s))]
        proc = self._run(*args)

        if proc.returncode != 0:
            raise DockerError(proc.stderr.strip() or "docker run failed")

        return SandboxHandle(id=proc.stdout.strip(), name=spec.name)

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

    def build_image(self, context: Path, tag: str) -> str:
        proc = self._run("build", "-t", tag, str(context), timeout=1800)

        if proc.returncode != 0:
            raise DockerError(proc.stderr.strip() or f"docker build failed for {tag}")

        digest = self.resolve_image(tag)

        if digest is None:
            raise DockerError(f"built {tag} but could not resolve its digest")

        return digest

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
