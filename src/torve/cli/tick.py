"""`torve tick` — one bounded pass of the standing loop (RFC 0019).
Parsing and wiring only (D-15.6); order, lock, pause and selection live in
`torve.application.loop`. Cadence belongs to the environment: schedule
this verb with cron, a CI schedule, or a timer — there is no daemon.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import typer

from torve.cli.console import (
    STYLE_DIM,
    Format,
    closing,
    emit_json,
    fail,
    header,
    make_table,
    out,
)
from torve.cli.options import ConfigOption, FormatOption, RootOption, load_config
from torve.domain.states import EXIT_CONFIG, EXIT_OK

# ----------------------- #

Leg = Callable[[], tuple[str, bool]]


def tick_cmd(
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """One pass of the standing loop: reap, poll the board, dispatch at
    most one queued task, process the lane if auto-merge is on, sync the
    board. Exits when the pass is done; schedule it for cadence."""
    from torve.adapters.vcs.git import GhCi, GhScm, GitLane, GitVcs, NullScm
    from torve.adapters.workspace.git import GitWorkspace
    from torve.application.loop import TickDeps, run_tick

    root = root.resolve()
    config = load_config(root, config_path)
    vcs = GitVcs()
    workspace = GitWorkspace(root)

    ci = None
    if config.promotion.require_ci:
        if not config.scm.repo:
            raise fail("configuration error: promotion.require_ci needs "
                       "scm.repo to name the remote whose verdict counts",
                       EXIT_CONFIG)
        ci = GhCi(config.scm.repo, config.scm.token_env)

    def reap_leg() -> tuple[str, bool]:
        from torve.adapters.store.durable import open_store
        from torve.application.reaper import reap
        from torve.cli.options import runtime_for

        # The factory travels regardless of adapter: under postgres the
        # reap is durable (D-3.15) and would refuse without it.
        report = reap(root, config, runtime_for(config, None), workspace,
                      store=open_store,
                      landed=lambda t: bool(vcs.landed_shas(root, t)))
        swept = (len(report.sandboxes_destroyed) + len(report.worktrees_removed)
                 + len(report.runs_expired) + len(report.states_removed))
        return (f"swept {swept} artefact(s)" if swept else "nothing to sweep",
                swept > 0)

    poll_leg: Leg | None = None
    sync_leg: Leg | None = None
    if config.tracker.kind == "github-issues" and config.tracker.repo:
        from torve.adapters.tracker.github import GithubIssues
        from torve.application.tracker import (
            poll_and_apply,
            project,
            relay_to_tracker,
        )

        board = GithubIssues(config.tracker.repo, config.tracker.token_env)

        def _requeue(task_id: str) -> str:
            import os

            from torve.application.feedback import capture_feedback
            from torve.base import naming

            branch = naming.branch(task_id)
            note = ""
            if config.review.feedback_from and config.scm.repo:
                # The revision loop (RFC 0005 §4a, D-5.12): captured before
                # the branch dies, or it is gone.
                scm = GhScm(config.scm.repo, config.scm.token_env)
                try:
                    threads = scm.review_threads(
                        branch, tuple(config.review.feedback_from))
                    diff = (vcs.diff(root, config.base or "origin/main", branch)
                            if GitLane().tip(root, branch) else "")
                    if capture_feedback(root, task_id, diff, threads):
                        note = "; feedback captured"
                except RuntimeError as exc:
                    note = f"; feedback capture failed: {exc}"
            token = (os.environ.get(config.scm.token_env)
                     if config.scm.token_env else None)
            deleted = vcs.delete_remote_branch(root, branch, token)
            return ("remote branch deleted" if deleted
                    else "no remote branch") + note

        def _approve_tip(task_id: str) -> str | None:
            from torve.base import naming

            return GitLane().tip(root, naming.branch(task_id))

        def _poll() -> tuple[str, bool]:
            report = poll_and_apply(root, board, tuple(config.tracker.commanders),
                                    _requeue, _approve_tip)
            if not report.outcomes:
                return ("no commands on the board", False)
            applied = sum(o.applied for o in report.outcomes)
            return (f"{applied} applied of {len(report.outcomes)} command(s)",
                    applied > 0)

        def _sync() -> tuple[str, bool]:
            from torve.application.tracker import project_landings

            staged = project(root, config.tracker.notify)
            staged += project_landings(
                root, lambda t: bool(vcs.landed_shas(root, t)))
            report = relay_to_tracker(root, board)
            return (f"staged {staged}, delivered {len(report.delivered)}",
                    bool(report.delivered))

        poll_leg, sync_leg = _poll, _sync

    def dispatch_leg(task_id: str) -> tuple[str, bool]:
        from torve.adapters.agent.fake import FakeAgent
        from torve.adapters.agent.harness import HarnessAgent
        from torve.adapters.store.durable import open_store
        from torve.adapters.vcs.git import repository_name
        from torve.application.ports import Agent
        from torve.application.runner import RunDeps, run_task
        from torve.cli.options import runtime_for
        from torve.cli.run import build_reviewer_agent
        from torve.config.runconfig import route_provider, tier_for
        from torve.gates.context import load_task

        task = load_task(root / ".torve" / "tasks" / task_id / "contract.yaml")
        tier = tier_for(config, task.tier)
        route_provider(config.providers, repository_name(root), tier.provider)
        agent: Agent = (FakeAgent(None) if tier.adapter == "fake"
                        else HarnessAgent(tier))
        review_agent = (build_reviewer_agent(config, root)
                        if "task_gated" in config.review.on else None)
        deps = RunDeps(
            workspace=workspace, runtime=runtime_for(config, None), agent=agent,
            vcs=vcs,
            scm=(GhScm(config.scm.repo, config.scm.token_env)
                 if config.scm.open_pr else NullScm()),
            store=open_store, review_agent=review_agent,
        )
        state = run_task(root, task, config, deps)
        return (f"{task_id}: {state.state} after {state.attempts} attempt(s)",
                True)

    lane_leg: Leg | None = None
    if config.promotion.auto_merge:
        from torve.application.lane import process_lane

        def _lane() -> tuple[str, bool]:
            lane_vcs = GitLane()
            results = process_lane(
                root, lane_vcs, ci=ci,
                approvals_required=config.promotion.approvals,
                quiet_window_s=config.promotion.quiet_window)
            if not results:
                return ("no ready candidates", False)
            if config.tracker.kind == "github-issues" and config.tracker.repo:
                from torve.application.tracker import project_approval_gap

                # D-8.13: the refusal prompts on its thread — delivered by
                # this same tick's sync leg.
                for r in results:
                    if r.action == "approvals short" and r.sha:
                        project_approval_gap(root, r.task, r.sha,
                                             config.promotion.approvals)
            landed = sum(1 for r in results if r.action == "landed")
            detail = f"landed {landed} of {len(results)} candidate(s)"
            if landed:
                # D-19.9 (A-28): the loop publishes what it lands —
                # fast-forward only for the base, no force path; a refusal
                # is this leg's loud error.
                import os

                from torve.base import naming

                token = (os.environ.get(config.scm.token_env)
                         if config.scm.token_env else None)
                base = lane_vcs.current_branch(root)
                # D-19.12 (A-34): the landed form returns to its branch
                # BEFORE the base push, so the forge sees that push as the
                # merge of every landed pull request; a refused lease falls
                # through to the close-out below.
                republished = 0
                for r in results:
                    if r.action == "landed" and r.detail.startswith("rebased"):
                        try:
                            if vcs.republish_branch(
                                    root, naming.branch(r.task), token):
                                republished += 1
                        except RuntimeError:
                            pass
                pushed = vcs.push(root, base, token)
                detail += "; base pushed" if pushed else "; no origin to push"
                if republished:
                    detail += f"; {republished} branch(es) republished"
                if pushed and config.scm.open_pr and config.scm.repo:
                    # D-19.13: the forge gets a short grace to mark the
                    # landing merged; a still-open PR closes with the note
                    # (T-0072, now the fallback), and the candidate branch
                    # retires in every case — cosmetics never fail the leg.
                    from torve.adapters.vcs.git import GhScm

                    scm = GhScm(config.scm.repo, config.scm.token_env)
                    outcomes: dict[str, int] = {}
                    for r in results:
                        if r.action != "landed":
                            continue
                        note = (f"landed on {base} as {r.sha[:10]} by "
                                "fast-forward — this pull request was a "
                                "review surface; the approval that landed "
                                "it lives on the task's issue")
                        branch_name = naming.branch(r.task)
                        try:
                            word = scm.retire_pr(branch_name, note)
                            if word != "closed":
                                vcs.delete_remote_branch(root, branch_name, token)
                        except RuntimeError:
                            word = "refused"
                        outcomes[word] = outcomes.get(word, 0) + 1
                    for word in sorted(outcomes):
                        detail += f"; {outcomes[word]} pr(s) {word}"
            return (detail, landed > 0)

        lane_leg = _lane

    def landed(task_id: str) -> bool:
        return bool(vcs.landed_shas(root, task_id))

    report = run_tick(root, config, TickDeps(
        reap=reap_leg, poll=poll_leg, dispatch=dispatch_leg,
        lane=lane_leg, sync=sync_leg, landed=landed))

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "noop": report.noop,
                   "locked_out": report.locked_out,
                   "legs": dict(report.legs)})
    else:
        console = out(fmt)
        header(console, "tick", "one bounded pass")
        table = make_table("leg", "outcome")
        for name, detail in report.legs:
            table.add_row(name, detail)
        console.print(table)
        closing(console, "noop — nothing moved" if report.noop else "work done",
                STYLE_DIM if report.noop else "")
    raise typer.Exit(EXIT_OK)
