"""The composition root (RFC 0042 §5.1): the one place that turns
`(root, config)` into runnable deps — `RunDeps`, the review leg, and the
store/broker/vcs/workspace/runtime constructors behind them. Verbs parse
arguments, enforce their front doors and render; they do not build adapters
inline. The bundles stay the application layer's contract, unchanged by
this move (D-42.2); the root lives in `cli` because adapter imports belong
to this layer and no other (RFC 0015 §2.1).

`prepare_for` is the per-task half of a dispatch as one callable (RFC 0044
D-44.12): a worker runs whatever the board hands it, so what a task needs
is resolved from the task — its character's tier (D-34.3) and the provider
routing that tier must pass (D-4.8).

The tick's leg bundles left with the standing loop (A-105); what they wired
is now the manager's pass, `torve merge` and `torve reap`.

`build_notifier` resolves the notification destination (RFC 0051 D-51.3,
D-51.4), following the broker's `none`-by-default precedent (D-21.9).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from torve.application.dispatch import RunDeps
    from torve.application.executors import Prepare
    from torve.application.ports import Agent, Notifier, Vcs, WorkspacePort
    from torve.cli.options import RuntimeName
    from torve.config.runconfig import RunnerConfig, TierConfig
    from torve.domain.task import Task

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
    from torve.application.dispatch import RunDeps
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


def build_dispatch_prepare(
    root: Path, config: RunnerConfig, *, runtime_name: RuntimeName | None = None
) -> Prepare:
    """The per-task half of a dispatch, as one callable.

    A worker runs whatever the board hands it, and what a task needs
    resolved is a fact about that task: the tier its character routes to,
    the provider routing that tier must pass, and the agent built for it.
    Building a dep bundle once per manager would pin every task to
    whichever tier happened to be first.
    """

    def prepare(task: Task) -> tuple[Task, RunDeps]:
        from torve.config.runconfig import resolve_character_tier, tier_for, tier_name_for

        make_agent = dispatch_agent_factory()
        task = resolve_character_tier(config, task)
        tier = tier_for(config, tier_name_for(task))
        route_dispatch_providers(config, root, tier)

        return task, build_run_deps(
            root,
            config,
            agent=make_agent(tier),
            review_agent=review_agent_for(config, root),
            retry_agent=make_agent,
            runtime_name=runtime_name,
        )

    return prepare


# ....................... #


def build_notifier(config: RunnerConfig) -> Notifier:
    """The destination in force.

    `none` by default and explicitly: a repository that has not chosen a
    destination sends nothing because somebody decided that, which is the
    broker's `none` adapter's precedent. An unknown adapter is a
    configuration error rather than a silent fallback to silence — a
    notifier that quietly does nothing is the failure this whole document
    exists to end.
    """

    from torve.adapters.notify import NoNotifier, WebhookNotifier

    if config.notify.adapter == "none":
        return NoNotifier()

    if config.notify.adapter == "webhook":
        return WebhookNotifier(
            os.environ.get(config.notify.url_env, ""), timeout_s=config.notify.timeout_s
        )

    raise ValueError(f"unknown notify adapter {config.notify.adapter!r} — one of: none, webhook")
