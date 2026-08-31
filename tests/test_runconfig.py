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
from torve.config.runconfig import RunnerConfig, load_runner_config, profiles_dir

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
