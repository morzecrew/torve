"""`torve fleet tick` / `torve fleet status` — manifest resolution and
per-root wiring (RFC 0024). The mechanism itself (survey, shared pause,
order, failure-recorded continuation) is exercised at the application layer
in test_fleet.py; these tests cover the CLI's own plumbing."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from torve.application.runstate import RunState
from torve.base import naming
from torve.cli.main import app
from torve.domain.states import EscalationReason, TaskState

runner = CliRunner()


def fixture_root(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / ".torve").mkdir(parents=True)
    (root / ".torve" / "gates.yaml").write_text("schema_version: 1\ngates: []\n", encoding="utf-8")
    return root


def write_manifest(path: Path, *roots: Path) -> Path:
    body = "repositories:\n" + "".join(f"  - root: {r}\n    trust: own\n" for r in roots)
    path.write_text(body, encoding="utf-8")
    return path


def test_fleet_tick_fails_config_when_no_manifest_exists(tmp_path: Path):
    result = runner.invoke(
        app, ["fleet", "tick", "--manifest", str(tmp_path / "nope.yaml"), "--format", "json"]
    )
    assert result.exit_code == 3
    assert "no fleet manifest" in result.stderr


def test_fleet_tick_ticks_every_root_in_the_manifest(tmp_path: Path):
    a, b = fixture_root(tmp_path, "a"), fixture_root(tmp_path, "b")
    manifest_path = write_manifest(tmp_path / "fleet.yaml", a, b)

    result = runner.invoke(
        app, ["fleet", "tick", "--manifest", str(manifest_path), "--format", "json"]
    )
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
    assert payload["paused"] is False
    assert payload["escalated_total"] == 0
    assert {row["root"] for row in payload["roots"]} == {str(a), str(b)}
    assert all(row["outcome"] == "ticked" for row in payload["roots"])
    # D-24.11: the fleet event lands in each ticked root's own telemetry.
    for r in (a, b):
        lines = (r / ".torve" / "telemetry.jsonl").read_text(encoding="utf-8").splitlines()
        assert any(json.loads(line).get("event") == "fleet_tick" for line in lines)


def test_fleet_status_shows_the_oldest_escalation_across_roots(tmp_path: Path):
    a, b = fixture_root(tmp_path, "a"), fixture_root(tmp_path, "b")

    def escalate(root: Path, task_id: str) -> None:
        state = RunState(task_id=task_id, path=naming.state_file(root, task_id))
        state.state = TaskState.RUNNING
        state.save()
        state.escalate(EscalationReason.BLOCKER_FINDING, "unresolved")

    escalate(a, "T-OLD")
    old = RunState.load(naming.state_file(a, "T-OLD"))
    old.heartbeat = "2001-01-01T00:00:00.000000Z"
    old.save()
    escalate(b, "T-NEW")

    manifest_path = write_manifest(tmp_path / "fleet.yaml", a, b)
    result = runner.invoke(
        app, ["fleet", "status", "--manifest", str(manifest_path), "--format", "json"]
    )
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
    assert [row["task_id"] for row in payload["escalations"]] == ["T-OLD", "T-NEW"]


def test_fleet_status_fails_config_when_no_manifest_exists(tmp_path: Path):
    result = runner.invoke(
        app, ["fleet", "status", "--manifest", str(tmp_path / "nope.yaml"), "--format", "json"]
    )
    assert result.exit_code == 3
    assert "no fleet manifest" in result.stderr
