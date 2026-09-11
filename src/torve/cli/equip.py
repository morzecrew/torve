"""`torve equip` — warm the equipment cache, and audit what is in it.

Fetching is an operator's act, done host-side and before any attempt exists
(S-0062/D-4). This verb is how it is done deliberately; dispatch does the same
thing for what a seat needs, so a cold cache costs the first attempt a fetch
rather than failing it. Parsing and rendering only (S-0015/D-6).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.text import Text

from torve.cli.console import (
    STYLE_ID,
    Format,
    emit_json,
    fail,
    header,
    make_table,
    out,
)
from torve.cli.options import ConfigOption, FormatOption, RootOption, load_config
from torve.config.equipment import Equipment
from torve.domain.states import EXIT_CONFIG, EXIT_INFRASTRUCTURE

# ----------------------- #

equip_app = typer.Typer(
    no_args_is_help=False, help="The equipment cache: warm it, and audit what is in it."
)


def _declared(root: Path, config_path: Path | None, seat: str | None) -> list[Equipment]:
    """Every item this repository's seats and roles declare, deduplicated.

    Both layers (S-0062/D-12): a role's profile is equipment too, and a cache
    warmed for the seats alone would leave every attempt fetching the role's.
    """

    from torve.config.agents import role_equipment
    from torve.config.equipment import merge_equipment

    config = load_config(root, config_path)
    seats = config.tiers if seat is None else {seat: config.tiers[seat]}
    items: list[Equipment] = []

    for tier in seats.values():
        items = merge_equipment(items, tier.equipment)

    for role_items in role_equipment(root).values():
        items = merge_equipment(items, role_items)

    return items


# ....................... #


@equip_app.callback(invoke_without_command=True)
def equip(
    seat: Annotated[
        str | None, typer.Option("--seat", help="One seat's equipment; omit for every seat.")
    ] = None,
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            help=(
                "Audit the cache against what each source recorded instead of "
                "fetching: a directory that does not hold what its key claims is "
                "a finding, and the exit code is 3."
            ),
        ),
    ] = False,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Warm the cache for what this repository declares, and say where each
    item landed. Fetching happens here and never inside a running attempt."""

    from torve.application.equipment import EquipmentError, warm

    root = root.resolve()

    try:
        items = _declared(root, config_path, seat)

    except KeyError:
        raise fail(f"configuration error: no seat named {seat!r}", EXIT_CONFIG) from None

    if check:
        _audit(items, root, fmt)
        return

    try:
        landed = warm(items, root=root)

    except EquipmentError as error:
        raise fail(f"{error}", EXIT_INFRASTRUCTURE) from None

    rows = [
        {"kind": item.kind, "source": item.source, "ref": item.ref, "path": str(where)}
        for item, where in zip(items, landed, strict=True)
    ]

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "items": rows})
        return

    console = out(fmt)
    header(console, "equip", f"{len(rows)} item(s)")

    if not rows:
        console.print(Text("nothing declared", STYLE_ID))
        return

    table = make_table("kind", "source", "ref", "cached")
    for row in rows:
        table.add_row(
            row["kind"],
            Text(row["source"], STYLE_ID),
            Text(row["ref"] or "—", STYLE_ID),
            Text(row["path"], STYLE_ID),
        )

    console.print(table)


def _audit(items: list[Equipment], root: Path, fmt: Format) -> None:
    """S-0062/D-11: what each source recorded, read back from the cache.

    Never a refetch and never a resolution — confirming a directory holds what
    its key says is a different thing from deciding what a version means, which
    is the line the non-goal draws.
    """

    from torve.application.equipment import PIN_FILE, item_path

    findings: list[dict[str, str]] = []

    for item in items:
        where = item_path(item)

        if not where.is_dir():
            findings.append({"source": item.source, "problem": "not fetched"})
            continue

        if item.scheme != "github":
            continue

        pin = where / PIN_FILE
        expected = f"{item.source}@{item.ref}"

        if not pin.is_file():
            findings.append({"source": item.source, "problem": "no recorded pin"})

        elif pin.read_text(encoding="utf-8").strip() != expected:
            findings.append(
                {
                    "source": item.source,
                    "problem": f"holds {pin.read_text(encoding='utf-8').strip()}, not {expected}",
                }
            )

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "checked": len(items), "findings": findings})

    else:
        console = out(fmt)
        header(console, "equip --check", f"{len(items)} item(s)")

        for finding in findings:
            console.print(Text(f"{finding['source']}: {finding['problem']}", STYLE_ID))

        if not findings:
            console.print(Text("every item holds what its key claims", STYLE_ID))

    if findings:
        raise typer.Exit(EXIT_CONFIG)
