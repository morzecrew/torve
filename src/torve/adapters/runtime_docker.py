"""Runtime port over Docker — the sanctioned fallback beside OpenSandbox
(D-3.3). The container is the unit of lifecycle: destroying it kills
everything inside, including grandchildren that called setsid (D-4).

The workspace is bind-mounted at the spec's workdir, so `sync_out` is a
no-op. The container runs as the invoking user so files it writes into the
mount stay reapable without privileges, and PID 1 is `sleep <timeout>` so the
platform itself bounds the lifecycle (RFC 0003 §4.1) even if the runner dies.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from torve import naming
from torve.ports import ExecResult, SandboxHandle, SandboxInfo, SandboxSpec
from torve.shell import truncate


class DockerError(RuntimeError):
    pass


class DockerRuntime:
    def __init__(self, docker_bin: str = "docker") -> None:
        self.docker = docker_bin

    def _run(self, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.docker, *args], capture_output=True, text=True, timeout=timeout, check=False
        )

    def create(self, spec: SandboxSpec, workspace: Path) -> SandboxHandle:
        args = ["run", "-d", "--init", "--name", spec.name,
                "--user", f"{os.getuid()}:{os.getgid()}",
                "-v", f"{workspace.resolve()}:{spec.workdir}",
                "-w", spec.workdir]
        for key, value in spec.labels.items():
            args += ["--label", f"{key}={value}"]
        for key, value in spec.env.items():
            args += ["-e", f"{key}={value}"]
        args += [spec.image, "sleep", str(int(spec.timeout_s))]
        proc = self._run(*args)
        if proc.returncode != 0:
            raise DockerError(proc.stderr.strip() or "docker run failed")
        return SandboxHandle(id=proc.stdout.strip(), name=spec.name)

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

    def sync_out(self, handle: SandboxHandle, workspace: Path) -> None:
        """Bind mount: the workspace already holds what the sandbox wrote."""

    def destroy(self, handle: SandboxHandle) -> None:
        self._run("rm", "-f", "-v", handle.id)

    def destroy_by_id(self, sandbox_id: str) -> None:
        self._run("rm", "-f", "-v", sandbox_id)

    def list_torve_sandboxes(self) -> list[SandboxInfo]:
        proc = self._run("ps", "-a", "--filter", f"label={naming.LABEL_TASK}",
                         "--format", "{{.ID}}\t{{.Names}}\t{{.Labels}}")
        if proc.returncode != 0:
            raise DockerError(proc.stderr.strip() or "docker ps failed")
        infos: list[SandboxInfo] = []
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            labels = dict(
                pair.split("=", 1) for pair in parts[2].split(",") if "=" in pair
            )
            infos.append(SandboxInfo(id=parts[0], name=parts[1], labels=labels))
        return infos
