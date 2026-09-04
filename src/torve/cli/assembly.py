"""The composition root (RFC 0042 §5.1): the one place that turns
`(root, config)` into runnable deps — `RunDeps`, `TickDeps`, the intake and
review legs, and the store/broker/vcs/workspace/runtime constructors
behind them. Verbs parse arguments, enforce their front doors and render;
they do not build adapters inline. The bundles stay the application layer's
contract, unchanged by this move (D-42.2); the root lives in `cli` because
adapter imports belong to this layer and no other (RFC 0015 §2.1).

`build_tick_deps` is what `torve tick` consumes; `build_fleet_tick_deps` is
what one fleet root consumes. They share every leg and differ in exactly
the two corners the fleet loop has never run — the post-push forge
bookkeeping (PR retirement, captured-thread replies) and the dispatch
line's envelope — each refused by a named switch rather than a rebuilt
copy, so a third consumer reads as a wiring choice, not a fork (D-42.1).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from torve.adapters.vcs.git import GitVcs
    from torve.application.intake import IntakeDeps
    from torve.application.loop import TickDeps
    from torve.application.ports import Agent, Vcs, WorkspacePort
    from torve.application.runner import RunDeps
    from torve.cli.options import RuntimeName
    from torve.config.runconfig import RunnerConfig, TierConfig

# ----------------------- #


def build_tier_agent(config: RunnerConfig, root: Path, tier_name: str) -> Agent:
    """A named tier's agent, provider-routed before a sandbox exists —
    shared by the run loop's review hook, the regression corpus, and the
    drafting run."""

    from torve.adapters.vcs.git import repository_name
    from torve.config.runconfig import route_provider, tier_for

    tier = tier_for(config, tier_name)
    route_provider(config.providers, repository_name(root), tier.provider)

    if tier.adapter == "fake":
        from torve.adapters.agent.fake import FakeAgent

        return FakeAgent(None)

    from torve.adapters.agent.harness import HarnessAgent

    return HarnessAgent(tier)


# ....................... #


def build_reviewer_agent(config: RunnerConfig, root: Path) -> Agent:
    return build_tier_agent(config, root, "reviewer")


# ....................... #


def review_agent_for(config: RunnerConfig, root: Path) -> Agent | None:
    """The reviewer agent where the configuration asks for a per-task
    review, None where it does not."""

    return build_reviewer_agent(config, root) if "task_gated" in config.review.on else None


# ....................... #


# The dispatch tier's and every retry rung's agent comes from one rule:
# D-27.11, its scalar generalized by D-34.6.
def dispatch_agent_factory(
    *, agent_name: str | None = None, scenario: Path | None = None
) -> Callable[[TierConfig], Agent]:
    """The rule that turns a resolved tier into the Agent that runs on it —
    the dispatch tier's and every retry rung's. `agent_name='fake'` is the
    run verb's scenario override; a scenario file rides it alone."""

    def make(tier: TierConfig) -> Agent:
        if agent_name == "fake" or tier.adapter == "fake":
            from torve.adapters.agent.fake import FakeAgent, load_scenario

            return FakeAgent(load_scenario(scenario) if scenario else None)

        from torve.adapters.agent.harness import HarnessAgent

        if scenario is not None:
            raise ValueError("--scenario is FakeAgent-only")

        return HarnessAgent(tier)

    return make


# ....................... #


# Provider routing is enforced at dispatch (D-4.8), before a sandbox
# exists; every retry rung routes too (D-27.11's scalar generalized by
# D-34.6), because D-27.1 refuses to dispatch under a regime it has not
# already validated.
def route_dispatch_providers(config: RunnerConfig, root: Path, tier: TierConfig) -> None:
    """Route the dispatch tier and every retry rung it names through the
    repository's permitted providers. A repository with no permitted
    provider for its tier is a configuration error, never a quiet
    fallback; the run may reach any axis's rung after the matching
    conviction, so each one is checked."""

    from torve.adapters.vcs.git import repository_name
    from torve.config.runconfig import route_provider, tier_for

    route_provider(config.providers, repository_name(root), tier.provider)

    for rung in tier.resolved_retry_variants().values():
        retry_tier = tier_for(config, rung)
        route_provider(config.providers, repository_name(root), retry_tier.provider)


# ....................... #


def build_run_deps(
    root: Path,
    config: RunnerConfig,
    *,
    agent: Agent,
    retry_agent: Callable[[TierConfig], Agent] | None = None,
    review_agent: Agent | None = None,
    runtime_name: RuntimeName | None = None,
    workspace: WorkspacePort | None = None,
    vcs: Vcs | None = None,
) -> RunDeps:
    """One task-run's dep bundle. The caller supplies the agent (its tier
    is a per-dispatch fact, not a per-root one); everything else is a
    function of `(root, config)`. `workspace`/`vcs` may be passed in so a
    tick shares one instance across its legs."""

    from torve.adapters.broker import build_broker
    from torve.adapters.store.durable import open_store
    from torve.adapters.vcs.git import GhScm, GitVcs, NullScm
    from torve.adapters.workspace.git import GitWorkspace
    from torve.application.runner import RunDeps
    from torve.cli.options import runtime_for

    return RunDeps(
        workspace=workspace if workspace is not None else GitWorkspace(root),
        runtime=runtime_for(config, runtime_name),
        agent=agent,
        vcs=vcs if vcs is not None else GitVcs(),
        scm=(GhScm(config.scm.repo, config.scm.token_env) if config.scm.open_pr else NullScm()),
        store=open_store,
        review_agent=review_agent,
        # The egress broker in force (RFC 0021): `none` by default, `local`
        # when configured — the run's keys never enter the sandbox either way.
        broker=build_broker(config.broker),
        # D-27.11: builds the tier a retry_variant names, mid-run — the same
        # rule that built the tier that dispatched.
        retry_agent=retry_agent,
    )


# ....................... #


def build_intake_deps(
    root: Path, config: RunnerConfig, *, board: Any, vcs: GitVcs
) -> IntakeDeps:  # GitVcs, not the Vcs port: the worktree pair lives on the adapter.
    """The intake leg's wiring: the tracker board is the surface, the
    drafter's harness is built only when a request needs it."""

    from torve.adapters.broker import build_broker
    from torve.adapters.vcs.git import GitLane
    from torve.application.intake import IntakeDeps
    from torve.application.telemetry import config_hash
    from torve.cli.options import runtime_for
    from torve.config import layout
    from torve.gates.context import resolve_base

    return IntakeDeps(
        tracker=board,
        runtime=runtime_for(config, None),
        agent_factory=lambda: build_tier_agent(config, root, "planner"),
        worktree_at=vcs.worktree_at,
        remove_worktree=vcs.remove_worktree,
        base_tip=lambda: GitLane().tip(root, resolve_base(root, config.base) or "HEAD"),
        config_digest=config_hash(layout.gates_file(root), root, config),
        broker=build_broker(config.broker),
    )


# ....................... #


def build_tick_deps(root: Path, config: RunnerConfig) -> TickDeps:
    """The solo tick's legs, wired from `(root, config)` — the shape
    `torve tick` has always dispatched."""

    return _tick_deps(root, config, forge_bookkeeping=True, dispatch_envelope=True)


# ....................... #


def build_fleet_tick_deps(root: Path, config: RunnerConfig) -> TickDeps:
    """A fleet root's legs: every solo leg, minus the two corners the fleet
    loop has never run — the post-push forge bookkeeping and the dispatch
    envelope line. Wiring them on is a behaviour change, not a refactor;
    until one is decided, this root's tick stays the tick it always was."""

    return _tick_deps(root, config, forge_bookkeeping=False, dispatch_envelope=False)


# ....................... #


def _tick_deps(
    root: Path, config: RunnerConfig, *, forge_bookkeeping: bool, dispatch_envelope: bool
) -> TickDeps:
    from torve.adapters.vcs.git import GhCi, GhScm, GitLane, GitVcs
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

    poll_leg = None
    sync_leg = None
    intake_leg_fn = None

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
            from torve.application.intake import intake_leg

            # Built per run, as the solo tick always built it: the digest
            # and the runtime snapshot belong to the leg's moment, not the
            # tick's setup.
            deps = build_intake_deps(root, config, board=board, vcs=vcs)

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
        from torve.application import sizing, specquality
        from torve.application.runner import run_task
        from torve.config.runconfig import (
            resolve_character_tier,
            tier_for,
            tier_name_for,
        )
        from torve.gates.context import load_task

        make_agent = dispatch_agent_factory()

        task = load_task(root / ".torve" / "tasks" / task_id / "contract.yaml")
        # RFC 0034 D-34.3: resolved once, before the tier this dispatch
        # routes and runs under is read anywhere else.
        task = resolve_character_tier(config, task)
        tier = tier_for(config, tier_name_for(task))
        route_dispatch_providers(config, root, tier)

        deps = build_run_deps(
            root,
            config,
            agent=make_agent(tier),
            review_agent=review_agent_for(config, root),
            retry_agent=make_agent,
            workspace=workspace,
            vcs=vcs,
        )

        # A batch member runs under its own worker slot (D-19.14): auth
        # volumes are per-slot (D-4.2), and two runs must never share one.
        run_config = (
            config
            if slot_offset == 0
            else config.model_copy(update={"worker_slot": config.worker_slot + slot_offset})
        )

        state = run_task(root, task, run_config, deps)

        line = f"{task_id}: {state.state} after {state.attempts} attempt(s)"

        if not dispatch_envelope:
            return line

        # D-22.11, A-62: the dispatch leg prints the envelope beside the
        # size verdict, same as `torve run` — a base rate the operator reads,
        # never a bound the loop acts on.
        envelope = specquality.dispatch_envelope(root, sizing.estimate(task).size)

        return f"{line} · {specquality.render_envelope(envelope)}"

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

                if forge_bookkeeping and pushed and config.scm.open_pr and config.scm.repo:
                    # D-19.13: the forge gets a short grace to mark the
                    # landing merged; a still-open PR closes with the note
                    # (T-0072, now the fallback), and the candidate branch
                    # retires in every case — cosmetics never fail the leg.
                    scm = GhScm(config.scm.repo, config.scm.token_env)
                    pr_outcomes: dict[str, int] = {}

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

                        pr_outcomes[word] = pr_outcomes.get(word, 0) + 1

                    for word in sorted(pr_outcomes):
                        detail += f"; {pr_outcomes[word]} pr(s) {word}"

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

    return TickDeps(
        reap=reap_leg,
        poll=poll_leg,
        dispatch=dispatch_leg,
        lane=lane_leg,
        sync=sync_leg,
        landed=landed,
        intake=intake_leg_fn,
    )
