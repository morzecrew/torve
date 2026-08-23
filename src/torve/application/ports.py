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
    from torve.domain.task import InheritedDecision, Task

# ----------------------- #

# Opens the durable run store for a given configuration. The adapters provide
# the real one; the loop and the reaper receive it injected, never imported
# (RFC 0015 §2.1: application does not import adapters).
StoreFactory = Callable[["StoreConfig"], Awaitable["DurableRunStorePort"]]

# The standard proxy convention: a sandbox on the host's egress path must see
# the same variables the host's own processes do, or its traffic silently
# takes a different (often blocked) route. Runtimes forward these by name —
# the values ride the runtime's own environment, never a spec.
PROXY_ENV = ("http_proxy", "https_proxy", "ftp_proxy", "all_proxy",
             "socks_proxy", "no_proxy")


@dataclass(frozen=True)
class SandboxSpec:
    name: str
    image: str
    labels: dict[str, str]
    timeout_s: float  # platform-enforced lifecycle bound (RFC 0003 §4.1)
    env: dict[str, str] = field(default_factory=dict)
    workdir: str = "/work"
    # Names of variables the runtime forwards from its own environment — the
    # value never enters the spec (D-4b), the runtime is the boundary.
    env_passthrough: tuple[str, ...] = ()
    # Named auth volume -> mount path, read-write because token refresh
    # writes (RFC 0004 §2, D-4.2). One per worker slot, never the host
    # config directory.
    volumes: dict[str, str] = field(default_factory=dict)
    # A reviewer physically cannot fix-and-approve (RFC 0005 §2, D-5.2):
    # the workspace bind mounts read-only. Host-side writes (the staged
    # prompt, the trace) stay visible through the mount.
    workspace_read_only: bool = False


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

    def resolve_image(self, image: str) -> str | None:
        """The image's content digest, or None when this runtime cannot
        resolve the reference (RFC 0017 §2, D-17.1: the digest is the
        identity; an unresolved image is recorded as unresolved, never
        invented)."""
        ...

    def build_image(self, context: Path, tag: str) -> str:
        """Build the definition at *context* under *tag* and return the
        digest. An operator action invoked by `torve sandbox build` only —
        the engine never builds mid-run (D-17.3)."""
        ...


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
    # Context composition is the runner's (D-3.19): when the runner hands a
    # composed prompt — the review input, assembled without the author's
    # trace (D-5.3) — the adapter stages it verbatim instead of building one.
    prompt: str | None = None


@dataclass(frozen=True)
class AgentResult:
    exit_code: int | None
    output: str
    # None of these can be reconstructed after the fact (RFC 0004 §6):
    # model_version is whatever version string the provider returned — None
    # marks an uncontrolled regime (D-4.6); trace_ref turns escalation triage
    # from archaeology into replay (§4). A trace is never gate evidence.
    cost_usd: float | None = None
    model_version: str | None = None
    trace_ref: str | None = None

    @property
    def timed_out(self) -> bool:
        return self.exit_code is None


class Agent(Protocol):
    # Which adapter this is ("fake", "api", "harness", "subscription") — the
    # telemetry records what actually ran, not what the tier configured
    # (RFC 0004 §6: an --agent fake override must not masquerade as a model).
    kind: str

    def run(self, ctx: AgentContext) -> AgentResult: ...


class DecisionSource(Protocol):
    """Standing decisions for a repository area (RFC 0007 §6a, D-7.6): every
    adapter is deterministic — model-assisted extraction runs outside the
    engine, in a supervised session, and lands as a committed document."""

    def standing(self, repo: str, paths: list[str]) -> list[InheritedDecision]: ...


class Vcs(Protocol):
    def commit_all(self, worktree: Path, message: str) -> str | None: ...

    def push(self, worktree: Path, branch: str) -> bool: ...


class LaneVcs(Protocol):
    """The serialized lane's git surface (RFC 0006 §1): ancestry questions,
    a rebase in a disposable worktree, and the fast-forward landing. The
    lane never resolves a conflict — a conflicted rebase aborts."""

    def tip(self, root: Path, ref: str) -> str | None: ...

    def is_ancestor(self, root: Path, ancestor: str, descendant: str) -> bool: ...

    def current_branch(self, root: Path) -> str: ...

    def is_clean(self, root: Path) -> bool: ...

    def rebase_in_worktree(self, root: Path, branch: str, onto: str, workdir: Path) -> bool: ...

    def remove_worktree(self, root: Path, workdir: Path) -> None: ...

    def merge_ff(self, root: Path, ref: str) -> str: ...

    def approver(self, root: Path) -> str: ...


class Scm(Protocol):
    def open_pr(self, worktree: Path, branch: str, title: str, body: str) -> str: ...
