"""The manager over the real store, end to end (RFC 0044 §5.4, §5.5).

Everything else about the manager is asserted over the mock document
adapter, which is the same port and therefore the same code path — but the
same port is a claim, and this is where it is checked: one task, minted,
claimed, executed in a real sandbox by v1's runner, landed, and read back
out of Postgres as a board.

Skipped without a docker daemon or a `TORVE_PG_DSN` naming a reachable,
migrated database. Each run takes a partition of its own, so a lab store
accumulates runs rather than having them collide.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from forze.application.execution import DepsRegistry, ExecutionRuntime
from test_run_integration import CONFIG, deps_for, seed_run_repo
from test_runtime_conformance import docker_available

from torve.adapters.agent.fake import FakeAgent
from torve.adapters.eventstore.document import postgres_module
from torve.application.eventlog import event_log
from torve.application.executors import runner_execute
from torve.application.manager import project
from torve.application.residency import serve
from torve.application.worker import Worker
from torve.domain.events import EventKind, gate_outcomes
from torve.domain.states import TaskState
from torve.gates.sabotage import TASK_ID

DSN = os.environ.get("TORVE_PG_DSN", "")

pytestmark = [
    pytest.mark.skipif(not docker_available(), reason="docker daemon not available"),
    pytest.mark.skipif(not DSN, reason="TORVE_PG_DSN not set"),
]


def test_two_attempts_from_mint_to_landing_over_postgres(repo):
    seed_run_repo(repo)
    partition = f"lab/e2e-{uuid.uuid4().hex[:8]}"
    # Two attempts on purpose: the first writes the wrong file and the
    # acceptance gate convicts it, the second writes the right one. One
    # dispatch, two attempts, and the log has to say so — a summary written
    # once per dispatch is the defect this asserts against (D-44.3).
    agent = FakeAgent(
        [
            {"writes": {"src/other.py": "WRONG = True\n"}, "exit": 0},
            {"writes": {"src/feature.py": "FEATURE = True\n"}, "exit": 0},
        ]
    )
    deps = deps_for(repo, agent)

    async def main():
        module = await postgres_module(DSN)
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(module).freeze())

        async with runtime.scope():
            log = event_log(runtime.get_context())
            worker = Worker(
                log=log,
                name="e2e-worker",
                # The manager's own seam, with the tier resolution stubbed:
                # what is under test is the loop over a real store, not the
                # composition root's agent construction.
                execute=runner_execute(
                    repo.root,
                    CONFIG,
                    lambda task: (task, deps),
                    log=log,
                    partition=partition,
                    seat="e2e-worker",
                ),
            )

            handled = await serve(log, worker, repo.root, partition, passes=1, idle_seconds=0)
            events = await log.since(partition=partition)

            return handled, events

    handled, events = asyncio.run(main())

    assert handled == 1
    assert (repo.root / ".wt" / TASK_ID / "src" / "feature.py").read_text() == "FEATURE = True\n"

    kinds = [event.kind for event in events]
    assert kinds == [
        EventKind.TASK_MINTED,
        EventKind.TASK_CLAIMED,
        EventKind.ATTEMPT_STARTED,
        EventKind.ATTEMPT_FINISHED,
        EventKind.GATES_EVALUATED,
        EventKind.ATTEMPT_STARTED,
        EventKind.ATTEMPT_FINISHED,
        EventKind.GATES_EVALUATED,
        EventKind.LANDING_RECORDED,
    ]

    gates = [event for event in events if event.kind is EventKind.GATES_EVALUATED]

    # Each attempt is numbered, and the verdicts differ — which is the whole
    # point: "this dispatch took two tries and here is what changed" is only
    # answerable if both are on record.
    assert [event.payload["attempt"] for event in gates] == [1, 2]
    assert [event.payload["exit_code"] != 0 for event in gates] == [True, False]
    assert gate_outcomes(gates[0].payload)["acceptance"] == "fail"
    assert gate_outcomes(gates[1].payload)["acceptance"] == "pass"
    # The event carries the attempt record itself, not a summary of it: the
    # regime the pass ran under is on the row a projection would read.
    assert gates[1].payload["config_hash"]
    assert gates[1].payload["agent"]["adapter"]

    # The board is a fold over what Postgres returned — the same projection
    # a restarted manager would build, from rows it did not write.
    board = project(events)
    assert board.tasks[TASK_ID].state is TaskState.READY
    # The full sha, not the abbreviation the history prints for a human.
    assert len(board.tasks[TASK_ID].landed_sha or "") == 40
