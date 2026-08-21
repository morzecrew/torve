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
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from torve.ports import Runtime
    from torve.runconfig import RunnerConfig

import torve
from torve.context import GitError, build_context, load_task
from torve.gates import sabotage
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


def _runtime_for(config: RunnerConfig, override: str | None) -> Runtime:
    from torve.adapters.runtime_docker import DockerRuntime
    from torve.adapters.runtime_opensandbox import OpenSandboxRuntime

    adapter = override or config.runtime.adapter
    if adapter == "docker":
        return DockerRuntime()
    if adapter == "opensandbox":
        return OpenSandboxRuntime(config.runtime.opensandbox)
    raise click.ClickException(f"unknown runtime adapter {adapter!r}")


@main.command("run")
@click.argument("task_id")
@click.option("--agent", "agent_name", type=click.Choice(["fake"]), default="fake",
              show_default=True, help="Real adapters arrive with RFC 0004.")
@click.option("--scenario", type=click.Path(exists=True, path_type=Path), default=None,
              help="FakeAgent scenario YAML; default writes one marker file and exits 0.")
@click.option("--runtime", "runtime_name", type=click.Choice(["docker", "opensandbox"]),
              default=None, help="Override torve.yaml's runtime adapter.")
@click.option("--root", type=click.Path(exists=True, file_okay=False, path_type=Path),
              default=Path("."), show_default=True)
def run_cmd(task_id: str, agent_name: str, scenario: Path | None,
            runtime_name: str | None, root: Path) -> None:
    """Run one task synchronously; the exit code is the outcome (RFC 0003)."""
    from torve.adapters.agent_fake import FakeAgent, load_scenario
    from torve.adapters.vcs_git import GhScm, GitVcs, NullScm
    from torve.adapters.workspace_git import GitWorkspace
    from torve.run import RunDeps, run_task
    from torve.runconfig import load_runner_config

    root = root.resolve()
    task_file = root / "tasks" / f"{task_id}.yaml"
    if not task_file.is_file():
        raise click.ClickException(f"no task contract at {task_file}")
    task = load_task(task_file)
    config = load_runner_config(root)

    deps = RunDeps(
        workspace=GitWorkspace(root),
        runtime=_runtime_for(config, runtime_name),
        agent=FakeAgent(load_scenario(scenario) if scenario else None),
        vcs=GitVcs(),
        scm=GhScm() if config.scm.open_pr else NullScm(),
    )
    try:
        state = run_task(root, task, config, deps)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"{task.id}: {state.state} after {state.attempts} attempt(s)")
    if state.escalation is not None:
        click.echo(f"  escalated: {state.escalation.reason} — {state.escalation.detail}")
    for event in state.history[-4:]:
        click.echo(f"  {event['from']} -> {event['to']}: {event['fact']}")
    sys.exit(0 if str(state.state) == "ready" else 1)


@main.command("cancel")
@click.argument("task_id")
@click.option("--root", type=click.Path(exists=True, file_okay=False, path_type=Path),
              default=Path("."), show_default=True)
def cancel(task_id: str, root: Path) -> None:
    """Ask a running task to stop — cooperative on the ask, fenced on the
    landing. Fails closed when the store backend cannot deliver a cancel."""
    import asyncio

    from torve import naming
    from torve.adapters.durable_store import open_store
    from torve.runconfig import load_runner_config
    from torve.runstate import RunState
    from torve.taskstore import TaskStore

    root = root.resolve()
    state_path = naming.state_file(root, task_id)
    if not state_path.exists():
        raise click.ClickException(f"no run state for {task_id}")
    state = RunState.load(state_path)
    run_id = state.durable_run_id
    if not run_id:
        raise click.ClickException(f"{task_id} has no durable run to cancel")

    config = load_runner_config(root)

    async def _cancel() -> bool:
        taskstore = TaskStore(await open_store(config.store), config.store)
        return await taskstore.request_cancel(run_id)

    try:
        recorded = asyncio.run(_cancel())
    except Exception as exc:  # noqa: BLE001 — fail-closed capability gate speaks here
        raise click.ClickException(str(exc)) from exc
    click.echo("cancel recorded — the holder observes it on the next lease renewal"
               if recorded else "nothing to stop (run already terminal or ask refused)")


@main.command("migrate")
@click.argument("target", required=False,
                type=click.Choice(["torve", "substrate", "telemetry"]))
@click.option("--all", "apply_all", is_flag=True, help="Apply every target's pending steps.")
@click.option("--status", "show_status", is_flag=True,
              help="Available and applied steps per target, plus the forze pin.")
@click.option("--root", type=click.Path(exists=True, file_okay=False, path_type=Path),
              default=Path("."), show_default=True)
def migrate_cmd(target: str | None, apply_all: bool, show_status: bool, root: Path) -> None:
    """Owner-grouped, forward-only SQL migrations (rfcs/MIGRATIONS.md).

    Three histories — torve, substrate (pinned to a forze version), telemetry
    (stage 3+) — each with its own version counter. No downgrade exists."""
    from torve.adapters.durable_store import resolve_dsn
    from torve.migrate import MigrateError, apply
    from torve.migrate import status as migrate_status
    from torve.runconfig import load_runner_config

    config = load_runner_config(root.resolve())
    try:
        if show_status:
            dsn = None
            if config.store.adapter == "postgres":
                import contextlib

                with contextlib.suppress(RuntimeError):
                    dsn = resolve_dsn(config.store)
            for line in migrate_status(dsn):
                click.echo(line)
            return
        if apply_all:
            targets = ["torve", "substrate", "telemetry"]
        elif target is None:
            raise click.UsageError("give a target, --all, or --status")
        else:
            targets = [target]
        dsn = resolve_dsn(config.store)
        for name in targets:
            applied = apply(name, dsn)
            click.echo(f"{name}: {applied} step(s) applied")
    except MigrateError as exc:
        click.echo(str(exc), err=True)
        sys.exit(exc.exit_code)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("doctor")
def doctor() -> None:
    """Preflight checks. Today: the forze pin (D-M.7) — a schema mismatch
    must be a check, not a symptom discovered through adapter behaviour."""
    from torve.migrate import check_forze_pin

    ok, message = check_forze_pin()
    click.echo(("ok    " if ok else "FAIL  ") + message)
    sys.exit(0 if ok else 1)


@main.command("status")
@click.option("--root", type=click.Path(exists=True, file_okay=False, path_type=Path),
              default=Path("."), show_default=True)
def status(root: Path) -> None:
    """Run states from the .wt/ state files."""
    from torve import naming
    from torve.runstate import RunState

    states = RunState.load_all(root.resolve() / naming.WORKTREE_DIR)
    if not states:
        click.echo("no runs")
        return
    for state in states:
        line = (f"{state.task_id:<8} {state.state:<10} attempts={state.attempts} "
                f"heartbeat={state.heartbeat_age_s():.0f}s ago")
        if state.escalation is not None:
            line += f"  [{state.escalation.reason}: {state.escalation.detail}]"
        click.echo(line)


@main.command("reap")
@click.option("--force", is_flag=True,
              help="Treat every non-terminal run as orphaned regardless of heartbeat age.")
@click.option("--runtime", "runtime_name", type=click.Choice(["docker", "opensandbox"]),
              default=None)
@click.option("--root", type=click.Path(exists=True, file_okay=False, path_type=Path),
              default=Path("."), show_default=True)
def reap_cmd(force: bool, runtime_name: str | None, root: Path) -> None:
    """Sweep orphaned sandboxes and worktrees, by convention (RFC 0003 §4.2)."""
    from torve.adapters.workspace_git import GitWorkspace
    from torve.reaper import reap
    from torve.runconfig import load_runner_config

    root = root.resolve()
    config = load_runner_config(root)
    report = reap(root, config, _runtime_for(config, runtime_name), GitWorkspace(root),
                  force=force)
    for label, names in (("sandboxes destroyed", report.sandboxes_destroyed),
                         ("runs expired", report.runs_expired),
                         ("worktrees removed", report.worktrees_removed)):
        detail = f" ({', '.join(names)})" if names else ""
        click.echo(f"{label}: {len(names)}{detail}")


if __name__ == "__main__":
    main()

