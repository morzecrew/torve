"""`torve eval` — the S-0009/evals eval loop: parsing and rendering only
(S-0015/D-6); the arms live in `torve.application.evals` over the shadow
machinery (S-0004/shadow-runs — nothing merges, S-0004/D-4). Exit codes follow the
shadow doctrine: a completed eval exits 0 whatever the arms measured, 3
is a configuration problem, 4 an infrastructure failure. The paired
configuration eval takes the candidate arm's override — an image (S-0027
S-0027/D-7) or a tier variant (S-0034 S-0034/D-10) — and both arms run the agent
their own configuration resolves, through the CLI's factory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.text import Text

from torve.cli.console import (
    STYLE_DIM,
    STYLE_PASS,
    STYLE_WARN,
    Format,
    closing,
    emit_json,
    err,
    fail,
    header,
    id_list,
    live_status,
    make_table,
    out,
)
from torve.cli.options import (
    ConfigOption,
    FormatOption,
    RootOption,
    load_config,
    runtime_for,
)
from torve.config import layout
from torve.domain.states import EXIT_CONFIG, EXIT_INFRASTRUCTURE, EXIT_OK
from torve.gates.context import load_task

# ----------------------- #


def _distribution(table: dict[str, dict[str, dict[str, Any]]]) -> dict[str, list[str]]:
    """Tasks grouped by which arms reached ready (S-0074/D-4): the summary is a
    distribution, never a mean — a task where the arm without the battery
    shipped what the battery would have refused keeps its own row instead
    of averaging into a percentage."""

    from torve.application.evals import ARMS

    groups: dict[str, list[str]] = {}

    for task in sorted(table):
        green = [arm for arm in ARMS if table[task].get(arm, {}).get("state") == "ready"]
        groups.setdefault("+".join(green) if green else "none", []).append(task)

    return groups


# ....................... #


def _cell(row: dict[str, Any] | None, key: str) -> str:
    """One measure of one arm, or `-` where that arm never ran the task."""

    if row is None or row.get(key) is None:
        return "-"

    value = row[key]

    return f"{value:.4f}" if key == "cost_usd" else str(value)


# ....................... #


def _report_arms(root: Path, task_ids: list[str], fmt: Format) -> None:
    """The three-arm reading, rebuilt from the eval ledger alone (S-0074/D-1):
    one table per task, three rows deep, and a distribution over the tasks
    instead of an aggregate (S-0074/D-4)."""

    from torve.application.evals import ARMS, three_arm_table

    table = three_arm_table(root)

    if task_ids:
        table = {task: arms for task, arms in table.items() if task in task_ids}

    distribution = _distribution(table)

    if fmt is Format.JSON:
        emit_json({"tasks": table, "distribution": distribution})
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "eval", "three arms")

    if not table:
        closing(console, "no arm results in the eval ledger — nothing to report", STYLE_WARN)
        raise typer.Exit(EXIT_OK)

    for task in sorted(table):
        rows = make_table("arm", "state", "attempts", "cost usd", title=task)

        for arm in ARMS:
            row = table[task].get(arm)
            rows.add_row(arm, _cell(row, "state"), _cell(row, "attempts"), _cell(row, "cost_usd"))

        console.print(rows)

    summary = make_table("arms green", "tasks", "task ids")

    for pattern, tasks in sorted(distribution.items()):
        summary.add_row(pattern, str(len(tasks)), id_list(tasks))

    console.print(summary)
    closing(
        console,
        "read it per task: the arms are not equally exposed to the same failures, so a mean "
        "over unlike tasks answers a question nobody asked",
        STYLE_DIM,
    )
    console.print(Text("direction, never magnitude — a replay is a quasi-experiment", STYLE_DIM))

    raise typer.Exit(EXIT_OK)


# ....................... #


def _preflight(arms: list[str], costs: dict[str, dict[str, Any]], seat: str, fmt: Format) -> None:
    """What an arm run is about to do, before it does it (S-0082/D-11): which arms
    over which tasks on which seat, and what those tasks' recorded attempts
    already cost when they were done for real. No ceiling of its own — an arm
    run is bounded by the contract's budget and the broker's mid-run refusal.
    Under --format json it goes to stderr, so stdout stays one document."""

    console = err() if fmt is Format.JSON else out(fmt)
    header(console, "eval", f"arms {', '.join(arms)} · seat {seat}")
    table = make_table("task", "recorded attempts", "cost usd")

    for task_id, row in costs.items():
        cost = row["cost_usd"]
        table.add_row(task_id, str(row["attempts"]), "-" if cost is None else f"{cost:.4f}")

    console.print(table)
    console.print(
        Text(
            f"{len(arms) * len(costs)} replay(s) about to start, nothing merged — an arm run "
            "is bounded by the contract's budget and the broker's refusal, and by nothing here",
            STYLE_WARN,
        )
    )


# ....................... #


def eval_cmd(
    skill: Annotated[
        str | None,
        typer.Argument(
            help="The skill under measurement; omit to run a paired incumbent/candidate "
            "configuration comparison with --tier plus --image or --variant instead."
        ),
    ] = None,
    *,
    task_ids: Annotated[
        list[str],
        typer.Option(
            "--task",
            help="A completed task to replay in both arms; repeatable. With --report it "
            "narrows the reading to the named tasks instead.",
        ),
    ] = [],  # noqa: B006 — typer reads the default, and a list option is never mutated
    arm_names: Annotated[
        list[str],
        typer.Option(
            "--arm",
            help="An arm of the apparatus axis to replay — bare, gated or configured; "
            "repeatable, and all three when omitted. Refuses to combine with a skill "
            "argument or with --image or --variant.",
        ),
    ] = [],  # noqa: B006 — typer reads the default, and a list option is never mutated
    report: Annotated[
        bool,
        typer.Option(
            "--report",
            help="Report the recorded arms per task — one table per task, one row per arm — "
            "from the eval ledger alone, and run nothing.",
        ),
    ] = False,
    tier: Annotated[
        str | None,
        typer.Option(
            "--tier",
            help="The seat tier under measurement; pairs with --image or --variant "
            "instead of a skill argument. On an arm run it names the seat every arm "
            "runs on instead of the task's own.",
        ),
    ] = None,
    image: Annotated[
        str | None,
        typer.Option("--image", help="The candidate image for --tier."),
    ] = None,
    variant: Annotated[
        str | None,
        typer.Option(
            "--variant",
            help="The candidate tier variant for --tier, resolved as a dotted tier entry "
            "beside the seat; pairs with --tier instead of --image.",
        ),
    ] = None,
    depth: Annotated[
        int, typer.Option(min=1, help="History depth of the truncated shadow clones.")
    ] = 50,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Measure a skill against its without-skill baseline, or (with --tier plus
    --image or --variant instead of a skill) a candidate configuration against
    the incumbent: every named task replays twice in shadow, and one eval
    record lands in the evals ledger. Naming neither — a bare --task, or
    --arm — replays the named tasks across the apparatus arms instead, all
    three unless --arm narrows them, on the task's own seat unless --tier
    names another. Nothing a replay produces is ever merged. With --report
    nothing runs: the arms already recorded are read back from the ledger,
    one table per task."""

    if report:
        # The reading needs the ledger and nothing else — no configuration,
        # no contracts, no agent (S-0074/D-1).
        _report_arms(root.resolve(), task_ids, fmt)

    from functools import partial

    from torve.adapters.broker import build_broker
    from torve.adapters.store.durable import open_store
    from torve.adapters.vcs.git import GitVcs, NullScm, repository_name
    from torve.adapters.workspace.git import (
        GitWorkspace,
        ShadowWorkspace,
        diff_range,
        diff_worktree,
        parent_of,
        shipped_commit,
    )
    from torve.application.dispatch import RunDeps
    from torve.application.evals import (
        ARMS,
        candidate_config,
        eligible_tasks,
        run_arm_eval,
        run_config_eval,
        run_skill_eval,
        without_skill,
    )
    from torve.application.ports import Agent
    from torve.application.shadow import ShadowSource
    from torve.cli.run import build_tier_agent
    from torve.config.runconfig import ProviderDenied, route_provider, tier_for, tier_name_for
    from torve.domain.task import Task

    config_mode = tier is not None or image is not None or variant is not None

    # The third mode, told apart by its own argument (S-0082/D-8) — and by naming
    # neither of the other two's, since the axis is the three arms and an
    # invocation that names no comparison inside the apparatus is a run of it.
    # `--tier` here is the seat the arms run on (S-0082/D-10), never a candidate.
    arm_mode = bool(arm_names) or (skill is None and not config_mode)

    if arm_mode:
        if skill is not None:
            raise fail(
                "configuration error: give a skill argument or --arm, not both — one names "
                "a comparison inside the apparatus and the other removes it",
                EXIT_CONFIG,
            )

        if image is not None or variant is not None:
            raise fail(
                "configuration error: --arm refuses to combine with --image or --variant — "
                "one names a comparison inside the apparatus and the other removes it",
                EXIT_CONFIG,
            )

        arms = list(arm_names) or list(ARMS)
        unknown = [name for name in arms if name not in ARMS]

        if unknown:
            raise fail(
                f"configuration error: unknown arm {unknown[0]!r} — the arms are {', '.join(ARMS)}",
                EXIT_CONFIG,
            )

    if skill is not None and config_mode:
        raise fail(
            "configuration error: give a skill argument or --tier with an override, not both",
            EXIT_CONFIG,
        )

    if (
        skill is None
        and not arm_mode
        and not (tier is not None and (image is not None or variant is not None))
    ):
        raise fail(
            "configuration error: give a skill argument, or --tier with either "
            "--image or --variant",
            EXIT_CONFIG,
        )

    if image is not None and variant is not None:
        raise fail(
            "configuration error: --image and --variant refuse to combine — give the "
            "candidate arm one override at a time",
            EXIT_CONFIG,
        )

    root = root.resolve()
    eligible: dict[str, dict[str, Any]] = {}

    if arm_mode:
        # One read answers both the refusal and the pre-flight, so they can
        # never disagree about what an arm run may name (S-0082/D-3, S-0082/D-9).
        eligible = eligible_tasks(root)
        listing = (
            f"eligible now: {id_list(sorted(eligible))}"
            if eligible
            else "nothing is eligible yet — a task becomes eligible once a landing names "
            "the commit a replay starts from"
        )

        if not task_ids:
            raise fail(
                f"configuration error: give at least one --task to replay; {listing}",
                EXIT_CONFIG,
            )

        outside = [task_id for task_id in task_ids if task_id not in eligible]

        if outside:
            raise fail(
                f"configuration error: no landing of {outside[0]} names a commit, so no "
                f"replay can start from it; {listing}",
                EXIT_CONFIG,
            )

        eligible = {task_id: eligible[task_id] for task_id in task_ids}

    if not task_ids:
        raise fail(
            "configuration error: give at least one --task to replay, or --report to read "
            "the arms already recorded",
            EXIT_CONFIG,
        )

    config = load_config(root, config_path)
    tasks: list[Task] = []

    for task_id in task_ids:
        task_file = layout.task_file(root, task_id)

        if not task_file.is_file():
            raise fail(f"configuration error: no task contract at {task_file}", EXIT_CONFIG)

        tasks.append(load_task(task_file))

    seat = ""

    try:
        if arm_mode:
            # The seat is the task's own unless --tier names another, and every
            # arm of one invocation runs on the same one — a difference between
            # arms is never a difference between seats (S-0082/D-10).
            seats = {tier} if tier is not None else {tier_name_for(task) for task in tasks}

            if len(seats) > 1:
                raise ValueError(
                    f"these tasks name {', '.join(sorted(seats))} — every arm of one "
                    "invocation runs on the same seat; name one with --tier"
                )

            seat = seats.pop()

            if tier is not None:
                # The named seat replaces each task's own, so the agent built
                # here and the image the replay resolves are one seat.
                named = tier_for(config, seat)
                config = config.model_copy(
                    update={"tiers": {**config.tiers, **{t.tier: named for t in tasks}}}
                )

            incumbent_agent = build_tier_agent(config, root, seat)
            candidate_agent: Agent | None = None
        elif skill is not None:
            without_skill(config, skill)  # refuse before any spend
            incumbent_agent = build_tier_agent(config, root, tasks[0].tier)
            candidate_agent = None
        else:
            assert tier is not None
            # Refuse before any spend: the candidate config validates the
            # override — an image or variant the seat already resolves, an
            # unknown variant, or a combined override (S-0027/D-7, S-0034/D-10).
            candidate = candidate_config(config, tier, image=image, variant=variant)
            incumbent_agent = build_tier_agent(config, root, tier)
            candidate_agent = build_tier_agent(candidate, root, tier)

        tiers = {task.tier for task in tasks}

        for name in tiers:
            resolved = tier_for(config, name)
            route_provider(config.providers, repository_name(root), resolved.provider)

    except (ProviderDenied, ValueError) as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    # Each arm runs the agent its own configuration resolves: the incumbent
    # keeps the seat's, the candidate arm builds its own from the resolved
    # override through the CLI's factory — a candidate differing in model,
    # command, adapter or image runs as itself in every respect, never as
    # the incumbent's agent under a candidate label (S-0034/D-10).
    deps = RunDeps(
        workspace=GitWorkspace(root),
        runtime=runtime_for(config, None),
        agent=incumbent_agent,
        vcs=GitVcs(),
        scm=NullScm(),
        store=open_store,
        broker=build_broker(config.broker),
    )

    shadow_ws = ShadowWorkspace(root, depth=depth)

    source = ShadowSource(
        create_workspace=shadow_ws.create,
        shipped_commit=partial(shipped_commit, root),
        parent_of=partial(parent_of, root),
        diff_range=partial(diff_range, root),
        diff_worktree=diff_worktree,
    )

    try:
        if arm_mode:
            _preflight(arms, eligible, seat, fmt)

            with live_status(f"{len(arms)} arm(s) over {len(tasks)} task(s) on {seat}", fmt):
                record = run_arm_eval(root, tasks, config, deps, source, tuple(arms))
        elif skill is not None:
            with live_status(f"eval of {skill} over {len(tasks)} task(s), two arms", fmt):
                record = run_skill_eval(root, skill, tasks, config, deps, source)
        else:
            assert tier is not None
            status = f"paired eval of tier {tier} over {len(tasks)} task(s), incumbent vs candidate"

            with live_status(status, fmt):
                record = run_config_eval(
                    root,
                    tier,
                    tasks,
                    config,
                    deps,
                    source,
                    image=image,
                    variant=variant,
                    candidate_agent=candidate_agent,
                )

    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    except RuntimeError as exc:
        raise fail(f"infrastructure failure: {exc}", EXIT_INFRASTRUCTURE) from exc

    if fmt is Format.JSON:
        emit_json(record)
        raise typer.Exit(EXIT_OK)

    if record["kind"] == "arm-eval":
        # The reading is the one already built: the record just landed is read
        # back out of the ledger it landed in, per task and never as a mean.
        _report_arms(root, task_ids, fmt)

    console = out(fmt)

    if record["kind"] == "skill-eval":
        header(console, "eval", record["skill"])
        table = make_table("arm", "green", "attempts", "cost usd")

        for arm in ("with", "without"):
            row = record["summary"][arm]

            table.add_row(
                arm,
                f"{row['green']}/{len(record['tasks'])}",
                str(row["attempts"]),
                "-" if row["cost_usd"] is None else f"{row['cost_usd']:.4f}",
            )

        console.print(table)

        if record["baseline_matched"] is None:
            # Nothing was compared, and saying "baseline matched" here is how
            # a skill gets deleted on the evidence of a replay that measured
            # nothing (S-0009/A-5).
            why = (
                "the seat is a fake adapter, so no model ran"
                if record.get("simulated")
                else "neither arm completed a task"
            )
            closing(console, f"inconclusive — {why}; nothing here measures the skill", STYLE_WARN)
        elif record["baseline_matched"]:
            closing(
                console,
                "baseline matched — this skill did not earn its tokens here; deletion is your call",
                STYLE_DIM,
            )
        else:
            closing(console, "the skill beat its baseline on this evidence", STYLE_PASS)

    else:
        candidate_identity = record.get("variant") or record["image"]
        header(console, "eval", f"tier {record['tier']} · candidate {candidate_identity}")
        identity_col = "config hash" if record.get("variant") else "digest"
        table = make_table("arm", "green", "attempts", "cost usd", identity_col)

        for arm in ("incumbent", "candidate"):
            row = record["summary"][arm]
            identity = record["configs"][arm] if record.get("variant") else record["digests"][arm]

            table.add_row(
                arm,
                f"{row['green']}/{len(record['tasks'])}",
                str(row["attempts"]),
                "-" if row["cost_usd"] is None else f"{row['cost_usd']:.4f}",
                identity[:12] if identity else "unresolved",
            )

        console.print(table)

        if record["candidate_matched"]:
            closing(
                console,
                "candidate matched the incumbent on this evidence — displacing the default "
                "stays your call",
                STYLE_PASS,
            )
        else:
            closing(console, "the candidate did not match the incumbent here", STYLE_DIM)

    console.print(Text("direction, never magnitude — a replay is a quasi-experiment", STYLE_DIM))

    raise typer.Exit(EXIT_OK)
