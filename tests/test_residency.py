"""The resident manager (RFC 0044 §5.4, D-44.5).

The loop itself is four lines; what is worth testing is what it refuses to
carry between passes. Minting reads the board rather than remembering what
it minted, an idle pass records nothing, and a second manager over the same
log picks up exactly where the first stopped — which is the whole of what
"restart transparency" has to mean for the process that holds the engine.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from forze.application.execution import DepsRegistry, ExecutionRuntime

from torve.adapters.eventstore.document import mock_module
from torve.application.eventlog import event_log
from torve.application.manager import project
from torve.application.residency import contracts, mint, once, serve
from torve.application.worker import Outcome, Worker
from torve.domain.events import EventKind
from torve.domain.states import TaskState

PARTITION = "morzecrew/torve"


def contract(root: Path, task_id: str, *, role: str = "implement", allow: str = "src/**") -> None:
    task_dir = root / ".torve" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "contract.yaml").write_text(
        f"schema_version: 1\nid: {task_id}\nrole: {role}\nintent: work here\n"
        f"scope: {{allow: ['{allow}']}}\nacceptance: []\ndecisions: []\n",
        encoding="utf-8",
    )


def landing(**overrides) -> Outcome:
    fields = {
        "attempt": 1,
        "exit_code": 0,
        "gates_exit_code": 0,
        "gate_outcomes": {"scope": "pass"},
        "landed_sha": "a" * 40,
    }

    return Outcome(**{**fields, **overrides})


def run(scenario):
    """One runtime per case; the mock log lives and dies with the scope."""

    async def main():
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            await scenario(event_log(runtime.get_context()))

    asyncio.run(main())


def worker_over(log, executed: list[str], outcome: Outcome | None = None) -> Worker:
    async def execute(task):
        executed.append(task.id)

        return outcome or landing()

    return Worker(log=log, name="worker-1", execute=execute)


# ....................... #


def test_only_executable_contracts_are_offered(tmp_path):
    contract(tmp_path, "T-0001")
    contract(tmp_path, "T-0002", role="review")
    (tmp_path / ".torve" / "tasks" / "T-0003").mkdir(parents=True)
    (tmp_path / ".torve" / "tasks" / "T-0003" / "contract.yaml").write_text("id: [", "utf-8")

    # A review is runner-minted mid-run and a malformed contract is not the
    # manager's emergency — neither stops the rest of the repository.
    assert list(contracts(tmp_path)) == ["T-0001"]


def test_minting_places_a_contract_on_this_partition(tmp_path):
    contract(tmp_path, "T-0001")

    async def scenario(log):
        minted = await mint(log, contracts(tmp_path), partition=PARTITION, actor_id="manager-1")

        assert minted == ["T-0001"]

        board = project(await log.since(partition=PARTITION))
        assert board.tasks["T-0001"].state is TaskState.QUEUED
        # The intent's first line stands in for a title the contract lacks,
        # so no board row is nameless.
        recorded = (await log.history("T-0001"))[0]
        assert recorded.payload["title"] == "work here"

    run(scenario)


def test_minting_twice_mints_nothing_twice(tmp_path):
    contract(tmp_path, "T-0001")

    async def scenario(log):
        tasks = contracts(tmp_path)
        await mint(log, tasks, partition=PARTITION, actor_id="manager-1")
        again = await mint(log, tasks, partition=PARTITION, actor_id="manager-1")

        assert again == []
        assert len(await log.history("T-0001")) == 1

    run(scenario)


def test_a_landed_task_is_not_minted_again(tmp_path):
    contract(tmp_path, "T-0001")

    async def scenario(log):
        executed: list[str] = []
        worker = worker_over(log, executed)

        assert await once(log, worker, tmp_path, PARTITION) == "T-0001"
        # The second pass re-reads the board and finds the task landed: a
        # mint would resurrect it as queued, which is the bug this asserts
        # against.
        assert await once(log, worker, tmp_path, PARTITION) is None
        assert executed == ["T-0001"]

        board = project(await log.since(partition=PARTITION))
        assert board.tasks["T-0001"].state is TaskState.READY

    run(scenario)


def test_a_dependency_holds_a_task_until_the_other_lands(tmp_path):
    contract(tmp_path, "T-0001")
    contract(tmp_path, "T-0002", allow="docs/**")
    path = tmp_path / ".torve" / "tasks" / "T-0002" / "contract.yaml"
    path.write_text(path.read_text() + "depends_on: [T-0001]\n", encoding="utf-8")

    async def scenario(log):
        executed: list[str] = []
        worker = worker_over(log, executed)

        assert await serve(log, worker, tmp_path, PARTITION, passes=2, idle_seconds=0) == 2
        assert executed == ["T-0001", "T-0002"]

    run(scenario)


def test_an_idle_pass_records_nothing(tmp_path):
    async def scenario(log):
        executed: list[str] = []

        assert (
            await serve(
                log, worker_over(log, executed), tmp_path, PARTITION, passes=2, idle_seconds=0
            )
            == 0
        )
        assert executed == []
        assert await log.since(partition=PARTITION) == []

    run(scenario)


def test_a_second_manager_resumes_from_the_log_alone(tmp_path):
    contract(tmp_path, "T-0001")
    contract(tmp_path, "T-0002", allow="docs/**")

    async def scenario(log):
        first: list[str] = []
        second: list[str] = []

        # One process handles one task and dies; another starts with no
        # memory of it and must neither repeat nor skip.
        await serve(log, worker_over(log, first), tmp_path, PARTITION, passes=1, idle_seconds=0)
        await serve(
            log,
            Worker(log=log, name="worker-2", execute=worker_over(log, second).execute),
            tmp_path,
            PARTITION,
            passes=1,
            idle_seconds=0,
        )

        assert first == ["T-0001"]
        assert second == ["T-0002"]

        board = project(await log.since(partition=PARTITION))
        assert board.tasks["T-0002"].claimed_by is None

    run(scenario)


def test_another_partition_sees_none_of_it(tmp_path):
    contract(tmp_path, "T-0001")

    async def scenario(log):
        executed: list[str] = []
        await once(log, worker_over(log, executed), tmp_path, PARTITION)

        # The partition is the boundary (D-44.7): a manager elsewhere reads
        # a board with nothing on it, whatever this repository holds.
        assert project(await log.since(partition="other/repo")).tasks == {}

    run(scenario)


def test_an_escalation_leaves_the_task_for_a_human(tmp_path):
    from torve.domain.states import EscalationReason

    contract(tmp_path, "T-0001")

    async def scenario(log):
        executed: list[str] = []
        worker = worker_over(
            log,
            executed,
            landing(landed_sha=None, escalation=EscalationReason.POISON_CEILING, detail="thrice"),
        )

        assert await once(log, worker, tmp_path, PARTITION) == "T-0001"
        # Escalated, not requeued: the next pass must not hand a convicted
        # task straight back to a worker.
        assert await once(log, worker, tmp_path, PARTITION) is None
        assert executed == ["T-0001"]

        board = project(await log.since(partition=PARTITION))
        assert board.tasks["T-0001"].state is TaskState.ESCALATED

    run(scenario)


def test_the_pass_records_the_facts_in_the_order_they_became_true(tmp_path):
    contract(tmp_path, "T-0001")

    async def scenario(log):
        await once(log, worker_over(log, []), tmp_path, PARTITION)

        assert [event.kind for event in await log.history("T-0001")] == [
            EventKind.TASK_MINTED,
            EventKind.TASK_CLAIMED,
            EventKind.ATTEMPT_STARTED,
            EventKind.ATTEMPT_FINISHED,
            EventKind.GATES_EVALUATED,
            EventKind.LANDING_RECORDED,
        ]

    run(scenario)


# ....................... #

# The seam to v1 (`executors`). What is asserted here is the wiring the
# manager depends on and the runner cannot see: the broker's metering
# reaches the log while the attempt is still running, and the worktree's
# divergences reach it afterwards, host-side.


@dataclass
class StubDeps:
    """The one field this seam touches. The real bundle is the CLI's to
    build; what `runner_execute` does to it is replace exactly this."""

    sink: object = None


def prepared(deps):
    """A `Prepare` that hands back a fixed bundle and the task unchanged."""

    return lambda task: (task, deps)


def test_the_attempt_burns_into_the_log_and_its_divergences_land_after(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import yaml

    from torve.application import executors as executors_module
    from torve.application.ports import BurnEvent
    from torve.config.runconfig import RunnerConfig
    from torve.domain.states import TaskState
    from torve.gates.context import load_task

    contract(tmp_path, "T-0001")
    worktree = tmp_path / ".wt" / "T-0001"
    (worktree / ".torve" / "tasks" / "T-0001").mkdir(parents=True)
    (worktree / ".torve" / "tasks" / "T-0001" / "log.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "task": "T-0001",
                "repo": "morzecrew/torve",
                "base_sha": "b" * 40,
                "drift_count": 1,
                "entries": [
                    {
                        "decision": "D-44.5",
                        "grade": "ASSUMED",
                        "kind": "departed",
                        "class": "spec-gap",
                        "attempt": 1,
                        "claim": "the tick cannot host a resident manager",
                        "evidence": "src/torve/cli/tick.py — one bounded pass, then exit",
                        "action": "departed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def fake_run_task(root, task, config, deps):
        # The broker meters on the request thread, which is this one: the
        # sink must cross back to the loop without the run waiting on it.
        deps.sink(BurnEvent(provider="anthropic", tokens=1200, cost_usd=0.03))

        return SimpleNamespace(
            state=TaskState.READY,
            attempts=1,
            escalation=None,
            worktree=str(worktree),
            landed_sha="c" * 40,
            history=[{"fact": f"committed {'c' * 10}"}],
        )

    monkeypatch.setattr(executors_module, "run_task", fake_run_task)

    async def scenario(log):
        execute = executors_module.runner_execute(
            tmp_path,
            RunnerConfig(),
            prepared(StubDeps()),
            log=log,
            partition=PARTITION,
            seat="worker-1",
        )
        outcome = await execute(
            load_task(tmp_path / ".torve" / "tasks" / "T-0001" / "contract.yaml")
        )

        assert outcome.landed_sha == "c" * 40

        # The burn crosses threads, so give the loop the one turn it needs
        # to run the append the sink scheduled.
        await asyncio.sleep(0.05)

        kinds = [event.kind for event in await log.history("T-0001")]
        assert EventKind.SEAT_CONSUMED in kinds
        assert EventKind.DIVERGENCE_RECORDED in kinds

    run(scenario)


def test_an_unobserved_run_is_still_a_run(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from torve.application import executors as executors_module
    from torve.config.runconfig import RunnerConfig
    from torve.domain.states import TaskState
    from torve.gates.context import load_task

    contract(tmp_path, "T-0001")
    seen: list[object] = []

    def fake_run_task(root, task, config, deps):
        seen.append(deps.sink)

        return SimpleNamespace(
            state=TaskState.READY,
            attempts=1,
            escalation=None,
            worktree=None,
            landed_sha=None,
            history=[],
        )

    monkeypatch.setattr(executors_module, "run_task", fake_run_task)

    execute = executors_module.runner_execute(tmp_path, RunnerConfig(), prepared(StubDeps()))
    task = load_task(tmp_path / ".torve" / "tasks" / "T-0001" / "contract.yaml")
    outcome = asyncio.run(execute(task))

    # Without a log nothing is observed and nothing is required to be: the
    # wiring is optional, the execution is not.
    assert seen == [None]
    assert outcome.exit_code == 0


# ....................... #


def test_the_serve_verb_runs_a_bounded_pass_and_says_what_it_did(tmp_path):
    import json

    from typer.testing import CliRunner

    from torve.cli.main import app

    (tmp_path / ".torve").mkdir()

    # No DSN, no contracts: the pass is idle, and an idle manager is a
    # success with nothing to report — not an error.
    result = CliRunner().invoke(
        app,
        [
            "manager",
            "serve",
            PARTITION,
            "--passes",
            "1",
            "--interval",
            "0",
            "--root",
            str(tmp_path),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["handled"] == 0
