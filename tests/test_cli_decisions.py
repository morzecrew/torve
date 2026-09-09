"""`torve decisions paths` states coverage per glob beside the rows the
record holds (D-53.6): governed, ungoverned or retired, from the corpus and
its archive, with a corpus that does not load answering nothing rather than
failing the record read."""

from __future__ import annotations

import json
from pathlib import Path

from test_decisions import PHASE, archived, corpus, document
from typer.testing import CliRunner

from torve.cli import app

runner = CliRunner()

# ----------------------- #


def test_paths_prints_coverage_per_glob(tmp_path: Path) -> None:
    rfc_dir = corpus(
        tmp_path,
        **{
            "0001": document(
                "0001", [("D-1.1", "LOCKED", "x", "`src/torve/domain/**`")], phasing=[PHASE]
            )
        },
    )
    archived(
        rfc_dir,
        "0000",
        document("0000", [("D-0.1", "LOCKED", "x", "`src/old/**`")], status="superseded"),
    )

    result = runner.invoke(
        app,
        [
            "decisions",
            "paths",
            "lab",
            "src/torve/domain/task.py",
            "src/torve/cli/x.py",
            "src/old/x.py",
            "src/none/x.py",
            "--root",
            str(tmp_path),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    assert payload["coverage"] == {
        "src/torve/domain/task.py": "governed",
        "src/torve/cli/x.py": "governed",
        "src/old/x.py": "retired",
        "src/none/x.py": "ungoverned",
    }


def test_paths_text_rendering_names_the_frontier(tmp_path: Path) -> None:
    corpus(tmp_path, **{"0001": document("0001", [("D-1.1", "OPEN", "x", "—")])})

    result = runner.invoke(
        app, ["decisions", "paths", "lab", "src/anything.py", "--root", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    assert "src/anything.py: ungoverned" in result.output


def test_a_corpus_that_does_not_load_answers_no_coverage(tmp_path: Path) -> None:
    corpus(tmp_path, **{"0001": document("0001", [("D-1.1", "MAYBE", "x", "—")])})

    result = runner.invoke(
        app,
        ["decisions", "paths", "lab", "src/x.py", "--root", str(tmp_path), "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["coverage"] == {}


# ----------------------- #
# RFC 0057 D-57.8: `decisions import --check` sees every execution file


def test_import_check_lists_the_landings_the_record_lacks(tmp_path: Path) -> None:
    landing = {
        "task": "T-0001",
        "commit": "abc",
        "at": "2026-09-09",
        "entries": [
            {
                "decision": "D-1.1",
                "grade": "LOCKED",
                "claim": "held",
                "evidence": "src/a.py:1 - x",
                "action": "decided",
            }
        ],
    }
    corpus(
        tmp_path,
        **{
            "0001": document(
                "0001",
                [("D-1.1", "LOCKED", "A rule.", "`src/a.py`")],
                implementation="none",
                landings=[landing],
            )
        },
    )
    (tmp_path / ".torve" / "config.yaml").write_text("schema_version: 1\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "decisions",
            "import",
            "morzecrew/x",
            "--check",
            "--root",
            str(tmp_path),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    kinds = [(e["kind"], e["subject"]) for e in json.loads(result.output)["events"]]
    assert ("divergence.recorded", "T-0001") in kinds
    assert ("landing.recorded", "T-0001") in kinds
