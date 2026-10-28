"""The manager's board (S-0044/the-manager).

The board is a fold over recorded facts, so every case here is written the
same way: record events, project, assert what the manager would decide. The
dispatch rules are v1's — dependency by landing, scope-disjoint in flight,
partitions independent — asserted against the new source of state.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from forze.application.execution import DepsRegistry, ExecutionRuntime

from torve.adapters.eventstore.document import mock_module
from torve.application.eventlog import event_log
from torve.application.manager import (
    LEASE_SECONDS,
    Board,
    Documents,
    PullRequests,
    ReviewThreads,
    TaskView,
    dispatchable,
    documents,
    expired,
    night_report,
    overlaps,
    project,
    pull_requests,
    review_threads,
    stalled,
)
from torve.base.clock import stamp
from torve.domain.events import ActorKind, EventKind, EventRecord, SubjectType
from torve.domain.states import EXIT_CONFIG, TaskState
from torve.domain.task import Scope, Task

PARTITION = "morzecrew/torve"
OTHER = "morzecrew/other"


def task(task_id: str, *, allow: list[str], depends_on: list[str] | None = None) -> Task:
    return Task(
        id=task_id,
        decisions=[],
        depends_on=depends_on or [],
        scope=Scope(allow=allow, deny=[]),
    )


def event(kind, task_id, payload=None, *, at=None, partition=PARTITION) -> EventRecord:
    """One record, built rather than written: these cases are about the fold
    and about clocks, and a real store stamps its own."""

    stamped = at or datetime.now(UTC)

    return EventRecord(
        id=uuid.uuid4().hex,
        rev=1,
        created_at=stamped,
        last_update_at=stamped,
        kind=kind,
        partition=partition,
        subject_type=SubjectType.TASK,
        subject_id=task_id,
        actor_kind=ActorKind.MANAGER,
        actor_id="manager-1",
        payload=payload or {},
    )


def run(scenario):
    async def main():
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            await scenario(event_log(runtime.get_context()))

    asyncio.run(main())


async def mint(log, task_id, *, partition=PARTITION, allow=None, depends_on=None, **payload):
    """One mint carrying its contract (S-0049 S-0049/D-1) — what the board
    reads for scope and dependencies, so a case that leaves it out is
    testing a mint written before A-91 and says so."""

    contract = task(
        task_id, allow=allow if allow is not None else ["src/**"], depends_on=depends_on
    )

    await log.record(
        EventKind.TASK_MINTED,
        partition=partition,
        subject_type=SubjectType.TASK,
        subject_id=task_id,
        actor_kind=ActorKind.MANAGER,
        actor_id="manager-1",
        payload={
            "title": task_id,
            "source_id": "0044",
            "depends_on": contract.depends_on,
            "contract": contract.model_dump(mode="json"),
            **payload,
        },
    )


async def land(log, task_id, *, partition=PARTITION):
    await log.record(
        EventKind.LANDING_RECORDED,
        partition=partition,
        subject_type=SubjectType.TASK,
        subject_id=task_id,
        actor_kind=ActorKind.MANAGER,
        actor_id="manager-1",
        payload={"sha": "a" * 40, "attempt": 1},
    )


async def minted(log, contract: Task) -> None:
    """One mint carrying a contract with whatever scope and document this
    case needs — `mint` above fixes the allow-set and names no document."""

    await log.record(
        EventKind.TASK_MINTED,
        partition=PARTITION,
        subject_type=SubjectType.TASK,
        subject_id=contract.id,
        actor_kind=ActorKind.MANAGER,
        actor_id="manager-1",
        payload={
            "title": contract.id,
            "source_id": "0104",
            "depends_on": contract.depends_on,
            "contract": contract.model_dump(mode="json"),
        },
    )


async def promote(log, task_id, path, *, partition=PARTITION) -> None:
    await log.record(
        EventKind.SCOPE_PROMOTED,
        partition=partition,
        subject_type=SubjectType.TASK,
        subject_id=task_id,
        actor_kind=ActorKind.WORKER,
        actor_id="w-1",
        payload={"path": path, "attempt": 1},
    )


async def claim(log, task_id, *, partition=PARTITION, worker="w-1"):
    await log.record(
        EventKind.TASK_CLAIMED,
        partition=partition,
        subject_type=SubjectType.TASK,
        subject_id=task_id,
        actor_kind=ActorKind.MANAGER,
        actor_id="manager-1",
        payload={"worker": worker, "lease_seconds": 900},
    )


def test_an_empty_log_projects_an_empty_board():
    assert project([]) == Board()


def test_a_minted_task_is_queued_and_dispatchable():
    async def scenario(log):
        await mint(log, "T-1")
        board = project(await log.since())

        assert board.tasks["T-1"].state is TaskState.QUEUED
        assert dispatchable(board, PARTITION) == ["T-1"]

    run(scenario)


def test_a_claimed_task_is_no_longer_dispatchable_and_names_its_worker():
    async def scenario(log):
        await mint(log, "T-1")
        await claim(log, "T-1")
        board = project(await log.since())

        assert board.tasks["T-1"].state is TaskState.CLAIMED
        assert board.tasks["T-1"].claimed_by == "w-1"
        assert dispatchable(board, PARTITION) == []

    run(scenario)


def test_a_dependency_is_satisfied_only_by_a_landing():
    async def scenario(log):
        await mint(log, "T-1", allow=["src/a/**"])
        await mint(log, "T-2", allow=["src/b/**"], depends_on=["T-1"])

        assert dispatchable(project(await log.since()), PARTITION) == ["T-1"]

        # Reaching a claim, an attempt, even a review is not a landing.
        await claim(log, "T-1")

        assert dispatchable(project(await log.since()), PARTITION) == []

        await land(log, "T-1")

        assert dispatchable(project(await log.since()), PARTITION) == ["T-2"]

    run(scenario)


def test_a_review_record_is_no_dependency():
    """S-0085/A-1: a review record concludes with the run that minted it and
    never lands, so a task minted waiting on one waits on its landed peers only."""

    async def scenario(log):
        await mint(log, "T-1", allow=["src/a/**"])
        review = Task(id="T-2", role="review", targets=["T-1"], decisions=[], scope=Scope())
        await minted(log, review)
        await mint(log, "T-3", allow=["src/b/**"], depends_on=["T-1", "T-2"])

        assert dispatchable(project(await log.since()), PARTITION) == ["T-1"]

        await land(log, "T-1")

        assert dispatchable(project(await log.since()), PARTITION) == ["T-3"]

    run(scenario)


def test_a_squash_merged_dependency_is_on_base_only_after_the_merge(tmp_path):
    """S-0085/D-4: a dependency is satisfied by a landing that is an ancestor
    of the base the dependent would be cut from, and by nothing else. The
    branch commit a task landed as is an ancestor of no base once its document
    is squash-merged, so the dependent waits forever unless the merge commit is
    the landing the board holds (S-0085/D-3, bloomery S-0008, 2026-09-19)."""

    from torve.adapters.vcs.git import GitLane
    from torve.cli.manager import _dependencies_on_base
    from torve.config.runconfig import RunnerConfig
    from torve.gates.sabotage import Repo

    repo = Repo(tmp_path / "repo")
    repo.root.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.name", "Squashing Human")
    repo.git("config", "user.email", "human@example.invalid")
    repo.write("src/a/app.py", "print('hello')\n")
    repo.commit("init")

    repo.git("checkout", "-q", "-b", "torve/S-0008")
    repo.write("src/a/app.py", "print('landed')\n")
    repo.commit("the phase")
    branch_sha = GitLane().tip(repo.root, "HEAD")

    repo.git("checkout", "-q", "main")
    repo.git("merge", "--squash", "torve/S-0008")
    repo.commit("squash-merge the document")
    merge_sha = GitLane().tip(repo.root, "main")

    def board_with(sha: str | None) -> Board:
        return Board(tasks={"T-1": TaskView(task_id="T-1", state=TaskState.READY, landed_sha=sha)})

    on_base = _dependencies_on_base(repo.root, RunnerConfig())
    dependent = task("T-2", allow=["src/b/**"], depends_on=["T-1"])

    assert not on_base(dependent, board_with(branch_sha))
    assert on_base(dependent, board_with(merge_sha))


def test_a_review_record_is_no_dependency_the_base_must_carry(tmp_path):
    """S-0085/A-1: the served worker's base check waits on the landings a
    task depends on, and a review record has none to wait for."""
    from torve.adapters.vcs.git import GitLane
    from torve.cli.manager import _dependencies_on_base
    from torve.config.runconfig import RunnerConfig
    from torve.gates.sabotage import Repo

    repo = Repo(tmp_path / "repo")
    repo.root.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.name", "Landing Lane")
    repo.git("config", "user.email", "lane@example.invalid")
    repo.write("src/a/app.py", "print('landed')\n")
    repo.commit("the phase")

    review = Task(id="T-2", role="review", targets=["T-1"], decisions=[], scope=Scope())
    board = Board(
        tasks={
            "T-1": TaskView(
                task_id="T-1", state=TaskState.READY, landed_sha=GitLane().tip(repo.root, "main")
            ),
            "T-2": TaskView(task_id="T-2", state=TaskState.QUEUED, contract=review),
        }
    )
    dependent = task("T-3", allow=["src/b/**"], depends_on=["T-1", "T-2"])

    assert _dependencies_on_base(repo.root, RunnerConfig())(dependent, board)


def test_a_landing_file_on_the_documents_remote_tip_satisfies_a_dependency(tmp_path):
    """The dependency check reads the remote's copy of the document branch
    (S-0091/D-1), and the landing file on it is the carrier (S-0099/D-1). A
    branch whose shas a rebase renamed still answers, where the attempt's sha
    no branch holds does not."""
    from torve.adapters.vcs.git import GitLane
    from torve.cli.manager import _dependencies_on_base
    from torve.config.runconfig import RunnerConfig
    from torve.gates.sabotage import Repo

    repo = Repo(tmp_path / "repo")
    repo.root.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.name", "Rebasing Lane")
    repo.git("config", "user.email", "lane@example.invalid")
    repo.write("src/a/app.py", "print('hello')\n")
    repo.commit("init")
    repo.git("checkout", "-q", "-b", "torve/S-0013")
    repo.write("docs/round.md", "a round\n")
    repo.commit("a round")
    # The dependency check reads the remote's copy (S-0091/D-1).
    repo.git("update-ref", "refs/remotes/origin/torve/S-0013", "HEAD")

    config = RunnerConfig.model_validate(
        {"promotion": {"landing": "pull_request", "unit": "document", "auto_merge": True}}
    )
    on_base = _dependencies_on_base(repo.root, config)
    dependent = Task(
        id="T-2",
        spec="S-0013",
        decisions=[],
        depends_on=["T-1"],
        scope=Scope(allow=["src/b/**"], deny=[]),
    )
    # The board's sha names no commit on the branch, and no landing file is
    # on it: the dependency is not satisfied.
    board = Board(
        tasks={"T-1": TaskView(task_id="T-1", state=TaskState.READY, landed_sha="f" * 40)}
    )
    assert not on_base(dependent, board)

    # The landing file on the branch's remote tip is the carrier: the
    # dependency is satisfied however the branch's shas are rewritten.
    repo.write(".torve/specs/S-0013/execution/T-1-1-20260926T000000Z.yaml", "task: T-1\n")
    repo.commit("torve(T-1): landing of attempt 1")
    repo.git("update-ref", "refs/remotes/origin/torve/S-0013", "HEAD")
    assert on_base(dependent, board)
    assert not GitLane().is_ancestor(repo.root, "f" * 40, "torve/S-0013")


@pytest.mark.parametrize("hand_pushed", [False, True])
def test_a_landing_held_for_its_wave_satisfies_a_dependency(tmp_path, hand_pushed):
    """S-0097/D-6: a landing held for its wave is on this checkout's copy only.
    The task cut carries it, so the dependency check reads the same copy —
    also after a hand push to the pull request diverges the two copies, since
    the lane's `_unpublished` keeps this one and lands onto it."""
    from torve.cli.manager import _dependencies_on_base
    from torve.config.runconfig import RunnerConfig
    from torve.gates.sabotage import Repo

    repo = Repo(tmp_path / "repo")
    repo.root.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.name", "Lane")
    repo.git("config", "user.email", "lane@example.invalid")
    repo.write("src/a/app.py", "print('hello')\n")
    repo.commit("init")
    repo.git("checkout", "-q", "-b", "torve/S-0015")
    repo.git("update-ref", "refs/remotes/origin/torve/S-0015", "HEAD")
    repo.write(".torve/specs/S-0015/execution/T-1-1-20261021T000000Z.yaml", "task: T-1\n")
    repo.commit("torve(T-1): landing of attempt 1")

    if hand_pushed:
        repo.git("checkout", "-q", "--detach", "refs/remotes/origin/torve/S-0015")
        repo.write("docs/hand.md", "a hand commit on the pull request\n")
        repo.commit("a hand commit")
        repo.git("update-ref", "refs/remotes/origin/torve/S-0015", "HEAD")

    repo.git("checkout", "-q", "main")

    config = RunnerConfig.model_validate(
        {"promotion": {"landing": "pull_request", "unit": "document", "auto_merge": True}}
    )
    dependent = Task(
        id="T-2", spec="S-0015", decisions=[], depends_on=["T-1"], scope=Scope(allow=[], deny=[])
    )
    board = Board(
        tasks={"T-1": TaskView(task_id="T-1", state=TaskState.READY, landed_sha="f" * 40)}
    )

    assert _dependencies_on_base(repo.root, config)(dependent, board)


def test_a_local_document_branch_the_remote_lacks_is_no_base(tmp_path):
    """The branch the remote deleted after its merge is not the base a
    dependent is cut from, so a landing only it holds satisfies nothing."""
    from torve.adapters.vcs.git import GitLane
    from torve.cli.manager import _dependencies_on_base
    from torve.config.runconfig import RunnerConfig
    from torve.gates.sabotage import Repo

    repo = Repo(tmp_path / "repo")
    repo.root.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.name", "Lane")
    repo.git("config", "user.email", "lane@example.invalid")
    repo.write("src/a/app.py", "print('hello')\n")
    repo.commit("init")
    repo.git("checkout", "-q", "-b", "torve/S-0014")
    repo.write("src/a/app.py", "print('landed')\n")
    repo.commit("the phase")
    landed = GitLane().tip(repo.root, "HEAD")
    repo.git("checkout", "-q", "main")

    config = RunnerConfig.model_validate(
        {"promotion": {"landing": "pull_request", "unit": "document", "auto_merge": True}}
    )
    on_base = _dependencies_on_base(repo.root, config)
    dependent = Task(
        id="T-2", spec="S-0014", decisions=[], depends_on=["T-1"], scope=Scope(allow=[], deny=[])
    )
    board = Board(tasks={"T-1": TaskView(task_id="T-1", state=TaskState.READY, landed_sha=landed)})

    assert not on_base(dependent, board)

    repo.git("update-ref", "refs/remotes/origin/torve/S-0014", landed)

    assert on_base(dependent, board)


def test_tasks_in_flight_hold_their_scope_against_new_dispatch():
    async def scenario(log):
        await mint(log, "T-1", allow=["src/**"])
        await mint(log, "T-2", allow=["src/**"])
        await claim(log, "T-1")

        assert dispatchable(project(await log.since()), PARTITION) == []

        # Disjoint scope dispatches beside it — re-minted, because that is
        # the only way a contract changes now (S-0049/D-4).
        await mint(log, "T-2", allow=["web/**"])

        assert dispatchable(project(await log.since()), PARTITION) == ["T-2"]

    run(scenario)


def test_two_wildcard_scopes_that_share_a_path_fence_each_other():
    """S-0105/D-2: `**/AGENTS.md` and `src/torve/cli/**` share
    `src/torve/cli/AGENTS.md`, which the deleted `globs_intersect` missed.
    The second task is held off the first's fence."""

    async def scenario(log):
        await mint(log, "T-1", allow=["**/AGENTS.md"])
        await mint(log, "T-2", allow=["src/torve/cli/**"])
        await claim(log, "T-1")

        board = project(await log.since())

        assert overlaps(board.tasks["T-2"].contract, board) == ["T-1"]
        assert dispatchable(board, PARTITION) == []

    run(scenario)


def test_a_promoted_contingent_path_is_on_the_board_and_fences_a_second_task():
    """S-0104/D-2: a contingent path the attempt wrote is folded onto the
    board's own task and joins that task's fence. A second task whose scope
    shares the path is held off until the promoting task lands."""

    async def scenario(log):
        await minted(log, Task(id="T-1", decisions=[], scope=Scope(allow=["src/a.py"], deny=[])))
        await minted(
            log, Task(id="T-2", decisions=[], scope=Scope(allow=["docs/notes.md"], deny=[]))
        )
        await claim(log, "T-1")

        # Before the write the two are disjoint and T-2 is dispatchable.
        assert dispatchable(project(await log.since(partition=PARTITION)), PARTITION) == ["T-2"]

        await promote(log, "T-1", "docs/notes.md")

        board = project(await log.since(partition=PARTITION))

        assert board.tasks["T-1"].promoted == ["docs/notes.md"]
        assert overlaps(board.tasks["T-2"].contract, board) == ["T-1"]
        assert dispatchable(board, PARTITION) == []

    run(scenario)


def test_a_later_phase_holding_a_promoted_path_waits_until_the_promoter_lands():
    """S-0104/D-4: a later, undispatched phase of the same document may hold
    the path on paper. The promoting task's fence keeps that phase out of the
    dispatch order until the promotion lands, then releases it."""

    async def scenario(log):
        await minted(
            log,
            Task(
                id="T-1",
                spec="S-0104",
                phase=1,
                decisions=[],
                scope=Scope(allow=["src/a.py"], deny=[]),
            ),
        )
        await minted(
            log,
            Task(
                id="T-2",
                spec="S-0104",
                phase=2,
                decisions=[],
                scope=Scope(allow=["docs/notes.md"], deny=[]),
            ),
        )
        await claim(log, "T-1")
        await promote(log, "T-1", "docs/notes.md")

        assert "T-2" not in dispatchable(project(await log.since(partition=PARTITION)), PARTITION)

        await land(log, "T-1")

        assert "T-2" in dispatchable(project(await log.since(partition=PARTITION)), PARTITION)

    run(scenario)


def test_a_task_waits_while_a_ready_sibling_of_its_document_is_unlanded():
    """S-0098/D-3: no task is cut from a document branch a ready sibling is
    about to move. A round waits while another task naming the same document
    sits `ready` with no landing recorded, dispatches once that sibling lands,
    and a task of another document is untouched throughout."""

    def contract(task_id: str, spec: str | None) -> Task:
        return Task(
            id=task_id,
            spec=spec,
            decisions=[],
            scope=Scope(allow=[f"src/{task_id}/**"], deny=[]),
        )

    def view(task_id, spec, *, state=TaskState.QUEUED, landed_sha=None) -> TaskView:
        return TaskView(
            task_id=task_id,
            partition=PARTITION,
            state=state,
            landed_sha=landed_sha,
            contract=contract(task_id, spec),
        )

    board = Board(
        tasks={
            "T-0001": view("T-0001", "S-0090"),  # the round, waiting
            "T-0002": view("T-0002", "S-0090", state=TaskState.READY),  # ready, unlanded
            "T-0003": view("T-0003", "S-0091"),  # a task of another document
        }
    )

    # The round waits for its document's ready sibling; the other document
    # does not.
    assert dispatchable(board, PARTITION) == ["T-0003"]

    landed = Board(
        tasks={
            **board.tasks,
            "T-0002": view("T-0002", "S-0090", state=TaskState.READY, landed_sha="a" * 40),
        }
    )

    # The sibling landed, so the round is cut now.
    assert dispatchable(landed, PARTITION) == ["T-0001", "T-0003"]


def test_a_document_less_task_waits_for_no_sibling():
    """An operator's ask or a standing job names no document (S-0059/D-1), so
    no document's readiness holds it (S-0098/D-3)."""

    def view(task_id, *, state=TaskState.QUEUED) -> TaskView:
        return TaskView(
            task_id=task_id,
            partition=PARTITION,
            state=state,
            contract=Task(
                id=task_id,
                spec=None,
                decisions=[],
                scope=Scope(allow=["src/**"], deny=[]),
            ),
        )

    board = Board(
        tasks={
            "T-0001": view("T-0001"),
            "T-0002": view("T-0002", state=TaskState.READY),
        }
    )

    assert dispatchable(board, PARTITION) == ["T-0001"]


def test_partitions_do_not_see_each_others_work():
    async def scenario(log):
        await mint(log, "T-1", partition=PARTITION)
        await mint(log, "T-2", partition=OTHER)
        board = project(await log.since(partition=PARTITION))

        assert dispatchable(board, PARTITION) == ["T-1"]
        assert dispatchable(project(await log.since(partition=OTHER)), OTHER) == ["T-2"]

    run(scenario)


def test_an_escalated_task_waits_for_a_human_and_returns_when_resolved():
    async def scenario(log):
        await mint(log, "T-1")
        await claim(log, "T-1")
        await log.record(
            EventKind.ESCALATION_RAISED,
            partition=PARTITION,
            subject_type=SubjectType.TASK,
            subject_id="T-1",
            actor_kind=ActorKind.WORKER,
            actor_id="w-1",
            payload={"reason": "poison_ceiling", "detail": "3 attempts"},
        )
        board = project(await log.since())

        assert board.tasks["T-1"].state is TaskState.ESCALATED
        assert board.tasks["T-1"].escalation == "poison_ceiling"
        assert board.tasks["T-1"].claimed_by is None
        assert dispatchable(board, PARTITION) == []

        await log.record(
            EventKind.ESCALATION_RESOLVED,
            partition=PARTITION,
            subject_type=SubjectType.TASK,
            subject_id="T-1",
            actor_kind=ActorKind.OPERATOR,
            actor_id="misery7100",
            payload={"resolution": "requeued", "note": "contract amended"},
        )

        assert dispatchable(project(await log.since()), PARTITION) == ["T-1"]

    run(scenario)


def test_an_abandoned_task_never_returns():
    async def scenario(log):
        await mint(log, "T-1")
        await log.record(
            EventKind.ESCALATION_RAISED,
            partition=PARTITION,
            subject_type=SubjectType.TASK,
            subject_id="T-1",
            actor_kind=ActorKind.WORKER,
            actor_id="w-1",
            payload={"reason": "underspecified"},
        )
        await log.record(
            EventKind.ESCALATION_RESOLVED,
            partition=PARTITION,
            subject_type=SubjectType.TASK,
            subject_id="T-1",
            actor_kind=ActorKind.OPERATOR,
            actor_id="misery7100",
            payload={"resolution": "abandoned"},
        )
        board = project(await log.since())

        assert board.tasks["T-1"].state is TaskState.ABANDONED
        assert dispatchable(board, PARTITION) == []

    run(scenario)


def test_a_replayed_log_rebuilds_the_same_board():
    """S-0044/D-5's restart transparency is a property of the data: a manager
    that died learns nothing by being handed state, because reading the log
    again is what it would have been handed."""

    async def scenario(log):
        await mint(log, "T-1")
        await mint(log, "T-2")
        await claim(log, "T-1")
        await land(log, "T-1")

        first = project(await log.since())
        second = project(await log.since())

        assert first == second
        assert first.landed() == {"T-1"}
        assert first.tasks["T-1"].landed_sha == "a" * 40

    run(scenario)


def test_a_returned_candidate_is_queued_again_and_shows_no_landing():
    """A-134: a candidate reaches `ready` when its gates and its review are
    done, and a person may still judge it wrong. Nothing could send it back
    — `task.released` is the manager reclaiming an expired lease and
    `expired()` sees only in-flight states, so `ready` was landed-or-
    abandoned and nothing else. T-0282 reached it with a real defect in it.
    """

    async def scenario(log):
        await mint(log, "T-1")
        await claim(log, "T-1")
        await land(log, "T-1")

        landed = project(await log.since())
        assert landed.tasks["T-1"].state is TaskState.READY
        assert landed.tasks["T-1"].landed_sha == "a" * 40
        assert dispatchable(landed, PARTITION) == []

        await log.record(
            EventKind.TASK_RETURNED,
            partition=PARTITION,
            subject_type=SubjectType.TASK,
            subject_id="T-1",
            actor_kind=ActorKind.OPERATOR,
            actor_id="operator",
            payload={"reason": "the decode aborts the pass", "note": "wrap the decode"},
        )

        board = project(await log.since())
        view = board.tasks["T-1"]

        assert view.state is TaskState.QUEUED
        # The candidate's commit is no longer the answer, and a queued row
        # still showing a landing reads as a landing.
        assert view.landed_sha is None
        assert view.claimed_by is None
        assert board.landed() == set()
        # And the whole point: a worker can pick it up again.
        assert dispatchable(board, PARTITION) == ["T-1"]

    run(scenario)


def test_only_an_operator_may_send_a_candidate_back():
    """ESCALATION_RESOLVED's reason exactly (A-134): an agent that could
    return its own judged work could route around every verdict it
    disliked."""

    from torve.domain.events import AUTHORITY, UnauthorizedWrite, check_authority

    assert AUTHORITY[EventKind.TASK_RETURNED] == frozenset({ActorKind.OPERATOR})

    for actor in (ActorKind.AGENT, ActorKind.WORKER, ActorKind.MANAGER):
        with pytest.raises(UnauthorizedWrite):
            check_authority(actor, EventKind.TASK_RETURNED)


def test_facts_that_are_not_transitions_leave_the_state_alone():
    """A divergence, a message or a burn event says something about the
    work, never about whose turn it is (S-0044/D-1)."""

    async def scenario(log):
        await mint(log, "T-1")
        await claim(log, "T-1")
        await log.record(
            EventKind.SEAT_CONSUMED,
            partition=PARTITION,
            subject_type=SubjectType.TASK,
            subject_id="T-1",
            actor_kind=ActorKind.WORKER,
            actor_id="w-1",
            payload={"seat": "executor", "tokens": 120, "cost_usd": 0.02},
        )
        board = project(await log.since())

        assert board.tasks["T-1"].state is TaskState.CLAIMED

    run(scenario)


def test_a_view_defaults_to_queued():
    assert TaskView(task_id="T-1").state is TaskState.QUEUED


# ....................... #

# Liveness, read from the burn stream and nowhere else (S-0045 S-0045/D-4).


def test_burn_lands_on_the_board_as_a_rate_and_a_total():
    now = datetime.now(UTC)
    board = project(
        [
            event(EventKind.TASK_MINTED, "T-1"),
            event(EventKind.TASK_CLAIMED, "T-1"),
            event(
                EventKind.ATTEMPT_STARTED, "T-1", {"attempt": 1, "tier": "executor", "agent": "x"}
            ),
            event(
                EventKind.SEAT_CONSUMED,
                "T-1",
                {"seat": "anthropic", "tokens": 900, "cost_usd": 0.02},
                at=now - timedelta(minutes=30),
            ),
            event(
                EventKind.SEAT_CONSUMED,
                "T-1",
                {"seat": "anthropic", "tokens": 600, "cost_usd": 0.01},
                at=now - timedelta(minutes=25),
            ),
        ]
    )
    view = board.tasks["T-1"]

    assert round(view.burned_usd, 3) == 0.03
    assert view.last_burn == now - timedelta(minutes=25)
    # Twenty-five minutes of an in-flight attempt spending nothing.
    assert stalled(view, now=now) is True
    assert stalled(view, now=now, after=timedelta(hours=1)) is False


def test_a_task_nobody_is_running_is_not_stalled():
    now = datetime.now(UTC)
    board = project(
        [
            event(EventKind.TASK_MINTED, "T-1"),
            event(EventKind.TASK_CLAIMED, "T-1"),
            event(
                EventKind.SEAT_CONSUMED,
                "T-1",
                {"seat": "anthropic", "tokens": 1, "cost_usd": 0.0},
                at=now - timedelta(days=1),
            ),
            event(EventKind.LANDING_RECORDED, "T-1", {"sha": "a" * 40, "attempt": 1}),
        ]
    )

    # Landed a day after its last burn: idle, not wedged.
    assert stalled(board.tasks["T-1"], now=now) is False


def test_an_attempt_that_has_burned_nothing_yet_accuses_nobody():
    now = datetime.now(UTC)
    board = project(
        [
            event(EventKind.TASK_MINTED, "T-1"),
            event(EventKind.TASK_CLAIMED, "T-1"),
            event(
                EventKind.ATTEMPT_STARTED, "T-1", {"attempt": 1, "tier": "executor", "agent": "x"}
            ),
        ]
    )

    # A sandbox still building, or a tier that calls no provider at all.
    assert stalled(board.tasks["T-1"], now=now) is False


# ....................... #

# The lease (S-0044 S-0044/D-6): a worker holds nothing else, and something
# has to be it running out.


def test_a_claim_that_has_gone_silent_past_its_lease_is_expired():
    now = datetime.now(UTC)
    board = project(
        [
            event(EventKind.TASK_MINTED, "T-1", at=now - timedelta(hours=2)),
            event(EventKind.TASK_CLAIMED, "T-1", {"worker": "w-1"}, at=now - timedelta(hours=1)),
        ]
    )

    assert [view.task_id for view in expired(board, now=now)] == ["T-1"]
    # The window is the manager's rule, and a longer one forgives the same
    # silence.
    assert expired(board, now=now, lease=timedelta(hours=3)) == []


def test_a_working_claim_is_not_expired():
    now = datetime.now(UTC)
    board = project(
        [
            event(EventKind.TASK_MINTED, "T-1", at=now - timedelta(hours=2)),
            event(EventKind.TASK_CLAIMED, "T-1", {"worker": "w-1"}, at=now - timedelta(hours=1)),
            # Activity is any recorded fact, not a heartbeat the holder
            # sends: a wedged process can report itself healthy, and a burn
            # event is what the work actually produced.
            event(
                EventKind.SEAT_CONSUMED,
                "T-1",
                {"seat": "anthropic", "tokens": 10, "cost_usd": 0.01},
                at=now - timedelta(minutes=1),
            ),
        ]
    )

    assert expired(board, now=now) == []


def test_a_gate_pass_keeps_its_lease_for_the_budget_it_announced():
    # A battery records nothing until its verdict. erp-backend's cold one ran
    # 21 minutes, and a second worker released a task the first was still
    # judging; the pass now says how long it may stay silent.
    now = datetime.now(UTC)
    started = now - timedelta(minutes=20)
    facts = [
        event(EventKind.TASK_MINTED, "T-1", at=now - timedelta(hours=1)),
        event(EventKind.TASK_CLAIMED, "T-1", {"worker": "w-1"}, at=now - timedelta(minutes=30)),
        event(EventKind.GATES_STARTED, "T-1", {"attempt": 1, "budget_s": 3600.0}, at=started),
    ]

    assert expired(project(facts), now=now) == []
    # Past the lease and the budget both, the holder is gone after all.
    later = started + timedelta(seconds=LEASE_SECONDS + 3600 + 1)
    assert [view.task_id for view in expired(project(facts), now=later)] == ["T-1"]

    # Facts are relayed without waiting, so one written before the pass can
    # land after it: a late burn must not cut the pass's silence short.
    late = event(
        EventKind.SEAT_CONSUMED,
        "T-1",
        {"seat": "anthropic", "tokens": 10, "cost_usd": 0.01},
        at=started + timedelta(seconds=1),
    )
    assert expired(project([*facts, late]), now=now) == []

    # The budget is that pass's alone: the verdict ends it, and the ordinary
    # lease runs from there.
    verdict = event(
        EventKind.GATES_EVALUATED,
        "T-1",
        {"attempt": 1, "exit_code": 0},
        at=now - timedelta(minutes=16),
    )
    assert [view.task_id for view in expired(project([*facts, verdict]), now=now)] == ["T-1"]


def test_a_gate_pass_within_its_budget_is_not_stalled():
    # The battery burns nothing; the board must not call it wedged meanwhile.
    now = datetime.now(UTC)
    burn = {"seat": "anthropic", "tokens": 10, "cost_usd": 0.01}
    facts = [
        event(EventKind.TASK_CLAIMED, "T-1", {"worker": "w-1"}, at=now - timedelta(minutes=40)),
        event(EventKind.SEAT_CONSUMED, "T-1", burn, at=now - timedelta(minutes=30)),
        event(
            EventKind.GATES_STARTED,
            "T-1",
            {"attempt": 1, "budget_s": 3600.0},
            at=now - timedelta(minutes=29),
        ),
    ]

    assert stalled(project(facts).tasks["T-1"], now=now) is False
    assert stalled(project(facts).tasks["T-1"], now=now + timedelta(hours=1)) is True


def test_a_task_nobody_holds_never_expires():
    now = datetime.now(UTC)
    board = project(
        [
            event(EventKind.TASK_MINTED, "T-1", at=now - timedelta(days=7)),
            event(EventKind.TASK_CLAIMED, "T-1", {"worker": "w-1"}, at=now - timedelta(days=7)),
            event(
                EventKind.LANDING_RECORDED,
                "T-1",
                {"sha": "a" * 40, "attempt": 1},
                at=now - timedelta(days=7),
            ),
        ]
    )

    # Landed a week ago and held by nobody: idle, not abandoned.
    assert expired(board, now=now) == []


def test_two_unconstrained_tasks_never_run_together():
    """The defect this shares a rule to prevent: an empty allow-set is
    unconstrained (S-0002/scope-in-detail), and a glob intersection over two empty
    sets is empty — so the manager would have dispatched two tasks that may
    each touch anything, while the standing loop refused the same pair."""

    unconstrained = Task(id="T-1", decisions=[]).model_dump(mode="json")
    board = project(
        [
            event(EventKind.TASK_MINTED, "T-1", {"contract": unconstrained}),
            event(
                EventKind.TASK_MINTED,
                "T-2",
                {"contract": {**unconstrained, "id": "T-2"}},
            ),
            event(EventKind.TASK_CLAIMED, "T-1", {"worker": "w-1"}),
        ]
    )

    assert dispatchable(board, PARTITION) == []


def test_an_oversize_contract_is_dispatchable_whatever_its_size(tmp_path):
    """S-0089/D-1: dispatch does not consult the size estimate — a queued
    too_large contract is offered like any other."""

    from torve.domain.task import Task

    huge = Task(
        id="T-1",
        decisions=[],
        intent="x" * 4000,
        scope=Scope(allow=["src/**"]),
        acceptance=["a"] * 12,
    )
    board = project(
        [event(EventKind.TASK_MINTED, huge.id, {"contract": huge.model_dump(mode="json")})]
    )

    assert dispatchable(board, PARTITION) == ["T-1"]


def test_resolving_an_escalation_returns_the_task_or_takes_it_off_the_board():
    """An escalation is the engine handing a task to a person; resolving is
    the person handing it back. Only an operator may write it (S-0044/D-2)."""

    def board_after(resolution: str):
        return project(
            [
                event(EventKind.TASK_MINTED, "T-1"),
                event(EventKind.TASK_CLAIMED, "T-1", {"worker": "w-1"}),
                event(EventKind.ESCALATION_RAISED, "T-1", {"reason": "poison_ceiling"}),
                event(EventKind.ESCALATION_RESOLVED, "T-1", {"resolution": resolution}),
            ]
        ).tasks["T-1"]

    requeued = board_after("requeued")
    assert requeued.state is TaskState.QUEUED
    assert requeued.escalation is None
    assert requeued.claimed_by is None

    assert board_after("abandoned").state is TaskState.ABANDONED


def test_a_task_requeued_folds_as_a_return_folds():
    """S-0109/D-1: the board folds `task.requeued` as it folds `task.returned`
    — `queued`, with the claim and the landed sha cleared — so the next pass
    dispatches the revision attempt the disposal promised."""

    board = project(
        [
            event(EventKind.TASK_MINTED, "T-1"),
            event(EventKind.LANDING_RECORDED, "T-1", {"sha": "abc123", "attempt": 1}),
            # After the landing, so the requeue's own fold is what clears it.
            event(EventKind.TASK_CLAIMED, "T-1", {"worker": "w-1"}),
            event(
                EventKind.TASK_REQUEUED,
                "T-1",
                {"reason": "merge_conflict", "base_tip": "def456"},
            ),
        ]
    )
    view = board.tasks["T-1"]

    assert view.state is TaskState.QUEUED
    assert view.claimed_by is None
    assert view.landed_sha is None


def test_a_landed_resolution_reads_as_a_landing():
    """`landed` is a hand finish: the row must read as a landing reads — off
    the queue, with the sha the person named — or the worker re-dispatches
    the task from its base and the hand finish is redone (bloomery T-0087:
    resolved landed at 16:40, claimed at 16:41)."""
    board = project(
        [
            event(EventKind.TASK_MINTED, "T-1"),
            event(EventKind.TASK_CLAIMED, "T-1", {"worker": "w-1"}),
            event(EventKind.ESCALATION_RAISED, "T-1", {"reason": "blocker_finding"}),
            event(EventKind.ESCALATION_RESOLVED, "T-1", {"resolution": "landed", "sha": "abc123"}),
        ]
    )
    landed = board.tasks["T-1"]
    assert landed.state is TaskState.READY
    assert landed.landed_sha == "abc123"
    assert landed.escalation is None
    assert landed.claimed_by is None


def test_an_agent_may_not_close_its_own_escalation():
    from torve.domain.events import AUTHORITY, UnauthorizedWrite, check_authority

    assert AUTHORITY[EventKind.ESCALATION_RESOLVED] == frozenset({ActorKind.OPERATOR})

    # An agent that could close its own escalation could escalate its way
    # out of every rule it dislikes.
    for actor in (ActorKind.AGENT, ActorKind.WORKER, ActorKind.MANAGER):
        with pytest.raises(UnauthorizedWrite):
            check_authority(actor, EventKind.ESCALATION_RESOLVED)


# ....................... #

# What the night left on the forge (S-0080/D-14): four counts folded from the
# events in the window, and nowhere for prose to go.


def stream(event: str, at: datetime, **fields):
    return {"kind": "engine", "event": event, "at": stamp(at), **fields}


def test_the_forge_counts_are_a_fold_over_the_windows_events():
    opened = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)
    rows = [
        stream("lane_pr_opened", opened + timedelta(minutes=5), task="T-1"),
        stream("lane_pr_opened", opened + timedelta(minutes=9), task="T-2"),
        stream("lane_landed", opened + timedelta(hours=1), task="T-3", mode="pull-request"),
        stream("lane_conflict", opened + timedelta(hours=2), task="T-4"),
        stream("lane_pr_closed", opened + timedelta(hours=3), task="T-5"),
        # A local landing is a landing, and not a pull request anybody merged.
        stream("lane_landed", opened + timedelta(hours=4), task="T-6", mode="fast-forward"),
        # Facts about the night that say nothing about the forge.
        stream("lane_gates_red", opened + timedelta(hours=4), task="T-7"),
    ]

    counts = pull_requests(rows, since=opened)

    assert counts == PullRequests(opened=2, merged=1, conflicted=1, closed=1)


def test_a_fact_outside_the_window_belongs_to_another_night():
    opened = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)
    closed = opened + timedelta(hours=8)
    rows = [
        stream("lane_pr_opened", opened - timedelta(minutes=1), task="T-1"),
        stream("lane_pr_opened", opened + timedelta(hours=1), task="T-2"),
        stream("lane_pr_opened", closed + timedelta(minutes=1), task="T-3"),
        # An instant that does not read counts nowhere rather than raising.
        {"event": "lane_pr_opened", "at": "last tuesday", "task": "T-4"},
        {"event": "lane_pr_opened", "task": "T-5"},
    ]

    assert pull_requests(rows, since=opened, until=closed) == PullRequests(opened=1)


def test_an_idle_night_counts_nothing():
    assert pull_requests([], since=datetime.now(UTC)) == PullRequests()


# ....................... #

# What the night left on the forge at the document unit (S-0083/D-16): three
# counts beside the four, folded from the same window and with nowhere for
# prose to go either.


def test_the_document_counts_are_a_fold_over_the_windows_events():
    opened = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)
    rows = [
        stream(
            "lane_landed",
            opened + timedelta(minutes=5),
            task="T-1",
            unit="document",
            branch="torve/S-0083",
        ),
        # A second phase onto the same branch refreshes one pull request:
        # one document, and one merge waiting on a person.
        stream(
            "lane_landed",
            opened + timedelta(hours=1),
            task="T-2",
            unit="document",
            branch="torve/S-0083",
        ),
        stream(
            "lane_landed",
            opened + timedelta(hours=2),
            task="T-3",
            unit="document",
            branch="torve/S-0084",
        ),
        stream(
            "lane_document_landed",
            opened + timedelta(hours=3),
            branch="torve/S-0080",
            tasks=["T-4"],
        ),
        stream("lane_document_closed", opened + timedelta(hours=4), branch="torve/S-0081"),
        # A task-unit landing is not a document, and a conflict on a
        # document branch is neither opened, merged nor closed.
        stream("lane_landed", opened + timedelta(hours=5), task="T-5", mode="fast-forward"),
        stream("lane_document_conflict", opened + timedelta(hours=5), branch="torve/S-0083"),
        # A record the lane wrote about no branch at all counts nowhere.
        stream("lane_landed", opened + timedelta(hours=6), task="T-6", unit="document"),
    ]

    assert documents(rows, since=opened) == Documents(opened=2, merged=1, closed=1)


def test_a_document_fact_outside_the_window_belongs_to_another_night():
    opened = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)
    closed = opened + timedelta(hours=8)
    rows = [
        stream(
            "lane_landed",
            opened - timedelta(minutes=1),
            unit="document",
            branch="torve/S-0082",
        ),
        stream(
            "lane_landed",
            opened + timedelta(hours=1),
            unit="document",
            branch="torve/S-0083",
        ),
        stream("lane_document_landed", closed + timedelta(minutes=1), branch="torve/S-0084"),
        # An instant that does not read counts nowhere rather than raising.
        {"event": "lane_document_closed", "at": "last tuesday", "branch": "torve/S-0085"},
    ]

    assert documents(rows, since=opened, until=closed) == Documents(opened=1)


def test_an_idle_night_left_no_documents_either():
    assert documents([], since=datetime.now(UTC)) == Documents()


# ....................... #

# What the review-thread leg did with the night's threads (S-0084/D-17): five
# counts folded from the leg's own events, beside the forge's.


def test_the_review_thread_counts_are_a_fold_over_the_leg_s_own_events():
    opened = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)
    rows = [
        stream(
            "lane_pr_threads",
            opened + timedelta(minutes=5),
            branch="torve/S-0084",
            pr=7,
            threads=3,
            findings=[
                {"path": "src/a.py", "line": 10, "threads": ["t1", "t2"]},
                {"path": "src/b.py", "line": 4, "threads": ["t3"]},
            ],
        ),
        # The read-back records what it saw every pass the pull request is
        # open: one night of many passes still saw three threads.
        stream(
            "lane_pr_threads",
            opened + timedelta(minutes=20),
            branch="torve/S-0084",
            pr=7,
            threads=3,
            findings=[
                {"path": "src/a.py", "line": 10, "threads": ["t1", "t2"]},
                {"path": "src/b.py", "line": 4, "threads": ["t3"]},
            ],
        ),
        stream(
            "lane_review_task",
            opened + timedelta(minutes=21),
            branch="torve/S-0084",
            task="T-9",
            threads=["t1", "t2"],
        ),
        stream(
            "lane_thread_resolved",
            opened + timedelta(hours=2),
            branch="torve/S-0084",
            task="T-9",
            thread="t1",
            resolved=True,
        ),
        stream(
            "lane_thread_resolved",
            opened + timedelta(hours=2),
            branch="torve/S-0084",
            task="T-9",
            thread="t2",
            resolved=False,
        ),
        stream(
            "lane_thread_refused",
            opened + timedelta(hours=3),
            branch="torve/S-0084",
            threads=["t4"],
            reason="a thread asked for a command to be run",
        ),
        # One finding re-raised, seen by two passes and waiting on one person.
        stream(
            "lane_finding_reraised",
            opened + timedelta(hours=4),
            branch="torve/S-0084",
            path="src/b.py",
            threads=["t3"],
        ),
        stream(
            "lane_finding_reraised",
            opened + timedelta(hours=5),
            branch="torve/S-0084",
            path="src/b.py",
            threads=["t3"],
        ),
        # A fact about the night that says nothing about a thread.
        stream("lane_pr_opened", opened + timedelta(hours=5), task="T-1"),
    ]

    counts = review_threads(rows, since=opened)

    assert counts == ReviewThreads(seen=3, minted=1, answered=2, refused=1, escalated=1)


def test_a_thread_fact_outside_the_window_belongs_to_another_night():
    opened = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)
    closed = opened + timedelta(hours=8)
    rows = [
        stream(
            "lane_pr_threads",
            opened - timedelta(minutes=1),
            findings=[{"path": "src/a.py", "threads": ["t0"]}],
        ),
        stream(
            "lane_pr_threads",
            opened + timedelta(hours=1),
            findings=[{"path": "src/a.py", "threads": ["t1"]}],
        ),
        stream("lane_review_task", closed + timedelta(minutes=1), task="T-9"),
        # An instant that does not read counts nowhere rather than raising.
        {"event": "lane_thread_refused", "at": "last tuesday", "threads": ["t2"]},
    ]

    assert review_threads(rows, since=opened, until=closed) == ReviewThreads(seen=1)


def test_a_night_that_answered_no_threads_counts_none():
    assert review_threads([], since=datetime.now(UTC)) == ReviewThreads()


def test_a_reply_and_a_resolve_are_mutations_against_the_threads_own_node_id():
    """The two writes the leg is handed: a comment and a resolution, each
    addressing the node id the read-back carries — and neither a merge, a push
    nor a force-push (S-0084/D-10)."""

    from torve.cli.manager import _ThreadForge

    calls: list[tuple[str, ...]] = []

    class _Scm:
        def _api(self, *args: str) -> str:
            calls.append(args)

            return ""

    forge = _ThreadForge(_Scm())
    forge.reply_thread("PRRT_kwABC", "landed in abc1234")
    forge.resolve_thread("PRRT_kwABC")

    assert [one[0] for one in calls] == ["graphql", "graphql"]
    assert "addPullRequestReviewThreadReply" in calls[0][2]
    assert "thread=PRRT_kwABC" in calls[0]
    assert "body=landed in abc1234" in calls[0]
    assert "resolveReviewThread" in calls[1][2]
    assert "thread=PRRT_kwABC" in calls[1]


# ....................... #
# `--night` at the terminal (S-0079/D-6): the one refusal an operator is
# present for, and the one line that says which term ended the night.


def _serve_night(tmp_path, *args):
    from typer.testing import CliRunner

    from torve.cli.main import app

    (tmp_path / ".torve").mkdir(exist_ok=True)

    return CliRunner().invoke(
        app,
        [
            "manager",
            "serve",
            PARTITION,
            "--night",
            "--passes",
            "1",
            "--interval",
            "0",
            "--root",
            str(tmp_path),
            *args,
        ],
    )


def test_a_night_over_an_empty_board_is_refused_before_the_first_pass(tmp_path):
    """Nothing to start, so the night would sleep to morning having claimed
    nothing. Refused now rather than reported at breakfast."""

    result = _serve_night(tmp_path)

    assert result.exit_code == EXIT_CONFIG, result.output
    assert "night refused" in result.output


def test_a_night_imports_the_repositorys_contracts_before_it_opens(tmp_path, monkeypatch):
    """S-0096/D-3: `serve --night` runs one import pass — the scan's `mint`
    over the repository's contracts — before it opens, so the queue the open
    reads is the queue it will work. Observed at the open rather than after
    it, which is the only place the ordering is visible: the board already
    carries the contract the repository minted, never seen by this partition
    before."""

    import yaml

    from torve.application import residency
    from torve.application.manager import project
    from torve.cli.manager import _serve

    contract = tmp_path / ".torve" / "tasks" / "T-1" / "contract.yaml"
    contract.parent.mkdir(parents=True)
    contract.write_text(
        yaml.safe_dump({"id": "T-1", "decisions": [], "scope": {"allow": ["src/**"], "deny": []}}),
        encoding="utf-8",
    )

    seen = {}

    class Opened(Exception):
        pass

    async def spy(log, partition, **kwargs):
        seen["board"] = project(await log.since(partition=partition))
        raise Opened

    monkeypatch.setattr(residency, "open_night", spy)

    with pytest.raises(Opened):
        asyncio.run(
            _serve(
                None,
                PARTITION,
                root=tmp_path,
                config_path=None,
                worker="w-1",
                passes=1,
                interval=0,
                only=None,
                dispatch=False,
                night=True,
            )
        )

    assert "T-1" in seen["board"].tasks


def test_a_serve_without_the_switch_is_the_pass_it_always_was(tmp_path):
    """The night is opt-in: the same empty board is an idle pass and a
    success, which is what it was before the switch existed."""

    import json

    from typer.testing import CliRunner

    from torve.cli.main import app

    (tmp_path / ".torve").mkdir(exist_ok=True)
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
    assert json.loads(result.stdout)["stopped_on"] == ""


# ....................... #
# The morning report (S-0079/D-3, S-0079/D-4): a fold over the window between
# an open and its close, with nowhere for a sentence to go.


NIGHT = "20260917T220000Z"
TERMS = {"queue": ["T-1"], "width": 1, "budget_attempts": 5, "lease_seconds": 900}


def night_event(kind, subject_id, payload, *, at, partition=PARTITION) -> EventRecord:
    """A night's own open or close — a subject in the log, not a file."""

    return EventRecord(
        id=uuid.uuid4().hex,
        rev=1,
        created_at=at,
        last_update_at=at,
        kind=kind,
        partition=partition,
        subject_type=SubjectType.NIGHT,
        subject_id=subject_id,
        actor_kind=ActorKind.MANAGER,
        actor_id="manager-1",
        payload=payload,
    )


def test_a_night_with_no_open_is_no_night():
    assert night_report([]) is None
    assert night_report([event(EventKind.TASK_MINTED, "T-1")]) is None


def test_the_report_is_the_four_lists_the_window_already_held():
    opened = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)
    closed = opened + timedelta(hours=8)
    events = [
        # Yesterday's landing belongs to yesterday's window.
        event(EventKind.LANDING_RECORDED, "T-0", {"sha": "f" * 40}, at=opened - timedelta(hours=1)),
        night_event(EventKind.NIGHT_OPENED, NIGHT, TERMS, at=opened),
        event(EventKind.LANDING_RECORDED, "T-1", {"sha": "a" * 40}, at=opened + timedelta(hours=1)),
        event(
            EventKind.GATES_EVALUATED,
            "T-2",
            {
                "attempt": 2,
                "exit_code": 1,
                "results": [
                    {"name": "acceptance", "outcome": "fail", "state": "blocking"},
                    {"name": "lint", "outcome": "pass", "state": "shadow"},
                ],
            },
            at=opened + timedelta(hours=2),
        ),
        event(
            EventKind.ATTEMPT_FINISHED,
            "T-3",
            {"attempt": 3, "escalation": "poison_ceiling"},
            at=opened + timedelta(hours=3),
        ),
        # An attempt that went on to a gate pass has no ending of its own.
        event(EventKind.ATTEMPT_FINISHED, "T-2", {"attempt": 2}, at=opened + timedelta(hours=3)),
        event(
            EventKind.ESCALATION_RAISED,
            "T-3",
            {"reason": "poison_ceiling"},
            at=opened + timedelta(hours=3, minutes=1),
        ),
        night_event(EventKind.NIGHT_CLOSED, NIGHT, {"reason": "drained", "handled": 3}, at=closed),
        # Tomorrow's facts are not tonight's.
        event(EventKind.LANDING_RECORDED, "T-9", {"sha": "b" * 40}, at=closed + timedelta(hours=1)),
    ]

    report = night_report(events)

    assert report is not None
    assert (report.night_id, report.opened_at, report.closed_at) == (NIGHT, opened, closed)
    assert report.unfinished is False
    assert report.close is not None and report.close.reason == "drained"
    assert report.terms.budget_attempts == 5
    assert [(one.task_id, one.sha) for one in report.landed] == [("T-1", "a" * 40)]
    # Only a gate that convicted; a green one is not an entry.
    assert [(one.task_id, one.attempt, one.gate, one.outcome) for one in report.convicted] == [
        ("T-2", 2, "acceptance", "fail")
    ]
    assert [(one.task_id, one.reason) for one in report.ended] == [("T-3", "poison_ceiling")]
    assert [(one.task_id, one.reason) for one in report.waiting] == [("T-3", "poison_ceiling")]


def test_an_escalation_a_person_closed_is_no_longer_waiting_on_one():
    opened = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)
    report = night_report(
        [
            night_event(EventKind.NIGHT_OPENED, NIGHT, TERMS, at=opened),
            event(
                EventKind.ESCALATION_RAISED,
                "T-1",
                {"reason": "merge_conflict"},
                at=opened + timedelta(hours=1),
            ),
            event(
                EventKind.ESCALATION_RESOLVED,
                "T-1",
                {"resolution": "requeued"},
                at=opened + timedelta(hours=2),
            ),
        ]
    )

    assert report is not None
    assert report.waiting == ()


def test_a_night_with_no_close_is_unfinished_and_its_window_is_still_open():
    """A manager killed at 04:00 wrote the open and nothing after it. That
    reads as unfinished, and everything since the open is still its own."""

    opened = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)
    report = night_report(
        [
            night_event(EventKind.NIGHT_OPENED, NIGHT, TERMS, at=opened),
            event(
                EventKind.LANDING_RECORDED,
                "T-1",
                {"sha": "a" * 40},
                at=opened + timedelta(days=2),
            ),
        ]
    )

    assert report is not None
    assert report.unfinished is True
    assert (report.closed_at, report.close) == (None, None)
    assert [one.task_id for one in report.landed] == ["T-1"]


def test_a_named_night_is_the_one_folded_and_the_default_is_the_latest():
    first = datetime(2026, 9, 16, 22, 0, tzinfo=UTC)
    second = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)
    events = [
        night_event(EventKind.NIGHT_OPENED, "first", TERMS, at=first),
        night_event(
            EventKind.NIGHT_CLOSED, "first", {"reason": "wall_clock"}, at=first + timedelta(hours=8)
        ),
        night_event(EventKind.NIGHT_OPENED, "second", TERMS, at=second),
    ]

    assert night_report(events).night_id == "second"
    assert night_report(events, night_id="first").night_id == "first"
    assert night_report(events, night_id="third") is None


def test_the_board_names_the_document_a_task_waits_on(tmp_path, monkeypatch):
    """S-0085/D-6 promised it on `torve manager board` as well as on `torve
    night show`, and only the night had it: a task waiting on another
    document's landing names that document, and a dependency the base holds
    a landing for is no wait."""
    import json

    import yaml
    from typer.testing import CliRunner

    from torve.application.manager import Board, TaskView
    from torve.cli import manager as manager_cli
    from torve.cli.main import app

    landing = tmp_path / ".torve" / "execution"
    landing.mkdir(parents=True)
    (landing / "T-0001-1-20260909T120000Z.yaml").write_text(
        "task: T-0001\nat: '2026-09-09T12:00:00Z'\ncommit: " + "a" * 40 + "\n",
        encoding="utf-8",
    )

    for task_id, spec, depends_on in (
        ("T-0001", "S-0090", ()),
        ("T-0002", "S-0090", ()),
        ("T-0003", "S-0092", ("T-0001", "T-0002")),
    ):
        directory = tmp_path / ".torve" / "tasks" / task_id
        directory.mkdir(parents=True)
        (directory / "contract.yaml").write_text(
            yaml.safe_dump({"id": task_id, "spec": spec, "depends_on": list(depends_on)}),
            encoding="utf-8",
        )

    board = Board(
        tasks={
            "T-0001": TaskView(task_id="T-0001", state=TaskState.READY, landed_sha="a" * 40),
            "T-0002": TaskView(task_id="T-0002"),
            "T-0003": TaskView(task_id="T-0003"),
        }
    )

    async def fake_board(dsn, partition):
        return board

    monkeypatch.setattr(manager_cli, "_board", fake_board)
    monkeypatch.setattr(manager_cli, "dsn_for", lambda root, dsn: None)
    runner = CliRunner()

    shown = runner.invoke(
        app, ["manager", "board", "repo", "--root", str(tmp_path), "--format", "json"]
    )

    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["waiting_on_documents"] == {"T-0003": {"S-0090": ["T-0002"]}}
    # A candidate whose landing is recorded reads landed, not ready.
    states = {row["task"]: row["state"] for row in json.loads(shown.output)["tasks"]}
    assert states["T-0001"] == "landed"
    assert states["T-0002"] != "landed"

    text = runner.invoke(app, ["manager", "board", "repo", "--root", str(tmp_path)])

    assert text.exit_code == 0, text.output
    assert "waits on document" in text.output and "S-0090" in text.output


def test_the_board_table_reads_a_concluded_review_as_consumed(tmp_path, monkeypatch):
    """A runner-minted review ran inline in its target's run, and its row sat
    `queued` forever, which read as a queue that never drains. The table says
    `consumed`, as `torve status` does; the JSON keeps `queued` and the role
    (S-0049/A-1), so a poller reading it sees no change."""
    import json

    from typer.testing import CliRunner

    from torve.application.manager import Board, TaskView
    from torve.cli import manager as manager_cli
    from torve.cli.main import app
    from torve.domain.task import Task

    board = Board(
        tasks={
            "T-0001": TaskView(task_id="T-0001", contract=Task(id="T-0001", decisions=[])),
            "T-0002": TaskView(
                task_id="T-0002",
                contract=Task(id="T-0002", role="review", targets=["T-0001"], decisions=[]),
            ),
        }
    )

    async def fake_board(dsn, partition):
        return board

    monkeypatch.setattr(manager_cli, "_board", fake_board)
    monkeypatch.setattr(manager_cli, "dsn_for", lambda root, dsn: None)
    runner = CliRunner()

    shown = runner.invoke(
        app, ["manager", "board", "repo", "--root", str(tmp_path), "--format", "json"]
    )

    assert shown.exit_code == 0, shown.output
    states = {row["task"]: row["state"] for row in json.loads(shown.output)["tasks"]}
    assert states == {"T-0001": "queued", "T-0002": "queued"}

    text = runner.invoke(app, ["manager", "board", "repo", "--root", str(tmp_path)])

    assert text.exit_code == 0, text.output
    rows = {line.split()[0]: line for line in text.output.splitlines() if "T-000" in line}
    assert "queued" in rows["T-0001"] and "consumed" not in rows["T-0001"]
    assert "consumed" in rows["T-0002"]


def test_a_landed_resolution_is_refused_for_a_sha_whose_tree_holds_no_landing_file(tmp_path):
    """S-0091/D-3: a hand resolution cannot claim a landing the tree does not
    carry — refused before anything is recorded, naming the task, the sha and
    the verb that writes the landing file."""
    from typer.testing import CliRunner

    from torve.adapters.vcs.git import GitLane
    from torve.cli.main import app
    from torve.gates.sabotage import Repo

    repo = Repo(tmp_path / "repo")
    repo.root.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.name", "Hand Finisher")
    repo.git("config", "user.email", "hand@example.invalid")
    repo.write("src/a/app.py", "print('by hand')\n")
    repo.commit("the hand finish")
    sha = GitLane().tip(repo.root, "HEAD")
    assert sha is not None

    result = CliRunner().invoke(
        app,
        [
            *("manager", "resolve", PARTITION, "T-0001", "--resolution", "landed"),
            *("--sha", sha, "--root", str(repo.root)),
        ],
    )

    assert result.exit_code == EXIT_CONFIG
    assert "T-0001" in result.output
    assert sha in result.output
    assert "torve log land" in result.output


def test_a_requeued_round_takes_its_documents_phasing_as_the_branch_holds_it(tmp_path, monkeypatch):
    """S-0092/D-4: a round requeued by a person is re-scoped from its
    document's phasing on the branch at the requeue, so a phase the operator
    widened there reaches the round's next attempt."""
    import yaml
    from test_decisions import document, place
    from typer.testing import CliRunner

    from torve.application.telemetry import engine_event
    from torve.cli import manager as cli_manager
    from torve.cli.main import app
    from torve.config import layout
    from torve.gates.sabotage import Repo

    async def recorded(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(cli_manager, "_resolve", recorded)

    def phasing(*scope: str) -> None:
        place(
            repo.root / ".torve" / "specs",
            "0084",
            document(
                "0084",
                [("D-1", "ASSUMED", "the leg reads threads", "src/app.py")],
                phasing=[{"phase": 1, "title": "t", "intent": "i", "scope": list(scope)}],
            ),
        )

    repo = Repo(tmp_path / "repo")
    repo.root.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.name", "Operator")
    repo.git("config", "user.email", "operator@example.invalid")
    phasing("src/app.py")
    repo.commit("the document")
    # The widened phase is the remote's alone (S-0094/D-2): the requeue's own
    # fetch is what brings it.
    remote = tmp_path / "remote.git"
    repo.git("init", "-q", "--bare", str(remote))
    repo.git("remote", "add", "origin", str(remote))
    repo.git("checkout", "-q", "-b", "torve/S-0084")
    phasing("src/app.py", "src/other.py")
    repo.commit("the phase widened by amendment")
    repo.git("push", "-q", "origin", "torve/S-0084")
    repo.git("checkout", "-q", "main")
    repo.git("branch", "-q", "-D", "torve/S-0084")
    repo.git("update-ref", "-d", "refs/remotes/origin/torve/S-0084")
    repo.write(
        f"{layout.TORVE_DIR}/tasks/T-0950/contract.yaml",
        yaml.safe_dump(
            {
                "id": "T-0950",
                "decisions": [],
                "scope": {"allow": ["src/app.py"], "deny": []},
                # The round rides in its own contract (S-0099/D-5): the
                # requeue's rescope reads it here, not from a `lane_review_task`
                # row on the host that minted it.
                "round": {
                    "branch": "torve/S-0084",
                    "pr": 0,
                    "findings": [
                        {"path": "src/app.py", "line": 3, "end_line": None, "threads": []}
                    ],
                    "phases": [],
                    "nonce": "",
                },
            }
        ),
    )
    engine_event(
        repo.root,
        "lane_review_task",
        {"branch": "torve/S-0084", "task": "T-0950", "path": "src/app.py", "line": 3},
    )

    result = CliRunner().invoke(
        app,
        [*("manager", "resolve", PARTITION, "T-0950"), *("--root", str(repo.root))],
    )

    assert result.exit_code == 0, result.output
    contract = yaml.safe_load(layout.task_file(repo.root, "T-0950").read_text(encoding="utf-8"))
    assert contract["scope"]["allow"] == [
        "src/app.py",
        "src/other.py",
        f"{layout.TORVE_DIR}/tasks/T-0950/**",
    ]


def test_a_requeued_phase_task_takes_its_contract_from_the_remotes_document_branch(
    tmp_path, monkeypatch
):
    """S-0094/D-1: a requeue refreshes a phase task's contract from its document
    as the remote's document branch holds it after the requeue's fetch."""
    import subprocess

    from test_plan import TABLE, phasing, written
    from typer.testing import CliRunner

    from torve.application.planner import plan_document, write_contracts
    from torve.cli import manager as cli_manager
    from torve.cli.main import app
    from torve.config import layout
    from torve.gates.context import load_task

    async def recorded(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(cli_manager, "_resolve", recorded)
    root = tmp_path / "repo"
    spec_dir = root / ".torve" / "specs"
    spec_dir.mkdir(parents=True)

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    written(spec_dir, "0090", "Widgets", phasing=phasing())
    (root / ".torve" / "config.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "corpus")
    write_contracts(root, plan_document(root, spec_dir, "0090"))
    git("init", "-q", "--bare", str(tmp_path / "remote.git"))
    git("remote", "add", "origin", str(tmp_path / "remote.git"))
    git("checkout", "-q", "-b", "torve/S-0090")
    written(
        spec_dir,
        "0090",
        "Widgets",
        rows=TABLE,
        phasing=phasing(scope=["src/widget/**", "pages/**"]),
    )
    git("add", ".torve/specs")
    git("commit", "-qm", "the phase widened by amendment")
    git("push", "-q", "origin", "torve/S-0090")
    git("checkout", "-q", "main")
    git("branch", "-q", "-D", "torve/S-0090")

    result = CliRunner().invoke(
        app, [*("manager", "resolve", PARTITION, "T-0001"), *("--root", str(root))]
    )

    assert result.exit_code == 0, result.output
    assert load_task(layout.task_file(root, "T-0001")).scope.allow == ["src/widget/**", "pages/**"]
    assert load_task(layout.task_file(root, "T-0002")).scope.allow == ["src/frob/**"]


def _escalated_state(root, task_id):
    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.domain.states import EscalationReason, TaskState

    state = RunState(task_id=task_id, path=naming.state_file(root, task_id))
    state.transition(TaskState.CLAIMED, "t")
    state.transition(TaskState.RUNNING, "t")
    state.escalate(EscalationReason.POISON_CEILING, "3 attempts, ceiling 3")
    return state


def test_a_requeued_resolution_clears_the_tasks_escalated_host_state(tmp_path, monkeypatch):
    """S-0094/D-3: the escalation's own run-state file and worktree are
    cleared before the requeue is written, so the next dispatch neither
    refuses on a state file still claiming the task nor fails the overlap
    gate on a worktree the escalation left behind."""
    from typer.testing import CliRunner

    from torve.cli import manager as cli_manager
    from torve.cli.main import app
    from torve.gates.sabotage import Repo

    async def recorded(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(cli_manager, "_resolve", recorded)

    repo = Repo(tmp_path / "repo")
    repo.root.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.name", "Operator")
    repo.git("config", "user.email", "operator@example.invalid")
    repo.write("a.txt", "x\n")
    repo.commit("init")

    state = _escalated_state(repo.root, "T-9810")

    result = CliRunner().invoke(
        app,
        [*("manager", "resolve", PARTITION, "T-9810"), *("--root", str(repo.root))],
    )

    assert result.exit_code == 0, result.output
    assert not state.path.exists()


def test_an_abandoned_resolution_leaves_the_tasks_host_state_alone(tmp_path, monkeypatch):
    """S-0094/D-5: `abandoned` clears nothing — a person can still read an
    abandoned attempt's worktree and diff before `torve reap --escalated`
    sweeps it."""
    from typer.testing import CliRunner

    from torve.cli import manager as cli_manager
    from torve.cli.main import app
    from torve.gates.sabotage import Repo

    async def recorded(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(cli_manager, "_resolve", recorded)

    repo = Repo(tmp_path / "repo")
    repo.root.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.name", "Operator")
    repo.git("config", "user.email", "operator@example.invalid")
    repo.write("a.txt", "x\n")
    repo.commit("init")

    state = _escalated_state(repo.root, "T-9811")

    result = CliRunner().invoke(
        app,
        [
            *("manager", "resolve", PARTITION, "T-9811"),
            *("--resolution", "abandoned", "--root", str(repo.root)),
        ],
    )

    assert result.exit_code == 0, result.output
    assert state.path.exists()


def test_a_requeued_resolution_briefs_the_next_attempt_from_the_record(tmp_path, monkeypatch):
    """S-0113/D-5: `manager resolve --resolution requeued --note` puts the note
    in the task's feedback record as an operator thread, after the threads and
    before the diff the record already holds; a resolve with no note leaves the
    record as it was."""
    from typer.testing import CliRunner

    from torve.application.feedback import capture_feedback, feedback_file
    from torve.cli import manager as cli_manager
    from torve.cli.main import app
    from torve.gates.sabotage import Repo

    async def recorded(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(cli_manager, "_resolve", recorded)

    repo = Repo(tmp_path / "repo")
    repo.root.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.name", "Operator")
    repo.git("config", "user.email", "operator@example.invalid")
    repo.write("a.txt", "x\n")
    repo.commit("init")

    threads = [
        {
            "path": "src/a/app.py",
            "line": 4,
            "comments": [{"author": "bot", "body": "the guard is missing"}],
        }
    ]
    assert capture_feedback(repo.root, "T-9820", "diff --git a/a b/a\n+1\n", threads) is True

    result = CliRunner().invoke(
        app,
        [
            *("manager", "resolve", PARTITION, "T-9820"),
            *("--note", "the guard is a helper you can import", "--root", str(repo.root)),
        ],
    )

    assert result.exit_code == 0, result.output
    text = feedback_file(repo.root, "T-9820").read_text(encoding="utf-8")
    assert "the guard is missing" in text and "diff --git a/a b/a" in text
    assert (
        text.index("the guard is missing")
        < text.index("the guard is a helper you can import")
        < text.index("## The superseded candidate's diff")
    )

    before = feedback_file(repo.root, "T-9820").read_bytes()
    again = CliRunner().invoke(
        app, [*("manager", "resolve", PARTITION, "T-9820"), *("--root", str(repo.root))]
    )

    assert again.exit_code == 0, again.output
    assert feedback_file(repo.root, "T-9820").read_bytes() == before


def test_an_urgent_note_is_recorded_as_urgent(tmp_path):
    """`manager note --urgent` marks the note so the implement hook can end the
    attempt on it; a note without the flag records what it always did. Both
    are recorded through the log itself, whose payload model refuses a field
    it does not know."""
    import json

    from typer.testing import CliRunner

    from torve.cli.main import app
    from torve.domain.events import EventKind, validate_payload

    for flags in (["--urgent"], []):
        result = CliRunner().invoke(
            app,
            [
                *("manager", "note", PARTITION, "T-9821", "stop: the contract is wrong"),
                *flags,
                *("--root", str(tmp_path), "--format", "json"),
            ],
        )

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["urgent"] is bool(flags)

    note = {"to_role": "implement", "topic": "note", "body": "stop", "urgent": True}

    assert validate_payload(EventKind.MESSAGE_SENT, note).urgent is True  # type: ignore[attr-defined]


# ....................... #
# The lane leg's rounds (S-0096/D-2): the lane mints a red completion
# battery's round only where the review leg reads recorded findings, so the
# caller derives `rounds` from the threads configuration.


def test_the_landing_leg_refreshes_the_landings_the_pass_reads(tmp_path):
    """S-0098/D-2: a serve reads its landings once, and the lane lands onto
    document branches for hours after. The leg re-reads them, so a reclaim in
    the same pass as a landing records it instead of releasing the task."""
    import asyncio

    from torve.cli.manager import _refreshing

    # The leg re-reads the carrier the lane writes — the landing file, not
    # the stream (S-0099/D-1).

    landings = {"T-0001": "aaaa"}

    async def lane() -> list[str]:
        execution = tmp_path / ".torve" / "execution"
        execution.mkdir(parents=True, exist_ok=True)
        (execution / "T-0002-1-20260909T120000Z.yaml").write_text(
            "task: T-0002\nat: '2026-09-09T12:00:00Z'\ncommit: bbbb\n", encoding="utf-8"
        )
        return ["T-0002"]

    assert asyncio.run(_refreshing(lane, landings, tmp_path)()) == ["T-0002"]
    assert landings == {"T-0001": "aaaa", "T-0002": "bbbb"}


def test_the_lane_leg_mints_rounds_only_where_the_leg_reads_records(monkeypatch):
    """S-0096/D-2: `rounds` is true only for `threads.enabled` with `record`
    among the sources; every other configuration leaves a red battery to
    escalate the completing task at once and record no finding."""

    import pathlib

    from torve.application import lane as lane_module
    from torve.cli.manager import _lane_leg
    from torve.config.runconfig import PromotionConfig, RunnerConfig, ThreadsConfig

    seen: list[bool] = []

    def fake_process_lane(_root, _vcs, **kwargs):
        seen.append(kwargs["rounds"])
        return []

    monkeypatch.setattr(lane_module, "process_lane", fake_process_lane)

    document = {"landing": "pull_request", "unit": "document", "auto_merge": True}
    configs = [
        RunnerConfig(promotion=PromotionConfig(auto_merge=True)),
        RunnerConfig(
            promotion=PromotionConfig(**document),
            threads=ThreadsConfig(enabled=True),
        ),
        RunnerConfig(
            promotion=PromotionConfig(**document),
            threads=ThreadsConfig(enabled=True, sources=["forge"]),
        ),
        RunnerConfig(
            promotion=PromotionConfig(**document),
            threads=ThreadsConfig(sources=["record"]),
        ),
        RunnerConfig(
            promotion=PromotionConfig(**document),
            threads=ThreadsConfig(enabled=True, sources=["record"]),
        ),
    ]

    for config in configs:
        leg = _lane_leg(pathlib.Path("."), config, only=None)
        assert leg is not None
        asyncio.run(leg())

    assert seen == [False, False, False, False, True]


def test_the_lane_leg_hands_the_lane_the_recorded_holdouts(tmp_path, monkeypatch):
    """S-0116/D-14: `_lane_leg` folds the recorded held-out properties on the
    loop and hands `process_lane` the reader over them, so a document's
    completion commits what a verifier recorded on any host."""

    import torve.application.lane as lane_module
    from torve.cli.manager import _lane_leg
    from torve.config.runconfig import PromotionConfig, RunnerConfig
    from torve.domain.events import HoldoutFile

    seen: dict[str, object] = {}

    def fake_process_lane(_root, _vcs, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(lane_module, "process_lane", fake_process_lane)

    files = [HoldoutFile(name="test_p.py", text="# S-0116/D-14\n")]

    async def holdouts():
        return {"T-1": files}

    leg = _lane_leg(
        tmp_path,
        RunnerConfig(promotion=PromotionConfig(auto_merge=True)),
        only=None,
        holdouts=holdouts,
    )
    assert leg is not None

    asyncio.run(leg())

    reader = seen["holdout"]
    assert reader("T-1") == files
    assert reader("T-2") == []


def test_the_lane_leg_builds_the_sandbox_factory_from_the_configurations_runtime(
    tmp_path, monkeypatch
):
    """S-0114/D-3: the served lane's batteries run in a gates sandbox built
    from the configuration's own runtime — the executor seat's image over the
    judge checkout under the lane's prefix — exactly as `torve merge` builds
    them, so neither caller runs a candidate's shell gates on the host."""

    import subprocess

    from torve.application import lane as lane_module
    from torve.application.ports import ExecResult, SandboxHandle
    from torve.cli.manager import _lane_leg
    from torve.config.runconfig import PromotionConfig, RunnerConfig, TierConfig

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "e@x.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "E"], check=True)
    (repo / "app.py").write_text("base = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)

    seen: dict = {"specs": []}

    class RecordingRuntime:
        def create(self, spec, workspace):
            seen["specs"].append(spec)
            seen["workspace"] = workspace
            return SandboxHandle(id="h-1", name=spec.name)

        def exec(self, handle, command, timeout_s):
            seen["command"] = command
            return ExecResult(exit_code=0, output="", duration_s=0.0)

        def sync_out(self, handle, workspace):
            pass

        def destroy(self, handle):
            seen["destroyed"] = handle.id

        def resolve_image(self, image):
            return None

    monkeypatch.setattr("torve.cli.options.runtime_for", lambda *a, **k: RecordingRuntime())

    captured: dict = {}

    def fake_process_lane(_root, _vcs, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(lane_module, "process_lane", fake_process_lane)

    config = RunnerConfig(
        promotion=PromotionConfig(auto_merge=True),
        tiers={"executor": TierConfig(image="registry.example/torve-executor:3")},
    )

    leg = _lane_leg(repo, config, only=None)
    assert leg is not None
    asyncio.run(leg())

    # The leg handed the lane a factory; the batteries it opens are sandboxes
    # over a judge checkout of the run, under the seat's own image.
    gates = captured["gates"]
    assert callable(gates)

    handle = gates(repo, "lane-T-1.judge")
    assert handle.root == repo / ".wt" / "lane-T-1.judge"
    assert handle.root.is_dir()
    # A battery with no shell gate costs no container: the sandbox opens on
    # the first one.
    assert seen["specs"] == []

    handle.execute("true", 10.0)
    assert seen["command"] == "true"
    assert seen["workspace"] == handle.root
    assert [s.image for s in seen["specs"]] == ["registry.example/torve-executor:3"]
    assert seen["specs"][0].name == "torve-lane-T-1.judge"
    assert "lane-T-1.judge" in "\n".join(seen["specs"][0].labels.values())

    # The checkout goes with the battery and the sandbox with it (S-0114/D-5).
    handle.close()
    assert seen["destroyed"] == "h-1"
    assert not handle.root.exists()


def test_the_lane_leg_hands_every_disposal_on_disk_to_the_callback_even_when_it_raises(
    tmp_path, monkeypatch
):
    """S-0109/D-1: after every lane pass, raised or not, `_lane_leg` hands the
    `requeue` callback each run the conflict disposal left `queued` with its
    base tip, read from the run-state files, so a disposal recorded before a
    later candidate raised is not lost."""

    from torve.application import lane as lane_module
    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.cli.manager import _lane_leg
    from torve.config.runconfig import PromotionConfig, RunnerConfig

    disposed = RunState(task_id="T-1", path=naming.state_file(tmp_path, "T-1"))
    disposed.state = TaskState.QUEUED
    disposed.conflict_base = "base123"
    disposed.save()
    ready = RunState(task_id="T-2", path=naming.state_file(tmp_path, "T-2"))
    ready.state = TaskState.READY
    ready.save()

    def raising_process_lane(_root, _vcs, **kwargs):
        raise RuntimeError("a later candidate's rebase blew up")

    monkeypatch.setattr(lane_module, "process_lane", raising_process_lane)
    config = RunnerConfig(promotion=PromotionConfig(auto_merge=True))
    seen: list[list[tuple[str, str]]] = []

    async def requeue(candidates: list[tuple[str, str]]) -> None:
        seen.append(candidates)

    leg = _lane_leg(tmp_path, config, only=None, requeue=requeue)
    assert leg is not None

    with pytest.raises(RuntimeError):
        asyncio.run(leg())

    assert seen == [[("T-1", "base123")]]


def test_a_requeue_is_owed_only_for_an_unlanded_ready_candidate():
    """S-0109/D-1: the log is told once — a task the board still reads as
    `ready` and that has not landed. A disposal whose work landed later, and a
    task the board already reads as queued, owe nothing."""

    from torve.cli.manager import _requeues_owed

    board = Board(
        tasks={
            "T-1": TaskView(task_id="T-1", state=TaskState.READY),
            "T-2": TaskView(task_id="T-2", state=TaskState.READY, landed_sha="abc"),
            "T-3": TaskView(task_id="T-3", state=TaskState.QUEUED),
        }
    )
    candidates = [("T-1", "b1"), ("T-2", "b2"), ("T-3", "b3"), ("T-4", "b4")]

    assert _requeues_owed(candidates, board, landed={"T-2"}) == [("T-1", "b1")]


def test_a_serve_reads_the_boards_resolutions_into_the_lane(tmp_path, monkeypatch):
    """S-0101/D-2: the pass's `resolutions` closure reads the board the caller
    already holds and hands the lane the fact, so a round a person abandoned on
    any host releases the wave. The skeleton of the round lives on the host that
    minted it; the resolution rides the log."""

    from forze_mock import MockDepsModule, MockState

    from torve.adapters.eventstore import document as eventstore
    from torve.application import lane as lane_module
    from torve.cli import manager as manager_cli
    from torve.config.runconfig import PromotionConfig, RunnerConfig

    state = MockState()
    monkeypatch.setattr(eventstore, "mock_module", lambda: MockDepsModule(state=state))

    async def seed() -> None:
        runtime = ExecutionRuntime(
            deps=DepsRegistry.from_modules(MockDepsModule(state=state)).freeze()
        )

        async with runtime.scope():
            await event_log(runtime.get_context()).record(
                EventKind.ESCALATION_RESOLVED,
                partition=PARTITION,
                subject_type=SubjectType.TASK,
                subject_id="T-7543",
                actor_kind=ActorKind.OPERATOR,
                actor_id="op-1",
                payload={"resolution": "abandoned"},
            )

    asyncio.run(seed())

    seen: dict[str, object] = {}

    def fake_process_lane(_root, _vcs, **kwargs):
        seen["resolved"] = kwargs["resolved"]
        return []

    monkeypatch.setattr(lane_module, "process_lane", fake_process_lane)
    terms = RunnerConfig(
        promotion=PromotionConfig(landing="pull_request", unit="document", auto_merge=True)
    )
    monkeypatch.setattr(manager_cli, "load_config", lambda root, path: terms)

    asyncio.run(
        manager_cli._serve(
            None,
            PARTITION,
            root=tmp_path,
            config_path=None,
            worker="w-1",
            passes=1,
            interval=0,
            only=None,
            dispatch=False,
        )
    )

    assert seen["resolved"] == {"T-7543"}


def test_a_serve_records_the_requeued_conflict_in_the_log(tmp_path, monkeypatch):
    """S-0109/D-1: the callback `_serve` hands the lane leg records
    `task.requeued` for the task the disposal re-queued, with the manager as
    the actor and the reason and base tip in the payload, so the fact reaches
    the log the next pass dispatches from and not only the task's run-state
    file. The manager writes it, never the operator's `task.returned`
    (S-0109/D-2), and writes it once: a second pass finds the board already
    reading the task as queued."""

    from forze_mock import MockDepsModule, MockState

    from torve.adapters.eventstore import document as eventstore
    from torve.application import lane as lane_module
    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.cli import manager as manager_cli
    from torve.config.runconfig import PromotionConfig, RunnerConfig

    state = MockState()
    monkeypatch.setattr(eventstore, "mock_module", lambda: MockDepsModule(state=state))

    async def seed() -> None:
        runtime = ExecutionRuntime(
            deps=DepsRegistry.from_modules(MockDepsModule(state=state)).freeze()
        )

        async with runtime.scope():
            log = event_log(runtime.get_context())
            await mint(log, "T-1")
            await land(log, "T-1")

    asyncio.run(seed())
    # What the disposal leaves on disk: the run re-queued against the base it
    # collided with, while the log still reads the candidate as ready.
    disposed = RunState(task_id="T-1", path=naming.state_file(tmp_path, "T-1"))
    disposed.state = TaskState.QUEUED
    disposed.conflict_base = "base123"
    disposed.save()

    monkeypatch.setattr(lane_module, "process_lane", lambda _root, _vcs, **kwargs: [])
    terms = RunnerConfig(promotion=PromotionConfig(auto_merge=True))
    monkeypatch.setattr(manager_cli, "load_config", lambda root, path: terms)

    asyncio.run(
        manager_cli._serve(
            None,
            PARTITION,
            root=tmp_path,
            config_path=None,
            worker="w-1",
            passes=2,
            interval=0,
            only=None,
            dispatch=False,
        )
    )

    async def read() -> list:
        runtime = ExecutionRuntime(
            deps=DepsRegistry.from_modules(MockDepsModule(state=state)).freeze()
        )

        async with runtime.scope():
            events = await event_log(runtime.get_context()).since(partition=PARTITION)

        return [event for event in events if event.kind is EventKind.TASK_REQUEUED]

    requeued = asyncio.run(read())

    assert len(requeued) == 1
    assert requeued[0].subject_id == "T-1"
    assert requeued[0].actor_kind is ActorKind.MANAGER
    assert requeued[0].payload == {"reason": "merge_conflict", "base_tip": "base123"}


@pytest.mark.parametrize("finished_by_hand", [False, True])
def test_a_serve_records_the_lanes_escalation_in_the_log(tmp_path, monkeypatch, finished_by_hand):
    """T-0657, 2026-10-19: the lane escalated a candidate red again at the same
    base and head in its run-state file alone. The board kept it `ready`, the
    phase behind it never dispatched, and the night idled for hours with
    nothing on the board. The served lane now records the escalation once,
    with the manager as the actor, so the board reads the task as a person's.

    A task a person finished by hand (`resolve --resolution landed`) reads
    `ready` with its escalated file still on disk, and is never escalated
    again."""

    from forze_mock import MockDepsModule, MockState

    from torve.adapters.eventstore import document as eventstore
    from torve.application import lane as lane_module
    from torve.application.runstate import Escalation, RunState
    from torve.base import naming
    from torve.cli import manager as manager_cli
    from torve.config.runconfig import PromotionConfig, RunnerConfig

    state = MockState()
    monkeypatch.setattr(eventstore, "mock_module", lambda: MockDepsModule(state=state))

    def runtime() -> ExecutionRuntime:
        return ExecutionRuntime(
            deps=DepsRegistry.from_modules(MockDepsModule(state=state)).freeze()
        )

    async def seed() -> None:
        held = runtime()

        async with held.scope():
            log = event_log(held.get_context())
            await mint(log, "T-1")
            await land(log, "T-1")

            if finished_by_hand:
                for kind, actor, payload in (
                    (
                        EventKind.ESCALATION_RAISED,
                        ActorKind.WORKER,
                        {"reason": "poison_ceiling", "detail": "red"},
                    ),
                    (
                        EventKind.ESCALATION_RESOLVED,
                        ActorKind.OPERATOR,
                        {"resolution": "landed", "sha": "abc"},
                    ),
                ):
                    await log.record(
                        kind,
                        partition=PARTITION,
                        subject_type=SubjectType.TASK,
                        subject_id="T-1",
                        actor_kind=actor,
                        actor_id="someone",
                        payload=payload,
                    )

    asyncio.run(seed())
    stuck = RunState(task_id="T-1", path=naming.state_file(tmp_path, "T-1"))
    stuck.state = TaskState.ESCALATED
    stuck.escalation = Escalation(
        reason="poison_ceiling", detail="red again at the same base and head: acceptance=fail"
    )
    stuck.save()

    monkeypatch.setattr(lane_module, "process_lane", lambda _root, _vcs, **kwargs: [])
    terms = RunnerConfig(promotion=PromotionConfig(auto_merge=True))
    monkeypatch.setattr(manager_cli, "load_config", lambda root, path: terms)

    asyncio.run(
        manager_cli._serve(
            None,
            PARTITION,
            root=tmp_path,
            config_path=None,
            worker="w-1",
            passes=2,
            interval=0,
            only=None,
            dispatch=False,
        )
    )

    async def read() -> list:
        held = runtime()

        async with held.scope():
            return await event_log(held.get_context()).since(partition=PARTITION)

    events = asyncio.run(read())
    raised = [
        event
        for event in events
        if event.kind is EventKind.ESCALATION_RAISED and event.actor_kind is ActorKind.MANAGER
    ]

    if finished_by_hand:
        assert raised == []
        assert project(events).tasks["T-1"].state is TaskState.READY

        return

    assert len(raised) == 1
    assert raised[0].subject_id == "T-1"
    assert raised[0].payload == {
        "reason": "poison_ceiling",
        "detail": "red again at the same base and head: acceptance=fail",
    }
    assert project(events).tasks["T-1"].state is TaskState.ESCALATED


# ....................... #
# The night waits for its document's review (S-0097/D-4).


def test_a_night_owes_a_document_pull_request_s_review_wait(tmp_path, monkeypatch):
    """A published document pull request whose head the bots have not finished
    reviewing is something the night owes: the drain check `_serve` asks is
    handed the wait, so the leg sees the wave the night produced instead of a
    drained night leaving it to a person."""

    import yaml

    from torve.application import residency, reviewleg
    from torve.cli import manager as manager_cli
    from torve.cli.manager import _serve
    from torve.config.runconfig import PromotionConfig, RunnerConfig, ThreadsConfig

    contract = tmp_path / ".torve" / "tasks" / "T-1" / "contract.yaml"
    contract.parent.mkdir(parents=True)
    contract.write_text(
        yaml.safe_dump({"id": "T-1", "decisions": [], "scope": {"allow": ["src/**"], "deny": []}}),
        encoding="utf-8",
    )

    terms = RunnerConfig(
        promotion=PromotionConfig(landing="pull_request", unit="document"),
        threads=ThreadsConfig(enabled=True, bots=["coderabbitai"]),
    )
    monkeypatch.setattr(manager_cli, "load_config", lambda root, path: terms)
    # The wait is read off the forge, which a unit test does not have; what is
    # asserted here is that the night's own drain check carries it.
    monkeypatch.setattr(reviewleg, "review_wait_owing", lambda *_args, **_kwargs: True)

    seen: dict[str, object] = {}

    class Stopped(Exception):
        pass

    async def spy(log, partition, night, *, owed=None, **kwargs):
        seen["owed"] = owed
        raise Stopped

    monkeypatch.setattr(residency, "reached", spy)

    with pytest.raises(Stopped):
        asyncio.run(
            _serve(
                None,
                PARTITION,
                root=tmp_path,
                config_path=None,
                worker="w-1",
                passes=1,
                interval=0,
                only=None,
                dispatch=False,
                night=True,
            )
        )

    owed = seen["owed"]

    assert callable(owed) and owed() is True


def test_the_served_thread_leg_reads_the_board_s_review_records(tmp_path, monkeypatch):
    """S-0101/D-4 in production: the manager pass hands the review-thread leg
    the board it reads, so the task-gated findings are raised from
    `review.recorded` rather than this host's stream."""

    import torve.application.reviewleg as reviewleg
    from torve.cli.manager import _thread_leg
    from torve.config.runconfig import RunnerConfig

    seen: list[object] = []

    def leg(root, config, forge, landed, records=()):
        seen.append(list(records))
        return "ok", False

    monkeypatch.setattr(reviewleg, "review_thread_leg", leg)
    config = RunnerConfig.model_validate(
        {
            "promotion": {"landing": "pull_request", "unit": "document"},
            "scm": {"repo": "owner/name", "open_pr": True},
            "store": {"adapter": "postgres"},
            "threads": {"enabled": True},
        }
    )
    sentinel = object()

    async def records():
        return [sentinel]

    threads = _thread_leg(tmp_path, config, records=records)
    assert threads is not None

    assert asyncio.run(threads()) == ("ok", False)
    assert seen == [[sentinel]]


def test_the_served_lane_leg_hands_the_lane_the_completion_forge(tmp_path, monkeypatch):
    """S-0099/D-4: the served lane writes the completion verdict as `torve
    merge` does. Without the status forge a served night left every completed
    document's pull request a draft."""

    import torve.application.lane as lane_module
    import torve.cli.merge as merge_module
    from torve.cli.manager import _lane_leg
    from torve.config.runconfig import RunnerConfig

    seen: dict[str, object] = {}
    sentinel = object()

    def fake_process_lane(root, vcs, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(lane_module, "process_lane", fake_process_lane)
    monkeypatch.setattr(merge_module, "_status", lambda config: sentinel)
    monkeypatch.setattr(
        merge_module, "_publisher", lambda root, config, status: seen.update(publisher=status)
    )
    monkeypatch.setattr(merge_module, "_forge", lambda config: None)
    monkeypatch.setattr(merge_module, "_resolve_ci", lambda config: None)
    config = RunnerConfig.model_validate(
        {
            "promotion": {"landing": "pull_request", "unit": "document", "auto_merge": True},
            "scm": {"repo": "owner/name", "open_pr": True},
            "store": {"adapter": "postgres"},
        }
    )

    leg = _lane_leg(tmp_path, config, only=None)
    assert leg is not None

    asyncio.run(leg())

    assert seen["status"] is sentinel
    # The draft flag reads the verdict through the forge the lane writes it to.
    assert seen["publisher"] is sentinel


# ....................... #
# `--until-change` (S-0110/D-13): an operator waits on a night without a
# script of its own. The verb reads the board in a loop and exits when a
# task moves or the night closes; it writes nothing and runs no leg.


def _watch_cli(tmp_path, monkeypatch, boards, nights, interval="1", dsn="postgresql://log/db"):
    """Invoke `manager board --until-change` over a scripted sequence of
    reads — each poll takes the next board and night, holding the last once
    the script runs out, so the loop always has an answer to exit on. The
    wait between reads is skipped, and the store runtime is a stand-in."""

    import itertools
    from contextlib import asynccontextmanager

    from typer.testing import CliRunner

    from torve.cli import manager as manager_cli
    from torve.cli.main import app

    polls = itertools.count()

    @asynccontextmanager
    async def fake_runtime(_dsn):
        yield object()

    async def fake(_runtime, _partition, _night_id=""):
        index = min(next(polls), len(boards) - 1)
        return boards[index], nights[index]

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(manager_cli, "_runtime", fake_runtime)
    monkeypatch.setattr(manager_cli, "_read_board", fake)
    monkeypatch.setattr(manager_cli, "dsn_for", lambda root, _typed: dsn)
    monkeypatch.setattr(manager_cli.asyncio, "sleep", no_wait)

    return CliRunner().invoke(
        app,
        [
            "manager",
            "board",
            PARTITION,
            "--until-change",
            "--interval",
            interval,
            "--root",
            str(tmp_path),
        ],
    )


def test_the_board_exits_when_a_task_moves(tmp_path, monkeypatch):
    """S-0110/D-13: the wait ends on the first row whose state, attempts or
    escalation changed, and prints that row."""

    boards = [
        Board(tasks={"T-1": TaskView(task_id="T-1", state=TaskState.QUEUED)}),
        Board(tasks={"T-1": TaskView(task_id="T-1", state=TaskState.RUNNING, attempts=1)}),
    ]

    result = _watch_cli(tmp_path, monkeypatch, boards, [None, None])

    assert result.exit_code == 0, result.output
    assert "T-1" in result.output
    assert "running" in result.output


def test_the_watch_refuses_an_interval_under_a_second(tmp_path, monkeypatch):
    """A zero or fractional interval would hammer the store with reads; the
    floor is one second, refused before the first read."""

    result = _watch_cli(tmp_path, monkeypatch, [Board()], [None], interval="0.5")

    assert result.exit_code == 2
    assert "--interval" in result.output


def test_the_watch_refuses_a_log_no_dsn_resolves(tmp_path, monkeypatch):
    """With no DSN every read stands up a fresh mock log that never changes,
    so the watch is refused as a configuration error before its first read."""

    result = _watch_cli(tmp_path, monkeypatch, [Board()], [None], dsn="")

    assert result.exit_code == EXIT_CONFIG
    assert "--until-change" in result.output and "DSN" in result.output


def test_the_board_exits_when_the_night_closes(tmp_path, monkeypatch):
    """S-0110/D-13: an unchanged board still ends the wait when the watched
    night closes — that is the operator's other answer."""

    opened = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)
    open_events = [night_event(EventKind.NIGHT_OPENED, NIGHT, TERMS, at=opened)]
    closed_events = [
        *open_events,
        night_event(
            EventKind.NIGHT_CLOSED,
            NIGHT,
            {"reason": "drained", "handled": 1},
            at=opened + timedelta(hours=8),
        ),
    ]

    result = _watch_cli(
        tmp_path,
        monkeypatch,
        [Board(), Board()],
        [night_report(open_events), night_report(closed_events)],
    )

    assert result.exit_code == 0, result.output
    assert NIGHT in result.output
    assert "closed" in result.output


def test_the_watch_follows_its_own_night_past_a_newer_one(monkeypatch):
    """S-0110/D-13: the watch pins the night open when it began. Night A
    closing and night B opening between two reads still ends the wait on A's
    close; following "the latest night" would see only B, open, and wait on."""

    from contextlib import asynccontextmanager

    from torve.cli import manager as manager_cli
    from torve.cli.manager import _watch

    opened = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)
    a_open = night_event(EventKind.NIGHT_OPENED, "N-A", TERMS, at=opened)
    a_closed = night_event(
        EventKind.NIGHT_CLOSED,
        "N-A",
        {"reason": "drained", "handled": 1},
        at=opened + timedelta(hours=1),
    )
    b_open = night_event(EventKind.NIGHT_OPENED, "N-B", TERMS, at=opened + timedelta(hours=2))
    logs = [[a_open], [a_open, a_closed, b_open]]
    asked: list[str] = []

    @asynccontextmanager
    async def fake_runtime(_dsn):
        yield object()

    async def read(_runtime, _partition, night_id=""):
        asked.append(night_id)
        assert len(asked) <= len(logs), "the watch missed the close of the night it began on"
        return Board(), night_report(logs[len(asked) - 1], night_id=night_id)

    monkeypatch.setattr(manager_cli, "_runtime", fake_runtime)
    monkeypatch.setattr(manager_cli, "_read_board", read)

    moved, night = asyncio.run(_watch("postgresql://log/db", PARTITION, interval=0, document=None))

    assert moved == []
    assert night is not None and night.night_id == "N-A" and night.close is not None
    assert asked == ["", "N-A"]


def test_the_watch_reads_every_interval_through_one_runtime(monkeypatch):
    """S-0113/D-4: `--until-change` opens the store runtime once and reads
    through it on every interval — a watcher waits on one connection pool,
    however many reads it makes, rather than standing up a new one each
    time."""

    from contextlib import asynccontextmanager

    from torve.cli import manager as manager_cli

    opened: list[str] = []
    reads: list[object] = []

    @asynccontextmanager
    async def fake_runtime(dsn):
        opened.append(dsn)
        yield "the-runtime"

    boards = [
        Board(tasks={"T-1": TaskView(task_id="T-1", state=TaskState.QUEUED)}),
        Board(tasks={"T-1": TaskView(task_id="T-1", state=TaskState.RUNNING, attempts=1)}),
    ]

    async def fake(_runtime, _partition, _night_id=""):
        reads.append(_runtime)
        return boards[min(len(reads) - 1, len(boards) - 1)], None

    monkeypatch.setattr(manager_cli, "_runtime", fake_runtime)
    monkeypatch.setattr(manager_cli, "_read_board", fake)

    moved, night = asyncio.run(
        manager_cli._watch("postgresql://log/db", PARTITION, interval=0, document=None)
    )

    assert opened == ["postgresql://log/db"]
    assert reads == ["the-runtime", "the-runtime"]
    assert [view.task_id for view in moved] == ["T-1"]
    assert night is None


def test_a_runtime_closes_its_client_when_the_scope_ends(monkeypatch):
    """S-0113/D-4: the Postgres client a CLI runtime opened is closed when
    the runtime's scope ends, so a verb does not leak a connection pool. The
    client is the one the deps module holds; a mock module holds none and is
    closed without complaint."""

    from forze_mock import MockDepsModule

    from torve.adapters.eventstore import document
    from torve.cli import manager as manager_cli

    closed: list[bool] = []

    class Client:
        async def close(self) -> None:
            closed.append(True)

    class Module:
        def __init__(self) -> None:
            self.client = Client()
            self._inner = MockDepsModule()

        def __call__(self):
            return self._inner()

    async def fake_postgres(_dsn, **_kwargs):
        return Module()

    monkeypatch.setattr(document, "postgres_module", fake_postgres)

    async def use() -> None:
        async with manager_cli._runtime("postgresql://log/db"):
            # The client is held for the life of the scope, not closed at open.
            assert closed == []

        # S-0113/D-4: the scope ended, so the client it opened is closed.
        assert closed == [True]

        # A mock module holds no client, and closing it is a no-op.
        async with manager_cli._runtime(None):
            pass

        assert closed == [True]

    asyncio.run(use())

    assert closed == [True]


def test_a_watch_of_one_document_ignores_anothers_tasks():
    """S-0110/D-13's `--document`: only the named document's rows move the
    watcher, and another document's task running beside it does not."""

    from torve.cli.manager import _moved, _of_document
    from torve.domain.task import Scope

    def contract(task_id: str, spec: str) -> Task:
        return Task(id=task_id, spec=spec, decisions=[], scope=Scope(allow=["src/**"], deny=[]))

    before = Board(
        tasks={
            "T-1": TaskView(
                task_id="T-1", state=TaskState.QUEUED, contract=contract("T-1", "S-0110")
            ),
            "T-2": TaskView(
                task_id="T-2", state=TaskState.QUEUED, contract=contract("T-2", "S-0111")
            ),
        }
    )
    now = Board(
        tasks={
            "T-1": TaskView(
                task_id="T-1", state=TaskState.QUEUED, contract=contract("T-1", "S-0110")
            ),
            "T-2": TaskView(
                task_id="T-2",
                state=TaskState.RUNNING,
                attempts=1,
                contract=contract("T-2", "S-0111"),
            ),
        }
    )

    mine = _of_document(before, "S-0110"), _of_document(now, "S-0110")
    theirs = _of_document(before, "S-0111"), _of_document(now, "S-0111")

    assert _moved(*mine) == []
    assert [view.task_id for view in _moved(*theirs)] == ["T-2"]


def test_a_return_waits_for_a_running_lane_pass(tmp_path, monkeypatch):
    """A pass that read a candidate ready ends before its return lands in the
    log or the run-state file, so the pass never lands what was sent back."""
    import threading

    from typer.testing import CliRunner

    from torve.application.lane import lane_held
    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.cli import manager as cli_manager
    from torve.cli.main import app
    from torve.domain.states import TaskState

    recorded: list[str] = []

    async def record(*args, **_kwargs) -> None:
        recorded.append(args[2])

    monkeypatch.setattr(cli_manager, "_return", record)

    state = RunState(task_id="T-9812", path=naming.state_file(tmp_path, "T-9812"))
    state.state = TaskState.READY
    state.save()

    def ret() -> None:
        CliRunner().invoke(
            app, [*("manager", "return", PARTITION, "T-9812"), *("--root", str(tmp_path))]
        )

    with lane_held(tmp_path):
        returning = threading.Thread(target=ret)
        returning.start()
        returning.join(0.3)

        assert recorded == []
        assert RunState.load(state.path).state is TaskState.READY

    returning.join(10)

    assert recorded == ["T-9812"]
    assert RunState.load(state.path).state is TaskState.QUEUED


def test_a_return_whose_log_write_fails_leaves_nothing_to_land(tmp_path, monkeypatch):
    """The run-state file moves before the log: a return that stops between
    the two leaves the candidate queued, and running it again records it."""
    from typer.testing import CliRunner

    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.cli import manager as cli_manager
    from torve.cli.main import app
    from torve.domain.states import TaskState

    async def unreachable(*_args, **_kwargs) -> None:
        raise ConnectionError("the log is unreachable")

    monkeypatch.setattr(cli_manager, "_return", unreachable)

    state = RunState(task_id="T-9813", path=naming.state_file(tmp_path, "T-9813"))
    state.state = TaskState.READY
    state.save()

    args = [*("manager", "return", PARTITION, "T-9813"), *("--root", str(tmp_path))]
    result = CliRunner().invoke(app, args)

    assert result.exit_code != 0
    assert RunState.load(state.path).state is TaskState.QUEUED

    recorded: list[str] = []

    async def record(*args, **_kwargs) -> None:
        recorded.append(args[2])

    monkeypatch.setattr(cli_manager, "_return", record)

    assert CliRunner().invoke(app, args).exit_code == 0
    assert recorded == ["T-9813"]
    assert RunState.load(state.path).state is TaskState.QUEUED
