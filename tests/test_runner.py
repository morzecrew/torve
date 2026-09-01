from __future__ import annotations

import json

import pytest
import yaml
from conftest import context_for

from torve.application.telemetry import append_record, build_record, config_hash
from torve.config import layout
from torve.config.runconfig import RunnerConfig
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

