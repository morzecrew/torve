"""RFC 0024's fleet-wide readings: survey every root's escalation queue,
decide the pause once for the fleet total, refuse a root whose own
configuration exceeds its manifest trust class, and read every root into
one table ordered by escalation age.

The pass these readings gate is the manager's (tests/test_fleet_serve.py).
The tick that used to run in its place is gone (A-105), and with it the
cases that were about its legs rather than about these rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from torve.application.fleet import (
    decide_pause,
    fleet_escalations,
    survey,
)
from torve.application.runstate import RunState
from torve.base import naming
from torve.config.fleet import (
    FleetAttention,
    FleetManifest,
    FleetRepository,
    TrustRefused,
    enforce_trust,
)
from torve.config.runconfig import load_runner_config
from torve.domain.states import EscalationReason, TaskState

# ----------------------- #


def root(tmp_path: Path, name: str) -> Path:
    r = tmp_path / name
    (r / ".torve").mkdir(parents=True)
    (r / ".torve" / "gates.yaml").write_text("schema_version: 1\ngates: []\n", encoding="utf-8")
    return r


def contract(root: Path, task_id: str) -> None:
    task_dir = root / ".torve" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "contract.yaml").write_text(
        f"schema_version: 1\nid: {task_id}\nrole: implement\nintent: work\ndecisions: []\n",
        encoding="utf-8",
    )


def escalate(root: Path, task_id: str) -> RunState:
    state = RunState(task_id=task_id, path=naming.state_file(root, task_id))
    state.state = TaskState.RUNNING
    state.save()
    state.escalate(EscalationReason.BLOCKER_FINDING, "unresolved")
    return state


def manifest(
    *repos: FleetRepository, pause_escalations: int = 1, order: str = "manifest"
) -> FleetManifest:
    return FleetManifest(
        repositories=list(repos),
        attention=FleetAttention(pause_escalations=pause_escalations),
        order=order,
    )


def test_survey_reads_each_roots_escalation_queue(tmp_path: Path):
    a, b = root(tmp_path, "a"), root(tmp_path, "b")
    escalate(a, "T-1")
    m = manifest(
        FleetRepository(root=str(a), trust="own"), FleetRepository(root=str(b), trust="own")
    )
    assert survey(m) == {str(a): 1, str(b): 0}


def test_decide_pause_is_the_fleet_total_not_a_per_root_check(tmp_path: Path):
    a, b = root(tmp_path, "a"), root(tmp_path, "b")
    escalate(a, "T-1")
    m = manifest(
        FleetRepository(root=str(a), trust="own"),
        FleetRepository(root=str(b), trust="own"),
        pause_escalations=2,
    )
    assert decide_pause(m, survey(m)) == (1, False)

    escalate(b, "T-2")
    assert decide_pause(m, survey(m)) == (2, True)


# ----------------------- #
# Trust (D-24.6). These were tick cases; the check they exercise is a pure
# function of one repository and its own configuration, so they are asked
# of it directly now — `serve_fleet` calls the same one before every pass
# (tests/test_fleet_serve.py), and the tick that used to is gone (A-105).
# ....................... #


def refusal(root_path: Path, trust_class: str) -> str:
    repo = FleetRepository(root=str(root_path), trust=trust_class)

    with pytest.raises(TrustRefused) as caught:
        enforce_trust(repo, load_runner_config(root_path))

    return str(caught.value)


def test_a_reviewed_root_configured_for_socket_mode_is_refused(tmp_path: Path):
    a = root(tmp_path, "a")
    (a / ".torve" / "config.yaml").write_text("runtime:\n  docker: socket\n", encoding="utf-8")

    message = refusal(a, "reviewed")
    assert "reviewed" in message and "runtime.docker: socket" in message


def test_a_reviewed_root_relying_on_the_default_provider_allowlist_is_refused(tmp_path: Path):
    a = root(tmp_path, "a")
    (a / ".torve" / "config.yaml").write_text(
        "providers:\n  default: [deepseek]\n", encoding="utf-8"
    )

    message = refusal(a, "reviewed")
    assert "reviewed" in message and "providers.default" in message


def test_an_untrusted_root_without_a_sealed_broker_is_refused(tmp_path: Path):
    a = root(tmp_path, "a")

    assert "sealed" in refusal(a, "untrusted")


def test_an_untrusted_root_asking_for_host_networking_is_refused(tmp_path: Path):
    a = root(tmp_path, "a")
    (a / ".torve" / "config.yaml").write_text("runtime:\n  network: host\n", encoding="utf-8")

    message = refusal(a, "untrusted")
    assert "untrusted" in message and "runtime.network: host" in message


def test_an_untrusted_root_configured_for_sealed_mode_passes(tmp_path: Path):
    a = root(tmp_path, "a")
    (a / ".torve" / "config.yaml").write_text(
        "runtime:\n"
        "  network: fleet-internal\n"
        "broker:\n"
        "  adapter: local\n"
        "  mode: sealed\n"
        "  network: fleet-internal\n",
        encoding="utf-8",
    )

    # No refusal: the configuration this trust class asks for is the one it
    # has, and enforcement says nothing else about it.
    enforce_trust(FleetRepository(root=str(a), trust="untrusted"), load_runner_config(a))


# ----------------------- #


def test_fleet_status_orders_escalations_by_age_across_roots(tmp_path: Path):
    a, b = root(tmp_path, "a"), root(tmp_path, "b")
    escalate(a, "T-OLD")
    old = RunState.load(naming.state_file(a, "T-OLD"))
    old.heartbeat = "2001-01-01T00:00:00.000000Z"
    old.save()
    escalate(b, "T-NEW")

    m = manifest(
        FleetRepository(root=str(a), trust="own"), FleetRepository(root=str(b), trust="own")
    )
    rows = fleet_escalations(m)
    assert [r.task_id for r in rows] == ["T-OLD", "T-NEW"]
    assert [r.root for r in rows] == [str(a), str(b)]


def test_fleet_status_ignores_non_escalated_runs(tmp_path: Path):
    a = root(tmp_path, "a")
    state = RunState(task_id="T-1", path=naming.state_file(a, "T-1"))
    state.state = TaskState.RUNNING
    state.save()
    m = manifest(FleetRepository(root=str(a), trust="own"))
    assert fleet_escalations(m) == []
