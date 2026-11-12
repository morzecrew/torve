"""The attempt's steps over a dispatch (S-0046/the-steps).

Conviction-routed tier advancement (S-0027/D-11) is the piece with a decision
attached and no I/O in it: given a dispatch and a state it resolves the rung
the last pass's convictions select, moves the regime, and hands back the
Agent to run. Before the restructuring it could only be reached through a
whole run — a workspace, a runtime, an agent, a VCS, an SCM and a store, none
of which it touches.
"""

import pytest
import yaml
from test_review_run import review_rig  # noqa: F401 — the fixture, by name

from torve.application.dispatch import Dispatch, RunDeps
from torve.application.runstate import RunState
from torve.application.session import advance_tier
from torve.config.runconfig import ReviewConfig, RunnerConfig, TierConfig
from torve.domain.attempt import GateResult
from torve.domain.task import Task

# ----------------------- #

TASK_ID = "T-0001"

MANIFEST = {
    "schema_version": 1,
    "gates": [
        {
            "name": "acceptance",
            "run": "true",
            "state": "blocking",
            "origin": "structural",
            "axis": "functional",
        },
        {
            "name": "tidy",
            "run": "true",
            "state": "blocking",
            "origin": "structural",
            "axis": "form",
        },
    ],
}


# ....................... #


class _StubRuntime:
    def __init__(self) -> None:
        self.resolved: list[str] = []

    def resolve_image(self, image: str) -> str | None:
        self.resolved.append(image)

        return f"sha256:{image or 'none'}"


# ....................... #


def _config() -> RunnerConfig:
    seat = TierConfig(adapter="api", provider="p", model="cheap")
    seat.retry_variants = {"form": "executor.tidy"}

    return RunnerConfig(
        tiers={
            "executor": seat,
            "executor.tidy": TierConfig(
                adapter="api", provider="p", model="neat", image="tidy-image"
            ),
        }
    )


def _revision_config(
    revision_variant: str = "executor.strong", review: ReviewConfig | None = None
) -> RunnerConfig:
    # The rung is reached only when a configuration names a revision mode
    # (S-0127/D-5); `off`, the default, keeps the revision on the seat.
    return RunnerConfig(
        tiers={
            "executor": TierConfig(
                adapter="api", provider="p", model="cheap", revision_variant=revision_variant
            ),
            "executor.strong": TierConfig(
                adapter="api", provider="p", model="strong", image="strong-image"
            ),
        },
        review=review or ReviewConfig(revision="fresh"),
    )


# ....................... #


def _dispatch(tmp_path, *, retry_agent=None, config: RunnerConfig | None = None) -> Dispatch:
    manifest = tmp_path / ".torve" / "gates.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(yaml.safe_dump(MANIFEST), encoding="utf-8")

    config = config or _config()
    deps = RunDeps(
        workspace=None,  # type: ignore[arg-type]  # unreached: no sandbox is opened
        runtime=_StubRuntime(),  # type: ignore[arg-type]
        agent="seat-agent",  # type: ignore[arg-type]
        vcs=object(),  # type: ignore[arg-type]
        scm=None,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
        retry_agent=retry_agent,
    )

    return Dispatch(
        root=tmp_path,
        task=Task(id=TASK_ID, decisions=[]),
        config=config,
        deps=deps,
        worktree=tmp_path,
        shadow=False,
        gates_base=None,
        resume=False,
        tier_name="executor",
        tier=config.tiers["executor"],
        image="seat-image",
        image_digest="sha256:seat",
        meta={},
    )


# ....................... #


def _state(*facts: str) -> RunState:
    state = RunState(task_id=TASK_ID, path=None)  # type: ignore[arg-type]  # never saved
    state.history = [{"at": "", "from": "", "to": "", "fact": fact} for fact in facts]

    return state


# ....................... #


def _convicted(name: str) -> GateResult:
    return GateResult(name=name, outcome="fail", state="blocking", exit_code=1)


# ....................... #


def test_a_gate_red_routes_the_next_attempt_to_the_mapped_rung(tmp_path):
    """S-0027/D-11: the attempt after a red resolves the rung the recorded
    convictions select — at the most severe axis present — and the regime
    moves with it, so the record stamps the tier that produced the work."""

    run = _dispatch(tmp_path, retry_agent=lambda tier: f"agent-for-{tier.model}")
    run.convictions = [_convicted("tidy")]

    agent = advance_tier(run, _state("gates red: tidy=fail", "attempt 2 dispatched"))

    assert agent == "agent-for-neat"
    assert run.tier_name == "executor.tidy"
    assert run.tier.model == "neat"
    assert run.image == "tidy-image"
    assert run.image_digest == "sha256:tidy-image"


# ....................... #


def test_a_red_with_no_mapped_rung_stays_on_the_seat(tmp_path):
    """The seat maps `form` and nothing else here. A functional conviction
    resolves no rung, so the attempt continues under the task's own tier —
    a red is not by itself a reason to change the regime."""

    run = _dispatch(tmp_path, retry_agent=lambda tier: f"agent-for-{tier.model}")
    run.convictions = [_convicted("acceptance")]

    agent = advance_tier(run, _state("gates red: acceptance=fail", "attempt 2 dispatched"))

    assert agent == "seat-agent"
    assert run.tier_name == "executor"


# ....................... #


def test_no_agent_factory_means_the_regime_never_moves(tmp_path):
    """Never fabricated (S-0027/D-1): advancement fires only where the CLI wired
    a factory that can actually build the rung's Agent, so telemetry can
    never stamp a tier that did not produce the work."""

    run = _dispatch(tmp_path, retry_agent=None)
    run.convictions = [_convicted("tidy")]

    agent = advance_tier(run, _state("gates red: tidy=fail", "attempt 2 dispatched"))

    assert agent == "seat-agent"
    assert run.tier_name == "executor"


# ....................... #


def test_an_advance_is_never_sticky(tmp_path):
    """One rung, not a ratchet: an attempt that did not follow a red goes
    back to the task's own tier even when the previous one was routed away
    from it."""

    run = _dispatch(tmp_path, retry_agent=lambda tier: f"agent-for-{tier.model}")
    run.convictions = [_convicted("tidy")]
    advance_tier(run, _state("gates red: tidy=fail", "attempt 2 dispatched"))

    assert run.tier_name == "executor.tidy"

    agent = advance_tier(run, _state("agent exited 0; gates running", "attempt 3 dispatched"))

    assert agent == "seat-agent"
    assert run.tier_name == "executor"
    assert run.image_digest == "sha256:python:3.13-slim"  # back to the seat's own image


# ....................... #
# blocker revisions (S-0117/D-1): under `review.revision: fresh` or `session`
# the attempt after a surviving blocker's revision resolves the seat's
# revision_variant, as a gate-red resolves a rung.


def _blocker_revision_fact() -> str:
    from torve.application.review import BLOCKER_REVISION_MARKER

    return f"{BLOCKER_REVISION_MARKER}1 of 1): R-1: the change is wrong"


def test_the_attempt_after_a_blocker_revision_runs_on_the_revision_variant(tmp_path):
    """S-0117/D-1: the revision the seat could not clear gets the stronger
    seat, built through the factory exactly as a gate-red rung is."""

    run = _dispatch(
        tmp_path, retry_agent=lambda tier: f"agent-for-{tier.model}", config=_revision_config()
    )

    agent = advance_tier(run, _state(_blocker_revision_fact(), "attempt 2 dispatched"))

    assert agent == "agent-for-strong"
    assert run.tier_name == "executor.strong"
    assert run.tier.model == "strong"
    assert run.image == "strong-image"


def test_an_empty_revision_variant_keeps_the_revision_on_the_seat(tmp_path):
    """S-0117/D-1: empty is the default and changes nothing — the revision
    retries under the seat, with no factory call to fabricate a tier."""

    called: list[object] = []
    run = _dispatch(
        tmp_path,
        retry_agent=lambda tier: called.append(tier) or "agent-for-rung",
        config=_revision_config(revision_variant=""),
    )

    agent = advance_tier(run, _state(_blocker_revision_fact(), "attempt 2 dispatched"))

    assert agent == "seat-agent"
    assert run.tier_name == "executor"
    assert called == []


def test_revision_off_keeps_the_attempt_after_a_blocker_on_the_seat(tmp_path):
    """S-0127/D-5: under `review.revision: off`, the default, the one fresh
    attempt a surviving blocker buys runs on the task's own seat even where the
    seat names a `revision_variant`; no factory call fabricates the rung."""

    called: list[object] = []
    run = _dispatch(
        tmp_path,
        retry_agent=lambda tier: called.append(tier) or "agent-for-rung",
        config=_revision_config(review=ReviewConfig()),
    )

    agent = advance_tier(run, _state(_blocker_revision_fact(), "attempt 2 dispatched"))

    assert agent == "seat-agent"
    assert run.tier_name == "executor"
    assert run.tier.model == "cheap"
    assert called == []


def test_a_green_pass_hands_the_next_attempt_back_to_the_seat(tmp_path):
    """S-0117/D-1: the revision variant is not sticky — the attempt after a
    green pass is the seat's, even when a revision ran on the rung."""

    run = _dispatch(
        tmp_path, retry_agent=lambda tier: f"agent-for-{tier.model}", config=_revision_config()
    )
    advance_tier(run, _state(_blocker_revision_fact(), "attempt 2 dispatched"))
    assert run.tier_name == "executor.strong"

    agent = advance_tier(run, _state("review clean (R-1)", "attempt 3 dispatched"))

    assert agent == "seat-agent"
    assert run.tier_name == "executor"


def test_the_blocker_revision_predicate_reads_the_slot_behind_the_dispatch():
    from torve.application.session import previous_attempt_follows_blocker_revision

    assert previous_attempt_follows_blocker_revision(
        _state(_blocker_revision_fact(), "attempt 2 dispatched")
    )
    assert not previous_attempt_follows_blocker_revision(
        _state("gates red: tidy=fail", "attempt 2 dispatched")
    )
    assert not previous_attempt_follows_blocker_revision(_state("attempt 1 dispatched"))


# ....................... #


@pytest.mark.parametrize("history", [[], ["attempt 1 dispatched"]])
def test_the_first_attempt_has_no_previous_pass_to_route_from(tmp_path, history):
    run = _dispatch(tmp_path, retry_agent=lambda tier: f"agent-for-{tier.model}")

    assert advance_tier(run, _state(*history)) == "seat-agent"
    assert run.tier_name == "executor"


# ....................... #
# Which authentication route a seat takes (S-0063/D-18, S-0064/D-9)


def _auth(tier, slot=0, *, key_env="K"):
    """`_sandbox_auth` against a config whose record names the credential — the
    name comes off the provider now, never off the harness that dials it."""

    from torve.application.session import _sandbox_auth
    from torve.config.providers import Provider, Route

    record = Provider(
        name=tier.provider or "p",
        key_env=key_env,
        routes={"openai": Route(base_url="https://p.test/v1")},
    )
    config = RunnerConfig(provider_records={tier.provider: record} if tier.provider else {})

    return _sandbox_auth(config, tier, slot)


def test_a_seat_whose_provider_names_a_variable_mounts_nothing():
    """The record decides, not the adapter and not the manifest. One token for
    one attempt is the narrower blast radius, and every harness this repository
    dispatches to has an env form."""

    seat = TierConfig(
        adapter="subscription",
        provider="anthropic",
        image="claude-sandbox:2.1.252",
    )
    names, volumes = _auth(seat, key_env="CLAUDE_CODE_OAUTH_TOKEN")

    assert names == ("CLAUDE_CODE_OAUTH_TOKEN",)
    assert volumes == {}


def test_a_seat_whose_provider_has_no_record_falls_back_to_its_volume():
    """The route for a harness with no env form. Read-write, because the
    harness refreshes its token mid-session and a mount it cannot write to
    hangs rather than failing."""

    from torve.application.session import _sandbox_auth

    seat = TierConfig(adapter="subscription", provider="openai", image="codex-sandbox:1")
    names, volumes = _sandbox_auth(RunnerConfig(), seat, 3)

    assert names == ()
    assert volumes == {"torve-auth-3": "/auth"}


def test_the_adapter_no_longer_decides_the_route():
    """The defect the first live dispatch found: keying on the adapter dropped
    a subscription seat's `api_key_env` and mounted a volume whether it
    declared one or not, so the agent reported `Not logged in` against an
    empty mount."""

    from torve.application.session import _sandbox_auth

    for adapter in ("api", "harness", "subscription"):
        assert _auth(TierConfig(adapter=adapter, provider="p", image="i")) == (("K",), {}), adapter

    assert _sandbox_auth(RunnerConfig(), TierConfig(), 0) == ((), {})


# ....................... #
# Equipment lands where torve owns it (S-0063/D-19)


def test_the_equipment_root_is_excluded_in_the_worktree(tmp_path):
    """`commit_all` runs `git add -A` and the scope gate reads the committed
    diff, so anything `equip` drops into the workspace is in the candidate
    before a gate can object.

    In a *linked worktree* — which is what every attempt runs in — git reads
    `info/exclude` from the common gitdir and never from the worktree's own, so
    the obvious spelling writes a file nothing consults. This asserts on
    `check-ignore`, which is the only thing that proves the rule is live."""

    import subprocess

    from torve.application.session import _exclude_equip_root

    main = tmp_path / "repo"
    main.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(main)], check=True)
    (main / "seed").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(main), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(main),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--no-gpg-sign",
            "-m",
            "seed",
        ],
        check=True,
    )
    worktree = tmp_path / "wt"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(worktree)], check=True)

    _exclude_equip_root(worktree, ".dsh/skills")
    (worktree / ".dsh" / "skills" / "tdd").mkdir(parents=True)
    (worktree / ".dsh" / "skills" / "tdd" / "SKILL.md").write_text("x", encoding="utf-8")

    ignored = subprocess.run(
        ["git", "-C", str(worktree), "check-ignore", ".dsh/skills/tdd/SKILL.md"],
        capture_output=True,
        text=True,
    )

    assert ignored.returncode == 0, "the equipment root is not excluded in the worktree"
    assert (
        ".dsh"
        not in subprocess.run(
            ["git", "-C", str(worktree), "status", "--porcelain", "-uall"],
            capture_output=True,
            text=True,
        ).stdout
    )

    exclude = (main / ".git" / "info" / "exclude").read_text(encoding="utf-8")

    assert "/.dsh/skills/" in exclude.splitlines()

    # Twice is once: an attempt retries, and a file that grows a line per
    # attempt is a file nobody reads.
    _exclude_equip_root(worktree, ".dsh/skills")
    again = (main / ".git" / "info" / "exclude").read_text(encoding="utf-8")

    assert again == exclude


def test_a_harness_reading_the_mount_excludes_nothing(tmp_path):
    """claude points `--add-dir` at the read-only mount, so nothing lands in
    the workspace and there is nothing to hide."""

    import subprocess

    from torve.application.session import _exclude_equip_root

    worktree = tmp_path / "wt"
    worktree.mkdir()
    subprocess.run(["git", "init", "-q", str(worktree)], check=True)
    _exclude_equip_root(worktree, "")
    exclude = worktree / ".git" / "info" / "exclude"

    assert not exclude.is_file() or "/.dsh" not in exclude.read_text(encoding="utf-8")


# ....................... #
# The arm reaches the attempt as a composed prompt (S-0082/D-1, S-0082/D-2)


def _arm_session(tmp_path, *, docker: str = "", nested_roles=None, **arm) -> dict:
    """Drive one attempt under *arm* and hand back what the agent was given,
    what the worktree carried when it looked, and the record the attempt left.
    *docker* and *nested_roles* configure the runtime, so a test can nest the
    attempt's sandbox (S-0111/D-4)."""

    import asyncio
    import json
    import subprocess

    from torve.application.ports import AgentResult, SandboxHandle
    from torve.application.runner import drive_attempts, real_hooks
    from torve.config.runconfig import NestedConfig, RuntimeConfig
    from torve.domain.states import TaskState

    seen: dict = {}

    class LookingAgent:
        kind = "harness"

        def run(self, ctx):
            pack = ctx.workspace / ".torve" / "context" / "index.md"
            seen["prompt"] = ctx.prompt
            seen["pack"] = pack.is_file()
            seen["contract"] = (ctx.workspace / ".torve" / "tasks" / TASK_ID).is_dir()
            seen["skills"] = sorted(p.name for p in (ctx.workspace / ".torve" / "skills").iterdir())

            return AgentResult(exit_code=1, output="", cost_usd=0.0, model_version="m")

    class InertRuntime:
        def create(self, spec, workspace):
            seen["nested"] = spec.nested

            return SandboxHandle(id="h-1", name=spec.name)

        def resolve_image(self, image):
            return None

        def sync_out(self, handle, worktree):
            pass

        def destroy(self, handle):
            pass

    worktree = tmp_path / "wt"
    (worktree / ".torve" / "skills").mkdir(parents=True)
    (worktree / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\ngates: []\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=worktree, check=True)

    config = RunnerConfig(
        poison_ceiling=1,
        runtime=RuntimeConfig(docker=docker, nested=NestedConfig(roles=nested_roles or ["gates"])),
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": TierConfig(adapter="harness", provider="p", model="m"),
        },
    )
    task = Task(id=TASK_ID, decisions=[], intent="make the thing work, and say why")
    deps = RunDeps(
        workspace=None,  # type: ignore[arg-type]
        runtime=InertRuntime(),  # type: ignore[arg-type]
        agent=LookingAgent(),  # type: ignore[arg-type]
        vcs=None,  # type: ignore[arg-type]
        scm=None,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
    )
    state = RunState(task_id=task.id, path=tmp_path / "state.json")
    state.transition(TaskState.CLAIMED, "test claim")

    asyncio.run(
        drive_attempts(
            state,
            task,
            config,
            real_hooks(tmp_path, task, config, deps, worktree, shadow=True, **arm),
        )
    )

    ledger = (tmp_path / ".torve" / "telemetry.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in ledger.splitlines() if line.strip()]
    seen["agent_block"] = next(row["agent"] for row in rows if "agent" in row)

    return seen


# ....................... #


@pytest.mark.parametrize("arm", [{"arm": "bare"}, {"bare": True}, {"arm": "gated"}])
def test_an_arm_whose_prompt_is_bare_is_handed_it_and_nothing_else(tmp_path, arm):
    """S-0082/D-1: the prompt's removal is a property of the replay, handed to
    the session through the composed-prompt seam the review leg already uses —
    so the adapter composes nothing and the intent is the whole prompt.

    S-0082/D-2: the worktree is stripped of what the runner would have written
    into it — the projected contract, the skill set, the context pack — and the
    record names the removals that were in force, so a reader of the arm's
    numbers can tell what the agent could still open."""

    seen = _arm_session(tmp_path, **arm)

    assert seen["prompt"] == f"# Torve task {TASK_ID}\n\nmake the thing work, and say why"
    assert not seen["pack"]
    assert not seen["contract"]
    assert seen["skills"] == []
    assert seen["agent_block"]["arm"] == arm.get("arm", "bare")
    assert "prompt" in seen["agent_block"]["removed"]
    assert "context-pack" in seen["agent_block"]["removed"]
    assert ("battery" in seen["agent_block"]["removed"]) == (arm.get("arm", "bare") == "bare")


# ....................... #


def test_the_configured_arm_composes_its_prompt_in_the_adapter_as_it_did(tmp_path):
    """The other side of S-0082/D-1: torve as configured hands the adapter no
    prompt — composing one is the adapter's, from the apparatus the runner
    writes into the worktree — and its record names no removal at all."""

    seen = _arm_session(tmp_path)

    assert seen["prompt"] is None
    assert seen["pack"]
    assert "removed" not in seen["agent_block"]
    assert "arm" not in seen["agent_block"]


# ....................... #
# The attempt's sandbox is nested when the configuration nests its role
# (S-0111/D-4): the same mark the gates sandbox always got, resolved from
# `runtime.nested.roles` so `implement` has to be named.


def test_the_attempt_sandbox_is_nested_when_implement_is_nested(tmp_path):
    seen = _arm_session(tmp_path, docker="nested", nested_roles=["gates", "implement"])

    assert seen["nested"] is not None
    assert seen["nested"].image == "docker:29.7.2-dind"
    # `_arm_session` drives a shadow run, which starts cold (S-0035/D-3): its
    # daemon gets a throwaway volume, never the slot's `torve-dind-0`.
    assert seen["nested"].volume == ""


def test_the_attempt_sandbox_is_not_nested_when_only_gates_is(tmp_path):
    """The default nests the gates role alone (S-0111/D-4), so an attempt's
    sandbox carries no daemon of its own until `implement` is named."""

    seen = _arm_session(tmp_path, docker="nested")

    assert seen["nested"] is None


# ....................... #
# Two candidates of one task never share a container or a gitdir (S-0124/D-5).


def test_candidate_names_carry_the_candidate_and_keep_task_labels():
    """S-0124/D-5: a candidate after the first gets `-c<k>` on its attempt sandbox, gates
    sandbox and sandbox gitdir names; the labels stay keyed by task, so
    `torve reap` still finds every candidate's sandbox."""

    from pathlib import Path

    from test_runner import _gate_dispatch

    from torve.application import session
    from torve.application.runner import _gates_spec
    from torve.base import naming

    state = RunState(task_id=TASK_ID, path=Path("state.json"), run_id="run-1234")
    run = _gate_dispatch(root=Path("/repo"), worker_slot=2)

    assert session.candidate_suffix(1) == ""
    assert session.candidate_suffix(2) == "-c2"

    assert session.attempt_sandbox_name(TASK_ID, state, 1) == "torve-t-0001-run-1234-a0"
    assert session.attempt_sandbox_name(TASK_ID, state, 2) == "torve-t-0001-run-1234-a0-c2"

    assert session.sandbox_gitdir_name(TASK_ID, 1) == TASK_ID
    assert session.sandbox_gitdir_name(TASK_ID, 3) == f"{TASK_ID}-c3"

    run.candidate = 1
    first = _gates_spec(run, state)
    run.candidate = 2
    second = _gates_spec(run, state)

    assert first.name.endswith("-gates") and not first.name.endswith("-c1")
    assert second.name.endswith("-gates-c2")
    assert second.labels == first.labels == naming.labels(run.task.id, state.run_id, run.root)


def test_candidate_names_reach_the_sandboxes_a_unit_creates(
    review_rig,  # noqa: F811
) -> None:
    """S-0124/D-5: the session builds candidate 2's attempt sandbox under the
    `-c2` name, so two candidates of one attempt never share a sandbox name,
    and both carry the task's labels."""

    from test_critic import answer
    from test_run_loop import ScriptedAgent, task_for

    import torve.application.runner as run_module
    from torve.application.ports import AgentResult
    from torve.config.runconfig import ReviewConfig

    repo, runtime, deps_for = review_rig
    config = RunnerConfig(review=ReviewConfig(on=["task_gated"], mode="critic"))
    deps = deps_for(ScriptedAgent([AgentResult(exit_code=0, output=answer())]))

    run_module.run_task(repo.root, task_for(repo), config, deps)

    # The target's own attempt sandboxes; the critic's review runs under its own task.
    attempts = [
        spec for spec in runtime.specs if "-t-9001-" in spec.name and "-gates" not in spec.name
    ]
    names = sorted(spec.name for spec in attempts)

    assert len(names) == 2
    assert names[0].endswith("-a1") and names[1] == names[0] + "-c2"
    assert attempts[0].labels == attempts[1].labels


# ....................... #
# The attempt's sandbox runs no git (S-0113/D-2): the host leaves the
# worktree's tracked files where the citation scan reads them, before the
# sandbox starts.


def test_the_session_leaves_the_tracked_list_before_the_attempt_sandbox(tmp_path):
    import asyncio
    import subprocess

    from torve.application.ports import AgentResult, SandboxHandle
    from torve.application.runner import drive_attempts, real_hooks
    from torve.config.runconfig import RuntimeConfig
    from torve.domain.states import TaskState

    seen: dict = {}

    class LookingAgent:
        kind = "harness"

        def run(self, ctx):
            return AgentResult(exit_code=1, output="", cost_usd=0.0, model_version="m")

    class ListRecordingRuntime:
        def create(self, spec, workspace):
            seen["tracked"] = (workspace / ".torve" / "tmp" / "tracked-files").read_text(
                encoding="utf-8"
            )

            return SandboxHandle(id="h-1", name=spec.name)

        def resolve_image(self, image):
            return None

        def sync_out(self, handle, worktree):
            pass

        def destroy(self, handle):
            pass

    worktree = tmp_path / "wt"
    (worktree / ".torve" / "skills").mkdir(parents=True)
    (worktree / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\ngates: []\n", encoding="utf-8"
    )
    (worktree / "src").mkdir()
    (worktree / "src" / "thing.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=worktree, check=True)
    subprocess.run(["git", "-C", str(worktree), "add", "-A"], check=True)

    config = RunnerConfig(
        poison_ceiling=1,
        runtime=RuntimeConfig(),
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": TierConfig(adapter="harness", provider="p", model="m"),
        },
    )
    task = Task(id=TASK_ID, decisions=[], intent="make the thing work, and say why")
    deps = RunDeps(
        workspace=None,  # type: ignore[arg-type]
        runtime=ListRecordingRuntime(),  # type: ignore[arg-type]
        agent=LookingAgent(),  # type: ignore[arg-type]
        vcs=None,  # type: ignore[arg-type]
        scm=None,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
    )
    state = RunState(task_id=task.id, path=tmp_path / "state.json")
    state.transition(TaskState.CLAIMED, "test claim")

    asyncio.run(
        drive_attempts(
            state, task, config, real_hooks(tmp_path, task, config, deps, worktree, shadow=True)
        )
    )

    expected = subprocess.run(
        ["git", "-C", str(worktree), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert seen["tracked"] == expected
    assert "src/thing.py" in expected


# ----------------------- #


def _halt_log(tmp_path, *entries):
    from torve.config import layout

    log = layout.log_file(tmp_path, TASK_ID)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        yaml.safe_dump({"schema_version": 1, "task": TASK_ID, "entries": list(entries)}),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([], None),
        ([{"grade": "ASSUMED", "class": "discovery", "action": "departed"}], None),
        ([{"grade": "LOCKED", "class": "discovery", "action": "halted"}], "locked_conflict"),
        ([{"grade": "UNLISTED", "class": "spec-gap", "action": "halted"}], "underspecified"),
        ([{"grade": "ASSUMED", "class": "drift", "action": "halted"}], "underspecified"),
        (
            [
                {"grade": "UNLISTED", "class": "spec-gap", "action": "halted"},
                {"grade": "LOCKED", "class": "spec-gap", "action": "halted"},
            ],
            "locked_conflict",
        ),
    ],
)
def test_a_halted_entry_picks_its_escalation(tmp_path, entries, expected):
    """S-0096/D-1: a cited LOCKED row escalates locked_conflict; every other
    halt, whatever its class, escalates underspecified."""
    from torve.application.session import _halt_reason

    _halt_log(tmp_path, *entries)

    assert _halt_reason(tmp_path, TASK_ID, 1) == expected


def test_no_log_means_no_halt(tmp_path):
    from torve.application.session import _halt_reason

    assert _halt_reason(tmp_path, TASK_ID, 1) is None


def test_an_earlier_attempt_s_halt_does_not_halt_this_one(tmp_path):
    """A task requeued after a halt keeps that halt in its append-only log;
    only the attempt's own entries decide whether it halted, so a later
    attempt that stops is judged by its gates rather than escalated on the
    old entry (T-0547, 2026-10-12)."""
    from torve.application.session import _halt_reason

    _halt_log(
        tmp_path,
        {"grade": "UNLISTED", "class": "spec-gap", "action": "halted", "attempt": 1},
        {"grade": "ASSUMED", "class": "discovery", "action": "decided", "attempt": 2},
    )

    assert _halt_reason(tmp_path, TASK_ID, 1) == "underspecified"
    assert _halt_reason(tmp_path, TASK_ID, 4) is None


# ....................... #
# S-0127/D-8: a review round's attempt that declines one claim goes on to its
# gates with its other fixes; a LOCKED row's halt still asks the owner.

# T-0894's shape: a round of seven findings, one file each.
ROUND_FILES = tuple(f"src/part{n}.py" for n in range(1, 8))


def _round_task(task_id: str = TASK_ID) -> Task:
    return Task(
        id=task_id,
        decisions=[],
        round={
            "branch": "torve/S-0001",
            "findings": [
                {"path": path, "line": 3, "threads": [f"thread-{n}"]}
                for n, path in enumerate(ROUND_FILES, 1)
            ],
        },
    )


def _declining(path: str, grade: str = "UNLISTED") -> dict:
    """A halt on the claim at *path*, as `torve abort` writes it: on no row, or
    on a LOCKED one (`divergence.halts`)."""

    return {
        "decision": "S-0001/D-1" if grade == "LOCKED" else "unlisted",
        "grade": grade,
        "kind": "contradicted" if grade == "LOCKED" else "blocked",
        "action": "halted",
        "attempt": 1,
        "claim": f"{path}:3 asks for a schema change outside this round",
        "evidence": "src/schema.py:1 — the schema is another phase's",
    }


class _Notes:
    def __init__(self, notes: list[dict]) -> None:
        self.sent = notes

    def notes(self) -> list[dict]:
        return self.sent


@pytest.mark.parametrize(
    ("halts", "expected"),
    [
        # One claim of seven declined: the attempt goes on to its gates.
        ([_declining(ROUND_FILES[6])], None),
        # Two declined, five still standing.
        ([_declining(ROUND_FILES[0]), _declining(ROUND_FILES[6])], None),
        # Named in the evidence alone.
        (
            [
                {
                    **_declining(ROUND_FILES[6]),
                    "claim": "the migration is out of scope",
                    "evidence": f"{ROUND_FILES[6]}:3 — the column belongs to S-0002",
                }
            ],
            None,
        ),
        # A halt naming no finding of the round ends the attempt.
        (
            [{**_declining(ROUND_FILES[6]), "claim": "the contract is unclear"}],
            "underspecified",
        ),
        # So does one naming none beside one naming a finding.
        (
            [
                _declining(ROUND_FILES[6]),
                {**_declining(ROUND_FILES[0]), "claim": "the contract is unclear"},
            ],
            "underspecified",
        ),
        # Halts naming every finding decline the whole round.
        ([_declining(path) for path in ROUND_FILES], "underspecified"),
        # A LOCKED row's halt asks the owner, whatever it names.
        ([_declining(ROUND_FILES[6], grade="LOCKED")], "locked_conflict"),
        (
            [_declining(ROUND_FILES[6]), _declining(ROUND_FILES[0], grade="LOCKED")],
            "locked_conflict",
        ),
    ],
)
def test_halted_claim_passes_over_a_declined_claim_while_another_stands(tmp_path, halts, expected):
    """S-0127/D-8: a halt citing no LOCKED row whose claim or evidence names
    one of the round's findings ends nothing while some finding is named by no
    halt; a halt naming none, halts naming all, and a LOCKED row's halt
    (S-0096/D-1) end the attempt as before."""

    from torve.application.session import halted

    run = _dispatch(tmp_path)
    run.task = _round_task()
    _halt_log(tmp_path, *halts)

    assert halted(run, 1) == expected


def test_halted_claim_declines_nothing_outside_a_review_round(tmp_path):
    """S-0127/D-8 reads a round's findings; a task that is no round has none,
    so the same halt escalates `underspecified` as before."""

    from torve.application.session import halted

    run = _dispatch(tmp_path)
    _halt_log(tmp_path, _declining(ROUND_FILES[6]))

    assert halted(run, 1) == "underspecified"


def test_halted_claim_an_urgent_note_still_stops_the_attempt(tmp_path):
    """S-0127/D-8 passes over a declined claim, never over the operator: an
    urgent note sent while the attempt ran still ends it as `killed`."""

    from dataclasses import replace

    from torve.application.session import halted

    run = _dispatch(tmp_path)
    run.task = _round_task()
    run.meta.update(started_at="2026-11-12T01:00:00Z", ended_at="2026-11-12T02:00:00Z")
    run.deps = replace(
        run.deps,
        channel=_Notes([{"urgent": True, "at": "2026-11-12T01:30:00Z"}]),  # type: ignore[arg-type]
    )
    _halt_log(tmp_path, _declining(ROUND_FILES[6]))

    assert halted(run, 1) == "killed"


def _round_run(repo, monkeypatch, halt: dict):
    """A run of T-0894's round whose agent fixes six claims and halts on the
    seventh with *halt*, over a gate pass that records that it ran."""

    from test_run_loop import MockRuntime, MockScm, MockVcs, MockWorkspace

    import torve.application.runner as run_module
    from torve.adapters.store.durable import open_store
    from torve.application.ports import AgentResult
    from torve.application.runner import run_task
    from torve.config import layout

    class DecliningAgent:
        kind = "fake"

        def run(self, ctx):
            for path in ROUND_FILES[:6]:
                fixed = ctx.workspace / path
                fixed.parent.mkdir(parents=True, exist_ok=True)
                fixed.write_text("FIXED = True\n", encoding="utf-8")

            log = layout.log_file(ctx.workspace, ctx.task.id)
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(
                yaml.safe_dump({"schema_version": 1, "task": ctx.task.id, "entries": [halt]}),
                encoding="utf-8",
            )

            return AgentResult(exit_code=0, output="")

    repo.seed()
    deps = RunDeps(
        workspace=MockWorkspace(repo.root),
        runtime=MockRuntime(),  # type: ignore[arg-type]
        agent=DecliningAgent(),
        vcs=MockVcs(),  # type: ignore[arg-type]
        scm=MockScm(),  # type: ignore[arg-type]
        store=open_store,
    )
    judged: list[list[str]] = []

    def gates(run, _state):
        judged.append(sorted(str(p.relative_to(run.worktree)) for p in run.worktree.glob("src/*")))
        return 0, "scripted", "cafecafe1234", [], "diff --git a/x b/x"

    monkeypatch.setattr(run_module, "run_gate_pass", gates)
    state = run_task(repo.root, _round_task("T-9001"), RunnerConfig(), deps)

    return state, judged


def test_halted_claim_one_declined_claim_of_seven_reaches_the_gates(repo, monkeypatch):
    """S-0127/D-8, T-0894: an attempt that halted on one out-of-scope claim of
    seven goes on to its gates with its six fixes, and lands them."""

    from torve.domain.states import TaskState

    state, judged = _round_run(repo, monkeypatch, _declining(ROUND_FILES[6]))

    assert judged == [sorted(ROUND_FILES[:6])]
    assert state.escalation is None
    assert state.state is TaskState.READY


def test_locked_halt_on_one_claim_of_seven_escalates_locked_conflict(repo, monkeypatch):
    """S-0127/D-8, S-0096/D-1: the same halt citing a LOCKED row reaches the
    owner as `locked_conflict` before the round lands; its gates never run."""

    state, judged = _round_run(repo, monkeypatch, _declining(ROUND_FILES[6], grade="LOCKED"))

    assert judged == []
    assert state.escalation is not None
    assert state.escalation.reason == "locked_conflict"


def test_the_sandbox_git_is_a_standalone_copy_of_the_attempt_s_head(repo, tmp_path):
    """The worktree's `.git` file points at the host's git directory, which no
    sandbox mounts. The copy the sandbox reads instead answers `status`, `log`
    and `rev-parse` as the worktree does, and carries none of the host's
    config or its other branches."""
    import shutil

    from torve.adapters.workspace.git import GitWorkspace
    from torve.application.session import _sandbox_gitdir
    from torve.gates.context import git

    repo.seed()
    repo.git("branch", "elsewhere")
    repo.git("remote", "add", "origin", "https://user:secret@forge.invalid/repo.git")
    worktree = GitWorkspace(repo.root).create(TASK_ID, "main")
    (worktree / "new.py").write_text("NEW = 1\n", encoding="utf-8")
    # A split index is two files; the copy needs its shared half to read it.
    git(worktree, "update-index", "--split-index")

    gitdir = _sandbox_gitdir(repo.root, worktree, TASK_ID)

    # What the sandbox sees: the same files, its `.git` resolving to the copy.
    view = tmp_path / "view"
    shutil.copytree(worktree, view, ignore=shutil.ignore_patterns(".git"))
    (view / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")

    assert git(view, "rev-parse", "HEAD") == git(worktree, "rev-parse", "HEAD")
    assert git(view, "status", "--porcelain") == "?? new.py\n"
    assert git(view, "log", "--oneline")
    assert "elsewhere" not in git(view, "branch", "--all")
    assert "secret" not in git(view, "config", "--list")

    # A worktree whose `.git` is a directory already has git of its own.
    assert _sandbox_gitdir(repo.root, repo.root, "clone") == ""


# ....................... #
# A candidate's session and the sandbox it ran in (S-0124/D-4, S-0124/D-8).


class _SessionAgent:
    kind = "harness"

    def run(self, ctx):
        from torve.application.ports import AgentResult

        return AgentResult(exit_code=0, output="first", cost_usd=0.1)


def test_a_session_ending_leaves_its_sibling_candidates_sandboxes(repo, monkeypatch):
    # S-0124/D-4, T-0848: side by side, the first candidate to finish cleared
    # the one `sandbox_id` while its siblings' sandboxes were live, so a kill
    # or a reap left them running.
    from test_run_loop import MockRuntime, MockScm, MockVcs, MockWorkspace, task_for

    import torve.application.runner as run_module
    from torve.adapters.store.durable import open_store
    from torve.application.runner import run_task

    repo.seed()
    deps = RunDeps(
        workspace=MockWorkspace(repo.root),
        runtime=MockRuntime(),  # type: ignore[arg-type]
        agent=_SessionAgent(),
        vcs=MockVcs(),  # type: ignore[arg-type]
        scm=MockScm(),  # type: ignore[arg-type]
        store=open_store,
    )
    held: list[tuple[list[str], str | None]] = []
    session = run_module.run_agent_session

    async def beside_a_sibling(run, state):
        state.sandboxes.append("sbx-sibling")
        result = await session(run, state)
        held.append((list(state.sandboxes), state.sandbox_id))
        state.sandboxes.remove("sbx-sibling")

        return result

    monkeypatch.setattr(
        run_module, "run_gate_pass", lambda run, _state: (0, "scripted", "cafecafe1234", [], "")
    )
    monkeypatch.setattr(run_module, "run_agent_session", beside_a_sibling)
    run_task(repo.root, task_for(repo), RunnerConfig(), deps)

    assert held == [(["sbx-sibling"], "sbx-sibling")]


def test_a_seat_that_cannot_follow_up_is_judged_with_its_never_send_files(
    review_rig,  # noqa: F811
    monkeypatch,
):
    """S-0124/D-8, S-0004 §6b: under `review.revision: session` a seat with
    no `follow_up` keeps no session, so its teardown restores the withheld
    files before the gates judge the tree; the agent still never saw them."""

    from test_critic import answer
    from test_review_run import SequencedReviewer
    from test_run_loop import ScriptedAgent

    import torve.application.runner as run_module
    from torve.application.ports import AgentResult
    from torve.application.runner import run_task
    from torve.config.runconfig import ProvidersConfig, ReviewConfig
    from torve.domain.task import Scope

    repo, _runtime, deps_for = review_rig
    worktree = repo.root / ".wt" / "T-9001"
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / "secret.env").write_text("token\n", encoding="utf-8")
    seen: list[tuple[str, bool]] = []

    def gate_pass(run, _state):
        seen.append(("gates", (run.worktree / "secret.env").is_file()))
        return 0, "scripted", "cafe", [], "diff --git a/x b/x"

    class NoFollowUp(ScriptedAgent):
        kind = "harness"

        def run(self, ctx):
            seen.append(("agent", (ctx.workspace / "secret.env").is_file()))
            return AgentResult(exit_code=0, output="")

    monkeypatch.setattr(run_module, "run_gate_pass", gate_pass)
    config = RunnerConfig(
        providers=ProvidersConfig(never_send=["secret.env"]),
        review=ReviewConfig(on=["task_gated"], mode="critic", revision="session"),
    )
    deps = deps_for(SequencedReviewer([answer(findings=[])]))
    deps.agent = NoFollowUp([])
    task = Task(id="T-9001", decisions=[], scope=Scope(files=["README.md"]))

    run_task(repo.root, task, config, deps)

    assert seen[:2] == [("agent", False), ("gates", True)]
