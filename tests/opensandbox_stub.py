"""An in-process emulation of the opensandbox SDK's sync surface.

Each stub sandbox is a host temp directory and commands run through the host
shell — which is exactly enough to exercise the OpenSandbox adapter's real
work (tar sync in and out, metadata labels, lifecycle bookkeeping) without a
server. The conformance battery runs the adapter against this stub and the
Docker adapter against the real daemon, asserting the same contract.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

REGISTRY: dict[str, SandboxSync] = {}


@dataclass
class ConnectionConfigSync:
    domain: str = "stub"
    api_key: str = ""


@dataclass
class WriteEntry:
    path: str
    data: str
    mode: int = 0o644


@dataclass
class SandboxFilter:
    states: list[str] = field(default_factory=list)
    page_size: int = 100


@dataclass
class _LogEntry:
    text: str


class _Logs:
    def __init__(self, stdout: str, stderr: str) -> None:
        self.stdout = [_LogEntry(stdout)] if stdout else []
        self.stderr = [_LogEntry(stderr)] if stderr else []


class _Execution:
    def __init__(self, exit_code: int | None, stdout: str, stderr: str) -> None:
        self.exit_code = exit_code
        self.logs = _Logs(stdout, stderr)


class _Commands:
    def __init__(self, sandbox: SandboxSync) -> None:
        self._sandbox = sandbox

    def run(self, command: str, timeout=None) -> _Execution:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout.total_seconds() if timeout else None,
            )
        except subprocess.TimeoutExpired:
            return _Execution(None, "", "stub: command timed out")
        return _Execution(proc.returncode, proc.stdout, proc.stderr)


class _Files:
    def write_files(self, entries: list[WriteEntry]) -> None:
        for entry in entries:
            target = Path(entry.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(entry.data, encoding="utf-8")


class SandboxSync:
    def __init__(self, image: str, metadata: dict[str, str]) -> None:
        self.id = uuid.uuid4().hex
        self.image = image
        self.metadata = metadata
        self.state = "RUNNING"
        self.root = Path(tempfile.mkdtemp(prefix="osb-stub-"))
        self.commands = _Commands(self)
        self.files = _Files()

    @classmethod
    def create(
        cls, image: str, *, connection_config=None, timeout=None, env=None, metadata=None, **_kwargs
    ) -> SandboxSync:
        sandbox = cls(image, dict(metadata or {}))
        sandbox.env = dict(env or {})  # recorded so tests can assert what the SDK was told
        REGISTRY[sandbox.id] = sandbox
        return sandbox

    def destroy(self) -> None:
        self.state = "DEAD"
        shutil.rmtree(self.root, ignore_errors=True)
        REGISTRY.pop(self.id, None)


@dataclass
class _Info:
    id: str
    metadata: dict[str, str]
    state: str


class SandboxManager:
    @classmethod
    def create(cls, *, connection_config=None) -> SandboxManager:
        return cls()

    def __enter__(self) -> SandboxManager:
        return self

    def __exit__(self, *exc) -> None:
        return None

    def list_sandbox_infos(self, sandbox_filter: SandboxFilter) -> list[_Info]:
        return [
            _Info(id=s.id, metadata=dict(s.metadata), state=s.state)
            for s in list(REGISTRY.values())
            if not sandbox_filter.states or s.state in sandbox_filter.states
        ]

    def kill_sandbox(self, sandbox_id: str) -> None:
        sandbox = REGISTRY.get(sandbox_id)
        if sandbox is not None:
            sandbox.destroy()
