"""One state per run (RFC 0044 phase 5, A-85).

A run's state is held in two places: the aggregate the attempt loop drives,
and the board the manager folds out of the record. Both are projections of
the same events, and nothing until now said they agree — so this is the
test that says it, over a real run of the loop with its ports mocked.

What is compared is what a manager acts on: the state a task ended in, how
many attempts it took, the landing it produced, and the escalation it
raised. If those four can differ, the board is not the run's state and
every dispatch decision made from it is a guess.
"""

from __future__ import annotations

import asyncio

import pytest
from forze.application.execution import DepsRegistry, ExecutionRuntime
from test_run_loop import (
    OK,
    MockRuntime,
    MockScm,
    MockVcs,
    MockWorkspace,
    ScriptedAgent,
    task_for,
)

import torve.application.runner as run_module
from torve.adapters.eventstore.document import mock_module
from torve.adapters.store.durable import open_store
from torve.application.dispatch import RunDeps
from torve.application.eventlog import event_log
from torve.application.executors import runner_execute
from torve.application.manager import project
from torve.application.residency import mint
from torve.application.worker import Worker
from torve.config.runconfig import RunnerConfig
from torve.domain.states import TaskState

PARTITION = "morzecrew/torve"


@pytest.fixture
def rig(repo, monkeypatch):
    """The run loop over mock ports, with the gate pass scripted — the same
    rig `test_run_loop` uses, wired so the manager drives it."""

    repo.seed()
    deps = RunDeps(
        workspace=MockWorkspace(repo.root),
        runtime=MockRuntime(),
        agent=ScriptedAgent([OK]),
        vcs=MockVcs(),
        scm=MockScm(),
        store=open_store,
    )
    verdicts: list[int] = []

    def scripted_gates(*args, **kwargs):
        code = verdicts.pop(0) if verdicts else 0
        return code, "scripted", "cafecafe1234", [], ""

    monkeypatch.setattr(run_module, "_run_gates_in_worktree", scripted_gates)

    return repo, deps, verdicts


def drive(repo, deps, *, config: RunnerConfig | None = None):
    """One task through the worker, and both views of it afterwards."""

    task = task_for(repo)
    holder: dict[str, object] = {}

    async def main():
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            log = event_log(runtime.get_context())
            await mint(log, {task.id: task}, partition=PARTITION, actor_id="manager-1")

            worker = Worker(
                log=log,
                name="w-1",
                execute=runner_execute(
                    repo.root,
                    config or RunnerConfig(),
                    lambda one: (one, deps),
                    log=log,
                    partition=PARTITION,
                    seat="w-1",
                ),
            )
            await worker.once({task.id: task}, PARTITION)

            holder["board"] = project(await log.since(partition=PARTITION)).tasks[task.id]

        return holder["board"]

    view = asyncio.run(main())
    state = run_module.RunState.load(repo.root / ".wt" / f"{task.id}.state.json")

    return view, state


# ....................... #


def test_a_landed_run_reads_the_same_from_both(rig):
    repo, deps, _ = rig
    view, state = drive(repo, deps)

    assert view.state is state.state is TaskState.READY
    assert view.attempts == state.attempts == 1
    assert view.landed_sha == state.landed_sha
    assert view.escalation is None and state.escalation is None


def test_a_convicted_run_reads_the_same_from_both(rig):
    repo, deps, verdicts = rig
    verdicts.extend([1, 1, 1])  # red every time, into the ceiling
    view, state = drive(repo, deps)

    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None
    # The board carries the reason a human has to act on, from the record
    # rather than from the state file the reaper deletes.
    assert view.escalation == state.escalation.reason
    assert view.attempts == state.attempts
    assert view.landed_sha is None and state.landed_sha is None


def test_the_attempt_count_survives_a_retry(rig):
    repo, deps, verdicts = rig
    verdicts.append(1)  # one red, then green
    view, state = drive(repo, deps)

    assert state.attempts == 2
    # The board counts attempts from the attempts themselves (D-44.3), so a
    # run that took two tries cannot read as one.
    assert view.attempts == state.attempts
    assert view.state is state.state is TaskState.READY
