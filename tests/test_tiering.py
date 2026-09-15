"""S-0004 phase 1: the tier mapping, harness-backed adapter mechanics,
provider routing at dispatch, and the telemetry fields nothing reconstructs
later. The sandbox side of authentication (env passthrough, auth volumes) is
integration-tested against real Docker in test_runtime_conformance-style
skips; everything else runs host-side."""

from __future__ import annotations

import dataclasses
import json
import pathlib
import subprocess

import pytest
from conftest import harness, seam
from pydantic import ValidationError
from typer.testing import CliRunner

from torve.adapters.agent.harness import (
    AgentMetadata,
    BurnProfile,
    HarnessAgent,
    HarnessResult,
    TurnBurn,
    build_prompt,
    parse_burn,
    parse_context_curve,
    parse_metadata,
    parse_tool_calls,
)
from torve.adapters.vcs.git import repository_name
from torve.application.ports import AgentContext, AgentResult, ExecResult, SandboxHandle
from torve.application.session import (
    _restore_never_send,
    _sandbox_auth,
    _withhold_never_send,
)
from torve.application.skills import materialize
from torve.base.shell import truncate
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


def test_the_prompt_says_what_asked_for_the_work(tmp_path):
    """S-0060/D-9: the line above the decisions names the source, its title
    and where it lives — read from the pack the engine wrote into the
    worktree, because an adapter does not reach the corpus."""

    from torve.adapters.agent.harness import PACK_RELPATH, build_prompt, source_line
    from torve.domain.task import Task

    task = Task(id="T-0001", source="audit/soc2-2026", intent="close the gap", decisions=[])
    pack = tmp_path / PACK_RELPATH
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "source.json").write_text(
        json.dumps({"title": "A gap", "ref": "https://x.invalid/42"}), encoding="utf-8"
    )

    line = source_line(tmp_path, task)

    assert line == 'audit/soc2-2026 — "A gap" (https://x.invalid/42)'
    assert f"Source: {line}" in build_prompt(task, asked=line)

    # No pack for it: the identifier alone, never a crash.
    assert source_line(tmp_path / "elsewhere", task) == "audit/soc2-2026"
    # No source: the prompt says nothing extra.
    assert "Source:" not in build_prompt(Task(id="T-0002", decisions=[]))


def test_the_first_message_carries_the_packs_small_files(tmp_path):
    """S-0076/D-1: the small deterministic files the engine wrote arrive with
    the task — the same seven reads every attempt opened them with become
    none — and the prompt does not also tell the agent to go and read them.
    `decisions.json` stays behind a read and is named as such."""

    from torve.adapters.agent.harness import PACK_RELPATH, build_prompt, pack_handover
    from torve.domain.task import Task

    pack = tmp_path / PACK_RELPATH
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "gates.json").write_text('{"gates": ["the battery"]}', encoding="utf-8")
    (pack / "tests.json").write_text('{"coverage": ["the tests"]}', encoding="utf-8")
    (pack / "attempts.json").write_text('{"attempts": ["the red"]}', encoding="utf-8")
    (pack / "contended.json").write_text('{"contended": ["the paths"]}', encoding="utf-8")
    (pack / "decisions.json").write_text('{"inherited": ["the rows"]}', encoding="utf-8")
    # S-0077/D-4: the map rides the same message, and it is markdown rather
    # than JSON, so the fence it lands in is not the JSON one.
    (pack / "map.md").write_text("# Where things are\n\n- `src/` — the layout", encoding="utf-8")

    handed = pack_handover(tmp_path)
    prompt = build_prompt(Task(id="T-0001", decisions=[]), pack=handed)

    for body in ("the battery", "the tests", "the red", "the paths", "the layout"):
        assert body in prompt

    assert "```\n# Where things are" in prompt
    assert "```json\n# Where things are" not in prompt

    # Named once, as what it is — never as a file to open.
    for name in ("map.md", "gates.json", "tests.json", "attempts.json", "contended.json"):
        assert f"{PACK_RELPATH}/{name}" not in prompt

    assert "the rows" not in prompt
    assert f"{PACK_RELPATH}/decisions.json" in prompt

    # Deterministic for the same pack, and no pack at all is no section.
    assert pack_handover(tmp_path) == handed
    assert pack_handover(tmp_path / "elsewhere") == ""
    assert "What the engine knows" not in build_prompt(Task(id="T-0002", decisions=[]))


def test_the_skills_travel_in_system_position_and_are_not_read(tmp_path):
    """S-0067/A-4: the prompt used to say "read every `SKILL.md` there before
    writing code", and every attempt in the corpus did — three round trips at
    call 0 on bytes the engine had just written into the worktree. A read does
    not avoid what the bodies cost, because they land in the context either
    way, so the trips were the whole price.

    The bodies travel in system position only: both channels are re-sent with
    every request, so a text in both is a text paid twice."""

    from torve.adapters.agent.harness import (
        SKILLS_RELPATH,
        build_prompt,
        skills_handover,
        working_rules,
    )
    from torve.domain.task import Task

    skills = tmp_path / SKILLS_RELPATH

    for name, body in (("working-rules", "how work is done"), ("tdd", "a failing test first")):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(f"# {name}\n\n{body}", encoding="utf-8")

    handed = skills_handover(tmp_path)

    assert "how work is done" in handed
    assert "a failing test first" in handed
    # Alphabetical, so the same worktree yields the same bytes every attempt.
    assert handed.index("`tdd`") < handed.index("`working-rules`")

    system = working_rules("", handed)
    assert "how work is done" in system
    # The prompt channel points and never carries: the bodies are paid once.
    prompt = build_prompt(Task(id="T-0001", decisions=[]))
    assert "how work is done" not in prompt
    assert "The skills for your role are in system position" in prompt
    assert SKILLS_RELPATH in prompt

    # No skills at all is no section, not an error.
    assert skills_handover(tmp_path / "elsewhere") == ""
    assert "Your skills" not in working_rules("", "")


def test_default_tiers_are_all_fake():
    config = RunnerConfig()
    assert set(config.tiers) == {"planner", "executor", "reviewer"}
    assert all(tier.adapter == "fake" for tier in config.tiers.values())


def test_a_real_adapter_needs_no_command_because_the_image_carries_it():
    """S-0063/D-1: the shell that starts a harness is the image's own
    `/opt/torve/run`, and a seat naming no image runs the runtime's default
    one — so there is nothing left for a seat to be missing here."""

    seat = TierConfig(adapter="api", provider="anthropic")

    assert seat.image == ""


def test_a_real_adapter_needs_a_provider():
    # Silence is not a policy (§6b): a real adapter must say where it sends.
    with pytest.raises(ValidationError, match="needs a provider"):
        TierConfig(adapter="harness", image="probe-sandbox")


def test_unknown_adapter_is_rejected():
    with pytest.raises(ValidationError, match="unknown agent adapter"):
        TierConfig(adapter="wishful")


def test_tier_for_missing_entry_is_a_configuration_error():
    config = RunnerConfig(tiers={"executor": TierConfig()})
    with pytest.raises(ValueError, match="no tier 'planner'"):
        tier_for(config, "planner")


# ....................... #
# S-0029: agent equipment — skills override and prompt extras


def test_tier_config_equipment_defaults_to_no_override():
    tier = TierConfig()
    assert tier.skills is None
    assert tier.prompt_extras == ""


def test_effective_skill_sets_none_inherits_the_role_set():
    sets = RunnerConfig().skills.sets
    assert effective_skill_sets(TierConfig(), "implement", sets) == sets


def test_effective_skill_sets_override_replaces_the_role_set_wholesale():
    # The sets come from the role profiles now (S-0061/D-11), so a unit test of
    # the override rule supplies its own rather than leaning on a default that
    # is no longer written in code.
    sets = {"implement": ["flag-dont-flip", "ratchet-what-you-build"], "review": ["ratchet"]}
    tier = TierConfig(skills=["prose-voice"])
    resolved = effective_skill_sets(tier, "implement", sets)

    assert resolved["implement"] == ["prose-voice"]  # replaced, not unioned
    assert resolved["review"] == sets["review"]  # other roles untouched
    assert sets["implement"] == ["flag-dont-flip", "ratchet-what-you-build"]  # source untouched


def test_effective_skill_sets_empty_list_equips_nothing(tmp_path):
    resolved = effective_skill_sets(TierConfig(skills=[]), "implement", RunnerConfig().skills.sets)
    assert materialize("implement", tmp_path, resolved) == []


def test_equipped_skill_resolution_keeps_the_materializers_refusals(tmp_path):
    """S-0029/D-2: an unknown equipped name refuses at the same place an unknown
    configured name always has — the override only changes which names
    `materialize` is asked to resolve, never how it resolves them."""
    resolved = effective_skill_sets(
        TierConfig(skills=["definitely-not-a-skill"]), "implement", RunnerConfig().skills.sets
    )
    with pytest.raises(RuntimeError, match=r"neither shipped .* nor vendored"):
        materialize("implement", tmp_path, resolved)


# ....................... #
# Provider routing (S-0004/D-8)


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
# Sandbox authentication routes (§1, §2, S-0064/D-9)


def _recorded(tier: TierConfig, key_env: str = "ANTHROPIC_API_KEY") -> RunnerConfig:
    """A config whose record names the credential for this seat's provider. The
    name is the provider's now: a harness dials whatever it is pointed at, and
    which key opens the door was never a fact about the dialer."""

    from torve.config.providers import Provider, Route

    if not tier.provider:
        return RunnerConfig()

    return RunnerConfig(
        provider_records={
            tier.provider: Provider(
                name=tier.provider,
                key_env=key_env,
                routes={"openai": Route(base_url="https://p.test/v1")},
            )
        }
    )


def test_api_and_harness_pass_key_names_never_values():
    tier = TierConfig(adapter="api", provider="p")
    env_passthrough, volumes = _sandbox_auth(_recorded(tier), tier, worker_slot=0)
    assert env_passthrough == ("ANTHROPIC_API_KEY",)
    assert volumes == {}


def test_subscription_mounts_one_volume_per_worker_slot():
    """The route for a seat whose provider record names no credential — a
    harness with no env form (S-0063/D-18). One whose record names a variable
    is forwarded by name and mounts nothing; see tests/test_session.py."""

    tier = TierConfig(adapter="subscription", provider="p")
    _, volumes = _sandbox_auth(RunnerConfig(), tier, worker_slot=2)
    assert volumes == {"torve-auth-2": "/auth"}
    env_passthrough, _ = _sandbox_auth(RunnerConfig(), tier, worker_slot=2)
    assert env_passthrough == ()


def test_fake_gets_no_auth():
    assert _sandbox_auth(RunnerConfig(), TierConfig(), worker_slot=0) == ((), {})


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


def test_empty_never_send_touches_nothing(tmp_path, monkeypatch):
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


def test_harness_agent_stages_prompt_and_captures_trace(tmp_path, monkeypatch):
    tier = TierConfig(
        adapter="api",
        provider="anthropic",
        model="test-model-1",
    )
    ctx, agent = harness_ctx(
        tmp_path,
        tier.model_copy(
            update={
                "env": seam(
                    'cat "$TORVE_PROMPT" && echo \'{"total_cost_usd": 0.12, "model": "\'"$TORVE_MODEL"\'"}\'',
                    monkeypatch,
                )
            },
        ),
    )
    result = agent.run(ctx)

    assert result.exit_code == 0
    prompt = (ctx.workspace / ".torve" / "tmp" / "prompt.md").read_text(encoding="utf-8")
    assert "Make the widget idempotent." in prompt
    assert "`D-9` (LOCKED)" in prompt
    assert "`working-rules`" in prompt
    assert "pytest -q" in prompt
    # The command saw the prompt file and its {model} substitution.
    assert "Make the widget idempotent." in result.output
    # Metadata parsed from the trailing JSON line (S-0004/D-6).
    assert result.cost_usd == 0.12
    assert result.model_version == "test-model-1"
    # The trace lives in the durable store — the worktree's root, under
    # `.torve/traces/` — and is referenced root-relative from the record,
    # never embedded (S-0039/D-1). Nothing created the store before the run:
    # the one path helper the adapter writes through ensures the directory.
    assert result.trace_ref == ".torve/traces/T-9010.a1.trace.log"
    trace = tmp_path / ".torve" / "traces" / "T-9010.a1.trace.log"
    assert trace.read_text(encoding="utf-8") == result.output


def test_harness_without_metadata_is_an_uncontrolled_regime(tmp_path, monkeypatch):
    tier = TierConfig(adapter="harness", provider="p")
    ctx, agent = harness_ctx(
        tmp_path, tier.model_copy(update={"env": seam("echo plain text only", monkeypatch)})
    )
    result = agent.run(ctx)
    assert result.cost_usd is None
    assert result.model_version is None  # S-0004/D-6: absence is recorded, not invented


def test_parse_metadata_takes_the_last_json_object():
    output = '{"model": "early"}\nnoise\n{"cost_usd": 3, "model_version": "final-2"}'
    assert parse_metadata(output) == AgentMetadata(cost_usd=3.0, model_version="final-2")
    assert parse_metadata("no json here") == AgentMetadata()
    assert parse_metadata('{"model": ""}') == AgentMetadata()


def test_prompt_states_explicit_emptiness():
    prompt = build_prompt(Task(id="T-1", decisions=[]))
    assert "none apply (explicitly)" in prompt
    assert "unconstrained" in prompt


def test_prompt_never_asks_for_a_pin_the_sandbox_cannot_resolve():
    """S-0001/D-36, S-0044/D-10: the agent states an entry and the engine
    writes the log, so nothing asks it for the base commit it cannot see."""

    assert "base_sha" not in build_prompt(Task(id="T-1", decisions=[]))


def test_prompt_extras_are_absent_by_default():
    assert "house voice" not in build_prompt(Task(id="T-1", decisions=[]))


def test_bare_prompt_carries_the_intent_and_nothing_else():
    """S-0074/D-2: the fourth mode asserts the absence directly — the bare
    arm's prompt is the task's intent and nothing else: no inherited rows,
    no context pack, no working rules, no scope, no acceptance."""
    task = Task(
        id="T-1",
        intent="Make the widget idempotent.",
        scope=Scope(allow=["src/**"]),
        acceptance=["pytest -q"],
        decisions=[
            InheritedDecision(
                id="D-9", grade="LOCKED", text="Widgets are idempotent", paths=["src/widget.py"]
            )
        ],
    )

    for prompt in (
        build_prompt(task, bare=True),
        build_prompt(task, bare=True, revision=True, continuation=True),  # bare wins
    ):
        assert "Make the widget idempotent." in prompt
        assert "## Decisions" not in prompt
        assert "## Scope" not in prompt
        assert "## Acceptance" not in prompt
        assert "## Working rules" not in prompt
        assert ".torve/context/index.md" not in prompt
        assert ".torve/skills/" not in prompt


def test_prompt_extras_follow_the_charters_base_working_rules():
    """S-0029/D-1: extras append after the base rules — never before, and the
    base rules are present regardless."""
    prompt = build_prompt(
        Task(id="T-1", decisions=[]),
        prompt_extras="Docstrings and user-facing text follow the house voice.\n",
    )
    assert "Docstrings and user-facing text follow the house voice." in prompt
    assert prompt.index("`working-rules`") < prompt.index("house voice")
    # The base rules stay unaddressable: still present, unaltered.
    assert "The skills for your role are in system position" in prompt


def test_harness_agent_appends_the_tiers_prompt_extras(tmp_path, monkeypatch):
    tier = TierConfig(
        adapter="api",
        provider="anthropic",
        model="m",
        prompt_extras="Docstrings and user-facing text follow the house voice.\n",
    )
    ctx, agent = harness_ctx(
        tmp_path, tier.model_copy(update={"env": seam('cat "$TORVE_PROMPT"', monkeypatch)})
    )
    agent.run(ctx)
    prompt = (ctx.workspace / ".torve" / "tmp" / "prompt.md").read_text(encoding="utf-8")

    assert "Docstrings and user-facing text follow the house voice." in prompt
    assert prompt.index("`working-rules`") < prompt.index("house voice")


def test_the_working_rules_are_staged_for_system_position(tmp_path, monkeypatch):
    """S-0073/D-2: the rules reach the harness through the seam's own name,
    carrying the text every harness gets — the persona's extras with them,
    still after the base rules. Which channel puts them in system position is
    the image's (S-0063/D-1); what the engine owes is one file and one name."""

    tier = TierConfig(
        adapter="harness",
        provider="p",
        model="m",
        prompt_extras="Docstrings and user-facing text follow the house voice.\n",
    )
    ctx, agent = harness_ctx(
        tmp_path, tier.model_copy(update={"env": seam('cat "$TORVE_SYSTEM_PROMPT"', monkeypatch)})
    )
    result = agent.run(ctx)
    system = (ctx.workspace / ".torve" / "tmp" / "system.md").read_text(encoding="utf-8")

    assert "`working-rules`" in system
    assert system.index("`working-rules`") < system.index("house voice")
    # It arrived by the name the images read, not just onto disk.
    assert "`working-rules`" in result.output


def test_a_composed_prompt_stages_no_rules_of_this_engines(tmp_path, monkeypatch):
    """S-0073/D-2 beside S-0074/D-2: a prompt the runner composed is staged
    verbatim, so the file the seam names is emptied rather than left carrying
    the previous attempt's — a continuation worktree arrives with one."""

    tier = TierConfig(adapter="harness", provider="p", model="m")
    ctx, agent = harness_ctx(
        tmp_path, tier.model_copy(update={"env": seam("echo ran", monkeypatch)})
    )
    stale = ctx.workspace / ".torve" / "tmp" / "system.md"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("- the previous attempt's rules\n", encoding="utf-8")

    agent.run(dataclasses.replace(ctx, prompt="# Review\n\nSay what you think."))

    assert stale.read_text(encoding="utf-8") == ""


# ....................... #
# Dispatch (CLI): routing enforced before anything exists


def seeded_run_repo(tmp_path, tier: dict, providers_yaml="providers: {default: []}"):
    """A repository whose executor seat is reached through a harness written
    for the case (S-0061/D-2).

    The case still describes one tier; the helper splits it where the files
    now split — `adapter`, `api` and `image` into the manifest,
    `provider` and the rest onto the seat — so a case reads as it always did
    while the tree carries the three files.
    """

    import yaml

    from torve.config.agents import HARNESS_KEYS

    root = tmp_path / "repo"
    (root / ".torve" / "tasks" / "T-0042").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".torve" / "tasks" / "T-0042" / "contract.yaml").write_text(
        "schema_version: 1\nid: T-0042\ndecisions: []\n", encoding="utf-8"
    )
    harness(root)
    harness(
        root,
        "under-test",
        yaml.safe_dump({k: v for k, v in tier.items() if k in HARNESS_KEYS}, sort_keys=False),
    )
    seat = {"harness": "under-test", **{k: v for k, v in tier.items() if k not in HARNESS_KEYS}}
    (root / ".torve" / "config.yaml").write_text(
        "schema_version: 1\ntiers:\n  planner: {harness: fake}\n"
        "  reviewer: {harness: fake}\n"
        f"  executor: {yaml.safe_dump(seat, default_flow_style=True).strip()}\n"
        f"{providers_yaml}\n",
        encoding="utf-8",
    )
    return root


def test_run_refuses_an_unrouted_provider_with_exit_3(tmp_path):
    root = seeded_run_repo(
        tmp_path,
        {"adapter": "api", "image": "probe-sandbox", "provider": "anthropic", "api": ["openai"]},
    )
    result = CliRunner().invoke(app, ["run", "T-0042", "--root", str(root)])
    assert result.exit_code == 3
    assert "not permitted" in result.stderr


def test_run_refuses_a_missing_tier_with_exit_3(tmp_path):
    root = seeded_run_repo(tmp_path, {"adapter": "fake"})
    (root / ".torve" / "config.yaml").write_text(
        "schema_version: 1\ntiers:\n  planner: {harness: fake}\n", encoding="utf-8"
    )
    result = CliRunner().invoke(app, ["run", "T-0042", "--root", str(root)])
    assert result.exit_code == 3
    assert "no tier 'executor'" in result.stderr


def test_scenario_with_a_real_tier_is_refused(tmp_path):
    root = seeded_run_repo(
        tmp_path,
        {"adapter": "api", "image": "probe-sandbox", "provider": "anthropic"},
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
            "executor": TierConfig(adapter="api", provider="p"),
        }
    )
    assert config_hash(manifest, tmp_path, plain) != config_hash(manifest, tmp_path, tiered)
    assert config_hash(manifest, tmp_path, plain) == config_hash(manifest, tmp_path, plain)


def test_config_hash_separates_regimes_equipped_with_different_skills(tmp_path):
    """S-0029/measurement-deliberately-not-built: no new code measures equipment — the tiers dump
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
    equipped = RunnerConfig(tiers=tiers(TierConfig(prompt_extras="house voice")))

    assert config_hash(manifest, tmp_path, generalist) != config_hash(manifest, tmp_path, equipped)


# ....................... #
# The regime preimage (S-0004/D-19, A-72): config_hash writes its own parts


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
            "executor": TierConfig(adapter="api", provider="p"),
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
# Tier variants (S-0027/tier-variants, S-0027/D-3): dotted entries in `tiers`, an
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
    fast = TierConfig(adapter="api", provider="p", model="fast")
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
# retry_variant (S-0027/5-1a-the-attempt-ladder, S-0027/D-11): a rung to nowhere is a
# configuration error at load time, not a dispatch-time surprise.


def test_retry_variant_must_name_a_configured_tier():
    with pytest.raises(ValidationError, match="retry_variant names no configured tier"):
        RunnerConfig(tiers={"executor": TierConfig(retry_variant="executor.ghost")})


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
    # dated snapshot ids S-0004/D-6 wants recorded (found in the first live run).
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
    # leaves the count unreported (S-0004/D-6's self-reported regime).
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


def test_harness_agent_carries_reported_token_counts(tmp_path, monkeypatch):
    # T-0186: the counts parse_metadata reads off a usage block ride the
    # harness result — the runner stamps them onto the record's agent block.
    tier = TierConfig(
        adapter="api",
        provider="anthropic",
        model="m",
    )
    ctx, agent = harness_ctx(
        tmp_path,
        tier.model_copy(
            update={
                "env": seam(
                    'echo \'{"total_cost_usd": 0.5, "model": "m", '
                    '"usage": {"input_tokens": 10, "cache_read_input_tokens": 100, '
                    '"output_tokens": 5}}\'',
                    monkeypatch,
                )
            },
        ),
    )
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


def test_an_attempt_never_inherits_the_previous_attempt_s_counts():
    """T-0187: `run.meta` is one dict for the whole run, and
    `agent_token_counts` only ever adds the keys an adapter reported —
    "absent stays absent" holds inside an attempt, not across them. An
    attempt whose adapter reported nothing therefore showed the previous
    attempt's counts beside its own `cost_usd: null`. Unreported must read
    as unreported, so the run's block is cleared of them per attempt."""

    from torve.application.telemetry import TOKEN_FIELDS, agent_token_counts

    meta: dict = {"cost_usd": 0.4}
    meta.update(agent_token_counts(HarnessResult(exit_code=0, output="", input_tokens=10)))
    assert meta["input_tokens"] == 10

    # The next attempt reports nothing, which is what session.py now does
    # before restamping the block.
    for stale in (*TOKEN_FIELDS, "burn"):
        meta.pop(stale, None)

    meta.update(agent_token_counts(AgentResult(exit_code=0, output="")))
    assert "input_tokens" not in meta


def test_agent_token_counts_records_only_what_was_reported():
    from torve.application.telemetry import agent_token_counts

    assert agent_token_counts(
        HarnessResult(exit_code=0, output="", input_tokens=10, output_tokens=5)
    ) == {"input_tokens": 10, "output_tokens": 5}

    # A plain AgentResult carries no token fields — the block stays empty,
    # and the absent keys are omitted from the record, never zeroed (S-0004/D-6).
    assert agent_token_counts(AgentResult(exit_code=0, output="")) == {}


# ....................... #
# The burn profile (S-0039 phase 2): a sibling scanner of every JSON line,
# reading the durable store's own bytes — never the clipped exec output.

CLAUDE_STREAM = "\n".join(
    [
        '{"type":"system","subtype":"init","tools":["Bash"]}',
        (
            '{"type":"assistant","message":{"content":[{"type":"text"}],'
            '"usage":{"input_tokens":10,"cache_read_input_tokens":50,"output_tokens":120}}}'
        ),
        (
            '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"1"},'
            '{"type":"tool_use","id":"2"}],'
            '"usage":{"input_tokens":60,"output_tokens":9120}}}'
        ),
        (
            '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"1"},'
            '{"type":"tool_result","tool_use_id":"2"}]}}'
        ),
        (
            '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"3"}],'
            '"usage":{"output_tokens":7004}}}'
        ),
        "narration line — the dsh reporter emits these between events",
        "{not json at all",
        (
            '{"type":"assistant","message":{"content":[{"type":"text","text":"done"}],'
            '"usage":{"output_tokens":40}}}'
        ),
        (
            '{"type":"result","subtype":"success","total_cost_usd":0.9,'
            '"usage":{"input_tokens":99,"output_tokens":16284}}'
        ),
    ]
)


def burn_trace(tmp_path, text):
    trace = tmp_path / "T-9010.a1.trace.log"
    trace.write_text(text, encoding="utf-8")

    return trace


def test_parse_burn_scans_every_json_line_of_a_per_turn_stream(tmp_path):
    # turns counts the output-bearing turn events, tool_calls the calls the
    # stream carried (a tool_result answers a call, it is not one), and the
    # final result envelope — the run's totals — never reads as a turn.
    burn = parse_burn(burn_trace(tmp_path, CLAUDE_STREAM))

    assert burn is not None
    assert burn.turns == 4
    assert burn.tool_calls == 3
    assert burn.top_turns == (TurnBurn(2, 9120), TurnBurn(3, 7004), TurnBurn(1, 120))
    assert burn.as_block() == {
        "turns": 4,
        "tool_calls": 3,
        "top_turns": [
            {"turn": 2, "output_tokens": 9120},
            {"turn": 3, "output_tokens": 7004},
            {"turn": 1, "output_tokens": 120},
        ],
    }


def test_parse_burn_reads_the_other_harness_spellings(tmp_path):
    # camelCase per-turn usage (the dsh reporter's naming) and opencode's
    # step-finish part.tokens.output are the same cross-harness facts in
    # different clothes; a reasoning count is a breakdown, never a turn.
    stream = "\n".join(
        [
            '{"type":"turn","usage":{"inputTokens":7,"outputTokens":250,"reasoningTokens":20}}',
            (
                '{"type":"step_finish","part":{"type":"step-finish","cost":0.01,'
                '"tokens":{"input":5,"output":310,"reasoning":10}}}'
            ),
            '{"type":"tool","part":{"type":"tool","tool":"bash"}}',
        ]
    )
    burn = parse_burn(burn_trace(tmp_path, stream))

    assert burn is not None
    assert burn.turns == 2
    assert burn.tool_calls == 1
    assert burn.top_turns == (TurnBurn(2, 310), TurnBurn(1, 250))


def test_parse_burn_absent_for_an_envelope_only_output(tmp_path):
    # claude -p --output-format json: one envelope line, totals only — no
    # per-turn facts, so no block (S-0039/D-4's no-stream-no-block regime),
    # never a turns:1 read off the totals.
    envelope = (
        '{"type":"result","total_cost_usd":0.09,"usage":{"input_tokens":500,'
        '"output_tokens":88},"modelUsage":{"claude-sonnet-5":{}}}'
    )
    assert parse_metadata(envelope).output_tokens == 88  # the sibling still reads it
    assert parse_burn(burn_trace(tmp_path, envelope)) is None

    # ...and the same envelope with no type at all (the dsh reporter's shape)
    # is equally unprofiled: absence is recorded by the missing block.
    assert parse_burn(burn_trace(tmp_path, '{"usage":{"outputTokens":88}}')) is None


def test_parse_burn_absent_without_error_for_garbage_lines(tmp_path):
    # Garbage never raises and never zeroes: a stream torve cannot read stays
    # visibly unprofiled (S-0004/D-6), exactly like an absent file.
    garbage = 'plain text\n{\x7f broken\n[]\n5\n{}\n{"usage":{"output_tokens":"NaN"}}'
    assert parse_burn(burn_trace(tmp_path, garbage)) is None
    assert parse_burn(burn_trace(tmp_path, "")) is None
    assert parse_burn(tmp_path / "never-written.trace.log") is None


def test_agent_burn_carries_only_a_present_profile():
    from torve.application.telemetry import agent_burn

    # A plain AgentResult — and a harness result whose stream held no
    # per-turn facts — contribute no key at all (S-0004/D-6's absent regime).
    assert agent_burn(AgentResult(exit_code=0, output="")) == {}
    assert agent_burn(HarnessResult(exit_code=0, output="")) == {}

    profile = BurnProfile(turns=41, tool_calls=87, top_turns=(TurnBurn(12, 9120),))

    assert agent_burn(HarnessResult(exit_code=0, output="", burn=profile)) == {
        "burn": {
            "turns": 41,
            "tool_calls": 87,
            "top_turns": [{"turn": 12, "output_tokens": 9120}],
        }
    }


def test_harness_agent_derives_the_burn_from_the_captured_stream(tmp_path, monkeypatch):
    tier = TierConfig(adapter="harness", provider="p")
    ctx, agent = harness_ctx(
        tmp_path, tier.model_copy(update={"env": seam('cat "$TORVE_PROMPT"', monkeypatch)})
    )
    result = agent.run(dataclasses.replace(ctx, prompt=CLAUDE_STREAM))

    assert isinstance(result, HarnessResult)
    assert result.burn is not None
    assert result.burn.turns == 4
    assert result.burn.tool_calls == 3
    # The envelope's totals still ride the sibling parse unchanged.
    assert result.cost_usd == 0.9
    assert result.output_tokens == 16284
    # The store keeps the stream verbatim — same bytes, same relative ref.
    trace = tmp_path / ".torve" / "traces" / "T-9010.a1.trace.log"
    assert result.trace_ref == ".torve/traces/T-9010.a1.trace.log"
    assert trace.read_text(encoding="utf-8") == CLAUDE_STREAM
    # The raw capture is the adapter's transit, not a kept artifact.
    assert not (ctx.workspace / ".torve" / "tmp" / "harness-output.a1.raw").exists()


# ....................... #
# The per-request context curve (S-0075/D-1): the shape of one request's
# context over the stream — sibling of the burn scan, and sibling in the
# reading discipline (the store's own bytes, never a clipped exec string).


def test_parse_context_curve_reads_the_cached_shape(tmp_path):
    # claude's message usage names the cache fields: one request's context
    # is input + cache read + cache creation.
    stream = "\n".join(
        [
            '{"type":"system","subtype":"init","tools":["Bash"]}',
            (
                '{"type":"assistant","message":{"content":[{"type":"text"}],'
                '"usage":{"input_tokens":10,"cache_read_input_tokens":50,"output_tokens":120}}}'
            ),
            (
                '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"1"},'
                '{"type":"tool_use","id":"2"}],'
                '"usage":{"input_tokens":60,"output_tokens":9120}}}'
            ),
            (
                '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"3"}],'
                '"usage":{"input_tokens":100,"cache_creation_input_tokens":200,'
                '"output_tokens":7004}}}'
            ),
            (
                '{"type":"result","subtype":"success","total_cost_usd":0.9,'
                '"usage":{"input_tokens":170,"cache_read_input_tokens":50,'
                '"cache_creation_input_tokens":200,"output_tokens":16284}}'
            ),
        ]
    )
    curve = parse_context_curve(burn_trace(tmp_path, stream))

    assert curve is not None
    # The three requests carry 10+50, 60 and 100+200 — first, median, max,
    # sum — and the sum is checked against the receipt's own total
    # (170+50+200), which holds.
    assert curve.shape == "with-cache"
    assert curve.first == 60
    assert curve.median == 60
    assert curve.max == 300
    assert curve.sum == 420
    assert curve.requests == 3
    assert curve.receipt_total == 420
    assert curve.matches_receipt is True


def test_parse_context_curve_counts_requests_not_repeated_events(tmp_path):
    # One request emits several assistant events, each repeating the same
    # usage object (S-0075/D-5). The curve counts the message id, so the
    # reconstruction closes against the receipt; counting the events would
    # book 550 against a receipt of 300 and publish the attempt wrong.
    def event(message_id, text):
        return (
            f'{{"type":"assistant","message":{{"id":"{message_id}",'
            f'"content":[{{"type":"text","text":"{text}"}}],'
            '"usage":{"input_tokens":50,"cache_read_input_tokens":200,"output_tokens":7}}}'
        )

    stream = "\n".join(
        [
            event("msg_a", "one"),
            event("msg_a", "two"),
            event("msg_a", "three"),
            (
                '{"type":"assistant","message":{"id":"msg_b",'
                '"content":[{"type":"tool_use","id":"1"}],'
                '"usage":{"input_tokens":50,"output_tokens":9}}}'
            ),
            (
                '{"type":"result","subtype":"success","total_cost_usd":0.3,'
                '"usage":{"input_tokens":100,"cache_read_input_tokens":200,'
                '"output_tokens":16}}'
            ),
        ]
    )
    curve = parse_context_curve(burn_trace(tmp_path, stream))

    assert curve is not None
    assert curve.requests == 2
    assert curve.first == 250
    assert curve.median == 150.0
    assert curve.max == 250
    assert curve.sum == 300
    assert curve.receipt_total == 300
    assert curve.matches_receipt is True


def test_parse_context_curve_reconstructs_the_input_only_shape(tmp_path):
    # deepseek/qwen: message usage carries input alone and the receipt's
    # per-request list stays empty. The reconstruction names its shape, and
    # the check against the receipt — which knows the cache total the
    # messages do not — fails, so the attempt reports itself unmeasured
    # instead of trusting either side.
    stream = "\n".join(
        [
            '{"type":"turn","usage":{"inputTokens":7,"outputTokens":250,"reasoningTokens":20}}',
            '{"type":"turn","usage":{"inputTokens":3,"outputTokens":40}}',
            (
                '{"total_cost_usd":0.05,"usage":{"inputTokens":10,"cacheReadTokens":900,'
                '"outputTokens":290}}'
            ),
        ]
    )
    curve = parse_context_curve(burn_trace(tmp_path, stream))

    assert curve is not None
    assert curve.shape == "input-only"
    assert curve.first == 7
    assert curve.median == 5.0
    assert curve.max == 7
    assert curve.sum == 10
    assert curve.requests == 2
    assert curve.receipt_total == 910
    assert curve.matches_receipt is False


def test_parse_context_curve_reads_opencodes_part_tokens_input(tmp_path):
    # opencode's step-finish part spells the same fact as `tokens.input`; a
    # stream whose last line is a turn names no envelope, so no receipt.
    stream = "\n".join(
        [
            (
                '{"type":"step_finish","part":{"type":"step-finish","cost":0.01,'
                '"tokens":{"input":5,"output":310,"reasoning":10}}}'
            ),
            (
                '{"type":"step_finish","part":{"type":"step-finish","cost":0.02,'
                '"tokens":{"input":55,"output":20}}}'
            ),
        ]
    )
    curve = parse_context_curve(burn_trace(tmp_path, stream))

    assert curve is not None
    assert curve.shape == "input-only"
    assert curve.first == 5
    assert curve.median == 30.0
    assert curve.max == 55
    assert curve.sum == 60
    assert curve.receipt_total is None
    assert curve.matches_receipt is None


def test_parse_context_curve_absent_for_an_envelope_only_output(tmp_path):
    # One envelope, no per-request usage: the burn's no-stream-no-block
    # regime holds for the curve too — there is nothing to reconstruct, and
    # the row says so via the `none` shape the adapter books.
    envelope = (
        '{"type":"result","total_cost_usd":0.09,"usage":{"input_tokens":500,'
        '"output_tokens":88},"modelUsage":{"claude-sonnet-5":{}}}'
    )

    assert parse_context_curve(burn_trace(tmp_path, envelope)) is None
    assert parse_context_curve(burn_trace(tmp_path, "plain text\n{}\n[]")) is None
    assert parse_context_curve(burn_trace(tmp_path, "")) is None
    assert parse_context_curve(tmp_path / "never-written.trace.log") is None


def test_harness_agent_derives_the_context_curve_from_the_captured_stream(tmp_path, monkeypatch):
    tier = TierConfig(adapter="harness", provider="p")
    ctx, agent = harness_ctx(
        tmp_path, tier.model_copy(update={"env": seam('cat "$TORVE_PROMPT"', monkeypatch)})
    )
    result = agent.run(dataclasses.replace(ctx, prompt=CLAUDE_STREAM))

    assert isinstance(result, HarnessResult)
    assert result.context is not None
    # The two assistant turns that name an input context carry 10+50 and 60;
    # the envelope names the receipt total (99), which the reconstruction
    # does not reach (its cache total is absent here).
    assert result.context.as_block() == {
        "shape": "with-cache",
        "first": 60,
        "median": 60.0,
        "max": 60,
        "sum": 120,
        "requests": 2,
        "receipt_total": 99,
        "matches_receipt": False,
    }


# ....................... #
# The burn classifier on a real attempt (S-0075/D-2 phase 3): the adapter
# scans the trace it already writes for the per-call facts the classifier
# reads, and books the classified profile by the context curve's route.


def profile_stream(workdir):
    """A claude stream-json session that exercises every part of the scan: an
    opening inventory line, calls across three messages, the results that
    answer them, a compaction event, and a closing envelope. The read and edit
    name the in-sandbox absolute path a harness actually logs, which is what
    the scan has to relativise before a scope glob can match it.

    The second message issues two calls and spends three lines doing it —
    text, then one tool_use, then the other, all carrying one `id`. That is
    the shape claude 2.1.x actually emits, and a fixture that packed the
    blocks into one line is why a scan counting lines instead of ids passed
    its own test while reporting 1.000 calls per message on every attempt."""

    return "\n".join(
        [
            (
                '{"type":"system","subtype":"init","tools":["Bash","Read"],'
                '"skills":["working-rules"],"mcp_servers":[],"agents":["Explore"]}'
            ),
            (
                '{"type":"assistant","message":{"id":"msg_a","content":['
                '{"type":"tool_use","id":"1",'
                '"name":"Glob","input":{"pattern":"**/*.py"}}],'
                '"usage":{"input_tokens":10,"output_tokens":20}}}'
            ),
            (
                '{"type":"user","message":{"content":[{"type":"tool_result",'
                '"tool_use_id":"1","content":"a.py\\nb.py"}]}}'
            ),
            (
                '{"type":"assistant","message":{"id":"msg_b",'
                '"content":[{"type":"text","text":"reading the widget"}],'
                '"usage":{"input_tokens":60,"output_tokens":40}}}'
            ),
            (
                '{"type":"assistant","message":{"id":"msg_b","content":['
                '{"type":"tool_use","id":"2",'
                f'"name":"Read","input":{{"file_path":"{workdir}/src/widget.py"}}}}],'
                '"usage":{"input_tokens":60,"output_tokens":40}}}'
            ),
            (
                '{"type":"assistant","message":{"id":"msg_b","content":['
                '{"type":"tool_use","id":"3","name":"Edit",'
                f'"input":{{"file_path":"{workdir}/src/widget.py"}}}}],'
                '"usage":{"input_tokens":60,"output_tokens":40}}}'
            ),
            (
                '{"type":"user","message":{"content":[{"type":"tool_result",'
                '"tool_use_id":"2","content":"...","duration_ms":30},'
                '{"type":"tool_result","tool_use_id":"3","content":"ok",'
                '"duration_ms":70}]}}'
            ),
            '{"type":"system","subtype":"compact_boundary"}',
            (
                '{"type":"assistant","message":{"id":"msg_c","content":['
                '{"type":"tool_use","id":"4",'
                '"name":"Bash","input":{"command":"pytest -q"}}],'
                '"usage":{"input_tokens":90,"output_tokens":15}}}'
            ),
            (
                '{"type":"user","message":{"content":[{"type":"tool_result",'
                '"tool_use_id":"4","content":"2 passed","duration_ms":8100}]}}'
            ),
            (
                '{"type":"result","subtype":"success","total_cost_usd":0.3,'
                '"usage":{"input_tokens":99,"output_tokens":100}}'
            ),
        ]
    )


def test_parse_tool_calls_emits_the_facts_the_classifier_reads(tmp_path):
    facts = parse_tool_calls(burn_trace(tmp_path, profile_stream("/w")), "/w")

    # The inventory line first, keyed as the classifier counts it; the empty
    # mcp list rides along and is counted as nothing rather than as zero.
    # Then one fact per call, carrying the message that issued it, the bytes
    # its result returned and the latency the result measured — and the
    # compaction event in the position the stream put it.
    assert facts == [
        {
            "name": "init",
            "tools": ["Bash", "Read"],
            "skills": ["working-rules"],
            "mcp_servers": [],
            "agents": ["Explore"],
        },
        {"name": "Glob", "input": {"pattern": "**/*.py"}, "message": 0, "bytes": 9},
        {
            "name": "Read",
            "input": {"file_path": "src/widget.py"},
            "message": 1,
            "bytes": 3,
            "latency_ms": 30,
        },
        {
            "name": "Edit",
            "input": {"file_path": "src/widget.py"},
            "message": 1,
            "bytes": 2,
            "latency_ms": 70,
        },
        # The boundary line carries no id and bears no turn, so it belongs to
        # the message it followed rather than claiming one of its own.
        {"name": "SessionStart:compact", "message": 1},
        {
            "name": "Bash",
            "input": {"command": "pytest -q"},
            "message": 2,
            "bytes": 8,
            "latency_ms": 8100,
        },
    ]
    # Two of the four calls rode one message, which is the whole point: the
    # scan joins on the id, so three lines spending one turn are one message
    # and `calls_per_message` can say something other than 1.000.
    assert [fact["message"] for fact in facts if fact.get("input")] == [0, 1, 1, 2]
    # The scan and the harness's own per-turn count never disagree about what
    # a call is: a tool_result answers a call, it is not one.
    assert (
        len([fact for fact in facts if fact["name"] not in ("init", "SessionStart:compact")]) == 4
    )


def test_parse_tool_calls_leaves_a_path_outside_the_workspace_verbatim(tmp_path):
    stream = (
        '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"1",'
        '"name":"Read","input":{"file_path":"/etc/hosts"}}],"usage":{"output_tokens":1}}}'
    )
    facts = parse_tool_calls(burn_trace(tmp_path, stream), "/w")

    # Not a path the scope can name, so nothing is rewritten; and with no
    # workdir at all the spelling is the stream's own.
    assert facts == [{"name": "Read", "input": {"file_path": "/etc/hosts"}, "message": 0}]
    assert parse_tool_calls(burn_trace(tmp_path, stream))[0]["input"] == {"file_path": "/etc/hosts"}


def test_parse_tool_calls_empty_for_a_stream_that_names_none(tmp_path):
    # No stream, no block (S-0039/D-4): an envelope, garbage and a file
    # retention already took all answer the same, and none of them raise.
    envelope = '{"type":"result","total_cost_usd":0.09,"usage":{"output_tokens":88}}'

    assert parse_tool_calls(burn_trace(tmp_path, envelope)) == []
    assert parse_tool_calls(burn_trace(tmp_path, "plain text\n{}\n[]")) == []
    assert parse_tool_calls(burn_trace(tmp_path, "")) == []
    assert parse_tool_calls(tmp_path / "never-written.trace.log") == []


def test_harness_agent_books_the_classified_profile_for_the_attempt(tmp_path, monkeypatch):
    from torve.application.telemetry import build_attempt_row

    tier = TierConfig(adapter="harness", provider="p")
    ctx, agent = harness_ctx(
        tmp_path, tier.model_copy(update={"env": seam('cat "$TORVE_PROMPT"', monkeypatch)})
    )
    agent.run(dataclasses.replace(ctx, prompt=profile_stream(ctx.workdir)))

    row = build_attempt_row(ctx.task, {}, verdict="agent_error", exit_code=1, timed_out=False)
    profile = row["agent"]["burn"]["profile"]

    # The task's scope (`src/**`) is what tells the in-scope read from the
    # orientation glob; the edit, the test run and the compaction event are
    # the stream's own, named by the engine's vocabulary.
    assert profile["classes"] == {
        "edit": 1,
        "in_scope_read": 1,
        "orientation": 1,
        "test_run": 1,
    }
    assert profile["calls"] == 4
    assert profile["messages"] == 3
    assert profile["calls_before_first_edit"] == 2
    assert profile["compaction_events"] == 1
    assert profile["bytes_by_class"] == {
        "orientation": 9,
        "in_scope_read": 3,
        "edit": 2,
        "test_run": 8,
    }
    assert profile["latency_medians"] == {"in_scope_read": 30, "edit": 70, "test_run": 8100}
    # What every request re-read, off the same line (S-0075/D-4); the empty
    # mcp list stays absent rather than becoming a zero.
    assert profile["init_tools"] == 2
    assert profile["init_skills"] == 1
    assert profile["init_agents"] == 1
    assert "init_mcp_servers" not in profile

    # The booking belongs to the attempt that produced it, drained once.
    again = build_attempt_row(ctx.task, {}, verdict="agent_error", exit_code=1, timed_out=False)

    assert "burn" not in again["agent"]


def test_harness_agent_books_no_profile_for_a_callless_stream(tmp_path, monkeypatch):
    from torve.application.telemetry import build_attempt_row

    tier = TierConfig(adapter="harness", provider="p")
    ctx, agent = harness_ctx(
        tmp_path, tier.model_copy(update={"env": seam('cat "$TORVE_PROMPT"', monkeypatch)})
    )
    # CLAUDE_STREAM's tool_use blocks name no tool, so the stream carries no
    # classifiable call — the row says so by silence, never by an empty block.
    agent.run(dataclasses.replace(ctx, prompt=CLAUDE_STREAM))

    row = build_attempt_row(ctx.task, {}, verdict="agent_error", exit_code=1, timed_out=False)

    assert "burn" not in row["agent"]


class SandboxReadOnlyRuntime(HostShellRuntime):
    """A drafting run's mount (S-0005/D-2, S-0020/D-2): the host writes the prompt
    into the worktree, and the sandbox sees the same tree read-only. Only
    the exec side is denied, which is what the real asymmetry looks like."""

    def exec(self, handle, command, timeout_s):
        staging = pathlib.Path(self.workspace) / ".torve" / "tmp"
        staging.chmod(0o555)

        try:
            return super().exec(handle, command, timeout_s)

        finally:
            staging.chmod(0o755)

    def sync_out(self, handle, destination):
        # Nothing to bring back: the tree is bound, and the capture the
        # adapter looks for was never created.
        return None


def test_a_read_only_workspace_still_runs_the_command(tmp_path, monkeypatch):
    """The raw capture path lives inside the workspace and the redirect was
    unconditional, so on a drafting run it failed before the command ran:
    `torve intake` returned three empty attempts and escalated `drafter
    output unparseable`, which is also what a model returning nothing looks
    like. The probe runs in a subshell because a failed redirect on `:`, a
    POSIX special builtin, exits the whole shell rather than returning
    non-zero — taking the fallback with it."""

    tier = TierConfig(adapter="harness", provider="p")
    ctx, agent = harness_ctx(
        tmp_path, tier.model_copy(update={"env": seam('cat "$TORVE_PROMPT"', monkeypatch)})
    )
    denied = SandboxReadOnlyRuntime(ctx.workspace)

    result = agent.run(dataclasses.replace(ctx, prompt="the drafter's answer", runtime=denied))

    # The command ran and its output stands, clipped as it was before the
    # capture existed — not an empty attempt reported as unparseable.
    assert result.exit_code == 0
    assert "the drafter's answer" in result.output
    assert not (ctx.workspace / ".torve" / "tmp" / "harness-output.a1.raw").exists()


class ClippingRuntime(HostShellRuntime):
    """The exec boundary every real runtime enforces: the output string the
    adapter holds comes back clipped (src/torve/base/shell.py's OUTPUT_LIMIT),
    whatever the process itself wrote."""

    def exec(self, handle, command, timeout_s):
        result = super().exec(handle, command, timeout_s)

        return dataclasses.replace(result, output=truncate(result.output))


def test_burn_counts_the_full_stream_a_clipped_result_output_cannot_see(tmp_path, monkeypatch):
    # T-0271's blocker, pinned: the heaviest turn sits inside the clip's
    # hole. Scanning result.output would silently drop it — so the scanner
    # reads the store's full bytes, and the store's file holds them.
    def turn(n: int) -> str:
        return f'{{"type":"assistant","message":{{"usage":{{"output_tokens":{n}}}}}}}'

    def filler(tag: str, n: int) -> str:
        return "\n".join(f"narration line {tag}-{j} — padded padding padding" for j in range(n))

    stream = "\n".join(
        [
            turn(5),
            filler("a", 120),
            turn(20000),  # inside the clip's hole: gone from the exec string
            filler("b", 200),
            turn(9120),
            turn(7004),
            '{"type":"result","total_cost_usd":1.0,"usage":{"output_tokens":36129}}',
        ]
    )
    assert len(stream) > 8000  # and the tail's preserved 6000 chars start far after it

    tier = TierConfig(adapter="harness", provider="p")
    ctx, agent = harness_ctx(
        tmp_path, tier.model_copy(update={"env": seam('cat "$TORVE_PROMPT"', monkeypatch)})
    )
    clipped = ClippingRuntime(ctx.workspace)
    result = agent.run(dataclasses.replace(ctx, prompt=stream, runtime=clipped))

    assert "truncated" in result.output  # the in-memory string is clipped...
    assert "20000" not in result.output  # ...its heaviest turn is gone from it
    assert result.burn is not None
    assert result.burn.turns == 4  # ...but the profile counted the whole stream.
    assert result.burn.top_turns == (TurnBurn(2, 20000), TurnBurn(3, 9120), TurnBurn(4, 7004))
    trace = tmp_path / ".torve" / "traces" / "T-9010.a1.trace.log"
    assert trace.read_text(encoding="utf-8") == stream  # the store holds full bytes
    # The envelope still rides the metadata parse from the clipped tail.
    assert result.cost_usd == 1.0
    assert result.output_tokens == 36129


def test_harness_capture_keeps_the_commands_own_exit_code(tmp_path, monkeypatch):
    # The wrapper moves bytes without touching the verdict the loop reads.
    tier = TierConfig(adapter="harness", provider="p")
    ctx, agent = harness_ctx(
        tmp_path,
        tier.model_copy(
            update={
                "env": seam(
                    'echo \'{"type":"turn","usage":{"outputTokens":5}}\'; exit 3', monkeypatch
                )
            },
        ),
    )
    result = agent.run(ctx)

    assert result.exit_code == 3
    assert result.burn is not None
    assert result.burn.turns == 1
    trace = tmp_path / ".torve" / "traces" / "T-9010.a1.trace.log"
    assert trace.read_text(encoding="utf-8").startswith('{"type":"turn"')


def test_harness_without_a_stream_profile_yields_no_block(tmp_path, monkeypatch):
    # Envelope-only output through the whole capture path: cost rides,
    # burn is absent — not zeroed (S-0039/D-4), and the trace stays verbatim.
    tier = TierConfig(adapter="harness", provider="p")
    ctx, agent = harness_ctx(
        tmp_path,
        tier.model_copy(
            update={
                "env": seam(
                    'echo \'{"total_cost_usd": 0.5, "usage": {"output_tokens": 88}}\'', monkeypatch
                )
            },
        ),
    )
    result = agent.run(ctx)

    assert isinstance(result, HarnessResult)
    assert result.burn is None
    assert result.cost_usd == 0.5
    assert result.output_tokens == 88
    trace = tmp_path / ".torve" / "traces" / "T-9010.a1.trace.log"
    assert "total_cost_usd" in trace.read_text(encoding="utf-8")


def test_attempt_record_carries_reported_token_counts(tmp_path):
    """T-0186 end to end: the token counts an adapter reports ride the agent
    block of the attempt record, and a silent adapter leaves the keys absent
    — absent stays absent, never zeroed (S-0004/D-6's self-reported regime). The
    burn profile rides the same block beside the totals (S-0039/the-burn-profile),
    with the same absence discipline."""
    import asyncio
    import subprocess

    from torve.application.dispatch import RunDeps
    from torve.application.runner import drive_attempts, real_hooks
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
                burn=BurnProfile(
                    turns=41,
                    tool_calls=87,
                    top_turns=(TurnBurn(12, 9120), TurnBurn(33, 7004)),
                ),
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
                "executor": TierConfig(adapter="harness", provider="p", model="m"),
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
    # S-0039/the-burn-profile: the burn profile rides the block beside the totals.
    assert agent_block["burn"] == {
        "turns": 41,
        "tool_calls": 87,
        "top_turns": [
            {"turn": 12, "output_tokens": 9120},
            {"turn": 33, "output_tokens": 7004},
        ],
    }

    silent_block = run_once(tmp_path / "silent", SilentAgent())
    assert silent_block["cost_usd"] is None
    for key in ("input_tokens", "cache_read_tokens", "cache_creation_tokens", "output_tokens"):
        assert key not in silent_block
    # No stream, no block: absence stays visible, never a zeros-shaped lie.
    assert "burn" not in silent_block


def test_review_record_carries_reported_token_counts(repo, monkeypatch):
    """T-0186: the reviewer's token counts survive the base-shape rebuild in
    run_review and ride the review record's agent block — only the reported
    ones, absent keys omitted (S-0004/D-6)."""
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
    from torve.application.dispatch import RunDeps
    from torve.application.runner import run_task
    from torve.domain.states import TaskState

    repo.seed()

    def scripted_gates(*args, **kwargs):
        return 0, "scripted", "cafecafe1234", [], "diff --git a/x b/x"

    monkeypatch.setattr(run_module, "run_gate_pass", scripted_gates)

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


def test_the_working_rules_are_named_and_not_restated():
    """S-0073/D-1: the section points at the `working-rules` skill and says
    nothing the skill says. Asserted as that property rather than as the
    words, so a bullet inlined back beside the skill fails here instead of
    passing quietly next to it."""

    from torve.adapters.agent.harness import build_prompt
    from torve.domain.task import Task

    section = build_prompt(Task(id="T-1", decisions=[])).split("## Working rules", 1)[1]
    bullets = [line for line in section.splitlines() if line.startswith("- ")]

    assert len(bullets) == 1
    assert "`working-rules`" in bullets[0]
    assert ".torve/skills/" in bullets[0]


def test_the_reading_advice_names_the_shell_forms_beside_the_readers():
    """S-0076/D-4: measured across 21 retained traces, 237 of the corpus's file
    reads are `cat` and `sed` against 185 distinct paths the reader touched at
    all — so advice shaped around the reader's own range governs the smaller
    half, and the shell forms are named beside it.

    Its own section: the rules section still names the `working-rules` skill
    and restates nothing of it (S-0073/D-1), because what a harness does to an
    oversized result is a fact about this attempt rather than how work is done
    here."""

    from torve.adapters.agent.harness import build_prompt
    from torve.domain.task import Task

    prompt = build_prompt(Task(id="T-1", decisions=[]))
    advice = prompt.split("## Reading", 1)[1].split("## Working rules", 1)[0]

    # The reader's own form, and the shell forms that do the same work.
    assert "offset" in advice and "limit" in advice

    for form in ("`sed", "`rg", "`head`", "`tail`", "`cat`"):
        assert form in advice, f"the advice does not name {form}"

    # The rules section is untouched by it: one bullet, still the skill's.
    rules = prompt.split("## Working rules", 1)[1]

    assert len([line for line in rules.splitlines() if line.startswith("- ")]) == 1

    # The base arm carries the intent and nothing else (S-0074/D-2), advice
    # included: what an arm removed is a property of the prompt.
    assert "## Reading" not in build_prompt(Task(id="T-2", decisions=[]), bare=True)


def test_one_tool_result_is_capped_where_every_tool_crosses_the_boundary():
    """S-0076/D-4: the cap is set at the harness's own result boundary rather
    than asked for in the prompt, so the context's growth term stops being set
    by whichever command dumped the most, whichever tool ran it.

    Asserted as the property — one cap, in each knob's own unit, under the
    default it tightens — rather than as two literals, so raising it in one
    place and forgetting the other fails here."""

    from torve.config.agents import load_harness

    env = load_harness(pathlib.Path("."), "claude-subscription").env
    chars, tokens = int(env["BASH_MAX_OUTPUT_LENGTH"]), int(env["MAX_MCP_OUTPUT_TOKENS"])

    # claude's own defaults are 30,000 characters and 25,000 tokens: a cap at
    # or above either is not a cap.
    assert chars < 30_000
    assert tokens < 25_000
    # One cap in two units, at four bytes to the token.
    assert chars == tokens * 4
