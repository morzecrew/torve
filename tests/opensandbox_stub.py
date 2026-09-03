"""An in-process emulation of the opensandbox SDK's sync surface.

Each stub sandbox is a host temp directory and commands run through the host
shell — which is exactly enough to exercise the OpenSandbox adapter's real
work (tar sync in and out, metadata labels, lifecycle bookkeeping) without a
server. The conformance battery runs the adapter against this stub and the
Docker adapter against the real daemon, asserting the same contract.

When TORVE_OPENSANDBOX_TEST_DOMAIN names a server, the battery runs a third
leg against that one (tests/test_sandbox_images.py) and asserts two things
this file cannot vouch for — the platform's own timeout collecting a sandbox,
and enumeration and destroy-by-id across connections. Where the live leg
fails and the stub leg passes, the stub is what lies: correct the stub,
never the assertion (RFC 0041 §5.1).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

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
class RunCommandOpts:
    timeout: timedelta | None = None
    working_directory: str | None = None


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

    def run(self, command: str, opts: RunCommandOpts | None = None) -> _Execution:
        opts = opts or RunCommandOpts()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                check=False,
                cwd=opts.working_directory,
                timeout=opts.timeout.total_seconds() if opts.timeout else None,
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


class SandboxManagerSync:
    @classmethod
    def create(cls, *, connection_config=None) -> SandboxManagerSync:
        return cls()

    def __enter__(self) -> SandboxManagerSync:
        return self

    def __exit__(self, *exc) -> None:
        return None

    def list_sandbox_infos(self, sandbox_filter: SandboxFilter) -> SimpleNamespace:
        # The real SDK returns PagedSandboxInfos — a pydantic model whose
        # rows live under .sandbox_infos. Iterating the model itself yields
        # (field, value) tuples, which is exactly the trap the live probe
        # caught the adapter in; the stub returns the same envelope shape so
        # the conformance battery keeps asserting the unwrap.
        return SimpleNamespace(
            sandbox_infos=[
                _Info(id=s.id, metadata=dict(s.metadata), state=s.state)
                for s in list(REGISTRY.values())
                if not sandbox_filter.states or s.state in sandbox_filter.states
            ]
        )

    def kill_sandbox(self, sandbox_id: str) -> None:
        sandbox = REGISTRY.get(sandbox_id)
        if sandbox is not None:
            sandbox.destroy()


# Mirrors the real SDK's nesting — ConnectionConfigSync under
# opensandbox.config, WriteEntry/SandboxFilter under opensandbox.models,
# RunCommandOpts one level deeper at opensandbox.models.execd (verified
# against the live server: opensandbox 0.1.15 does not re-export it at
# .models) — so the adapter's attribute lookups behave identically against
# the stub and the real package.
config = SimpleNamespace(ConnectionConfigSync=ConnectionConfigSync)
models = SimpleNamespace(
    WriteEntry=WriteEntry,
    SandboxFilter=SandboxFilter,
    execd=SimpleNamespace(RunCommandOpts=RunCommandOpts),
)
