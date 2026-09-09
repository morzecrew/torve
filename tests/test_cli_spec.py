"""`torve spec` (RFC 0054 §5.5): read-only over the worktree's corpus and
archive; `show` answers a row, an invariant or a question and marks an
archived one; `paths` states coverage and the rows governing a path;
`tests` names what proves a row; `why-not` finds the rejected alternative
before it is re-proposed. Nothing here opens the record."""

from __future__ import annotations

import json
from pathlib import Path

from test_decisions import archived, corpus, document
from typer.testing import CliRunner

from torve.cli import app

runner = CliRunner()

DETAILS = {
    "D-1.1": {
        "rationale": "because",
        "cites": ["D-1.2"],
        "check": "pytest tests/test_cli.py",
        "check_twin": "tests/test_cli_sabotage.py",
    }
}
INVARIANTS = [
    {
        "id": "I-1.1",
        "statement": "one lander",
        "paths": ["src/torve/cli/**"],
        "check": "pytest tests/test_lane.py",
    }
]
ALTERNATIVES = [
    {
        "option": "land one candidate per pass",
        "rejected_because": "buys nothing while the lane serialises",
    }
]
QUESTIONS = [{"id": "Q-1.1", "text": "how long a pass holds the base", "status": "open"}]


def _seed(tmp_path: Path) -> Path:
    (tmp_path / "src/torve/cli").mkdir(parents=True)
    rfc_dir = corpus(
        tmp_path,
        **{
            "0001": document(
                "0001",
                [
                    ("D-1.1", "LOCKED", "Verbs parse and render only", "`src/torve/cli/**`"),
                    ("D-1.2", "ASSUMED", "A plain row", "—"),
                ],
                details=DETAILS,
                invariants=INVARIANTS,
                alternatives=ALTERNATIVES,
                questions=QUESTIONS,
            )
        },
    )
    archived(
        rfc_dir,
        "0000",
        document("0000", [("D-0.1", "LOCKED", "An old rule", "`src/old/**`")], status="superseded"),
    )

    return rfc_dir


def _spec(tmp_path: Path, *args: str) -> tuple[int, str]:
    result = runner.invoke(app, ["spec", *args, "--root", str(tmp_path), "--format", "json"])

    return result.exit_code, result.output


# ----------------------- #


def test_show_answers_a_row_with_its_details(tmp_path: Path) -> None:
    _seed(tmp_path)

    code, output = _spec(tmp_path, "show", "D-1.1")
    payload = json.loads(output)

    assert code == 0, output
    assert payload["rationale"] == "because" and payload["cites"] == ["D-1.2"]
    assert payload["check"] == "pytest tests/test_cli.py" and payload["check_state"] == "shadow"
    assert payload["archived"] is False


def test_show_answers_an_archived_row_an_invariant_and_a_question(tmp_path: Path) -> None:
    _seed(tmp_path)

    code, output = _spec(tmp_path, "show", "D-0.1")
    assert code == 0 and json.loads(output)["archived"] is True

    code, output = _spec(tmp_path, "show", "I-1.1")
    assert code == 0 and json.loads(output)["check"] == "pytest tests/test_lane.py"

    code, output = _spec(tmp_path, "show", "Q-1.1")
    assert code == 0 and json.loads(output)["status"] == "open"

    code, _ = _spec(tmp_path, "show", "D-9.9")
    assert code == 3


def test_paths_states_coverage_and_the_governing_rows(tmp_path: Path) -> None:
    _seed(tmp_path)

    code, output = _spec(tmp_path, "paths", "src/torve/cli/rfc.py")
    payload = json.loads(output)

    assert code == 0, output
    assert payload["coverage"] == "governed"
    assert [r["identifier"] for r in payload["decisions"]] == ["D-1.1"]
    assert [i["identifier"] for i in payload["invariants"]] == ["I-1.1"]

    code, output = _spec(tmp_path, "paths", "src/old/thing.py")
    assert json.loads(output)["coverage"] == "retired"

    code, output = _spec(tmp_path, "paths", "src/nowhere.py")
    assert json.loads(output)["coverage"] == "ungoverned"


def test_tests_names_what_proves_a_row(tmp_path: Path) -> None:
    _seed(tmp_path)

    code, output = _spec(tmp_path, "tests", "D-1.1")
    proofs = json.loads(output)["proofs"]

    assert code == 0, output
    assert {"kind": "check", "command": "pytest tests/test_cli.py", "state": "shadow"} in proofs
    assert {"kind": "twin", "path": "tests/test_cli_sabotage.py"} in proofs
    assert any(p["kind"] == "invariant" and p["identifier"] == "I-1.1" for p in proofs)

    code, output = _spec(tmp_path, "tests", "D-1.2")
    assert code == 0 and json.loads(output)["proofs"] == []


def test_why_not_finds_the_rejected_alternative(tmp_path: Path) -> None:
    _seed(tmp_path)

    code, output = _spec(tmp_path, "why-not", "one candidate per pass")
    matches = json.loads(output)["alternatives"]

    assert code == 0, output
    assert len(matches) == 1 and "serialises" in matches[0]["rejected_because"]

    code, output = _spec(tmp_path, "why-not", "unicorns")
    assert json.loads(output)["alternatives"] == []


def test_a_corpus_that_does_not_load_is_a_configuration_error(tmp_path: Path) -> None:
    corpus(tmp_path, **{"0001": document("0001", [("D-1.1", "MAYBE", "x", "—")])})

    code, _ = _spec(tmp_path, "show", "D-1.1")

    assert code == 3
