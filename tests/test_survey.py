"""The survey (RFC 0031 phase 1): the load-bearing properties pinned here are
the fixture-history outcomes — fired, clean and no-corpus-skip each
represented, the first-parent walk pinned against a merge-heavy history — the
read-only contract (the target tree byte-identical after a run, no `.wt`
residue) and the JSON report shape. A survey is a measurement, so exit 0
on any completed measurement, red history included.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml
from typer.testing import CliRunner

from torve.cli import app

# ----------------------- #

FAKE_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"

# A target manifest: scope constrained to src/**, and acceptance commands the
# survey can actually run inside the clone.
TARGET_MANIFEST = {
    "schema_version": 1,
    "scope": {"allow": ["src/**"], "deny": []},
    "gates": [
        {"name": "scope", "run": "@scope", "state": "blocking", "origin": "structural"},
        {"name": "secrets", "run": "@secrets", "state": "blocking", "origin": "structural"},
        {"name": "no-test-tampering", "run": "@no-test-tampering", "state": "blocking", "origin": "structural"},
        {"name": "decisions-reported", "run": "@decisions-reported", "state": "blocking", "origin": "structural"},
        {"name": "self-audit", "run": "@self-audit", "state": "shadow", "origin": "structural"},
        {
            "name": "acceptance",
            "run": "@task.acceptance",
            "state": "blocking",
            "origin": "structural",
            "commands": ["test -f src/app.py"],
        },
    ],
}

PRODUCT_GATES = ["scope", "secrets", "no-test-tampering", "decisions-reported", "self-audit", "acceptance"]
NO_CORPUS_GATES = ["no-test-tampering", "decisions-reported", "self-audit"]


# ....................... #


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _head(root: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def build_history(tmp_path: Path, manifest: dict | None):
    """A merge-heavy main line with four landings — two clean feature merges,
    one landing that leaks a credential inside `src/`, and one that lands a
    file outside it. Returns (root, {label: sha})."""

    root = tmp_path / "target"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "survey@example.invalid")
    _git(root, "config", "user.name", "Survey Human")

    def write(rel: str, content: str) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(message: str) -> None:
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "--no-gpg-sign", "-m", message)

    if manifest is not None:
        write(".torve/gates.yaml", yaml.safe_dump(manifest, sort_keys=False))

    write("src/app.py", "print('hello')\n")
    write("tests/test_app.py", "def test_app():\n    assert True\n")
    commit("init")
    init = _head(root)

    _git(root, "checkout", "-q", "-b", "feature1")
    write("src/feature1.py", "FEATURE1 = 1\n")
    commit("feat: feature one")
    f1 = _head(root)

    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "-q", "--no-ff", "--no-gpg-sign", "-m", "merge feature one", "feature1")
    landing1 = _head(root)

    _git(root, "checkout", "-q", "-b", "feature2")
    write("src/feature2.py", "FEATURE2 = 2\n")
    commit("feat: feature two")
    f2 = _head(root)

    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "-q", "--no-ff", "--no-gpg-sign", "-m", "merge feature two", "feature2")
    landing2 = _head(root)

    write("src/leak.py", f"KEY = '{FAKE_AWS_KEY}'\n")
    commit("chore: credential in source")
    landing3 = _head(root)

    write("rogue.txt", "outside src/\n")
    commit("chore: rogue file")
    landing4 = _head(root)

    return root, {
        "init": init,
        "f1": f1,
        "landing1": landing1,
        "f2": f2,
        "landing2": landing2,
        "landing3": landing3,
        "landing4": landing4,
    }


def run_survey(root: Path, *args: str):
    return CliRunner().invoke(app, ["survey", "--root", str(root), "--format", "json", *args])


def _outcomes(landing: dict) -> dict[str, str]:
    return {g["name"]: g["outcome"] for g in landing["gates"]}


# ....................... #
# The measurement over fixture history


def test_first_parent_walk_and_outcomes_default_battery(tmp_path):
    """No `.torve/gates.yaml`: the shipped product battery runs under
    manifest defaults. The walk lands the merge commits, never the feature
    branches' own commits; the leaked credential fires secrets; task- and
    log-input gates skip for want of a corpus."""

    root, shas = build_history(tmp_path, manifest=None)
    result = run_survey(root, "--last", "4")

    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)

    assert doc["kind"] == "survey"
    assert doc["branch"] == "main"
    assert doc["last"] == 4
    assert doc["manifest"] == "product-default"

    # First-parent walk, newest first: the side branches' commits never
    # land, their merges do.
    assert [landing["sha"] for landing in doc["landings"]] == [
        shas["landing4"],
        shas["landing3"],
        shas["landing2"],
        shas["landing1"],
    ]
    walked = {landing["sha"] for landing in doc["landings"]}
    assert shas["f1"] not in walked and shas["f2"] not in walked and shas["init"] not in walked

    newest = doc["landings"][0]
    outcomes = _outcomes(newest)
    assert outcomes["scope"] == "pass"  # no manifest -> unconstrained
    assert outcomes["secrets"] == "pass"
    assert outcomes["acceptance"] == "skipped"  # no commands anywhere
    assert outcomes["no-test-tampering"] == "skipped"  # no task, no log
    assert outcomes["decisions-reported"] == "skipped"
    assert outcomes["self-audit"] == "skipped"

    leak = doc["landings"][1]
    outcomes = _outcomes(leak)
    assert outcomes["secrets"] == "fail"  # the leaked credential fired
    assert outcomes["scope"] == "pass"
    # The runner fails fast: after a blocking failure the remaining blocking
    # gates report "not run", shadow gates still run.
    assert outcomes["no-test-tampering"] == "skipped"
    assert outcomes["self-audit"] == "skipped"

    summary = doc["summary"]
    assert summary["landings"] == 4
    assert summary["by_gate"]["secrets"] == {"fired": 1, "clean": 3, "skipped": 0}
    assert summary["by_gate"]["scope"] == {"fired": 0, "clean": 4, "skipped": 0}
    assert summary["by_gate"]["acceptance"] == {"fired": 0, "clean": 0, "skipped": 4}
    # What a corpus would add: exactly the gates that never measured anything
    # and whose silence is the no-task skip — not acceptance, whose skip is
    # structural to the battery itself.
    assert summary["corpus_adds"] == NO_CORPUS_GATES


def test_target_manifest_battery(tmp_path):
    """A target with its own `.torve/gates.yaml` is surveyed with it: the
    constrained scope fires on the rogue file, its acceptance commands run
    inside the clone, and the no-corpus skip is still recorded."""

    root, _shas = build_history(tmp_path, manifest=TARGET_MANIFEST)
    result = run_survey(root, "--last", "4")

    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["manifest"] == "target"

    newest = doc["landings"][0]  # rogue.txt lands
    outcomes = _outcomes(newest)
    assert outcomes["scope"] == "fail"  # rogue.txt sits outside src/**
    assert outcomes["secrets"] == "skipped"  # not reached: scope fired first

    leak = doc["landings"][1]  # the credential lands inside src/
    outcomes = _outcomes(leak)
    assert outcomes["scope"] == "pass"
    assert outcomes["secrets"] == "fail"

    for landing in doc["landings"][2:]:  # the clean merges
        assert _outcomes(landing)["acceptance"] == "pass"  # the target's commands ran in the clone
        assert _outcomes(landing)["secrets"] == "pass"

    summary = doc["summary"]
    assert summary["by_gate"]["scope"] == {"fired": 1, "clean": 3, "skipped": 0}
    assert summary["by_gate"]["secrets"] == {"fired": 1, "clean": 2, "skipped": 1}
    assert summary["corpus_adds"] == NO_CORPUS_GATES


def test_survey_exits_zero_on_fired_history(tmp_path):
    """A red landing is a successful measurement: exit 0 while the report
    carries the failures."""

    root, _shas = build_history(tmp_path, manifest=None)
    result = run_survey(root, "--last", "4")

    assert result.exit_code == 0
    doc = json.loads(result.stdout)
    assert any(g["outcome"] == "fail" for landing in doc["landings"] for g in landing["gates"])


# ....................... #
# The read-only contract (D-31.1)


def _tree_snapshot(root: Path) -> list[tuple[object, ...]]:
    entries: list[tuple[object, ...]] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        rel = Path(dirpath).relative_to(root)
        entries.append(("d", str(rel)))

        for name in filenames:
            path = Path(dirpath) / name
            entries.append(("f", str(path.relative_to(root)), path.read_bytes()))

    return sorted(entries)


def test_survey_is_read_only(tmp_path):
    root, _shas = build_history(tmp_path, manifest=TARGET_MANIFEST)
    before = _tree_snapshot(root)

    result = run_survey(root, "--last", "4")
    assert result.exit_code == 0, result.output

    assert _tree_snapshot(root) == before  # byte-identical, no workspace residue
    assert not (root / ".wt").exists()
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, check=True
    )
    assert status.stdout == ""  # the tree is as clean as it was


# ....................... #
# The JSON report shape


def test_json_report_shape(tmp_path):
    root, shas = build_history(tmp_path, manifest=None)
    result = run_survey(root, "--last", "4")

    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)  # exactly one document, nothing else on stdout

    assert doc["schema_version"] == 1
    assert set(doc) == {"schema_version", "kind", "branch", "last", "manifest", "landings", "summary"}

    landing = doc["landings"][0]
    assert set(landing) == {"sha", "short", "subject", "parent", "gates"}
    assert landing["short"] == landing["sha"][:7]
    assert landing["parent"] == shas["landing3"]  # first parent, not the side branch

    gate = landing["gates"][0]
    assert set(gate) == {"name", "outcome", "state", "duration_s", "exit_code", "output", "no_corpus"}

    summary = doc["summary"]
    assert summary["landings"] == 4
    assert set(summary) == {"landings", "by_gate", "corpus_adds"}
    assert list(summary["by_gate"]) == PRODUCT_GATES  # deterministic order

    for _name, counts in summary["by_gate"].items():
        assert set(counts) == {"fired", "clean", "skipped"}
        assert sum(counts.values()) == summary["landings"]


# ....................... #
# Edges and CLI behaviour


def test_survey_single_commit_repo(tmp_path):
    """A root commit has no base to diff against: the landing is recorded,
    gates do not run, and the measurement still completes with exit 0."""

    root = tmp_path / "solo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "readme.md").write_text("hi\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--no-gpg-sign", "-m", "init")

    result = run_survey(root, "--last", "5")

    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["summary"]["landings"] == 1
    assert doc["landings"][0]["parent"] is None
    assert doc["landings"][0]["gates"] == []
    assert doc["summary"]["corpus_adds"] == []


def test_survey_output_writes_the_report(tmp_path):
    root, _shas = build_history(tmp_path, manifest=None)
    report_path = tmp_path / "survey-report.json"

    result = CliRunner().invoke(
        app,
        [
            "survey",
            "--root",
            str(root),
            "--last",
            "4",
            "--format",
            "json",
            "--output",
            str(report_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == ""  # the report went to the named path
    doc = json.loads(report_path.read_text(encoding="utf-8"))
    assert doc["kind"] == "survey"
    assert doc["manifest"] == "product-default"


def test_survey_bad_manifest_exits_3(tmp_path):
    root, _shas = build_history(tmp_path, manifest=None)
    gates_dir = root / ".torve"
    gates_dir.mkdir(parents=True, exist_ok=True)
    (gates_dir / "gates.yaml").write_text("schema_version: 1\nsope: {}\n", encoding="utf-8")

    result = run_survey(root, "--last", "4")

    assert result.exit_code == 3
    assert "configuration error" in result.stderr


def test_survey_unknown_branch_exits_4(tmp_path):
    root, _shas = build_history(tmp_path, manifest=None)

    result = run_survey(root, "--last", "4", "--branch", "no-such-branch")

    assert result.exit_code == 4
    assert "infrastructure failure" in result.stderr


def test_survey_text_view(tmp_path):
    """The human view renders the per-landing verdicts and the corpus line."""

    root, _shas = build_history(tmp_path, manifest=None)
    result = CliRunner().invoke(
        app, ["survey", "--root", str(root), "--last", "4", "--format", "text"]
    )

    assert result.exit_code == 0, result.output
    assert "landing" in result.stdout
    assert "fired:" in result.stdout
    assert "silent (no corpus): no-test-tampering, decisions-reported, self-audit" in result.stdout
    assert "a corpus would add:" in result.stdout
