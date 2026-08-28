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

# The standard proxy convention: a sandbox on the host's egress path must see
# the same variables the host's own processes do, or its traffic silently
# takes a different (often blocked) route. Runtimes forward these by name —
# the values ride the runtime's own environment, never a spec.
PROXY_ENV = ("http_proxy", "https_proxy", "ftp_proxy", "all_proxy", "socks_proxy", "no_proxy")


# ....................... #


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


# ....................... #


@dataclass(frozen=True)
class SandboxHandle:
    id: str
    name: str


# ....................... #


@dataclass(frozen=True)
class SandboxInfo:
    id: str
    name: str
    labels: dict[str, str]


# ....................... #


@dataclass(frozen=True)
class ExecResult:
    exit_code: int | None  # None when the command timed out
    output: str
    duration_s: float

    # ....................... #

    @property
    def timed_out(self) -> bool:
        return self.exit_code is None


# ....................... #


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


# ....................... #


class WorkspacePort(Protocol):
    def create(self, task_id: str, base_ref: str | None) -> Path: ...

    def remove(self, task_id: str) -> None: ...

    def list_worktrees(self) -> list[tuple[str, Path]]: ...


# ....................... #


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
    # The run's broker handle (RFC 0021 §5.1): a base URL per routed
    # provider and the run-scoped token, substituted into the tier command.
    # None when no broker adapter is in force.
    broker: BrokerHandle | None = None


# ....................... #


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

    # ....................... #

    @property
    def timed_out(self) -> bool:
        return self.exit_code is None


# ....................... #


class Agent(Protocol):
    # Which adapter this is ("fake", "api", "harness", "subscription") — the
    # telemetry records what actually ran, not what the tier configured
    # (RFC 0004 §6: an --agent fake override must not masquerade as a model).
    kind: str

    def run(self, ctx: AgentContext) -> AgentResult: ...


# ....................... #


@dataclass(frozen=True)
class BrokerRoute:
    """One routed provider (RFC 0021 §5): the wire destination and the name
    of the environment variable holding the key in the broker's own
    environment — names, never values (D-4b)."""

    provider: str
    upstream: str  # the provider's real base URL
    key_env: str  # env var name; the value lives only in the broker's process


# ....................... #


@dataclass(frozen=True)
class BrokerRouting:
    """The run's routing (RFC 0021 §5.4, D-21.4): every provider this run's
    agents may use, dispatch-checked before the broker opens. The broker
    exposes one loopback route per routed provider and refuses anything
    else at the wire."""

    routes: tuple[BrokerRoute, ...] = ()

    def route_for(self, provider: str) -> BrokerRoute | None:
        return next((route for route in self.routes if route.provider == provider), None)


# ....................... #


@dataclass(frozen=True)
class BrokerBudget:
    """The run's token bound (RFC 0021 §5.4): the task contract's
    `budget.tokens`, held by the broker and enforced mid-run — requests past
    it are refused and the run escalates `cost_anomaly`. None is unbounded."""

    tokens: int | None = None


# ....................... #


@dataclass(frozen=True)
class BrokerHandle:
    """What the sandbox needs and nothing else (RFC 0021 §5.1): a base URL
    per routed provider and a per-run bearer token the broker issued and
    revokes at close. Both are operator non-secret knobs — they ride the
    tier command inline (RFC 0017 §3), never a spec env."""

    token: str
    base_urls: dict[str, str] = field(default_factory=dict)

    def url_for(self, provider: str) -> str | None:
        return self.base_urls.get(provider)


# ....................... #


@dataclass(frozen=True)
class BrokerUsage:
    """Counts and metadata only (D-21.7): request count, token counts per
    provider where the provider reports them, wall time, refusals by cause,
    the broker's measured cost where the provider reports one, and which
    providers were refused for routing. The broker never keeps request or
    response bodies."""

    requests: int = 0
    tokens_per_provider: dict[str, int] = field(default_factory=dict)
    wall_time_s: float = 0.0
    refusals: dict[str, int] = field(default_factory=dict)
    cost_usd: float | None = None
    refused_providers: dict[str, int] = field(default_factory=dict)


# ....................... #


class Broker(Protocol):
    """The egress broker port (RFC 0021 §5.1): holds every provider
    credential the run needs, exposes one loopback route per routed
    provider, injects the key and meters at the wire, and refuses requests
    past the run's budget. Adapters: `local` (a reverse proxy the runner
    starts on loopback for the life of the run), `opensandbox` (the server's
    vault and egress control behind the same port, when a server exists),
    `none` (today's behaviour, named explicitly).

    The handle's fields reach the sandbox through the tier command — the
    channel RFC 0017 §3 assigns to operator non-secret knobs."""

    name: str  # "local" | "opensandbox" | "none"

    def open(self, run: str, routing: BrokerRouting, budget: BrokerBudget) -> BrokerHandle: ...

    def usage(self, handle: BrokerHandle) -> BrokerUsage:
        """Live counters, mid-run: the runner reads them to escalate
        `cost_anomaly` while the run is still in progress (D-21.6)."""

        ...

    def close(self, handle: BrokerHandle) -> BrokerUsage: ...


# ....................... #


class Vcs(Protocol):
    """Local git at the runner boundary (RFC 0010 §2): the agent produces a
    tree, the runner produces the commit — author is the agent identity,
    committer is Torve, and the signing key, when configured, never enters
    a sandbox (D-10.3)."""

    def commit_all(
        self, worktree: Path, message: str, author: str | None = None, sign_key: str | None = None
    ) -> str | None: ...

    def changed_names(self, worktree: Path) -> list[str]: ...

    def push(
        self, worktree: Path, branch: str, token: str | None = None, supersede: bool = False
    ) -> bool: ...

    def republish_branch(self, root: Path, branch: str, token: str | None = None) -> bool: ...

    def landed_shas(self, worktree: Path, task_id: str) -> list[str]: ...

    def revert(self, worktree: Path, shas: list[str]) -> bool: ...


# ....................... #


class LaneVcs(Protocol):
    """The serialized lane's git surface (RFC 0006 §1): ancestry questions,
    a rebase in a disposable worktree, and the fast-forward landing. The
    lane never resolves a conflict — a conflicted rebase aborts."""

    def tip(self, root: Path, ref: str) -> str | None: ...

    def is_ancestor(self, root: Path, ancestor: str, descendant: str) -> bool: ...

    def current_branch(self, root: Path) -> str: ...

    def dirty_paths(self, root: Path) -> list[str]: ...

    def adopt_identical(self, root: Path, ref: str) -> list[str]: ...

    def tip_age_s(self, root: Path, ref: str) -> float: ...

    def rebase_conflicts(self, root: Path, branch: str, onto: str) -> bool: ...

    def rebase_in_worktree(self, root: Path, branch: str, onto: str, workdir: Path) -> bool: ...

    def remove_worktree(self, root: Path, workdir: Path) -> None: ...

    def merge_ff(self, root: Path, ref: str) -> str: ...

    def approver(self, root: Path) -> str: ...


# ....................... #


class Scm(Protocol):
    def open_pr(self, worktree: Path, branch: str, title: str, body: str) -> str: ...


# ....................... #


@dataclass
class PrInfo:
    """One pull request as the forge reports it (RFC 0005 §4): enough to
    apply the skip rules and locate the head, nothing more."""

    number: int
    title: str
    author: str
    draft: bool
    head_sha: str
    base_ref: str
    changed_files: int
    state: str  # open | closed | merged, forge-cased


# ....................... #


class PrScm(Protocol):
    """The PR-review trigger's forge surface (RFC 0005 §4, D-5.2): the
    runner reads the pull request and posts the findings comment; the
    reviewer itself never holds a forge credential."""

    def pr_info(self, number: int) -> PrInfo: ...

    def comment(self, number: int, body: str, key: str) -> str: ...


# ....................... #


class PrVcs(Protocol):
    """The PR-review trigger's git surface: fetch the pull request's head
    and base, materialise a detached worktree to review, diff, and read
    Torve-Task trailers to map the head back to a task contract."""

    def fetch_pr(
        self, root: Path, number: int, base_ref: str, token: str | None = None
    ) -> tuple[str, str]: ...

    def worktree_at(self, root: Path, sha: str, workdir: Path) -> None: ...

    def remove_worktree(self, root: Path, workdir: Path) -> None: ...

    def diff(self, root: Path, base: str, head: str) -> str: ...

    def task_trailers(self, root: Path, base: str, head: str) -> list[str]: ...


# ....................... #


@dataclass
class ReflectResult:
    """applied | refused | unsupported (RFC 0008 §5, D-8.6). A refusal is a
    logged divergence, never an exception — the engine's state is correct
    whether or not the board accepted it."""

    outcome: str
    detail: str = ""


# ....................... #


@dataclass
class TrackerCommand:
    """One inbound intent (RFC 0008 §3, D-8.3): parsed allow-listed from
    tracker text, validated against the real store — a card move submits an
    intent, it never changes state."""

    verb: str
    task_id: str
    actor: str
    source: str  # the comment id the reply threads back to
    # The command comment's full body (RFC 0020, D-20.6): a revise on a
    # drafting task carries its feedback in the same comment. Untrusted
    # text everywhere (D-8.5) — it steers a drafter, never the engine.
    text: str = ""


# ....................... #


@dataclass
class IntakeRequest:
    """One unclaimed intake issue (RFC 0020 §5.4): a torve.intake-labeled
    request a commander filed, not yet retitled to a task's row."""

    number: int
    title: str
    body: str
    author: str


# ....................... #


class Tracker(Protocol):
    """The tracker is an output port (RFC 0008 §1, D-8.1): it holds no
    authoritative state, and its text is untrusted input (D-8.5)."""

    def reflect(self, task_id: str, state: str, title: str) -> ReflectResult: ...

    def label(self, task_id: str, name: str) -> ReflectResult: ...

    def unlabel(self, task_id: str, name: str) -> ReflectResult: ...

    def comment(self, task_id: str, body: str, key: str) -> ReflectResult: ...

    def annotate(self, task_id: str, location: str, body: str, key: str) -> ReflectResult: ...

    def notify(self, task_id: str, login: str, body: str, key: str) -> ReflectResult: ...

    def poll_commands(self) -> list[TrackerCommand]: ...

    def intake_requests(self) -> list[IntakeRequest]: ...

    def retitle(self, number: int, title: str) -> ReflectResult: ...


# ....................... #


class CiStatus(Protocol):
    """The remote's CI verdict for one commit (RFC 0006 §3): the lane's
    `ci: green_on_current_head` requirement consults this before landing.
    The adapter owns the polling — backoff against a lightweight endpoint,
    because the rate budget is shared with the agents (§1) — and returns a
    settled word: "success", a failure conclusion, "pending" when the
    budget ran out mid-run, or "absent" when the remote never saw the
    commit. Only "success" lands."""

    def conclusion(self, sha: str) -> str: ...
