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
from torve.cli.console import Format, emit_json, fail, out
from torve.cli.options import FormatOption, RootOption, load_config
from torve.config import layout
from torve.config.manifest import load_manifest
from torve.domain.states import EXIT_CONFIG, EXIT_GATES_RED, EXIT_INFRASTRUCTURE, EXIT_OK
from torve.gates import sabotage
from torve.gates.context import GitError, build_context, load_task
from torve.gates.runner import run_gates

# ----------------------- #

OUTCOME_MARKS = {
    "pass": "✓",
    "flaky": "≈",
    "skipped": "∅",
    "bypassed": "⤳",
    "fail": "✗",
    "error": "!",
}


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
        console.print(f"torve gates · {task_note} · config {record['config_hash']}")
        for result in report.results:
            mark = OUTCOME_MARKS.get(result.outcome, "?")
            console.print(f"  {mark} {result.name:<20} {result.outcome:<9} "
                          f"[{result.state}, {result.duration_s:.1f}s]")
            if result.outcome in ("fail", "error") and result.output:
                for line in result.output.splitlines()[:40]:
                    console.print(f"      {line}")
            if result.bypass is not None:
                console.print(
                    f"      bypassed by {result.bypass.author}: {result.bypass.reason}")
        console.print(f"exit {report.exit_code}")
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
            mark = "✓" if o.ok else "✗"
            console.print(f"  {mark} {o.name:<40} expected {o.expected:<8} got {o.got}")
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
