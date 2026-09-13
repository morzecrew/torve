"""`torve brief <contract>` — what dispatch settles before an agent starts,
printed instead of enforced (S-0067/D-1): the contract lint, the size
estimate, the standing rows this scope crosses that the contract has not
inherited (S-0067/D-2), the context pack written where an attempt would find
it, and the battery with its blocking axes named.

It refuses nothing and exits zero on a contract that lints red: a
hand-minted contract is already signed by the person reading the output
(S-0030/D-4), which is the same ground the lint's own advisories stand on.

Parsing and rendering only (S-0015/D-6, S-0055/D-28) — every fact here is
computed by the application modules dispatch itself calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.text import Text

from torve.cli.console import (
    STYLE_DIM,
    STYLE_FAIL,
    STYLE_PASS,
    STYLE_WARN,
    Format,
    closing,
    emit_json,
    fail,
    footer,
    header,
    make_table,
    out,
)
from torve.cli.options import ConfigOption, FormatOption, RootOption, load_config
from torve.config import layout
from torve.domain.states import EXIT_CONFIG, EXIT_OK
from torve.gates.context import load_task

# ----------------------- #


def brief_cmd(
    contract: Annotated[Path, typer.Argument(help="A contract.yaml to brief a session on.")],
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Everything dispatch settles before an agent starts, printed: the
    contract lint, the size estimate, the rows this scope crosses that the
    contract has not inherited, the context pack written where an attempt
    would find it, and the battery with its blocking axes. Refuses nothing
    and exits zero on a red lint — a hand-minted contract is already signed
    by whoever is reading this.
    """

    from torve.application.contextpack import build as build_pack
    from torve.application.contextpack import materialize as materialize_pack
    from torve.application.intake import lint_contract, standing_warnings
    from torve.application.sizing import estimate

    root = root.resolve()
    config = load_config(root, config_path)

    if not contract.is_file():
        raise fail(f"configuration error: no contract at {contract}", EXIT_CONFIG)

    try:
        task = load_task(contract)
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    specs = root / config.specs.path
    errors = lint_contract(root, contract)
    uninherited = standing_warnings(root, contract, specs)
    verdict = estimate(task)
    # The pack an attempt reads is the pack a session gets, built by the same
    # function dispatch calls and written where the prompt says it is
    # (S-0054/D-10) — the battery is read back out of it rather than recomputed.
    files = build_pack(root, specs, task, layout.gates_file(root))
    pack = materialize_pack(root, files)
    battery = _battery(files)

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "contract": str(contract),
                "task": task.id,
                "lint": {"ok": not errors, "errors": errors},
                "size": {"size": verdict.size, "reasons": verdict.reasons},
                "uninherited": uninherited,
                "pack": str(pack),
                "gates": battery,
            }
        )

        raise typer.Exit(EXIT_OK)

    _render(task.id, errors, verdict.size, verdict.reasons, uninherited, pack, battery, fmt)

    raise typer.Exit(EXIT_OK)


# ....................... #


def _battery(files: dict[str, str]) -> list[dict[str, Any]]:
    payload: dict[str, Any] = json.loads(files["gates.json"])
    gates: list[dict[str, Any]] = payload.get("gates") or []

    return gates


# ....................... #


def _render(
    task_id: str,
    errors: list[str],
    size: str,
    reasons: list[str],
    uninherited: list[str],
    pack: Path,
    battery: list[dict[str, Any]],
    fmt: Format,
) -> None:
    console = out(fmt)
    header(console, "brief", task_id)

    console.print(
        Text("lint: green", STYLE_PASS)
        if not errors
        else Text(f"lint: {len(errors)} refusal(s) — advisory here, nothing is refused", STYLE_WARN)
    )

    for error in errors:
        console.print(Text(f"  {error}", STYLE_DIM))

    console.print(
        Text(f"size: {size}", STYLE_PASS if size == "ok" else STYLE_WARN),
    )

    for reason in reasons:
        console.print(Text(f"  {reason}", STYLE_DIM))

    if uninherited:
        console.print(Text(f"uninherited rows over this scope: {len(uninherited)}", STYLE_WARN))

        for row in uninherited:
            console.print(Text(f"  {row}", STYLE_DIM))
    else:
        console.print(Text("uninherited rows over this scope: none", STYLE_PASS))

    console.print(Text(f"pack: {pack}", STYLE_DIM))
    console.print()

    table = make_table("gate", "axis", "state", "convicts on", title="Battery")

    for gate in battery:
        state = str(gate.get("state", ""))
        table.add_row(
            str(gate.get("name", "")),
            str(gate.get("axis", "")),
            Text(state, STYLE_FAIL if state == "blocking" else STYLE_DIM),
            str(gate.get("convicts_on", "")),
        )

    console.print(table)

    axes = sorted({str(g.get("axis", "")) for g in battery if g.get("state") == "blocking"})

    if axes:
        footer(console, f"blocking axes: {', '.join(axes)}")

    closing(console, "briefed — nothing here refuses the work")
