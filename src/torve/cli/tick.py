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


# ....................... #


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
            raise fail(
                "configuration error: promotion.require_ci needs "
                "scm.repo to name the remote whose verdict counts",
                EXIT_CONFIG,
            )

        ci = GhCi(config.scm.repo, config.scm.token_env)

    def reap_leg() -> tuple[str, bool]:
        from torve.adapters.store.durable import open_store
        from torve.application.reaper import reap
        from torve.cli.options import runtime_for

        # The factory travels regardless of adapter: under postgres the
        # reap is durable (D-3.15) and would refuse without it.
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
        """The re-queue cleanup the retry command and the lane's conflict
        disposal share (T-0059 as amended by A-37, A-35): the revision
        feedback is captured while the candidate stands (RFC 0005 §4a,
        D-5.12), and the branch persists — the next attempt supersedes it
        under lease (D-10.10), so its pull request stays the task's one
        thread of review."""

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

    poll_leg: Leg | None = None
    sync_leg: Leg | None = None
    intake_leg_fn: Leg | None = None

    if config.tracker.kind == "github-issues" and config.tracker.repo:
        from torve.adapters.tracker.github import GithubIssues
        from torve.application.tracker import (
            poll_and_apply,
            project,
            relay_to_tracker,
        )

        board = GithubIssues(config.tracker.repo, config.tracker.token_env)

        def _approve_tip(task_id: str) -> str | None:
            from torve.base import naming

            return GitLane().tip(root, naming.branch(task_id))

        def _adopt_drafts(task_id: str) -> list[str]:
            from torve.application.intake import adopt

            # The poll runs under the tick's lock — adoption borrows it.
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
            from torve.application.intake import IntakeDeps, intake_leg
            from torve.application.telemetry import config_hash
            from torve.cli.options import runtime_for
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
        )
        # A batch member runs under its own worker slot (D-19.14): auth
        # volumes are per-slot (D-4.2), and two runs must never share one.
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

        # D-19.14 (A-39): a scope-disjoint batch runs concurrently — the
        # loop admitted only what provably cannot collide, and the store's
        # per-task claims (D-6.9) stay the mutual-exclusion backstop.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(task_ids)) as pool:
            futures = [
                pool.submit(_dispatch_one, task_id, index) for index, task_id in enumerate(task_ids)
            ]
            outcomes: list[str] = []

            for task_id, future in zip(task_ids, futures, strict=True):
                try:
                    outcomes.append(future.result())
                except Exception as exc:  # one member's failure is its own
                    outcomes.append(f"{task_id}: error: {exc}")

        return ("; ".join(outcomes), True)

    lane_leg: Leg | None = None

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
                # D-6.10 as amended by A-35: the loop disposes of its own
                # conflicts through the revision loop; the operator's
                # manual lane never does.
                on_conflict=_capture_for_revision,
            )

            if not results:
                return ("no ready candidates", False)

            if config.tracker.kind == "github-issues" and config.tracker.repo:
                from torve.application.tracker import project_approval_gap

                # D-8.13: the refusal prompts on its thread — delivered by
                # this same tick's sync leg.
                for r in results:
                    if r.action == "approvals short" and r.sha:
                        project_approval_gap(root, r.task, r.sha, config.promotion.approvals)

            landed = sum(1 for r in results if r.action == "landed")
            detail = f"landed {landed} of {len(results)} candidate(s)"

            if landed:
                # D-19.9 (A-28): the loop publishes what it lands —
                # fast-forward only for the base, no force path; a refusal
                # is this leg's loud error.
                import os

                from torve.base import naming

                token = os.environ.get(config.scm.token_env) if config.scm.token_env else None
                base = lane_vcs.current_branch(root)
                # D-19.12 (A-34): the landed form returns to its branch
                # BEFORE the base push, so the forge sees that push as the
                # merge of every landed pull request; a refused lease falls
                # through to the close-out below.
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

                        note = (
                            f"landed on {base} as {r.sha[:10]} by "
                            "fast-forward — this pull request was a "
                            "review surface; the approval that landed "
                            "it lives on the task's issue"
                        )
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

                    # D-5.14 (A-41): the landing answers the review threads
                    # its revision consumed — one reply per captured root,
                    # from records; the forge's cosmetics never fail the leg.
                    import json as _json

                    from torve.application.feedback import threads_file

                    answered = 0

                    for r in results:
                        if r.action != "landed":
                            continue

                        pending = threads_file(root, r.task)

                        if not pending.is_file():
                            continue

                        try:
                            records = _json.loads(pending.read_text(encoding="utf-8"))
                            reply = (
                                f"Captured into {r.task}'s revision "
                                "record; the revised candidate landed "
                                f"as `{r.sha[:10]}`. The finding's "
                                "disposition stays the reviewer's call."
                            )
                            done, _already = scm.answer_captured_threads(records, reply)
                            pending.unlink()
                            answered += done
                        except (RuntimeError, ValueError):
                            continue  # the file stays; the next tick retries

                    if answered:
                        detail += f"; {answered} review thread(s) answered"

            return (detail, landed > 0)

        lane_leg = _lane

    def landed(task_id: str) -> bool:
        return bool(vcs.landed_shas(root, task_id))

    report = run_tick(
        root,
        config,
        TickDeps(
            reap=reap_leg,
            poll=poll_leg,
            dispatch=dispatch_leg,
            lane=lane_leg,
            sync=sync_leg,
            landed=landed,
            intake=intake_leg_fn,
        ),
    )

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "noop": report.noop,
                "locked_out": report.locked_out,
                "legs": dict(report.legs),
            }
        )
    else:
        console = out(fmt)
        header(console, "tick", "one bounded pass")
        table = make_table("leg", "outcome")

        for name, detail in report.legs:
            table.add_row(name, detail)

        console.print(table)
        closing(
            console,
            "noop — nothing moved" if report.noop else "work done",
            STYLE_DIM if report.noop else "",
        )

    raise typer.Exit(EXIT_OK)
