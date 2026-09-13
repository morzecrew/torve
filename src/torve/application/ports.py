"""The runner's ports (S-0001/ports, S-0003): Workspace, Runtime, Agent, and
the minimal Vcs/Scm pair. Abstract what is replaced wholesale; the state
machine, gate ordering and escalation vocabulary stay above these seams.

The Runtime contract is "workspace in, changed files out": how the workspace
reaches the sandbox is the adapter's business (Docker bind-mounts it; a
server-side runtime syncs it), and after `sync_out` the host-side workspace
holds whatever the sandbox produced. The conformance battery asserts the
contract, not the mechanism.

Credentials never enter a spec's env from here (S-0001/D-13): adapters receive names
of things, not secrets, and outbound credentials are the vault's job
(S-0003/runtime).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from forze.application.contracts.durable.function import DurableRunStorePort

    from torve.config.runconfig import StoreConfig
    from torve.domain.task import Task

# ----------------------- #

# Opens the durable run store for a given configuration. The adapters provide
# the real one; the loop and the reaper receive it injected, never imported
# (S-0015/permitted-imports: application does not import adapters).
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
    timeout_s: float  # platform-enforced lifecycle bound (S-0003/runtime)
    env: dict[str, str] = field(default_factory=dict)
    workdir: str = "/work"
    # Names of variables the runtime forwards from its own environment — the
    # value never enters the spec (S-0001/D-13), the runtime is the boundary.
    env_passthrough: tuple[str, ...] = ()
    # Named auth volume -> mount path, read-write because token refresh
    # writes (S-0004/subscription-authentication, S-0004/D-2). One per worker slot, never the host
    # config directory.
    volumes: dict[str, str] = field(default_factory=dict)
    # A reviewer physically cannot fix-and-approve (S-0005/what-makes-review-independent-rather-than-ceremonial, S-0005/D-2):
    # the workspace bind mounts read-only. Host-side writes (the staged
    # prompt, the trace) stay visible through the mount.
    workspace_read_only: bool = False
    # Host path -> mount point, read-only (S-0062/D-5). The equipment cache
    # arrives this way: nothing inside an attempt may edit what it was equipped
    # with, and the description of that equipment sits inside the same mount so
    # it cannot be rewritten either (S-0063/D-12).
    readonly_binds: dict[str, str] = field(default_factory=dict)


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
        resolve the reference (S-0017/the-image-is-an-input-not-an-environment, S-0017/D-1: the digest is the
        identity; an unresolved image is recorded as unresolved, never
        invented)."""

        ...


# ....................... #


class WorkspacePort(Protocol):
    def create(self, task_id: str, base_ref: str | None, *, resume: bool = False) -> Path: ...

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
    # Context composition is the runner's (S-0003/D-19): when the runner hands a
    # composed prompt — the review input, assembled without the author's
    # trace (S-0005/D-3) — the adapter stages it verbatim instead of building one.
    prompt: str | None = None
    # The run's broker handle (S-0021/the-port): a base URL per routed
    # provider and the run-scoped token, substituted into the tier command.
    # None when no broker adapter is in force.
    broker: BrokerHandle | None = None
    # Continuation (S-0026 S-0026/D-8/9): the previous attempt ended on budget
    # exhaustion and this worktree was cut from its own candidate tip rather
    # than base. The agent-facing prompt names this plainly; nothing else in
    # the loop branches on it.
    resume: bool = False


# ....................... #


@dataclass(frozen=True)
class AgentResult:
    exit_code: int | None
    output: str

    # None of these can be reconstructed after the fact (S-0004/telemetry-staged):
    # model_version is whatever version string the provider returned — None
    # marks an uncontrolled regime (S-0004/D-6); trace_ref turns escalation triage
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
    # (S-0004/telemetry-staged: an --agent fake override must not masquerade as a model).
    kind: str

    def run(self, ctx: AgentContext) -> AgentResult: ...


# ....................... #


@dataclass(frozen=True)
class BrokerRoute:
    """One routed provider (S-0021): the wire destination and the name
    of the environment variable holding the key in the broker's own
    environment — names, never values (S-0001/D-13)."""

    provider: str
    upstream: str  # the provider's real base URL
    key_env: str  # env var name; the value lives only in the broker's process
    via_proxy: bool = False  # S-0021/A-1: the broker tunnels this upstream through https_proxy


# ....................... #


@dataclass(frozen=True)
class BrokerRouting:
    """The run's routing (S-0021/metering-and-the-number-the-subject-did-not-write, S-0021/D-4): every provider this run's
    agents may use, dispatch-checked before the broker opens. The broker
    exposes one loopback route per routed provider and refuses anything
    else at the wire."""

    routes: tuple[BrokerRoute, ...] = ()

    def route_for(self, provider: str) -> BrokerRoute | None:
        return next((route for route in self.routes if route.provider == provider), None)


# ....................... #


@dataclass(frozen=True)
class BrokerBudget:
    """The run's token bound (S-0021/metering-and-the-number-the-subject-did-not-write): the task contract's
    `budget.tokens`, held by the broker and enforced mid-run — requests past
    it are refused and the run escalates `cost_anomaly`. None is unbounded."""

    tokens: int | None = None


# ....................... #


@dataclass(frozen=True)
class BrokerHandle:
    """What the sandbox needs and nothing else (S-0021/the-port): a base URL
    per routed provider and a per-run bearer token the broker issued and
    revokes at close. Both are operator non-secret knobs — they ride the
    tier command inline (S-0017/configuration-routes-by-nature), never a spec env."""

    token: str
    base_urls: dict[str, str] = field(default_factory=dict)
    # The intake route's base URL (S-0045/the-intake-route), empty when the run has no
    # channel. The same server and the same token: what separates this from
    # a provider route is the path, and what separates it from a store
    # credential is that the sandbox can only ask, never write directly.
    channel_url: str = ""

    def url_for(self, provider: str) -> str | None:
        return self.base_urls.get(provider)


# ....................... #


@dataclass(frozen=True)
class BrokerUsage:
    """Counts and metadata only (S-0021/D-7): request count, token counts per
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


@dataclass(frozen=True)
class BurnEvent:
    """One metered provider response (S-0045/liveness-is-the-burn-stream): the provider's own
    numbers, as the wire reported them. Emitted per call rather than summed
    at close, because a rate is the form the question "is this attempt
    working?" is actually asked in — and an attempt with no recent burn is
    not working, whatever it would say about itself (S-0045/D-4)."""

    provider: str
    tokens: int
    cost_usd: float | None


# A sink the broker calls, in the request thread, once per metered response.
# It must not raise and must not block: the run's egress path is not a place
# to do I/O, and an observer that can break a run is not an observer.
BurnSink = Callable[[BurnEvent], None]


# ....................... #


@dataclass(frozen=True)
class AttemptFact:
    """One thing that became true inside a run, carried out at the moment it
    did (S-0044 S-0044/D-3).

    The runner loops attempts internally: one dispatch can be three attempts
    under three tiers with three gate verdicts. Summarising that afterwards
    loses the only question worth asking of a poison ceiling — what changed
    between the tries — so each attempt reports itself as it happens. The
    runner knows nothing about who is listening; `kind` names the fact and
    `payload` carries the fact's own fields.
    """

    kind: Literal["attempt_started", "attempt_finished", "gates_evaluated"]
    attempt: int
    payload: dict[str, Any]


# Same contract as the burn sink above: called on the runner's thread, must
# not raise, must not block.
AttemptSink = Callable[[AttemptFact], None]


# ....................... #

# Called host-side between an attempt and the gate pass that judges it: it
# records whatever the attempt wrote into the system of record and rewrites
# the worktree's log from what the record then holds (S-0044 S-0044/A-3). Unlike
# the sinks above this one may raise and may block — the gate is fail-closed,
# and a battery that cannot verify what it is judging must not run.
JournalSync = Callable[[Path], None]


# ....................... #


class RunChannel(Protocol):
    """The run's own route into the system of record (S-0045/the-intake-route).

    Built host-side for one run and handed to the broker, which is why
    identity cannot be forged: the channel already knows whose run it is, so
    a request never states its actor, partition or subject and could not be
    believed if it did (S-0045/D-2). The broker calls this on behalf of a
    sandbox that holds no store credential of its own (S-0045/D-1).
    """

    def record(self, kind: str, payload: dict[str, Any]) -> None:
        """Append one record the agent is authorized to write. Raises for a
        kind outside that set, or a payload its kind rejects."""

        ...

    def notes(self) -> list[dict[str, Any]]:
        """The notes addressed to this run, oldest first (S-0045/D-7). A poll —
        nothing here interrupts an agent."""

        ...

    def records(self) -> list[dict[str, Any]]:
        """What this run has already recorded, oldest first. A sandbox that
        posts through the channel never sees its own entries in the
        worktree — the engine writes that file at the next gate pass — so
        reading them back is the only way an attempt can check its own
        bookkeeping before it is judged on it."""

        ...


# ....................... #


class Broker(Protocol):
    """The egress broker port (S-0021/the-port): holds every provider
    credential the run needs, exposes one loopback route per routed
    provider, injects the key and meters at the wire, and refuses requests
    past the run's budget. Adapters: `local` (a reverse proxy the runner
    starts on loopback for the life of the run), `opensandbox` (the server's
    vault and egress control behind the same port, when a server exists),
    `none` (today's behaviour, named explicitly).

    The handle's fields reach the sandbox through the tier command — the
    channel S-0017/configuration-routes-by-nature assigns to operator non-secret knobs."""

    name: str  # "local" | "opensandbox" | "none"

    def open(
        self,
        run: str,
        routing: BrokerRouting,
        budget: BrokerBudget,
        sink: BurnSink | None = None,
        channel: RunChannel | None = None,
    ) -> BrokerHandle: ...

    def usage(self, handle: BrokerHandle) -> BrokerUsage:
        """Live counters, mid-run: the runner reads them to escalate
        `cost_anomaly` while the run is still in progress (S-0021/D-6)."""

        ...

    def close(self, handle: BrokerHandle) -> BrokerUsage: ...


# ....................... #


class Vcs(Protocol):
    """Local git at the runner boundary (S-0010/two-ports-deliberately-separate): the agent produces a
    tree, the runner produces the commit — author is the agent identity,
    committer is Torve, and the signing key, when configured, never enters
    a sandbox (S-0010/D-3)."""

    def commit_all(
        self, worktree: Path, message: str, author: str | None = None, sign_key: str | None = None
    ) -> str | None: ...

    def changed_names(self, worktree: Path) -> list[str]: ...

    def push(
        self, worktree: Path, branch: str, token: str | None = None, supersede: bool = False
    ) -> bool: ...

    def republish_branch(self, root: Path, branch: str, token: str | None = None) -> bool: ...

    def revert(self, worktree: Path, shas: list[str]) -> bool: ...


# ....................... #


class LaneVcs(Protocol):
    """The serialized lane's git surface (S-0006/the-correction-this-document-exists-for): ancestry questions,
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

    def reset_branch(self, root: Path, branch: str, to_sha: str) -> None: ...

    def remove_worktree(self, root: Path, workdir: Path) -> None: ...

    def merge_ff(self, root: Path, ref: str) -> str: ...

    def approver(self, root: Path) -> str: ...


# ....................... #


class Scm(Protocol):
    def open_pr(self, worktree: Path, branch: str, title: str, body: str) -> str: ...


# ....................... #


@dataclass
class PrInfo:
    """One pull request as the forge reports it (S-0005/triggers): enough to
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
    """The PR-review trigger's forge surface (S-0005/triggers, S-0005/D-2): the
    runner reads the pull request and posts the findings comment; the
    reviewer itself never holds a forge credential."""

    def pr_info(self, number: int) -> PrInfo: ...

    def comment(self, number: int, body: str, key: str) -> str: ...


# ....................... #


class PrVcs(Protocol):
    """The PR-review trigger's git surface: fetch the pull request's head
    and base, materialise a detached worktree to review, diff, and read
    the landings the range adds to map the head back to a task contract
    (S-0059/D-12)."""

    def fetch_pr(
        self, root: Path, number: int, base_ref: str, token: str | None = None
    ) -> tuple[str, str]: ...

    def worktree_at(self, root: Path, sha: str, workdir: Path) -> None: ...

    def remove_worktree(self, root: Path, workdir: Path) -> None: ...

    def diff(self, root: Path, base: str, head: str) -> str: ...

    def landed_tasks(self, root: Path, base: str, head: str) -> list[str]: ...


# ....................... #


class CiStatus(Protocol):
    """The remote's CI verdict for one commit (S-0006/promotion): the lane's
    `ci: green_on_current_head` requirement consults this before landing.
    The adapter owns the polling — backoff against a lightweight endpoint,
    because the rate budget is shared with the agents (§1) — and returns a
    settled word: "success", a failure conclusion, "pending" when the
    budget ran out mid-run, or "absent" when the remote never saw the
    commit. Only "success" lands."""

    def conclusion(self, sha: str) -> str: ...


# ....................... #


@dataclass(frozen=True)
class Notification:
    """One escalation, addressed (S-0051/the-port).

    Composed from records and from nothing else: the reason and detail are
    what the escalation recorded, never a finding's own words, because the
    engine does not judge what a finding said. `event_id` is the
    escalation's own, which is what makes this a delivery *of* something
    and what a destination dedups on (S-0051/D-6).
    """

    task_id: str
    partition: str
    reason: str
    detail: str
    at: datetime
    event_id: str
    age_s: float


# ....................... #


class TransientDelivery(RuntimeError):
    """The destination might take this later — a timeout, a 5xx, a refused
    connection. Recorded nowhere and retried on the next pass (S-0051/D-6); a
    refusal a retry will not fix raises RuntimeError instead."""


# ....................... #


class Notifier(Protocol):
    """One destination for a notification (S-0051 S-0051/D-3).

    The domain never names a destination: webhook, email or pager is an
    adapter behind this, and adding one is a wiring edit. `deliver` returns
    the destination's own receipt, which the record keeps — a delivery you
    cannot point at afterwards is indistinguishable from one that did not
    happen.
    """

    name: str

    def deliver(self, notification: Notification) -> str: ...
