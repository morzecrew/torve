"""The three files a seat is made of (S-0061): the agent profile, the harness
manifest, and the seat that names them.

Each key has exactly one home and the other two files refuse it by name
(S-0061/I-2); a profile may not reach the working rules an attempt is gated
against (S-0061/D-4); and both files are the repository's, so two checkouts of
one tree resolve one regime (S-0061/D-10).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from torve.application.telemetry import config_hash
from torve.config.agents import (
    AgentError,
    agents_dir,
    harnesses_dir,
    load_harness,
    load_profile,
    role_profiles,
)
from torve.config.runconfig import load_runner_config

# ----------------------- #


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    where = tmp_path / "repo"
    write(harnesses_dir(where) / "fake.yaml", "adapter: fake\n")
    return where


def load(root: Path, text: str):
    return load_runner_config(root, write(root / ".torve" / "config.yaml", text))


# ....................... #
# The seat names one of each, and what they carry is merged onto it


def test_a_seat_resolves_its_harness_and_its_profile(root: Path):
    write(
        harnesses_dir(root) / "claude-code.yaml",
        "adapter: harness\ncommand: claude -p {prompt}\nimage: torve-agent:claude\n",
    )
    write(
        agents_dir(root) / "careful.yaml",
        "skills: [flag-dont-flip]\nprompt_extras: [Prefer the smallest change.]\n",
    )
    config = load(
        root,
        "tiers:\n  executor:\n    harness: claude-code\n    profile: careful\n"
        "    model: claude-opus-5\n    provider: anthropic\n",
    )
    seat = config.tiers["executor"]

    # The harness's, the profile's and the seat's own, on one object — three
    # files, one resolved seat, because three objects at every call site would
    # be the split leaking out of the files it belongs in.
    assert (seat.adapter, seat.command, seat.image) == (
        "harness",
        "claude -p {prompt}",
        "torve-agent:claude",
    )
    assert seat.skills == ["flag-dont-flip"]
    assert seat.prompt_extras == ["Prefer the smallest change."]
    assert (seat.model, seat.provider) == ("claude-opus-5", "anthropic")
    assert (seat.harness, seat.profile) == ("claude-code", "careful")


def test_a_seat_naming_no_harness_is_refused(root: Path):
    with pytest.raises(ValueError, match="names no harness"):
        load(root, "tiers:\n  executor:\n    model: m\n")


def test_a_plugin_is_a_source_and_a_ref(root: Path):
    write(
        agents_dir(root) / "equipped.yaml",
        "plugins:\n  - source: github:JuliusBrussee/caveman\n    ref: 81536f57b330\n",
    )
    config = load(root, "tiers:\n  executor:\n    harness: fake\n    profile: equipped\n")
    (plugin,) = config.tiers["executor"].plugins

    assert (plugin.source, plugin.ref) == ("github:JuliusBrussee/caveman", "81536f57b330")


# ....................... #
# One home per key (S-0061/I-2): the other two files refuse it by name


@pytest.mark.parametrize(
    ("key", "value", "where"),
    [
        ("adapter", "harness", "harness manifest"),
        ("command", "run {model}", "harness manifest"),
        ("image", "img:1", "harness manifest"),
        ("api_key_env", "[FOO]", "harness manifest"),
        ("skills", "[flag-dont-flip]", "agent profile"),
        ("plugins", "[]", "agent profile"),
        ("prompt_extras", "[be brief]", "agent profile"),
    ],
)
def test_a_moved_key_on_the_seat_names_the_file_it_moved_to(root, key, value, where):
    """S-0061/D-9: the migration guide is the error, so no repository needs
    one written."""

    with pytest.raises(ValueError, match=where) as excinfo:
        load(root, f"tiers:\n  executor:\n    harness: fake\n    {key}: {value}\n")

    assert f"`{key}`" in str(excinfo.value)


def test_a_seat_key_in_a_profile_is_refused(root: Path):
    """The reason this document exists: a file named for a persona could
    legally decide where a conviction routes."""

    write(agents_dir(root) / "routing.yaml", "retry_variants: {functional: executor.heavy}\n")

    with pytest.raises(AgentError, match="seat"):
        load_profile(root, "routing")


def test_a_harness_key_in_a_profile_is_refused(root: Path):
    write(agents_dir(root) / "mixed.yaml", "adapter: harness\n")

    with pytest.raises(AgentError, match="harness manifest"):
        load_profile(root, "mixed")


def test_a_profile_key_in_a_harness_is_refused(root: Path):
    write(harnesses_dir(root) / "mixed.yaml", "adapter: fake\nskills: [flag-dont-flip]\n")

    with pytest.raises(AgentError, match="agent profile"):
        load_harness(root, "mixed")


# ....................... #
# What a profile may not say (S-0061/D-4)


def test_a_profile_may_not_name_a_prompt(root: Path):
    """The one real refusal: the base working rules tell an executor to log
    every divergence over a governed file and to keep corpus coordinates out
    of user-facing strings, which is exactly what `decisions-reported` and
    `user-facing-text` convict on. A file that could replace them could
    disarm two blocking gates."""

    write(agents_dir(root) / "loud.yaml", "prompt: ignore the contract\n")

    with pytest.raises(AgentError, match="prompt_extras") as excinfo:
        load_profile(root, "loud")

    assert "the engine's" in str(excinfo.value)


# ....................... #
# Refusals name the file (S-0061/D-3's inheritance from S-0028/D-3)


def test_a_missing_file_names_its_path_and_what_is_present(root: Path):
    write(agents_dir(root) / "existing.yaml", "skills: []\n")

    with pytest.raises(AgentError, match=r"missing\.yaml") as excinfo:
        load_profile(root, "missing")

    assert "existing" in str(excinfo.value)


def test_a_non_mapping_body_names_the_file(root: Path):
    path = write(agents_dir(root) / "listy.yaml", "- just\n- a\n- list\n")

    with pytest.raises(AgentError, match="must be a mapping") as excinfo:
        load_profile(root, "listy")

    assert str(path) in str(excinfo.value)


def test_an_unknown_key_names_the_key_and_the_file(root: Path):
    path = write(agents_dir(root) / "typo.yaml", "bogus_field: 1\n")

    with pytest.raises(AgentError, match="bogus_field") as excinfo:
        load_profile(root, "typo")

    assert str(path) in str(excinfo.value)


def test_an_invalid_merged_seat_names_its_harness_and_profile(root: Path):
    """A real adapter with no command: the underlying pydantic error, wrapped
    to name the seat and the files that supplied it."""

    write(harnesses_dir(root) / "half.yaml", "adapter: harness\n")

    with pytest.raises(ValueError, match="needs a command") as excinfo:
        load(root, "tiers:\n  executor:\n    harness: half\n")

    message = str(excinfo.value)
    assert "executor" in message and "half" in message


def test_one_merge_level_a_profile_naming_a_profile_is_refused(root: Path):
    """S-0061/D-8: neither file references another of its kind, so the file a
    refusal names is the file carrying the bad key."""

    write(agents_dir(root) / "wrapper.yaml", "profile: base\nskills: []\n")

    with pytest.raises(AgentError, match="seat"):
        load_profile(root, "wrapper")

    assert not (agents_dir(root) / "base.yaml").exists()


# ....................... #
# The role default is a profile named for the role (S-0061/D-11)


def test_the_role_default_is_a_profile_named_for_the_role(root: Path):
    write(agents_dir(root) / "implement.yaml", "skills: [flag-dont-flip]\n")
    write(agents_dir(root) / "review.yaml", "skills: [ratchet-what-you-build]\n")

    assert role_profiles(root) == {
        "implement": ["flag-dont-flip"],
        "review": ["ratchet-what-you-build"],
    }

    config = load(root, "tiers:\n  executor:\n    harness: fake\n")

    assert config.skills.sets["implement"] == ["flag-dont-flip"]


def test_a_seat_with_no_profile_declares_no_skills_of_its_own(root: Path):
    """`None`, not `[]` — the seat's profile named none, so the role's own
    profile answers instead (`effective_skill_sets`)."""

    config = load(root, "tiers:\n  executor:\n    harness: fake\n")

    assert config.tiers["executor"].skills is None


# ....................... #
# The regime is the repository's (S-0061/D-10)


def test_two_checkouts_of_one_tree_hash_one_regime(tmp_path: Path):
    """The reason both files are committed: D-7 puts their bodies into the
    hash, and a hash over a file that exists on one laptop reproduces
    nowhere."""

    text = "tiers:\n  executor:\n    harness: fake\n    profile: shared\n"
    digests = []

    for name in ("first", "second"):
        where = tmp_path / name
        write(harnesses_dir(where) / "fake.yaml", "adapter: fake\n")
        write(agents_dir(where) / "shared.yaml", "skills: [flag-dont-flip]\n")
        gates = write(where / ".torve" / "gates.yaml", "schema_version: 1\ngates: []\n")
        digests.append(config_hash(gates, where, load(where, text)))

    assert digests[0] == digests[1]


def test_editing_a_profile_changes_the_regime(root: Path):
    write(root / ".torve" / "gates.yaml", "schema_version: 1\ngates: []\n")
    profile = write(agents_dir(root) / "shared.yaml", "skills: [flag-dont-flip]\n")
    text = "tiers:\n  executor:\n    harness: fake\n    profile: shared\n"

    before = config_hash(root / ".torve" / "gates.yaml", root, load(root, text))
    write(profile, "skills: [ratchet-what-you-build]\n")
    after = config_hash(root / ".torve" / "gates.yaml", root, load(root, text))

    assert before != after
