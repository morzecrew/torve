"""Standing maintenance (RFC 0023 phase 1): the contract format, the
command predicate evaluated with no agent, the RFC 0020 lint reused
unchanged, instantiation through the adoption path, and the bounds that
keep the leg from outpacing triage."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from torve.application.ports import ExecResult, SandboxHandle
from torve.application.runstate import RunState
from torve.application.standing import (
    STANDING_RECORD,
    PredicateError,
    StandingContract,
    Trigger,
    evaluate_predicate,
    instantiate,
    lint_job_body,
    load_standing_contracts,
    standing_leg,
)
from torve.base import naming
from torve.config import layout
from torve.config.runconfig import RunnerConfig
from torve.domain.states import TaskState

# ----------------------- #


class ScriptedRuntime:
    """Records what it was asked to run; hands back one exit code per call,
    the last one repeating once exhausted."""

    def __init__(self, exit_codes: list[int | None]) -> None:
        self.exit_codes = list(exit_codes)
        self.created = 0
        self.destroyed = 0
        self.commands: list[str] = []
        self.read_only: list[bool] = []

    def create(self, spec, workspace):
        self.created += 1
        self.read_only.append(spec.workspace_read_only)
        return SandboxHandle(id=f"sbx-{self.created}", name=spec.name)

    def exec(self, handle, command, timeout_s):
        self.commands.append(command)
        code = self.exit_codes.pop(0) if self.exit_codes else 0
        return ExecResult(exit_code=code, output="", duration_s=0.0)

    def destroy(self, handle):
        self.destroyed += 1


# ....................... #


@pytest.fixture
def seeded(repo):
    repo.seed()
    repo.git("checkout", "-q", "main")
    return repo


def job_dict(
    name: str = "lockfile-drift",
    *,
    run: str = "uv lock --check",
    allow: list[str] | None = None,
    acceptance: list[str] | None = None,
    decisions_from: str | None = None,
    cooldown_hours: float = 0.0,
    max_open: int = 1,
    strike_limit: int = 3,
) -> dict:
    return {
        "name": name,
        "trigger": {"kind": "command", "run": run},
        "intent": "refresh the drifted lock and confirm the suite passes under the refreshed pins",
        "scope": {"allow": allow if allow is not None else ["src/app.py", "tests/test_app.py"]},
        "acceptance": acceptance if acceptance is not None else ["true"],
        "decisions_from": decisions_from,
        "cooldown_hours": cooldown_hours,
        "max_open": max_open,
        "strike_limit": strike_limit,
    }


# ....................... #


def path_digest_job_dict(
    name: str = "pin-drift",
    *,
    paths: list[str] | None = None,
    allow: list[str] | None = None,
    acceptance: list[str] | None = None,
    cooldown_hours: float = 0.0,
    max_open: int = 1,
    strike_limit: int = 3,
) -> dict:
    return {
        "name": name,
        "trigger": {"kind": "path-digest", "paths": paths if paths is not None else ["src/app.py"]},
        "intent": "the pinned reference moved; re-pin it",
        "scope": {"allow": allow if allow is not None else ["src/app.py", "tests/test_app.py"]},
        "acceptance": acceptance if acceptance is not None else ["true"],
        "decisions_from": None,
        "cooldown_hours": cooldown_hours,
        "max_open": max_open,
        "strike_limit": strike_limit,
    }


# ....................... #


def abandon(root: Path, task_id: str) -> None:
    """Drives a fresh run state through to ABANDONED (QUEUED -> CLAIMED ->
    ESCALATED -> ABANDONED, all legal per domain.states.TRANSITIONS) so
    strike-limit tests can simulate a job whose instances kept failing to
    land."""

    state = RunState(task_id=task_id, path=naming.state_file(root, task_id))
    state.transition(TaskState.CLAIMED, "test")
    state.transition(TaskState.ESCALATED, "test")
    state.transition(TaskState.ABANDONED, "test")
    state.save()


def write_job(root: Path, job: dict, filename: str | None = None) -> Path:
    directory = layout.standing_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (filename or f"{job['name']}.yaml")
    path.write_text(yaml.safe_dump(job, sort_keys=False), encoding="utf-8")
    return path


# ----------------------- #
# The contract format (D-23.1, D-23.5).


def test_trigger_requires_a_command_line_for_command_kind():
    with pytest.raises(ValidationError, match="empty"):
        Trigger(kind="command", run="  ")
    Trigger(kind="command", run="uv lock --check")  # does not raise


def test_trigger_requires_paths_for_path_digest_kind():
    with pytest.raises(ValidationError, match="paths"):
        Trigger(kind="path-digest", paths=[])
    Trigger(kind="path-digest", paths=["src/app.py"])  # does not raise


def test_standing_contract_rejects_bad_bounds():
    with pytest.raises(ValidationError, match="max_open"):
        StandingContract.model_validate({**job_dict(), "max_open": 0})
    with pytest.raises(ValidationError, match="cooldown_hours"):
        StandingContract.model_validate({**job_dict(), "cooldown_hours": -1})
    with pytest.raises(ValidationError, match="name"):
        StandingContract.model_validate({**job_dict(), "name": "  "})


def test_standing_contract_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        StandingContract.model_validate({**job_dict(), "enabled": True})


# ----------------------- #
# Loading (D-23.7: an empty or absent directory is off, not misconfigured).


def test_load_standing_contracts_absent_directory_is_off(tmp_path: Path):
    assert load_standing_contracts(tmp_path) == ([], [])


def test_load_standing_contracts_parses_a_committed_job(tmp_path: Path):
    write_job(tmp_path, job_dict())
    jobs, errors = load_standing_contracts(tmp_path)
    assert errors == []
    assert len(jobs) == 1
    assert jobs[0].name == "lockfile-drift"
    assert jobs[0].trigger.run == "uv lock --check"


def test_load_standing_contracts_records_malformed_yaml(tmp_path: Path):
    directory = layout.standing_dir(tmp_path)
    directory.mkdir(parents=True)
    (directory / "broken.yaml").write_text("name: [unterminated\n", encoding="utf-8")
    jobs, errors = load_standing_contracts(tmp_path)
    assert jobs == []
    assert any("not YAML" in e for e in errors)


def test_load_standing_contracts_records_a_schema_error(tmp_path: Path):
    write_job(tmp_path, {**job_dict(), "max_open": 0}, "bad.yaml")
    jobs, errors = load_standing_contracts(tmp_path)
    assert jobs == []
    assert any("max_open" in e and "bad.yaml" in e for e in errors)


def test_load_standing_contracts_refuses_duplicate_names(tmp_path: Path):
    write_job(tmp_path, job_dict(), "a.yaml")
    write_job(tmp_path, job_dict(), "b.yaml")
    jobs, errors = load_standing_contracts(tmp_path)
    assert len(jobs) == 2
    assert any("duplicate" in e for e in errors)


# ----------------------- #
# The lint, unchanged (D-23.9).


def test_lint_job_body_names_the_job_not_a_draft_ref(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    job = StandingContract.model_validate(
        {**job_dict(allow=["src/missing.py"]), "acceptance": []}
    )
    errors = lint_job_body(tmp_path, job)
    assert any("lockfile-drift" in e for e in errors)
    assert any("acceptance is empty" in e for e in errors)


def test_lint_job_body_green_on_a_healthy_body(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "tests" / "test_app.py").write_text("def test(): pass\n", encoding="utf-8")
    job = StandingContract.model_validate(job_dict())
    assert lint_job_body(tmp_path, job) == []


# ----------------------- #
# The predicate (D-23.2, D-23.3).


def test_evaluate_predicate_nonzero_exit_is_due(tmp_path: Path):
    job = StandingContract.model_validate(job_dict())
    runtime = ScriptedRuntime([1])
    assert evaluate_predicate(job, tmp_path, RunnerConfig(), runtime) is True
    assert runtime.commands == ["uv lock --check"]
    assert runtime.read_only == [True]  # D-23.2's read-only isolation
    assert runtime.created == runtime.destroyed == 1


def test_evaluate_predicate_zero_exit_is_not_due(tmp_path: Path):
    job = StandingContract.model_validate(job_dict())
    runtime = ScriptedRuntime([0])
    assert evaluate_predicate(job, tmp_path, RunnerConfig(), runtime) is False


def test_evaluate_predicate_timeout_is_an_error_not_a_verdict(tmp_path: Path):
    job = StandingContract.model_validate(job_dict())
    runtime = ScriptedRuntime([None])
    with pytest.raises(PredicateError, match="timed out"):
        evaluate_predicate(job, tmp_path, RunnerConfig(), runtime)
    assert runtime.destroyed == 1  # the sandbox is still cleaned up


def test_evaluate_predicate_path_digest_with_no_prior_firing_is_due(seeded):
    # Nothing to compare against yet reads as 'differs': the first
    # evaluation fires to record a baseline.
    job = StandingContract.model_validate(path_digest_job_dict())
    assert evaluate_predicate(job, seeded.root, RunnerConfig(), ScriptedRuntime([])) is True


def test_evaluate_predicate_path_digest_unchanged_content_is_not_due(seeded):
    job = StandingContract.model_validate(path_digest_job_dict())
    instantiate(seeded.root, job, RunnerConfig())  # records the baseline digest
    assert evaluate_predicate(job, seeded.root, RunnerConfig(), ScriptedRuntime([])) is False


def test_evaluate_predicate_path_digest_changed_content_is_due(seeded):
    job = StandingContract.model_validate(path_digest_job_dict())
    instantiate(seeded.root, job, RunnerConfig())
    seeded.write("src/app.py", "print('moved')\n")
    assert evaluate_predicate(job, seeded.root, RunnerConfig(), ScriptedRuntime([])) is True


def test_evaluate_predicate_path_digest_never_touches_the_sandbox(seeded):
    job = StandingContract.model_validate(path_digest_job_dict())
    runtime = ScriptedRuntime([])
    evaluate_predicate(job, seeded.root, RunnerConfig(), runtime)
    assert runtime.created == 0


# ----------------------- #
# Instantiation through the adoption path, unchanged (D-23.4).


def test_instantiate_mints_through_adoption_and_records_origin(seeded):
    job = StandingContract.model_validate(job_dict())
    task_id = instantiate(seeded.root, job, RunnerConfig())

    contract = yaml.safe_load(
        (seeded.root / ".torve" / "tasks" / task_id / "contract.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert contract["role"] == "implement"
    assert contract["decisions"] == []

    sidecar = json.loads(
        (seeded.root / ".torve" / "tasks" / task_id / STANDING_RECORD).read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["job"] == "lockfile-drift"

    log = subprocess.run(
        ["git", "-C", str(seeded.root), "log", "-1", "--format=%s"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "adopt" in log


def test_instantiate_resolves_decisions_from_a_bare_rfc_id(seeded):
    seeded.write(
        "rfcs/0012-fixture.md",
        "\n".join(
            [
                "---",
                'id: "0012"',
                "title: Fixture",
                "status: accepted",
                "owner: t",
                "schema_version: 1",
                "---",
                "",
                "## Decisions",
                "",
                "| # | Grade | Decision | Paths | Consequence |",
                "| --- | --- | --- | --- | --- |",
                "| D-12.1 | `LOCKED` | The rule | `src/**` | — |",
                "",
            ]
        ),
    )
    seeded.commit("fixture rfc")
    job = StandingContract.model_validate(job_dict(decisions_from="0012"))
    task_id = instantiate(seeded.root, job, RunnerConfig())

    contract = yaml.safe_load(
        (seeded.root / ".torve" / "tasks" / task_id / "contract.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert contract["decisions"] == [
        {"id": "D-12.1", "grade": "LOCKED", "text": "The rule", "paths": ["src/**"]}
    ]


def test_instantiate_records_the_path_digest_baseline(seeded):
    job = StandingContract.model_validate(path_digest_job_dict())
    task_id = instantiate(seeded.root, job, RunnerConfig())

    sidecar = json.loads(
        (seeded.root / ".torve" / "tasks" / task_id / STANDING_RECORD).read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["digest"]
    assert isinstance(sidecar["digest"], str)


def test_instantiate_two_firings_differ_only_in_id(seeded):
    # The comparability D-23.5 exists for (RFC 0023 §12's exit criterion).
    job = StandingContract.model_validate(job_dict())
    first = instantiate(seeded.root, job, RunnerConfig())
    second = instantiate(seeded.root, job, RunnerConfig())
    assert first != second

    def without_id(task_id: str) -> dict:
        data = yaml.safe_load(
            (seeded.root / ".torve" / "tasks" / task_id / "contract.yaml").read_text(
                encoding="utf-8"
            )
        )
        data.pop("id", None)
        return data

    assert without_id(first) == without_id(second)


# ----------------------- #
# The leg's bounds (D-23.6, D-23.11).


def test_standing_leg_fires_a_due_job(seeded):
    write_job(seeded.root, job_dict())
    detail, moved = standing_leg(seeded.root, RunnerConfig(), ScriptedRuntime([1]), lambda _t: False)
    assert moved
    assert "fired 1" in detail
    assert len(list((seeded.root / ".torve" / "tasks").glob("T-*"))) == 1


def test_standing_leg_skips_a_job_that_is_not_due(seeded):
    write_job(seeded.root, job_dict())
    detail, moved = standing_leg(seeded.root, RunnerConfig(), ScriptedRuntime([0]), lambda _t: False)
    assert not moved
    assert "no standing jobs due" in detail
    assert not list((seeded.root / ".torve" / "tasks").glob("T-*"))


def test_standing_leg_respects_max_open(seeded):
    write_job(seeded.root, job_dict(max_open=1))
    landed: dict[str, bool] = {}
    detail, moved = standing_leg(
        seeded.root, RunnerConfig(), ScriptedRuntime([1]), lambda t: landed.get(t, False)
    )
    assert moved

    # The instance is still open (queued, not landed): a second due
    # predicate must not mint another while max_open holds.
    detail, moved = standing_leg(
        seeded.root, RunnerConfig(), ScriptedRuntime([1]), lambda t: landed.get(t, False)
    )
    assert not moved
    assert "max_open" in detail
    assert len(list((seeded.root / ".torve" / "tasks").glob("T-*"))) == 1

    # Once the instance lands, the job is open to fire again.
    task_dirs = [p.name for p in (seeded.root / ".torve" / "tasks").glob("T-*")]
    landed[task_dirs[0]] = True
    detail, moved = standing_leg(
        seeded.root, RunnerConfig(), ScriptedRuntime([1]), lambda t: landed.get(t, False)
    )
    assert moved
    assert len(list((seeded.root / ".torve" / "tasks").glob("T-*"))) == 2


def test_standing_leg_respects_cooldown_hours(seeded):
    # max_open=2 keeps this test isolated to the cooldown bound alone.
    write_job(seeded.root, job_dict(cooldown_hours=24, max_open=2))
    detail, moved = standing_leg(seeded.root, RunnerConfig(), ScriptedRuntime([1]), lambda _t: False)
    assert moved

    # Immediately again: inside the cooldown, mints nothing even though due.
    detail, moved = standing_leg(seeded.root, RunnerConfig(), ScriptedRuntime([1]), lambda _t: False)
    assert not moved
    assert "cooldown" in detail

    # Age the sidecar past the cooldown window: due again.
    task_dir = next((seeded.root / ".torve" / "tasks").glob("T-*"))
    sidecar = task_dir / STANDING_RECORD
    record = json.loads(sidecar.read_text(encoding="utf-8"))
    stale = datetime.now(UTC) - timedelta(hours=25)
    record["at"] = stale.strftime("%Y-%m-%dT%H:%M:%SZ")
    sidecar.write_text(json.dumps(record), encoding="utf-8")

    detail, moved = standing_leg(seeded.root, RunnerConfig(), ScriptedRuntime([1]), lambda _t: False)
    assert moved


def test_standing_leg_bounds_total_firings_per_tick(seeded):
    write_job(seeded.root, job_dict(name="job-a", allow=["src/app.py", "tests/test_app.py"]))
    write_job(
        seeded.root,
        job_dict(name="job-b", allow=["src/other.py"], acceptance=["true"]),
    )
    (seeded.root / "src" / "other.py").write_text("x = 1\n", encoding="utf-8")
    seeded.commit("add other module")

    config = RunnerConfig()
    config.loop.standing_max_per_tick = 1
    detail, moved = standing_leg(seeded.root, config, ScriptedRuntime([1, 1]), lambda _t: False)
    assert moved
    assert "fired 1" in detail
    assert "skipped 1" in detail
    assert len(list((seeded.root / ".torve" / "tasks").glob("T-*"))) == 1


def test_standing_leg_predicate_error_mints_nothing(seeded):
    write_job(seeded.root, job_dict())
    detail, moved = standing_leg(
        seeded.root, RunnerConfig(), ScriptedRuntime([None]), lambda _t: False
    )
    assert not moved
    assert "error" in detail
    assert not list((seeded.root / ".torve" / "tasks").glob("T-*"))

    events = [
        json.loads(line)
        for line in (seeded.root / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]
    assert any(e.get("event") == "standing_predicate_error" for e in events)


def test_standing_leg_contract_lint_red_mints_nothing(seeded):
    # A due predicate whose body the lint refuses (T-0113: an existing
    # module without its existing test) must fail closed too.
    write_job(seeded.root, job_dict(allow=["src/app.py"]))
    detail, moved = standing_leg(seeded.root, RunnerConfig(), ScriptedRuntime([1]), lambda _t: False)
    assert not moved
    assert "contract lint red" in detail
    assert not list((seeded.root / ".torve" / "tasks").glob("T-*"))


def test_standing_leg_invalid_contract_is_reported_and_skipped(seeded):
    write_job(seeded.root, {**job_dict(), "max_open": 0}, "bad.yaml")
    detail, moved = standing_leg(seeded.root, RunnerConfig(), ScriptedRuntime([]), lambda _t: False)
    assert not moved
    assert "1 error" in detail
    events = [
        json.loads(line)
        for line in (seeded.root / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]
    assert any(e.get("event") == "standing_contract_invalid" for e in events)


def test_standing_leg_with_no_committed_jobs_is_a_quiet_noop(seeded):
    detail, moved = standing_leg(seeded.root, RunnerConfig(), ScriptedRuntime([]), lambda _t: False)
    assert not moved
    assert detail == "no standing jobs due"


# ----------------------- #
# path-digest through the leg (D-23.8).


def test_standing_leg_path_digest_fires_once_then_waits_for_a_change(seeded):
    write_job(seeded.root, path_digest_job_dict(max_open=2))

    detail, moved = standing_leg(
        seeded.root, RunnerConfig(), ScriptedRuntime([]), lambda _t: False
    )
    assert moved
    assert "fired 1" in detail
    assert len(list((seeded.root / ".torve" / "tasks").glob("T-*"))) == 1

    # Unchanged content: not due, mints nothing.
    detail, moved = standing_leg(
        seeded.root, RunnerConfig(), ScriptedRuntime([]), lambda _t: False
    )
    assert not moved
    assert len(list((seeded.root / ".torve" / "tasks").glob("T-*"))) == 1

    # The declared path's content changes: due again.
    seeded.write("src/app.py", "print('moved')\n")
    detail, moved = standing_leg(
        seeded.root, RunnerConfig(), ScriptedRuntime([]), lambda _t: False
    )
    assert moved
    assert len(list((seeded.root / ".torve" / "tasks").glob("T-*"))) == 2


# ----------------------- #
# Self-disable, the fourth bound (D-23.6, RFC 0023 §5.4).


def test_standing_leg_self_disables_after_strike_limit_consecutive_non_landings(seeded):
    write_job(seeded.root, job_dict(max_open=1, strike_limit=2))
    landed: dict[str, bool] = {}

    def is_landed(task_id: str) -> bool:
        return landed.get(task_id, False)

    # Fire, then abandon without landing: strike one.
    _detail, moved = standing_leg(seeded.root, RunnerConfig(), ScriptedRuntime([1]), is_landed)
    assert moved
    first = next((seeded.root / ".torve" / "tasks").glob("T-*")).name
    abandon(seeded.root, first)

    # Fire again, abandon again: strike two, at the limit.
    _detail, moved = standing_leg(seeded.root, RunnerConfig(), ScriptedRuntime([1]), is_landed)
    assert moved
    second = next(
        p.name for p in (seeded.root / ".torve" / "tasks").glob("T-*") if p.name != first
    )
    abandon(seeded.root, second)

    # Third tick: two consecutive non-landings at strike_limit=2 — self-disabled,
    # the predicate is never even consulted.
    detail, moved = standing_leg(seeded.root, RunnerConfig(), ScriptedRuntime([1]), is_landed)
    assert not moved
    assert "self-disabled" in detail
    assert len(list((seeded.root / ".torve" / "tasks").glob("T-*"))) == 2

    events = [
        json.loads(line)
        for line in (seeded.root / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]
    assert any(
        e.get("event") == "standing_self_disabled" and e.get("job") == "lockfile-drift"
        for e in events
    )


def test_standing_leg_a_landing_resets_the_strike_streak(seeded):
    write_job(seeded.root, job_dict(max_open=1, strike_limit=2))
    landed: dict[str, bool] = {}

    def is_landed(task_id: str) -> bool:
        return landed.get(task_id, False)

    # One abandoned firing (strike one)...
    standing_leg(seeded.root, RunnerConfig(), ScriptedRuntime([1]), is_landed)
    first = next((seeded.root / ".torve" / "tasks").glob("T-*")).name
    abandon(seeded.root, first)

    # ...then one that lands: the streak resets to zero.
    _detail, moved = standing_leg(seeded.root, RunnerConfig(), ScriptedRuntime([1]), is_landed)
    assert moved
    second = next(
        p.name for p in (seeded.root / ".torve" / "tasks").glob("T-*") if p.name != first
    )
    landed[second] = True

    # Due again: not self-disabled, because the last instance landed.
    _detail, moved = standing_leg(seeded.root, RunnerConfig(), ScriptedRuntime([1]), is_landed)
    assert moved
    assert len(list((seeded.root / ".torve" / "tasks").glob("T-*"))) == 3


def test_flake_threshold_reads_the_engines_own_records(tmp_path):
    """A-68's third kind: telemetry's flaky_count_by_command summed, the
    gate manifest's quarantine list honoured, both read with the engine's
    parsers — never a regex over YAML."""
    import json

    from torve.application.standing import _flake_over_threshold

    torve_dir = tmp_path / ".torve"
    torve_dir.mkdir()
    telemetry = torve_dir / "telemetry.jsonl"
    telemetry.write_text(
        json.dumps({"flaky_count_by_command": {"uv run pytest": 2}}) + "\n"
        + json.dumps({"flaky_count_by_command": {"uv run pytest": 1, "ruff check": 1}}) + "\n",
        encoding="utf-8",
    )

    assert _flake_over_threshold(tmp_path, 3) is True  # pytest sums to 3
    assert _flake_over_threshold(tmp_path, 4) is False

    (torve_dir / "gates.yaml").write_text(
        "schema_version: 1\ngates: []\nquarantine:\n  - uv run pytest\n",
        encoding="utf-8",
    )
    # The quarantined command no longer fires the job (a landed response
    # stops the refire); ruff's count of 1 stays under threshold.
    assert _flake_over_threshold(tmp_path, 3) is False
