"""S-0009/evals: the eval loop — with-skill versus without-skill shadow
replays of the same task, arms marked on the shadow records, one eval
record in the ledger, and the baseline verdict as direction only.

S-0034 (S-0034/D-10) extends the paired configuration eval with a
tier-variant override beside the image override: the candidate arm
resolves the dotted variant, the record names it and cites both config
hashes, and the two overrides refuse to combine in one invocation.
"""

from __future__ import annotations

import json
import subprocess
from functools import partial
from types import SimpleNamespace

import pytest
from conftest import harness
from test_shadow import ship
from typer.testing import CliRunner

from torve.adapters.agent.fake import FakeAgent
from torve.adapters.store.durable import open_store
from torve.adapters.vcs.git import GitVcs, NullScm
from torve.adapters.workspace.git import (
    GitWorkspace,
    ShadowWorkspace,
    diff_range,
    diff_worktree,
    parent_of,
    shipped_commit,
)
from torve.application import evals
from torve.application.dispatch import RunDeps
from torve.application.evals import (
    ARMS,
    EVAL_LEDGER,
    candidate_config,
    eligible_tasks,
    run_arm_eval,
    run_arm_shadow,
    run_config_eval,
    run_skill_eval,
    three_arm_table,
    without_skill,
)
from torve.application.shadow import ShadowSource, run_shadow
from torve.cli import app
from torve.config import layout
from torve.config.runconfig import (
    ROLE_SKILLS,
    RunnerConfig,
    RuntimeConfig,
    SkillsConfig,
    TierConfig,
)
from torve.gates.context import load_task
from torve.gates.sabotage import TASK_ID, base_task

# ----------------------- #


def test_without_skill_strips_every_role_set():
    config = RunnerConfig(skills=SkillsConfig(sets=dict(ROLE_SKILLS)))
    stripped = without_skill(config, "flag-dont-flip")
    assert all("flag-dont-flip" not in names for names in stripped.skills.sets.values())
    # The with-arm's configuration is untouched.
    assert "flag-dont-flip" in config.skills.sets["implement"]


def test_a_skill_in_no_role_set_refuses():
    with pytest.raises(ValueError, match="no role set"):
        without_skill(RunnerConfig(), "no-such-skill")


def test_candidate_config_overrides_the_named_tiers_image():
    config = RunnerConfig()
    candidate = candidate_config(config, "executor", "torve-agent:candidate")
    assert candidate.tiers["executor"].image == "torve-agent:candidate"
    # The incumbent's configuration is untouched, and every other tier too.
    assert config.tiers["executor"].image == ""
    assert candidate.tiers["planner"] == config.tiers["planner"]


def test_an_image_the_tier_already_resolves_refuses():
    config = RunnerConfig()
    with pytest.raises(ValueError, match="nothing to measure"):
        candidate_config(config, "executor", config.runtime.image)

    named = RunnerConfig(
        tiers={
            **config.tiers,
            "executor": TierConfig(image="torve-agent:pinned"),
        }
    )
    with pytest.raises(ValueError, match="nothing to measure"):
        candidate_config(named, "executor", "torve-agent:pinned")


def test_an_unknown_tier_refuses_loudly():
    with pytest.raises(ValueError, match="no tier"):
        candidate_config(RunnerConfig(), "no-such-tier", "torve-agent:candidate")


def test_candidate_config_resolves_a_configured_variant_onto_the_seat():
    config = RunnerConfig(
        tiers={
            **RunnerConfig().tiers,
            # S-0034/D-10: a tier variant is a dotted tier entry beside the seat.
            "executor.indexed": TierConfig(model="candidate-model", image="torve-agent:candidate"),
        }
    )

    candidate = candidate_config(config, "executor", variant="indexed")

    # The seat now resolves the variant's content — the candidate arm runs
    # the dotted variant's model, command, adapter or image as itself.
    assert candidate.tiers["executor"] == config.tiers["executor.indexed"]
    assert candidate.tiers["executor"].model == "candidate-model"
    # The incumbent's configuration is untouched, and every other tier too.
    assert config.tiers["executor"].model == ""
    assert candidate.tiers["planner"] == config.tiers["planner"]
    assert candidate.tiers["executor.indexed"] == config.tiers["executor.indexed"]


def test_an_unknown_variant_refuses_loudly():
    config = RunnerConfig(
        tiers={**RunnerConfig().tiers, "executor.indexed": TierConfig(model="candidate-model")}
    )
    with pytest.raises(ValueError, match=r"no tier 'executor\.ghost'"):
        candidate_config(config, "executor", variant="ghost")


def test_a_variant_the_seat_already_resolves_refuses():
    config = RunnerConfig(
        tiers={
            **RunnerConfig().tiers,
            "executor.indexed": TierConfig(),  # identical to the seat — nothing to measure
        }
    )
    with pytest.raises(ValueError, match="nothing to measure"):
        candidate_config(config, "executor", variant="indexed")


def test_image_and_variant_overrides_refuse_to_combine():
    config = RunnerConfig(
        tiers={**RunnerConfig().tiers, "executor.indexed": TierConfig(model="candidate-model")}
    )
    with pytest.raises(ValueError, match="refuse to combine"):
        candidate_config(config, "executor", "torve-agent:candidate", variant="indexed")


def test_candidate_config_needs_an_override():
    with pytest.raises(ValueError, match="needs an override"):
        candidate_config(RunnerConfig(), "executor")


def test_skill_eval_runs_both_arms_and_ledgers(repo):
    from test_runtime_conformance import docker_available

    if not docker_available():
        pytest.skip("docker daemon not available")
    from torve.adapters.runtime.docker import DockerRuntime

    repo.seed()
    task_doc = base_task(allow=["src/**"])
    task_doc["acceptance"] = ["test -f src/feature.py"]
    repo.task(task_doc, None)
    repo.commit("task minted")
    ship(repo)

    config = RunnerConfig(
        runtime=RuntimeConfig(sandbox_timeout=300, agent_timeout=90),
        poison_ceiling=2,
        # S-0061/D-11: a configuration built in Python carries no role sets, since
        # they are read off `.torve/agents/` at load; the arms need one to strip.
        skills=SkillsConfig(sets=dict(ROLE_SKILLS)),
    )
    deps = RunDeps(
        workspace=GitWorkspace(repo.root),
        runtime=DockerRuntime(),
        agent=FakeAgent(
            [
                {"writes": {"src/feature.py": "FEATURE = 'a'\n"}, "exit": 0},
                {"writes": {"src/feature.py": "FEATURE = 'b'\n"}, "exit": 0},
            ]
        ),
        vcs=GitVcs(),
        scm=NullScm(),
        store=open_store,
    )
    shadow_ws = ShadowWorkspace(repo.root, depth=10)
    source = ShadowSource(
        create_workspace=shadow_ws.create,
        shipped_commit=partial(shipped_commit, repo.root),
        parent_of=partial(parent_of, repo.root),
        diff_range=partial(diff_range, repo.root),
        diff_worktree=diff_worktree,
    )
    task = load_task(layout.task_file(repo.root, TASK_ID))

    record = run_skill_eval(repo.root, "flag-dont-flip", [task], config, deps, source)

    assert record["kind"] == "skill-eval" and record["skill"] == "flag-dont-flip"
    assert [r["task"] for r in record["arms"]["with"]] == [TASK_ID]
    assert [r["task"] for r in record["arms"]["without"]] == [TASK_ID]
    # The seat is a fake adapter, so this run rehearses the record's shape
    # and measures nothing: no verdict, and the record says why (A-125).
    assert record["simulated"] is True
    assert record["baseline_matched"] is None

    # One line in the ledger; two arm-marked shadow records in telemetry;
    # attribution shows the without-arm ran skill-less (T-0070).
    ledger = (repo.root / ".torve" / EVAL_LEDGER).read_text().splitlines()
    assert len(ledger) == 1 and json.loads(ledger[0])["skill"] == "flag-dont-flip"
    lines = [
        json.loads(line)
        for line in (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]
    arms = [line["eval"]["arm"] for line in lines if line.get("eval")]
    assert sorted(arms) == ["with", "without"]
    skills_by_shadow = [
        line["agent"]["skills"]
        for line in lines
        if line.get("agent") and line["agent"].get("shadow")
    ]
    assert any("flag-dont-flip" in (s or []) for s in skills_by_shadow)
    assert any("flag-dont-flip" not in (s or []) for s in skills_by_shadow)
    # Never merged: the shipped content is untouched.
    head_file = subprocess.run(
        ["git", "-C", str(repo.root), "show", "HEAD:src/feature.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert head_file == "FEATURE = 'shipped'\n"


def test_config_eval_runs_both_arms_and_ledgers(repo, tmp_path):
    from test_runtime_conformance import docker_available

    if not docker_available():
        pytest.skip("docker daemon not available")
    from torve.adapters.runtime.docker import DockerRuntime

    repo.seed()
    task_doc = base_task(allow=["src/**"])
    task_doc["acceptance"] = ["test -f src/feature.py"]
    repo.task(task_doc, None)
    repo.commit("task minted")
    ship(repo)

    config = RunnerConfig(
        runtime=RuntimeConfig(sandbox_timeout=300, agent_timeout=90),
        poison_ceiling=2,
        # S-0061/D-11: a configuration built in Python carries no role sets, since
        # they are read off `.torve/agents/` at load; the arms need one to strip.
        skills=SkillsConfig(sets=dict(ROLE_SKILLS)),
    )
    runtime = DockerRuntime()

    # A candidate image distinguishable from the incumbent's by a single
    # extra layer — same base, a different digest to measure (S-0027/D-7).
    context = tmp_path / "candidate-image"
    context.mkdir()
    context.joinpath("Dockerfile").write_text(
        f"FROM {config.runtime.image}\nLABEL torve.eval=candidate\n", encoding="utf-8"
    )
    candidate_image = "torve-eval-candidate:test"
    # Built here rather than through the runtime: the engine lost
    # `build_image` with S-0063/D-11, and a test that needs an image builds
    # one the way an operator does.
    built = subprocess.run(
        ["docker", "build", "-t", candidate_image, str(context)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr[-2000:]

    steps = [
        {"writes": {"src/feature.py": "FEATURE = 'a'\n"}, "exit": 0},
        {"writes": {"src/feature.py": "FEATURE = 'b'\n"}, "exit": 0},
    ]
    deps = RunDeps(
        workspace=GitWorkspace(repo.root),
        runtime=runtime,
        agent=FakeAgent(steps),
        vcs=GitVcs(),
        scm=NullScm(),
        store=open_store,
    )
    shadow_ws = ShadowWorkspace(repo.root, depth=10)
    source = ShadowSource(
        create_workspace=shadow_ws.create,
        shipped_commit=partial(shipped_commit, repo.root),
        parent_of=partial(parent_of, repo.root),
        diff_range=partial(diff_range, repo.root),
        diff_worktree=diff_worktree,
    )
    task = load_task(layout.task_file(repo.root, TASK_ID))

    record = run_config_eval(
        repo.root,
        "executor",
        [task],
        config,
        deps,
        source,
        image=candidate_image,
        # Each arm runs the agent its own configuration resolves — the
        # candidate's own, not the incumbent's under a candidate label.
        candidate_agent=FakeAgent(steps),
    )

    assert record["kind"] == "config-eval" and record["tier"] == "executor"
    assert record["image"] == candidate_image
    assert [r["task"] for r in record["arms"]["incumbent"]] == [TASK_ID]
    assert [r["task"] for r in record["arms"]["candidate"]] == [TASK_ID]
    assert isinstance(record["candidate_matched"], bool)
    # Both digests cited, and they name two different regimes (S-0027/D-7).
    assert record["digests"]["incumbent"] and record["digests"]["candidate"]
    assert record["digests"]["incumbent"] != record["digests"]["candidate"]
    # Both config hashes cited too — the regime identity of each arm (S-0034/D-10).
    assert record["configs"]["incumbent"] and record["configs"]["candidate"]
    assert record["configs"]["incumbent"] != record["configs"]["candidate"]

    # One line in the ledger; two arm-marked shadow records in telemetry.
    ledger = (repo.root / ".torve" / EVAL_LEDGER).read_text().splitlines()
    assert len(ledger) == 1 and json.loads(ledger[0])["kind"] == "config-eval"
    lines = [
        json.loads(line)
        for line in (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]
    arms = [line["eval"]["arm"] for line in lines if line.get("eval")]
    assert sorted(arms) == ["candidate", "incumbent"]
    image_digests = {line["image_digest"] for line in lines if line.get("eval")}
    assert len(image_digests) == 2
    # Never merged: the shipped content is untouched.
    head_file = subprocess.run(
        ["git", "-C", str(repo.root), "show", "HEAD:src/feature.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert head_file == "FEATURE = 'shipped'\n"


def test_variant_eval_runs_both_arms_and_ledgers(repo):
    from test_runtime_conformance import docker_available

    if not docker_available():
        pytest.skip("docker daemon not available")
    from torve.adapters.runtime.docker import DockerRuntime

    repo.seed()
    task_doc = base_task(allow=["src/**"])
    task_doc["acceptance"] = ["test -f src/feature.py"]
    repo.task(task_doc, None)
    repo.commit("task minted")
    ship(repo)

    config = RunnerConfig(
        runtime=RuntimeConfig(sandbox_timeout=300, agent_timeout=90),
        poison_ceiling=2,
        tiers={
            **RunnerConfig().tiers,
            # S-0034/D-10: the candidate arm resolves the dotted variant.
            "executor.indexed": TierConfig(model="candidate-model"),
        },
    )
    steps = [
        {"writes": {"src/feature.py": "FEATURE = 'a'\n"}, "exit": 0},
        {"writes": {"src/feature.py": "FEATURE = 'b'\n"}, "exit": 0},
    ]
    deps = RunDeps(
        workspace=GitWorkspace(repo.root),
        runtime=DockerRuntime(),
        agent=FakeAgent(steps),
        vcs=GitVcs(),
        scm=NullScm(),
        store=open_store,
    )
    shadow_ws = ShadowWorkspace(repo.root, depth=10)
    source = ShadowSource(
        create_workspace=shadow_ws.create,
        shipped_commit=partial(shipped_commit, repo.root),
        parent_of=partial(parent_of, repo.root),
        diff_range=partial(diff_range, repo.root),
        diff_worktree=diff_worktree,
    )
    task = load_task(layout.task_file(repo.root, TASK_ID))

    record = run_config_eval(
        repo.root,
        "executor",
        [task],
        config,
        deps,
        source,
        variant="indexed",
        candidate_agent=FakeAgent(steps),
    )

    assert record["kind"] == "config-eval" and record["tier"] == "executor"
    # S-0034/D-10: the record names the dotted variant the candidate arm resolved.
    assert record["variant"] == "executor.indexed"
    assert [r["task"] for r in record["arms"]["incumbent"]] == [TASK_ID]
    assert [r["task"] for r in record["arms"]["candidate"]] == [TASK_ID]
    assert isinstance(record["candidate_matched"], bool)
    # Both config hashes cited, and they name two different regimes.
    assert record["configs"]["incumbent"] and record["configs"]["candidate"]
    assert record["configs"]["incumbent"] != record["configs"]["candidate"]
    # A variant eval is a config measurement, not an image displacement: no
    # image, no digests to feed the image-displacement guard (S-0027/D-7).
    assert "image" not in record and "digests" not in record

    # One line in the ledger; two arm-marked shadow records in telemetry,
    # both stamped with the dotted variant they replayed under.
    ledger = (repo.root / ".torve" / EVAL_LEDGER).read_text().splitlines()
    assert len(ledger) == 1 and json.loads(ledger[0])["variant"] == "executor.indexed"
    lines = [
        json.loads(line)
        for line in (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]
    arms = [line["eval"]["arm"] for line in lines if line.get("eval")]
    assert sorted(arms) == ["candidate", "incumbent"]
    variants = {line["eval"]["variant"] for line in lines if line.get("eval")}
    assert variants == {"executor.indexed"}
    # Never merged: the shipped content is untouched.
    head_file = subprocess.run(
        ["git", "-C", str(repo.root), "show", "HEAD:src/feature.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert head_file == "FEATURE = 'shipped'\n"


def test_bare_arm_replays_without_the_battery_and_still_merges_nothing(repo):
    from test_runtime_conformance import docker_available

    if not docker_available():
        pytest.skip("docker daemon not available")
    from torve.adapters.runtime.docker import DockerRuntime

    repo.seed()
    task_doc = base_task(allow=["src/**"])
    task_doc["acceptance"] = ["test -f src/feature.py"]
    repo.task(task_doc, None)
    repo.commit("task minted")
    ship(repo)

    manifest_before = (repo.root / ".torve" / "gates.yaml").read_bytes()

    config = RunnerConfig(
        runtime=RuntimeConfig(sandbox_timeout=300, agent_timeout=90),
        poison_ceiling=2,
        # S-0061/D-11: a configuration built in Python carries no role sets, since
        # they are read off `.torve/agents/` at load; the arms need one to strip.
        skills=SkillsConfig(sets=dict(ROLE_SKILLS)),
    )
    steps = [
        {"writes": {"src/feature.py": "FEATURE = 'a'\n"}, "exit": 0},
        {"writes": {"src/feature.py": "FEATURE = 'b'\n"}, "exit": 0},
        {"writes": {"src/feature.py": "FEATURE = 'c'\n"}, "exit": 0},
    ]
    deps = RunDeps(
        workspace=GitWorkspace(repo.root),
        runtime=DockerRuntime(),
        agent=FakeAgent(steps),
        vcs=GitVcs(),
        scm=NullScm(),
        store=open_store,
    )
    shadow_ws = ShadowWorkspace(repo.root, depth=10)
    source = ShadowSource(
        create_workspace=shadow_ws.create,
        shipped_commit=partial(shipped_commit, repo.root),
        parent_of=partial(parent_of, repo.root),
        diff_range=partial(diff_range, repo.root),
        diff_worktree=diff_worktree,
    )
    task = load_task(layout.task_file(repo.root, TASK_ID))

    # The same task, the same harness — once judged by the battery, once not.
    gated = run_shadow(repo.root, task, config, deps, source, annotation={"arm": "gated"})
    bare = run_arm_shadow(repo.root, task, config, deps, source, "bare")

    # The bare arm reached green with no battery judging anything.
    assert bare["state"] == "ready" and bare["attempts"] == 1
    # The battery's removal travels as a property of the replay…
    assert bare["eval"]["arm"] == "bare"
    # …never as an edit to the gate manifest: the manifest every other
    # attempt is judged by never changed (S-0074/D-3), so the bare arm
    # replayed under the same regime the gated arm did.
    assert (repo.root / ".torve" / "gates.yaml").read_bytes() == manifest_before
    assert bare["config_hash"] == gated["config_hash"]
    # A bare arm still merges nothing (S-0004/D-4): the shipped content is
    # untouched on the branch.
    head_file = subprocess.run(
        ["git", "-C", str(repo.root), "show", "HEAD:src/feature.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert head_file == "FEATURE = 'shipped'\n"


# ....................... #
# CLI: parsing only — the run_skill_eval/run_config_eval split above stays
# unexercised here (needs a real agent); this is the argument-shape contract.


def _bare_task_repo(root, tier: str = "executor"):
    (root / ".torve" / "tasks" / "T-0042").mkdir(parents=True)
    harness(root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    (root / ".torve" / "tasks" / "T-0042" / "contract.yaml").write_text(
        f"schema_version: 1\nid: T-0042\ntier: {tier}\ndecisions: []\n", encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "task minted"], check=True, capture_output=True
    )


def test_eval_cli_refuses_neither_skill_nor_tier_with_an_override(tmp_path):
    root = tmp_path / "repo"
    _bare_task_repo(root)

    result = CliRunner().invoke(app, ["eval", "--task", "T-0042", "--root", str(root)])

    assert result.exit_code == 3
    assert "give a skill argument, or --tier with either --image or --variant" in result.stderr


def test_eval_cli_refuses_a_skill_together_with_config_overrides(tmp_path):
    root = tmp_path / "repo"
    _bare_task_repo(root)

    for overrides in (
        ["--tier", "executor"],
        ["--tier", "executor", "--variant", "indexed"],
    ):
        result = CliRunner().invoke(
            app, ["eval", "flag-dont-flip", "--task", "T-0042", "--root", str(root), *overrides]
        )

        assert result.exit_code == 3
        assert "not both" in result.stderr


def test_eval_cli_refuses_tier_without_an_override(tmp_path):
    root = tmp_path / "repo"
    _bare_task_repo(root)

    result = CliRunner().invoke(
        app, ["eval", "--task", "T-0042", "--tier", "executor", "--root", str(root)]
    )

    assert result.exit_code == 3
    assert "give a skill argument, or --tier with either --image or --variant" in result.stderr


def test_eval_cli_refuses_image_and_variant_together(tmp_path):
    root = tmp_path / "repo"
    _bare_task_repo(root)

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "--task",
            "T-0042",
            "--tier",
            "executor",
            "--image",
            "torve-agent:candidate",
            "--variant",
            "indexed",
            "--root",
            str(root),
        ],
    )

    assert result.exit_code == 3
    assert "refuse to combine" in result.stderr


def test_eval_cli_refuses_an_unknown_variant(tmp_path):
    root = tmp_path / "repo"
    _bare_task_repo(root)

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "--task",
            "T-0042",
            "--tier",
            "executor",
            "--variant",
            "ghost",
            "--root",
            str(root),
        ],
    )

    assert result.exit_code == 3
    assert "no tier 'executor.ghost'" in result.stderr


def test_eval_cli_config_mode_refuses_an_already_resolved_variant(tmp_path):
    root = tmp_path / "repo"
    _bare_task_repo(root)
    # The variant is configured, but identical to the seat — nothing to measure.
    (root / ".torve" / "config.yaml").write_text(
        "tiers:\n  executor: {harness: fake}\n  executor.indexed: {harness: fake}\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "--task",
            "T-0042",
            "--tier",
            "executor",
            "--variant",
            "indexed",
            "--root",
            str(root),
        ],
    )

    assert result.exit_code == 3
    assert "nothing to measure" in result.stderr


def test_eval_cli_config_mode_refuses_an_already_resolved_image(tmp_path):
    root = tmp_path / "repo"
    _bare_task_repo(root)
    default_image = RunnerConfig().runtime.image

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "--task",
            "T-0042",
            "--tier",
            "executor",
            "--image",
            default_image,
            "--root",
            str(root),
        ],
    )

    assert result.exit_code == 3
    assert "nothing to measure" in result.stderr


# ....................... #


def test_arm_axis_names_what_it_removes():
    assert ARMS == ("bare", "gated", "configured")


def _rows_by_arm(**arms):
    return {arm: [row] for arm, row in arms.items()}


def test_three_arm_table_rebuilds_from_ledger_alone(tmp_path):
    """S-0074/D-1: the reader rebuilds the per-task three-arm table from the
    ledger alone — each axis arm's latest row for the task, nothing else."""
    root = tmp_path / "repo"
    ledger = root / layout.TORVE_DIR / EVAL_LEDGER
    ledger.parent.mkdir(parents=True)
    rows = {
        "bare": {
            "arm": "bare",
            "task": "T-0042",
            "state": "ready",
            "attempts": 3,
            "cost_usd": 0.01,
        },
        "gated": {
            "arm": "gated",
            "task": "T-0042",
            "state": "ready",
            "attempts": 4,
            "cost_usd": 0.02,
        },
        "configured": {
            "arm": "configured",
            "task": "T-0042",
            "state": "ready",
            "attempts": 5,
            "cost_usd": 0.03,
        },
    }
    record = {"schema_version": 1, "kind": "skill-eval", "arms": _rows_by_arm(**rows)}
    ledger.write_text(json.dumps(record) + "\n")

    assert three_arm_table(root) == {"T-0042": rows}


def test_three_arm_table_ignores_everything_off_the_axis(tmp_path):
    """Arms that are not apparatus removals — the config eval's incumbent
    and candidate, a task-less row, a line that is not JSON — contribute
    nothing to the three-arm table."""
    root = tmp_path / "repo"
    ledger = root / layout.TORVE_DIR / EVAL_LEDGER
    ledger.parent.mkdir(parents=True)
    below_axis = _rows_by_arm(
        incumbent={
            "arm": "incumbent",
            "task": "T-0042",
            "state": "ready",
            "attempts": 2,
            "cost_usd": 0.01,
        },
        candidate={
            "arm": "candidate",
            "task": "T-0042",
            "state": "ready",
            "attempts": 1,
            "cost_usd": 0.02,
        },
    )
    lines = [
        json.dumps({"schema_version": 1, "kind": "config-eval", "arms": below_axis}),
        json.dumps(
            {"schema_version": 1, "kind": "skill-eval", "arms": {"bare": [{"arm": "bare"}]}}
        ),
        "not json",
    ]
    ledger.write_text("\n".join(lines) + "\n")

    assert three_arm_table(root) == {}


def test_three_arm_table_latest_line_wins(tmp_path):
    """The ledger is append-only; the last line for a task-arm pair is the
    most recent measurement and replaces any earlier for that arm."""
    root = tmp_path / "repo"
    ledger = root / layout.TORVE_DIR / EVAL_LEDGER
    ledger.parent.mkdir(parents=True)
    older = _rows_by_arm(
        bare={"arm": "bare", "task": "T-0042", "state": "ready", "attempts": 3, "cost_usd": 0.01}
    )
    newer = _rows_by_arm(
        bare={"arm": "bare", "task": "T-0042", "state": "ready", "attempts": 6, "cost_usd": 0.02}
    )
    lines = [
        json.dumps({"schema_version": 1, "kind": "skill-eval", "arms": older}),
        json.dumps({"schema_version": 1, "kind": "skill-eval", "arms": newer}),
    ]
    ledger.write_text("\n".join(lines) + "\n")

    assert three_arm_table(root)["T-0042"]["bare"]["attempts"] == 6


# ....................... #
# The reader (S-0074/D-4): three rows per task, four columns, and any
# summary a distribution rather than a mean.


def _ledger(root, *records):
    ledger = root / layout.TORVE_DIR / EVAL_LEDGER
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        "".join(
            json.dumps({"schema_version": 1, "kind": "skill-eval", "arms": arms}) + "\n"
            for arms in records
        ),
        encoding="utf-8",
    )


def _arm_row(arm, task, state="ready", attempts=1, cost_usd=0.01):
    return {"arm": arm, "task": task, "state": state, "attempts": attempts, "cost_usd": cost_usd}


def test_eval_report_renders_three_rows_and_four_columns_per_task(tmp_path):
    """Each task is read across its three arms with the four columns, and
    an arm that never ran the task is a dash rather than a missing row."""
    root = tmp_path / "repo"
    _ledger(
        root,
        _rows_by_arm(
            bare=_arm_row("bare", "T-0042", state="escalated", attempts=3, cost_usd=0.5),
            gated=_arm_row("gated", "T-0042", attempts=2, cost_usd=0.25),
        ),
    )

    result = CliRunner().invoke(app, ["eval", "--report", "--root", str(root)])

    assert result.exit_code == 0
    assert "T-0042" in result.stdout

    for column in ("arm", "state", "attempts", "cost usd"):
        assert column in result.stdout

    for arm in ARMS:
        assert arm in result.stdout

    assert "escalated" in result.stdout
    assert "0.5000" in result.stdout
    # The configured arm never ran this task: a row that says so.
    assert "-" in result.stdout


def test_eval_report_summarises_as_a_distribution_never_a_mean(tmp_path):
    """Every task stays visible under the arms that went green for it —
    the task where the bare arm shipped what the battery refused is a row
    of its own, not a percentage."""
    root = tmp_path / "repo"
    _ledger(
        root,
        _rows_by_arm(
            bare=_arm_row("bare", "T-0042"),
            gated=_arm_row("gated", "T-0042", state="escalated"),
            configured=_arm_row("configured", "T-0042", state="escalated"),
        ),
        _rows_by_arm(
            bare=_arm_row("bare", "T-0043"),
            gated=_arm_row("gated", "T-0043"),
            configured=_arm_row("configured", "T-0043"),
        ),
    )

    result = CliRunner().invoke(app, ["eval", "--report", "--root", str(root), "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["distribution"] == {
        "bare": ["T-0042"],
        "bare+gated+configured": ["T-0043"],
    }
    assert set(payload["tasks"]) == {"T-0042", "T-0043"}


def test_eval_report_narrows_to_the_named_tasks(tmp_path):
    root = tmp_path / "repo"
    _ledger(
        root,
        _rows_by_arm(bare=_arm_row("bare", "T-0042")),
        _rows_by_arm(bare=_arm_row("bare", "T-0043")),
    )

    result = CliRunner().invoke(
        app,
        ["eval", "--report", "--task", "T-0043", "--root", str(root), "--format", "json"],
    )

    assert result.exit_code == 0
    assert list(json.loads(result.stdout)["tasks"]) == ["T-0043"]


def test_eval_report_with_an_empty_ledger_says_so_and_exits_zero(tmp_path):
    """No arms recorded is not a failure and not a verdict — reporting a
    table nobody measured is how a number gets quoted a year later."""
    root = tmp_path / "repo"
    root.mkdir()

    result = CliRunner().invoke(app, ["eval", "--report", "--root", str(root)])

    assert result.exit_code == 0
    assert "nothing to report" in result.stdout


def test_eval_without_a_task_refuses(tmp_path):
    """The replay path needs at least one task; --report is the way to ask
    for a reading instead."""
    root = tmp_path / "repo"
    _bare_task_repo(root)

    result = CliRunner().invoke(app, ["eval", "flag-dont-flip", "--root", str(root)])

    assert result.exit_code == 3
    assert "give at least one --task" in result.stderr


# ....................... #
# What an arm run may name, and what it already cost (S-0082/D-3): one
# reader over the landings the tree holds and the telemetry ledger.


def _landing(root, task, commit="", at="2026-01-01T00:00:00Z"):
    execution = layout.execution_dir(root)
    execution.mkdir(parents=True, exist_ok=True)
    (execution / f"{task}-1-{at.replace('-', '').replace(':', '')}.yaml").write_text(
        f"task: {task}\nattempt: 1\nat: '{at}'\ncommit: {commit}\n", encoding="utf-8"
    )


def _telemetry(root, *rows):
    stream = root / layout.TORVE_DIR / "telemetry.jsonl"
    stream.parent.mkdir(parents=True, exist_ok=True)
    stream.write_text(
        "".join(json.dumps(row) + "\n" for row in rows) + "not json\n", encoding="utf-8"
    )


def test_eligible_tasks_are_the_landings_that_name_a_commit(tmp_path):
    root = tmp_path / "repo"
    _landing(root, "T-0042", commit="a" * 40)
    # A landing with no commit is a task that finished, not one a replay can
    # start from — there is no parent to truncate a clone at.
    _landing(root, "T-0043")

    assert list(eligible_tasks(root)) == ["T-0042"]
    assert eligible_tasks(root)["T-0042"]["commit"] == "a" * 40
    # Nothing recorded against it yet: eligible, and costless rather than absent.
    assert eligible_tasks(root)["T-0042"] == {
        "commit": "a" * 40,
        "attempts": 0,
        "cost_usd": None,
    }


def test_eligible_tasks_sum_what_the_task_cost_when_it_was_done_for_real(tmp_path):
    root = tmp_path / "repo"
    _landing(root, "T-0042", commit="a" * 40)
    _telemetry(
        root,
        {"task_id": "T-0042", "agent": {"adapter": "claude", "cost_usd": 0.25}},
        {"task_id": "T-0042", "agent": {"adapter": "claude", "cost_usd": 0.5}},
        # A replay of the task is a measurement of it, never the task being
        # done; a fake adapter is simulation, not spend (S-0004/D-6).
        {"task_id": "T-0042", "agent": {"adapter": "claude", "cost_usd": 9.0, "shadow": True}},
        {"task_id": "T-0042", "agent": {"adapter": "fake", "cost_usd": 9.0}},
        # A shadow summary carries no agent block, and another task's rows
        # belong to that task.
        {"task_id": "T-0042", "kind": "shadow", "cost_usd_total": 9.0},
        {"task_id": "T-0043", "agent": {"adapter": "claude", "cost_usd": 9.0}},
    )

    assert eligible_tasks(root)["T-0042"] == {
        "commit": "a" * 40,
        "attempts": 2,
        "cost_usd": 0.75,
    }


def test_eligible_tasks_of_a_tree_with_no_landings_is_empty(tmp_path):
    """The reader takes no configuration and no agent, so the refusal for an
    arm with no task is answered from the same read that prints the
    pre-flight — including when the answer is that nothing is eligible."""
    root = tmp_path / "repo"
    root.mkdir()

    assert eligible_tasks(root) == {}


# ....................... #
# One record per invocation (S-0082/D-5): the three arms replayed by name,
# no verdict beside the rows (S-0082/D-6), and what a run that dies partway
# leaves behind (S-0082/D-7).


def _shadow_record(task, state="ready", attempts=1, cost=0.01):
    return {"task_id": task, "state": state, "attempts": attempts, "cost_usd_total": cost}


def _arms_ran(monkeypatch, outcome):
    """run_arm_shadow replaced by `outcome(task, arm)` — the record's shape
    and the partial-landing behaviour are the eval's, not the replay's."""
    ran = []

    def fake(root, task, config, deps, source, arm, commit=None, annotation=None):
        ran.append((task.id, arm))
        return outcome(task.id, arm)

    monkeypatch.setattr(evals, "run_arm_shadow", fake)

    return ran


def test_arm_eval_writes_one_record_the_three_arm_reader_reads(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / layout.TORVE_DIR).mkdir(parents=True)
    tasks = [SimpleNamespace(id="T-0042"), SimpleNamespace(id="T-0043")]
    ran = _arms_ran(monkeypatch, lambda task, arm: _shadow_record(task))

    record = run_arm_eval(root, tasks, RunnerConfig(), None, None)

    # Every arm over every task, one record naming all three.
    assert ran == [(task.id, arm) for task in tasks for arm in ARMS]
    assert record["kind"] == "arm-eval" and record["complete"] is True
    assert record["tasks"] == ["T-0042", "T-0043"]
    assert set(record["arms"]) == set(ARMS)
    assert record["arms"]["bare"] == [
        {"arm": "bare", "task": "T-0042", "state": "ready", "attempts": 1, "cost_usd": 0.01},
        {"arm": "bare", "task": "T-0043", "state": "ready", "attempts": 1, "cost_usd": 0.01},
    ]
    # No verdict rides the record (S-0082/D-6): three arms are not equally
    # exposed to the same failures, so there is no boolean over them.
    assert not [key for key in record if "match" in key]

    # One line in the ledger, and the reader S-0074 built reads it unchanged.
    assert len((root / layout.TORVE_DIR / EVAL_LEDGER).read_text().splitlines()) == 1
    assert set(three_arm_table(root)) == {"T-0042", "T-0043"}
    assert set(three_arm_table(root)["T-0042"]) == set(ARMS)


def test_arm_eval_runs_only_the_arms_it_is_given(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / layout.TORVE_DIR).mkdir(parents=True)
    ran = _arms_ran(monkeypatch, lambda task, arm: _shadow_record(task))

    record = run_arm_eval(
        root, [SimpleNamespace(id="T-0042")], RunnerConfig(), None, None, arms=("bare", "gated")
    )

    assert ran == [("T-0042", "bare"), ("T-0042", "gated")]
    assert set(record["arms"]) == {"bare", "gated"}


def test_an_unknown_arm_refuses_before_anything_is_replayed(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / layout.TORVE_DIR).mkdir(parents=True)
    ran = _arms_ran(monkeypatch, lambda task, arm: _shadow_record(task))

    with pytest.raises(ValueError, match="unknown arm 'ghost'"):
        run_arm_eval(root, [SimpleNamespace(id="T-0042")], RunnerConfig(), None, None, ("ghost",))

    assert ran == []
    assert not (root / layout.TORVE_DIR / EVAL_LEDGER).exists()


def test_an_arm_that_raises_partway_lands_the_rows_it_has_and_says_so(tmp_path, monkeypatch):
    """S-0082/D-7: an invocation that dies on the third arm never reads like a
    two-arm record that finished — the rows it bought are kept, `complete`
    says they are not all of them, and the failure still reaches the caller."""
    root = tmp_path / "repo"
    (root / layout.TORVE_DIR).mkdir(parents=True)

    def outcome(task, arm):
        if arm == "configured":
            raise RuntimeError("the sandbox died")

        return _shadow_record(task)

    _arms_ran(monkeypatch, outcome)

    with pytest.raises(RuntimeError, match="the sandbox died"):
        run_arm_eval(root, [SimpleNamespace(id="T-0042")], RunnerConfig(), None, None)

    ledger = (root / layout.TORVE_DIR / EVAL_LEDGER).read_text().splitlines()
    record = json.loads(ledger[0])
    assert len(ledger) == 1 and record["complete"] is False
    assert [row["arm"] for rows in record["arms"].values() for row in rows] == ["bare", "gated"]
    assert record["arms"]["configured"] == []
