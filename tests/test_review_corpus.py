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
