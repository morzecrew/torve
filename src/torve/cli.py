"""`torve` — the gates-library CLI (RFC 0002 §2).

    torve gates run --base origin/main       # all gates
    torve gates run --only scope,acceptance
    torve gates check                        # the sabotage suite
    torve size tasks/T-0002.yaml
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

import torve
from torve import sabotage
from torve.context import GitError, build_context, load_task
from torve.manifest import config_hash, load_manifest
from torve.runner import run_gates
from torve.sizing import StaticThresholds
from torve.telemetry import append_record, build_record

OUTCOME_MARKS = {
    "pass": "✓",
    "flaky": "≈",
    "skipped": "∅",
    "bypassed": "⤳",
    "fail": "✗",
    "error": "!",
}


@click.group()
@click.version_option(version=torve.__version__)
def main() -> None:
    """Deterministic gates for agent and human pull requests."""


@main.group()
def gates() -> None:
    """Run or verify the gate set."""


@gates.command("run")
@click.option("--base", default=None, help="Base ref; defaults to origin/main, then main.")
@click.option("--only", default=None, help="Comma-separated gate names.")
@click.option("--task", "task_path", type=click.Path(exists=True, path_type=Path), default=None,
              help="Task contract; defaults to tasks/<id>.yaml for a torve/T-nnnn branch.")
@click.option("--manifest", "manifest_path", type=click.Path(path_type=Path),
              default=Path("gates.yaml"), show_default=True)
@click.option("--root", type=click.Path(exists=True, file_okay=False, path_type=Path),
              default=Path("."), show_default=True)
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text",
              show_default=True)
def gates_run(base: str | None, only: str | None, task_path: Path | None,
              manifest_path: Path, root: Path, fmt: str) -> None:
    """Run the gates; the exit code is the outcome."""
    root = root.resolve()
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    if not manifest_path.is_file():
        raise click.ClickException(f"no manifest at {manifest_path}")

    try:
        manifest = load_manifest(manifest_path)
        ctx = build_context(root, manifest, base=base, task_path=task_path)
        selected = {name.strip() for name in only.split(",")} if only else None
        report = run_gates(ctx, only=selected)
    except (GitError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    record = build_record(ctx, report, config_hash(manifest_path, root))
    append_record(root / manifest.telemetry, record)

    if fmt == "json":
        click.echo(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        task_note = f"task {ctx.task.id}" if ctx.task else "no task (degraded mode)"
        click.echo(f"torve gates · {task_note} · config {record['config_hash']}")
        for result in report.results:
            mark = OUTCOME_MARKS.get(result.outcome, "?")
            block = "blocking" if result.blocking else "non-blocking"
            click.echo(f"  {mark} {result.name:<20} {result.outcome:<9} "
                       f"[{block}, {result.duration_s:.1f}s]")
            if result.outcome in ("fail", "error") and result.output:
                for line in result.output.splitlines()[:40]:
                    click.echo(f"      {line}")
            if result.bypass is not None:
                click.echo(f"      bypassed by {result.bypass.author}: {result.bypass.reason}")
        click.echo(f"exit {report.exit_code}")
    sys.exit(report.exit_code)


@gates.command("check")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text",
              show_default=True)
def gates_check(fmt: str) -> None:
    """Sabotage suite (D-2.2): a gate that cannot be shown to fail is not a
    check. Applies one deliberately bad diff per gate and asserts red, plus a
    clean twin per gate asserting green."""
    outcomes = sabotage.run_all()
    failed = [o for o in outcomes if not o.ok]
    if fmt == "json":
        click.echo(json.dumps([o.__dict__ for o in outcomes], indent=2))
    else:
        for o in outcomes:
            mark = "✓" if o.ok else "✗"
            click.echo(f"  {mark} {o.name:<40} expected {o.expected:<8} got {o.got}")
            if not o.ok and o.detail:
                for line in o.detail.splitlines()[:12]:
                    click.echo(f"      {line}")
        click.echo(f"{len(outcomes) - len(failed)}/{len(outcomes)} sabotage cases behaved")
    sys.exit(1 if failed else 0)


@main.command("size")
@click.argument("task_file", type=click.Path(exists=True, path_type=Path))
def size(task_file: Path) -> None:
    """Pre-dispatch size estimate for a task contract (D-2.9)."""
    verdict = StaticThresholds().estimate(load_task(task_file))
    click.echo(verdict.size)
    for reason in verdict.reasons:
        click.echo(f"  - {reason}")
    sys.exit(0 if verdict.size == "ok" else 1)


if __name__ == "__main__":
    main()
