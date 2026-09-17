"""The attempt's steps over a dispatch (S-0046/the-steps).

Conviction-routed tier advancement (S-0027/D-11) is the piece with a decision
attached and no I/O in it: given a dispatch and a state it resolves the rung
the last pass's convictions select, moves the regime, and hands back the
Agent to run. Before the restructuring it could only be reached through a
whole run — a workspace, a runtime, an agent, a VCS, an SCM and a store, none
of which it touches.
"""

import pytest
import yaml

from torve.application.dispatch import Dispatch, RunDeps
from torve.application.runstate import RunState
from torve.application.session import advance_tier
from torve.config.runconfig import RunnerConfig, TierConfig
from torve.domain.attempt import GateResult
from torve.domain.task import Task

# ----------------------- #

TASK_ID = "T-0001"

MANIFEST = {
    "schema_version": 1,
    "gates": [
        {
            "name": "acceptance",
            "run": "true",
            "state": "blocking",
            "origin": "structural",
            "axis": "functional",
        },
        {
            "name": "tidy",
            "run": "true",
            "state": "blocking",
            "origin": "structural",
            "axis": "form",
        },
    ],
}


# ....................... #


class _StubRuntime:
    def __init__(self) -> None:
        self.resolved: list[str] = []

    def resolve_image(self, image: str) -> str | None:
        self.resolved.append(image)

        return f"sha256:{image or 'none'}"


# ....................... #


def _config() -> RunnerConfig:
    seat = TierConfig(adapter="api", provider="p", model="cheap")
    seat.retry_variants = {"form": "executor.tidy"}

    return RunnerConfig(
        tiers={
            "executor": seat,
            "executor.tidy": TierConfig(
                adapter="api", provider="p", model="neat", image="tidy-image"
            ),
        }
    )


# ....................... #


def _dispatch(tmp_path, *, retry_agent=None) -> Dispatch:
    manifest = tmp_path / ".torve" / "gates.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(yaml.safe_dump(MANIFEST), encoding="utf-8")

    config = _config()
    deps = RunDeps(
        workspace=None,  # type: ignore[arg-type]  # unreached: no sandbox is opened
        runtime=_StubRuntime(),  # type: ignore[arg-type]
        agent="seat-agent",  # type: ignore[arg-type]
        vcs=object(),  # type: ignore[arg-type]
        scm=None,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
        retry_agent=retry_agent,
    )

    return Dispatch(
        root=tmp_path,
        task=Task(id=TASK_ID, decisions=[]),
        config=config,
        deps=deps,
        worktree=tmp_path,
        shadow=False,
        gates_base=None,
        resume=False,
        tier_name="executor",
        tier=config.tiers["executor"],
        image="seat-image",
        image_digest="sha256:seat",
        meta={},
    )


# ....................... #


def _state(*facts: str) -> RunState:
    state = RunState(task_id=TASK_ID, path=None)  # type: ignore[arg-type]  # never saved
    state.history = [{"at": "", "from": "", "to": "", "fact": fact} for fact in facts]

    return state


# ....................... #


def _convicted(name: str) -> GateResult:
    return GateResult(name=name, outcome="fail", state="blocking", exit_code=1)


# ....................... #


def test_a_gate_red_routes_the_next_attempt_to_the_mapped_rung(tmp_path):
    """S-0027/D-11: the attempt after a red resolves the rung the recorded
    convictions select — at the most severe axis present — and the regime
    moves with it, so the record stamps the tier that produced the work."""

    run = _dispatch(tmp_path, retry_agent=lambda tier: f"agent-for-{tier.model}")
    run.convictions = [_convicted("tidy")]

    agent = advance_tier(run, _state("gates red: tidy=fail", "attempt 2 dispatched"))

    assert agent == "agent-for-neat"
    assert run.tier_name == "executor.tidy"
    assert run.tier.model == "neat"
    assert run.image == "tidy-image"
    assert run.image_digest == "sha256:tidy-image"


# ....................... #


def test_a_red_with_no_mapped_rung_stays_on_the_seat(tmp_path):
    """The seat maps `form` and nothing else here. A functional conviction
    resolves no rung, so the attempt continues under the task's own tier —
    a red is not by itself a reason to change the regime."""

    run = _dispatch(tmp_path, retry_agent=lambda tier: f"agent-for-{tier.model}")
    run.convictions = [_convicted("acceptance")]

    agent = advance_tier(run, _state("gates red: acceptance=fail", "attempt 2 dispatched"))

    assert agent == "seat-agent"
    assert run.tier_name == "executor"


# ....................... #


def test_no_agent_factory_means_the_regime_never_moves(tmp_path):
    """Never fabricated (S-0027/D-1): advancement fires only where the CLI wired
    a factory that can actually build the rung's Agent, so telemetry can
    never stamp a tier that did not produce the work."""

    run = _dispatch(tmp_path, retry_agent=None)
    run.convictions = [_convicted("tidy")]

    agent = advance_tier(run, _state("gates red: tidy=fail", "attempt 2 dispatched"))

    assert agent == "seat-agent"
    assert run.tier_name == "executor"


# ....................... #


def test_an_advance_is_never_sticky(tmp_path):
    """One rung, not a ratchet: an attempt that did not follow a red goes
    back to the task's own tier even when the previous one was routed away
    from it."""

    run = _dispatch(tmp_path, retry_agent=lambda tier: f"agent-for-{tier.model}")
    run.convictions = [_convicted("tidy")]
    advance_tier(run, _state("gates red: tidy=fail", "attempt 2 dispatched"))

    assert run.tier_name == "executor.tidy"

    agent = advance_tier(run, _state("agent exited 0; gates running", "attempt 3 dispatched"))

    assert agent == "seat-agent"
    assert run.tier_name == "executor"
    assert run.image_digest == "sha256:python:3.13-slim"  # back to the seat's own image


# ....................... #


@pytest.mark.parametrize("history", [[], ["attempt 1 dispatched"]])
def test_the_first_attempt_has_no_previous_pass_to_route_from(tmp_path, history):
    run = _dispatch(tmp_path, retry_agent=lambda tier: f"agent-for-{tier.model}")

    assert advance_tier(run, _state(*history)) == "seat-agent"
    assert run.tier_name == "executor"


# ....................... #
# Which authentication route a seat takes (S-0063/D-18, S-0064/D-9)


def _auth(tier, slot=0, *, key_env="K"):
    """`_sandbox_auth` against a config whose record names the credential — the
    name comes off the provider now, never off the harness that dials it."""

    from torve.application.session import _sandbox_auth
    from torve.config.providers import Provider, Route

    record = Provider(
        name=tier.provider or "p",
        key_env=key_env,
        routes={"openai": Route(base_url="https://p.test/v1")},
    )
    config = RunnerConfig(provider_records={tier.provider: record} if tier.provider else {})

    return _sandbox_auth(config, tier, slot)


def test_a_seat_whose_provider_names_a_variable_mounts_nothing():
    """The record decides, not the adapter and not the manifest. One token for
    one attempt is the narrower blast radius, and every harness this repository
    dispatches to has an env form."""

    seat = TierConfig(
        adapter="subscription",
        provider="anthropic",
        image="claude-sandbox:2.1.252",
    )
    names, volumes = _auth(seat, key_env="CLAUDE_CODE_OAUTH_TOKEN")

    assert names == ("CLAUDE_CODE_OAUTH_TOKEN",)
    assert volumes == {}


def test_a_seat_whose_provider_has_no_record_falls_back_to_its_volume():
    """The route for a harness with no env form. Read-write, because the
    harness refreshes its token mid-session and a mount it cannot write to
    hangs rather than failing."""

    from torve.application.session import _sandbox_auth

    seat = TierConfig(adapter="subscription", provider="openai", image="codex-sandbox:1")
    names, volumes = _sandbox_auth(RunnerConfig(), seat, 3)

    assert names == ()
    assert volumes == {"torve-auth-3": "/auth"}


def test_the_adapter_no_longer_decides_the_route():
    """The defect the first live dispatch found: keying on the adapter dropped
    a subscription seat's `api_key_env` and mounted a volume whether it
    declared one or not, so the agent reported `Not logged in` against an
    empty mount."""

    from torve.application.session import _sandbox_auth

    for adapter in ("api", "harness", "subscription"):
        assert _auth(TierConfig(adapter=adapter, provider="p", image="i")) == (("K",), {}), adapter

    assert _sandbox_auth(RunnerConfig(), TierConfig(), 0) == ((), {})


# ....................... #
# Equipment lands where torve owns it (S-0063/D-19)


def test_the_equipment_root_is_excluded_in_the_worktree(tmp_path):
    """`commit_all` runs `git add -A` and the scope gate reads the committed
    diff, so anything `equip` drops into the workspace is in the candidate
    before a gate can object.

    In a *linked worktree* — which is what every attempt runs in — git reads
    `info/exclude` from the common gitdir and never from the worktree's own, so
    the obvious spelling writes a file nothing consults. This asserts on
    `check-ignore`, which is the only thing that proves the rule is live."""

    import subprocess

    from torve.application.session import _exclude_equip_root

    main = tmp_path / "repo"
    main.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(main)], check=True)
    (main / "seed").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(main), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(main),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--no-gpg-sign",
            "-m",
            "seed",
        ],
        check=True,
    )
    worktree = tmp_path / "wt"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(worktree)], check=True)

    _exclude_equip_root(worktree, ".dsh/skills")
    (worktree / ".dsh" / "skills" / "tdd").mkdir(parents=True)
    (worktree / ".dsh" / "skills" / "tdd" / "SKILL.md").write_text("x", encoding="utf-8")

    ignored = subprocess.run(
        ["git", "-C", str(worktree), "check-ignore", ".dsh/skills/tdd/SKILL.md"],
        capture_output=True,
        text=True,
    )

    assert ignored.returncode == 0, "the equipment root is not excluded in the worktree"
    assert (
        ".dsh"
        not in subprocess.run(
            ["git", "-C", str(worktree), "status", "--porcelain", "-uall"],
            capture_output=True,
            text=True,
        ).stdout
    )

    exclude = (main / ".git" / "info" / "exclude").read_text(encoding="utf-8")

    assert "/.dsh/skills/" in exclude.splitlines()

    # Twice is once: an attempt retries, and a file that grows a line per
    # attempt is a file nobody reads.
    _exclude_equip_root(worktree, ".dsh/skills")
    again = (main / ".git" / "info" / "exclude").read_text(encoding="utf-8")

    assert again == exclude


def test_a_harness_reading_the_mount_excludes_nothing(tmp_path):
    """claude points `--add-dir` at the read-only mount, so nothing lands in
    the workspace and there is nothing to hide."""

    import subprocess

    from torve.application.session import _exclude_equip_root

    worktree = tmp_path / "wt"
    worktree.mkdir()
    subprocess.run(["git", "init", "-q", str(worktree)], check=True)
    _exclude_equip_root(worktree, "")
    exclude = worktree / ".git" / "info" / "exclude"

    assert not exclude.is_file() or "/.dsh" not in exclude.read_text(encoding="utf-8")


# ....................... #
# The arm reaches the attempt as a composed prompt (S-0082/D-1, S-0082/D-2)


def _arm_session(tmp_path, **arm) -> dict:
    """Drive one attempt under *arm* and hand back what the agent was given,
    what the worktree carried when it looked, and the record the attempt left."""

    import asyncio
    import json
    import subprocess

    from torve.application.ports import AgentResult, SandboxHandle
    from torve.application.runner import drive_attempts, real_hooks
    from torve.domain.states import TaskState

    seen: dict = {}

    class LookingAgent:
        kind = "harness"

        def run(self, ctx):
            pack = ctx.workspace / ".torve" / "context" / "index.md"
            seen["prompt"] = ctx.prompt
            seen["pack"] = pack.is_file()
            seen["contract"] = (ctx.workspace / ".torve" / "tasks" / TASK_ID).is_dir()
            seen["skills"] = sorted(p.name for p in (ctx.workspace / ".torve" / "skills").iterdir())

            return AgentResult(exit_code=1, output="", cost_usd=0.0, model_version="m")

    class InertRuntime:
        def create(self, spec, workspace):
            return SandboxHandle(id="h-1", name=spec.name)

        def resolve_image(self, image):
            return None

        def sync_out(self, handle, worktree):
            pass

        def destroy(self, handle):
            pass

    worktree = tmp_path / "wt"
    (worktree / ".torve" / "skills").mkdir(parents=True)
    (worktree / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\ngates: []\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=worktree, check=True)

    config = RunnerConfig(
        poison_ceiling=1,
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": TierConfig(adapter="harness", provider="p", model="m"),
        },
    )
    task = Task(id=TASK_ID, decisions=[], intent="make the thing work, and say why")
    deps = RunDeps(
        workspace=None,  # type: ignore[arg-type]
        runtime=InertRuntime(),  # type: ignore[arg-type]
        agent=LookingAgent(),  # type: ignore[arg-type]
        vcs=None,  # type: ignore[arg-type]
        scm=None,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
    )
    state = RunState(task_id=task.id, path=tmp_path / "state.json")
    state.transition(TaskState.CLAIMED, "test claim")

    asyncio.run(
        drive_attempts(
            state,
            task,
            config,
            real_hooks(tmp_path, task, config, deps, worktree, shadow=True, **arm),
        )
    )

    ledger = (tmp_path / ".torve" / "telemetry.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in ledger.splitlines() if line.strip()]
    seen["agent_block"] = next(row["agent"] for row in rows if "agent" in row)

    return seen


# ....................... #


@pytest.mark.parametrize("arm", [{"arm": "bare"}, {"bare": True}, {"arm": "gated"}])
def test_an_arm_whose_prompt_is_bare_is_handed_it_and_nothing_else(tmp_path, arm):
    """S-0082/D-1: the prompt's removal is a property of the replay, handed to
    the session through the composed-prompt seam the review leg already uses —
    so the adapter composes nothing and the intent is the whole prompt.

    S-0082/D-2: the worktree is stripped of what the runner would have written
    into it — the projected contract, the skill set, the context pack — and the
    record names the removals that were in force, so a reader of the arm's
    numbers can tell what the agent could still open."""

    seen = _arm_session(tmp_path, **arm)

    assert seen["prompt"] == f"# Torve task {TASK_ID}\n\nmake the thing work, and say why"
    assert not seen["pack"]
    assert not seen["contract"]
    assert seen["skills"] == []
    assert seen["agent_block"]["arm"] == arm.get("arm", "bare")
    assert "prompt" in seen["agent_block"]["removed"]
    assert "context-pack" in seen["agent_block"]["removed"]
    assert ("battery" in seen["agent_block"]["removed"]) == (arm.get("arm", "bare") == "bare")


# ....................... #


def test_the_configured_arm_composes_its_prompt_in_the_adapter_as_it_did(tmp_path):
    """The other side of S-0082/D-1: torve as configured hands the adapter no
    prompt — composing one is the adapter's, from the apparatus the runner
    writes into the worktree — and its record names no removal at all."""

    seen = _arm_session(tmp_path)

    assert seen["prompt"] is None
    assert seen["pack"]
    assert "removed" not in seen["agent_block"]
    assert "arm" not in seen["agent_block"]
