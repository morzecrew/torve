"""The runner configuration: the seat's own fields, the retry rungs, the
clocks and the broker wiring. What a seat *names* — its harness and its
profile — is `tests/test_agents.py` (S-0061/D-3); this file is what the
resolved `TierConfig` does once they are merged onto it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import harness
from pydantic import ValidationError

from torve.application.telemetry import config_hash
from torve.config import layout
from torve.config.runconfig import (
    ROLE_SKILLS,
    BrokerConfig,
    RunnerConfig,
    TierConfig,
    TracesConfig,
    agent_timeout_for,
    load_runner_config,
    remote_broker_proxy,
    resolve_character_tier,
    sandbox_timeout_for,
)
from torve.domain.task import Task

# ----------------------- #


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def load(tmp_path: Path, text: str) -> RunnerConfig:
    """The two harnesses these fixtures name, beside the configuration that
    names them (S-0061/D-2) — a seat is reached through one now."""

    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    harness(root)
    harness(root, "heavy", "adapter: api\nimage: probe-sandbox\n")
    config_path = write(root / "config.yaml", text)
    return load_runner_config(root, config_path)


def manifest(tmp_path: Path) -> Path:
    return write(tmp_path / "gates.yaml", "schema_version: 1\ngates: []\n")


# ....................... #
# retry_variants (S-0034/D-6, S-0034/D-7): the axis-keyed mapping, the scalar kept
# as its functional sugar, and one resolution every reader shares.


def test_retry_variants_load_axes_from_yaml_and_resolve_through_one_map(tmp_path: Path):
    config = load(
        tmp_path,
        "schema_version: 1\n"
        "tiers:\n"
        "  executor:\n"
        "    harness: fake\n"
        "    retry_variants: {functional: executor.heavy, compliance: executor}\n"
        "  executor.heavy: {harness: heavy, provider: heavy, model: h}\n",
    )

    assert config.tiers["executor"].resolved_retry_variants() == {
        "functional": "executor.heavy",
        "compliance": "executor",
    }


def test_the_scalar_retry_variant_reads_as_functional_sugar():
    tier = TierConfig(retry_variant="executor.heavy")
    assert tier.resolved_retry_variants() == {"functional": "executor.heavy"}


def test_the_scalar_and_the_mapping_merge_when_they_name_one_rung_together():
    tier = TierConfig(
        retry_variants={"compliance": "executor"},
        retry_variant="executor.heavy",
    )
    assert tier.resolved_retry_variants() == {
        "functional": "executor.heavy",
        "compliance": "executor",
    }


def test_the_functional_rung_spelled_two_ways_differently_is_refused():
    with pytest.raises(ValidationError, match="name different tiers for the same axis"):
        TierConfig(
            retry_variant="executor.heavy",
            retry_variants={"functional": "executor.other"},
        )


def test_the_boundary_axis_may_name_no_rung():
    with pytest.raises(ValidationError, match="boundary conviction resolves no retry rung"):
        TierConfig(retry_variants={"boundary": "executor.heavy"})


def test_an_empty_rung_in_the_mapping_is_refused():
    with pytest.raises(ValidationError, match="must name a tier"):
        TierConfig(retry_variants={"form": ""})


def test_an_unknown_axis_key_is_refused_by_the_vocabulary():
    with pytest.raises(ValidationError):
        TierConfig(retry_variants={"performance": "executor.heavy"})  # type: ignore[dict-item]


def test_a_rung_named_on_any_axis_must_be_a_configured_tier():
    with pytest.raises(ValidationError, match="retry_variant names no configured tier"):
        RunnerConfig(
            tiers={
                "executor": TierConfig(
                    retry_variants={"compliance": "executor.ghost"},
                )
            }
        )


def test_retry_variants_change_the_regime_digest(tmp_path: Path):
    base = {"planner": TierConfig(), "reviewer": TierConfig(), "executor": TierConfig()}
    plain = RunnerConfig(tiers=base)
    routed = RunnerConfig(
        tiers={**base, "executor": TierConfig(retry_variants={"compliance": "executor"})}
    )
    assert config_hash(manifest(tmp_path), tmp_path, plain) != config_hash(
        manifest(tmp_path), tmp_path, routed
    )


def test_blocker_revisions_defaults_to_one_and_parses_from_yaml(tmp_path: Path):
    # S-0043 S-0043/D-1/D-43.3: the knob defaults to 1 in-run revision, and a
    # YAML override rides the same `review:` mapping as `on`/`feedback_from`.
    config = load(
        tmp_path, "\n".join(["review:", '  "on": [task_gated]', "  blocker_revisions: 3"])
    )
    assert config.review.blocker_revisions == 3
    assert RunnerConfig().review.blocker_revisions == 1


# ....................... #
# The tier clock (S-0035/D-6): a named override wins, absence falls to the global


def test_a_tier_clock_overrides_the_runtime_global(tmp_path: Path):
    config = load(
        tmp_path,
        "runtime:\n  agent_timeout: 1200\n  sandbox_timeout: 1800\n"
        "tiers:\n  executor:\n    harness: fake\n    agent_timeout: 3600\n    sandbox_timeout: 4200\n",
    )
    tier = config.tiers["executor"]

    assert agent_timeout_for(config, tier) == 3600
    assert sandbox_timeout_for(config, tier) == 4200


def test_an_absent_tier_clock_falls_through_to_the_runtime_global(tmp_path: Path):
    config = load(tmp_path, "runtime:\n  agent_timeout: 999\n  sandbox_timeout: 1500\n")
    tier = config.tiers["executor"]

    assert tier.agent_timeout is None
    assert tier.sandbox_timeout is None
    assert agent_timeout_for(config, tier) == 999
    assert sandbox_timeout_for(config, tier) == 1500


def test_the_two_clocks_resolve_independently(tmp_path: Path):
    """Naming only the agent clock leaves the sandbox bound at the global —
    the heavy rung wants a longer attempt inside an unchanged platform
    ceiling just as easily as both raised."""

    config = load(tmp_path, "tiers:\n  executor:\n    harness: fake\n    agent_timeout: 3600\n")
    tier = config.tiers["executor"]

    assert agent_timeout_for(config, tier) == 3600
    assert sandbox_timeout_for(config, tier) == 1800  # the runtime default


def test_an_explicit_zero_is_named_not_absent(tmp_path: Path):
    """`is None`, not truthiness: a tier that writes `0` wins with `0`. The
    fall-through is for absence, and a written value is never quietly
    discarded — the same key-presence rule the profile merge pins."""

    config = load(tmp_path, "tiers:\n  executor:\n    harness: fake\n    agent_timeout: 0\n")
    tier = config.tiers["executor"]

    assert agent_timeout_for(config, tier) == 0
    assert sandbox_timeout_for(config, tier) == 1800


# ....................... #
# The derived-cache volume (S-0035/the-derived-cache-volume, S-0035/D-4)


def test_an_unnamed_cache_volume_is_cold_by_default():
    assert TierConfig().cache_volume == ""


def test_cache_volume_loads_from_the_tier_mapping(tmp_path: Path):
    config = load(
        tmp_path,
        "tiers:\n  executor:\n    harness: fake\n    cache_volume: torve-cache\n  planner: {harness: fake}\n",
    )

    assert config.tiers["executor"].cache_volume == "torve-cache"
    assert config.tiers["planner"].cache_volume == ""  # opted-in per tier, not globally


def test_a_warm_regime_is_a_different_regime_digest(tmp_path: Path):
    # The tier dump `config_hash` already digests carries `cache_volume` for
    # free — warm and cold arms of one campaign are separable populations,
    # even though the only difference a run may see is wall clock (S-0035/D-1).
    cold = RunnerConfig()
    warm = RunnerConfig(tiers={**cold.tiers, "executor": TierConfig(cache_volume="torve-cache")})
    gate = manifest(tmp_path)

    assert config_hash(gate, tmp_path, cold) != config_hash(gate, tmp_path, warm)


# ....................... #
# character_routing (S-0034 S-0034/D-3)


def test_character_routing_names_no_configured_tier_is_a_load_time_refusal():
    with pytest.raises(ValidationError, match="character_routing names no configured tier"):
        RunnerConfig(
            tiers={
                "executor": TierConfig(character_routing={"structural": "indexed"}),
                "reviewer": TierConfig(),
            }
        )


def test_character_routing_may_name_a_configured_variant():
    config = RunnerConfig(
        tiers={
            "executor": TierConfig(character_routing={"structural": "indexed"}),
            "executor.indexed": TierConfig(),
            "reviewer": TierConfig(),
        }
    )
    assert config.tiers["executor"].character_routing == {"structural": "indexed"}


def _executor_task(**overrides) -> Task:
    fields: dict = {"id": "T-1", "decisions": []}
    fields.update(overrides)
    return Task(**fields)


def test_resolve_character_tier_maps_a_declared_character_to_its_variant():
    config = RunnerConfig(
        tiers={
            "executor": TierConfig(character_routing={"structural": "indexed"}),
            "executor.indexed": TierConfig(),
            "reviewer": TierConfig(),
        }
    )
    resolved = resolve_character_tier(config, _executor_task(character="structural"))
    assert resolved.tier_variant == "indexed"


def test_resolve_character_tier_leaves_an_unmapped_character_on_the_seat_default():
    config = RunnerConfig(
        tiers={
            "executor": TierConfig(character_routing={"structural": "indexed"}),
            "executor.indexed": TierConfig(),
            "reviewer": TierConfig(),
        }
    )
    resolved = resolve_character_tier(config, _executor_task(character="routine"))
    assert resolved.tier_variant is None


def test_resolve_character_tier_leaves_a_task_with_no_character_alone():
    config = RunnerConfig(
        tiers={
            "executor": TierConfig(character_routing={"structural": "indexed"}),
            "executor.indexed": TierConfig(),
            "reviewer": TierConfig(),
        }
    )
    task = _executor_task()
    assert resolve_character_tier(config, task) is task


def test_an_explicit_tier_variant_wins_over_a_mapped_character():
    """S-0034/D-3: explicit tier_variant always wins over character routing."""

    config = RunnerConfig(
        tiers={
            "executor": TierConfig(character_routing={"structural": "indexed"}),
            "executor.indexed": TierConfig(),
            "executor.pinned": TierConfig(),
            "reviewer": TierConfig(),
        }
    )
    task = _executor_task(character="structural", tier_variant="pinned")
    resolved = resolve_character_tier(config, task)
    assert resolved.tier_variant == "pinned"


# ....................... #
# The trace store's retention block: `traces.keep_days` and `traces.max_mb`,
# enforced by the reaper's pass, defaulting to the drafting values.


def test_traces_retention_defaults():
    config = RunnerConfig()
    assert config.traces.keep_days == 30
    assert config.traces.max_mb == 512


def test_traces_block_loads_from_yaml(tmp_path: Path):
    config = load(tmp_path, "traces:\n  keep_days: 7\n  max_mb: 64\n")
    assert config.traces == TracesConfig(keep_days=7, max_mb=64)


def test_traces_block_rejects_unknown_keys(tmp_path: Path):
    # S-0013/D-5 again: a typo under traces must not silently drop a knob.
    with pytest.raises(ValidationError):
        load(tmp_path, "traces:\n  keep_weeks: 4\n")


def test_traces_bounds_refuse_non_numbers():
    # Keep days and max megabytes: the pass enforces numbers, and a bound
    # that cannot be read as one is a refusal at load, never a surprise
    # at sweep time.
    with pytest.raises(ValidationError):
        TracesConfig(keep_days="a month")  # type: ignore[arg-type]


# ....................... #
# Remote endpoint mode (S-0041/the-broker-reachable): broker.bind and broker.advertise.
# The advertised address is resolved once at load and published on the
# opensandbox config — the runtime composes the sandbox's proxy env from
# it with no channel to the broker, replacing the Docker-gateway
# derivation for runs whose sandboxes are elsewhere.


def test_load_resolves_the_advertised_broker_address_once(tmp_path: Path):
    config = load(
        tmp_path,
        "runtime:\n"
        "  adapter: opensandbox\n"
        "broker:\n"
        "  adapter: local\n"
        "  bind: 0.0.0.0:8321\n"
        "  advertise: broker.example.net:9443\n",
    )
    assert config.runtime.opensandbox.remote_broker_proxy == "http://broker.example.net:9443"


def test_advertise_defaults_to_bind(tmp_path: Path):
    config = load(
        tmp_path,
        "runtime:\n  adapter: opensandbox\nbroker:\n  adapter: local\n  bind: 203.0.113.7:8321\n",
    )
    assert config.runtime.opensandbox.remote_broker_proxy == "http://203.0.113.7:8321"

    # A host-only advertise is the hostname split without the port split:
    # the port is inherited from bind.
    config = load(
        tmp_path,
        "runtime:\n  adapter: opensandbox\n"
        "broker:\n  adapter: local\n  bind: 203.0.113.7:8321\n"
        "  advertise: broker.example.net\n",
    )
    assert config.runtime.opensandbox.remote_broker_proxy == "http://broker.example.net:8321"


def test_no_bind_publishes_no_remote_proxy(tmp_path: Path):
    # Empty is today's behaviour: the broker keeps the loopback/bridge
    # derivation and the runtime keeps forwarding the runner's proxy env.
    config = load(tmp_path, "runtime:\n  adapter: opensandbox\nbroker:\n  adapter: local\n")
    assert config.runtime.opensandbox.remote_broker_proxy == ""
    assert RunnerConfig().runtime.opensandbox.remote_broker_proxy == ""
    assert remote_broker_proxy(BrokerConfig(adapter="local")) == ""


def test_direct_bind_needs_a_configured_port():
    # The sandbox-side composition has no channel to learn an ephemeral
    # port: the configured number is the shared derivation, so a bind
    # without one is refused at load, never resolved to a guess.
    with pytest.raises(ValidationError, match="names no port"):
        BrokerConfig(adapter="local", bind="203.0.113.7")


def test_advertise_needs_a_bind():
    with pytest.raises(ValidationError, match="advertising an address nothing binds"):
        BrokerConfig(adapter="local", advertise="broker.example.net:9443")


def test_wildcard_bind_must_advertise():
    # 0.0.0.0 listens everywhere and reaches no one: an in-sandbox client
    # dialing the bind verbatim dials itself.
    with pytest.raises(ValidationError, match="no reachable destination"):
        BrokerConfig(adapter="local", bind="0.0.0.0:8321")


def test_bind_needs_a_thread():
    with pytest.raises(ValidationError, match="no thread to bind"):
        BrokerConfig(adapter="none", bind="203.0.113.7:8321")


def test_broker_addresses_are_hosts_not_urls():
    for bad in (
        "http://broker.example.net:1",
        "broker.example.net/path",
        "*.example.net",
        "broker.example.net:99999",
        " broker.example.net:1 ",
    ):
        with pytest.raises(ValidationError, match=r"broker\.bind"):
            BrokerConfig(adapter="local", bind=bad)

    with pytest.raises(ValidationError, match=r"broker\.advertise"):
        BrokerConfig(adapter="local", bind="203.0.113.7:1", advertise="https://a.b:2")


def test_the_remote_proxy_is_not_a_configured_key(tmp_path: Path):
    # The advertised address has one location — the broker block. Setting
    # the derived value directly under runtime.opensandbox is refused like
    # any unknown key: there is no second channel.
    with pytest.raises(ValidationError, match="remote_broker_proxy"):
        RunnerConfig.model_validate(
            {"runtime": {"opensandbox": {"remote_broker_proxy": "http://forged:1"}}}
        )


def test_remote_broker_proxy_is_silent_for_sealed():
    # Sealed mode's address is the network's gateway at a name-derived
    # port; the helper answers no remote endpoint rather than inventing
    # an address the sealed wiring does not use.
    sealed = BrokerConfig(adapter="local", mode="sealed", network="torve-sealed")
    assert remote_broker_proxy(sealed) == ""


def test_the_review_role_loads_a_shipped_skill_by_default() -> None:
    # S-0054/D-14 as landed: the shipped skill declaring the role reaches it.
    # `reading-isnt-proof` is vendored in this repository, not shipped, so
    # a default naming it would refuse every adopter's review. The default is
    # the profile `torve init` mints for the role now (S-0061/D-11), so the
    # table it mints from is what carries the promise.
    assert ROLE_SKILLS["review"] == ["ratchet-what-you-build"]


def test_the_corpus_path_defaults_beside_everything_else_under_torve() -> None:
    # S-0057/D-3: one path, and the archive and the schemas are its siblings —
    # so the default is the layout constant, not a second spelling of it.
    assert RunnerConfig().specs.path == layout.SPECS_DIR == ".torve/specs"


def test_the_old_rfcs_key_is_refused_naming_specs() -> None:
    # S-0057/D-3: renamed, never mapped. Silently accepting `rfcs` would point
    # a converted repository's engine at a corpus that no longer exists.
    with pytest.raises(ValidationError, match=r"`rfcs` is `specs`"):
        RunnerConfig.model_validate({"rfcs": {"path": "rfcs"}})


# ....................... #
# The provider record (S-0064/D-1): `broker.providers` folded into
# `.torve/providers/<name>.yaml`, and the broker's three wire facts are
# projected from there at load rather than written twice.


def provider(root: Path, name: str, body: str) -> Path:
    return write(root / layout.TORVE_DIR / "providers" / f"{name}.yaml", body)


def seated(tmp_path: Path) -> Path:
    """A repository whose record has a seat on it. The broker routes what seats
    reach rather than what the records declare (S-0064/D-4), so a record nothing
    is seated on is deliberately not routed at all."""

    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    harness(root, "dialled", "adapter: api\nimage: probe-sandbox\napi: [openai, anthropic]\n")

    return root


def test_a_provider_record_is_projected_onto_the_broker_at_load(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    provider(
        root,
        "modelstudio",
        "key_env: MODELSTUDIO_CODING_API_KEY\n"
        "via_proxy: true\n"
        "routes:\n"
        "  openai:\n"
        "    base_url: https://p.example/compatible-mode/v1\n"
        "models:\n"
        "  qwen3.8-flash: {price: {input: 0.3, output: 1.2}}\n",
    )
    seated(tmp_path)
    config = load(
        tmp_path,
        "schema_version: 1\nbroker: {adapter: local}\ntiers:\n"
        "  planner: {harness: fake}\n  reviewer: {harness: fake}\n"
        "  executor: {harness: dialled, provider: modelstudio, model: qwen3.8-flash,"
        " dialect: openai}\n",
    )

    # The broker forwards, so it gets exactly the three facts it needs.
    routed = config.broker.providers["modelstudio.openai"]
    assert routed.upstream == "https://p.example/compatible-mode/v1"
    assert routed.key_env == "MODELSTUDIO_CODING_API_KEY"
    assert routed.via_proxy is True

    # The roster and the clocks stay on the record, where the dispatch reads
    # them: the broker has no business knowing what a token costs.
    entry = config.provider_records["modelstudio"].models["qwen3.8-flash"]
    assert entry.price is not None
    assert entry.price.input == 0.3


def test_a_record_needs_no_broker_block_to_be_read(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    provider(
        root,
        "deepseek",
        "key_env: DEEPSEEK_API_KEY\nroutes: {openai: {base_url: https://api.deepseek.com}}\n"
        "models: {deepseek-flash: {}}\n",
    )
    seated(tmp_path)
    config = load(
        tmp_path,
        "schema_version: 1\ntiers:\n"
        "  planner: {harness: fake}\n  reviewer: {harness: fake}\n"
        "  executor: {harness: dialled, provider: deepseek, model: deepseek-flash}\n",
    )

    assert set(config.provider_records) == {"deepseek"}
    assert config.broker.providers["deepseek.openai"].upstream == "https://api.deepseek.com"


def test_broker_providers_in_the_configuration_is_refused_naming_where_it_went(tmp_path: Path):
    with pytest.raises(ValueError, match=r"\.torve/providers"):
        load(
            tmp_path,
            "schema_version: 1\n"
            "broker:\n"
            "  adapter: local\n"
            "  providers:\n"
            "    p: {upstream: https://p.example, key_env: P_API_KEY}\n",
        )


def test_provider_records_is_read_from_the_records_and_never_written_here(tmp_path: Path):
    with pytest.raises(ValueError, match="never written in this file"):
        load(
            tmp_path,
            "schema_version: 1\n"
            "provider_records:\n"
            "  p: {key_env: P_API_KEY, routes: {openai: {base_url: https://p.example}}}\n",
        )


TWO_DIALECTS = (
    "key_env: MODELSTUDIO_CODING_API_KEY\n"
    "routes:\n"
    "  openai: {base_url: https://p.example/compatible-mode/v1}\n"
    "  anthropic: {base_url: https://p.example/apps/anthropic}\n"
    "models: {qwen3.8-flash: {}}\n"
)


def test_two_seats_on_one_provider_get_a_route_each(tmp_path: Path):
    """The broker keyed a route by provider while every record served one
    dialect, and refused two seats that wanted different ones — naming the work
    in the words of the thing that had to change. A route is a provider and a
    dialect together now (S-0064/D-2): two upstreams that answer differently are
    two routes on one credential."""

    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    provider(root, "modelstudio", TWO_DIALECTS)
    seated(tmp_path)
    harness(root, "anthropic-only", "adapter: api\nimage: probe-sandbox\napi: [anthropic]\n")
    config = load(
        tmp_path,
        "schema_version: 1\nbroker: {adapter: local}\ntiers:\n"
        "  planner: {harness: fake}\n  reviewer: {harness: fake}\n"
        "  executor: {harness: dialled, provider: modelstudio, model: qwen3.8-flash,"
        " dialect: openai}\n"
        "  executor.other: {harness: anthropic-only, provider: modelstudio,"
        " model: qwen3.8-flash}\n",
    )

    assert sorted(config.broker.providers) == ["modelstudio.anthropic", "modelstudio.openai"]
    assert (
        config.broker.providers["modelstudio.anthropic"].upstream
        == "https://p.example/apps/anthropic"
    )

    # The seat carries the name the broker knows its route by, so nothing
    # downstream has to re-derive it.
    assert config.tiers["executor"].route == "modelstudio.openai"

    # A seat that names no dialect where its harness and provider share only
    # one gets it resolved rather than left implied.
    assert config.tiers["executor.other"].dialect == "anthropic"


def test_a_malformed_record_refuses_the_whole_load_by_path(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    provider(root, "broken", "routes: {openai: {base_url: https://p.example}}\n")

    with pytest.raises(ValueError, match="key_env"):
        load(tmp_path, "schema_version: 1\n")


# ....................... #
# The refusals the record makes possible (S-0064/D-4, S-0064/D-5)


ACME = (
    "key_env: ACME_KEY\n"
    "routes: {openai: {base_url: https://acme.test/v1}}\n"
    "models: {fast: {reasoning: [low, high]}}\n"
)


def _paired(tmp_path: Path, seat: str, api: str = "[openai]") -> RunnerConfig:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    provider(root, "acme", ACME)
    harness(root, "dialled", f"adapter: api\nimage: probe-sandbox\napi: {api}\n")

    return load(
        tmp_path,
        "schema_version: 1\nproviders: {default: [acme]}\ntiers:\n"
        "  planner: {harness: fake}\n  reviewer: {harness: fake}\n"
        f"  executor: {{harness: dialled, provider: acme, {seat}}}\n",
    )


def test_a_seat_reaches_only_a_model_its_provider_declares(tmp_path: Path):
    """The roster is what a seat may reach. An undeclared model would reach the
    provider under whatever name was typed, and the regime hash would record a
    model nobody wrote down."""

    with pytest.raises(ValueError, match="never one it does not"):
        _paired(tmp_path, "model: ghost")

    assert _paired(tmp_path, "model: fast").tiers["executor"].model == "fast"


def test_a_seat_whose_harness_cannot_speak_to_its_provider_is_refused(tmp_path: Path):
    with pytest.raises(ValueError, match="serves openai") as excinfo:
        _paired(tmp_path, "model: fast", api="[anthropic]")

    # Both files, because which of them is wrong is the reader's call.
    assert "manifest names another dialect" in str(excinfo.value)
    assert ".torve/providers/acme.yaml" in str(excinfo.value)


def test_a_seat_on_a_harness_declaring_no_dialect_is_refused(tmp_path: Path):
    with pytest.raises(ValueError, match="owes `api`"):
        _paired(tmp_path, "model: fast", api="[]")


def test_an_effort_the_model_does_not_have_is_refused_before_a_sandbox_exists(tmp_path: Path):
    """The endpoint refuses this per request, after a sandbox exists. The engine
    can refuse it before one does, which is the whole argument for the model
    declaring its levels rather than carrying a flag."""

    with pytest.raises(ValueError, match="has low, high"):
        _paired(tmp_path, "model: fast, reasoning: max")

    assert _paired(tmp_path, "model: fast, reasoning: high").tiers["executor"].reasoning == "high"


def test_a_model_that_does_not_reason_cannot_be_asked_to_think_harder(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    provider(
        root,
        "acme",
        "key_env: ACME_KEY\nroutes: {openai: {base_url: https://acme.test/v1}}\n"
        "models: {plain: {}}\n",
    )
    harness(root, "dialled", "adapter: api\nimage: probe-sandbox\napi: [openai]\n")

    with pytest.raises(ValueError, match="declares no reasoning levels"):
        load(
            tmp_path,
            "schema_version: 1\ntiers:\n"
            "  planner: {harness: fake}\n  reviewer: {harness: fake}\n"
            "  executor: {harness: dialled, provider: acme, model: plain, reasoning: low}\n",
        )


# ....................... #
# The landing leg's refusal (S-0068/D-1): `auto_merge` alone converts five unused
# criteria into five unset ones, so it cannot be flipped alone. Tested by its
# twin — every criterion off must refuse, any one armed must load.


def test_auto_merge_with_no_criterion_armed_is_refused_naming_what_to_set(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"promotion\.auto_merge is on with no") as excinfo:
        load(tmp_path, "schema_version: 1\npromotion:\n  auto_merge: true\n")

    message = str(excinfo.value)

    # The refusal names the file, the field, and every criterion that would
    # answer it — a warning nobody reads at three in the morning is what it
    # replaces, so the message has to carry the fix.
    assert "config.yaml" in message

    for criterion in ("require_ci", "require_review", "approvals", "quiet_window"):
        assert f"promotion.{criterion}" in message


@pytest.mark.parametrize(
    "criterion",
    ["require_ci: true", "require_review: true", "approvals: 1", "quiet_window: 3600"],
)
def test_any_one_promotion_criterion_arms_auto_merge(tmp_path: Path, criterion: str) -> None:
    # The bar is deliberately weak: the refusal catches the configuration
    # nobody meant to write, not a landing policy someone chose.
    config = load(tmp_path, f"schema_version: 1\npromotion:\n  auto_merge: true\n  {criterion}\n")

    assert config.promotion.auto_merge is True and config.promotion.armed()


def test_a_criterion_loads_without_auto_merge_and_the_default_config_still_loads(
    tmp_path: Path,
) -> None:
    # `torve merge` reads the same knobs and is a human act; arming a criterion
    # for it must not need the pass's landing leg armed too, and the shape this
    # repository is in — no `promotion:` block at all — still loads.
    promotion = load(tmp_path, "schema_version: 1\npromotion:\n  require_review: true\n").promotion

    assert promotion.require_review is True and promotion.auto_merge is False
    assert load(tmp_path, "schema_version: 1\n").promotion.armed() is False


# ....................... #
# The landing mode (S-0080/D-1, S-0080/D-2): a term of configuration with `local`
# as the default, and `pull_request` refused at load when the forge it needs is
# not configured.


def test_landing_defaults_to_local_and_is_never_inferred_from_the_forge(tmp_path: Path) -> None:
    # A configured remote decides nothing: the act a repository lands by changes
    # only when somebody writes that it should.
    assert load(tmp_path, "schema_version: 1\n").promotion.landing == "local"

    configured = load(
        tmp_path,
        "schema_version: 1\nscm:\n  repo: acme/widgets\n  open_pr: true\n",
    )

    assert configured.promotion.landing == "local"


def test_pull_request_loads_with_a_repository_and_open_pr(tmp_path: Path) -> None:
    config = load(
        tmp_path,
        "schema_version: 1\npromotion:\n  landing: pull_request\n"
        "scm:\n  repo: acme/widgets\n  open_pr: true\n",
    )

    assert config.promotion.landing == "pull_request"


def test_an_unknown_landing_mode_is_refused_by_the_vocabulary(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="landing"):
        load(tmp_path, "schema_version: 1\npromotion:\n  landing: forge\n")


@pytest.mark.parametrize(
    ("scm", "missing"),
    [
        ("", "scm.repo"),
        ("scm:\n  open_pr: true\n", "scm.repo"),
        ("scm:\n  repo: acme/widgets\n", "scm.open_pr"),
    ],
)
def test_pull_request_is_refused_at_load_naming_the_field_to_set(
    tmp_path: Path, scm: str, missing: str
) -> None:
    # The mode could only ever fail at the first landing, hours into an
    # unattended night; it fails here instead, naming what to set.
    with pytest.raises(ValueError, match=r"promotion\.landing is pull_request") as excinfo:
        load(tmp_path, f"schema_version: 1\npromotion:\n  landing: pull_request\n{scm}")

    message = str(excinfo.value)

    assert missing in message and "config.yaml" in message


# ....................... #
# The landing unit (S-0083/D-1, S-0083/D-2): a second term beside the mode, with
# `task` as the default, and inert rather than refused under `landing: local`.


def test_the_landing_unit_defaults_to_task_and_is_never_inferred(tmp_path: Path) -> None:
    # Every repository configured today keeps landing one pull request per task;
    # candidates that happen to share a document decide nothing.
    assert load(tmp_path, "schema_version: 1\n").promotion.unit == "task"

    configured = load(
        tmp_path,
        "schema_version: 1\npromotion:\n  landing: pull_request\n  unit: document\n"
        "scm:\n  repo: acme/widgets\n  open_pr: true\n",
    )

    assert configured.promotion.unit == "document"


def test_the_unit_is_ignored_under_a_local_landing_rather_than_refused(tmp_path: Path) -> None:
    # A repository moving between the two modes edits one key, and a `unit`
    # that survives the switch back is inert rather than wrong.
    promotion = load(tmp_path, "schema_version: 1\npromotion:\n  unit: document\n").promotion

    assert promotion.landing == "local" and promotion.unit == "document"


def test_an_unknown_landing_unit_is_refused_by_the_vocabulary(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unit"):
        load(tmp_path, "schema_version: 1\npromotion:\n  unit: phase\n")


# ....................... #
# The night section (S-0079/D-5). Both refusals fire at load, which is the
# whole point of them: a night is armed by somebody standing at a terminal
# and read again at 04:00 by nobody.


def test_the_night_section_loads_its_terms_and_defaults_to_a_bounded_one(tmp_path):
    config = load(
        tmp_path,
        "schema_version: 1\nnight:\n  budget_usd: 12.5\n  budget_attempts: 4\n"
        "  minutes: 90\n  stop_on: [locked_conflict, blocker_finding]\n",
    )

    assert config.night.budget_usd == 12.5
    assert config.night.budget_attempts == 4
    assert config.night.minutes == 90
    assert config.night.stop_on == ["locked_conflict", "blocker_finding"]

    # A repository that never writes the section still gets terms a night
    # can end under — which is what makes the zero refusal a statement
    # about what somebody typed rather than about the default.
    assert load(tmp_path, "schema_version: 1\n").night.budget_attempts > 0


# ....................... #


def test_a_night_with_no_budget_on_either_axis_is_refused_at_load(tmp_path):
    with pytest.raises(ValueError, match="zero on both axes"):
        load(tmp_path, "schema_version: 1\nnight:\n  budget_usd: 0\n  budget_attempts: 0\n")

    # One axis is enough: the other is deliberately unbounded, and the
    # night still ends.
    assert load(tmp_path, "schema_version: 1\nnight:\n  budget_usd: 0\n").night.budget_usd == 0.0


# ....................... #


def test_a_stop_class_nothing_escalates_is_refused_naming_what_does(tmp_path):
    """The misspelling is caught while a person is there to fix it. Left to
    04:00 it is a stop condition that silently never fires, and nobody is
    awake to notice that it did not."""

    with pytest.raises(ValueError, match="locked_conflcit"):
        load(tmp_path, "schema_version: 1\nnight:\n  stop_on: [locked_conflcit]\n")

    assert load(tmp_path, "schema_version: 1\nnight:\n  stop_on: [killed]\n").night.stop_on == [
        "killed"
    ]
