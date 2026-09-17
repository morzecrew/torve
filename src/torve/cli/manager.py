"""`torve manager` — the board a partition's event log adds up to (S-0044
§5.4). Parsing and rendering only (S-0015/D-6); the fold is
`torve.application.manager`. `serve` rebuilds its whole view from the log
every pass (S-0044/D-5); `note` writes the manager's half of the live channel,
which the run polls for rather than being interrupted by (S-0045/notes-and-stopping-on-evidence,
S-0045/D-7).

References: S-0044/A-13.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from torve.application.manager import Board, TaskView, stalled
from torve.application.residency import IDLE_SECONDS, NightRefused
from torve.cli.console import (
    STYLE_DIM,
    Format,
    add_rows_truncated,
    closing,
    emit_json,
    fail,
    footer,
    header,
    make_table,
    out,
)
from torve.cli.options import (
    ConfigOption,
    FormatOption,
    RootOption,
    dsn_for,
    dsn_to_write,
    load_config,
    runtime_for,
)
from torve.domain.states import EXIT_CONFIG, EXIT_OK

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from forze.application.execution import ExecutionRuntime

    from torve.application.residency import Lane
    from torve.config.runconfig import RunnerConfig

# ----------------------- #

manager_app = typer.Typer(no_args_is_help=True, help="Run and read the engine's board.")


# ....................... #


@asynccontextmanager
async def _runtime(dsn: str | None) -> AsyncGenerator[ExecutionRuntime]:
    """The store this manager reads and writes. A DSN names the Postgres
    log; without one the mock stands in, which is a real log for the life of
    the process and nothing afterwards — useful for a dry pass, useless as a
    board (S-0044/D-11: the port is what makes the two interchangeable)."""

    import sys

    from forze.application.execution import DepsRegistry, ExecutionRuntime
    from forze.base.logging import configure_logging

    from torve.adapters.eventstore.document import mock_module, postgres_module

    # The runtime narrates itself on stdout by default, and stdout is where
    # this verb's JSON goes. Warnings and worse, on stderr: a machine-read
    # channel carries one thing (S-0015/D-6).
    configure_logging(level="warning", stream=sys.stderr)

    module = await postgres_module(dsn) if dsn else mock_module()
    runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(module).freeze())

    async with runtime.scope():
        yield runtime


# ....................... #


async def _board(dsn: str | None, partition: str) -> Board:
    from torve.application.eventlog import event_log
    from torve.application.manager import project

    async with _runtime(dsn) as runtime:
        log = event_log(runtime.get_context())

        return project(await log.since(partition=partition))


# ....................... #


def _lane_leg(root: Path, config: RunnerConfig, *, only: str | None) -> Lane | None:
    """The pass's landing leg, or None when the switch is off.

    Off, a pass behaves exactly as it did before the leg existed: the
    switch's default is the whole of the safety story here, and landing
    stays the manual verb's job. On, the leg calls the same `process_lane`
    the operator calls, with the same CI, approvals, review and quiet
    window arguments this configuration feeds the verb — it adds a caller,
    not a policy, so no refusal changes and nothing is gated twice.
    """

    if not config.promotion.auto_merge:
        return None

    async def lane() -> list[str]:
        # Built here rather than above, for S-0044/A-11's reason one step back:
        # `_leg` protects a leg's *call*, so anything raised while building
        # one still takes the pass down. `_resolve_ci` refuses a promotion
        # configuration that names no remote, and refusing it out here left
        # the manager unable to reclaim, mint or dispatch (T-0284).
        from torve.adapters.vcs.git import GitLane
        from torve.application.lane import conflict_disposal, process_lane
        from torve.cli.merge import _resolve_ci

        # Landing is git in a blocking world: run it off the loop so a slow
        # rebase cannot stall the pass's clock. The arguments are `merge_cmd`'s
        # but for the conflict disposal (S-0079/D-10): a candidate whose rebase
        # conflicts against a moved base is re-queued here instead of waiting
        # for a person, bounded as ever by `conflict_base`. `torve merge`
        # passes none — the operator standing at the terminal is exactly who
        # should see a conflict.
        results = await asyncio.to_thread(
            process_lane,
            root,
            GitLane(),
            only=only,
            ci=_resolve_ci(config),
            approvals_required=config.promotion.approvals,
            require_review=config.promotion.require_review,
            quiet_window_s=config.promotion.quiet_window,
            on_conflict=conflict_disposal(root, GitLane()),
        )

        return [result.task for result in results if result.landed]

    return lane


# ....................... #


async def _serve(
    dsn: str | None,
    partition: str,
    *,
    root: Path,
    config_path: Path | None,
    worker: str,
    passes: int | None,
    interval: float,
    only: str | None,
    dispatch: bool,
    night: bool = False,
) -> tuple[int, str]:
    from torve.application.eventlog import event_log
    from torve.application.executors import runner_execute
    from torve.application.fleet import escalated_tasks
    from torve.application.manager import project
    from torve.application.projections import shipped_landings
    from torve.application.residency import close_night, open_night, ran_here, reached, serve
    from torve.application.worker import Worker
    from torve.cli import assembly
    from torve.cli.assembly import build_notifier
    from torve.domain.events import SubjectType

    config = load_config(root, config_path)

    # The repository's own answer (S-0019/A-3): a contract the tree already landed
    # is minted onto the board as landed, so a pass over a repository with
    # history does not offer a worker somebody's finished work. One batched
    # log pass, and the same evidence the projections call shipped (S-0049/A-2) —
    # the engine's trailer and a human's citation both mean finished.
    landings = shipped_landings(root)
    ran = ran_here(root)

    async def paused() -> bool:
        """This root's own pause rule, re-decided every pass (S-0048/A-1).

        The queue is the union of both carriers (S-0048/D-5): a task escalated
        under v1 left a run-state file, one this manager escalated is in
        the log, and a task in both is one task a person has to look at.
        """

        board = project(await log.of_subject_type(SubjectType.TASK, partition=partition))

        return len(escalated_tasks(root, board)) >= config.loop.pause_escalations

    async def relay() -> list[str]:
        """Drain the undelivered queue to whatever destination is
        configured. Built per pass from the same log the pass reads —
        the destination included, for S-0044/A-11's reason one step back:
        `build_notifier` refuses an adapter it does not know, and refusing
        it outside the leg took the whole pass down with it."""

        from torve.application.notify import relay as drain

        return await drain(
            log,
            build_notifier(config),
            partition=partition,
            actor_id=worker,
            max_attempts=config.notify.attempts,
        )

    def standing() -> tuple[str, bool]:
        # S-0023's leg, unchanged — it mints a contract through the
        # ordinary adoption path, and the scan above imports whatever it
        # minted onto the board. Nothing about it had to move for the
        # manager to run it (S-0023/A-2).
        from torve.application.standing import standing_leg

        return standing_leg(root, config, runtime_for(config, None), landings.__contains__)

    # None unless the auto-merge switch is on: an unarmed serve is the
    # same pass it was before the landing leg existed.
    lane_leg = _lane_leg(root, config, only=only)

    async with _runtime(dsn) as runtime:
        log = event_log(runtime.get_context())

        # S-0079/D-6: refused here, before the first pass, while the operator
        # who typed the command is still standing there. Nothing refuses the
        # night after this point — the same empty queue an hour later is the
        # night's work finished, and `reached` closes it.
        terms = (
            await open_night(log, partition, config=config.night, actor_id=worker)
            if night
            else None
        )

        async def stop() -> str | None:
            return await reached(log, partition, terms) if terms is not None else None

        handled = await serve(
            log,
            Worker(
                log=log,
                name=worker,
                execute=runner_execute(
                    root,
                    config,
                    assembly.build_dispatch_prepare(root, config),
                    log=log,
                    partition=partition,
                    seat=worker,
                ),
            ),
            root,
            partition,
            idle_seconds=interval,
            passes=passes,
            landed=landings.get,
            ran=ran.__contains__,
            only=only,
            dispatch=dispatch,
            standing=standing,
            paused=paused,
            relay=relay,
            lane=lane_leg,
            stop=stop if terms is not None else None,
        )

        if terms is None:
            return handled, ""

        # The reason is asked once more rather than remembered from the loop:
        # `serve` also returns when its pass count runs out, and a night that
        # ended that way has no term to name.
        return handled, await close_night(
            log,
            partition,
            terms,
            reason=await reached(log, partition, terms) or "passes",
            handled=handled,
            actor_id=worker,
        )


# ....................... #


def _burn(view: TaskView) -> str:
    """What the burn stream says about this task: how long since it last
    spent anything, and how much. `stalled` is the reading, not a verdict —
    nothing acts on it (S-0045/D-8 is open)."""

    if view.last_burn is None:
        return "—"

    age = (datetime.now(UTC) - view.last_burn).total_seconds() / 60

    return f"{age:.0f}m ago ${view.burned_usd:.2f}" + (" · stalled" if stalled(view) else "")


# ....................... #


@manager_app.command("board")
def board_cmd(
    partition: Annotated[str, typer.Argument(help="The repository this board is for.")],
    dsn: Annotated[
        str,
        typer.Option(
            "--dsn",
            help="Postgres DSN holding the log; omitted reads the DSN this repository's configuration names.",
        ),
    ] = "",
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Show what a partition's recorded facts add up to: every task the log
    has mentioned, the state its events leave it in, and who holds it.

    Nothing here is stored — the board is rebuilt from the log on every
    call, which is the same thing a manager does when it restarts.
    """

    result = asyncio.run(_board(dsn_for(root, dsn) or None, partition))

    if fmt is Format.JSON:
        emit_json(
            {
                "partition": partition,
                "tasks": [
                    {
                        "task": view.task_id,
                        # S-0049/A-1: the record holds every contract, and a
                        # review or draft one is queued in the sense that
                        # nobody will ever claim it. The role is what tells
                        # the two kinds of queued apart.
                        "role": view.contract.role if view.contract else None,
                        "state": str(view.state),
                        "attempts": view.attempts,
                        "claimed_by": view.claimed_by,
                        "landed_sha": view.landed_sha,
                        "escalation": view.escalation,
                        "last_burn": view.last_burn.isoformat() if view.last_burn else None,
                        "burned_usd": round(view.burned_usd, 4),
                        "stalled": stalled(view),
                    }
                    for view in sorted(result.tasks.values(), key=lambda one: one.task_id)
                ],
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "manager board", partition)
    table = make_table("task", "role", "state", "attempts", "held by", "burn", "landing")
    withheld = add_rows_truncated(
        table,
        [
            (
                view.task_id,
                view.contract.role if view.contract else "—",
                view.escalation or str(view.state),
                str(view.attempts),
                view.claimed_by or "—",
                _burn(view),
                (view.landed_sha or "")[:12] or "—",
            )
            for view in sorted(result.tasks.values(), key=lambda one: one.task_id)
        ],
    )
    console.print(table)
    footer(
        console,
        f"{len(result.tasks)} task(s) the log has mentioned"
        + (f" — … {withheld} more (see JSON)" if withheld else ""),
    )
    raise typer.Exit(EXIT_OK)


# ....................... #


@manager_app.command("serve")
def serve_cmd(
    partition: Annotated[str, typer.Argument(help="The repository this manager owns.")],
    dsn: Annotated[
        str,
        typer.Option(
            "--dsn",
            help="Postgres DSN holding the log; omitted uses the DSN this repository's configuration names.",
        ),
    ] = "",
    worker: Annotated[
        str, typer.Option("--worker", help="This process's name in the log.")
    ] = "worker-1",
    task: Annotated[
        str,
        typer.Option("--task", help="Run only this contract; omitted takes the board's order."),
    ] = "",
    passes: Annotated[
        int,
        typer.Option("--passes", help="Stop after this many passes; 0 runs until interrupted."),
    ] = 0,
    no_dispatch: Annotated[
        bool,
        typer.Option(
            "--no-dispatch",
            help="Import contracts and release expired leases, but claim nothing.",
        ),
    ] = False,
    interval: Annotated[
        float, typer.Option("--interval", help="Seconds an idle pass waits before looking again.")
    ] = IDLE_SECONDS,
    night: Annotated[
        bool,
        typer.Option(
            "--night",
            help="Run under the configuration's night terms: refused now if nothing can be "
            "started, and stopped by a budget, the wall-clock end or a named escalation.",
        ),
    ] = False,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Run the manager: mint what the repository has added, claim one task
    at a time, execute it, and record what happened.

    When the configuration arms auto-merge, each pass also lands finished
    candidates through the serialized lane — the same lane the merge
    command runs, with the same approvals, review, CI and quiet-window
    refusals, and stopped by the same pause. Off, which is the default,
    landing stays a human act.

    The process holds nothing. Every pass rebuilds its view from the log, so
    interrupting this is safe at any moment — the cost of a kill is the
    lease on whatever was in flight, and a restart reads back exactly what
    the previous process knew.

    `--night` runs the same loop under the terms the configuration's night
    section carries. The terms are read once, at the open, and recorded
    there; the open refuses a board with nothing to start, and the loop
    stops on a budget, on the wall-clock end or on the first escalation of
    a class the terms name. Every bound is soft: they are read between
    passes, so the attempt that was running when one was reached finishes.
    """

    root = root.resolve()

    interrupted = False
    reason = ""

    try:
        handled, reason = asyncio.run(
            _serve(
                dsn_to_write(root, dsn) or None,
                partition,
                root=root,
                config_path=config_path,
                worker=worker,
                passes=passes or None,
                interval=interval,
                only=task or None,
                dispatch=not no_dispatch,
                night=night,
            )
        )

    except KeyboardInterrupt:
        # Not an error and not a loss: the log already carries everything
        # this process did, and the next one reads it back.
        handled, interrupted = 0, True

    except NightRefused as exc:
        # S-0079/D-6, at the one moment somebody is there to read it.
        raise fail(f"night refused: {exc}", EXIT_CONFIG) from None

    if fmt is Format.JSON:
        emit_json(
            {
                "partition": partition,
                "worker": worker,
                "handled": handled,
                "interrupted": interrupted,
                "stopped_on": reason,
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    closing(
        console,
        "interrupted — the log holds the pass"
        if interrupted
        else f"{handled} task(s) handled" + (f" · stopped on {reason}" if reason else ""),
        STYLE_DIM if interrupted or not handled else "",
    )
    raise typer.Exit(EXIT_OK)


# ....................... #


async def _note(dsn: str | None, partition: str, task_id: str, topic: str, body: str) -> None:
    from torve.application.eventlog import event_log
    from torve.domain.events import ActorKind, EventKind, SubjectType

    async with _runtime(dsn) as runtime:
        await event_log(runtime.get_context()).record(
            EventKind.MESSAGE_SENT,
            partition=partition,
            subject_type=SubjectType.TASK,
            subject_id=task_id,
            actor_kind=ActorKind.OPERATOR,
            actor_id="operator",
            payload={"to_role": "implement", "topic": topic, "body": body},
        )


# ....................... #


async def _resolve(
    dsn: str | None, partition: str, task_id: str, resolution: str, note: str
) -> None:
    from torve.application.eventlog import event_log
    from torve.domain.events import ActorKind, EventKind, SubjectType

    async with _runtime(dsn) as runtime:
        await event_log(runtime.get_context()).record(
            EventKind.ESCALATION_RESOLVED,
            partition=partition,
            subject_type=SubjectType.TASK,
            subject_id=task_id,
            actor_kind=ActorKind.OPERATOR,
            actor_id="operator",
            payload={"resolution": resolution, "note": note},
        )


# ....................... #


async def _return(dsn: str | None, partition: str, task_id: str, reason: str, note: str) -> None:
    from torve.application.eventlog import event_log
    from torve.domain.events import ActorKind, EventKind, SubjectType

    async with _runtime(dsn) as runtime:
        await event_log(runtime.get_context()).record(
            EventKind.TASK_RETURNED,
            partition=partition,
            subject_type=SubjectType.TASK,
            subject_id=task_id,
            actor_kind=ActorKind.OPERATOR,
            actor_id="operator",
            payload={"reason": reason, "note": note},
        )


# ....................... #


@manager_app.command("return")
def return_cmd(
    partition: Annotated[str, typer.Argument(help="The repository the task belongs to.")],
    task_id: Annotated[str, typer.Argument(help="The reviewed candidate to send back.")],
    reason: Annotated[
        str, typer.Option("--reason", help="Why it is going back, in a few words.")
    ] = "returned for revision",
    note: Annotated[
        str,
        typer.Option("--note", help="What the next attempt should do differently."),
    ] = "",
    dsn: Annotated[
        str,
        typer.Option(
            "--dsn",
            help="Postgres DSN holding the log; omitted uses the DSN this repository's "
            "configuration names.",
        ),
    ] = "",
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Send a reviewed candidate back for revision.

    A candidate reaches `ready` when its gates and its review are done —
    and a person may still judge it wrong. Before this, the only answers
    were to land it and fix it afterwards or to abandon the work; a lease
    reclaim returns only what is still in flight, and writing an escalation
    nobody raised would put a lie in the log to move a task.

    `--note` rides the same feedback record a surviving blocker uses, so
    the next attempt is briefed by the person who sent it back rather than
    starting blind. Only an operator may write this.
    """

    from torve.application.feedback import capture_feedback

    root = root.resolve()
    asyncio.run(_return(dsn_to_write(root, dsn) or None, partition, task_id, reason, note))

    # The critique travels the way a blocker's does (S-0043/D-2, S-0005/D-13): the
    # note is a thread, and an empty one captures nothing rather than
    # briefing the next attempt with silence.
    briefed = bool(note) and capture_feedback(
        root,
        task_id,
        "",
        [{"comments": [{"author": "operator", "body": note}]}],
    )

    if fmt is Format.JSON:
        emit_json({"partition": partition, "task": task_id, "reason": reason, "briefed": briefed})
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    closing(console, f"{task_id}: returned — {reason}")

    if briefed:
        closing(console, "the note is in the task's feedback record", STYLE_DIM)


# ....................... #


@manager_app.command("resolve")
def resolve_cmd(
    partition: Annotated[str, typer.Argument(help="The repository the task belongs to.")],
    task_id: Annotated[str, typer.Argument(help="The escalated task.")],
    resolution: Annotated[
        str,
        typer.Option("--resolution", help="requeued, abandoned or landed."),
    ] = "requeued",
    note: Annotated[str, typer.Option("--note", help="Why, for whoever reads this later.")] = "",
    dsn: Annotated[
        str,
        typer.Option(
            "--dsn",
            help="Postgres DSN holding the log; omitted uses the DSN this repository's configuration names.",
        ),
    ] = "",
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Close an escalation, and say how.

    An escalation is the engine handing a task to a person; this is the
    person handing it back. `requeued` returns it to the board, `abandoned`
    takes it off, `landed` records that it was finished by hand. Only an
    operator may write this — an agent that could close its own escalation
    could escalate its way out of every rule it dislikes.
    """

    if resolution not in ("requeued", "abandoned", "landed"):
        raise fail(
            f"configuration error: resolution must be requeued, abandoned or landed, "
            f"not {resolution!r}",
            EXIT_CONFIG,
        )

    asyncio.run(_resolve(dsn_to_write(root, dsn) or None, partition, task_id, resolution, note))

    if fmt is Format.JSON:
        emit_json({"partition": partition, "task": task_id, "resolution": resolution})
        raise typer.Exit(EXIT_OK)

    closing(out(fmt), f"{task_id}: {resolution}")
    raise typer.Exit(EXIT_OK)


# ....................... #


@manager_app.command("note")
def note_cmd(
    partition: Annotated[str, typer.Argument(help="The repository the task belongs to.")],
    task_id: Annotated[str, typer.Argument(help="The task to address.")],
    body: Annotated[str, typer.Argument(help="What the agent should know.")],
    topic: Annotated[str, typer.Option("--topic", help="One word naming what this is about.")] = (
        "note"
    ),
    dsn: Annotated[
        str,
        typer.Option(
            "--dsn",
            help="Postgres DSN holding the log; omitted uses the DSN this repository's configuration names.",
        ),
    ] = "",
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Say something to a running attempt.

    A note is a recorded fact, not a prompt edit: the agent polls for it
    with `torve log notes`, nothing interrupts it mid-thought, and what the
    engine tried to say is auditable afterwards whether or not it was read.
    """

    asyncio.run(_note(dsn_to_write(root, dsn) or None, partition, task_id, topic, body))

    if fmt is Format.JSON:
        emit_json({"partition": partition, "task": task_id, "topic": topic, "sent": True})
        raise typer.Exit(EXIT_OK)

    closing(out(fmt), f"note sent to {task_id} — the agent reads it when it polls")
    raise typer.Exit(EXIT_OK)
