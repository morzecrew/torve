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
