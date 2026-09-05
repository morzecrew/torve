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
from datetime import timedelta
from pathlib import Path

from forze.application.execution import DepsRegistry, ExecutionRuntime

from torve.adapters.eventstore.document import mock_module
from torve.application.eventlog import event_log
from torve.application.manager import dispatchable, project
from torve.application.residency import contracts, mint, once, reclaim, serve
from torve.application.worker import Outcome, Worker
from torve.domain.events import ActorKind, EventKind, SubjectType
from torve.domain.states import TaskState

PARTITION = "morzecrew/torve"


def contract(root: Path, task_id: str, *, role: str = "implement", allow: str = "src/**") -> None:
    task_dir = root / ".torve" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    # A review or revert contract names what it acts on, and neither carries
    # acceptance commands — the shape the model refuses to build without.
    acts_on = role in ("review", "revert")
    (task_dir / "contract.yaml").write_text(
        f"schema_version: 1\nid: {task_id}\nrole: {role}\nintent: work here\n"
        f"scope: {{allow: ['{allow}']}}\nacceptance: []\ndecisions: []\n"
        + ("targets: ['T-9999']\n" if acts_on else ""),
        encoding="utf-8",
    )


def landing(**overrides) -> Outcome:
    fields = {
        "attempt": 1,
        "exit_code": 0,
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


def test_every_contract_is_imported_and_a_broken_one_is_skipped(tmp_path):
    contract(tmp_path, "T-0001")
    contract(tmp_path, "T-0002", role="review")
    (tmp_path / ".torve" / "tasks" / "T-0003").mkdir(parents=True)
    (tmp_path / ".torve" / "tasks" / "T-0003" / "contract.yaml").write_text("id: [", "utf-8")

    # A-96: the importer takes every role, because the projections that read
    # the record for a planning view need the whole population. A malformed
    # contract is not the manager's emergency and stops nothing else.
    assert list(contracts(tmp_path)) == ["T-0001", "T-0002"]


def test_a_review_contract_is_recorded_and_offered_to_nobody(tmp_path):
    contract(tmp_path, "T-0001", role="review")

    async def scenario(log):
        assert await mint(log, contracts(tmp_path), partition=PARTITION, actor_id="m") == ["T-0001"]

        board = project(await log.since(partition=PARTITION))

        # On the board, with its contract, and never dispatchable: the run
        # that mints a review is the only thing that ever executes one.
        assert board.tasks["T-0001"].contract is not None
        assert dispatchable(board, PARTITION) == []

    run(scenario)


def test_a_review_that_ran_and_landed_nothing_is_still_imported(tmp_path):
    """The `ran` guard keeps a task off the board so no worker is handed it
    twice. A review is handed to nobody, so the guard has nothing to
    protect and would only hide the row the projections read (A-96)."""

    contract(tmp_path, "T-0001", role="review")
    contract(tmp_path, "T-0002")

    async def scenario(log):
        minted = await mint(
            log,
            contracts(tmp_path),
            partition=PARTITION,
            actor_id="m",
            ran=lambda task_id: True,
        )

        assert minted == ["T-0001"]

    run(scenario)


def test_a_landing_the_record_missed_is_imported_onto_a_minted_row(tmp_path):
    """A row minted before this partition could see the landing (A-97): the
    repository proves it landed, the record has only ever minted it, so the
    landing the first mint would have written is written now."""

    contract(tmp_path, "T-0001")

    async def scenario(log):
        await mint(log, contracts(tmp_path), partition=PARTITION, actor_id="m")

        await mint(
            log,
            contracts(tmp_path),
            partition=PARTITION,
            actor_id="m",
            landed=lambda task_id: "c" * 40,
        )

        board = project(await log.since(partition=PARTITION))
        assert board.tasks["T-0001"].state is TaskState.READY
        assert board.tasks["T-0001"].landed_sha == "c" * 40


def test_a_task_the_record_has_run_is_not_overruled_by_the_repository(tmp_path):
    """Once a task has run here the board outranks the host: a human who
    requeued it after an escalation is not sent back to ready by a scan
    that found an old commit."""

    contract(tmp_path, "T-0001")

    async def scenario(log):
        executed: list[str] = []
        await once(
            log, worker_over(log, executed, Outcome(attempt=1, exit_code=1)), tmp_path, PARTITION
        )

        await mint(
            log,
            contracts(tmp_path),
            partition=PARTITION,
            actor_id="m",
            landed=lambda task_id: "c" * 40,
        )

        board = project(await log.since(partition=PARTITION))
        assert board.tasks["T-0001"].landed_sha is None


def test_the_standing_leg_runs_before_the_scan_and_a_paused_pass_skips_it(tmp_path):
    """RFC 0023 §5.4: whatever a predicate mints is a contract file, and the
    scan that runs after it is what puts that contract on the board — so
    the leg needs no record-side machinery at all, only its turn (A-106).

    D-23.6's first bound is the caller's, and this is that caller: a paused
    pass evaluates no predicate, because a predicate that fires creates work
    and a pause says nobody can triage it."""

    fired: list[str] = []

    def standing() -> tuple[str, bool]:
        # What a real leg does: write a contract, and let the scan find it.
        contract(tmp_path, "T-0002")
        fired.append("job")

        return "fired 1: job->T-0002", True

    async def scenario(log):
        worker = worker_over(log, [])
        await once(log, worker, tmp_path, PARTITION, dispatch=False, standing=standing)

        board = project(await log.since(partition=PARTITION))
        assert fired == ["job"]
        assert "T-0002" in board.tasks

        await once(log, worker, tmp_path, PARTITION, dispatch=False, standing=standing, paused=True)
        assert fired == ["job"]

    run(scenario)


def test_a_pass_that_does_not_dispatch_imports_and_claims_nothing(tmp_path):
    """The re-mint pass (A-96): the scan must be able to reach the record
    without a worker taking the first thing it finds there."""

    contract(tmp_path, "T-0001")

    async def scenario(log):
        executed: list[str] = []
        worker = worker_over(log, executed)

        assert await once(log, worker, tmp_path, PARTITION, dispatch=False) is None
        assert executed == []

        board = project(await log.since(partition=PARTITION))
        assert board.tasks["T-0001"].state is TaskState.QUEUED
        assert board.tasks["T-0001"].claimed_by is None

    run(scenario)


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

        # The pass's own facts. What happened inside the run is the run's to
        # report (D-44.3) and this execute is a stub, so nothing between the
        # claim and the landing is invented here.
        assert [event.kind for event in await log.history("T-0001")] == [
            EventKind.TASK_MINTED,
            EventKind.TASK_CLAIMED,
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
    """The four fields this seam touches. The real bundle is the CLI's to
    build; what `runner_execute` does to it is replace exactly these."""

    sink: object = None
    facts: object = None
    journal: object = None
    channel: object = None


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
        from torve.application.ports import AttemptFact

        # The broker meters on the request thread, which is this one: the
        # sink must cross back to the loop without the run waiting on it.
        deps.sink(BurnEvent(provider="anthropic", tokens=1200, cost_usd=0.03))
        # And the run reports its own attempts, which is the only place the
        # tier that actually ran one is known.
        deps.facts(
            AttemptFact(
                kind="attempt_started",
                attempt=2,
                payload={"tier": "executor.heavy", "agent": "claude"},
            )
        )

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

        recorded = await log.history("T-0001")
        kinds = [event.kind for event in recorded]
        assert EventKind.SEAT_CONSUMED in kinds
        assert EventKind.DIVERGENCE_RECORDED in kinds
        assert EventKind.ATTEMPT_STARTED in kinds

        started = next(one for one in recorded if one.kind is EventKind.ATTEMPT_STARTED)
        # The attempt number and the tier are the run's, not the worker's
        # guess: a worker that never saw attempt 2 cannot report it.
        assert started.payload["attempt"] == 2
        assert started.payload["tier"] == "executor.heavy"

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


# ....................... #


def test_a_dead_worker_s_task_comes_back_when_its_lease_runs_out(tmp_path):
    contract(tmp_path, "T-0001")

    async def scenario(log):
        # A worker claims and dies: nothing releases the task, because the
        # process that would have is gone.
        dead = Worker(log=log, name="w-dead", execute=worker_over(log, []).execute)
        await mint(log, contracts(tmp_path), partition=PARTITION, actor_id="manager-1")

        assert await dead.claim(PARTITION) is not None
        assert await once(log, worker_over(log, []), tmp_path, PARTITION) is None

        # Reclaiming is the manager's, and it says why.
        released = await reclaim(log, partition=PARTITION, actor_id="manager-1", lease=timedelta(0))

        assert released == ["T-0001"]

        board = project(await log.since(partition=PARTITION))
        assert board.tasks["T-0001"].state is TaskState.QUEUED
        assert board.tasks["T-0001"].claimed_by is None

        recorded = [
            one for one in await log.history("T-0001") if one.kind is EventKind.TASK_RELEASED
        ]
        assert "lease expired" in recorded[0].payload["reason"]
        assert "w-dead" in recorded[0].payload["reason"]

    run(scenario)


def test_the_next_pass_picks_up_what_the_lease_released(tmp_path):
    contract(tmp_path, "T-0001")

    async def scenario(log):
        executed: list[str] = []
        dead = Worker(log=log, name="w-dead", execute=worker_over(log, []).execute)
        await mint(log, contracts(tmp_path), partition=PARTITION, actor_id="manager-1")
        await dead.claim(PARTITION)

        # A pass whose reclaim window has passed frees the task and runs it
        # in the same pass — waiting an idle interval to notice would be a
        # second cost on top of the death.
        assert (
            await once(log, worker_over(log, executed), tmp_path, PARTITION, lease=timedelta(0))
            == "T-0001"
        )
        assert executed == ["T-0001"]

    run(scenario)


def test_a_contract_that_already_landed_is_minted_as_landed(tmp_path):
    """A partition's first pass sees every contract the tree carries,
    including years of finished work. A board that called those queued
    would hand a worker a task somebody finished long ago (A-29)."""

    contract(tmp_path, "T-0001")
    contract(tmp_path, "T-0002", allow="docs/**")

    async def scenario(log):
        executed: list[str] = []
        landed = {"T-0001": "a" * 40}.get

        minted = await mint(
            log,
            contracts(tmp_path),
            partition=PARTITION,
            actor_id="manager-1",
            landed=landed,
        )

        assert minted == ["T-0001", "T-0002"]

        board = project(await log.since(partition=PARTITION))
        assert board.tasks["T-0001"].state is TaskState.READY
        assert board.tasks["T-0001"].landed_sha == "a" * 40
        assert board.tasks["T-0002"].state is TaskState.QUEUED

        # And only the unfinished one is offered.
        assert await once(log, worker_over(log, executed), tmp_path, PARTITION, landed=landed) == (
            "T-0002"
        )
        assert executed == ["T-0002"]

    run(scenario)


def test_a_dependency_on_landed_work_is_satisfied_by_the_import(tmp_path):
    contract(tmp_path, "T-0001")
    contract(tmp_path, "T-0002", allow="docs/**")
    path = tmp_path / ".torve" / "tasks" / "T-0002" / "contract.yaml"
    path.write_text(path.read_text() + "depends_on: [T-0001]\n", encoding="utf-8")

    async def scenario(log):
        executed: list[str] = []

        # Recording the landing rather than skipping the mint is what keeps
        # the dependency rule honest: a task waiting on finished work has to
        # be able to find that landing on the board.
        handled = await once(
            log,
            worker_over(log, executed),
            tmp_path,
            PARTITION,
            landed={"T-0001": "a" * 40}.get,
        )

        assert handled == "T-0002"

    run(scenario)


def test_a_contract_that_ran_and_never_landed_is_not_offered_again(tmp_path):
    """A repository carries every contract it has ever executed, and the
    early ones landed before the trailer that proves it. The standing loop
    excludes them by the host's own run record; the manager asks the same
    question, or its first pass over a real repository offers a worker work
    somebody finished a year ago (A-29)."""

    contract(tmp_path, "T-0001")
    contract(tmp_path, "T-0002", allow="docs/**")
    ran = {"T-0001"}.__contains__

    async def scenario(log):
        executed: list[str] = []
        handled = await once(log, worker_over(log, executed), tmp_path, PARTITION, ran=ran)

        assert handled == "T-0002"
        # And it is not on the board at all: a task nobody may run is not a
        # row an operator has to learn to ignore.
        board = project(await log.since(partition=PARTITION))
        assert "T-0001" not in board.tasks

    run(scenario)


def test_the_board_outranks_the_host_once_a_task_is_on_it(tmp_path):
    """A task that ran, escalated and was requeued by a human is queued —
    however many times it ran before. The host's run record decides what
    gets minted; after that the board is the state, and the board is what a
    person acts on."""

    contract(tmp_path, "T-0001")
    ran = {"T-0001"}.__contains__

    async def scenario(log):
        executed: list[str] = []

        # A human puts it on the board and requeues it.
        await log.record(
            EventKind.TASK_MINTED,
            partition=PARTITION,
            subject_type=SubjectType.TASK,
            subject_id="T-0001",
            actor_kind=ActorKind.MANAGER,
            actor_id="manager-1",
            payload={"title": "T-0001", "source_id": "operator"},
        )

        assert await once(log, worker_over(log, executed), tmp_path, PARTITION, ran=ran) == (
            "T-0001"
        )
        assert executed == ["T-0001"]

    run(scenario)


def test_naming_one_task_runs_that_one_and_no_other(tmp_path):
    contract(tmp_path, "T-0001")
    contract(tmp_path, "T-0002", allow="docs/**")

    async def scenario(log):
        executed: list[str] = []
        handled = await once(log, worker_over(log, executed), tmp_path, PARTITION, only="T-0002")

        assert handled == "T-0002"
        assert executed == ["T-0002"]
        # The board's own order would have picked T-0001; an operator
        # naming a task means that task.
        board = project(await log.since(partition=PARTITION))
        assert "T-0001" not in board.tasks

    run(scenario)


def test_serve_honours_the_named_task_too(tmp_path):
    """The loop and the pass are different functions, and the pass having
    the filter is not the loop having it — which is exactly how a real
    dispatch went to the wrong task."""

    contract(tmp_path, "T-0001")
    contract(tmp_path, "T-0002", allow="docs/**")

    async def scenario(log):
        executed: list[str] = []
        handled = await serve(
            log,
            worker_over(log, executed),
            tmp_path,
            PARTITION,
            passes=1,
            idle_seconds=0,
            only="T-0002",
        )

        assert handled == 1
        assert executed == ["T-0002"]

    run(scenario)
