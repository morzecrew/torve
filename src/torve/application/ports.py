"""The runner's ports (RFC 0001 §5, RFC 0003): Workspace, Runtime, Agent, and
the minimal Vcs/Scm pair. Abstract what is replaced wholesale; the state
machine, gate ordering and escalation vocabulary stay above these seams.

The Runtime contract is "workspace in, changed files out": how the workspace
reaches the sandbox is the adapter's business (Docker bind-mounts it; a
server-side runtime syncs it), and after `sync_out` the host-side workspace
holds whatever the sandbox produced. The conformance battery asserts the
contract, not the mechanism.

Credentials never enter a spec's env from here (D-4b): adapters receive names
of things, not secrets, and outbound credentials are the vault's job
(RFC 0003 §4.1).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from forze.application.contracts.durable.function import DurableRunStorePort

    from torve.config.runconfig import StoreConfig
    from torve.domain.task import Task

# ----------------------- #

# Opens the durable run store for a given configuration. The adapters provide
# the real one; the loop and the reaper receive it injected, never imported
# (RFC 0015 §2.1: application does not import adapters).
StoreFactory = Callable[["StoreConfig"], Awaitable["DurableRunStorePort"]]


@dataclass(frozen=True)
class SandboxSpec:
    name: str
    image: str
    labels: dict[str, str]
    timeout_s: float  # platform-enforced lifecycle bound (RFC 0003 §4.1)
    env: dict[str, str] = field(default_factory=dict)
    workdir: str = "/work"


@dataclass(frozen=True)
class SandboxHandle:
    id: str
    name: str


@dataclass(frozen=True)
class SandboxInfo:
    id: str
    name: str
    labels: dict[str, str]


@dataclass(frozen=True)
class ExecResult:
    exit_code: int | None  # None when the command timed out
    output: str
    duration_s: float

    @property
    def timed_out(self) -> bool:
        return self.exit_code is None


class Runtime(Protocol):
    def create(self, spec: SandboxSpec, workspace: Path) -> SandboxHandle: ...

    def exec(self, handle: SandboxHandle, command: str, timeout_s: float) -> ExecResult: ...

    def sync_out(self, handle: SandboxHandle, workspace: Path) -> None: ...

    def destroy(self, handle: SandboxHandle) -> None: ...

    def list_torve_sandboxes(self) -> list[SandboxInfo]: ...

    def destroy_by_id(self, sandbox_id: str) -> None: ...


class WorkspacePort(Protocol):
    def create(self, task_id: str, base_ref: str | None) -> Path: ...

    def remove(self, task_id: str) -> None: ...

    def list_worktrees(self) -> list[tuple[str, Path]]: ...


@dataclass
class AgentContext:
    task: Task
    attempt: int
    workspace: Path
    handle: SandboxHandle
    runtime: Runtime
    workdir: str
    timeout_s: float


@dataclass(frozen=True)
class AgentResult:
    exit_code: int | None
    output: str

    @property
    def timed_out(self) -> bool:
        return self.exit_code is None


class Agent(Protocol):
    def run(self, ctx: AgentContext) -> AgentResult: ...


class Vcs(Protocol):
    def commit_all(self, worktree: Path, message: str) -> str | None: ...

    def push(self, worktree: Path, branch: str) -> bool: ...


class Scm(Protocol):
    def open_pr(self, worktree: Path, branch: str, title: str, body: str) -> str: ...
