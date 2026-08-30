from __future__ import annotations

import json

import pytest
from conftest import context_for

from torve.application.telemetry import append_record, build_record, config_hash
from torve.config import layout
from torve.gates.runner import run_gates
from torve.gates.sabotage import BASE_MANIFEST, TASK_ID, base_task, log_document


def manifest_with(gates: list[dict], **extra) -> dict:
    # Test convenience only: real manifests must carry state and origin
    # explicitly (D-2.19); the schema requirement has its own test.
    for gate in gates:
        gate.setdefault("state", "blocking")
        gate.setdefault("origin", "structural")
    data = dict(BASE_MANIFEST, gates=gates)
    data.update(extra)
    return data


def test_cheapest_first_ordering(repo):
    repo.seed(
        manifest_with(
            [
                {"name": "slow", "run": "echo slow", "timeout": 700},
                {"name": "fast", "run": "echo fast", "timeout": 5},
            ]
        )
    )
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")
    report = run_gates(context_for(repo))
    assert [r.name for r in report.results] == ["fast", "slow"]


def test_fail_fast_skips_later_blocking_but_runs_shadow(repo):
    repo.seed(
        manifest_with(
            [
                {"name": "red", "run": "exit 3", "timeout": 5},
                {"name": "later-blocking", "run": "echo never", "timeout": 100},
                {"name": "advisory", "run": "echo still-runs", "timeout": 200, "state": "shadow"},
            ]
        )
    )
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")
    report = run_gates(context_for(repo))
    by_name = {r.name: r for r in report.results}
    assert by_name["red"].outcome == "fail"
    assert by_name["red"].exit_code == 3
    assert by_name["later-blocking"].outcome == "skipped"
    assert by_name["advisory"].outcome == "pass"
    assert report.exit_code == 1


def test_shadow_failure_does_not_gate(repo):
    repo.seed(
        manifest_with(
            [
                {"name": "advisory", "run": "false", "timeout": 5, "state": "shadow"},
                {"name": "real", "run": "true", "timeout": 100},
            ]
        )
    )
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")
    report = run_gates(context_for(repo))
    by_name = {r.name: r for r in report.results}
    assert by_name["advisory"].outcome == "fail"  # measured, reported —
    assert report.exit_code == 0  # — and powerless (§7.3)


def test_quarantined_gate_failure_does_not_gate(repo):
    repo.seed(
        manifest_with(
            [
                {"name": "drifted", "run": "false", "timeout": 5, "state": "quarantined"},
                {"name": "real", "run": "true", "timeout": 100},
            ]
        )
    )
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")
    report = run_gates(context_for(repo))
    by_name = {r.name: r for r in report.results}
    assert by_name["drifted"].outcome == "fail"  # not removed: the retirement
    assert report.exit_code == 0  # decision is made on data


def test_quarantined_acceptance_failure_does_not_block(repo):
    repo.seed(
        manifest_with(
            [
                {
                    "name": "acceptance",
                    "run": "@task.acceptance",
                    "commands": ["exit 7"],
                    "timeout": 30,
                }
            ],
            quarantine=["exit 7"],
        )
    )
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")
    report = run_gates(context_for(repo))
    result = report.results[0]
    assert result.outcome == "pass"
    assert result.quarantined_failures == ["exit 7"]
    assert report.exit_code == 0


def test_bypass_is_appended_to_the_task_log(repo):
    repo.seed(
        manifest_with([{"name": "scope", "run": "@scope"}], scope={"allow": ["src/**"], "deny": []})
    )
    repo.task(base_task(allow=["src/**"]), log_document())
    repo.write("rogue.txt", "outside\n")
    repo.git("add", "-A")
    repo.git(
        "commit", "-q", "--no-gpg-sign", "-m", "widen\n\nTorve-Bypass: scope: allow list is stale"
    )
    report = run_gates(context_for(repo))
    assert report.results[0].outcome == "bypassed"
    import yaml

    document = yaml.safe_load(layout.log_file(repo.root, TASK_ID).read_text())
    record = document["bypasses"][0]
    assert record["gate"] == "scope"
    assert record["reason"] == "allow list is stale"
    assert document["entries"] is not None  # the divergence list survived the append
    assert report.bypass_count_by_gate == {"scope": 1}


def test_telemetry_record_shape(repo, tmp_path):
    repo.seed()
    repo.task(
        base_task(
            allow=["src/**"],
            decisions=[{"id": "D-1", "grade": "LOCKED", "text": "settled", "paths": []}],
        ),
        log_document(),
    )
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")
    ctx = context_for(repo)
    report = run_gates(ctx, only={"scope"})
    record = build_record(ctx, report, config_hash(layout.gates_file(repo.root), repo.root))

    assert record["schema_version"] == 1
    assert record["config_hash"]
    assert record["task_id"] == TASK_ID
    # Denormalised, not referenced: the decision rides inside the record.
    assert record["decisions"][0]["id"] == "D-1"
    assert record["decisions"][0]["text"] == "settled"

    target = tmp_path / "telemetry.jsonl"
    append_record(target, record)
    append_record(target, record)
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["config_hash"] == record["config_hash"]


# ....................... #

# T-0120 (RFC 0027 D-27.7): a live run's dispatch resolves its tier's image
# digest and refuses to proceed when the task names no explicit tier_variant
# and that digest is neither arm the eval ledger's most recent citing verdict
# measured — a regime change nobody measured must not ship silently.


class _StubRuntime:
    def __init__(self, digest: str | None) -> None:
        self.digest = digest

    def resolve_image(self, image: str) -> str | None:
        return self.digest


def _dispatch_deps(runtime: _StubRuntime):
    from torve.application.runner import RunDeps

    return RunDeps(
        workspace=None,  # type: ignore[arg-type]  # unreached: the guard fires at real_hooks() construction
        runtime=runtime,
        agent=object(),  # type: ignore[arg-type]
        vcs=object(),  # type: ignore[arg-type]
        scm=None,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
    )


def _executor_task(**overrides):
    from torve.domain.task import Task

    fields: dict = {"id": TASK_ID, "decisions": []}
    fields.update(overrides)
    return Task(**fields)


def _write_config_eval(root, tier: str, incumbent: str, candidate: str) -> None:
    ledger = root / ".torve" / "evals.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)

    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"kind": "config-eval", "tier": tier, "digests": {"incumbent": incumbent, "candidate": candidate}}
            )
            + "\n"
        )


def test_real_dispatch_refuses_a_digest_no_citing_verdict_ever_measured(tmp_path):
    from torve.application.runner import real_hooks
    from torve.config.runconfig import RunnerConfig

    _write_config_eval(tmp_path, "executor", "sha256:old", "sha256:new")
    deps = _dispatch_deps(_StubRuntime("sha256:drifted"))

    with pytest.raises(ValueError, match="never measured"):
        real_hooks(tmp_path, _executor_task(), RunnerConfig(), deps, tmp_path / "wt")


def test_real_dispatch_allows_the_unchanged_measured_default(tmp_path):
    from torve.application.runner import real_hooks
    from torve.config.runconfig import RunnerConfig

    _write_config_eval(tmp_path, "executor", "sha256:old", "sha256:new")
    deps = _dispatch_deps(_StubRuntime("sha256:old"))

    hooks = real_hooks(tmp_path, _executor_task(), RunnerConfig(), deps, tmp_path / "wt")
    assert hooks.attempt is not None


def test_real_dispatch_allows_the_digest_the_verdict_measured_as_the_candidate(tmp_path):
    """A human reading a green verdict and flipping the committed config to
    the measured candidate (D-27.7) is the sanctioned path to a new regime —
    the guard must not refuse the very digest the ledger already names."""
    from torve.application.runner import real_hooks
    from torve.config.runconfig import RunnerConfig

    _write_config_eval(tmp_path, "executor", "sha256:old", "sha256:new")
    deps = _dispatch_deps(_StubRuntime("sha256:new"))

    hooks = real_hooks(tmp_path, _executor_task(), RunnerConfig(), deps, tmp_path / "wt")
    assert hooks.attempt is not None


def test_real_dispatch_allows_anything_before_any_verdict_cites_the_tier(tmp_path):
    """No citing verdict means nothing has been measured yet, so nothing can
    have been displaced — the bootstrap case for a tier RFC 0027 has not
    reached (an empty ledger)."""
    from torve.application.runner import real_hooks
    from torve.config.runconfig import RunnerConfig

    deps = _dispatch_deps(_StubRuntime("sha256:whatever"))

    hooks = real_hooks(tmp_path, _executor_task(), RunnerConfig(), deps, tmp_path / "wt")
    assert hooks.attempt is not None


def test_an_explicit_tier_variant_dispatches_freely_even_against_an_unmeasured_digest(tmp_path):
    """D-27.3: naming a variant is naming and running a candidate on
    purpose — the displacement refusal is scoped to the base seat's silent
    resolution alone."""
    from torve.application.runner import real_hooks
    from torve.config.runconfig import RunnerConfig, TierConfig

    _write_config_eval(tmp_path, "executor", "sha256:old", "sha256:new")
    deps = _dispatch_deps(_StubRuntime("sha256:nobody-measured-this-one"))
    config = RunnerConfig(
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": TierConfig(),
            "executor.candidate": TierConfig(),
        }
    )

    hooks = real_hooks(
        tmp_path, _executor_task(tier_variant="candidate"), config, deps, tmp_path / "wt"
    )
    assert hooks.attempt is not None


def test_shadow_dispatch_never_trips_the_guard(tmp_path):
    """The eval loop's own shadow replays (run_config_eval) resolve an
    unmeasured candidate digest through this same real_hooks() call — the
    guard must not refuse the very measurement it exists to require."""
    from torve.application.runner import real_hooks
    from torve.config.runconfig import RunnerConfig

    _write_config_eval(tmp_path, "executor", "sha256:old", "sha256:new")
    deps = _dispatch_deps(_StubRuntime("sha256:an-untested-candidate"))

    hooks = real_hooks(
        tmp_path, _executor_task(), RunnerConfig(), deps, tmp_path / "wt", shadow=True
    )
    assert hooks.attempt is not None


def test_a_failed_attempt_still_appends_its_cost(tmp_path):
    """RFC 0004 §6: a budget-killed or nonzero-exit attempt never reaches the
    gates leg, and its record used to vanish with it — four ~$4 first
    attempts were missing from cost-and-iterations when this was found. The
    attempt hook appends a gates_run:false record with the spend."""
    import asyncio
    import subprocess

    from torve.application.ports import AgentResult, SandboxHandle
    from torve.application.runner import RunDeps, drive_attempts, real_hooks
    from torve.application.runstate import RunState
    from torve.config.runconfig import RunnerConfig, TierConfig
    from torve.domain.states import TaskState
    from torve.domain.task import Task

    class DyingAgent:
        kind = "harness"

        def run(self, ctx):
            return AgentResult(
                exit_code=1, output="", cost_usd=4.05, model_version="m-x", trace_ref=None
            )

    class InertRuntime:
        def create(self, spec, workspace):
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
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": TierConfig(
                adapter="harness", command="run", provider="p", model="m", api_key_env=[]
            ),
        },
    )
    task = Task(id="T-9020", decisions=[])
    deps = RunDeps(
        workspace=None,  # type: ignore[arg-type]
        runtime=InertRuntime(),
        agent=DyingAgent(),
        vcs=None,  # type: ignore[arg-type]
        scm=None,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
    )
    state = RunState(task_id=task.id, path=tmp_path / "T-9020.state.json")
    state.transition(TaskState.CLAIMED, "test claim")
    hooks = real_hooks(tmp_path, task, config, deps, worktree)
    asyncio.run(drive_attempts(state, task, config, hooks))

    telemetry = tmp_path / ".torve" / "telemetry.jsonl"
    records = [json.loads(line) for line in telemetry.read_text().splitlines()]
    failed = [r for r in records if r.get("gates_run") is False]
    assert failed and failed[0]["agent"]["cost_usd"] == 4.05
    assert failed[0]["task_id"] == "T-9020"
    assert failed[0]["exit_code"] == 1
