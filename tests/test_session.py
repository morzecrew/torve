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
    seat = TierConfig(adapter="api", command="c", provider="p", model="cheap")
    seat.retry_variants = {"form": "executor.tidy"}

    return RunnerConfig(
        tiers={
            "executor": seat,
            "executor.tidy": TierConfig(
                adapter="api", command="c", provider="p", model="neat", image="tidy-image"
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
