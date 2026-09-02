"""RFC 0028 phase 1 — agent profiles: `TierConfig.profile`, and the raw-mapping
merge `load_runner_config` performs before validation (D-28.1-D-28.5). Every
resolution failure is a refusal naming the file (D-28.3); the D-21.1
broker/credential interaction and the `config_hash` regime property (§5.3)
are both pinned here since neither needed a code change to hold.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from torve.application.telemetry import config_hash
from torve.config.runconfig import (
    RunnerConfig,
    TierConfig,
    agent_timeout_for,
    load_runner_config,
    profiles_dir,
    sandbox_timeout_for,
)

# ----------------------- #


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def agents_dir(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    directory = profiles_dir()
    directory.mkdir(parents=True)
    return directory


def load(tmp_path: Path, text: str) -> RunnerConfig:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    config_path = write(root / "config.yaml", text)
    return load_runner_config(root, config_path)


def manifest(tmp_path: Path) -> Path:
    return write(tmp_path / "gates.yaml", "schema_version: 1\ngates: []\n")


# ....................... #
# Resolution: profile fills fields, local keys win


def test_profile_fills_a_tiers_fields(agents_dir: Path, tmp_path: Path):
    write(
        agents_dir / "claude-sonnet.yaml",
        "adapter: harness\nprovider: anthropic\nmodel: claude-sonnet-5\ncommand: run {model}\n",
    )
    config = load(tmp_path, "tiers:\n  executor:\n    profile: claude-sonnet\n")
    tier = config.tiers["executor"]

    assert tier.adapter == "harness"
    assert tier.provider == "anthropic"
    assert tier.model == "claude-sonnet-5"
    assert tier.command == "run {model}"
    assert tier.profile == "claude-sonnet"


def test_local_key_wins_over_profile_key(agents_dir: Path, tmp_path: Path):
    write(agents_dir / "prof.yaml", "model: profile-model\n")
    config = load(
        tmp_path,
        "tiers:\n  executor:\n    profile: prof\n    model: local-model\n",
    )

    assert config.tiers["executor"].model == "local-model"


def test_locally_written_empty_string_wins_over_a_profile_value(agents_dir: Path, tmp_path: Path):
    """The §5.2 ambiguity: a model-level merge can't tell "set to empty" from
    "never written" because both read as the field default. The raw-mapping
    merge can, by key presence — this is exactly why D-28.2 mandates it."""

    write(agents_dir / "prof.yaml", "model: profile-model\n")
    config = load(
        tmp_path,
        'tiers:\n  executor:\n    profile: prof\n    model: ""\n',
    )

    assert config.tiers["executor"].model == ""


def test_api_key_env_replaces_wholesale_never_concatenates(agents_dir: Path, tmp_path: Path):
    write(agents_dir / "prof.yaml", "api_key_env: [FOO, BAR]\n")
    config = load(
        tmp_path,
        "tiers:\n  executor:\n    profile: prof\n    api_key_env: [BAZ]\n",
    )

    assert config.tiers["executor"].api_key_env == ["BAZ"]


def test_tier_skills_replace_a_profiles_skills_wholesale(agents_dir: Path, tmp_path: Path):
    """D-29.3: `skills` rides the profile merge under D-28.4's rule — a
    local list replaces the profile's list entirely, the same way
    `api_key_env` does above; there is no per-field union logic to add."""

    write(agents_dir / "prof.yaml", "skills: [prose-voice, keep-a-changelog]\n")
    config = load(
        tmp_path,
        "tiers:\n  executor:\n    profile: prof\n    skills: [flag-dont-flip]\n",
    )

    assert config.tiers["executor"].skills == ["flag-dont-flip"]


def test_tier_skills_carry_through_a_profile_when_not_locally_overridden(
    agents_dir: Path, tmp_path: Path
):
    write(agents_dir / "prof.yaml", "skills: [prose-voice, keep-a-changelog]\n")
    config = load(tmp_path, "tiers:\n  executor:\n    profile: prof\n")

    assert config.tiers["executor"].skills == ["prose-voice", "keep-a-changelog"]


def test_a_partial_profile_leaves_untouched_fields_at_their_default(
    agents_dir: Path, tmp_path: Path
):
    """D-28.5: a profile body may be a skeleton — only checked for key
    validity at merge time, never validated as a standalone TierConfig."""

    write(agents_dir / "skeleton.yaml", "image: torve-battery:latest\ncommand: claude -p {prompt}\n")
    config = load(tmp_path, "tiers:\n  executor:\n    profile: skeleton\n")
    tier = config.tiers["executor"]

    assert tier.image == "torve-battery:latest"
    assert tier.command == "claude -p {prompt}"
    assert tier.adapter == "fake"  # untouched by the profile, at its model default


def test_profile_list_merges_left_to_right(agents_dir: Path, tmp_path: Path):
    """A-74: a list of names merges left to right — the rightmost profile's
    keys win over the ones before it, same shallow rule as local-over-profile."""

    write(agents_dir / "wiring.yaml", "adapter: harness\ncommand: c\nmodel: wiring-model\n")
    write(agents_dir / "equipment.yaml", "model: equipment-model\nprovider: anthropic\n")
    config = load(
        tmp_path,
        "tiers:\n  executor:\n    profile: [wiring, equipment]\n",
    )
    tier = config.tiers["executor"]

    assert tier.adapter == "harness"  # only wiring names it
    assert tier.model == "equipment-model"  # equipment, to the right, wins the overlap
    assert tier.provider == "anthropic"


def test_profile_list_local_key_still_wins_last(agents_dir: Path, tmp_path: Path):
    write(agents_dir / "wiring.yaml", "model: wiring-model\n")
    write(agents_dir / "equipment.yaml", "model: equipment-model\n")
    config = load(
        tmp_path,
        "tiers:\n  executor:\n    profile: [wiring, equipment]\n    model: local-model\n",
    )

    assert config.tiers["executor"].model == "local-model"


def test_profile_list_list_fields_replace_wholesale_across_every_layer(
    agents_dir: Path, tmp_path: Path
):
    write(agents_dir / "wiring.yaml", "api_key_env: [FOO]\n")
    write(agents_dir / "equipment.yaml", "api_key_env: [BAR]\n")
    config = load(
        tmp_path,
        "tiers:\n  executor:\n    profile: [wiring, equipment]\n",
    )

    # equipment's list replaces wiring's outright, never concatenates.
    assert config.tiers["executor"].api_key_env == ["BAR"]

    config = load(
        tmp_path,
        "tiers:\n  executor:\n    profile: [wiring, equipment]\n    api_key_env: [BAZ]\n",
    )

    # the local list, in turn, replaces the composed chain's outright.
    assert config.tiers["executor"].api_key_env == ["BAZ"]


def test_profile_list_records_the_chain_in_order(agents_dir: Path, tmp_path: Path):
    write(agents_dir / "wiring.yaml", "model: m\n")
    write(agents_dir / "equipment.yaml", "provider: anthropic\n")
    config = load(tmp_path, "tiers:\n  executor:\n    profile: [wiring, equipment]\n")

    assert config.tiers["executor"].profile == "wiring -> equipment"


def test_single_name_profile_is_unaffected_by_list_support(agents_dir: Path, tmp_path: Path):
    """A single name behaves exactly as today — the list machinery is never
    exercised, and `profile` carries just that one name, no arrow."""

    write(agents_dir / "solo.yaml", "adapter: harness\ncommand: c\nprovider: p\nmodel: solo-model\n")
    config = load(tmp_path, "tiers:\n  executor:\n    profile: solo\n")
    tier = config.tiers["executor"]

    assert tier.model == "solo-model"
    assert tier.profile == "solo"


def test_profile_list_missing_file_names_that_profiles_own_path(
    agents_dir: Path, tmp_path: Path
):
    write(agents_dir / "wiring.yaml", "model: m\n")

    with pytest.raises(ValueError, match=r"missing\.yaml") as excinfo:
        load(tmp_path, "tiers:\n  executor:\n    profile: [wiring, missing]\n")

    assert "profile 'missing'" in str(excinfo.value)


def test_profile_list_non_mapping_body_names_that_profiles_own_path(
    agents_dir: Path, tmp_path: Path
):
    write(agents_dir / "wiring.yaml", "model: m\n")
    bad_path = write(agents_dir / "listy.yaml", "- just\n- a\n- list\n")

    with pytest.raises(ValueError, match="must be a mapping") as excinfo:
        load(tmp_path, "tiers:\n  executor:\n    profile: [wiring, listy]\n")

    assert str(bad_path) in str(excinfo.value)


def test_profile_list_unknown_key_names_that_profiles_own_path(agents_dir: Path, tmp_path: Path):
    write(agents_dir / "wiring.yaml", "model: m\n")
    bad_path = write(agents_dir / "typo.yaml", "bogus_field: 1\n")

    with pytest.raises(ValueError, match="bogus_field") as excinfo:
        load(tmp_path, "tiers:\n  executor:\n    profile: [wiring, typo]\n")

    assert str(bad_path) in str(excinfo.value)


def test_profile_to_profile_reference_is_not_chased(agents_dir: Path, tmp_path: Path):
    """D-28.4: one merge level. A profile body naming its own `profile` key
    is not itself resolved — `base.yaml` is never read, and the field simply
    carries through like any other unconsumed key."""

    write(
        agents_dir / "wrapper.yaml",
        "profile: base\nmodel: wrapper-model\n",
    )
    config = load(tmp_path, "tiers:\n  executor:\n    profile: wrapper\n")
    tier = config.tiers["executor"]

    assert tier.model == "wrapper-model"
    assert tier.profile == "wrapper"  # the referenced name, not the unread "base"
    assert not (agents_dir / "base.yaml").exists()


# ....................... #
# Refusals — D-28.3, every failure names the file


def test_unknown_profile_name_refuses_naming_the_path_and_present_stems(
    agents_dir: Path, tmp_path: Path
):
    write(agents_dir / "existing.yaml", "model: m\n")

    with pytest.raises(ValueError, match=r"missing\.yaml") as excinfo:
        load(tmp_path, "tiers:\n  executor:\n    profile: missing\n")

    assert "existing" in str(excinfo.value)


def test_non_mapping_profile_body_refuses_naming_the_file(agents_dir: Path, tmp_path: Path):
    path = write(agents_dir / "listy.yaml", "- just\n- a\n- list\n")

    with pytest.raises(ValueError, match="must be a mapping") as excinfo:
        load(tmp_path, "tiers:\n  executor:\n    profile: listy\n")

    assert str(path) in str(excinfo.value)


def test_unknown_key_in_profile_body_refuses_naming_key_and_file(
    agents_dir: Path, tmp_path: Path
):
    path = write(agents_dir / "typo.yaml", "bogus_field: 1\n")

    with pytest.raises(ValueError, match="bogus_field") as excinfo:
        load(tmp_path, "tiers:\n  executor:\n    profile: typo\n")

    assert str(path) in str(excinfo.value)


def test_invalid_merged_result_fails_tierconfig_validation(agents_dir: Path, tmp_path: Path):
    """A real adapter with no command and no provider — the underlying
    pydantic error, now wrapped to name the tier, the profile and its file
    (D-28.3's fourth refusal class), since local content had the last word
    (there is none here, so the profile's own gap surfaces)."""

    path = write(agents_dir / "half.yaml", "adapter: harness\n")

    with pytest.raises(ValueError, match="needs a command") as excinfo:
        load(tmp_path, "tiers:\n  executor:\n    profile: half\n")

    assert not isinstance(excinfo.value, ValidationError)
    message = str(excinfo.value)
    assert "executor" in message
    assert "half" in message
    assert str(path) in message


# ....................... #
# D-21.1 interaction: profiles cannot launder a credential channel


def test_profile_supplied_api_key_env_is_refused_under_a_broker(agents_dir: Path, tmp_path: Path):
    write(agents_dir / "leaky.yaml", "api_key_env: [ANTHROPIC_API_KEY]\n")

    with pytest.raises(ValidationError, match="api_key_env"):
        load(
            tmp_path,
            "broker:\n  adapter: local\ntiers:\n  executor:\n    profile: leaky\n",
        )


# ....................... #
# The hash property (§5.3) — arrives for free, pinned as a property test


def test_two_roots_referencing_one_profile_hash_identical_tiers(agents_dir: Path, tmp_path: Path):
    write(agents_dir / "shared.yaml", "adapter: fake\nmodel: shared-model\n")
    text = "tiers:\n  executor:\n    profile: shared\n"

    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()

    first = load_runner_config(first_root, write(first_root / "config.yaml", text))
    second = load_runner_config(second_root, write(second_root / "config.yaml", text))

    assert config_hash(manifest(first_root), first_root, first) == config_hash(
        manifest(second_root), second_root, second
    )


def test_editing_a_profile_changes_the_digest_on_its_next_load(agents_dir: Path, tmp_path: Path):
    profile_path = write(agents_dir / "shared.yaml", "adapter: fake\nmodel: v1\n")
    text = "tiers:\n  executor:\n    profile: shared\n"
    root = tmp_path / "repo"
    root.mkdir()
    config_path = write(root / "config.yaml", text)

    before = load_runner_config(root, config_path)
    before_hash = config_hash(manifest(root), root, before)

    write(profile_path, "adapter: fake\nmodel: v2\n")
    after = load_runner_config(root, config_path)
    after_hash = config_hash(manifest(root), root, after)

    assert before_hash != after_hash


# ....................... #
# profiles_dir() — beside the fleet manifest (D-28.1)


def test_profiles_dir_honours_xdg_config_home(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert profiles_dir() == tmp_path / "torve" / "agents"


def test_profiles_dir_falls_back_to_the_home_config_dir(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert profiles_dir() == tmp_path / ".config" / "torve" / "agents"


# ....................... #
# retry_variants (D-34.6, D-34.7): the axis-keyed mapping, the scalar kept
# as its functional sugar, and one resolution every reader shares.


def test_retry_variants_load_axes_from_yaml_and_resolve_through_one_map(tmp_path: Path):
    config = load(
        tmp_path,
        "schema_version: 1\n"
        "tiers:\n"
        "  executor:\n"
        "    retry_variants: {functional: executor.heavy, compliance: executor}\n"
        "  executor.heavy: {adapter: api, command: c, provider: heavy, model: h}\n",
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


def test_the_mapping_rides_the_profile_merge_like_any_other_field(
    agents_dir: Path, tmp_path: Path
):
    write(agents_dir / "armed.yaml", "retry_variants: {compliance: executor}\n")
    config = load(
        tmp_path,
        "schema_version: 1\n"
        "tiers:\n"
        "  executor:\n"
        "    profile: armed\n"
        "    retry_variant: executor.heavy\n"
        "  executor.heavy: {adapter: api, command: c, provider: heavy, model: h}\n",
    )
    assert config.tiers["executor"].resolved_retry_variants() == {
        "compliance": "executor",
        "functional": "executor.heavy",
    }


def test_retry_variants_change_the_regime_digest(tmp_path: Path):
    base = {"planner": TierConfig(), "reviewer": TierConfig(), "executor": TierConfig()}
    plain = RunnerConfig(tiers=base)
    routed = RunnerConfig(
        tiers={**base, "executor": TierConfig(retry_variants={"compliance": "executor"})}
    )
    assert config_hash(manifest(tmp_path), tmp_path, plain) != config_hash(
        manifest(tmp_path), tmp_path, routed
    )


# ....................... #
# The tier clock (D-35.6): a named override wins, absence falls to the global


def test_a_tier_clock_overrides_the_runtime_global(tmp_path: Path):
    config = load(
        tmp_path,
        "runtime:\n  agent_timeout: 1200\n  sandbox_timeout: 1800\n"
        "tiers:\n  executor:\n    agent_timeout: 3600\n    sandbox_timeout: 4200\n",
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

    config = load(tmp_path, "tiers:\n  executor:\n    agent_timeout: 3600\n")
    tier = config.tiers["executor"]

    assert agent_timeout_for(config, tier) == 3600
    assert sandbox_timeout_for(config, tier) == 1800  # the runtime default


def test_an_explicit_zero_is_named_not_absent(tmp_path: Path):
    """`is None`, not truthiness: a tier that writes `0` wins with `0`. The
    fall-through is for absence, and a written value is never quietly
    discarded — the same key-presence rule the profile merge pins."""

    config = load(tmp_path, "tiers:\n  executor:\n    agent_timeout: 0\n")
    tier = config.tiers["executor"]

    assert agent_timeout_for(config, tier) == 0
    assert sandbox_timeout_for(config, tier) == 1800


def test_tier_clocks_ride_the_profile_merge(agents_dir: Path, tmp_path: Path):
    write(agents_dir / "heavy.yaml", "agent_timeout: 3600\nsandbox_timeout: 4200\n")
    config = load(
        tmp_path,
        "tiers:\n  executor:\n    profile: heavy\n    sandbox_timeout: 5000\n",
    )
    tier = config.tiers["executor"]

    assert agent_timeout_for(config, tier) == 3600  # from the profile
    assert sandbox_timeout_for(config, tier) == 5000  # local wins last
