"""`torve context` — parsing and rendering only (D-15.6); the projections
live in `torve.application.projections`. Three renderings of one report
(D-7.4, D-18.6): rich tables for reading in place (the default), markdown
for pasting into a planning session, JSON for machines. `--format markdown`
exists here and only here — this is the one document-producing command.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.text import Text

from torve.cli.console import (
    STYLE_DIM,
    STYLE_FAIL,
    STYLE_ID,
    STYLE_PASS,
    STYLE_WARN,
    add_rows_truncated,
    emit_json,
    footer,
    header,
    id_list,
    make_table,
    out,
)
from torve.cli.options import ConfigOption, RootOption, load_config
from torve.domain.states import EXIT_OK

# ----------------------- #


class ContextFormat(StrEnum):
    TEXT = "text"
    JSON = "json"
    MARKDOWN = "markdown"


# ....................... #


def context_cmd(
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: Annotated[
        ContextFormat,
        typer.Option(
            "--format",
            help="text for reading in place, markdown for pasting into "
            "a planning session, json for machines.",
        ),
    ] = ContextFormat.TEXT,
) -> None:
    """Project accumulated facts for a planning session: tasks
    by state, escalations by reason, proposals awaiting the author, gate
    health, cost against config_hash, and the programme view."""

    from torve.application.projections import context_report, render_markdown

    root = root.resolve()
    config = load_config(root, config_path)
    report = context_report(root, root / config.rfcs.path)

    if fmt is ContextFormat.JSON:
        emit_json(report)
    elif fmt is ContextFormat.MARKDOWN:
        out().print(render_markdown(report))
    else:
        _render_rich(report)

    raise typer.Exit(EXIT_OK)


# ....................... #


def _age(seconds: float) -> str:
    """Coarse humanised age — days beat hours beat minutes; precision is
    noise at triage granularity."""

    if seconds >= 86400:
        return f"{seconds / 86400:.0f}d"

    if seconds >= 3600:
        return f"{seconds / 3600:.0f}h"

    return f"{max(1, seconds // 60):.0f}m"


# ....................... #


def _render_rich(report: dict[str, Any]) -> None:
    console = out()
    header(console, "context", f"projected {report['at']}")
    console.print()

    programme = make_table("rfc", "title", "status", "impl", "progress", "notes", title="Programme")
    settled: list[str] = []

    for doc in report["programme"]:
        notes: list[str] = []

        if doc["plannable"]:
            notes.append("plannable")

        if doc["unsatisfied_depends_on"]:
            notes.append(f"waits on {', '.join(doc['unsatisfied_depends_on'])}")

        if doc["disagreement"]:
            notes.append(f"⚠ {doc['disagreement']}")

        if doc["status"] == "accepted" and doc["implementation"] == "complete" and not notes:
            # Finished business earns a dim count, not a row — the JSON
            # report still carries every document.
            settled.append(str(doc["rfc"]))
            continue

        progress = ", ".join(f"P{k}: {v}" for k, v in doc["progress"].items())
        programme.add_row(
            Text(str(doc["rfc"]), STYLE_ID),
            str(doc["title"]),
            Text(str(doc["status"]), STYLE_PASS if doc["status"] == "accepted" else STYLE_DIM),
            str(doc["implementation"]),
            progress,
            Text("; ".join(notes), STYLE_WARN if doc["disagreement"] else ""),
        )

    console.print(programme)

    if settled:
        footer(console, f"… plus {len(settled)} accepted and complete: {id_list(settled)}")

    tasks = make_table("state", "count", "tasks", title="Tasks by state")
    by_state: dict[str, list[str]] = {}

    for task in report["tasks"]:
        by_state.setdefault(str(task["state"]), []).append(str(task["id"]))

    for state, ids in sorted(by_state.items()):
        # Newest first, so the bounded list surfaces recent work — the report
        # itself keeps its stable ascending order.
        tasks.add_row(
            Text(
                state,
                STYLE_PASS if state == "ready" else STYLE_FAIL if state == "escalated" else "",
            ),
            str(len(ids)),
            id_list(sorted(ids, reverse=True)),
        )

    console.print(tasks)

    if report["escalations"]:
        escalations = make_table(
            "reason", "route", "count", "tasks", "oldest", title="Escalations by reason"
        )
        ages: list[float] = []

        for reason, items in sorted(report["escalations"].items()):
            reason_ages = [float(item["age_s"]) for item in items if item.get("age_s") is not None]
            ages += reason_ages
            escalations.add_row(
                Text(reason, STYLE_FAIL),
                str(items[0].get("route", "")),
                str(len(items)),
                ", ".join(str(item["task"]) for item in items),
                _age(max(reason_ages)) if reason_ages else "",
            )

        console.print(escalations)

        if ages:
            # The queue's age is the alert, not its length: a queue nobody
            # triages looks identical to success from inside the runner.
            footer(
                console,
                f"oldest escalation has waited {_age(max(ages))} — "
                "the queue's age is the signal to watch",
            )

    fresh = [p for p in report["proposals"] if not p.get("possibly_landed")]

    if report["proposals"]:
        proposals = make_table(
            "decision",
            "from",
            "proposal",
            title="Proposals awaiting the author",
            lines=True,
            last_max_width=76,
        )
        withheld = add_rows_truncated(
            proposals,
            [
                (
                    Text(str(item["decision"]), STYLE_ID),
                    Text(str(item["task"]), STYLE_ID),
                    str(item["proposal"]).strip(),
                )
                for item in fresh
            ],
            limit=40,
        )
        console.print(proposals)

        if withheld:
            footer(console, f"… {withheld} more fresh proposal(s) (see JSON)")

        landed = len(report["proposals"]) - len(fresh)

        if landed:
            footer(
                console,
                f"… plus {landed} from tasks the decision tables "
                "already cite — likely landed (see JSON)",
            )

    if report["gates"]:
        gates = make_table(
            "gate", "runs", "failures", "flaky", "bypassed", "mean", "max", title="Gate health"
        )

        for name, gate in sorted(report["gates"].items()):
            gates.add_row(
                name,
                str(gate["runs"]),
                Text(str(gate["failures"]), STYLE_FAIL if gate["failures"] else STYLE_DIM),
                str(gate["flaky"]),
                str(gate["bypassed"]),
                Text(f"{gate['mean_duration_s']}s", STYLE_DIM),
                Text(f"{gate['max_duration_s']}s", STYLE_DIM),
            )

        console.print(gates)

    if report["costs"]:
        costs = make_table("kind", "task", "regime", "cost", "detail", title="Cost and iterations")
        rows: list[tuple[Any, ...]] = []

        for row in report["costs"]:
            cost = row.get("cost_usd")
            shown = f"${cost:.4f}" if isinstance(cost, (int, float)) else "unrecorded"
            detail = (
                f"attempts {row['attempts']}, {row['state']}"
                if row["kind"] == "shadow"
                else str(row.get("adapter") or "")
            )
            rows.append(
                (
                    row["kind"],
                    Text(str(row["task"]), STYLE_ID),
                    Text(str(row.get("config_hash")), STYLE_ID),
                    shown,
                    detail,
                )
            )

        withheld = add_rows_truncated(costs, rows, limit=40)
        console.print(costs)

        if withheld:
            footer(console, f"… {withheld} more record(s) (see JSON)")
