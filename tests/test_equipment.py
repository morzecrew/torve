"""What an agent has, declared once (S-0062): a kind, a source, a ref.

The refusals are the surface. A declaration that names no reachable source, or
reaches one at a version nobody wrote down, is refused where it is written —
not discovered by an attempt that ran without its equipment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from torve.config.agents import (
    AgentError,
    agents_dir,
    harnesses_dir,
    load_harness,
    load_profile,
)
from torve.config.equipment import Equipment, merge_equipment, parse_key, skill_names
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
    write(
        harnesses_dir(where) / "claude.yaml",
        "adapter: fake\nequips:\n  skill: --add-dir {path}\n  plugin: --plugin-dir {path}\n",
    )
    return where


def load(root: Path, text: str):
    return load_runner_config(root, write(root / ".torve" / "config.yaml", text))


# ....................... #
# One item is a kind, a source and a ref (S-0062/D-1, S-0062/D-3)


def test_the_three_schemes_and_what_each_says_about_a_ref():
    package = Equipment(kind="skill", source="torve:flag-dont-flip")
    tracked = Equipment(kind="mcp", source="local:.torve/mcp/torve.json")
    fetched = Equipment(kind="plugin", source="github:JuliusBrussee/caveman", ref="81536f57")

    assert (package.scheme, package.locator) == ("torve", "flag-dont-flip")
    assert (tracked.scheme, tracked.locator) == ("local", ".torve/mcp/torve.json")
    assert (fetched.scheme, fetched.ref) == ("github", "81536f57")


@pytest.mark.parametrize(
    ("body", "because"),
    [
        ({"kind": "skill", "source": "flag-dont-flip"}, "names no source"),
        ({"kind": "skill", "source": "npm:some-skill"}, "names no source"),
        ({"kind": "plugin", "source": "github:owner/repo"}, "names no ref"),
        ({"kind": "skill", "source": "torve:flag-dont-flip", "ref": "v1"}, "with the engine"),
        ({"kind": "mcp", "source": "local:mcp.json", "ref": "v1"}, "versioned by git"),
        ({"kind": "skill", "source": "torve:flag-dont-flip", "select": ["a"]}, "nothing to select"),
    ],
)
def test_a_declaration_nobody_could_rebuild_is_refused(body: dict[str, object], because: str):
    """An unpinned fetch is the one S-0061/D-7 took out of the regime hash; a
    second version on a source that has one already could only disagree."""

    with pytest.raises(ValueError, match=because):
        Equipment.model_validate(body)


def test_a_cache_key_is_the_declaration_and_reads_back_reconstructable():
    """S-0062/I-2: a record naming a key names the source and the ref an
    operator wrote, without the declaration being at hand."""

    item = Equipment(kind="plugin", source="github:owner/repo", ref="v4.9.0")

    assert "/" not in item.key
    assert parse_key(item.key) == ("github:owner/repo", "v4.9.0")

    other = Equipment(kind="plugin", source="github:owner/repo", ref="v4.8.0")

    assert item.key != other.key  # two refs, two directories


# ....................... #
# The harness says what it can be given (S-0062/D-2)


def test_a_manifest_naming_a_kind_the_engine_has_no_name_for_is_refused(root: Path):
    write(harnesses_dir(root) / "odd.yaml", "adapter: fake\nequips:\n  widget: --widget {path}\n")

    with pytest.raises(AgentError, match="no equipment kind"):
        load_harness(root, "odd")


def test_a_template_with_nowhere_to_put_the_path_is_refused(root: Path):
    write(harnesses_dir(root) / "flat.yaml", "adapter: fake\nequips:\n  plugin: --plugins-on\n")

    with pytest.raises(AgentError, match="carries no"):
        load_harness(root, "flat")


def test_a_kind_the_harness_cannot_be_told_about_is_refused_naming_both_files(root: Path):
    """The refusal S-0061/D-6 already made for plugins, for every kind: an
    attempt quietly missing its equipment measures a regime nobody configured."""

    write(
        agents_dir(root) / "served.yaml",
        "equipment:\n- kind: mcp\n  source: local:.torve/mcp/torve.json\n",
    )

    with pytest.raises(ValueError, match="mcp") as excinfo:
        load(root, "tiers:\n  executor:\n    harness: claude\n    profile: served\n")

    message = str(excinfo.value)
    assert "served" in message and "claude" in message


def test_a_package_skill_needs_no_flag_because_materialize_is_its_channel(root: Path):
    """S-0062/D-10 only stands if a harness with no `skill` flag still receives
    skills — `materialize` writes them into the worktree and the prompt names
    them. So the refusal is "no way to deliver it", not "no flag for it"."""

    write(
        agents_dir(root) / "plain.yaml",
        "equipment:\n- {kind: skill, source: torve:flag-dont-flip}\n",
    )
    seat = load(root, "tiers:\n  executor:\n    harness: fake\n    profile: plain\n").tiers[
        "executor"
    ]

    assert seat.skills == ["flag-dont-flip"]

    # A skill that has to be fetched has only the flag, so `fake` cannot take it.
    write(
        agents_dir(root) / "fetched.yaml",
        "equipment:\n- kind: skill\n  source: github:owner/skills\n  ref: v1\n",
    )

    with pytest.raises(ValueError, match="no equipment at all"):
        load(root, "tiers:\n  executor:\n    harness: fake\n    profile: fetched\n")


# ....................... #
# Two layers, role then seat (S-0062/D-12)


def test_the_seat_s_profile_is_appended_to_the_role_s_not_substituted_for_it():
    """The defect this closes is T-0325's arriving through the door D-1 opens:
    a seat profile declaring one plugin would replace the role's skills."""

    role = [
        Equipment(kind="skill", source="torve:flag-dont-flip"),
        Equipment(kind="skill", source="torve:ratchet-what-you-build"),
    ]
    seat = [Equipment(kind="plugin", source="github:owner/caveman", ref="abc")]

    assert [item.source for item in merge_equipment(role, seat)] == [
        "torve:flag-dont-flip",
        "torve:ratchet-what-you-build",
        "github:owner/caveman",
    ]


def test_where_both_layers_name_one_source_the_seat_wins():
    role = [Equipment(kind="plugin", source="github:owner/caveman", ref="old")]
    seat = [Equipment(kind="plugin", source="github:owner/caveman", ref="new")]
    (merged,) = merge_equipment(role, seat)

    assert merged.ref == "new"


def test_one_kind_and_one_source_are_two_different_items():
    """The same repository can be a plugin and a skill source at once, so the
    key is both halves."""

    role = [Equipment(kind="skill", source="github:owner/repo", ref="v1")]
    seat = [Equipment(kind="plugin", source="github:owner/repo", ref="v1")]

    assert len(merge_equipment(role, seat)) == 2


# ....................... #
# What the two keys equipment replaced now say (S-0062/D-1)


@pytest.mark.parametrize(
    ("key", "value", "names"),
    [("skills", "[flag-dont-flip]", "kind `skill`"), ("plugins", "[]", "kind `plugin`")],
)
def test_a_folded_key_is_refused_with_what_replaced_it(
    root: Path, key: str, value: str, names: str
):
    """Not "unknown key": the shape moved, and a reader who wrote the old one
    deserves the new one rather than a list of valid keys."""

    write(agents_dir(root) / "old.yaml", f"{key}: {value}\n")

    with pytest.raises(AgentError, match="equipment") as excinfo:
        load_profile(root, "old")

    assert names in str(excinfo.value)


def test_only_package_skills_become_names_the_materializer_resolves():
    """A fetched skill is declarable before it is deliverable: `materialize`
    resolves names the engine ships, and the flag carries the rest (phase 2)."""

    items = [
        Equipment(kind="skill", source="torve:flag-dont-flip"),
        Equipment(kind="skill", source="github:owner/skills", ref="v1"),
        Equipment(kind="plugin", source="github:owner/caveman", ref="abc"),
    ]

    assert skill_names(items) == ["flag-dont-flip"]


def test_a_profile_declares_what_runs_before_the_agent(root: Path):
    """S-0062/D-7: declared here so torve can time it and book its failure as
    its own; phase 3 is where it runs."""

    write(agents_dir(root) / "indexed.yaml", "prepare: uv run repowise init --yes\n")

    assert load_profile(root, "indexed").prepare == "uv run repowise init --yes"


def test_a_subagent_is_a_kind_because_the_harness_has_a_channel_for_it(root: Path):
    """S-0062/A-5: Claude Code takes `--agents <json>` beside `--plugin-dir`,
    `--mcp-config` and `--settings`, so a subagent is a channel like the rest."""

    write(
        harnesses_dir(root) / "sub.yaml",
        "adapter: fake\nequips:\n  agent: --agents {path}\n",
    )
    write(
        agents_dir(root) / "delegating.yaml",
        "equipment:\n- {kind: agent, source: 'local:.torve/subagents/reviewer.json'}\n",
    )
    seat = load(root, "tiers:\n  executor:\n    harness: sub\n    profile: delegating\n").tiers[
        "executor"
    ]

    assert [(item.kind, item.locator) for item in seat.equipment] == [
        ("agent", ".torve/subagents/reviewer.json")
    ]


def test_nothing_declared_means_nothing_attached(root: Path, tmp_path: Path):
    """S-0062/A-6: a seat naming no profile, in a repository writing no role
    profile, runs the bare harness. `torve init` mints none, so this is what a
    repository that asked for nothing gets."""

    from torve.cli.init import expected_profiles

    assert expected_profiles(tmp_path) == {}

    seat = load(root, "tiers:\n  executor:\n    harness: fake\n").tiers["executor"]

    assert seat.equipment == []
    assert seat.skills is None
    assert seat.plugins == []
