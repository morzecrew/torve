"""S-0028/D-7, S-0061/D-10: `torve doctor` names, per seat, the two files it
was resolved from — informational only, so it can never turn doctor red.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from torve.cli import app
from torve.cli.doctor import _equipment_checks, _profile_checks
from torve.config.agents import agents_dir, harnesses_dir

# ----------------------- #


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _doctor_repo(tmp_path: Path, config: dict) -> Path:
    import yaml

    root = tmp_path / "repo"
    write(root / ".torve" / "config.yaml", yaml.safe_dump({"schema_version": 1, **config}))
    return root


def _seat_repo(tmp_path: Path, config: dict, **profiles: str) -> Path:
    """A repository with the fake harness present and any named profiles
    written, since both files are the repository's now (S-0061/D-10)."""

    root = _doctor_repo(tmp_path, config)
    write(harnesses_dir(root) / "fake.yaml", "adapter: fake\n")

    for name, body in profiles.items():
        write(agents_dir(root) / f"{name}.yaml", body)

    return root


# ....................... #


def _verify_repo(tmp_path: Path, config: dict) -> Path:
    """A repository with the two harnesses a verify seat and an executor seat
    are reached through, beside the configuration that names them."""

    root = _doctor_repo(tmp_path, config)
    write(harnesses_dir(root) / "fake.yaml", "adapter: fake\n")
    write(harnesses_dir(root) / "h.yaml", "adapter: harness\nimage: probe-sandbox\n")
    return root


def test_doctor_reds_a_verifier_on_the_executors_own_model(tmp_path: Path):
    # S-0116/D-3: an independent check on the executor's own model proves
    # nothing the executor would not — red, not a warning.
    from torve.cli.doctor import _verify_check

    root = _verify_repo(
        tmp_path,
        {
            "verify": {"enabled": True},
            "tiers": {
                "executor": {"harness": "h", "provider": "p", "model": "m-1"},
                "executor.verify": {"harness": "h", "provider": "p", "model": "m-1"},
            },
        },
    )

    checks = _verify_check(root, None)

    assert len(checks) == 1
    name, ok, detail = checks[0]
    assert name == "verify" and ok is False
    assert "own model" in detail


def test_the_verify_check_is_silent_with_the_pilot_off_or_a_different_model(tmp_path: Path):
    from torve.cli.doctor import _verify_check

    off = _verify_repo(tmp_path / "off", {"tiers": {}})
    assert _verify_check(off, None) == []

    apart = _verify_repo(
        tmp_path / "apart",
        {
            "verify": {"enabled": True},
            "tiers": {
                "executor": {"harness": "h", "provider": "p", "model": "m-1"},
                "executor.verify": {"harness": "h", "provider": "q", "model": "m-2"},
            },
        },
    )
    assert _verify_check(apart, None) == []


# ....................... #


def test_seat_check_names_the_harness_and_the_profile(tmp_path: Path):
    root = _seat_repo(
        tmp_path,
        {"tiers": {"executor": {"harness": "fake", "profile": "careful"}}},
        careful="equipment: [{kind: skill, source: torve:flag-dont-flip}]\n",
    )

    checks = _profile_checks(root, None)

    assert len(checks) == 1
    name, ok, detail = checks[0]
    assert name == "seat executor"
    assert ok is True
    assert "harness 'fake'" in detail and "profile 'careful'" in detail


def test_seat_check_says_when_the_role_s_own_profile_answers(tmp_path: Path):
    """S-0061/D-11: a seat naming no profile is not a seat with no equipment —
    it is one whose role's profile answers, and the line says so rather than
    going silent."""

    root = _seat_repo(tmp_path, {"tiers": {"executor": {"harness": "fake"}}})

    (_, _, detail) = _profile_checks(root, None)[0]

    assert "the role's own profile" in detail


def test_seat_check_is_silent_with_no_tiers_configured(tmp_path: Path):
    root = _doctor_repo(tmp_path, {"tiers": {}})

    assert _profile_checks(root, None) == []


def test_seat_check_ignores_an_unreferenced_profile_file(tmp_path: Path):
    # S-0028/D-7: unreferenced profiles are not warned about.
    root = _seat_repo(tmp_path, {"tiers": {}}, unused="skills: []\n")

    assert _profile_checks(root, None) == []


def test_equipment_check_names_an_override_tier(tmp_path: Path):
    root = _seat_repo(
        tmp_path,
        {"tiers": {"executor.copywriter": {"harness": "fake", "profile": "copywriter"}}},
        copywriter=(
            "equipment: [{kind: skill, source: torve:prose-voice}]\n"
            "prompt_extras: Docstrings follow the house voice.\n"
        ),
    )

    checks = _equipment_checks(root, None)

    assert len(checks) == 1
    name, ok, detail = checks[0]
    assert name == "equipment executor.copywriter"
    assert ok is True
    assert "skills [prose-voice] (override)" in detail
    assert "prompt extra (1 line)" in detail


def test_equipment_check_is_silent_with_no_override(tmp_path: Path):
    root = _seat_repo(tmp_path, {"tiers": {"executor": {"harness": "fake"}}})

    assert _equipment_checks(root, None) == []


def test_doctor_json_carries_the_seat_line_and_stays_green(tmp_path: Path):
    root = _seat_repo(
        tmp_path,
        {
            "runtime": {"adapter": "opensandbox"},
            "tiers": {"executor": {"harness": "fake", "profile": "careful"}},
        },
        careful="equipment: [{kind: skill, source: torve:flag-dont-flip}]\n",
    )

    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])

    document = json.loads(result.stdout)
    checks = {c["name"]: c for c in document["checks"]}
    assert checks["seat executor"]["ok"] is True
    assert result.exit_code == 0


# ----------------------- #
# S-0057 S-0057/D-5: doctor reddens when a schema lags its model or the ignore
# file lacks a minted pattern


def test_doctor_names_a_lagging_schema_and_a_missing_ignore_pattern(tmp_path: Path):
    from torve.cli.doctor import _init_checks

    root = _doctor_repo(tmp_path, {})
    write(root / ".torve" / "gates.yaml", "schema_version: 1\ngates: []\n")

    before = _init_checks(root, None)

    # never initialised: a hint, not a red — as `spec check` only warns
    assert [(name, ok) for name, ok, _ in before] == [
        ("schemas", True),
        ("ignore", True),
        ("standing", True),
        ("sources", True),
    ]
    assert "not written yet" in before[0][2] and "not written yet" in before[1][2]

    assert CliRunner().invoke(app, ["init", "--root", str(root)]).exit_code == 0
    after = _init_checks(root, None)

    assert [(name, ok) for name, ok, _ in after] == [
        ("schemas", True),
        ("ignore", True),
        ("standing", True),
        ("sources", True),
    ]

    write(root / ".torve" / "schemas" / "gates.json", "{}\n")
    ignore = root / ".torve" / ".gitignore"
    ignore.write_text(ignore.read_text(encoding="utf-8").replace("traces/\n", ""), encoding="utf-8")

    tampered = _init_checks(root, None)

    assert tampered[0][1] is False and "gates.json" in tampered[0][2]
    assert tampered[1][1] is False and "traces/" in tampered[1][2]
    assert "tasks/" not in tampered[1][2]


def test_doctor_warns_when_the_stub_is_missing(tmp_path: Path):
    """S-0100/D-3: `torve init` writes the stub every skill root carries, and
    doctor names a root that lacks it — a hint, never a red, as with the
    schemas a repository has not yet had written."""
    from torve.cli.doctor import _stub_checks

    root = _doctor_repo(tmp_path, {})

    missing = _stub_checks(root)

    assert [(name, ok) for name, ok, _ in missing] == [("stub", True)]
    assert ".claude/skills/torve/SKILL.md" in missing[0][2]
    assert "not written yet" in missing[0][2]

    assert CliRunner().invoke(app, ["init", "--root", str(root)]).exit_code == 0
    present = _stub_checks(root)

    assert [(name, ok) for name, ok, _ in present] == [("stub", True)]
    assert present[0][2] != missing[0][2]
    assert "not written yet" not in present[0][2]


# ----------------------- #
# S-0068/D-2, S-0068/D-3: two statements about what is armed, neither a verdict on
# whether it should be — so neither may ever turn doctor red.


def test_promotion_line_names_what_a_landing_would_skip_with_nothing_configured(tmp_path: Path):
    from torve.cli.doctor import _promotion_check

    # The likelier shape than a written configuration: no `promotion:` block
    # at all, every criterion unset, and until now nothing saying so.
    root = _doctor_repo(tmp_path, {})

    checks = _promotion_check(root, None)

    assert len(checks) == 1
    name, ok, detail = checks[0]
    assert (name, ok) == ("promotion", True)
    assert "auto_merge off" in detail and "no landing leg runs at all" in detail

    for criterion in ("require_ci", "require_review", "approvals", "quiet_window"):
        assert criterion in detail


def test_promotion_line_separates_the_armed_criteria_from_the_unarmed(tmp_path: Path):
    from torve.cli.doctor import _promotion_check

    root = _doctor_repo(
        tmp_path, {"promotion": {"auto_merge": True, "require_review": True, "approvals": 2}}
    )

    (_, ok, detail) = _promotion_check(root, None)[0]

    assert ok is True
    assert "auto_merge on" in detail
    assert "would land without require_ci, quiet_window" in detail
    assert "armed: require_review, approvals" in detail


def test_the_standing_refusal_line_counts_the_refusals_and_names_the_reason(tmp_path: Path):
    from torve.cli.doctor import _standing_refusal_check

    root = _doctor_repo(tmp_path, {})
    write(root / ".torve" / "gates.yaml", "schema_version: 1\ngates: []\n")
    write(
        root / ".torve" / "telemetry.jsonl",
        "".join(
            json.dumps({"kind": "engine", "event": event, "job": job, "error": error}) + "\n"
            for event, job, error in (
                ("standing_instantiate_refused", "flake-quarantine", "crosses a locked row"),
                ("standing_self_disabled", "flake-quarantine", ""),
                ("standing_instantiate_refused", "flake-quarantine", "crosses a locked row still"),
            )
        )
        + "not json\n",
    )

    checks = _standing_refusal_check(root)

    assert len(checks) == 1
    name, ok, detail = checks[0]
    # A statement, not a verdict: a blocked mechanism is the check working.
    assert (name, ok) == ("standing-refused", True)
    assert "flake-quarantine refused instantiation 2 times" in detail
    assert "crosses a locked row still" in detail


def test_the_standing_refusal_line_is_absent_when_nothing_was_refused(tmp_path: Path):
    from torve.cli.doctor import _standing_refusal_check

    root = _doctor_repo(tmp_path, {})

    assert _standing_refusal_check(root) == []


def test_doctor_json_carries_both_statements_and_stays_green(tmp_path: Path):
    root = _seat_repo(
        tmp_path,
        {
            "runtime": {"adapter": "opensandbox"},
            "promotion": {"auto_merge": True, "approvals": 1},
            "tiers": {"executor": {"harness": "fake"}},
        },
    )
    write(root / ".torve" / "gates.yaml", "schema_version: 1\ngates: []\n")
    write(
        root / ".torve" / "telemetry.jsonl",
        json.dumps(
            {
                "kind": "engine",
                "event": "standing_instantiate_refused",
                "job": "flake-quarantine",
                "error": "scope crosses a locked row",
            }
        )
        + "\n",
    )

    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])

    document = json.loads(result.stdout)
    checks = {c["name"]: c for c in document["checks"]}
    assert checks["promotion"]["ok"] is True
    assert (
        "would land without require_ci, require_review, quiet_window"
        in (checks["promotion"]["detail"])
    )
    assert checks["standing-refused"]["ok"] is True
    assert "refused instantiation 1 time" in checks["standing-refused"]["detail"]
    assert result.exit_code == 0


# ----------------------- #
# The toolkit an image is held to is the toolkit's bytes: a projected
# `AGENTS.md` under it is the corpus's, and an adopter's reworded or deleted
# copy must not read as a toolkit the image lacks.


def test_the_toolkit_listing_leaves_the_projection_out(tmp_path: Path):
    from torve.cli.doctor import _TOOLKIT_LIST, _toolkit_tree

    (tmp_path / "run").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("<!-- torve:managed x -->\n", encoding="utf-8")
    (tmp_path / "deep").mkdir()
    (tmp_path / "deep" / "AGENTS.md").write_text("rows\n", encoding="utf-8")

    assert set(_toolkit_tree(tmp_path)) == {"./run"}
    assert "! -name 'AGENTS.md'" in _TOOLKIT_LIST


# ----------------------- #
# S-0099/D-11: a promotion that needs a durable store is red on the mock


def test_doctor_reds_a_promotion_that_needs_a_durable_store(tmp_path: Path):
    """S-0099/D-11: a document unit and a pull-request landing need the
    Postgres store; on the in-process mock doctor is red under either, and a
    local, task-unit promotion is unaffected."""
    from torve.cli.doctor import _store_checks

    for name, config in (
        ("document", {"promotion": {"unit": "document"}}),
        (
            "pull_request",
            {"promotion": {"landing": "pull_request"}, "scm": {"open_pr": True, "repo": "o/n"}},
        ),
    ):
        root = _doctor_repo(tmp_path / name, config)

        check, ok, detail = _store_checks(root, None)[0]
        assert (check, ok) == ("store", False), detail
        assert "postgres store" in detail

        document = json.loads(
            CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"]).stdout
        )
        assert {c["name"]: c["ok"] for c in document["checks"]}["store"] is False

    local = _doctor_repo(tmp_path / "local", {"promotion": {"landing": "local"}})
    assert _store_checks(local, None)[0][1] is True


# ----------------------- #
# S-0095/D-3: a seat dispatch would refuse its provider is red in doctor


def test_route_check_reds_a_seat_whose_provider_is_not_allowed(tmp_path: Path):
    from torve.cli.doctor import _route_checks

    root = _doctor_repo(
        tmp_path,
        {
            "tiers": {
                "executor": {"harness": "fake"},
                "executor.deep": {"harness": "deep", "provider": "deepseek", "model": "m"},
            },
            "providers": {"default": ["anthropic"]},
        },
    )
    write(harnesses_dir(root) / "fake.yaml", "adapter: fake\n")
    write(harnesses_dir(root) / "deep.yaml", "adapter: harness\nimage: probe-sandbox\n")

    checks = _route_checks(root, None)

    assert checks == [
        (
            "route executor.deep",
            False,
            (
                "seat executor.deep: provider 'deepseek' is not permitted for repository "
                "'repo'; allowed: anthropic"
            ),
        )
    ]


# ----------------------- #
# S-0110/D-4: a seat image on another torve than the host is a red check. The
# image is asked through one seam, so a fake image runner answers as docker would.


def test_the_image_version_check_reds_a_seat_running_another_torve(monkeypatch):
    from torve.cli import doctor

    monkeypatch.setattr(doctor, "_in_image", lambda image, command: "0.1.0")

    check = doctor._image_version_check("claude-sandbox:latest", "0.3.2")

    assert check is not None
    name, ok, detail = check
    assert (name, ok) == ("image claude-sandbox:latest", False)
    assert "runs torve 0.1.0" in detail and "0.3.2" in detail


def test_the_image_version_check_is_silent_when_the_versions_agree(monkeypatch):
    from torve.cli import doctor

    monkeypatch.setattr(doctor, "_in_image", lambda image, command: "0.3.2")

    assert doctor._image_version_check("claude-sandbox:latest", "0.3.2") is None


def test_the_image_version_check_is_silent_when_the_image_cannot_answer(monkeypatch):
    """The existence check already names an unusable image; this adds nothing
    when the image answers no version at all."""

    from torve.cli import doctor

    monkeypatch.setattr(doctor, "_in_image", lambda image, command: None)

    assert doctor._image_version_check("claude-sandbox:latest", "0.3.2") is None


def test_the_image_version_check_asks_the_image_for_its_torve_version(monkeypatch):
    from torve.cli import doctor

    seen: dict = {}

    def fake(image: str, command: list) -> str:
        seen["image"], seen["command"] = image, command
        return "0.3.2"

    monkeypatch.setattr(doctor, "_in_image", fake)

    doctor._image_torve_version("claude-sandbox:latest")

    assert seen == {"image": "claude-sandbox:latest", "command": ["torve", "--version"]}


# ----------------------- #
# S-0112/D-6: a `<name>-sandbox` image with no definition beside it is torve's
# own when its labels say so (S-0112/D-5) — the source names this repository and
# the version equals the host's torve. The runtime and the label read are faked,
# the way the version probe above is.


def _published_image_checks(
    tmp_path, monkeypatch, labels, *, image="claude-sandbox:0.3.2", host="0.3.2", pulled=True
):
    from torve.cli import doctor
    from torve.config import runconfig

    root = _doctor_repo(tmp_path, {"runtime": {"adapter": "docker", "image": image}})

    class _Runtime:
        def resolve_image(self, reference: str) -> str | None:
            return "sha256:" + "ab" * 32 if pulled else None

    # The registry answers for an image the runtime does not hold.
    monkeypatch.setattr(doctor, "_registry_digest", lambda reference: "sha256:" + "cd" * 32)

    monkeypatch.setattr("torve.cli.options.runtime_for", lambda config, override: _Runtime())
    monkeypatch.setattr(runconfig, "installed_version", lambda: host)
    monkeypatch.setattr(doctor, "_image_labels", lambda reference: labels)

    return doctor._image_checks(root, None)


def test_a_published_image_with_no_definition_beside_it_is_accepted(tmp_path, monkeypatch):
    from torve.cli.sandbox import PUBLISHED_SOURCE, SOURCE_LABEL, VERSION_LABEL

    labels = {SOURCE_LABEL: PUBLISHED_SOURCE, VERSION_LABEL: "0.3.2"}

    (name, ok, detail) = _published_image_checks(tmp_path, monkeypatch, labels)[0]

    assert (name, ok) == ("image claude-sandbox:0.3.2", True)
    assert "published by torve" in detail


def test_a_published_image_on_another_version_is_red(tmp_path, monkeypatch):
    from torve.cli.sandbox import PUBLISHED_SOURCE, SOURCE_LABEL, VERSION_LABEL

    labels = {SOURCE_LABEL: PUBLISHED_SOURCE, VERSION_LABEL: "0.2.9"}

    (name, ok, detail) = _published_image_checks(tmp_path, monkeypatch, labels)[0]

    assert (name, ok) == ("image claude-sandbox:0.3.2", False)
    assert "ambient regime" in detail


def test_a_published_image_from_another_source_is_red(tmp_path, monkeypatch):
    from torve.cli.sandbox import SOURCE_LABEL, VERSION_LABEL

    labels = {SOURCE_LABEL: "https://github.com/someone/else", VERSION_LABEL: "0.3.2"}

    (name, ok, detail) = _published_image_checks(tmp_path, monkeypatch, labels)[0]

    assert (name, ok) == ("image claude-sandbox:0.3.2", False)
    assert "ambient regime" in detail


def test_a_published_image_not_yet_pulled_says_to_pull_it(tmp_path, monkeypatch):
    """Its labels are read off a local copy: an image only the registry answers
    for is not pulled, not an ambient regime."""

    from torve.cli.sandbox import PUBLISHED_SOURCE, SOURCE_LABEL, VERSION_LABEL

    labels = {SOURCE_LABEL: PUBLISHED_SOURCE, VERSION_LABEL: "0.3.2"}

    (name, ok, detail) = _published_image_checks(tmp_path, monkeypatch, labels, pulled=False)[0]

    assert (name, ok) == ("image claude-sandbox:0.3.2", False)
    assert "pull it" in detail
    assert "ambient regime" not in detail


def test_a_local_image_with_no_definition_and_no_labels_is_red(tmp_path, monkeypatch):
    (name, ok, detail) = _published_image_checks(tmp_path, monkeypatch, {})[0]

    assert (name, ok) == ("image claude-sandbox:0.3.2", False)
    assert "ambient regime" in detail


def test_the_image_labels_are_read_without_running_the_image(monkeypatch):
    import subprocess

    from torve.cli import doctor

    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='{"a": "b"}', stderr="")

    monkeypatch.setattr(doctor.subprocess, "run", run)

    assert doctor._image_labels("claude-sandbox:t") == {"a": "b"}
    assert calls[0] == [
        "docker",
        "image",
        "inspect",
        "--format",
        "{{json .Config.Labels}}",
        "claude-sandbox:t",
    ]


# ----------------------- #
# S-0110/D-2: doctor still runs on a configuration naming an engine outside this
# build — it reports the refusal rather than dying on the same load.


def test_doctor_reports_an_engine_outside_the_range_rather_than_dying(tmp_path, monkeypatch):
    from torve.cli import doctor
    from torve.config import runconfig

    monkeypatch.setattr(runconfig, "installed_version", lambda: "0.2.9")
    root = _doctor_repo(tmp_path, {"engine": "0.3.2"})

    checks = doctor._engine_check(root, None)

    assert checks is not None
    name, ok, detail = checks[0]
    assert (name, ok) == ("engine", False)
    assert ">=0.3.2,<0.4.0" in detail and "0.2.9" in detail


def test_doctor_names_no_engine_line_when_the_range_admits(tmp_path, monkeypatch):
    from torve.cli import doctor
    from torve.config import runconfig

    monkeypatch.setattr(runconfig, "installed_version", lambda: "0.3.5")

    assert doctor._engine_check(_doctor_repo(tmp_path, {"engine": "0.3.2"}), None) is None


def test_the_image_is_asked_through_docker_and_a_failure_is_no_answer(monkeypatch):
    """S-0110/D-4: the probe runs the image's own entrypoint-less command and
    takes only a clean, non-empty answer; a non-zero exit, empty output or no
    runtime at all is no answer, never a version."""

    import subprocess

    from torve.cli import doctor

    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        code, out = answers.pop(0)
        if code is None:
            raise OSError("no docker")
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr="")

    monkeypatch.setattr(doctor.subprocess, "run", run)
    answers = [(0, " 0.3.3.dev7+g25dc21b27\n"), (1, "0.3.2\n"), (0, "  \n"), (None, "")]

    assert doctor._in_image("claude-sandbox:t", ["torve", "--version"]) == "0.3.3.dev7+g25dc21b27"
    assert calls[0] == [
        "docker",
        "run",
        "--rm",
        "--entrypoint",
        "torve",
        "claude-sandbox:t",
        "--version",
    ]
    assert doctor._in_image("claude-sandbox:t", ["torve", "--version"]) is None
    assert doctor._in_image("claude-sandbox:t", ["torve", "--version"]) is None
    assert doctor._in_image("claude-sandbox:t", ["torve", "--version"]) is None


# ----------------------- #
# S-0111/D-6: a nested attempt reaches its model through the host's loopback
# proxy rewritten to the side container's bridge gateway, or doctor says why
# not. A fake docker answers both calls: the gateway lookup and the probe from
# a bridge container.


def _clear_proxies(monkeypatch) -> None:
    from torve.application.ports import PROXY_ENV

    for name in PROXY_ENV:
        for variant in (name, name.upper()):
            monkeypatch.delenv(variant, raising=False)


def _nested_repo(tmp_path: Path, roles: list[str]) -> Path:
    return _doctor_repo(tmp_path, {"runtime": {"docker": "nested", "nested": {"roles": roles}}})


def _fake_docker_runs(monkeypatch, *, answers: bool, seen: list) -> None:
    import subprocess

    from torve.cli import doctor

    def run(argv, **kwargs):
        seen.append((argv, kwargs.get("env") or {}))

        if argv[1] == "network":
            return subprocess.CompletedProcess(argv, 0, stdout="172.17.0.1\n", stderr="")

        return subprocess.CompletedProcess(argv, 0 if answers else 1, stdout="", stderr="")

    monkeypatch.setattr(doctor.subprocess, "run", run)


def test_the_nested_proxy_check_rewrites_a_loopback_proxy_and_probes_the_gateway(
    tmp_path: Path, monkeypatch
):
    from torve.cli import doctor

    _clear_proxies(monkeypatch)
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7890")
    root = _nested_repo(tmp_path, ["gates", "implement"])
    seen: list = []
    _fake_docker_runs(monkeypatch, answers=True, seen=seen)

    checks = doctor._nested_proxy_check(root, None)

    assert [(name, ok) for name, ok, _ in checks] == [("nested-proxy", True)]
    assert "https_proxy=http://172.17.0.1:7890" in checks[0][2]

    probe, env = next((argv, env) for argv, env in seen if argv[1] == "run")
    assert "--network" in probe and "bridge" in probe
    # The URL rides in the environment, named only: never in the argument list.
    assert "TORVE_PROBE_URL" in probe
    assert not any("7890" in arg for arg in probe)
    assert env["TORVE_PROBE_URL"] == "http://172.17.0.1:7890"


def test_the_nested_proxy_check_reds_a_proxy_the_bridge_cannot_reach(tmp_path: Path, monkeypatch):
    from torve.cli import doctor

    _clear_proxies(monkeypatch)
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7890")
    root = _nested_repo(tmp_path, ["gates", "implement"])
    _fake_docker_runs(monkeypatch, answers=False, seen=[])

    checks = doctor._nested_proxy_check(root, None)

    assert [(name, ok) for name, ok, _ in checks] == [("nested-proxy", False)]
    assert "https_proxy=http://172.17.0.1:7890" in checks[0][2]
    assert "bridge gateway 172.17.0.1" in checks[0][2]


def test_the_nested_proxy_check_never_prints_a_proxy_password(tmp_path: Path, monkeypatch):
    from torve.cli import doctor

    _clear_proxies(monkeypatch)
    monkeypatch.setenv("https_proxy", "http://user:hunter2@127.0.0.1:7890")
    root = _nested_repo(tmp_path, ["gates", "implement"])

    for answers in (True, False):
        _fake_docker_runs(monkeypatch, answers=answers, seen=[])

        message = doctor._nested_proxy_check(root, None)[0][2]

        assert "https_proxy=http://172.17.0.1:7890" in message
        assert "hunter2" not in message and "user@" not in message


def test_the_nested_proxy_check_runs_for_gates_alone_and_is_silent_when_nested_is_off(
    tmp_path: Path, monkeypatch
):
    """S-0111/A-2: the gates side daemon pulls its images through the rewritten
    proxy too, so a gates-only configuration is checked; nested off is silent."""

    from torve.cli import doctor

    _clear_proxies(monkeypatch)
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7890")
    _fake_docker_runs(monkeypatch, answers=True, seen=[])

    gates_only = _nested_repo(tmp_path / "gates", ["gates"])
    off = _doctor_repo(tmp_path / "off", {})

    assert [(name, ok) for name, ok, _ in doctor._nested_proxy_check(gates_only, None)] == [
        ("nested-proxy", True)
    ]
    assert doctor._nested_proxy_check(off, None) == []
