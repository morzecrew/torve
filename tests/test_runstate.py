"""The run state's write (S-0124/D-5): a unit's candidates may save together,
so the dump and the replace happen under one lock and two writers never stage
through the same temporary file at once."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from torve.application import runstate
from torve.application.runstate import RunState

# ----------------------- #


def test_candidate_names_are_saved_under_one_lock(tmp_path: Path, monkeypatch) -> None:
    """S-0124/D-5: four candidate writers save the same run state at once: the replace
    calls never overlap, none of them raises for a temporary file a sibling
    took, and the file left behind parses whole."""

    state = RunState(task_id="T-0001", path=tmp_path / "T-0001.state.json")
    state.save()

    real_replace = runstate.os.replace
    guard = threading.Lock()
    active = 0
    peak = 0
    calls = 0

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        nonlocal active, peak, calls

        with guard:
            active += 1
            calls += 1
            peak = max(peak, active)

        time.sleep(0.05)

        with guard:
            active -= 1

        real_replace(source, destination)

    monkeypatch.setattr(runstate.os, "replace", recording_replace)

    gate = threading.Barrier(4)
    errors: list[BaseException] = []

    def writer() -> None:
        gate.wait()

        try:
            state.save()

        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(4)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert errors == []
    assert calls == 4
    # The lock spans the dump and the replace, so only one writer is ever
    # inside the replacement at a time.
    assert peak == 1
    assert RunState.load(state.path).task_id == "T-0001"


def test_every_live_sandbox_survives_a_save_and_is_named_once(tmp_path):
    """S-0124/D-4, T-0848: the candidates' sandboxes are on the record a kill
    or a reap reads from another process."""

    state = RunState(task_id="T-9901", path=tmp_path / "T-9901.state.json")
    state.sandboxes = ["sbx-1", "sbx-2"]
    state.sandbox_id = "sbx-2"
    state.save()

    reloaded = RunState.load(state.path)

    assert reloaded.sandboxes == ["sbx-1", "sbx-2"]
    assert reloaded.live_sandboxes() == ["sbx-1", "sbx-2"]
    assert RunState(
        task_id="T-9902", path=tmp_path / "x.json", sandbox_id="s"
    ).live_sandboxes() == ["s"]


def test_the_reaper_destroys_every_candidate_s_sandbox(tmp_path):
    """S-0124/D-4, T-0848: side-by-side candidates each hold a sandbox, and
    the reap takes all of them, not only the one `sandbox_id` names."""

    from test_reaper import ListingWorkspace, MockRuntime, state_at

    from torve.application.reaper import clear_escalated
    from torve.domain.states import EscalationReason, TaskState

    state = state_at(tmp_path, "T-9802", TaskState.RUNNING)
    state.sandboxes = ["sbx-1", "sbx-2"]
    state.sandbox_id = "sbx-2"
    state.escalate(EscalationReason.POISON_CEILING, "3 attempts, ceiling 3")

    runtime = MockRuntime()
    workspace = ListingWorkspace([("T-9802", tmp_path / ".wt" / "T-9802")])

    assert clear_escalated(tmp_path, runtime, workspace, "T-9802") is True

    assert runtime.destroyed == ["sbx-1", "sbx-2"]


def test_the_kill_destroys_every_candidate_s_sandbox(repo, monkeypatch):
    """S-0124/D-4, T-0848: a kill mid-unit takes every candidate's sandbox."""

    from types import SimpleNamespace

    from test_lane_prevention import active_run
    from typer.testing import CliRunner

    import torve.cli.run as run_cli
    from torve.base import naming
    from torve.cli.main import app

    destroyed: list[str] = []
    monkeypatch.setattr(
        run_cli, "runtime_for", lambda *_args: SimpleNamespace(destroy_by_id=destroyed.append)
    )
    repo.seed()
    state = active_run(repo.root, "T-8007", ["src/**"])
    state.sandboxes = ["sbx-a", "sbx-b"]
    state.sandbox_id = "sbx-b"
    state.save()

    result = CliRunner().invoke(
        app, ["kill", "T-8007", "--root", str(repo.root), "--format", "json"]
    )
    assert result.exit_code == 0, result.output

    assert destroyed == ["sbx-a", "sbx-b"]
    reloaded = RunState.load(naming.state_file(repo.root, "T-8007"))
    assert reloaded.sandboxes == []
    assert reloaded.sandbox_id is None
