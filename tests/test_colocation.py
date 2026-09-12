"""The projections beside the code (S-0054/the-projections-beside-the-code): a governed directory
gets a managed section, an ungoverned one none; the operator's text outside
the markers is byte-identical after a rewrite; a hand edit inside the
markers is drift named by file; the root index lists every directory with
a section; removing the last governing row removes the section and an
otherwise-empty file. This test file is also the sabotage twin of the
`spec-projection` gate: the drift case is what reddens it."""

from __future__ import annotations

import json
from pathlib import Path

from test_decisions import corpus, document, place
from typer.testing import CliRunner

from torve.application.colocation import (
    MARK_CLOSE,
    directory_of,
    governed_directories,
    project,
    splice,
    strip_section,
    superseded_rows,
)
from torve.cli import app
from torve.config.spec import load_corpus

runner = CliRunner()

# ----------------------- #


def _tree(tmp_path: Path) -> Path:
    for d in ("src/torve/cli", "src/torve/gates", "src/torve/domain"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
        (tmp_path / d / "x.py").write_text("", encoding="utf-8")

    return tmp_path


PHASE = {
    "phase": 1,
    "title": "one",
    "intent": "Build it.",
    "scope": ["src/torve/domain/**"],
    "acceptance": [],
    "depends_on": [],
}
DETAILS = {"S-0001/D-1": {"check": "pytest tests/test_cli.py"}}
INVARIANTS = [
    {
        "id": "S-0001/I-1",
        "statement": "one lander",
        "paths": ["src/torve/cli/**"],
        "check": "pytest tests/test_lane.py",
    }
]


def _seed(tmp_path: Path) -> Path:
    _tree(tmp_path)

    return corpus(
        tmp_path,
        **{
            "0001": document(
                "0001",
                [
                    (
                        "S-0001/D-1",
                        "LOCKED",
                        "Verbs parse and render only",
                        "`src/torve/cli/**`",
                        "because",
                    ),
                    ("S-0001/D-2", "ASSUMED", "Gates stand alone", "`src/torve/gates/**`"),
                ],
                phasing=[PHASE],
                details=DETAILS,
                invariants=INVARIANTS,
            )
        },
    )


# ----------------------- #


def test_directory_of_a_glob_or_a_file() -> None:
    assert directory_of("src/torve/cli/**") == "src/torve/cli"
    assert directory_of("src/torve/cli/manager.py") == "src/torve/cli"
    assert directory_of("src/torve/cli/") == "src/torve/cli"
    assert directory_of("pyproject.toml") == "."
    assert directory_of("*.md") == "."


def test_a_governed_directory_gets_a_section_and_an_ungoverned_one_none(tmp_path: Path) -> None:
    rfc_dir = _seed(tmp_path)

    projection = project(tmp_path, rfc_dir)

    cli = (tmp_path / "src/torve/cli/AGENTS.md").read_text(encoding="utf-8")

    assert "### S-0001/D-1 — `LOCKED` (Document 0001)" in cli
    assert "- Consequence: because" in cli
    assert (
        "- Check: `pytest tests/test_cli.py` (shadow; runs as `decision:S-0001/D-1`, no log entry owed)"
        in cli
    )
    assert "- **S-0001/I-1**: one lander" in cli
    assert cli.endswith(MARK_CLOSE + "\n")

    gates = (tmp_path / "src/torve/gates/AGENTS.md").read_text(encoding="utf-8")

    assert (
        "S-0001/D-2" in gates and "Touching these paths owes" not in gates
    )  # ASSUMED owes nothing

    # a phase reaches the domain directory: a section with no rows, so the
    # index names it and the file carries only the markers
    assert (tmp_path / "src/torve/domain/AGENTS.md").exists()
    assert not (tmp_path / "src/torve/AGENTS.md").exists()
    assert sorted(projection.written) == [
        "AGENTS.md",
        "src/torve/cli/AGENTS.md",
        "src/torve/domain/AGENTS.md",
        "src/torve/gates/AGENTS.md",
    ]

    root = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    assert "## Governed directories" in root
    assert "- `src/torve/cli/` — 1 decision(s)" in root
    assert "- `src/torve/domain/` — 0 decision(s)" in root


def test_text_outside_the_markers_survives_byte_for_byte(tmp_path: Path) -> None:
    rfc_dir = _seed(tmp_path)
    own = "# My notes\n\nKeep this.\n"
    (tmp_path / "src/torve/cli/AGENTS.md").write_text(own, encoding="utf-8")

    project(tmp_path, rfc_dir)
    first = (tmp_path / "src/torve/cli/AGENTS.md").read_text(encoding="utf-8")

    assert first.startswith(own)

    project(tmp_path, rfc_dir)  # idempotent

    assert (tmp_path / "src/torve/cli/AGENTS.md").read_text(encoding="utf-8") == first
    assert project(tmp_path, rfc_dir, check=True).ok


def test_a_hand_edit_inside_the_markers_is_drift_named_by_file(tmp_path: Path) -> None:
    rfc_dir = _seed(tmp_path)
    project(tmp_path, rfc_dir)
    path = tmp_path / "src/torve/cli/AGENTS.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("because", "because I said so"), encoding="utf-8"
    )

    checked = project(tmp_path, rfc_dir, check=True)

    assert checked.drifted == ["src/torve/cli/AGENTS.md"]
    assert not checked.ok

    result = runner.invoke(
        app, ["spec", "project", "--check", "--root", str(tmp_path), "--format", "json"]
    )

    assert result.exit_code == 3
    assert json.loads(result.output)["drifted"] == ["src/torve/cli/AGENTS.md"]


def test_removing_the_last_governing_row_removes_the_section_and_an_empty_file(
    tmp_path: Path,
) -> None:
    rfc_dir = _seed(tmp_path)
    project(tmp_path, rfc_dir)
    (tmp_path / "src/torve/gates/AGENTS.md").write_text(
        "kept\n" + (tmp_path / "src/torve/gates/AGENTS.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    slim = document(
        "0001", [("S-0001/D-1", "LOCKED", "Verbs parse and render only", "`src/torve/cli/**`")]
    )
    place(rfc_dir, "0001", slim)

    projection = project(tmp_path, rfc_dir)

    assert sorted(projection.removed) == ["src/torve/domain/AGENTS.md", "src/torve/gates/AGENTS.md"]
    assert not (tmp_path / "src/torve/domain/AGENTS.md").exists()
    assert (tmp_path / "src/torve/gates/AGENTS.md").read_text(encoding="utf-8") == "kept\n"
    assert "src/torve/gates/" not in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")


def test_splice_and_strip_are_inverse_around_the_markers() -> None:
    section = "<!-- torve:managed x — rendered from the corpus; do not edit by hand -->\nbody\n<!-- /torve:managed -->\n"
    own = "mine\n"

    spliced = splice(own, section, "x")

    assert spliced == "mine\n\n" + section
    assert strip_section(spliced, "x") == "mine\n"
    assert splice(spliced, section.replace("body", "new"), "x").count("torve:managed x") == 1


# ....................... #
# A row its own document replaced is not projected beside its replacement


def test_a_superseded_row_leaves_the_section_to_its_replacement(tmp_path: Path) -> None:
    """The filter was document-level only: `standing()` skips a superseded
    document, and nothing looked at the row. Two rows over one directory, one
    replacing the other, rendered side by side — and a page has no way to say
    which of them is live."""

    _tree(tmp_path)
    rfc_dir = corpus(
        tmp_path,
        **{
            "0001": document(
                "0001",
                [
                    ("S-0001/D-1", "LOCKED", "The old rule", "`src/torve/cli/**`", "because"),
                    ("S-0001/D-2", "LOCKED", "The rule that replaced it", "`src/torve/cli/**`"),
                ],
                details={"S-0001/D-1": {"superseded_by": "S-0001/D-2"}},
            )
        },
    )
    loaded = load_corpus(rfc_dir)

    assert superseded_rows(loaded) == ["S-0001/D-1"]
    governed = governed_directories(loaded, tmp_path, rfc_dir)

    assert [row.id for _, row in governed["src/torve/cli"]] == ["S-0001/D-2"]

    project(tmp_path, rfc_dir)
    section = (tmp_path / "src/torve/cli/AGENTS.md").read_text(encoding="utf-8")

    assert "The rule that replaced it" in section
    assert "The old rule" not in section
