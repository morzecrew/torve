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
    PINS_FILE,
    AgentError,
    agents_dir,
    harnesses_dir,
    load_harness,
    load_profile,
    pins,
    role_equipment,
)
from torve.config.layout import TORVE_DIR
from torve.config.runconfig import effective_skill_sets, load_runner_config

# ----------------------- #


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


SKILL = "equipment: [{kind: skill, source: torve:%s}]\n"
ROLE_SKILL = "role: %s\nequipment: [{kind: skill, source: torve:%s}]\n"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    where = tmp_path / "repo"
    write(harnesses_dir(where) / "fake.yaml", "adapter: fake\n")
    # A harness that takes a plugin, for the seats whose profile declares one:
    # `fake` names no kind, which is what a harness that takes nothing looks like.
    write(
        harnesses_dir(where) / "plugged.yaml",
        "adapter: fake\nkinds: [plugin]\n",
    )
    return where


def load(root: Path, text: str):
    return load_runner_config(root, write(root / ".torve" / "config.yaml", text))


# ....................... #
# The seat names one of each, and what they carry is merged onto it


def test_a_seat_resolves_its_harness_and_its_profile(root: Path):
    write(
        harnesses_dir(root) / "claude-code.yaml",
        "adapter: harness\nimage: claude-sandbox:2.1.252\nenv: {CLAUDE_PERMISSION_MODE: bypassPermissions}\n",
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
    assert (seat.adapter, seat.image) == ("harness", "claude-sandbox:2.1.252")
    assert seat.env == {"CLAUDE_PERMISSION_MODE": "bypassPermissions"}
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
    (plugin,) = config.tiers["executor"].equipment

    assert (plugin.kind, plugin.source, plugin.ref) == (
        "plugin",
        "github:JuliusBrussee/caveman",
        "81536f57b330",
    )


# ....................... #
# One home per key (S-0061/I-2): the other two files refuse it by name


@pytest.mark.parametrize(
    ("key", "value", "where"),
    [
        ("adapter", "harness", "harness manifest"),
        ("kinds", "[plugin]", "harness manifest"),
        ("image", "img:1", "harness manifest"),
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


def test_a_missing_profile_names_the_directory_and_what_answers_in_it(root: Path):
    """The names, not the filenames (S-0061/D-12): what a seat may write is what
    the directory answers to, and a file declaring `name` answers to that."""

    write(agents_dir(root) / "existing.yaml", "equipment: []\n")
    write(agents_dir(root) / "on-disk.yaml", "name: declared\nequipment: []\n")

    with pytest.raises(AgentError, match="no agent profile named 'missing'") as excinfo:
        load_profile(root, "missing")

    assert str(agents_dir(root)) in str(excinfo.value)
    assert "declared, existing" in str(excinfo.value)
    assert "on-disk" not in str(excinfo.value)


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
    """A real adapter with no provider: the underlying pydantic error, wrapped
    to name the seat and the files that supplied it.

    Not "no command" any more — the shell that starts a harness is the image's
    (S-0063/D-1), and a seat naming no image runs the runtime's default one."""

    write(harnesses_dir(root) / "half.yaml", "adapter: harness\n")

    with pytest.raises(ValueError, match="needs a provider") as excinfo:
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


def test_the_role_default_is_a_profile_that_declares_the_role(root: Path):
    # The filenames deliberately say nothing: the role is the declaration.
    write(agents_dir(root) / "house-build.yaml", ROLE_SKILL % ("implement", "flag-dont-flip"))
    write(agents_dir(root) / "house-check.yaml", ROLE_SKILL % ("review", "ratchet-what-you-build"))

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

    write(agents_dir(root) / "implement.yaml", ROLE_SKILL % ("implement", "flag-dont-flip"))
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
    assert [item.source for item in seat.equipment] == ["github:JuliusBrussee/caveman"]

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


def test_a_filename_that_reads_as_a_role_is_not_one(root: Path):
    """S-0061/D-13. The four roles are ordinary words, so a profile written for
    a reviewer seat and sensibly called `review.yaml` used to become the review
    role's default for every seat in the repository — putting a role nothing
    dispatched into the role sets and into the regime hash, which is what made
    `torve eval` refuse a skill as being in no role set."""

    write(agents_dir(root) / "review.yaml", SKILL % "ratchet-what-you-build")
    write(agents_dir(root) / "careful.yaml", ROLE_SKILL % ("review", "flag-dont-flip"))

    assert {
        role: [item.source for item in items] for role, items in role_equipment(root).items()
    } == {"review": ["torve:flag-dont-flip"]}


def test_a_role_no_profile_declares_has_no_default(root: Path):
    """The mirror failure, and the quieter one: a repository writing its
    defaults in a file the stem lookup did not recognise got no error and no
    equipment. Declared, an unclaimed role is simply absent."""

    write(agents_dir(root) / "implement.yaml", SKILL % "flag-dont-flip")

    assert role_equipment(root) == {}


def test_a_role_two_profiles_claim_is_refused_naming_both(root: Path):
    write(agents_dir(root) / "one.yaml", ROLE_SKILL % ("implement", "flag-dont-flip"))
    write(agents_dir(root) / "two.yaml", ROLE_SKILL % ("implement", "ratchet-what-you-build"))

    with pytest.raises(AgentError, match="both declare role 'implement'") as excinfo:
        role_equipment(root)

    assert "one.yaml" in str(excinfo.value)
    assert "two.yaml" in str(excinfo.value)


def test_a_role_the_engine_never_dispatches_is_refused(root: Path):
    write(agents_dir(root) / "odd.yaml", ROLE_SKILL % ("archivist", "flag-dont-flip"))

    with pytest.raises(AgentError, match="no role the engine dispatches"):
        role_equipment(root)


# ....................... #
# Identity is declared, not read off a filename (S-0061/D-12)


def test_a_declared_name_is_what_a_seat_resolves(root: Path):
    """A file may be called anything; what a seat writes is the name. The seat
    below names neither file by its stem and resolves both."""

    write(harnesses_dir(root) / "01-dsh.yaml", "name: dsh\nadapter: fake\nkinds: [plugin]\n")
    write(
        agents_dir(root) / "profiles.d-heavy.yaml",
        "name: heavy\nequipment: [{kind: plugin, source: 'github:o/r', ref: abc}]\n",
    )
    config = load(root, "tiers:\n  executor:\n    harness: dsh\n    profile: heavy\n")

    assert [item.source for item in config.tiers["executor"].equipment] == ["github:o/r"]


def test_a_name_two_files_claim_is_refused_naming_both(root: Path):
    """The half a directory cannot enforce: a filesystem will not take two
    `twin.yaml`, but it takes two files both writing `name: twin` without
    complaint, and the winner would be whichever `sorted()` reached first."""

    write(harnesses_dir(root) / "a.yaml", "name: twin\nadapter: fake\n")
    write(harnesses_dir(root) / "b.yaml", "name: twin\nadapter: fake\n")

    with pytest.raises(AgentError, match="both named 'twin'") as excinfo:
        load_harness(root, "fake")

    assert "a.yaml" in str(excinfo.value)
    assert "b.yaml" in str(excinfo.value)


def test_identity_never_reaches_the_seat(root: Path):
    """`name` and `role` say which file was read, never what the seat does, so
    they are dropped before the merged body is validated as a `TierConfig`."""

    write(agents_dir(root) / "seated.yaml", "name: seated\nrole: implement\nequipment: []\n")
    # A `TierConfig` is STRICT, so reaching it is the failure this asserts against.
    seat = load(root, "tiers:\n  executor:\n    harness: fake\n    profile: seated\n").tiers[
        "executor"
    ]

    assert not {"name", "role"} & set(type(seat).model_fields)


# ....................... #
# One place a ref moves (S-0062/D-13)

FETCHED = "equipment: [{kind: skill, source: 'github:o/r'%s}]\n"
PIN = "github:o/r: v9.9.9\n"


def test_a_profile_omitting_a_ref_takes_the_pin(root: Path):
    write(root / TORVE_DIR / PINS_FILE, PIN)
    write(agents_dir(root) / "seated.yaml", FETCHED % "")

    assert [item.ref for item in load_profile(root, "seated").equipment] == ["v9.9.9"]


def test_a_profile_writing_its_own_ref_keeps_it(root: Path):
    """The same precedence the seat has over the role, one level further out:
    what a file says beats what it would have inherited."""

    write(root / TORVE_DIR / PINS_FILE, PIN)
    write(agents_dir(root) / "seated.yaml", FETCHED % ", ref: abc123")

    assert [item.ref for item in load_profile(root, "seated").equipment] == ["abc123"]


def test_a_fetched_source_with_neither_is_refused_naming_both(root: Path):
    write(agents_dir(root) / "seated.yaml", FETCHED % "")

    with pytest.raises(AgentError, match="names no ref and no pin") as excinfo:
        load_profile(root, "seated")

    assert "pins.yaml" in str(excinfo.value)


def test_a_pin_gives_a_seat_nothing_it_did_not_ask_for(root: Path):
    """S-0062/A-6 holds: a pin is not a third equipment layer. The pinned source
    is never named by this profile, so nothing about it reaches the seat."""

    write(root / TORVE_DIR / PINS_FILE, PIN)
    write(agents_dir(root) / "bare.yaml", "equipment: []\n")
    seat = load(root, "tiers:\n  executor:\n    harness: fake\n    profile: bare\n")

    assert seat.tiers["executor"].equipment == []


def test_the_seat_resolves_through_the_pin_too(root: Path):
    """Not only `load_profile`: a seat merges the profile's body before any
    `Equipment` is built, and that body is where the ref has to arrive."""

    write(root / TORVE_DIR / PINS_FILE, PIN)
    write(harnesses_dir(root) / "skilled.yaml", "adapter: fake\nkinds: [skill]\n")
    write(agents_dir(root) / "seated.yaml", FETCHED % "")
    seat = load(root, "tiers:\n  executor:\n    harness: skilled\n    profile: seated\n")

    assert [item.ref for item in seat.tiers["executor"].equipment] == ["v9.9.9"]


def test_a_pin_that_is_not_a_ref_is_refused_with_the_file(root: Path):
    path = write(root / TORVE_DIR / PINS_FILE, "github:o/r:\n")

    with pytest.raises(AgentError, match="a ref is a non-empty string") as excinfo:
        pins(root)

    assert str(path) in str(excinfo.value)


def test_no_pins_file_is_no_pins(root: Path):
    assert pins(root) == {}


# ....................... #
# An item carries one directory per harness beside its payload (S-0072/D-1)


def _directions(root: Path) -> None:
    """One harness that reads `.torve/agents/hooks/guard/claude/`, and one that
    reads `.../guard/dsh/` — two readers of one kind, the shape D-1 exists for."""

    write(
        harnesses_dir(root) / "claude.yaml",
        "adapter: fake\nkinds: [hook]\nimage: claude-sandbox:2.1.252\n",
    )
    write(
        harnesses_dir(root) / "dsh.yaml",
        "adapter: fake\nkinds: [hook]\nimage: dsh-sandbox:0.1.1-rc.2\n",
    )
    write(
        agents_dir(root) / "guarded.yaml",
        "equipment: [{kind: hook, source: 'local:.torve/agents/hooks/guard'}]\n",
    )


def test_a_hook_item_carrying_the_seats_directory_loads(root: Path):
    """The payload stays at the item's root and the harness reads its own
    directory: `claude` reads the claude shape, and the item the seat
    declared is still the item the seat got."""

    _directions(root)
    write(root / ".torve" / "agents" / "hooks" / "guard" / "claude" / "settings.json", "{}\n")

    config = load(root, "tiers:\n  executor:\n    harness: claude\n    profile: guarded\n")

    assert [item.kind for item in config.tiers["executor"].equipment] == ["hook"]


def test_a_hook_item_with_no_directory_for_the_seats_harness_is_refused(root: Path):
    """Both manifests declare the `hook` kind, so S-0063/D-4's check has
    nothing to object to — the disagreement is in the item's tree, and this
    is the message that would have stopped T-0391 before an image was pulled."""

    _directions(root)
    write(root / ".torve" / "agents" / "hooks" / "guard" / "claude" / "settings.json", "{}\n")

    load(root, "tiers:\n  executor:\n    harness: claude\n    profile: guarded\n")

    with pytest.raises(ValueError, match="`dsh/`") as excinfo:
        load(root, "tiers:\n  executor:\n    harness: dsh\n    profile: guarded\n")

    message = str(excinfo.value)
    assert "guarded" in message  # the profile
    assert "dsh" in message  # the manifest
    assert str(root / ".torve" / "agents" / "hooks" / "guard" / "dsh") in message


def test_a_harness_that_names_no_sandbox_names_no_directory(root: Path):
    """The per-harness directory is the sandbox the image was built from
    (S-0063/D-6). A harness with no image answers no harness, so it requires
    none — which is what keeps a shapeless seat declarable."""

    write(
        harnesses_dir(root) / "bare.yaml",
        "adapter: fake\nkinds: [hook]\n",
    )
    write(
        agents_dir(root) / "guarded.yaml",
        "equipment: [{kind: hook, source: 'local:.torve/agents/hooks/guard'}]\n",
    )

    config = load(root, "tiers:\n  executor:\n    harness: bare\n    profile: guarded\n")

    assert [item.kind for item in config.tiers["executor"].equipment] == ["hook"]


def test_a_fetched_hook_item_is_not_checkable_at_load(root: Path):
    """Only `local:` payload is in the repository the config was read from; a
    fetched hook's tree does not exist until `torve equip` runs, so the load
    check asks nothing of it — equip is where that item's shape is read."""

    write(
        harnesses_dir(root) / "claude.yaml",
        "adapter: fake\nkinds: [hook]\nimage: claude-sandbox:2.1.252\n",
    )
    write(
        agents_dir(root) / "guarded.yaml",
        "equipment:\n- {kind: hook, source: 'github:o/hooks', ref: abc123}\n",
    )

    config = load(root, "tiers:\n  executor:\n    harness: claude\n    profile: guarded\n")

    assert [item.source for item in config.tiers["executor"].equipment] == ["github:o/hooks"]


# ....................... #
# A manifest says which dialects its harness speaks (S-0064/D-4, S-0064/D-9)


def test_a_manifest_declares_its_dialects(root: Path):
    write(harnesses_dir(root) / "both.yaml", "adapter: api\napi: [openai, anthropic]\n")

    assert load_harness(root, "both").api == ["openai", "anthropic"]


def test_a_dialect_the_engine_has_no_name_for_is_refused(root: Path):
    write(harnesses_dir(root) / "odd.yaml", "adapter: api\napi: [grpc]\n")

    with pytest.raises(AgentError, match="no dialect this engine knows"):
        load_harness(root, "odd")


def test_a_manifest_naming_a_credential_is_refused_with_where_it_went(root: Path):
    """A credential is a property of the provider, which names its own `key_env`
    — a harness dials whatever it is pointed at, and which key opens the door
    was never a fact about the dialer (S-0064/D-9)."""

    write(harnesses_dir(root) / "old.yaml", "adapter: api\napi_key_env: [FOO]\n")

    with pytest.raises(AgentError, match="`key_env` on the provider record") as excinfo:
        load_harness(root, "old")

    assert ".torve/providers/" in str(excinfo.value)
