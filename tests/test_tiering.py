"""RFC 0004 phase 1: the tier mapping, harness-backed adapter mechanics,
provider routing at dispatch, and the telemetry fields nothing reconstructs
later. The sandbox side of authentication (env passthrough, auth volumes) is
integration-tested against real Docker in test_runtime_conformance-style
skips; everything else runs host-side."""

from __future__ import annotations

import dataclasses
import json
import subprocess

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from torve.adapters.agent.harness import (
    AgentMetadata,
    HarnessAgent,
    HarnessResult,
    build_prompt,
    parse_metadata,
)
from torve.adapters.vcs.git import repository_name
from torve.application.ports import AgentContext, AgentResult, ExecResult, SandboxHandle
from torve.application.runner import _restore_never_send, _sandbox_auth, _withhold_never_send
from torve.application.skills import materialize
from torve.cli import app
from torve.config.runconfig import (
    ProviderDenied,
    ProvidersConfig,
    RepositoryProviders,
    RunnerConfig,
    TierConfig,
    effective_skill_sets,
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
# RFC 0029: agent equipment — skills override and prompt extras


def test_tier_config_equipment_defaults_to_no_override():
    tier = TierConfig()
    assert tier.skills is None
    assert tier.prompt_extras == []


def test_effective_skill_sets_none_inherits_the_role_set():
    sets = RunnerConfig().skills.sets
    assert effective_skill_sets(TierConfig(), "implement", sets) == sets


def test_effective_skill_sets_override_replaces_the_role_set_wholesale():
    sets = RunnerConfig().skills.sets
    tier = TierConfig(skills=["prose-voice"])
    resolved = effective_skill_sets(tier, "implement", sets)

    assert resolved["implement"] == ["prose-voice"]  # replaced, not unioned
    assert resolved["review"] == sets["review"]  # other roles untouched
    assert sets["implement"] == ["flag-dont-flip", "ratchet-what-you-build"]  # source untouched


def test_effective_skill_sets_empty_list_equips_nothing(tmp_path):
    resolved = effective_skill_sets(TierConfig(skills=[]), "implement", RunnerConfig().skills.sets)
    assert materialize("implement", tmp_path, resolved) == []


def test_equipped_skill_resolution_keeps_the_materializers_refusals(tmp_path):
    """D-29.2: an unknown equipped name refuses at the same place an unknown
    configured name always has — the override only changes which names
    `materialize` is asked to resolve, never how it resolves them."""
    resolved = effective_skill_sets(
        TierConfig(skills=["definitely-not-a-skill"]), "implement", RunnerConfig().skills.sets
    )
    with pytest.raises(RuntimeError, match=r"neither shipped .* nor vendored"):
        materialize("implement", tmp_path, resolved)


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
    # The trace lives in the durable store — the worktree's root, under
    # `.torve/traces/` — and is referenced root-relative from the record,
    # never embedded (D-39.1). Nothing created the store before the run:
    # the one path helper the adapter writes through ensures the directory.
    assert result.trace_ref == ".torve/traces/T-9010.a1.trace.log"
    trace = tmp_path / ".torve" / "traces" / "T-9010.a1.trace.log"
    assert trace.read_text(encoding="utf-8") == result.output


def test_harness_without_metadata_is_an_uncontrolled_regime(tmp_path):
    tier = TierConfig(adapter="harness", provider="p", command="echo plain text only")
    ctx, agent = harness_ctx(tmp_path, tier)
    result = agent.run(ctx)
    assert result.cost_usd is None
    assert result.model_version is None  # D-4.6: absence is recorded, not invented


def test_parse_metadata_takes_the_last_json_object():
    output = '{"model": "early"}\nnoise\n{"cost_usd": 3, "model_version": "final-2"}'
    assert parse_metadata(output) == AgentMetadata(cost_usd=3.0, model_version="final-2")
    assert parse_metadata("no json here") == AgentMetadata()
    assert parse_metadata('{"model": ""}') == AgentMetadata()


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


def test_prompt_extras_are_absent_by_default():
    assert "house voice" not in build_prompt(Task(id="T-1", decisions=[]))


def test_prompt_extras_follow_the_charters_base_working_rules():
    """D-29.1: extras append after the base rules — never before, and the
    base rules are present regardless."""
    prompt = build_prompt(
        Task(id="T-1", decisions=[]),
        prompt_extras=["Docstrings and user-facing text follow the house voice."],
    )
    assert "- Docstrings and user-facing text follow the house voice." in prompt
    assert prompt.index("Gates run outside this session") < prompt.index("house voice")
    # The base rules stay unaddressable: still present, unaltered.
    assert "Skills for your role are under `.torve/skills/`" in prompt


def test_harness_agent_appends_the_tiers_prompt_extras(tmp_path):
    tier = TierConfig(
        adapter="api",
        provider="anthropic",
        model="m",
        command="cat {prompt}",
        prompt_extras=["Docstrings and user-facing text follow the house voice."],
    )
    ctx, agent = harness_ctx(tmp_path, tier)
    agent.run(ctx)
    prompt = (ctx.workspace / ".torve" / "tmp" / "prompt.md").read_text(encoding="utf-8")

    assert "- Docstrings and user-facing text follow the house voice." in prompt
    assert prompt.index("Gates run outside this session") < prompt.index("house voice")


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


def test_config_hash_separates_regimes_equipped_with_different_skills(tmp_path):
    """RFC 0029 §5.4: no new code measures equipment — the tiers dump
    `config_hash` already digests carries `skills` for free through
    `TierConfig.model_dump()`."""
    from torve.application.telemetry import config_hash

    manifest = tmp_path / "gates.yaml"
    manifest.write_text("schema_version: 1\ngates: []\n", encoding="utf-8")

    def tiers(executor: TierConfig) -> dict[str, TierConfig]:
        return {"planner": TierConfig(), "reviewer": TierConfig(), "executor": executor}

    generalist = RunnerConfig(tiers=tiers(TierConfig()))
    equipped = RunnerConfig(tiers=tiers(TierConfig(skills=["flag-dont-flip"])))

    assert config_hash(manifest, tmp_path, generalist) != config_hash(manifest, tmp_path, equipped)


def test_config_hash_separates_regimes_with_different_prompt_extras(tmp_path):
    from torve.application.telemetry import config_hash

    manifest = tmp_path / "gates.yaml"
    manifest.write_text("schema_version: 1\ngates: []\n", encoding="utf-8")

    def tiers(executor: TierConfig) -> dict[str, TierConfig]:
        return {"planner": TierConfig(), "reviewer": TierConfig(), "executor": executor}

    generalist = RunnerConfig(tiers=tiers(TierConfig()))
    equipped = RunnerConfig(tiers=tiers(TierConfig(prompt_extras=["house voice"])))

    assert config_hash(manifest, tmp_path, generalist) != config_hash(manifest, tmp_path, equipped)


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


def test_regime_preimage_lands_beside_the_host_telemetry_not_the_worktree(tmp_path):
    from torve.application.telemetry import config_hash

    host = tmp_path
    worktree = host / ".wt" / "T-0137"
    worktree.mkdir(parents=True)
    manifest = worktree / "gates.yaml"
    manifest.write_text("schema_version: 1\ngates: []\n", encoding="utf-8")

    digest = config_hash(manifest, worktree, RunnerConfig())

    assert (host / ".torve" / "regimes" / f"{digest}.json").exists()
    assert not (worktree / ".torve" / "regimes" / f"{digest}.json").exists()


def test_regime_preimage_write_is_best_effort_not_raising(tmp_path):
    from torve.application.telemetry import config_hash

    manifest = tmp_path / "gates.yaml"
    manifest.write_text("schema_version: 1\ngates: []\n", encoding="utf-8")

    # .torve exists as a file, so mkdir(parents=True) for regimes/ fails —
    # config_hash must still return a digest, never raise.
    (tmp_path / ".torve").write_text("not a directory", encoding="utf-8")

    digest = config_hash(manifest, tmp_path, RunnerConfig())
    assert digest


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
    assert parse_metadata(line) == AgentMetadata(
        cost_usd=0.0999, model_version="claude-haiku-4-5-20251001+claude-sonnet-5"
    )


def test_parse_metadata_reads_claude_usage_token_counts():
    # The claude envelope's usage block spells the four counts in
    # snake_case, flat beside cost and modelUsage (T-0186).
    line = json.dumps(
        {
            "total_cost_usd": 0.0999,
            "usage": {
                "input_tokens": 1000,
                "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 5000,
                "output_tokens": 300,
            },
            "modelUsage": {"claude-sonnet-5": {}},
        }
    )
    meta = parse_metadata(line)

    assert meta == AgentMetadata(
        cost_usd=0.0999,
        model_version="claude-sonnet-5",
        input_tokens=1000,
        cache_creation_tokens=200,
        cache_read_tokens=5000,
        output_tokens=300,
    )


def test_parse_metadata_reads_the_dsh_reporters_usage_object():
    # The dsh reporter's usage object spells the counts in camelCase and
    # adds reasoningTokens as a breakdown of output — deliberately not
    # extracted (T-0186): its own cost math bills outputTokens as the
    # complete output, so recording reasoning invites double counting.
    line = json.dumps(
        {
            "total_cost_usd": 0.05,
            "model": "deepseek-chat",
            "usage": {
                "inputTokens": 100,
                "outputTokens": 50,
                "cacheReadTokens": 900,
                "reasoningTokens": 10,
            },
        }
    )
    meta = parse_metadata(line)

    assert meta.input_tokens == 100
    assert meta.output_tokens == 50
    assert meta.cache_read_tokens == 900
    # The dsh shape reports no cache-creation count — absent stays absent.
    assert meta.cache_creation_tokens is None
    # reasoningTokens is a breakdown of output, not an additional count:
    # the metadata vocabulary has no field for it (T-0186), so the parse
    # could not have recorded it even by mistake.
    assert all(field.name != "reasoning_tokens" for field in dataclasses.fields(meta))


def test_parse_metadata_absent_token_keys_stay_none():
    # Best effort, never invented: no usage object, or a non-numeric value,
    # leaves the count unreported (D-4.6's self-reported regime).
    assert parse_metadata('{"cost_usd": 1.0}').input_tokens is None
    assert parse_metadata('{"cost_usd": 1.0}').cache_creation_tokens is None
    assert parse_metadata('{"usage": {"input_tokens": "NaN"}}').output_tokens is None


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
    assert parse_metadata(output) == AgentMetadata(
        cost_usd=0.0431, model_version="claude-haiku-4-5+claude-sonnet-5-20260315"
    )


def test_harness_agent_carries_reported_token_counts(tmp_path):
    # T-0186: the counts parse_metadata reads off a usage block ride the
    # harness result — the runner stamps them onto the record's agent block.
    tier = TierConfig(
        adapter="api",
        provider="anthropic",
        model="m",
        command='echo \'{"total_cost_usd": 0.5, "model": "m", '
        '"usage": {"input_tokens": 10, "cache_read_input_tokens": 100, '
        '"output_tokens": 5}}\'',
    )
    ctx, agent = harness_ctx(tmp_path, tier)
    result = agent.run(ctx)

    assert isinstance(result, HarnessResult)
    assert result.input_tokens == 10
    assert result.cache_read_tokens == 100
    assert result.output_tokens == 5
    # The harness did not report a cache-creation count — absent stays absent.
    assert result.cache_creation_tokens is None
    # The base AgentResult contract is intact.
    assert result.exit_code == 0
    assert result.cost_usd == 0.5


def test_agent_token_counts_records_only_what_was_reported():
    from torve.application.telemetry import agent_token_counts

    assert agent_token_counts(
        HarnessResult(exit_code=0, output="", input_tokens=10, output_tokens=5)
    ) == {"input_tokens": 10, "output_tokens": 5}

    # A plain AgentResult carries no token fields — the block stays empty,
    # and the absent keys are omitted from the record, never zeroed (D-4.6).
    assert agent_token_counts(AgentResult(exit_code=0, output="")) == {}


def test_attempt_record_carries_reported_token_counts(tmp_path):
    """T-0186 end to end: the token counts an adapter reports ride the agent
    block of the attempt record, and a silent adapter leaves the keys absent
    — absent stays absent, never zeroed (D-4.6's self-reported regime)."""
    import asyncio
    import subprocess

    from torve.application.runner import RunDeps, drive_attempts, real_hooks
    from torve.application.runstate import RunState
    from torve.config.runconfig import RunnerConfig, TierConfig
    from torve.domain.states import TaskState
    from torve.domain.task import Task

    class TokenAgent:
        kind = "harness"

        def run(self, ctx):
            return HarnessResult(
                exit_code=1,
                output="",
                cost_usd=4.05,
                model_version="m-x",
                input_tokens=1000,
                cache_read_tokens=9000,
                cache_creation_tokens=100,
                output_tokens=400,
            )

    class SilentAgent:
        kind = "harness"

        def run(self, ctx):
            return HarnessResult(exit_code=1, output="")

    class InertRuntime:
        def create(self, spec, workspace):
            return SandboxHandle(id="h-1", name=spec.name)

        def resolve_image(self, image):
            return None

        def sync_out(self, handle, worktree):
            pass

        def destroy(self, handle):
            pass

    def run_once(root, agent):
        worktree = root / "wt"
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
            agent=agent,
            vcs=None,  # type: ignore[arg-type]
            scm=None,  # type: ignore[arg-type]
            store=None,  # type: ignore[arg-type]
        )
        state = RunState(task_id=task.id, path=root / "T-9020.state.json")
        state.transition(TaskState.CLAIMED, "test claim")
        hooks = real_hooks(root, task, config, deps, worktree)
        asyncio.run(drive_attempts(state, task, config, hooks))

        telemetry = root / ".torve" / "telemetry.jsonl"
        records = [json.loads(line) for line in telemetry.read_text().splitlines()]
        failed = [r for r in records if r.get("gates_run") is False]
        assert failed
        return failed[0]["agent"]

    agent_block = run_once(tmp_path / "reporting", TokenAgent())
    assert agent_block["cost_usd"] == 4.05
    assert agent_block["input_tokens"] == 1000
    assert agent_block["cache_read_tokens"] == 9000
    assert agent_block["cache_creation_tokens"] == 100
    assert agent_block["output_tokens"] == 400

    silent_block = run_once(tmp_path / "silent", SilentAgent())
    assert silent_block["cost_usd"] is None
    for key in ("input_tokens", "cache_read_tokens", "cache_creation_tokens", "output_tokens"):
        assert key not in silent_block


def test_review_record_carries_reported_token_counts(repo, monkeypatch):
    """T-0186: the reviewer's token counts survive the base-shape rebuild in
    run_review and ride the review record's agent block — only the reported
    ones, absent keys omitted (D-4.6)."""
    from test_review_run import review_config, reviewer_output
    from test_run_loop import (
        OK,
        MockRuntime,
        MockScm,
        MockVcs,
        MockWorkspace,
        ScriptedAgent,
        task_for,
    )

    import torve.application.runner as run_module
    from torve.adapters.store.durable import open_store
    from torve.application.runner import RunDeps, run_task
    from torve.domain.states import TaskState

    repo.seed()

    def scripted_gates(*args, **kwargs):
        return 0, "scripted", "cafecafe1234", [], "diff --git a/x b/x"

    monkeypatch.setattr(run_module, "_run_gates_in_worktree", scripted_gates)

    deps = RunDeps(
        workspace=MockWorkspace(repo.root),
        runtime=MockRuntime(),
        agent=ScriptedAgent([OK]),
        vcs=MockVcs(),
        scm=MockScm(),
        store=open_store,
        review_agent=ScriptedAgent(
            [
                HarnessResult(
                    exit_code=0,
                    output=reviewer_output([]),
                    cost_usd=0.2,
                    model_version="m-r",
                    input_tokens=50,
                    cache_read_tokens=5,
                    output_tokens=10,
                )
            ]
        ),
    )

    # The review's record rides the worktree's manifest telemetry path.
    (repo.root / ".wt" / "T-9001" / ".torve").mkdir(parents=True, exist_ok=True)
    (repo.root / ".wt" / "T-9001" / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\ngates: []\n", encoding="utf-8"
    )

    state = run_task(repo.root, task_for(repo), review_config(), deps)

    assert state.state is TaskState.READY
    telemetry = repo.root / ".torve" / "telemetry.jsonl"
    records = [json.loads(line) for line in telemetry.read_text().splitlines()]
    review_records = [r for r in records if r.get("kind") == "review"]
    assert len(review_records) == 1
    agent_block = review_records[0]["agent"]
    assert agent_block["input_tokens"] == 50
    assert agent_block["cache_read_tokens"] == 5
    assert agent_block["output_tokens"] == 10
    assert "cache_creation_tokens" not in agent_block
