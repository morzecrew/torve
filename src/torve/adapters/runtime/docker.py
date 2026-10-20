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
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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


# The hosts that mean "this machine's own loopback": a proxy on one of them is
# reachable from the host and, under a chosen network mode, from the sandbox —
# but not from a nested sandbox, which holds the side container's namespace
# instead of the host's (S-0111/D-6).
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# The proxy variables that carry a URL, as opposed to `no_proxy`'s host list:
# only a proxy a loopback host names is rewritten, never the host list.
PROXY_URL_ENV = tuple(name for name in PROXY_ENV if name != "no_proxy")


# ....................... #


def _proxy_host(value: str) -> str | None:
    return urlsplit(value).hostname


def host_loopback_proxies() -> dict[str, str]:
    """The host environment's proxy variables whose host is loopback, by the
    spelling the variable carries — the ones a nested sandbox cannot reach at
    that address (S-0111/D-6)."""

    found: dict[str, str] = {}

    for name in PROXY_URL_ENV:
        for variant in (name, name.upper()):
            value = os.environ.get(variant)

            if value and _proxy_host(value) in LOOPBACK_HOSTS:
                found[variant] = value

    return found


# ....................... #


def rewrite_loopback(url: str, gateway: str) -> str:
    """*url* with a loopback host replaced by *gateway* (S-0111/D-6), preserving
    scheme, credentials and port. Anything not a loopback URL is returned
    unchanged: the rewrite is for the one address a nested sandbox lost, never a
    general rewrite of what the host forwards."""

    parts = urlsplit(url)

    if parts.hostname not in LOOPBACK_HOSTS:
        return url

    host = f"[{gateway}]" if ":" in gateway else gateway
    netloc = f"{host}:{parts.port}" if parts.port else host

    if parts.username:
        credentials = parts.username + (f":{parts.password}" if parts.password else "")
        netloc = f"{credentials}@{netloc}"

    return urlunsplit(parts._replace(netloc=netloc))


# ....................... #


# A nested sandbox (S-0111/D-1): the side container's name is the
# sandbox's plus this, and the sandbox reaches the daemon over the namespace it
# shares with it. The address is the one the side container's own dockerd serves:
# the dind image's entrypoint enables TLS on 2376 unless `DOCKER_TLS_CERTDIR` is
# cleared, so the side container is started with it empty and plain 2375 is where
# the daemon listens — the same address the readiness probe dials, so a daemon that
# answers to the probe is one the sandbox's CLI can actually reach.
NESTED_SUFFIX = "-dockerd"
DOCKER_HOST = "tcp://127.0.0.1:2375"

# The dockerd entrypoint's own switch for a registry mirror; one per mirror.
REGISTRY_MIRROR_FLAG = "--registry-mirror"


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
        self,
        *args: str,
        timeout: float | None = None,
        stdin: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        # *env* overlays the docker CLI's own environment, so a value an `-e NAME`
        # argument forwards never shows in the process's argument list.
        return subprocess.run(
            [self.docker, *args],
            capture_output=True,
            text=True,
            # A sandboxed command's output is whatever bytes it printed: one
            # that is not UTF-8 is a gate's output, not an infrastructure failure.
            errors="replace",
            timeout=timeout,
            check=False,
            input=stdin,
            env={**os.environ, **env} if env else None,
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

    def _loopback_gateway(self) -> str:
        """The address a nested sandbox's loopback proxy is rewritten to
        (S-0111/D-6): the side container — and so the sandbox sharing its
        namespace — sits on the daemon's default bridge, whose gateway is the
        host side of that network, where the host's proxy must answer. Resolved
        only when a forwarded proxy is on loopback, so a host with no such proxy
        asks docker nothing."""

        if not host_loopback_proxies():
            return ""

        return self._network_gateway("bridge")

    # ....................... #

    @staticmethod
    def _mounts_cache(spec: SandboxSpec) -> bool:
        """A spec carrying the fixed cache mount is the whole signal: the
        runner composed the slot-suffixed volume name, the decision that a
        warm tier's sandboxes point their toolchains at it lives here
        (S-0035/D-4), and shadow runs simply never carry the mount (S-0035/D-3)."""

        return CACHE_MOUNT in spec.volumes.values()

    # ....................... #

    @staticmethod
    def _proxy_args() -> list[str]:
        """The host's proxy variables, name-only, for a container's own tools
        and its daemon. An ordinary non-sealed sandbox forwards them under a
        chosen network mode; a nested sandbox takes the side container's
        namespace instead of a mode, so it and the side container forward them
        here rather than losing egress to the branch they no longer take. Name-only
        always: a rewritten value travels in the docker CLI's environment
        (`_proxy_env`), so a proxy's credentials never reach an argument list."""

        return [
            arg
            for name in PROXY_ENV
            for variant in (name, name.upper())
            if variant in os.environ
            for arg in ("-e", variant)
        ]

    # ....................... #

    @staticmethod
    def _proxy_env(gateway: str) -> dict[str, str]:
        """The loopback proxies rewritten to *gateway*, the host side of the side
        container's network (S-0111/D-6), by the variable that carries each: the
        values a nested sandbox's `-e NAME` arguments forward in place of the
        host's. A proxy that needs no rewrite keeps the host's own value."""

        if not gateway:
            return {}

        return {
            variant: rewrite_loopback(value, gateway)
            for variant, value in host_loopback_proxies().items()
        }

    # ....................... #

    def _run_args(self, spec: SandboxSpec, workspace: Path, side: str = "") -> list[str]:
        """The `docker run` argument list for a spec — pure, so the mount
        and env wiring is testable without a daemon. *side* names the nested
        side container this sandbox joins (S-0111/D-1), when it has one: then the
        sandbox shares the side container's network namespace and reaches its
        daemon at `DOCKER_HOST`, and the run's own network mode does not apply."""

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

        if side:
            # A nested sandbox joins the side container's namespace instead of
            # taking a network mode of its own (S-0111/D-1): its loopback then holds
            # the daemon, so published ports answer on `localhost`.
            args += ["--network", f"container:{side}", "-e", f"DOCKER_HOST={DOCKER_HOST}"]
            args += self._proxy_args()

        elif self.network:
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
                args += self._proxy_args()

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

        for host, mount in spec.readonly_binds.items():
            # `ro` is the point, not an optimisation (S-0062/D-5): the cache is
            # shared between seats and the attempt must not be able to edit
            # what it was equipped with, nor the manifest describing it.
            args += ["-v", f"{host}:{mount}:ro"]

        args += [spec.image, "sleep", str(int(spec.timeout_s))]

        return args

    # ....................... #

    def _side_args(self, spec: SandboxSpec, workspace: Path, side: str) -> list[str]:
        """The `docker run` argument list for a nested sandbox's side container
        (S-0111/D-2): privileged, carrying the sandbox's labels so the reaper
        removes it with the sandbox, the worktree at the sandbox's own workdir
        so a compose file's relative binds resolve to the same files, and the
        slot's docker-state volume at `/var/lib/docker` so pulled images
        survive the task.

        `DOCKER_TLS_CERTDIR` is cleared: the dind image's entrypoint otherwise
        generates certificates and serves TLS on 2376, where the sandbox's
        `DOCKER_HOST` never looks — the daemon must listen plain on 2375, the
        address the sandbox and the readiness probe both dial."""

        assert spec.nested is not None

        mount_mode = ":ro" if spec.workspace_read_only else ""

        args = [
            "run",
            "-d",
            "--init",
            "--name",
            side,
            "--privileged",
            "-e",
            "DOCKER_TLS_CERTDIR=",
            "-v",
            f"{workspace.resolve()}:{spec.workdir}{mount_mode}",
            "-w",
            spec.workdir,
            "-v",
            # No volume name is an anonymous volume, which `rm -v` removes with
            # the side container: a shadow run's daemon starts and ends cold.
            f"{spec.nested.volume}:/var/lib/docker" if spec.nested.volume else "/var/lib/docker",
        ]

        # The side daemon pulls the task's images itself, so it needs the host's
        # proxy too (S-0111/D-2): without it a mirrored or proxied registry is
        # unreachable and every `docker pull` in the sandbox fails. A loopback
        # proxy is rewritten for the side container as well, since it shares the
        # namespace the rewrite is meant for (S-0111/D-6).
        args += self._proxy_args()

        for key, value in spec.labels.items():
            args += ["--label", f"{key}={value}"]

        args += [spec.nested.image]

        for mirror in spec.nested.registry_mirrors:
            args += [f"{REGISTRY_MIRROR_FLAG}={mirror}"]

        return args

    # ....................... #

    def _await_daemon(self, side: str, ready_s: float) -> bool:
        """Whether the side container's daemon answers at the sandbox's own
        address, `DOCKER_HOST`, within *ready_s* (S-0111/D-3): a sandbox that asked
        for Docker either has it or does not run, so a daemon that never comes
        up fails the creation rather than letting a Docker-bound test skip
        green.

        The probe dials `DOCKER_HOST` from inside the side container rather than
        the local unix socket: it is the exact endpoint the sandbox reaches, so a
        daemon answering it is one the sandbox's CLI can use — a TLS-only daemon
        on 2376 answers the unix socket and nothing else, and would read ready
        here while the sandbox's Docker-bound gate failed."""

        deadline = time.monotonic() + ready_s

        while True:
            # Each probe gets only what is left of the clock: a stalled dockerd
            # hangs `docker info`, and an unbounded call would never come back to
            # the deadline check below.
            try:
                proc = self._run(
                    "exec",
                    side,
                    "sh",
                    "-c",
                    f"DOCKER_HOST={DOCKER_HOST} docker info",
                    timeout=max(0.1, deadline - time.monotonic()),
                )
            except subprocess.TimeoutExpired:
                return False

            if proc.returncode == 0:
                return True

            if time.monotonic() >= deadline:
                return False

            time.sleep(max(0.0, min(0.5, deadline - time.monotonic())))

    # ....................... #

    # What a nested daemon is cleared of before a task starts (S-0111/D-2): the
    # containers, volumes and networks a previous task in this slot left, never
    # the images — what was pulled stays pulled. Run once the daemon answers, so
    # the task's own names and ports cannot collide with a corpse. Every container
    # goes, running ones too: the daemon restarts a `restart: always` service
    # from the last task, and a prune skips what runs. `volume prune -a` takes
    # named volumes, which a plain prune keeps since Docker 23. Chained with
    # `&&` and stderr kept, so a failed step fails the clearing and says why.
    NESTED_PRUNE = (
        'ids=$(docker ps -aq) && { [ -z "$ids" ] || docker rm -f -v $ids >/dev/null; } && '
        "docker volume prune -a -f >/dev/null && "
        "docker network prune -f >/dev/null"
    )

    # ....................... #

    def _create_nested(self, spec: SandboxSpec, workspace: Path) -> SandboxHandle:
        """Start the daemon, join the sandbox to it, or fail the creation and
        remove what was started (S-0111/D-1..D-3)."""

        assert spec.nested is not None

        side = f"{spec.name}{NESTED_SUFFIX}"
        proxies = self._proxy_env(self._loopback_gateway())

        started = self._run(*self._side_args(spec, workspace, side), env=proxies)

        if started.returncode != 0:
            raise DockerError(started.stderr.strip() or "nested side container failed to start")

        if not self._await_daemon(side, spec.nested.ready_s):
            self._run("rm", "-f", "-v", side)
            raise DockerError(
                f"nested sandbox {spec.name}: its daemon did not answer `docker info` "
                f"within {spec.nested.ready_s:.0f}s — the side container was removed"
            )

        try:
            pruned = self._run("exec", side, "sh", "-c", self.NESTED_PRUNE, timeout=120)
        except subprocess.TimeoutExpired:
            # A stalled prune fails the creation like any other: the side
            # container is removed below rather than left running.
            pruned = subprocess.CompletedProcess(
                [], 1, "", "nested sandbox: the side daemon's prune timed out"
            )

        if pruned.returncode != 0:
            self._run("rm", "-f", "-v", side)
            raise DockerError(
                pruned.stderr.strip() or "nested sandbox: the side daemon would not prune"
            )

        proc = self._run(*self._run_args(spec, workspace, side), env=proxies)

        if proc.returncode != 0:
            # D-3: both containers are removed — the sandbox never came up, and
            # the side container is a daemon nothing will use.
            self._run("rm", "-f", "-v", side)
            raise DockerError(proc.stderr.strip() or "docker run failed")

        return SandboxHandle(id=proc.stdout.strip(), name=spec.name)

    # ....................... #

    def create(self, spec: SandboxSpec, workspace: Path) -> SandboxHandle:
        if spec.nested is not None:
            # A nested sandbox mounts the same cache volume, so it takes the
            # same ownership step below.
            handle = self._create_nested(spec, workspace)

        else:
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

        return handle

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
        # A nested sandbox's side container goes with it (S-0111/D-2); `-f` makes
        # the removal a no-op for the ordinary sandbox that never had one.
        self._run("rm", "-f", "-v", f"{handle.name}{NESTED_SUFFIX}")

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
