"""RFC 0005 phase 3: the seeded-defect corpus command — expected findings
matched against the reviewer's output, a dropped catch or an invented
blocker on a clean case exiting red."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from test_run_loop import MockRuntime
from typer.testing import CliRunner

import torve.cli.review as review_cli
from torve.application.ports import AgentResult
from torve.cli.main import app


class ScriptedReviewer:
    kind = "api"

    def __init__(self, output: str) -> None:
        self.output = output

    def run(self, ctx):
        return AgentResult(exit_code=0, output=self.output)


def seed_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    case = root / ".torve" / "review-corpus" / "seeded"
    (case / "tree" / "src").mkdir(parents=True)
    (root / ".torve" / "config.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (case / "case.yaml").write_text(
        yaml.safe_dump(
            {
                "intent": "Add the widget.",
                "decisions": [],
                "expect": [{"severity": "blocker", "claim_contains": "swallow"}],
            }
        ),
        encoding="utf-8",
    )
    (case / "diff.patch").write_text("diff --git a/src/app.py b/src/app.py\n", encoding="utf-8")
    (case / "tree" / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    return root


def invoke_corpus(root: Path, monkeypatch, reviewer_output: str):
    import torve.cli.run as run_cli

    monkeypatch.setattr(
        run_cli, "build_reviewer_agent", lambda config, r: ScriptedReviewer(reviewer_output)
    )
    monkeypatch.setattr(review_cli, "runtime_for", lambda config, name: MockRuntime())
    return CliRunner().invoke(app, ["review", "corpus", "--root", str(root), "--format", "json"])


def test_a_caught_seeded_defect_passes(tmp_path, monkeypatch):
    root = seed_corpus(tmp_path)
    output = json.dumps(
        {
            "findings": [
                {
                    "severity": "blocker",
                    "claim": "the loop swallows every exception",
                    "evidence": "src/app.py:1 — the bare except",
                }
            ]
        }
    )
    result = invoke_corpus(root, monkeypatch, output)
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["cases"][0]["caught"] == 1


def test_a_dropped_catch_is_a_regression(tmp_path, monkeypatch):
    root = seed_corpus(tmp_path)
    result = invoke_corpus(root, monkeypatch, json.dumps({"findings": []}))
    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert report["cases"][0]["missed"]


def test_an_invented_blocker_on_a_clean_case_is_a_regression(tmp_path, monkeypatch):
    root = seed_corpus(tmp_path)
    case = root / ".torve" / "review-corpus" / "seeded" / "case.yaml"
    case.write_text(
        yaml.safe_dump({"intent": "Clean.", "decisions": [], "expect": []}), encoding="utf-8"
    )
    output = json.dumps(
        {
            "findings": [
                {
                    "severity": "blocker",
                    "claim": "invented objection",
                    "evidence": "src/app.py:1 — nothing wrong here",
                }
            ]
        }
    )
    result = invoke_corpus(root, monkeypatch, output)
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["cases"][0]["false_blockers"]


# RFC 0036 phase 2 (T-0256): `torve review corpus add <fixing-commit>` —
# the escape pair scaffolded into an entry whose finding is a person's to
# write, and whose unwritten placeholder the loader refuses. The replay's
# old spellings must survive the group that now hosts `add`.


def git(root: Path, *args: str) -> str:
    import subprocess

    proc = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=operator@example.com",
            "-c",
            "user.name=Operator",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def escape_repo(tmp_path: Path) -> Path:
    """A history with one escape: T-0142 ships a fetch that swallows every
    exception, and HEAD fixes it, citing the landing with a Torve-Fixes
    trailer."""

    root = tmp_path / "proj"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def fetch():\n    return 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "seed work (T-0001)\n\nTorve-Task: T-0001")
    (root / "src" / "app.py").write_text(
        "def fetch():\n    try:\n        return 1\n    except Exception:\n        return None\n",
        encoding="utf-8",
    )
    git(root, "add", ".")
    git(
        root,
        "commit",
        "-qm",
        "torve(T-0142): add retry handling — attempt 1 green\n\nTorve-Task: T-0142",
    )
    (root / "notes.md").write_text("unrelated\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "docs: note")
    (root / "src" / "app.py").write_text("def fetch():\n    return 1  # fixed\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "fix: stop swallowing fetch errors\n\nTorve-Fixes: T-0142")
    return root


def invoke_add(root: Path, ref: str, *extra: str):
    return CliRunner().invoke(app, ["review", "corpus", "add", ref, "--root", str(root), *extra])


def write_finding(root: Path, case: str, needle: str) -> None:
    """What the operator owes the scaffold: the written finding, placeholder gone."""

    case_file = root / ".torve" / "review-corpus" / case / "case.yaml"
    document = yaml.safe_load(case_file.read_text(encoding="utf-8"))
    document["finding"] = "the retry path swallows every exception and returns None"
    document["expect"] = [{"severity": "blocker", "claim_contains": needle}]
    case_file.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def test_add_scaffolds_the_pair_into_a_case(tmp_path):
    root = escape_repo(tmp_path)
    result = invoke_add(root, "HEAD")
    assert result.exit_code == 0, result.output

    case = root / ".torve" / "review-corpus" / "escape-t-0142"
    document = yaml.safe_load((case / "case.yaml").read_text(encoding="utf-8"))
    assert "T-0142" in document["intent"]
    assert "FINDING UNWRITTEN" in document["finding"]
    assert document["expect"][0]["claim_contains"].startswith("FINDING UNWRITTEN")

    diff = (case / "diff.patch").read_text(encoding="utf-8")
    assert "+    except Exception:" in diff

    tree_file = case / "tree" / "src" / "app.py"
    assert "except Exception" in tree_file.read_text(encoding="utf-8")
    assert "# fixed" not in tree_file.read_text(encoding="utf-8")
    assert (case / "tree" / "notes.md").is_file()


def test_add_reports_the_pair_in_json(tmp_path):
    root = escape_repo(tmp_path)
    result = invoke_add(root, "HEAD", "--format", "json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["case"] == "escape-t-0142"
    assert report["defective_commit"] == git(root, "rev-parse", "HEAD~2").strip()
    assert report["fixing_commit"] == git(root, "rev-parse", "HEAD").strip()


def test_a_written_scaffold_is_a_loadable_entry(tmp_path, monkeypatch):
    root = escape_repo(tmp_path)
    assert invoke_add(root, "HEAD").exit_code == 0
    write_finding(root, "escape-t-0142", "except")
    (root / ".torve" / "config.yaml").write_text("schema_version: 1\n", encoding="utf-8")

    output = json.dumps(
        {
            "findings": [
                {
                    "severity": "blocker",
                    "claim": "the loop swallows every exception",
                    "evidence": "src/app.py:2 — the bare except",
                }
            ]
        }
    )
    result = invoke_corpus(root, monkeypatch, output)
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["cases"][0]["case"] == "escape-t-0142"
    assert report["cases"][0]["caught"] == 1


def test_the_unwritten_placeholder_is_refused_by_the_loader(tmp_path, monkeypatch):
    root = escape_repo(tmp_path)
    assert invoke_add(root, "HEAD").exit_code == 0
    (root / ".torve" / "config.yaml").write_text("schema_version: 1\n", encoding="utf-8")

    result = invoke_corpus(root, monkeypatch, json.dumps({"findings": []}))
    assert result.exit_code == 3, result.output
    assert "FINDING UNWRITTEN" in result.output
    assert "escape-t-0142" in result.output


def test_a_commit_without_a_defective_ancestor_is_refused(tmp_path):
    root = escape_repo(tmp_path)
    result = invoke_add(root, "HEAD~1")
    assert result.exit_code == 3, result.output
    assert "Torve-Fixes" in result.output
    assert "--defect" in result.output


def test_defect_outside_the_ancestry_is_refused(tmp_path):
    root = escape_repo(tmp_path)
    git(root, "checkout", "-q", "-b", "later")
    (root / "notes.md").write_text("other branch\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "torve(T-0300): other work\n\nTorve-Task: T-0300")
    git(root, "checkout", "-q", "main")

    result = invoke_add(root, "HEAD", "--defect", "T-0300")
    assert result.exit_code == 3, result.output
    assert "T-0300" in result.output


def test_the_landing_itself_is_not_its_own_defective_ancestor(tmp_path):
    root = escape_repo(tmp_path)
    landing = git(root, "rev-parse", "HEAD~2").strip()
    result = invoke_add(root, landing, "--defect", "T-0142")
    assert result.exit_code == 3, result.output
    assert "before" in result.output


def test_defect_option_and_name_scaffold_beside_the_trailer(tmp_path):
    root = escape_repo(tmp_path)
    result = invoke_add(root, "HEAD~1", "--defect", "T-0142", "--name", "swallowed-fetch")
    assert result.exit_code == 0, result.output

    case = root / ".torve" / "review-corpus" / "swallowed-fetch"
    diff = (case / "diff.patch").read_text(encoding="utf-8")
    assert "+    except Exception:" in diff
    assert "except Exception" in (case / "tree" / "src" / "app.py").read_text(encoding="utf-8")


def test_a_second_add_on_a_taken_case_name_is_refused(tmp_path):
    root = escape_repo(tmp_path)
    assert invoke_add(root, "HEAD").exit_code == 0
    result = invoke_add(root, "HEAD")
    assert result.exit_code == 3, result.output
    assert "escape-t-0142" in result.output


def test_the_replay_keeps_its_spellings(tmp_path, monkeypatch):
    root = seed_corpus(tmp_path)
    output = json.dumps(
        {
            "findings": [
                {
                    "severity": "blocker",
                    "claim": "the loop swallows every exception",
                    "evidence": "src/app.py:1 — the bare except",
                }
            ]
        }
    )

    import torve.cli.run as run_cli

    monkeypatch.setattr(run_cli, "build_reviewer_agent", lambda config, r: ScriptedReviewer(output))
    monkeypatch.setattr(review_cli, "runtime_for", lambda config, name: MockRuntime())

    trailing = CliRunner().invoke(
        app, ["review", "corpus", "seeded", "--root", str(root), "--format", "json"]
    )
    assert trailing.exit_code == 0, trailing.output
    assert json.loads(trailing.stdout)["cases"][0]["caught"] == 1

    leading = CliRunner().invoke(
        app, ["review", "corpus", "--format", "json", "seeded", "--root", str(root)]
    )
    assert leading.exit_code == 0, leading.output
    assert json.loads(leading.stdout)["cases"][0]["caught"] == 1

    explicit = CliRunner().invoke(
        app, ["review", "corpus", "case", "seeded", "--root", str(root), "--format", "json"]
    )
    assert explicit.exit_code == 0, explicit.output

    missing = CliRunner().invoke(app, ["review", "corpus", "nope", "--root", str(root)])
    assert missing.exit_code == 3, missing.output
    assert "nope" in missing.output


def test_review_lists_the_pr_verb_beside_the_corpus(tmp_path):
    result = CliRunner().invoke(app, ["review", "--help"])
    assert result.exit_code == 0, result.output
    assert "pr" in result.output
    assert "corpus" in result.output
