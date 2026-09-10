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
    role_equipment,
)
from torve.config.runconfig import effective_skill_sets, load_runner_config

# ----------------------- #


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


SKILL = "equipment: [{kind: skill, source: torve:%s}]\n"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    where = tmp_path / "repo"
    write(harnesses_dir(where) / "fake.yaml", "adapter: fake\n")
    # A harness that takes a plugin, for the seats whose profile declares one:
    # `fake` names no kind, which is what a harness that takes nothing looks like.
    write(
        harnesses_dir(where) / "plugged.yaml",
        "adapter: fake\nequips:\n  plugin: --plugin-dir {path}\n",
    )
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
        "equipment: [{kind: skill, source: torve:flag-dont-flip}]\n"
        "prompt_extras: Prefer the smallest change.\n",
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
    assert seat.prompt_extras == "Prefer the smallest change."
    assert (seat.model, seat.provider) == ("claude-opus-5", "anthropic")
    assert (seat.harness, seat.profile) == ("claude-code", "careful")


def test_a_seat_naming_no_harness_is_refused(root: Path):
    with pytest.raises(ValueError, match="names no harness"):
        load(root, "tiers:\n  executor:\n    model: m\n")


def test_a_plugin_is_a_source_and_a_ref(root: Path):
    write(
        agents_dir(root) / "equipped.yaml",
        "equipment:\n  - kind: plugin\n    source: github:JuliusBrussee/caveman\n"
        "    ref: 81536f57b330\n",
    )
    config = load(root, "tiers:\n  executor:\n    harness: plugged\n    profile: equipped\n")
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
        ("equipment", "[]", "agent profile"),
        ("prepare", "index --yes", "agent profile"),
        ("prompt_extras", "be brief", "agent profile"),
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
    write(harnesses_dir(root) / "mixed.yaml", "adapter: fake\nprompt_extras: be brief\n")

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
    write(agents_dir(root) / "existing.yaml", "equipment: []\n")

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

    write(agents_dir(root) / "wrapper.yaml", "profile: base\nequipment: []\n")

    with pytest.raises(AgentError, match="seat"):
        load_profile(root, "wrapper")

    assert not (agents_dir(root) / "base.yaml").exists()


# ....................... #
# The role default is a profile named for the role (S-0061/D-11)


def test_the_role_default_is_a_profile_named_for_the_role(root: Path):
    write(agents_dir(root) / "implement.yaml", SKILL % "flag-dont-flip")
    write(agents_dir(root) / "review.yaml", SKILL % "ratchet-what-you-build")

    assert {
        role: [item.source for item in items] for role, items in role_equipment(root).items()
    } == {
        "implement": ["torve:flag-dont-flip"],
        "review": ["torve:ratchet-what-you-build"],
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
        write(agents_dir(where) / "shared.yaml", SKILL % "flag-dont-flip")
        gates = write(where / ".torve" / "gates.yaml", "schema_version: 1\ngates: []\n")
        digests.append(config_hash(gates, where, load(where, text)))

    assert digests[0] == digests[1]


def test_editing_a_profile_changes_the_regime(root: Path):
    write(root / ".torve" / "gates.yaml", "schema_version: 1\ngates: []\n")
    profile = write(agents_dir(root) / "shared.yaml", SKILL % "flag-dont-flip")
    text = "tiers:\n  executor:\n    harness: fake\n    profile: shared\n"

    before = config_hash(root / ".torve" / "gates.yaml", root, load(root, text))
    write(profile, SKILL % "ratchet-what-you-build")
    after = config_hash(root / ".torve" / "gates.yaml", root, load(root, text))

    assert before != after


def test_a_profile_contributes_what_it_wrote_and_not_its_model_s_defaults(root: Path):
    """The trap this closes: writing a profile only to add plugins used to set
    `skills: []` on the seat and silently strip the role's set. A merge over a
    model dump cannot tell a key the file omitted from one it set to the
    default — the ambiguity S-0028/D-2 mandated a raw-mapping merge to avoid."""

    write(agents_dir(root) / "implement.yaml", SKILL % "flag-dont-flip")
    write(
        agents_dir(root) / "equipped.yaml",
        "equipment:\n  - kind: plugin\n    source: github:JuliusBrussee/caveman\n    ref: abc\n",
    )
    config = load(root, "tiers:\n  executor:\n    harness: plugged\n    profile: equipped\n")
    seat = config.tiers["executor"]

    assert seat.skills is None  # not written, so the role's profile answers
    assert effective_skill_sets(seat, "implement", config.skills.sets)["implement"] == [
        "flag-dont-flip"
    ]
    assert [p.source for p in seat.plugins] == ["github:JuliusBrussee/caveman"]

    # An empty list written *is* a declaration: this agent equips nothing.
    write(agents_dir(root) / "bare.yaml", "equipment: []\n")
    bare = load(root, "tiers:\n  executor:\n    harness: fake\n    profile: bare\n")

    assert bare.tiers["executor"].skills is None


def test_a_harness_contributes_what_it_wrote_too(root: Path):
    """The same rule at the other file: a manifest naming no image leaves the
    seat's empty so `image_for` falls through to the runtime global, rather
    than writing the model default over a value the seat could have had."""

    write(harnesses_dir(root) / "bare.yaml", "adapter: fake\n")
    config = load(root, "tiers:\n  executor:\n    harness: bare\n")

    assert config.tiers["executor"].image == ""
    assert config.tiers["executor"].auth_mount == "/auth"  # the model's default still applies


def test_only_a_name_the_engine_has_a_role_for_is_a_role_profile(root: Path):
    """A profile written for a seat is not a role. Keying it as one puts a role
    nothing dispatches into the role sets and into the regime hash, and that is
    what makes `torve eval` refuse a skill as being in no role set."""

    write(agents_dir(root) / "review.yaml", SKILL % "ratchet-what-you-build")
    write(agents_dir(root) / "careful.yaml", SKILL % "flag-dont-flip")

    assert list(role_equipment(root)) == ["review"]
