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
        "adapter: fake\nkinds: [skill, plugin]\n",
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
    write(harnesses_dir(root) / "odd.yaml", "adapter: fake\nkinds: [widget]\n")

    with pytest.raises(AgentError, match="no equipment kind"):
        load_harness(root, "odd")


def test_a_manifest_still_naming_the_template_map_is_refused(root: Path):
    """S-0063/D-4: `equips` kept the refusal and lost the templates, because a
    flag per kind describes claude and neither of the other two harnesses this
    repository builds. The map's reader is the image's own `equip` now."""

    write(
        harnesses_dir(root) / "flat.yaml",
        "adapter: fake\nequips:\n  plugin: --plugin-dir {path}\n",
    )

    with pytest.raises(AgentError, match="`kinds`, a list"):
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
        "adapter: fake\nkinds: [agent]\n",
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


# ....................... #
# The cache: fetched host-side, mounted read-only (S-0062/D-4, S-0062/D-5)


def test_nothing_is_fetched_while_an_attempt_runs_host_side(tmp_path: Path, monkeypatch) -> None:
    """S-0062/I-1: every fetch is host-side, before the sandbox exists. The
    check is that warming is reachable without a sandbox at all, and that a
    warm cache fetches nothing the second time."""

    from torve.application import equipment as equip_mod

    cache = tmp_path / "cache"
    repo = tmp_path / "repo"
    (repo / "skills" / "house").mkdir(parents=True)
    (repo / "skills" / "house" / "SKILL.md").write_text("---\nname: house\n---\n", encoding="utf-8")
    item = Equipment(kind="skill", source="local:skills/house")

    fetched: list[str] = []
    real = equip_mod._fetch_local
    monkeypatch.setattr(
        equip_mod,
        "_fetch_local",
        lambda i, into, *, root: (fetched.append(i.source), real(i, into, root=root))[1],
    )

    first = equip_mod.warm([item], root=repo, cache=cache)
    second = equip_mod.warm([item], root=repo, cache=cache)

    assert first == second
    assert (first[0] / "SKILL.md").is_file()
    assert fetched == ["local:skills/house"], "a warm cache fetched again"


def test_a_cache_key_is_the_declaration_so_two_refs_are_two_directories(tmp_path: Path) -> None:
    from torve.application.equipment import item_path

    cache = tmp_path / "cache"
    old = Equipment(kind="plugin", source="github:owner/repo", ref="v1")
    new = Equipment(kind="plugin", source="github:owner/repo", ref="v2")

    assert item_path(old, cache) != item_path(new, cache)
    assert item_path(old, cache).parent == cache / "plugin"


def test_the_mount_carries_a_manifest_naming_in_container_paths(tmp_path: Path) -> None:
    """S-0063/D-12: `manifest.json` at the root of the mount, and every path in
    it is where the image will find that item — the image is told where the
    mount landed and does not have to guess."""

    import json

    from torve.application.equipment import EQUIPMENT_MOUNT, MANIFEST, mount_root

    cache = tmp_path / "cache"
    repo = tmp_path / "repo"
    (repo / "skills" / "house").mkdir(parents=True)
    (repo / "skills" / "house" / "SKILL.md").write_text("x\n", encoding="utf-8")

    where = mount_root(
        [Equipment(kind="skill", source="local:skills/house")], root=repo, cache=cache
    )

    assert where is not None
    document = json.loads((where / MANIFEST).read_text(encoding="utf-8"))
    (entry,) = document["items"]

    assert entry["kind"] == "skill"
    assert entry["source"] == "local:skills/house"
    assert entry["path"].startswith(EQUIPMENT_MOUNT + "/")
    # And the bytes are actually there, under the name the manifest gave.
    assert (where / entry["path"].removeprefix(EQUIPMENT_MOUNT + "/") / "SKILL.md").is_file()


def test_a_seat_given_nothing_mounts_nothing(tmp_path: Path) -> None:
    """A seat with no equipment runs the bare harness (S-0062/A-6), so there is
    no mount and `TORVE_EQUIPMENT` names a path that is simply absent — which
    every `equip` script treats as nothing to do."""

    from torve.application.equipment import mount_root

    assert mount_root([], root=tmp_path, cache=tmp_path / "cache") is None


def test_the_regime_reads_keys_and_not_contents(tmp_path: Path) -> None:
    """S-0062/D-8: two checkouts of one tree hash one regime without either
    having fetched anything yet."""

    from torve.application.equipment import regime_keys

    items = [
        Equipment(kind="plugin", source="github:owner/repo", ref="v1"),
        Equipment(kind="skill", source="torve:flag-dont-flip"),
    ]

    assert regime_keys(items) == regime_keys(list(reversed(items))), "order changed the regime"
    assert all("@" in key for key in regime_keys(items))


def test_the_mount_is_reconstructable_from_its_keys(tmp_path: Path) -> None:
    """S-0062/I-2: every item a run used is named by a cache key that resolves
    to a source and a ref an operator wrote."""

    from torve.application.equipment import regime_keys
    from torve.config.equipment import parse_key

    item = Equipment(kind="plugin", source="github:owner/repo", ref="v4.9.0")
    (key,) = regime_keys([item])
    kind, _, rest = key.partition("/")

    assert kind == "plugin"
    assert parse_key(rest) == ("github:owner/repo", "v4.9.0")


def test_an_item_is_named_under_the_mount_as_it_is_named(tmp_path: Path) -> None:
    """A directory name under the mount is not private bookkeeping: dsh refuses
    a skill whose directory is not a valid skill name, and claude puts the name
    in a flag an operator reads. So it is the thing's own name, not its key."""

    from torve.application.equipment import mount_name

    assert mount_name(Equipment(kind="skill", source="local:skills/house-voice")) == "house-voice"
    assert mount_name(Equipment(kind="skill", source="torve:flag-dont-flip")) == "flag-dont-flip"
    assert (
        mount_name(Equipment(kind="plugin", source="github:JuliusBrussee/caveman", ref="abc"))
        == "caveman"
    )


def test_two_items_of_one_name_do_not_collide(tmp_path: Path) -> None:
    """The key disambiguates where the name cannot — two `caveman` from two
    owners are two directories, and the second carries enough of its key to say
    which it is."""

    import json

    from torve.application.equipment import MANIFEST, mount_root

    cache = tmp_path / "cache"
    repo = tmp_path / "repo"

    for owner in ("one", "two"):
        (repo / owner / "caveman").mkdir(parents=True)
        (repo / owner / "caveman" / "SKILL.md").write_text(owner, encoding="utf-8")

    where = mount_root(
        [
            Equipment(kind="skill", source="local:one/caveman"),
            Equipment(kind="skill", source="local:two/caveman"),
        ],
        root=repo,
        cache=cache,
    )

    assert where is not None
    paths = {entry["path"] for entry in json.loads((where / MANIFEST).read_text())["items"]}

    assert len(paths) == 2
    assert sum(1 for one in where.iterdir() if one.is_dir()) == 2


def test_the_bind_is_read_only_in_the_arguments_docker_runs(tmp_path: Path) -> None:
    """S-0062/D-5, at the one place it is actually enforced. Everything the
    read-only argument rests on is this flag: the cache is shared between
    seats, and an attempt that could write to its mount could edit both what it
    was equipped with and the manifest saying what that was."""

    from torve.adapters.runtime.docker import DockerRuntime
    from torve.application.ports import SandboxSpec

    spec = SandboxSpec(
        name="probe",
        image="probe-sandbox",
        labels={},
        timeout_s=60.0,
        readonly_binds={str(tmp_path / "mount"): "/opt/torve/equipment"},
    )
    args = DockerRuntime()._run_args(spec, tmp_path)
    bind = f"{tmp_path / 'mount'}:/opt/torve/equipment:ro"

    assert bind in args
    # And the writable auth volume still is not, so the two channels stay apart.
    assert not any(one.endswith(":/auth:ro") for one in args)


# ....................... #
# `torve equip` (S-0062/D-4) and its audit (S-0062/D-11)


def _equip_repo(tmp_path: Path) -> Path:
    """A repository declaring one local skill on a role and one on a seat."""

    root = tmp_path / "repo"
    # A harness with a skill channel: a `local:` skill is not package data, so
    # the exemption that lets `torve:` through a channel-less harness does not
    # apply and the refusal is right to fire (S-0062/A-3).
    write(harnesses_dir(root) / "fake.yaml", "adapter: fake\nkinds: [skill, plugin]\n")
    write(root / "skills" / "house-voice" / "SKILL.md", "---\nname: house-voice\n---\n")
    write(root / "skills" / "ratchet" / "SKILL.md", "---\nname: ratchet\n---\n")
    write(
        agents_dir(root) / "implement.yaml",
        "equipment: [{kind: skill, source: 'local:skills/house-voice'}]\n",
    )
    write(
        agents_dir(root) / "seated.yaml",
        "equipment: [{kind: skill, source: 'local:skills/ratchet'}]\n",
    )
    write(
        root / ".torve" / "config.yaml",
        "tiers:\n  executor:\n    harness: fake\n    profile: seated\n",
    )
    return root


def test_equip_warms_both_layers(tmp_path: Path, monkeypatch) -> None:
    """S-0062/D-12: a cache warmed for the seats alone would leave every
    attempt fetching the role's, so the verb reads both."""

    import json

    from typer.testing import CliRunner

    from torve.cli.main import app

    root = _equip_repo(tmp_path)
    monkeypatch.setenv("TORVE_EQUIPMENT_CACHE", str(tmp_path / "cache"))

    result = CliRunner().invoke(app, ["equip", "--root", str(root), "--format", "json"])

    assert result.exit_code == 0, result.output
    sources = {item["source"] for item in json.loads(result.stdout)["items"]}

    assert sources == {"local:skills/house-voice", "local:skills/ratchet"}
    assert (tmp_path / "cache" / "skill").is_dir()


def test_equip_check_finds_a_cache_that_does_not_hold_what_its_key_claims(
    tmp_path: Path, monkeypatch
) -> None:
    """S-0062/D-11: the audit reads the pin the fetch recorded, never the
    network — confirming a directory holds what its key says is a different
    thing from deciding what a version means."""

    from typer.testing import CliRunner

    from torve.application.equipment import PIN_FILE, item_path, warm
    from torve.cli.main import app

    root = _equip_repo(tmp_path)
    cache = tmp_path / "cache"
    monkeypatch.setenv("TORVE_EQUIPMENT_CACHE", str(cache))
    write(
        agents_dir(root) / "seated.yaml",
        "equipment: [{kind: plugin, source: 'github:o/r', ref: v1}]\n",
    )

    # The role's own item is warmed for real; only the fetched one is staged,
    # because the audit's subject is the pin a fetch recorded and never a clone.
    warm([Equipment(kind="skill", source="local:skills/house-voice")], root=root, cache=cache)
    where = item_path(Equipment(kind="plugin", source="github:o/r", ref="v1"), cache)
    where.mkdir(parents=True)
    (where / PIN_FILE).write_text("github:o/r@v9\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["equip", "--check", "--root", str(root)])

    assert result.exit_code == 3
    assert "github:o/r" in result.stdout

    (where / PIN_FILE).write_text("github:o/r@v1\n", encoding="utf-8")

    assert CliRunner().invoke(app, ["equip", "--check", "--root", str(root)]).exit_code == 0


def test_a_source_that_cannot_be_fetched_names_itself(tmp_path: Path) -> None:
    """The failure an operator sees when a declaration names something absent:
    the source, and what was looked for."""

    from torve.application.equipment import EquipmentError, warm

    with pytest.raises(EquipmentError, match="does not exist"):
        warm(
            [Equipment(kind="skill", source="local:skills/absent")],
            root=tmp_path,
            cache=tmp_path / "cache",
        )

    with pytest.raises(EquipmentError, match="ships no"):
        warm(
            [Equipment(kind="skill", source="torve:not-a-shipped-skill")],
            root=tmp_path,
            cache=tmp_path / "cache",
        )


def test_a_fetch_that_fails_leaves_no_half_warm_directory(tmp_path: Path, monkeypatch) -> None:
    """A half-fetched clone must never look like a warm one: the next attempt
    would mount it and equip the agent with a partial checkout. The fetch lands
    beside its destination and is renamed into place, so failure leaves nothing."""

    import subprocess

    from torve.application import equipment as equip_mod

    cache = tmp_path / "cache"
    item = Equipment(kind="plugin", source="github:owner/repo", ref="v1")

    def clone_then_fail(args, **kwargs):
        if args[1] == "clone":
            Path(args[-1]).mkdir(parents=True)
            (Path(args[-1]) / "half").write_text("x", encoding="utf-8")

            return subprocess.CompletedProcess(args, 0, "", "")

        return subprocess.CompletedProcess(args, 1, "", "fatal: reference is not a tree")

    monkeypatch.setattr(equip_mod.subprocess, "run", clone_then_fail)

    with pytest.raises(equip_mod.EquipmentError, match="reference is not a tree"):
        equip_mod.warm([item], root=tmp_path, cache=cache)

    assert not equip_mod.item_path(item, cache).exists()


def test_a_fetch_records_the_pin_beside_the_bytes(tmp_path: Path, monkeypatch) -> None:
    """S-0062/D-11: the pin travels with what was fetched, so an audit needs no
    network — which is what makes `--check` a read rather than a resolution."""

    import subprocess

    from torve.application import equipment as equip_mod

    cache = tmp_path / "cache"
    item = Equipment(kind="plugin", source="github:owner/repo", ref="v4.9.0")

    def clone(args, **kwargs):
        if args[1] == "clone":
            Path(args[-1]).mkdir(parents=True)

        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(equip_mod.subprocess, "run", clone)
    (where,) = equip_mod.warm([item], root=tmp_path, cache=cache)

    assert (where / equip_mod.PIN_FILE).read_text(encoding="utf-8").strip() == (
        "github:owner/repo@v4.9.0"
    )
