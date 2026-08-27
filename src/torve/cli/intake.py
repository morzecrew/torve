"""`torve intake` / `torve adopt` / `torve lint-contract` — RFC 0020's
phase-1 face. Parsing and rendering only (D-15.6); the drafting run, the
lint and adoption live in `torve.application.intake`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from torve.cli.console import (
    STYLE_DIM as DIM,
)
from torve.cli.console import (
    STYLE_ID as ID,
)
from torve.cli.console import (
    Format,
    closing,
    emit_json,
    fail,
    header,
    out,
    styled,
)
from torve.cli.options import (
    ConfigOption,
    FormatOption,
    RootOption,
    RuntimeName,
    load_config,
    runtime_for,
)
from torve.domain.states import EXIT_CONFIG, EXIT_ESCALATED, EXIT_GATES_RED, EXIT_OK

# ----------------------- #


def intake_cmd(
    request: Annotated[str, typer.Argument(
        help="The request, in prose — what should exist and why.")],
    rfc: Annotated[str | None, typer.Option(
        "--rfc", help="Governing document; its decisions are copied at "
        "adoption (accepted documents only).")] = None,
    runtime_name: Annotated[RuntimeName | None, typer.Option("--runtime")] = None,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Run the drafter against a request: a sandboxed run over a read-only
    worktree at base whose gate is the contract lint. Green persists drafts
    awaiting `torve adopt`; a spent budget escalates."""
    from torve.adapters.vcs.git import GitLane, GitVcs
    from torve.application.intake import mint_intake_task, run_intake
    from torve.application.telemetry import config_hash
    from torve.cli.run import build_tier_agent
    from torve.config import layout
    from torve.gates.context import resolve_base

    root = root.resolve()
    config = load_config(root, config_path)
    try:
        agent = build_tier_agent(config, root, "planner")
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc
    runtime = runtime_for(config, runtime_name)
    vcs = GitVcs()
    base_sha = GitLane().tip(root, resolve_base(root, config.base) or "HEAD")
    if base_sha is None:
        raise fail("configuration error: no base tip to draft against", EXIT_CONFIG)

    from torve.base import naming

    task = mint_intake_task(root, request, config, rfc=rfc)
    workdir = root / naming.WORKTREE_DIR / f"{task.id}.intake"
    vcs.worktree_at(root, base_sha, workdir)
    try:
        digest = config_hash(layout.gates_file(root), root, config)
        outcome = run_intake(root, workdir, task, config, runtime, agent, digest)
    finally:
        vcs.remove_worktree(root, workdir)

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "task": outcome.task_id,
                   "fact": outcome.fact, "attempts": outcome.attempts,
                   "rationale": outcome.rationale,
                   "drafts": [d.model_dump() for d in outcome.drafts],
                   "lint_errors": outcome.lint_errors,
                   "unparseable": outcome.unparseable})
        raise typer.Exit(EXIT_OK if outcome.drafts else EXIT_ESCALATED)
    console = out(fmt)
    header(console, "intake", outcome.task_id)
    console.print(outcome.fact)
    for draft in outcome.drafts:
        console.print(f"\n{styled(draft.ref, ID)}: {draft.intent}")
        console.print(styled(f"  allow: {', '.join(draft.scope.allow)}", DIM))
        console.print(styled(f"  acceptance: {'; '.join(draft.acceptance)}", DIM))
        if draft.depends_on:
            console.print(styled(f"  depends_on: {', '.join(draft.depends_on)}", DIM))
    if outcome.rationale:
        console.print(f"\n{outcome.rationale}")
    if outcome.drafts:
        closing(console, f"adopt with: torve adopt {outcome.task_id}")
        raise typer.Exit(EXIT_OK)
    for error in outcome.lint_errors:
        console.print(styled(f"  {error}", DIM))
    closing(console, "escalated — retry after amending the request")
    raise typer.Exit(EXIT_ESCALATED)


def adopt_cmd(
    task_id: Annotated[str, typer.Argument(
        help="The drafting run whose drafts to adopt.")],
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """The human signature: mint ids under the engine lock, rewrite
    draft refs, commit the contracts as engine records. The loop
    dispatches them like hand-minted work."""
    from torve.application.intake import adopt

    root = root.resolve()
    config = load_config(root, config_path)
    try:
        adopted = adopt(root, task_id, config)
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc
    except RuntimeError as exc:
        raise fail(str(exc), EXIT_ESCALATED) from exc
    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "source": task_id, "adopted": adopted})
    else:
        console = out(fmt)
        header(console, "adopt", task_id)
        closing(console, f"adopted: {', '.join(adopted)}")
    raise typer.Exit(EXIT_OK)


def lint_contract_cmd(
    contract: Annotated[Path, typer.Argument(
        help="A contract.yaml to lint against the tree.")],
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """The standalone lint — the same mechanical protection a drafted
    contract gets, for the hand-minted path."""
    from torve.application.intake import lint_contract

    root = root.resolve()
    if not contract.is_file():
        raise fail(f"configuration error: no contract at {contract}", EXIT_CONFIG)
    errors = lint_contract(root, contract)
    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "contract": str(contract),
                   "ok": not errors, "errors": errors})
        raise typer.Exit(EXIT_OK if not errors else EXIT_GATES_RED)
    console = out(fmt)
    header(console, "lint-contract", contract.name)
    if not errors:
        closing(console, "lint green")
        raise typer.Exit(EXIT_OK)
    for error in errors:
        console.print(error)
    closing(console, f"{len(errors)} refusal(s)")
    raise typer.Exit(EXIT_GATES_RED)
