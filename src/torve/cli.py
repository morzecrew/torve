"""`torve` — the CLI, under the RFC 0011 contract (Typer plus Rich, D-11.1).

    torve gates run --base origin/main       # all gates
    torve gates run --only scope,acceptance
    torve gates check                        # the sabotage suite
    torve size .torve/tasks/T-0002.yaml

Three surfaces are contractual, everything else is presentation (deferred,
D-11.8): every result-producing command takes `--format json` and emits the
persisted record shape (D-11.2, D-11.3); exit codes 0–5 are the escalation
vocabulary projected per `torve.domain` (D-11.4); results go to stdout and
diagnostics to stderr, never mixed (D-11.6). `--plain` is implied by `CI`,
a non-TTY stdout or `--format json`, and `NO_COLOR` is honoured (D-11.5).

Files resolve under `.torve/` with the legacy root-level names as fallback
(RFC 0013); `--gates` and `--config` are the only overrides (D-13.4). A
malformed manifest or runner configuration exits 3 (D-13.6).
"""

from __future__ import annotations

import json
import os
import sys
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
import yaml
from rich.console import Console

if TYPE_CHECKING:
    from torve.ports import Runtime
    from torve.runconfig import RunnerConfig

import torve
from torve import layout
from torve.context import GitError, build_context, load_task
from torve.domain import (
    EXIT_BY_REASON,
    EXIT_CONFIG,
    EXIT_GATES_RED,
    EXIT_INFRASTRUCTURE,
    EXIT_OK,
    EscalationReason,
    TaskState,
)
from torve.gates import sabotage
from torve.manifest import config_hash, load_manifest
from torve.runner import run_gates
from torve.sizing import StaticThresholds
from torve.telemetry import append_record, build_record

# ----------------------- #

OUTCOME_MARKS = {
    "pass": "✓",
    "flaky": "≈",
    "skipped": "∅",
    "bypassed": "⤳",
    "fail": "✗",
    "error": "!",
}

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Deterministic gates for agent and human pull requests.")
gates_app = typer.Typer(no_args_is_help=True, help="Run or verify the gate set.")
app.add_typer(gates_app, name="gates")


class Format(StrEnum):
    TEXT = "text"
    JSON = "json"


class RuntimeName(StrEnum):
    DOCKER = "docker"
    OPENSANDBOX = "opensandbox"


_plain_flag = False


def _version(value: bool) -> None:
    if value:
        sys.stdout.write(torve.__version__ + "\n")
        raise typer.Exit(EXIT_OK)


@app.callback()
def root_options(
    plain: Annotated[bool, typer.Option(
        "--plain", help="No colour, spinners or live redraw; implied by CI, "
                        "a non-TTY stdout, or --format json.")] = False,
    version: Annotated[bool, typer.Option(
        "--version", callback=_version, is_eager=True,
        help="Print the version and exit.")] = False,
) -> None:
    global _plain_flag
    _plain_flag = plain


def _is_plain(fmt: Format | None = None) -> bool:
    return (_plain_flag or fmt is Format.JSON or bool(os.environ.get("CI"))
            or not sys.stdout.isatty())


def _out(fmt: Format | None = None) -> Console:
    """Human results, stdout. Rich honours NO_COLOR on its own (D-11.5)."""
    return Console(no_color=_is_plain(fmt) or None, highlight=False, markup=False,
                   soft_wrap=True)


def _err() -> Console:
    """Diagnostics, stderr — in both formats (D-11.6)."""
    return Console(stderr=True, no_color=_is_plain() or None, highlight=False,
                   markup=False, soft_wrap=True)


def _emit_json(document: dict[str, object]) -> None:
    """Exactly one JSON document on stdout and nothing else (D-11.6) —
    written raw so no console width ever wraps it."""
    sys.stdout.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")


def _fail(message: str, code: int) -> typer.Exit:
    _err().print(message)
    return typer.Exit(code)


# ....................... #


def _load_config(root: Path, config_path: Path | None) -> RunnerConfig:
    """Configuration errors exit 3 (D-13.6): a bad file is the operator's to
    fix, distinct from red gates (1) and infrastructure failure (4)."""
    from torve.runconfig import load_runner_config

    try:
        return load_runner_config(root, config_path)
    except (ValueError, yaml.YAMLError) as exc:
        raise _fail(f"configuration error: {exc}", EXIT_CONFIG) from exc


def _runtime_for(config: RunnerConfig, override: RuntimeName | None) -> Runtime:
    from torve.adapters.runtime_docker import DockerRuntime
    from torve.adapters.runtime_opensandbox import OpenSandboxRuntime

    adapter = override.value if override else config.runtime.adapter
    if adapter == "docker":
        return DockerRuntime()
    if adapter == "opensandbox":
        return OpenSandboxRuntime(config.runtime.opensandbox)
    raise _fail(f"configuration error: unknown runtime adapter {adapter!r}", EXIT_CONFIG)


ConfigOption = Annotated[Path | None, typer.Option(
    "--config", exists=True, dir_okay=False,
    help="Runner configuration; defaults to .torve/config.yaml, then torve.yaml.")]
RootOption = Annotated[Path, typer.Option(
    "--root", exists=True, file_okay=False, help="Repository root.")]
FormatOption = Annotated[Format, typer.Option(
    "--format", help="text for a person, json for a machine (D-11.2).")]


# ....................... #


@gates_app.command("run")
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
        raise _fail(f"configuration error: no gate manifest at {manifest_path}", EXIT_CONFIG)

    try:
        manifest = load_manifest(manifest_path)
    except (ValueError, yaml.YAMLError) as exc:
        raise _fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    try:
        ctx = build_context(root, manifest, base=base, task_path=task_path)
        selected = {name.strip() for name in only.split(",")} if only else None
        report = run_gates(ctx, only=selected)
    except GitError as exc:
        raise _fail(f"infrastructure failure: {exc}", EXIT_INFRASTRUCTURE) from exc
    except ValueError as exc:
        raise _fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    record = build_record(ctx, report, config_hash(manifest_path, root))
    append_record(root / manifest.telemetry, record)

    if fmt is Format.JSON:
        _emit_json(record)
    else:
        out = _out(fmt)
        task_note = f"task {ctx.task.id}" if ctx.task else "no task (degraded mode)"
        out.print(f"torve gates · {task_note} · config {record['config_hash']}")
        for result in report.results:
            mark = OUTCOME_MARKS.get(result.outcome, "?")
            out.print(f"  {mark} {result.name:<20} {result.outcome:<9} "
                      f"[{result.state}, {result.duration_s:.1f}s]")
            if result.outcome in ("fail", "error") and result.output:
                for line in result.output.splitlines()[:40]:
                    out.print(f"      {line}")
            if result.bypass is not None:
                out.print(f"      bypassed by {result.bypass.author}: {result.bypass.reason}")
        out.print(f"exit {report.exit_code}")
    raise typer.Exit(report.exit_code)


@gates_app.command("check")
def gates_check(fmt: FormatOption = Format.TEXT) -> None:
    """Sabotage suite (D-2.2): a gate that cannot be shown to fail is not a
    check. Applies one deliberately bad diff per gate and asserts red, plus a
    clean twin per gate asserting green."""
    outcomes = sabotage.run_all()
    failed = [o for o in outcomes if not o.ok]
    if fmt is Format.JSON:
        _emit_json({"schema_version": 1, "cases": [o.__dict__ for o in outcomes]})
    else:
        out = _out(fmt)
        for o in outcomes:
            mark = "✓" if o.ok else "✗"
            out.print(f"  {mark} {o.name:<40} expected {o.expected:<8} got {o.got}")
            if not o.ok and o.detail:
                for line in o.detail.splitlines()[:12]:
                    out.print(f"      {line}")
        out.print(f"{len(outcomes) - len(failed)}/{len(outcomes)} sabotage cases behaved")
    raise typer.Exit(EXIT_GATES_RED if failed else EXIT_OK)


@app.command("size")
def size(
    task_file: Annotated[Path, typer.Argument(exists=True)],
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Pre-dispatch size estimate for a task contract (D-2.9)."""
    verdict = StaticThresholds().estimate(load_task(task_file))
    if fmt is Format.JSON:
        _emit_json({"schema_version": 1, "size": verdict.size, "reasons": verdict.reasons})
    else:
        out = _out(fmt)
        out.print(verdict.size)
        for reason in verdict.reasons:
            out.print(f"  - {reason}")
    raise typer.Exit(EXIT_OK if verdict.size == "ok" else EXIT_GATES_RED)


# ....................... #


@app.command("run")
def run_cmd(
    task_id: Annotated[str, typer.Argument()],
    agent_name: Annotated[str, typer.Option(
        "--agent", help="Only 'fake' today; real adapters arrive with RFC 0004.")] = "fake",
    scenario: Annotated[Path | None, typer.Option(
        exists=True,
        help="FakeAgent scenario YAML; default writes one marker file and exits 0.")] = None,
    runtime_name: Annotated[RuntimeName | None, typer.Option(
        "--runtime", help="Override the configured runtime adapter.")] = None,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Run one task synchronously; the exit code is the outcome (RFC 0003,
    projected onto codes 0–5 per D-11.4)."""
    from torve.adapters.agent_fake import FakeAgent, load_scenario
    from torve.adapters.vcs_git import GhScm, GitVcs, NullScm
    from torve.adapters.workspace_git import GitWorkspace
    from torve.run import RunDeps, run_task

    if agent_name != "fake":
        raise _fail(f"configuration error: unknown agent {agent_name!r}", EXIT_CONFIG)
    root = root.resolve()
    task_file = layout.task_file(root, task_id)
    if not task_file.is_file():
        raise _fail(f"configuration error: no task contract at {task_file}", EXIT_CONFIG)
    task = load_task(task_file)
    config = _load_config(root, config_path)

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
        raise _fail(f"infrastructure failure: {exc}", EXIT_INFRASTRUCTURE) from exc

    if fmt is Format.JSON:
        _emit_json(state.to_record())
    else:
        out = _out(fmt)
        out.print(f"{task.id}: {state.state} after {state.attempts} attempt(s)")
        if state.escalation is not None:
            out.print(f"  escalated: {state.escalation.reason} — {state.escalation.detail}")
        for event in state.history[-4:]:
            out.print(f"  {event['from']} -> {event['to']}: {event['fact']}")

    if state.state is TaskState.READY:
        raise typer.Exit(EXIT_OK)
    if state.escalation is not None:
        reason = EscalationReason(state.escalation.reason)
        raise typer.Exit(EXIT_BY_REASON[reason])
    raise typer.Exit(EXIT_GATES_RED)


@app.command("cancel")
def cancel(
    task_id: Annotated[str, typer.Argument()],
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Ask a running task to stop — cooperative on the ask, fenced on the
    landing. Fails closed when the store backend cannot deliver a cancel."""
    import asyncio

    from torve import naming
    from torve.adapters.durable_store import open_store
    from torve.runstate import RunState
    from torve.taskstore import TaskStore

    root = root.resolve()
    state_path = naming.state_file(root, task_id)
    if not state_path.exists():
        raise _fail(f"configuration error: no run state for {task_id}", EXIT_CONFIG)
    state = RunState.load(state_path)
    run_id = state.durable_run_id
    if not run_id:
        raise _fail(f"configuration error: {task_id} has no durable run to cancel",
                    EXIT_CONFIG)

    config = _load_config(root, config_path)

    async def _cancel() -> bool:
        taskstore = TaskStore(await open_store(config.store), config.store)
        return await taskstore.request_cancel(run_id)

    try:
        recorded = asyncio.run(_cancel())
    except Exception as exc:
        raise _fail(f"infrastructure failure: {exc}", EXIT_INFRASTRUCTURE) from exc
    if fmt is Format.JSON:
        _emit_json({"schema_version": 1, "task_id": task_id, "recorded": recorded})
    else:
        _out(fmt).print(
            "cancel recorded — the holder observes it on the next lease renewal"
            if recorded else "nothing to stop (run already terminal or ask refused)")


@app.command("migrate")
def migrate_cmd(
    target: Annotated[str | None, typer.Argument(
        help="torve | substrate | telemetry")] = None,
    apply_all: Annotated[bool, typer.Option(
        "--all", help="Apply every target's pending steps.")] = False,
    show_status: Annotated[bool, typer.Option(
        "--status", help="Available and applied steps per target, plus the forze pin.")]
    = False,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
) -> None:
    """Owner-grouped, forward-only SQL migrations (rfcs/0012-migrations.md).

    Three histories — torve, substrate (pinned to a forze version), telemetry
    (stage 3+) — each with its own version counter. No downgrade exists.
    `--status` is this command's preview; there is no partial dry run of a
    forward-only history."""
    from torve.adapters.durable_store import resolve_dsn
    from torve.migrate import MigrateError, apply
    from torve.migrate import status as migrate_status

    all_targets = ["torve", "substrate", "telemetry"]
    config = _load_config(root.resolve(), config_path)
    out = _out()
    try:
        if show_status:
            dsn = None
            if config.store.adapter == "postgres":
                import contextlib

                with contextlib.suppress(RuntimeError):
                    dsn = resolve_dsn(config.store)
            for line in migrate_status(dsn):
                out.print(line)
            return
        if apply_all:
            targets = all_targets
        elif target is None:
            raise _fail("configuration error: give a target, --all, or --status",
                        EXIT_CONFIG)
        elif target not in all_targets:
            raise _fail(f"configuration error: unknown target {target!r}", EXIT_CONFIG)
        else:
            targets = [target]
        dsn = resolve_dsn(config.store)
        for name in targets:
            applied = apply(name, dsn)
            out.print(f"{name}: {applied} step(s) applied")
    except MigrateError as exc:
        raise _fail(str(exc), exc.exit_code) from exc
    except RuntimeError as exc:
        raise _fail(f"infrastructure failure: {exc}", EXIT_INFRASTRUCTURE) from exc


@app.command("doctor")
def doctor(fmt: FormatOption = Format.TEXT) -> None:
    """Preflight checks. Today: the forze pin (D-12.7) — a schema mismatch
    must be a check, not a symptom discovered through adapter behaviour. A
    failed check is a configuration error (exit 3), not a red gate."""
    from torve.migrate import check_forze_pin

    ok, message = check_forze_pin()
    if fmt is Format.JSON:
        _emit_json({"schema_version": 1, "ok": ok,
                    "checks": [{"name": "forze-pin", "ok": ok, "detail": message}]})
    else:
        _out(fmt).print(("ok    " if ok else "FAIL  ") + message)
    raise typer.Exit(EXIT_OK if ok else EXIT_CONFIG)


@app.command("status")
def status(
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Run states from the .wt/ state files."""
    from torve import naming
    from torve.runstate import RunState

    states = RunState.load_all(root.resolve() / naming.WORKTREE_DIR)
    if fmt is Format.JSON:
        _emit_json({"schema_version": 1, "runs": [s.to_record() for s in states]})
        return
    out = _out(fmt)
    if not states:
        out.print("no runs")
        return
    for state in states:
        line = (f"{state.task_id:<8} {state.state:<10} attempts={state.attempts} "
                f"heartbeat={state.heartbeat_age_s():.0f}s ago")
        if state.escalation is not None:
            line += f"  [{state.escalation.reason}: {state.escalation.detail}]"
        out.print(line)


@app.command("reap")
def reap_cmd(
    force: Annotated[bool, typer.Option(
        "--force",
        help="Treat every non-terminal run as orphaned regardless of heartbeat age.")]
    = False,
    dry_run: Annotated[bool, typer.Option(
        "--dry-run", help="Report what would be swept without touching anything; "
                          "durable lease expiry cannot be predicted and is not shown.")]
    = False,
    runtime_name: Annotated[RuntimeName | None, typer.Option("--runtime")] = None,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Sweep orphaned sandboxes and worktrees, by convention (RFC 0003 §4.2)."""
    from torve.adapters.workspace_git import GitWorkspace
    from torve.reaper import reap

    root = root.resolve()
    config = _load_config(root, config_path)
    report = reap(root, config, _runtime_for(config, runtime_name), GitWorkspace(root),
                  force=force, dry_run=dry_run)
    if fmt is Format.JSON:
        _emit_json({"schema_version": 1, "dry_run": dry_run,
                    "sandboxes_destroyed": report.sandboxes_destroyed,
                    "runs_expired": report.runs_expired,
                    "worktrees_removed": report.worktrees_removed})
        return
    out = _out(fmt)
    tense = "would be " if dry_run else ""
    for label, names in (("sandboxes destroyed", report.sandboxes_destroyed),
                         ("runs expired", report.runs_expired),
                         ("worktrees removed", report.worktrees_removed)):
        detail = f" ({', '.join(names)})" if names else ""
        out.print(f"{tense}{label}: {len(names)}{detail}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
