"""`torve gates run/check` and `torve size` — the gates-library commands
(RFC 0002 §2). Parsing and rendering only (D-15.6); the checking lives in
`torve.gates`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import yaml

from torve.application.sizing import StaticThresholds
from torve.application.telemetry import append_record, build_record, config_hash
from torve.cli.console import (
    STYLE_DIM,
    STYLE_FAIL,
    STYLE_PASS,
    STYLE_WARN,
    Format,
    closing,
    emit_json,
    fail,
    failure_detail,
    header,
    make_table,
    mark,
    out,
    styled,
)
from torve.cli.options import FormatOption, RootOption, load_config
from torve.config import layout
from torve.config.manifest import load_manifest
from torve.domain.states import EXIT_CONFIG, EXIT_GATES_RED, EXIT_INFRASTRUCTURE, EXIT_OK
from torve.gates import sabotage
from torve.gates.context import GitError, build_context, load_task
from torve.gates.runner import run_gates

# ----------------------- #

# The verdict marks live in the shared component vocabulary (D-18.5).


def gates_run(
    base: Annotated[str | None, typer.Option(
        help="Base ref; defaults to origin/main, then main.")] = None,
    only: Annotated[str | None, typer.Option(
        help="Comma-separated gate names.")] = None,
    task_path: Annotated[Path | None, typer.Option(
        "--task", exists=True,
        help="Task contract; defaults to .torve/tasks/<id>.yaml for a torve/T-nnnn branch.")]
    = None,
    manifest_path: Annotated[Path | None, typer.Option(
        "--gates", "--manifest",
        help="Gate manifest; defaults to .torve/gates.yaml, then legacy gates.yaml.")] = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Run the gates; the exit code is the outcome."""
    root = root.resolve()
    if manifest_path is None:
        manifest_path = layout.gates_file(root)
    elif not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    if not manifest_path.is_file():
        raise fail(f"configuration error: no gate manifest at {manifest_path}", EXIT_CONFIG)

    try:
        manifest = load_manifest(manifest_path)
    except (ValueError, yaml.YAMLError) as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    try:
        ctx = build_context(root, manifest, base=base, task_path=task_path)
        selected = {name.strip() for name in only.split(",")} if only else None
        report = run_gates(ctx, only=selected)
    except GitError as exc:
        raise fail(f"infrastructure failure: {exc}", EXIT_INFRASTRUCTURE) from exc
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    # The runner configuration joins the hash (RFC 0004 §6, D-4.3): the tier
    # mapping and provider policy are part of the regime a number belongs to.
    record = build_record(
        ctx, report, config_hash(manifest_path, root, load_config(root, None)))
    append_record(root / manifest.telemetry, record)

    if fmt is Format.JSON:
        emit_json(record)
    else:
        console = out(fmt)
        task_note = f"task {ctx.task.id}" if ctx.task else "no task (degraded mode)"
        header(console, "gates run", task_note, str(record["config_hash"]))
        table = make_table("", "gate", "outcome", "state", "duration")
        for result in report.results:
            table.add_row(
                mark(result.outcome), result.name,
                styled(result.outcome, STYLE_FAIL if result.outcome in ("fail", "error")
                       else STYLE_DIM if result.outcome == "skipped" else ""),
                styled(result.state, STYLE_DIM),
                styled(f"{result.duration_s:.1f}s", STYLE_DIM))
        console.print(table)
        for result in report.results:
            if result.outcome in ("fail", "error") and result.output:
                console.print(styled(f"  ✗ {result.name}", STYLE_FAIL))
                failure_detail(console, result.output)
            if result.bypass is not None:
                console.print(styled(
                    f"  ⤳ {result.name} bypassed by {result.bypass.author}: "
                    f"{result.bypass.reason}", STYLE_WARN))
        closing(console, f"exit {report.exit_code}",
                STYLE_PASS if report.exit_code == 0 else STYLE_FAIL)
    raise typer.Exit(report.exit_code)


def gates_check(fmt: FormatOption = Format.TEXT) -> None:
    """Sabotage suite (D-2.2): a gate that cannot be shown to fail is not a
    check. Applies one deliberately bad diff per gate and asserts red, plus a
    clean twin per gate asserting green."""
    outcomes = sabotage.run_all()
    failed = [o for o in outcomes if not o.ok]
    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "cases": [o.__dict__ for o in outcomes]})
    else:
        console = out(fmt)
        for o in outcomes:
            verdict = mark("pass" if o.ok else "fail")
            console.print(verdict + styled(
                f" {o.name:<40} expected {o.expected:<8} got {o.got}", ""))
            if not o.ok and o.detail:
                for line in o.detail.splitlines()[:12]:
                    console.print(f"      {line}")
        console.print(f"{len(outcomes) - len(failed)}/{len(outcomes)} sabotage cases behaved")
    raise typer.Exit(EXIT_GATES_RED if failed else EXIT_OK)


def size(
    task_file: Annotated[Path, typer.Argument(exists=True)],
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Pre-dispatch size estimate for a task contract (D-2.9)."""
    verdict = StaticThresholds().estimate(load_task(task_file))
    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "size": verdict.size, "reasons": verdict.reasons})
    else:
        console = out(fmt)
        console.print(verdict.size)
        for reason in verdict.reasons:
            console.print(f"  - {reason}")
    raise typer.Exit(EXIT_OK if verdict.size == "ok" else EXIT_GATES_RED)
