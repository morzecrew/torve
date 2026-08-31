"""RFC 0004 phase 1: the tier mapping, harness-backed adapter mechanics,
provider routing at dispatch, and the telemetry fields nothing reconstructs
later. The sandbox side of authentication (env passthrough, auth volumes) is
integration-tested against real Docker in test_runtime_conformance-style
skips; everything else runs host-side."""

from __future__ import annotations

import json
import subprocess

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from torve.adapters.agent.harness import HarnessAgent, build_prompt, parse_metadata
from torve.adapters.vcs.git import repository_name
from torve.application.ports import AgentContext, ExecResult, SandboxHandle
from torve.application.runner import _restore_never_send, _sandbox_auth, _withhold_never_send
from torve.cli import app
from torve.config.runconfig import (
    ProviderDenied,
    ProvidersConfig,
    RepositoryProviders,
    RunnerConfig,
    TierConfig,
    route_provider,
    tier_for,
    tier_name_for,
)
from torve.domain.task import InheritedDecision, Scope, Task

# ----------------------- #
# The tier mapping


def test_default_tiers_are_all_fake():
    config = RunnerConfig()
    assert set(config.tiers) == {"planner", "executor", "reviewer"}
    assert all(tier.adapter == "fake" for tier in config.tiers.values())


def test_a_real_adapter_needs_a_command():
    with pytest.raises(ValidationError, match="needs a command"):
        TierConfig(adapter="api", provider="anthropic")


def test_a_real_adapter_needs_a_provider():
    # Silence is not a policy (§6b): a real adapter must say where it sends.
    with pytest.raises(ValidationError, match="needs a provider"):
        TierConfig(adapter="harness", command="claude -p x")


def test_unknown_adapter_is_rejected():
    with pytest.raises(ValidationError, match="unknown agent adapter"):
        TierConfig(adapter="wishful")


def test_tier_for_missing_entry_is_a_configuration_error():
    config = RunnerConfig(tiers={"executor": TierConfig()})
    with pytest.raises(ValueError, match="no tier 'planner'"):
        tier_for(config, "planner")


# ....................... #
# Provider routing (D-4.8)


def providers(**overrides):
    return ProvidersConfig(**overrides)


def test_default_allow_admits():
    route_provider(providers(default=["anthropic"]), "org/repo", "anthropic")


def test_empty_provider_is_the_fake_tier_and_routes():
    route_provider(providers(), "org/repo", "")


def test_unconfigured_policy_denies_a_real_provider():
    with pytest.raises(ProviderDenied, match="none configured"):
        route_provider(providers(), "org/repo", "anthropic")


def test_repository_allow_overrides_the_default():
    policy = providers(
        default=["cheap-vendor"],
        repositories={
            "payments-core": RepositoryProviders(
                allow=["vendor-eu-only"], deny_reason="customer data in fixtures"
            )
        },
    )
    route_provider(policy, "payments-core", "vendor-eu-only")
    with pytest.raises(ProviderDenied, match="customer data in fixtures"):
        route_provider(policy, "payments-core", "cheap-vendor")
    route_provider(policy, "other/repo", "cheap-vendor")


def test_repository_name_prefers_the_origin_remote(tmp_path):
    root = tmp_path / "checkout-dir"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    assert repository_name(root) == "checkout-dir"  # no remote -> directory name
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:morzecrew/torve.git"],
        cwd=root,
        check=True,
    )
    assert repository_name(root) == "morzecrew/torve"


def test_repository_name_parses_https_remotes(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/morzecrew/torve"],
        cwd=root,
        check=True,
    )
    assert repository_name(root) == "morzecrew/torve"


# ....................... #
# Sandbox authentication routes (§1, §2)


def test_api_and_harness_pass_key_names_never_values():
    tier = TierConfig(adapter="api", command="run", provider="p", api_key_env=["ANTHROPIC_API_KEY"])
    env_passthrough, volumes = _sandbox_auth(tier, worker_slot=0)
    assert env_passthrough == ("ANTHROPIC_API_KEY",)
    assert volumes == {}


def test_subscription_mounts_one_volume_per_worker_slot():
    tier = TierConfig(adapter="subscription", command="run", provider="p")
    _, volumes = _sandbox_auth(tier, worker_slot=2)
    assert volumes == {"torve-auth-2": "/auth"}
    env_passthrough, _ = _sandbox_auth(tier, worker_slot=2)
    assert env_passthrough == ()


def test_fake_gets_no_auth():
    assert _sandbox_auth(TierConfig(), worker_slot=0) == ((), {})


# ....................... #
# never_send (§6b): lifted out of the sandbox's world, restored after


def test_never_send_files_are_withheld_and_restored(tmp_path):
    worktree = tmp_path / "wt"
    (worktree / "fixtures").mkdir(parents=True)
    secret = worktree / "fixtures" / "production-users.json"
    secret.write_text("real customer data", encoding="utf-8")
    (worktree / "app.py").write_text("code\n", encoding="utf-8")

    withheld = _withhold_never_send(worktree, ["**/fixtures/production-*"])
    assert not secret.exists()
    assert (worktree / "app.py").exists()

    _restore_never_send(withheld)
    assert secret.read_text(encoding="utf-8") == "real customer data"


def test_an_agent_edit_to_a_withheld_path_is_discarded(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    key = worktree / "deploy.pem"
    key.write_text("original", encoding="utf-8")
    withheld = _withhold_never_send(worktree, ["**/*.pem"])
    key.write_text("agent-planted", encoding="utf-8")
    _restore_never_send(withheld)
    assert key.read_text(encoding="utf-8") == "original"


def test_empty_never_send_touches_nothing(tmp_path):
    assert _withhold_never_send(tmp_path, []) == {}


# ....................... #
# HarnessAgent: prompt in, command in the sandbox, trace and metadata out


class HostShellRuntime:
    """Runs the tier command on the host with cwd at the workspace — a double
    for the adapter's staging, not a Runtime port."""

    def __init__(self, workspace):
        self.workspace = workspace

    def exec(self, handle, command, timeout_s):
        proc = subprocess.run(
            command,
            shell=True,
            cwd=self.workspace,
            timeout=timeout_s,
            capture_output=True,
            text=True,
            check=False,
        )
        return ExecResult(
            exit_code=proc.returncode,
            output=(proc.stdout or "") + (proc.stderr or ""),
            duration_s=0.0,
        )


def harness_ctx(tmp_path, tier):
    workspace = tmp_path / "wt-t9010" / "T-9010"
    workspace.mkdir(parents=True)
    task = Task(
        id="T-9010",
        intent="Make the widget idempotent.",
        scope=Scope(allow=["src/**"]),
        acceptance=["pytest -q"],
        decisions=[
            InheritedDecision(
                id="D-9", grade="LOCKED", text="Widgets are idempotent", paths=["src/widget.py"]
            )
        ],
    )
    return AgentContext(
        task=task,
        attempt=1,
        workspace=workspace,
        handle=SandboxHandle(id="h", name="h"),
        runtime=HostShellRuntime(workspace),
        workdir=str(workspace),
        timeout_s=30.0,
    ), HarnessAgent(tier)


def test_harness_agent_stages_prompt_and_captures_trace(tmp_path):
    tier = TierConfig(
        adapter="api",
        provider="anthropic",
        model="test-model-1",
        command='cat {prompt} && echo \'{"total_cost_usd": 0.12, "model": "{model}"}\'',
    )
    ctx, agent = harness_ctx(tmp_path, tier)
    result = agent.run(ctx)

    assert result.exit_code == 0
    prompt = (ctx.workspace / ".torve" / "tmp" / "prompt.md").read_text(encoding="utf-8")
    assert "Make the widget idempotent." in prompt
    assert "`D-9` (LOCKED)" in prompt
    assert ".torve/tasks/T-9010/log.yaml" in prompt
    assert "pytest -q" in prompt
    # The command saw the prompt file and its {model} substitution.
    assert "Make the widget idempotent." in result.output
    # Metadata parsed from the trailing JSON line (D-4.6).
    assert result.cost_usd == 0.12
    assert result.model_version == "test-model-1"
    # The trace lives beside the worktree and is referenced, not embedded.
    assert result.trace_ref is not None
    trace = ctx.workspace.parent / "T-9010.a1.trace.log"
    assert trace.read_text(encoding="utf-8") == result.output
    assert result.trace_ref == str(trace)


def test_harness_without_metadata_is_an_uncontrolled_regime(tmp_path):
    tier = TierConfig(adapter="harness", provider="p", command="echo plain text only")
    ctx, agent = harness_ctx(tmp_path, tier)
    result = agent.run(ctx)
    assert result.cost_usd is None
    assert result.model_version is None  # D-4.6: absence is recorded, not invented


def test_parse_metadata_takes_the_last_json_object():
    output = '{"model": "early"}\nnoise\n{"cost_usd": 3, "model_version": "final-2"}'
    assert parse_metadata(output) == (3.0, "final-2")
    assert parse_metadata("no json here") == (None, None)
    assert parse_metadata('{"model": ""}') == (None, None)


def test_prompt_states_explicit_emptiness():
    prompt = build_prompt(Task(id="T-1", decisions=[]))
    assert "none apply (explicitly)" in prompt
    assert "unconstrained" in prompt
    # The recurring gate red of the 0022–0024 campaign: every task needed a
    # hand triage moving corpus coordinates out of user-facing strings.
    assert "no corpus coordinates" in prompt


def test_prompt_carries_the_engine_base_sha_pin():
    """D-A.7: the sandbox cannot resolve the host .git pointer, so the pin
    travels in the prompt — and only when the engine actually has it."""
    sha = "83ceeaeaf29d7aa189f7e7d308cce698079af624"
    assert f"`base_sha` is `{sha}`" in build_prompt(Task(id="T-1", decisions=[]), base_sha=sha)
    assert "base_sha" not in build_prompt(Task(id="T-1", decisions=[]))


# ....................... #
# Dispatch (CLI): routing enforced before anything exists


def seeded_run_repo(tmp_path, tier_yaml, providers_yaml="providers: {default: []}"):
    root = tmp_path / "repo"
    (root / ".torve" / "tasks" / "T-0042").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".torve" / "tasks" / "T-0042" / "contract.yaml").write_text(
        "schema_version: 1\nid: T-0042\ndecisions: []\n", encoding="utf-8"
    )
    (root / ".torve" / "config.yaml").write_text(
        f"schema_version: 1\ntiers:\n  planner: {{adapter: fake}}\n"
        f"  reviewer: {{adapter: fake}}\n  executor: {tier_yaml}\n{providers_yaml}\n",
        encoding="utf-8",
    )
    return root


def test_run_refuses_an_unrouted_provider_with_exit_3(tmp_path):
    root = seeded_run_repo(
        tmp_path,
        "{adapter: api, command: run-it, provider: anthropic, api_key_env: [K]}",
    )
    result = CliRunner().invoke(app, ["run", "T-0042", "--root", str(root)])
    assert result.exit_code == 3
    assert "not permitted" in result.stderr


def test_run_refuses_a_missing_tier_with_exit_3(tmp_path):
    root = seeded_run_repo(tmp_path, "{adapter: fake}")
    (root / ".torve" / "config.yaml").write_text(
        "schema_version: 1\ntiers:\n  planner: {adapter: fake}\n", encoding="utf-8"
    )
    result = CliRunner().invoke(app, ["run", "T-0042", "--root", str(root)])
    assert result.exit_code == 3
    assert "no tier 'executor'" in result.stderr


def test_scenario_with_a_real_tier_is_refused(tmp_path):
    root = seeded_run_repo(
        tmp_path,
        "{adapter: api, command: run-it, provider: anthropic}",
        "providers: {default: [anthropic]}",
    )
    scenario = tmp_path / "scenario.yaml"
    scenario.write_text("attempts:\n  - {exit: 0}\n", encoding="utf-8")
    result = CliRunner().invoke(
        app, ["run", "T-0042", "--root", str(root), "--scenario", str(scenario)]
    )
    assert result.exit_code == 3
    assert "FakeAgent-only" in result.stderr


# ....................... #
# Telemetry (§6): the regime hash and the feedback stream


def test_config_hash_moves_with_the_tier_mapping(tmp_path):
    from torve.application.telemetry import config_hash

    manifest = tmp_path / "gates.yaml"
    manifest.write_text("schema_version: 1\ngates: []\n", encoding="utf-8")
    plain = RunnerConfig()
    tiered = RunnerConfig(
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": TierConfig(adapter="api", command="c", provider="p"),
        }
    )
    assert config_hash(manifest, tmp_path, plain) != config_hash(manifest, tmp_path, tiered)
    assert config_hash(manifest, tmp_path, plain) == config_hash(manifest, tmp_path, plain)


# ....................... #
# The regime preimage (D-4.19, A-72): config_hash writes its own parts


def test_config_hash_writes_the_regime_preimage(tmp_path):
    from torve.application.telemetry import config_hash

    manifest = tmp_path / "gates.yaml"
    manifest.write_text("schema_version: 1\ngates: []\n", encoding="utf-8")
    digest = config_hash(manifest, tmp_path, RunnerConfig())

    regime = tmp_path / ".torve" / "regimes" / f"{digest}.json"
    parts = json.loads(regime.read_text(encoding="utf-8"))
    assert parts["gates.yaml"] == manifest.read_text(encoding="utf-8")
    assert "torve" in parts and "forze" in parts


def test_two_regimes_diff_as_two_files(tmp_path):
    from torve.application.telemetry import config_hash

    manifest = tmp_path / "gates.yaml"
    manifest.write_text("schema_version: 1\ngates: []\n", encoding="utf-8")
    plain = RunnerConfig()
    tiered = RunnerConfig(
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": TierConfig(adapter="api", command="c", provider="p"),
        }
    )
    digest_plain = config_hash(manifest, tmp_path, plain)
    digest_tiered = config_hash(manifest, tmp_path, tiered)

    regimes = tmp_path / ".torve" / "regimes"
    assert (regimes / f"{digest_plain}.json").exists()
    assert (regimes / f"{digest_tiered}.json").exists()
    assert regimes / f"{digest_plain}.json" != regimes / f"{digest_tiered}.json"


def test_regime_preimage_is_written_once_only_if_absent(tmp_path):
    from torve.application.telemetry import config_hash

    manifest = tmp_path / "gates.yaml"
    manifest.write_text("schema_version: 1\ngates: []\n", encoding="utf-8")
    digest = config_hash(manifest, tmp_path, RunnerConfig())

    regime = tmp_path / ".torve" / "regimes" / f"{digest}.json"
    regime.write_text('{"planted": true}', encoding="utf-8")

    config_hash(manifest, tmp_path, RunnerConfig())

    assert json.loads(regime.read_text(encoding="utf-8")) == {"planted": True}


def test_feedback_appends_a_keyed_record(tmp_path):
    root = tmp_path / "repo"
    (root / ".torve").mkdir(parents=True)
    result = CliRunner().invoke(
        app,
        [
            "feedback",
            "T-0042",
            "--human-minutes",
            "25",
            "--rework",
            "--root",
            str(root),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    emitted = json.loads(result.stdout)
    assert emitted["task_id"] == "T-0042"
    assert emitted["human_minutes"] == 25
    assert emitted["rework_after_review"] is True
    lines = (root / ".torve" / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["task_id"] == "T-0042"


# ....................... #
# Tier variants (RFC 0027 §5.1, D-27.3): dotted entries in `tiers`, an
# optional contract field selecting one, loud refusal on an unknown variant,
# the seat literal untouched, and the variant riding the tiers digest into
# config_hash.


def test_tier_name_for_is_the_seat_alone_with_no_variant_named():
    task = Task(id="T-1", decisions=[])
    assert task.tier == "executor"
    assert tier_name_for(task) == "executor"


def test_tier_name_for_dots_the_variant_onto_the_seat():
    task = Task(id="T-1", decisions=[], tier="executor", tier_variant="long-context")
    assert tier_name_for(task) == "executor.long-context"
    # The seat literal is unchanged — a variant refines it, never replaces it.
    assert task.tier == "executor"


def test_unknown_variant_is_refused_loudly_not_a_fallback_to_the_seat():
    config = RunnerConfig(tiers={"executor": TierConfig()})
    task = Task(id="T-1", decisions=[], tier="executor", tier_variant="ghost")
    with pytest.raises(ValueError, match=r"no tier 'executor\.ghost'"):
        tier_for(config, tier_name_for(task))


def test_a_variant_resolves_once_configured_as_a_dotted_entry():
    fast = TierConfig(adapter="api", command="run", provider="p", model="fast")
    config = RunnerConfig(tiers={"executor": TierConfig(), "executor.fast": fast})
    task = Task(id="T-1", decisions=[], tier="executor", tier_variant="fast")
    assert tier_for(config, tier_name_for(task)) is fast


def test_two_variants_are_provably_two_regimes_in_the_config_hash(tmp_path):
    from torve.application.telemetry import config_hash

    manifest = tmp_path / "gates.yaml"
    manifest.write_text("schema_version: 1\ngates: []\n", encoding="utf-8")
    base = {"planner": TierConfig(), "reviewer": TierConfig(), "executor": TierConfig()}
    variant_a = RunnerConfig(tiers={**base, "executor.long-context": TierConfig(model="a")})
    variant_b = RunnerConfig(tiers={**base, "executor.long-context": TierConfig(model="b")})
    assert config_hash(manifest, tmp_path, variant_a) != config_hash(manifest, tmp_path, variant_b)


# ....................... #
# retry_variant (RFC 0027 §5.1a, D-27.11): a rung to nowhere is a
# configuration error at load time, not a dispatch-time surprise.


def test_retry_variant_must_name_a_configured_tier():
    with pytest.raises(ValidationError, match="retry_variant names no configured tier"):
        RunnerConfig(
            tiers={"executor": TierConfig(retry_variant="executor.ghost")}
        )


def test_retry_variant_naming_a_real_configured_tier_is_accepted():
    config = RunnerConfig(
        tiers={
            "executor": TierConfig(retry_variant="executor.fast"),
            "executor.fast": TierConfig(),
        }
    )
    assert config.tiers["executor"].retry_variant == "executor.fast"


def test_parse_metadata_reads_claude_model_usage_keys():
    # The claude CLI's json result names models as modelUsage keys — the
    # dated snapshot ids D-4.6 wants recorded (found in the first live run).
    line = json.dumps(
        {
            "total_cost_usd": 0.0999,
            "modelUsage": {"claude-haiku-4-5-20251001": {}, "claude-sonnet-5": {}},
        }
    )
    assert parse_metadata(line) == (0.0999, "claude-haiku-4-5-20251001+claude-sonnet-5")


def test_parse_metadata_reads_opencodes_nested_step_finish_part():
    # opencode's `--format json` nests cost and per-model token counts one
    # level down, under the last step_finish event's `part` — not at the
    # flat keys the claude CLI uses (T-0132).
    output = "\n".join(
        [
            json.dumps({"type": "step_start", "part": {"text": "working..."}}),
            json.dumps(
                {
                    "type": "step_finish",
                    "part": {
                        "cost": 0.0431,
                        "tokens": {"claude-sonnet-5-20260315": {}, "claude-haiku-4-5": {}},
                    },
                }
            ),
        ]
    )
    assert parse_metadata(output) == (0.0431, "claude-haiku-4-5+claude-sonnet-5-20260315")
