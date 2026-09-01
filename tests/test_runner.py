from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from conftest import context_for

from torve.application.telemetry import append_record, build_record, config_hash
from torve.config import layout
from torve.config.runconfig import RunnerConfig, TierConfig
from torve.domain.attempt import GateResult
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


def test_a_character_routed_task_dispatches_freely_like_an_explicit_variant(tmp_path):
    """RFC 0034 D-34.3: character routing resolves to a tier_variant before
    real_hooks ever sees the task — the same free-dispatch path an explicit
    tier_variant already takes (D-27.3), never the silently-resolved base
    seat's displacement refusal."""
    from torve.application.runner import real_hooks
    from torve.config.runconfig import RunnerConfig, TierConfig, resolve_character_tier

    _write_config_eval(tmp_path, "executor", "sha256:old", "sha256:new")
    deps = _dispatch_deps(_StubRuntime("sha256:nobody-measured-this-one"))
    config = RunnerConfig(
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": TierConfig(character_routing={"structural": "candidate"}),
            "executor.candidate": TierConfig(),
        }
    )

    task = resolve_character_tier(config, _executor_task(character="structural"))
    hooks = real_hooks(tmp_path, task, config, deps, tmp_path / "wt")
    assert hooks.attempt is not None


def test_run_routing_includes_the_character_routed_variants_provider(tmp_path):
    """D-21.4 parity with include_retry: once resolve_character_tier has run
    upstream, the run's routing derivation sees the character-routed
    variant's provider, not the seat default's."""
    from torve.application.runner import run_routing
    from torve.config.runconfig import (
        BrokerConfig,
        BrokerProvider,
        RunnerConfig,
        TierConfig,
        resolve_character_tier,
    )

    config = RunnerConfig(
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": TierConfig(adapter="fake", character_routing={"structural": "indexed"}),
            "executor.indexed": TierConfig(
                adapter="harness", command="run", provider="p", model="m"
            ),
        },
        broker=BrokerConfig(
            adapter="local",
            providers={"p": BrokerProvider(upstream="https://p.example", key_env="P_KEY")},
        ),
    )

    task = resolve_character_tier(config, _executor_task(character="structural"))
    routing = run_routing(config, task, review_on=False)
    assert [route.provider for route in routing.routes] == ["p"]


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


# ....................... #
# The tier clock (RFC 0035 §5.3, D-35.6): the attempt hook reads the
# resolved tier's values, so the heavy rung carries its own clocks.


def test_the_attempt_hook_reads_the_resolved_tiers_clocks(tmp_path):
    """The resolved value reaches the agent context and the sandbox spec:
    a task varianting to a tier that names its clocks runs under them, and
    the same configuration's untiered seat still runs under the runtime
    globals — the fall-through is per-tier, not a global mutation."""
    import asyncio
    import subprocess

    from torve.application.ports import AgentResult, SandboxHandle
    from torve.application.runner import RunDeps, drive_attempts, real_hooks
    from torve.application.runstate import RunState
    from torve.config.runconfig import RunnerConfig, RuntimeConfig, TierConfig
    from torve.domain.states import TaskState
    from torve.domain.task import Task

    class ClockRecordingAgent:
        kind = "harness"

        def __init__(self):
            self.contexts = []

        def run(self, ctx):
            self.contexts.append(ctx)
            return AgentResult(exit_code=1, output="")

    class SpecRecordingRuntime:
        def __init__(self):
            self.specs = []

        def create(self, spec, workspace):
            self.specs.append(spec)
            return SandboxHandle(id="h-1", name=spec.name)

        def resolve_image(self, image):
            return None

        def sync_out(self, handle, worktree):
            pass

        def destroy(self, handle):
            pass

    def drive(config: RunnerConfig, tier_variant: str | None, task_id: str) -> tuple[float, float]:
        worktree = tmp_path / f"wt-{task_id}"
        (worktree / ".torve" / "skills").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=worktree, check=True)

        agent = ClockRecordingAgent()
        runtime = SpecRecordingRuntime()
        deps = RunDeps(
            workspace=None,  # type: ignore[arg-type]
            runtime=runtime,
            agent=agent,
            vcs=None,  # type: ignore[arg-type]
            scm=None,  # type: ignore[arg-type]
            store=None,  # type: ignore[arg-type]
        )
        task = Task(id=task_id, decisions=[], tier_variant=tier_variant)
        state = RunState(task_id=task.id, path=tmp_path / f"{task.id}.state.json")
        state.transition(TaskState.CLAIMED, "test claim")
        hooks = real_hooks(tmp_path, task, config, deps, worktree)
        asyncio.run(drive_attempts(state, task, config, hooks))

        assert len(agent.contexts) == 1 and len(runtime.specs) == 1

        return agent.contexts[0].timeout_s, runtime.specs[0].timeout_s

    config = RunnerConfig(
        poison_ceiling=1,
        runtime=RuntimeConfig(agent_timeout=1200, sandbox_timeout=1800),
        tiers={
            "executor": TierConfig(adapter="harness", command="run", provider="p", model="m"),
            "executor.heavy": TierConfig(
                adapter="harness",
                command="run",
                provider="p",
                model="m",
                agent_timeout=3600,
                sandbox_timeout=4200,
            ),
        },
    )

    agent_clock, sandbox_clock = drive(config, "heavy", "T-9024")
    assert agent_clock == 3600
    assert sandbox_clock == 4200

    agent_clock, sandbox_clock = drive(config, None, "T-9025")
    assert agent_clock == 1200
    assert sandbox_clock == 1800


class _NoSandboxRuntime:
    """The gate pass under test never opens a sandbox: the empty-diff
    refusal fires before the battery, and the pass-through manifests carry
    no shell gates. Any sandbox call is a test defect."""

    def create(self, spec, workspace):
        raise AssertionError("no sandbox should be opened")

    def exec(self, handle, command, timeout_s):
        raise AssertionError("no command should execute")

    def sync_out(self, handle, workspace):
        raise AssertionError("no sandbox to sync")

    def destroy(self, handle):
        raise AssertionError("no sandbox to destroy")

    def resolve_image(self, image):
        return "sha256:mock"


def _cut_worktree(repo, manifest, task_doc):
    """Seed a repo whose `main` carries the gate manifest and the task
    contract (tracked, so the worktree cut there is clean), and return a
    fresh worktree at its tip."""
    from torve.adapters.workspace.git import GitWorkspace

    repo.git("checkout", "-q", "main")
    repo.write(".torve/gates.yaml", yaml.safe_dump(manifest, sort_keys=False))
    repo.task(task_doc, None)
    repo.commit("task minted")

    return GitWorkspace(repo.root).create(TASK_ID, "main")


def test_the_empty_implement_diff_predicate(tmp_path):
    # T-0172 + T-0177: only a standalone implement task with a resolvable
    # base and nothing changed reads as a no-op. The integration task's
    # empty diff stays legal — but only the adoption-made one: a task is
    # the integration task exactly when some contract under the engine's
    # .torve/tasks names it as parent (T-0177). `depends_on` alone exempted
    # every phase-sequenced implement task — the wrong discriminator;
    # revert/review roles and base-less repos are untouched.
    from torve.application.runner import _is_empty_implement_diff
    from torve.config.manifest import Manifest
    from torve.domain.task import Task
    from torve.gates.context import DiffEntry, GateContext

    def ctx(*, task, merge_base="main-tip", diff=()):
        return GateContext(
            root=tmp_path,
            manifest=Manifest(),
            head_sha="head",
            base="main",
            merge_base=merge_base,
            diff=list(diff),
            task=task,
        )

    implement = Task(id="T-9001", role="implement", decisions=[])
    sequenced = Task(id="T-9002", role="implement", decisions=[], depends_on=["T-9001"])
    revert = Task(id="T-9003", role="revert", decisions=[], targets=["T-9001"])

    assert _is_empty_implement_diff(ctx(task=implement), tmp_path)
    # An untracked-only candidate is a change, not a no-op.
    assert not _is_empty_implement_diff(
        ctx(task=implement, diff=[DiffEntry(status="A", path="new.txt")]), tmp_path
    )
    # The engine's own contract copy — a worktree cut at base before the
    # mint carries none, so the gate pass's copy is the only untracked
    # file a no-op leaves — is bookkeeping, not candidate work.
    assert _is_empty_implement_diff(
        ctx(
            task=implement,
            diff=[DiffEntry(status="A", path=".torve/tasks/T-9001/contract.yaml")],
        ),
        tmp_path,
    )
    assert _is_empty_implement_diff(
        ctx(
            task=implement,
            diff=[
                DiffEntry(status="A", path=".torve/tasks/T-9001/contract.yaml"),
                DiffEntry(status="A", path=".torve/tasks/T-9001/log.yaml"),
            ],
        ),
        tmp_path,
    )
    # A real file beside the bookkeeping is a candidate, not a no-op; so is
    # another task's contract, which the scope gate would not skip.
    assert not _is_empty_implement_diff(
        ctx(
            task=implement,
            diff=[
                DiffEntry(status="A", path=".torve/tasks/T-9001/contract.yaml"),
                DiffEntry(status="A", path="src/app.py"),
            ],
        ),
        tmp_path,
    )
    assert not _is_empty_implement_diff(
        ctx(task=implement, diff=[DiffEntry(status="A", path=".torve/tasks/T-9999/contract.yaml")]),
        tmp_path,
    )
    # No base resolved: nothing to diff against, nothing to refuse.
    assert not _is_empty_implement_diff(ctx(task=implement, merge_base=None), tmp_path)
    # A phase-sequenced task's `depends_on` does not exempt it (T-0177):
    # nothing names it as parent, so it is an ordinary implement task and
    # its empty diff is a no-op like any other.
    assert _is_empty_implement_diff(ctx(task=sequenced), tmp_path)
    assert not _is_empty_implement_diff(ctx(task=revert), tmp_path)
    assert not _is_empty_implement_diff(ctx(task=None), tmp_path)

    # The adoption record decides: a contract naming the task as parent
    # (D-26.5) makes it the integration task, whose empty diff stays legal.
    adopted = tmp_path / "with-children"
    (adopted / ".torve" / "tasks" / "T-9000").mkdir(parents=True)
    (adopted / ".torve" / "tasks" / "T-9000" / "contract.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": "T-9000",
                "role": "implement",
                "parent": "T-9002",
                "depends_on": [],
                "scope": {"allow": [], "deny": []},
                "acceptance": [],
                "decisions": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    assert not _is_empty_implement_diff(ctx(task=sequenced), adopted)


def test_an_empty_implement_diff_is_refused_before_the_battery(repo):
    """T-0172: end to end through the shipped gate pass — an implement
    attempt that changed nothing comes back red with a fact naming the
    empty diff, and the attempt's spend survives as a red record (RFC 0004
    §6). The battery is never blessed over a tree the agent never touched."""
    from torve.application.runner import _run_gates_in_worktree

    repo.seed()
    worktree = _cut_worktree(repo, BASE_MANIFEST, base_task(allow=["src/**"]))

    exit_code, summary, _digest, results, patch = _run_gates_in_worktree(
        worktree,
        TASK_ID,
        RunnerConfig(),
        _NoSandboxRuntime(),
        "run-1",
        repo.root,
        agent_meta={"adapter": "fake", "model": None},
        base="main",
    )

    assert exit_code == 1
    assert summary == "empty diff against base — no changes produced"
    assert results == []
    assert patch == ""

    telemetry = repo.root / ".torve" / "telemetry.jsonl"
    records = [json.loads(line) for line in telemetry.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["task_id"] == TASK_ID
    assert records[0]["exit_code"] == 1
    assert records[0]["results"] == []


def test_an_empty_implement_diff_is_refused_when_the_contract_was_minted_after_base(repo):
    """T-0172, the incident's own shape: the contract was minted after the
    base, so the worktree cut at base carries none and the gate pass's copy
    of it is the only file a no-op attempt leaves behind. That copy must
    not read as candidate work — the refusal fires, before the battery."""
    from torve.adapters.workspace.git import GitWorkspace
    from torve.application.runner import _run_gates_in_worktree

    repo.seed()
    repo.git("checkout", "-q", "main")
    # Minted but never committed: untracked at the root, absent from the
    # worktree the dispatch cuts — exactly how a fresh mint reads to the
    # runner. The gate pass's own copy step brings it into the worktree.
    repo.task(base_task(allow=["src/**"]), None)
    worktree = GitWorkspace(repo.root).create(TASK_ID, "main")

    exit_code, summary, _digest, results, _patch = _run_gates_in_worktree(
        worktree,
        TASK_ID,
        RunnerConfig(),
        _NoSandboxRuntime(),
        "run-1",
        repo.root,
        agent_meta={"adapter": "fake", "model": None},
        base="main",
    )

    assert exit_code == 1
    assert summary == "empty diff against base — no changes produced"
    assert results == []

    telemetry = repo.root / ".torve" / "telemetry.jsonl"
    records = [json.loads(line) for line in telemetry.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["task_id"] == TASK_ID
    assert records[0]["exit_code"] == 1
    assert records[0]["results"] == []


def test_an_integration_tasks_empty_diff_stays_legal(repo):
    """D-26.6 + T-0177: at adoption the parent becomes the integration
    task — its `depends_on` grows with every child and its landing is the
    decomposition's completion, so its legitimately-empty diff is not a
    refused no-op. What makes it the integration task is the engine's
    record of the adoption — a child contract naming it as parent — not
    the grown `depends_on`, which an ordinary phase-sequenced task carries
    too."""
    from torve.application.runner import _run_gates_in_worktree

    repo.seed()
    task_doc = base_task(allow=["src/**"])
    task_doc["depends_on"] = ["T-9000"]
    # The adopted children carry the parent (D-26.5) — the engine's record
    # the discriminator reads. It lives in the engine root's task
    # directory; the worktree cut at base carries the committed copy.
    repo.write(
        ".torve/tasks/T-9000/contract.yaml",
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": "T-9000",
                "role": "implement",
                "parent": TASK_ID,
                "depends_on": [],
                "scope": {"allow": ["src/**"], "deny": []},
                "acceptance": [],
                "decisions": [],
            },
            sort_keys=False,
        ),
    )
    worktree = _cut_worktree(repo, {"schema_version": 1, "gates": []}, task_doc)

    exit_code, summary, _digest, results, _patch = _run_gates_in_worktree(
        worktree,
        TASK_ID,
        RunnerConfig(),
        _NoSandboxRuntime(),
        "run-1",
        repo.root,
        agent_meta={"adapter": "fake", "model": None},
        base="main",
    )

    assert exit_code == 0
    assert summary == ""
    assert results == []


def test_a_nonempty_untracked_diff_never_triggers_the_refusal(repo):
    """An untracked-only candidate is a change, not a no-op — the pass must
    proceed and record a green verdict."""
    from torve.application.runner import _run_gates_in_worktree

    repo.seed()
    worktree = _cut_worktree(repo, {"schema_version": 1, "gates": []}, base_task(allow=["src/**"]))
    (worktree / "notes.txt").write_text("agent's own notes\n", encoding="utf-8")

    exit_code, _summary, _digest, results, _patch = _run_gates_in_worktree(
        worktree,
        TASK_ID,
        RunnerConfig(),
        _NoSandboxRuntime(),
        "run-1",
        repo.root,
        agent_meta={"adapter": "fake", "model": None},
        base="main",
    )

    assert exit_code == 0
    assert results == []

    telemetry = repo.root / ".torve" / "telemetry.jsonl"
    records = [json.loads(line) for line in telemetry.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["exit_code"] == 0



# ....................... #
# Conviction-routed retries (T-0216, D-34.5/D-34.6/D-34.7): the red attempt's
# recorded gate outcomes resolve the rung — the axis-keyed mapping read at
# the most severe axis present, the scalar read as its functional sugar. The
# selection sees outcomes, states and declared labels only, never a trace or
# model text, so every case here doubles as a replay.


def conviction(name: str, outcome: str = "fail", state: str = "blocking") -> GateResult:
    return GateResult(name=name, outcome=outcome, state=state)  # type: ignore[arg-type]


AXES: dict = {
    "acceptance": "functional",
    "fence": "boundary",
    "grammar": "compliance",
    "tidy": "form",
}

RUNGED = TierConfig(
    retry_variants={
        "functional": "executor.heavy",
        "compliance": "executor.ink",
        "form": "executor.tidy",
    }
)


def test_the_most_severe_conviction_present_routes_the_retry():
    from torve.application.runner import retry_rung_for

    both = [conviction("grammar"), conviction("acceptance")]
    assert retry_rung_for(RUNGED, both, AXES) == "executor.heavy"
    assert (
        retry_rung_for(RUNGED, [conviction("tidy"), conviction("grammar")], AXES)
        == "executor.ink"
    )
    assert retry_rung_for(RUNGED, [conviction("tidy")], AXES) == "executor.tidy"


def test_a_boundary_conviction_resolves_no_rung_and_masks_the_lighter_axes():
    from torve.application.runner import retry_rung_for

    assert retry_rung_for(RUNGED, [conviction("fence")], AXES) == ""

    # The fence defect outranks compliance and form beside it: the operator
    # repairs the contract with a disclosed chore, the retry earns no rung.
    fence_with_light_convictions = [
        conviction("fence"),
        conviction("grammar"),
        conviction("tidy"),
    ]
    assert retry_rung_for(RUNGED, fence_with_light_convictions, AXES) == ""

    # Functional is heavier than boundary — it still wins over the fence.
    assert (
        retry_rung_for(RUNGED, [conviction("fence"), conviction("acceptance")], AXES)
        == "executor.heavy"
    )


def test_an_unlabeled_gate_and_an_empty_record_read_as_functional():
    from torve.application.runner import retry_rung_for

    # A failing gate the manifest does not name (or does not label).
    assert retry_rung_for(RUNGED, [conviction("mystery")], AXES) == "executor.heavy"
    assert retry_rung_for(RUNGED, [conviction("grammar")], {}) == "executor.heavy"

    # The empty-diff refusal: red with no per-gate outcome recorded at all.
    assert retry_rung_for(RUNGED, [], AXES) == "executor.heavy"


def test_only_a_blocking_failure_convicts():
    from torve.application.runner import retry_rung_for

    # A shadow or bypassed result beside a red attempt is not a conviction —
    # it did not drive the exit code, so it does not drive the routing.
    assert retry_rung_for(RUNGED, [conviction("grammar", state="shadow")], AXES) == "executor.heavy"
    assert retry_rung_for(RUNGED, [conviction("tidy", outcome="bypassed")], AXES) == "executor.heavy"

    # A blocking gate that errored reddens the attempt exactly as a failing
    # one does (the runner's own redness rule), so it convicts likewise.
    assert retry_rung_for(RUNGED, [conviction("grammar", outcome="error")], AXES) == "executor.ink"


def test_the_scalar_form_is_the_functional_rung_of_the_resolved_mapping():
    from torve.application.runner import retry_rung_for

    scalar = TierConfig(retry_variant="executor.heavy")
    assert retry_rung_for(scalar, [conviction("acceptance")], AXES) == "executor.heavy"
    assert retry_rung_for(scalar, [conviction("grammar")], AXES) == ""
    assert retry_rung_for(TierConfig(), [conviction("acceptance")], AXES) == ""


def test_run_routing_carries_the_provider_of_every_axis_rung():
    from torve.application.runner import run_routing
    from torve.config.runconfig import BrokerConfig, BrokerProvider
    from torve.domain.task import Task

    def rung(provider: str) -> TierConfig:
        return TierConfig(adapter="api", command="c", provider=provider, model="m")

    routed = {
        name: BrokerProvider(upstream="http://up", key_env="K")
        for name in ("base", "heavy", "ink", "tidy")
    }
    config = RunnerConfig(
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": TierConfig(
                adapter="api",
                command="c",
                provider="base",
                model="m",
                retry_variants={
                    "functional": "executor.heavy",
                    "compliance": "executor.ink",
                    "form": "executor.tidy",
                },
            ),
            "executor.heavy": rung("heavy"),
            "executor.ink": rung("ink"),
            "executor.tidy": rung("tidy"),
        },
        broker=BrokerConfig(adapter="local", providers=routed),
    )
    task = Task(id="T-9100", decisions=[])

    routing = run_routing(config, task, review_on=False, include_retry=True)
    assert {route.provider for route in routing.routes} == {"base", "heavy", "ink", "tidy"}

    without = run_routing(config, task, review_on=False)
    assert {route.provider for route in without.routes} == {"base"}


def test_a_rung_naming_the_seat_itself_is_never_routed_twice():
    from torve.application.runner import run_routing
    from torve.config.runconfig import BrokerConfig, BrokerProvider
    from torve.domain.task import Task

    config = RunnerConfig(
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": TierConfig(
                adapter="api",
                command="c",
                provider="base",
                model="m",
                retry_variants={"functional": "executor", "compliance": "executor"},
            ),
        },
        broker=BrokerConfig(
            adapter="local",
            providers={"base": BrokerProvider(upstream="http://up", key_env="K")},
        ),
    )

    routing = run_routing(config, Task(id="T-9101", decisions=[]), review_on=False, include_retry=True)
    assert [route.provider for route in routing.routes] == ["base"]


def test_an_axis_rung_the_broker_cannot_route_is_a_configuration_error():
    from torve.application.runner import run_routing
    from torve.config.runconfig import BrokerConfig, BrokerProvider
    from torve.domain.task import Task

    config = RunnerConfig(
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": TierConfig(
                adapter="api",
                command="c",
                provider="base",
                model="m",
                retry_variants={"compliance": "executor.ink"},
            ),
            "executor.ink": TierConfig(adapter="api", command="c", provider="ink", model="m"),
        },
        broker=BrokerConfig(
            adapter="local",
            providers={"base": BrokerProvider(upstream="http://up", key_env="K")},
        ),
    )

    with pytest.raises(ValueError, match=r"executor\.ink"):
        run_routing(config, Task(id="T-9102", decisions=[]), review_on=False, include_retry=True)


def test_a_credentialed_compliance_rung_is_refused_under_a_broker():
    """The runner-side re-check walks every resolved rung, not only the
    scalar's — the programmatically-built configuration the re-check exists
    for slips the credential past the validator on a non-functional axis."""
    from torve.application.runner import RunDeps, real_hooks
    from torve.config.runconfig import BrokerConfig
    from torve.domain.task import Task

    calm = TierConfig(
        adapter="api", command="c", provider="p", model="m", api_key_env=["CALM_KEY"]
    )
    config = RunnerConfig(
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": TierConfig(retry_variants={"compliance": "executor.calm"}),
            "executor.calm": calm,
        }
    )
    brokered = config.model_copy(update={"broker": BrokerConfig(adapter="local")})
    deps = RunDeps(
        workspace=None,  # type: ignore[arg-type]
        runtime=_StubRuntime(None),
        agent=object(),  # type: ignore[arg-type]
        vcs=object(),  # type: ignore[arg-type]
        scm=None,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
        retry_agent=lambda tier: object(),
    )

    with pytest.raises(ValueError, match=r"executor\.calm"):
        real_hooks(Path("/unused"), Task(id="T-9103", decisions=[]), brokered, deps, Path("/unused/wt"))


# ....................... #
# The routed retry end to end: a scripted gate pass returns recorded
# `GateResult`s against a labeled manifest — the same records the real pass
# appends to telemetry — and the loop hands the next attempt to the rung
# the convictions resolve.


RETRY_MANIFEST = {
    "schema_version": 1,
    "gates": [
        {"name": "acceptance", "run": "true", "state": "blocking", "origin": "structural", "axis": "functional"},
        {"name": "fence", "run": "true", "state": "blocking", "origin": "structural", "axis": "boundary"},
        {"name": "grammar", "run": "true", "state": "blocking", "origin": "structural", "axis": "compliance"},
        {"name": "tidy", "run": "true", "state": "blocking", "origin": "structural", "axis": "form"},
    ],
}


def _conviction_passes(monkeypatch, repo, passes, seen_metas, append_telemetry=False):
    """Replace the gate pass with one driven by per-attempt entries: a
    non-empty conviction list goes red with those recorded results, `[]` is
    green, and `None` is the empty-diff shape — red with no per-gate record
    at all. Each pass seeds the labeled manifest into the mock worktree so
    selection reads the same declarations a real pass judged by. When
    `append_telemetry`, each pass also writes the row the real pass would:
    the outcome and state of every gate beside the tier stamp of the
    attempt that produced them."""
    import torve.application.runner as run_module

    outcomes = list(passes)

    def scripted(worktree, task_id, _config, _runtime, _run_id, root, agent_meta=None, *_args):
        manifest_file = worktree / ".torve" / "gates.yaml"
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_text(yaml.safe_dump(RETRY_MANIFEST), encoding="utf-8")

        entry = outcomes.pop(0)
        results = [] if entry is None else entry
        code = 0 if entry == [] else 1
        seen_metas.append(dict(agent_meta or {}))

        if append_telemetry:
            append_record(
                root / ".torve" / "telemetry.jsonl",
                {
                    "schema_version": 1,
                    "task_id": task_id,
                    "agent": dict(agent_meta or {}),
                    "results": [r.model_dump() for r in results],
                    "exit_code": code,
                },
            )

        summary = ", ".join(f"{r.name}={r.outcome}" for r in results)
        return code, summary or "all green", "cafecafe1234", results, ""

    monkeypatch.setattr(run_module, "_run_gates_in_worktree", scripted)


def _retry_config(**rungs) -> RunnerConfig:
    def rung(model: str) -> TierConfig:
        return TierConfig(adapter="api", command="c", provider="p", model=model)

    return RunnerConfig(
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": TierConfig(retry_variants=dict(rungs)),
            "executor.heavy": rung("heavy-model"),
            "executor.ink": rung("ink-model"),
            "executor.tidy": rung("tidy-model"),
        }
    )


def _loop_deps(repo, retry_agent):
    from test_run_loop import OK, MockRuntime, MockScm, MockVcs, MockWorkspace, ScriptedAgent

    from torve.adapters.store.durable import open_store
    from torve.application.runner import RunDeps

    return RunDeps(
        workspace=MockWorkspace(repo.root),
        runtime=MockRuntime(),
        agent=ScriptedAgent([OK]),
        vcs=MockVcs(),
        scm=MockScm(),
        store=open_store,
        retry_agent=retry_agent,
    )


def test_a_compliance_conviction_routes_the_retry_to_the_compliance_rung(repo, monkeypatch):
    """A red carrying a compliance and a form conviction escalates on the
    compliance rung only: the lighter axis loses, the unmapped-to-here
    heavier rung is never built (one rung, not a chain)."""
    from test_run_loop import OK, ScriptedAgent, task_for

    from torve.application.runner import run_task
    from torve.domain.states import TaskState

    repo.seed()
    seen: list[dict] = []
    _conviction_passes(
        monkeypatch,
        repo,
        [[conviction("grammar"), conviction("tidy")], []],
        seen,
    )

    built: list[str] = []

    def retry_agent(tier):
        built.append(tier.model)
        return ScriptedAgent([OK])

    config = _retry_config(functional="executor.heavy", compliance="executor.ink", form="executor.tidy")
    state = run_task(repo.root, task_for(repo), config, _loop_deps(repo, retry_agent))

    assert state.state is TaskState.READY
    assert state.attempts == 2
    assert built == ["ink-model"]
    assert [meta["tier"] for meta in seen] == ["executor", "executor.ink"]


def test_a_boundary_conviction_retries_under_the_same_tier_never_a_heavier_one(repo, monkeypatch):
    """A fence defect resolves no rung whatever the mapping says elsewhere:
    the retry is the gate's own repair text on the same tier, and the
    heavier rungs join nothing."""
    from test_run_loop import task_for

    from torve.application.runner import run_task
    from torve.domain.states import TaskState

    repo.seed()
    seen: list[dict] = []
    _conviction_passes(monkeypatch, repo, [[conviction("fence"), conviction("tidy")], []], seen)

    def retry_agent(tier):
        raise AssertionError("a boundary conviction must not reach any rung's factory")

    config = _retry_config(functional="executor.heavy", compliance="executor.ink", form="executor.tidy")
    state = run_task(repo.root, task_for(repo), config, _loop_deps(repo, retry_agent))

    assert state.state is TaskState.READY
    assert state.attempts == 2
    assert [meta["tier"] for meta in seen] == ["executor", "executor"]


def test_the_scalar_mirror_still_routes_every_unclassified_red(repo, monkeypatch):
    """D-27.11 compatibility: a configured scalar is the functional rung of
    the resolved mapping, and it still fires after reds whose record names
    no gate (the scripted recordless red — the empty-diff shape)."""
    from test_run_loop import OK, ScriptedAgent, task_for

    from torve.application.runner import run_task

    repo.seed()
    seen: list[dict] = []
    _conviction_passes(monkeypatch, repo, [None, []], seen)

    built: list[str] = []

    def retry_agent(tier):
        built.append(tier.model)
        return ScriptedAgent([OK])

    config = RunnerConfig(
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": TierConfig(retry_variant="executor.heavy"),
            "executor.heavy": TierConfig(adapter="api", command="c", provider="p", model="heavy-model"),
        }
    )
    state = run_task(repo.root, task_for(repo), config, _loop_deps(repo, retry_agent))

    assert state.attempts == 2
    assert built == ["heavy-model"]
    assert [meta["tier"] for meta in seen] == ["executor", "executor.heavy"]


def test_the_chosen_rung_is_derivable_from_telemetry_records_alone(repo, monkeypatch):
    """D-34.5's claim made testable: replay the rung choice from the rows —
    names, outcomes and states are the record's own fields, the axis labels
    and the rung map are the configuration they were recorded beside — and
    no attempt-level trace enters the derivation."""
    from test_run_loop import task_for

    from torve.application.runner import run_task
    from torve.domain.states import TaskState

    repo.seed()
    _conviction_passes(
        monkeypatch,
        repo,
        [[conviction("grammar"), conviction("tidy")], []],
        [],
        append_telemetry=True,
    )

    from test_run_loop import OK, ScriptedAgent

    retry_agent = lambda tier: ScriptedAgent([OK])  # noqa: E731
    config = _retry_config(functional="executor.heavy", compliance="executor.ink", form="executor.tidy")
    state = run_task(repo.root, task_for(repo), config, _loop_deps(repo, retry_agent))

    assert state.state is TaskState.READY
    rows = [
        json.loads(line)
        for line in (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]
    assert [row["agent"]["tier"] for row in rows] == ["executor", "executor.ink"]

    # The replay, independent of the runner: severity ladder and axis map as
    # declared, rung map as configured, outcomes as recorded.
    axis_of = {gate["name"]: gate["axis"] for gate in RETRY_MANIFEST["gates"]}
    convicted = {
        axis_of[result["name"]]
        for result in rows[0]["results"]
        if result["outcome"] == "fail" and result["state"] == "blocking"
    }
    most_severe = next(
        axis for axis in ("functional", "boundary", "compliance", "form") if axis in convicted
    )
    assert most_severe == "compliance"
    assert config.tiers["executor"].resolved_retry_variants()[most_severe] == rows[1]["agent"]["tier"]


# ....................... #
# The derived-cache volume (RFC 0035 §5.2, D-35.4): slot-suffixed naming
# like the auth volume, a fixed mount outside the workspace, and the gates
# lane of a live run carrying the same warmth the attempt got. D-35.3's
# replay exclusion is pinned beside the replay, in test_shadow.py.


def test_an_unnamed_cache_mounts_nothing_a_named_one_is_slot_suffixed():
    from torve.application.ports import SandboxSpec
    from torve.application.runner import _sandbox_cache
    from torve.config.runconfig import CACHE_MOUNT

    assert _sandbox_cache(TierConfig(), worker_slot=0) == {}  # cold as today

    mounts = _sandbox_cache(TierConfig(cache_volume="torve-cache"), worker_slot=2)
    assert mounts == {"torve-cache-2": CACHE_MOUNT}

    # Slot-scoped like auth volumes: two concurrent workers share nothing.
    assert _sandbox_cache(TierConfig(cache_volume="torve-cache"), 3) != mounts

    # The mount is fixed and outside the workspace bind — no attempt can
    # read the cache as project content.
    workdir = SandboxSpec(name="n", image="i", labels={}, timeout_s=1.0).workdir
    assert workdir != CACHE_MOUNT
    assert not CACHE_MOUNT.startswith(f"{workdir}/")


def _warm_config(worker_slot: int) -> RunnerConfig:
    cold = RunnerConfig()

    return RunnerConfig(
        worker_slot=worker_slot,
        tiers={**cold.tiers, "executor": TierConfig(cache_volume="torve-cache")},
    )


def _cache_deps(repo, runtime):
    from test_run_loop import OK, MockScm, MockVcs, MockWorkspace, ScriptedAgent

    from torve.adapters.store.durable import open_store
    from torve.application.runner import RunDeps

    return RunDeps(
        workspace=MockWorkspace(repo.root),
        runtime=runtime,
        agent=ScriptedAgent([OK]),
        vcs=MockVcs(),
        scm=MockScm(),
        store=open_store,
    )


def _recording_runtime():
    from test_run_loop import MockRuntime

    class RecordingRuntime(MockRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.specs: list = []

        def create(self, spec, workspace):
            self.specs.append(spec)
            return super().create(spec, workspace)

    return RecordingRuntime()


def _script_gates_capturing_cache(monkeypatch):
    """Replace the gate pass with a recorder of the cache volumes it was
    handed — the argument the gates sandbox spec would mount verbatim."""
    import torve.application.runner as run_module

    seen: list[dict] = []

    def scripted(_worktree, _task_id, _config, _runtime, _run_id, _root, _meta=None, *_args):
        seen.append(dict(_args[-1]) if _args else {})
        return 0, "scripted", "cafecafe1234", [], ""

    monkeypatch.setattr(run_module, "_run_gates_in_worktree", scripted)
    return seen


def test_a_warm_tiers_run_mounts_the_slot_volume_attempt_and_gates_alike(repo, monkeypatch):
    from test_run_loop import task_for

    from torve.application.runner import run_task
    from torve.config.runconfig import CACHE_MOUNT
    from torve.domain.states import TaskState

    repo.seed()
    gate_caches = _script_gates_capturing_cache(monkeypatch)
    runtime = _recording_runtime()

    state = run_task(repo.root, task_for(repo), _warm_config(5), _cache_deps(repo, runtime))

    assert state.state is TaskState.READY
    assert runtime.specs  # the attempt sandbox, and only it: gates are scripted
    for spec in runtime.specs:
        assert spec.volumes == {"torve-cache-5": CACHE_MOUNT}
    # The gates lane of the same live pass is handed the same mount: the
    # battery is where the toolchain cold tax is paid.
    assert gate_caches == [{"torve-cache-5": CACHE_MOUNT}]


def test_a_cold_run_mounts_nothing_at_all(repo, monkeypatch):
    from test_run_loop import task_for

    from torve.application.runner import run_task

    repo.seed()
    gate_caches = _script_gates_capturing_cache(monkeypatch)
    runtime = _recording_runtime()

    run_task(repo.root, task_for(repo), RunnerConfig(), _cache_deps(repo, runtime))

    # Empty (the default) is cold exactly as today (D-35.4).
    assert runtime.specs and all(spec.volumes == {} for spec in runtime.specs)
    assert gate_caches == [{}]
