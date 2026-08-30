"""`torve fleet` — the operator-side manifest and one pass over every root
(RFC 0024). Parsing, manifest resolution and per-root wiring only
(D-15.6); survey, the shared pause decision, deterministic order and
failure-recorded continuation live in `torve.application.fleet`.

Wiring one root's `TickDeps` mirrors `torve.cli.tick` — a fleet is many
solo ticks under one shared decision, not a new execution regime. The
lane leg lands and republishes exactly as a solo tick does; the PR
retirement and captured-thread-reply cosmetics a solo tick also performs
are not reproduced here.
ponytail: the trimmed corner is display-only forge bookkeeping (closing a
pull request's own comment thread, replying to captured review threads) —
landings, pushes and republishing all still happen. Upgrade path: extract
that block out of `torve.cli.tick` into a shared helper once a second
caller (this one) needs it, then call it from both.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

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
    header,
    make_table,
    out,
)
from torve.cli.options import FormatOption
from torve.config.fleet import default_manifest_path, load_fleet_manifest
from torve.domain.states import EXIT_CONFIG, EXIT_OK

if TYPE_CHECKING:
    from torve.application.loop import TickDeps, TickReport
    from torve.config.fleet import FleetManifest, FleetRepository
    from torve.config.runconfig import RunnerConfig

# ----------------------- #

fleet_app = typer.Typer(
    no_args_is_help=True,
    help="Run the standing loop over every repository in the operator's manifest.",
)

ManifestOption = Annotated[
    Path | None,
    typer.Option(
        "--manifest",
        dir_okay=False,
        help="Fleet manifest; defaults to ~/.config/torve/fleet.yaml.",
    ),
]


# ....................... #


def _load_manifest(manifest_path: Path | None) -> FleetManifest:
    import yaml

    path = manifest_path or default_manifest_path()

    if not path.is_file():
        raise fail(
            f"no fleet manifest at {path} — set --manifest or write one",
            EXIT_CONFIG,
        )

    try:
        return load_fleet_manifest(path)

    except (ValueError, yaml.YAMLError) as exc:
        raise fail(f"fleet manifest error: {exc}", EXIT_CONFIG) from exc


# ....................... #


def _build_deps(root: Path, config: RunnerConfig) -> TickDeps:
    """One root's `TickDeps` — reap, poll/intake/sync when a tracker is
    configured, dispatch, and the lane when auto_merge is on. The wiring a
    solo tick (`torve.cli.tick`) already does, rebuilt here because that
    module is not this task's to change and a fleet root's tick needs the
    same legs a solo tick gives it."""

    from torve.adapters.vcs.git import GhCi, GhScm, GitLane, GitVcs, NullScm
    from torve.adapters.workspace.git import GitWorkspace
    from torve.application.loop import TickDeps
    from torve.cli.options import runtime_for

    vcs = GitVcs()
    workspace = GitWorkspace(root)
    ci = None

    if config.promotion.require_ci:
        if not config.scm.repo:
            raise ValueError(
                "promotion.require_ci needs scm.repo to name the remote whose verdict counts"
            )

        ci = GhCi(config.scm.repo, config.scm.token_env)

    def reap_leg() -> tuple[str, bool]:
        from torve.adapters.store.durable import open_store
        from torve.application.reaper import reap

        report = reap(
            root,
            config,
            runtime_for(config, None),
            workspace,
            store=open_store,
            landed=lambda t: bool(vcs.landed_shas(root, t)),
        )
        swept = (
            len(report.sandboxes_destroyed)
            + len(report.worktrees_removed)
            + len(report.runs_expired)
            + len(report.states_removed)
        )

        return (f"swept {swept} artefact(s)" if swept else "nothing to sweep", swept > 0)

    def _capture_for_revision(task_id: str) -> str:
        from torve.application.feedback import capture_feedback
        from torve.base import naming

        branch = naming.branch(task_id)

        if not (config.review.feedback_from and config.scm.repo):
            return "branch kept; revision loop off"

        scm = GhScm(config.scm.repo, config.scm.token_env)

        try:
            threads = scm.review_threads(branch, tuple(config.review.feedback_from))
            diff = (
                vcs.diff(root, config.base or "origin/main", branch)
                if GitLane().tip(root, branch)
                else ""
            )
            captured = capture_feedback(root, task_id, diff, threads)

        except RuntimeError as exc:
            return f"branch kept; feedback capture failed: {exc}"

        return "branch kept; feedback captured" if captured else "branch kept; nothing to capture"

    poll_leg = None
    sync_leg = None
    intake_leg_fn = None

    if config.tracker.kind == "github-issues" and config.tracker.repo:
        from torve.adapters.tracker.github import GithubIssues
        from torve.application.tracker import poll_and_apply, project, relay_to_tracker

        board = GithubIssues(config.tracker.repo, config.tracker.token_env)

        def _approve_tip(task_id: str) -> str | None:
            from torve.base import naming

            return GitLane().tip(root, naming.branch(task_id))

        def _adopt_drafts(task_id: str) -> list[str]:
            from torve.application.intake import adopt

            return adopt(root, task_id, config, assume_lock=True)

        def _draft_feedback(task_id: str, text: str) -> str:
            import re as _re

            from torve.application.feedback import feedback_file

            body = _re.sub(r"^/torve\s+[a-z]+\s*$", "", text, flags=_re.MULTILINE).strip()
            target = feedback_file(root, task_id)
            target.parent.mkdir(parents=True, exist_ok=True)

            if body:
                target.write_text(body + "\n", encoding="utf-8")
                return "thread feedback captured"

            target.unlink(missing_ok=True)

            return "no feedback text — the drafter re-runs on the request"

        def _intake() -> tuple[str, bool]:
            from torve.adapters.broker import build_broker
            from torve.application.intake import IntakeDeps, intake_leg
            from torve.application.telemetry import config_hash
            from torve.cli.run import build_tier_agent
            from torve.config import layout
            from torve.gates.context import resolve_base

            deps = IntakeDeps(
                tracker=board,
                runtime=runtime_for(config, None),
                agent_factory=lambda: build_tier_agent(config, root, "planner"),
                worktree_at=vcs.worktree_at,
                remove_worktree=vcs.remove_worktree,
                base_tip=lambda: GitLane().tip(root, resolve_base(root, config.base) or "HEAD"),
                config_digest=config_hash(layout.gates_file(root), root, config),
                broker=build_broker(config.broker),
            )

            return intake_leg(root, config, deps, tuple(config.tracker.commanders))

        intake_leg_fn = _intake

        def _poll() -> tuple[str, bool]:
            report = poll_and_apply(
                root,
                board,
                tuple(config.tracker.commanders),
                _capture_for_revision,
                _approve_tip,
                _adopt_drafts,
                _draft_feedback,
            )

            if not report.outcomes:
                return ("no commands on the board", False)

            applied = sum(o.applied for o in report.outcomes)

            return (f"{applied} applied of {len(report.outcomes)} command(s)", applied > 0)

        def _sync() -> tuple[str, bool]:
            from torve.application.tracker import project_landings

            staged = project(root, config.tracker.notify)
            staged += project_landings(root, lambda t: bool(vcs.landed_shas(root, t)))
            report = relay_to_tracker(root, board)

            return (f"staged {staged}, delivered {len(report.delivered)}", bool(report.delivered))

        poll_leg, sync_leg = _poll, _sync

    def _dispatch_one(task_id: str, slot_offset: int) -> str:
        from torve.adapters.agent.fake import FakeAgent
        from torve.adapters.agent.harness import HarnessAgent
        from torve.adapters.broker import build_broker
        from torve.adapters.store.durable import open_store
        from torve.adapters.vcs.git import repository_name
        from torve.application.ports import Agent
        from torve.application.runner import RunDeps, run_task
        from torve.cli.run import build_reviewer_agent
        from torve.config.runconfig import route_provider, tier_for
        from torve.gates.context import load_task

        task = load_task(root / ".torve" / "tasks" / task_id / "contract.yaml")
        tier = tier_for(config, task.tier)
        route_provider(config.providers, repository_name(root), tier.provider)
        agent: Agent = FakeAgent(None) if tier.adapter == "fake" else HarnessAgent(tier)

        review_agent = (
            build_reviewer_agent(config, root) if "task_gated" in config.review.on else None
        )

        deps = RunDeps(
            workspace=workspace,
            runtime=runtime_for(config, None),
            agent=agent,
            vcs=vcs,
            scm=(GhScm(config.scm.repo, config.scm.token_env) if config.scm.open_pr else NullScm()),
            store=open_store,
            review_agent=review_agent,
            broker=build_broker(config.broker),
        )

        run_config = (
            config
            if slot_offset == 0
            else config.model_copy(update={"worker_slot": config.worker_slot + slot_offset})
        )

        state = run_task(root, task, run_config, deps)

        return f"{task_id}: {state.state} after {state.attempts} attempt(s)"

    def dispatch_leg(task_ids: list[str]) -> tuple[str, bool]:
        if len(task_ids) == 1:
            return (_dispatch_one(task_ids[0], 0), True)

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(task_ids)) as pool:
            futures = [
                pool.submit(_dispatch_one, task_id, index) for index, task_id in enumerate(task_ids)
            ]
            outcomes: list[str] = []

            for task_id, future in zip(task_ids, futures, strict=True):
                try:
                    outcomes.append(future.result())

                except Exception as exc:
                    outcomes.append(f"{task_id}: error: {exc}")

        return ("; ".join(outcomes), True)

    lane_leg = None

    if config.promotion.auto_merge:
        from torve.application.lane import process_lane

        def _lane() -> tuple[str, bool]:
            lane_vcs = GitLane()
            results = process_lane(
                root,
                lane_vcs,
                ci=ci,
                approvals_required=config.promotion.approvals,
                require_review=config.promotion.require_review,
                quiet_window_s=config.promotion.quiet_window,
                on_conflict=_capture_for_revision,
            )

            if not results:
                return ("no ready candidates", False)

            if config.tracker.kind == "github-issues" and config.tracker.repo:
                from torve.application.tracker import project_approval_gap

                for r in results:
                    if r.action == "approvals short" and r.sha:
                        project_approval_gap(root, r.task, r.sha, config.promotion.approvals)

            landed = sum(1 for r in results if r.action == "landed")
            detail = f"landed {landed} of {len(results)} candidate(s)"

            if landed:
                import os

                from torve.base import naming

                token = os.environ.get(config.scm.token_env) if config.scm.token_env else None
                base = lane_vcs.current_branch(root)
                republished = 0

                for r in results:
                    if r.action == "landed" and r.detail.startswith("rebased"):
                        try:
                            if vcs.republish_branch(root, naming.branch(r.task), token):
                                republished += 1

                        except RuntimeError:
                            pass

                pushed = vcs.push(root, base, token)
                detail += "; base pushed" if pushed else "; no origin to push"

                if republished:
                    detail += f"; {republished} branch(es) republished"

            return (detail, landed > 0)

        lane_leg = _lane

    def landed(task_id: str) -> bool:
        return bool(vcs.landed_shas(root, task_id))

    return TickDeps(
        reap=reap_leg,
        poll=poll_leg,
        dispatch=dispatch_leg,
        lane=lane_leg,
        sync=sync_leg,
        landed=landed,
        intake=intake_leg_fn,
    )


# ....................... #


@fleet_app.command("tick")
def fleet_tick_cmd(
    manifest_path: ManifestOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Survey every root's escalation queue, decide the pause once for the
    fleet, tick each root in the manifest's order under its own lock with
    that decision passed down, and record one fleet event.
    A locked-out or failing root is recorded and the pass continues."""

    from torve.application.fleet import fleet_tick
    from torve.application.loop import run_tick
    from torve.config.runconfig import load_runner_config

    manifest = _load_manifest(manifest_path)

    def tick_one(repo: FleetRepository, paused: bool) -> TickReport:
        root = repo.path
        config = load_runner_config(root)
        deps = _build_deps(root, config)

        return run_tick(root, config, deps, fleet_pause=paused)

    report = fleet_tick(manifest, tick_one)

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "escalated_total": report.escalated_total,
                "paused": report.paused,
                "roots": [asdict(o) for o in report.outcomes],
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "fleet tick", f"{len(report.outcomes)} root(s)")
    table = make_table("root", "trust", "escalated", "outcome")

    for outcome in report.outcomes:
        style = (
            STYLE_FAIL
            if outcome.outcome.startswith("error") or outcome.outcome == "locked out"
            else (STYLE_DIM if outcome.noop else STYLE_PASS)
        )
        table.add_row(
            outcome.root, outcome.trust, str(outcome.escalated), Text(outcome.outcome, style)
        )

    console.print(table)
    closing(
        console,
        f"fleet-wide pause {'in force' if report.paused else 'not in force'} "
        f"(queue at {report.escalated_total})",
        STYLE_WARN if report.paused else STYLE_DIM,
    )
    raise typer.Exit(EXIT_OK)


# ....................... #


@fleet_app.command("status")
def fleet_status_cmd(
    manifest_path: ManifestOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Every root's escalation queue in one table, oldest first — the
    primary alert, in its fleet form. Read-only:
    nothing here writes, and there is no fleet store to read from instead
    of the roots themselves."""

    from torve.application.fleet import fleet_escalations

    manifest = _load_manifest(manifest_path)
    rows = fleet_escalations(manifest)

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "escalations": [asdict(r) for r in rows]})
        return

    console = out(fmt)

    if not rows:
        console.print("no escalations across the fleet")
        return

    header(console, "fleet status", f"{len(rows)} escalation(s)")
    table = make_table("root", "task", "reason", "detail", "age")

    for row in rows:
        table.add_row(
            row.root, row.task_id, row.reason, row.detail, Text(f"{row.age_s:.0f}s ago", STYLE_DIM)
        )

    console.print(table)
