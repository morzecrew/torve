"""`torve brief` (S-0067/D-1, S-0067/D-2): the lint, the size estimate, the
standing rows the scope crosses and the contract has not inherited, the pack
written where an attempt would find it, and the battery with its blocking
axes — printed before the work, refusing none of it, on a contract that
lints red.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_decisions import corpus, document
from typer.testing import CliRunner

from torve.cli import app
from torve.gates.sabotage import TASK_ID, base_task

# ----------------------- #

STANDING = [("S-0001/D-1", "LOCKED", "The app module layout is settled.", "`src/**`")]


def _repo_with_a_red_contract(repo) -> Path:
    """A contract the lint refuses twice over — no acceptance command, and it
    allows `src/app.py` without the test file that already exists beside it —
    and a corpus holding one standing row over the same paths it does not
    carry."""

    repo.seed()
    repo.task(base_task(allow=["src/**"]), None)
    corpus(repo.root, **{"0001": document("0001", STANDING)})

    return repo.root / ".torve" / "tasks" / TASK_ID / "contract.yaml"


# ....................... #


def test_a_contract_that_lints_red_is_briefed_and_exits_zero(repo) -> None:
    contract = _repo_with_a_red_contract(repo)

    result = CliRunner().invoke(
        app, ["brief", str(contract), "--root", str(repo.root), "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    brief = json.loads(result.stdout)

    assert brief["task"] == TASK_ID
    assert brief["lint"]["ok"] is False
    assert brief["lint"]["errors"]
    assert brief["size"]["size"] == "ok"
    # The row's paths cross this scope and the contract never carried it.
    assert any("S-0001/D-1" in row for row in brief["uninherited"])

    # The pack is written where an attempt would find it, not only printed.
    pack = Path(brief["pack"])
    assert pack == repo.root / ".torve" / "context"
    assert (pack / "index.md").is_file()
    assert (pack / "gates.json").is_file()

    names = {gate["name"] for gate in brief["gates"]}
    assert "scope" in names
    assert any(gate["state"] == "blocking" for gate in brief["gates"])


def test_the_battery_prints_with_its_blocking_axes_named(repo) -> None:
    contract = _repo_with_a_red_contract(repo)

    result = CliRunner().invoke(app, ["brief", str(contract), "--root", str(repo.root)])

    assert result.exit_code == 0, result.output
    assert "blocking axes:" in result.output
    assert "S-0001/D-1" in result.output


def test_a_task_id_resolves_to_the_contract_the_repository_holds(repo) -> None:
    """S-0067/D-10: the verb addresses work the way `torve run` does — the id
    resolves under the repository's task directory, and the path a draft
    needs keeps working beside it."""

    contract = _repo_with_a_red_contract(repo)

    by_id = CliRunner().invoke(
        app, ["brief", TASK_ID, "--root", str(repo.root), "--format", "json"]
    )
    by_path = CliRunner().invoke(
        app, ["brief", str(contract), "--root", str(repo.root), "--format", "json"]
    )

    assert by_id.exit_code == 0, by_id.output
    assert json.loads(by_id.stdout)["contract"] == str(contract)
    assert json.loads(by_id.stdout) == json.loads(by_path.stdout)


def test_a_contract_that_is_not_there_is_a_configuration_error(repo) -> None:
    repo.seed()

    result = CliRunner().invoke(
        app, ["brief", str(repo.root / "nowhere.yaml"), "--root", str(repo.root)]
    )

    assert result.exit_code == 3
    assert "configuration error" in result.stderr
