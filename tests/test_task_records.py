"""Tasks as records (RFC 0049).

What the record did not hold was the contract — the one field every reader
had to be standing next to the repository for. These cases are about the
three rules that made recording it safe: a re-mint is a version and never a
transition, a task in flight is never re-minted, and a mint written before
the amendment stays readable.

The parity case at the end is the one that says the reader moved without the
rule moving: dispatch answered from the board alone equals dispatch answered
from the scan's dictionary, over the same repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_residency import PARTITION, contract, run

from torve.application.manager import dispatchable, minted_contract, project
from torve.application.residency import contracts, mint
from torve.domain.events import ActorKind, EventKind, SubjectType
from torve.domain.states import TaskState

# ----------------------- #


async def board_of(log):
    return project(await log.since(partition=PARTITION))


# ....................... #


async def sync(log, root: Path, *, actor_id: str = "manager-1") -> list[str]:
    return await mint(log, contracts(root), partition=PARTITION, actor_id=actor_id)


# ....................... #


async def record(log, kind, task_id, payload, *, actor=ActorKind.WORKER, actor_id="w-1"):
    await log.record(
        kind,
        partition=PARTITION,
        subject_type=SubjectType.TASK,
        subject_id=task_id,
        actor_kind=actor,
        actor_id=actor_id,
        payload=payload,
    )


# ....................... #


def test_a_minted_contract_reads_back_off_the_board_unchanged(tmp_path):
    """D-49.1: the whole contract, not a summary of it — the scope and the
    decisions are what the gates enforce, so a lossy record would be worse
    than none."""

    contract(tmp_path, "T-0001", allow="src/a/**")

    async def scenario(log):
        await sync(log, tmp_path)
        view = (await board_of(log)).tasks["T-0001"]

        assert view.contract == contracts(tmp_path)["T-0001"]
        assert view.contract.scope.allow == ["src/a/**"]
        assert view.contract.intent == "work here"

    run(scenario)


# ....................... #


def test_an_unchanged_repository_mints_once(tmp_path):
    contract(tmp_path, "T-0001")

    async def scenario(log):
        assert await sync(log, tmp_path) == ["T-0001"]
        assert await sync(log, tmp_path) == []
        assert len(await log.history("T-0001")) == 1

    run(scenario)


# ....................... #


def test_a_changed_contract_is_re_minted_as_a_new_version(tmp_path):
    """D-49.4: the operator's recourse after an escalation is to fix the
    contract, and a record that could not accept the fix would send them to
    delete the task and mint a new id."""

    contract(tmp_path, "T-0001", allow="src/a/**")

    async def scenario(log):
        await sync(log, tmp_path)
        contract(tmp_path, "T-0001", allow="src/b/**")

        assert await sync(log, tmp_path) == ["T-0001"]

        history = await log.history("T-0001")

        assert [one.kind for one in history] == [
            EventKind.TASK_MINTED,
            EventKind.TASK_MINTED,
        ]
        assert minted_contract(history[0].payload).scope.allow == ["src/a/**"]
        assert (await board_of(log)).tasks["T-0001"].contract.scope.allow == ["src/b/**"]

    run(scenario)


# ....................... #


def test_a_re_mint_does_not_transition_an_escalated_task(tmp_path):
    """D-49.2: a manager that could re-queue an escalated task by noticing an
    edited file would be writing an `escalation.resolved` it has no authority
    to write, under another name."""

    contract(tmp_path, "T-0001", allow="src/a/**")

    async def scenario(log):
        await sync(log, tmp_path)
        await record(log, EventKind.ESCALATION_RAISED, "T-0001", {"reason": "poison_ceiling"})
        contract(tmp_path, "T-0001", allow="src/b/**")

        assert await sync(log, tmp_path) == ["T-0001"]

        view = (await board_of(log)).tasks["T-0001"]

        assert view.state is TaskState.ESCALATED
        assert view.escalation == "poison_ceiling"
        assert view.contract.scope.allow == ["src/b/**"]
        assert dispatchable(await board_of(log), PARTITION) == []

    run(scenario)


# ....................... #


def test_the_operators_whole_recourse_works_end_to_end(tmp_path):
    """Escalate, fix the contract, resolve — and the next pass records the
    change and puts it in force. The workflow D-49.4 exists for."""

    contract(tmp_path, "T-0001", allow="src/a/**")

    async def scenario(log):
        await sync(log, tmp_path)
        await record(log, EventKind.ESCALATION_RAISED, "T-0001", {"reason": "underspecified"})
        contract(tmp_path, "T-0001", allow="src/b/**")
        await sync(log, tmp_path)
        await record(
            log,
            EventKind.ESCALATION_RESOLVED,
            "T-0001",
            {"resolution": "requeued"},
            actor=ActorKind.OPERATOR,
            actor_id="misery7100",
        )

        board = await board_of(log)

        assert dispatchable(board, PARTITION) == ["T-0001"]
        assert board.tasks["T-0001"].contract.scope.allow == ["src/b/**"]

    run(scenario)


# ....................... #


@pytest.mark.parametrize(
    ("kind", "payload", "actor"),
    [
        (EventKind.TASK_CLAIMED, {"worker": "w-1", "lease_seconds": 900}, ActorKind.MANAGER),
        (
            EventKind.ATTEMPT_STARTED,
            {"attempt": 1, "tier": "executor", "agent": "fake"},
            ActorKind.WORKER,
        ),
        (
            EventKind.GATES_EVALUATED,
            {"attempt": 1, "exit_code": 0, "results": []},
            ActorKind.WORKER,
        ),
    ],
)
def test_a_task_in_flight_is_never_re_minted(tmp_path, kind, payload, actor):
    """D-49.3: the contract an attempt is judged against is the one it
    started under. Parameterised because "in flight" is a set, and a rule
    that holds for one member is not a rule."""

    contract(tmp_path, "T-0001", allow="src/a/**")

    async def scenario(log):
        await sync(log, tmp_path)
        await record(log, kind, "T-0001", payload, actor=actor)
        contract(tmp_path, "T-0001", allow="src/b/**")

        assert await sync(log, tmp_path) == []
        assert (await board_of(log)).tasks["T-0001"].contract.scope.allow == ["src/a/**"]

    run(scenario)


# ....................... #


def test_the_flat_fields_agree_with_the_contract_at_write_time(tmp_path):
    """A-91 keeps `phase` and `depends_on` beside the contract for the mints
    written before it. Two carriers of one fact is only safe while a test
    says they agree."""

    contract(tmp_path, "T-0001")

    async def scenario(log):
        await sync(log, tmp_path)
        payload = (await log.history("T-0001"))[0].payload
        recorded = minted_contract(payload)

        assert payload["phase"] == recorded.phase
        assert payload["depends_on"] == recorded.depends_on

    run(scenario)


# ....................... #


def test_a_mint_written_before_the_amendment_stays_readable(tmp_path):
    """D-49.5: 192 of them, over 184 tasks, are already in the lab log. A
    fold that raised on one would take a whole board down."""

    async def scenario(log):
        await record(
            log,
            EventKind.TASK_MINTED,
            "T-0001",
            {"title": "old", "source_id": "0044", "phase": 0, "depends_on": []},
            actor=ActorKind.MANAGER,
            actor_id="manager-1",
        )

        board = await board_of(log)

        assert board.tasks["T-0001"].state is TaskState.QUEUED
        assert board.tasks["T-0001"].contract is None
        assert dispatchable(board, PARTITION) == []

    run(scenario)


# ....................... #


def test_a_payload_that_no_longer_validates_reads_as_no_contract():
    """The model can move under a recorded payload. That must cost the board
    one undispatchable row, not the whole fold."""

    assert minted_contract({}) is None
    assert minted_contract({"contract": {}}) is None
    assert minted_contract({"contract": {"id": "T-1"}}) is None  # decisions is required
    assert minted_contract({"contract": {"id": "T-1", "decisions": []}}).id == "T-1"


# ....................... #


def test_a_log_with_no_contracts_completes_itself_on_the_next_pass(tmp_path):
    """§5.4: no migration step and no backfill script — the pass that reads
    a contract-less mint finds it differs from the repository's and records
    what it has been running under all along."""

    contract(tmp_path, "T-0001")

    async def scenario(log):
        await record(
            log,
            EventKind.TASK_MINTED,
            "T-0001",
            {"title": "old", "source_id": "0044", "phase": 0, "depends_on": []},
            actor=ActorKind.MANAGER,
            actor_id="manager-1",
        )

        assert (await board_of(log)).tasks["T-0001"].contract is None
        assert await sync(log, tmp_path) == ["T-0001"]

        board = await board_of(log)

        assert board.tasks["T-0001"].contract is not None
        assert board.tasks["T-0001"].state is TaskState.QUEUED
        assert dispatchable(board, PARTITION) == ["T-0001"]

    run(scenario)


# ....................... #


def test_dispatch_from_the_board_equals_dispatch_from_the_scan(tmp_path):
    """The parity that says the reader moved and the rule did not.

    The old signature is gone, so the scan's answer is reconstructed here
    from the rules `dispatchable` applies — queued, dependencies landed,
    scope disjoint from what is in flight — against the contracts on disk.
    """

    contract(tmp_path, "T-0001", allow="src/a/**")
    contract(tmp_path, "T-0002", allow="src/b/**")
    contract(tmp_path, "T-0003", allow="src/a/**")

    async def scenario(log):
        await sync(log, tmp_path)
        await record(
            log,
            EventKind.TASK_CLAIMED,
            "T-0001",
            {"worker": "w-1", "lease_seconds": 900},
            actor=ActorKind.MANAGER,
            actor_id="manager-1",
        )

        board = await board_of(log)
        from_disk = contracts(tmp_path)
        expected = sorted(
            task_id
            for task_id, task in from_disk.items()
            if board.tasks[task_id].state is TaskState.QUEUED
            and not any(
                other.scope.allow == task.scope.allow
                for view in board.in_flight()
                if (other := from_disk.get(view.task_id)) is not None
            )
        )

        assert dispatchable(board, PARTITION) == expected == ["T-0002"]

    run(scenario)


# ....................... #


def test_a_contract_whose_file_vanished_keeps_its_history_and_stops_dispatching(tmp_path):
    """§5.4: not re-minted, not erased. The board keeps what happened to it
    and stops offering it."""

    contract(tmp_path, "T-0001")

    async def scenario(log):
        await sync(log, tmp_path)
        (tmp_path / ".torve" / "tasks" / "T-0001" / "contract.yaml").unlink()

        assert await sync(log, tmp_path) == []

        board = await board_of(log)

        assert board.tasks["T-0001"].contract is not None  # what it was minted with
        assert board.tasks["T-0001"].state is TaskState.QUEUED

    run(scenario)
