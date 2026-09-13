"""`torve ledger` — the four behaviours S-0065's tests section asks a fixture to
prove: the join through git history, the exclusions a rate rests on, the
unpriced seat that reports unreported rather than zero, and the unjoinable
row that enters no denominator.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from typer.testing import CliRunner

from torve.application.ledger import contract_at, counted_rows, ledger_report
from torve.cli.main import app
from torve.domain.spec import Landing
from torve.gates.sabotage import Repo

# ----------------------- #


def _row(
    task: str,
    *,
    tier: str = "sonnet",
    image: str = "claude-sandbox",
    adapter: str = "claude",
    cost: float | None = 1.0,
    wall: float = 60.0,
    at: str = "2026-01-01T00:00:00Z",
    sha: str = "deadbee",
    convicted: list[str] | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """One attempt as the stream carries it. `cost` None is the unpriced
    seat: the roster listed no price, so the record holds none."""

    return {
        "schema_version": 1,
        "at": at,
        "task_id": task,
        "merge_base": sha,
        "head": sha,
        **({} if kind is None else {"kind": kind}),
        "agent": {
            "tier": tier,
            "image": image,
            "adapter": adapter,
            "cost_usd": cost,
            "wall_time_s": wall,
        },
        "results": [
            {"name": name, "outcome": "fail", "state": "blocking", "duration_s": 5.0}
            for name in (convicted or [])
        ],
    }


def _stream(repo: Repo, rows: list[dict[str, Any]]) -> None:
    repo.write(".torve/telemetry.jsonl", "".join(json.dumps(row) + "\n" for row in rows))


def _landing(repo: Repo, task: str) -> None:
    """A landing file in the tree — the carrier the ledger counts, written
    through the model the tree's own readers validate against."""

    landing = Landing(task=task, at="2026-01-01T01:00:00Z")
    repo.write(
        f".torve/execution/{landing.file_name()}",
        yaml.safe_dump(landing.model_dump(mode="json"), sort_keys=False),
    )


# ....................... #


def test_contract_resolves_from_history_after_the_tree_loses_it(repo: Repo) -> None:
    """S-0065/D-3: the contract is read at the attempt's own sha whether or not
    it is still on disk. This is the whole finding S-0056/D-10's consequence
    claims and nothing had ever tested."""

    repo.seed()
    repo.write(
        ".torve/tasks/T-0001/contract.yaml",
        yaml.safe_dump(
            {
                "id": "T-0001",
                "spec": "S-0065",
                "scope": {"allow": ["src/**"]},
                "acceptance": ["ok"],
            },
            sort_keys=False,
        ),
    )
    repo.commit("the contract")
    sha = _head(repo)

    (repo.root / ".torve" / "tasks" / "T-0001" / "contract.yaml").unlink()
    repo.commit("archive the task directory")

    found = contract_at(repo.root, "T-0001", sha)

    assert found is not None
    assert found["found_in_tree"] is False
    assert found["spec"] == "S-0065"
    assert found["scope"] == {"allow": ["src/**"]}
    assert found["acceptance"] == ["ok"]

    # An attempt that ran before its contract was committed resolves to
    # nothing, which is a fact about that attempt and not a defect.
    assert contract_at(repo.root, "T-0404", sha) is None


def _head(repo: Repo) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(repo.root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


# ....................... #


def test_fake_adapters_and_shadow_replays_leave_the_denominator(repo: Repo) -> None:
    """S-0065/D-5, LOCKED: only attempts that ran a model count, and the
    exclusion is reported rather than silent."""

    counted, excluded = counted_rows(
        [
            _row("T-0001"),
            _row("T-0002", adapter="fake"),
            _row("T-0003", kind="shadow"),
            _row("T-0004", kind="review"),
            {"schema_version": 1, "kind": "engine", "event": "blocked"},
        ]
    )

    assert [row["task_id"] for row in counted] == ["T-0001"]
    assert excluded["fake_adapter"] == 1
    assert excluded["shadow_replay"] == 1
    # The review row and the engine event are both "not an attempt".
    assert excluded["not_an_attempt"] == 2


def test_a_row_with_no_base_sha_is_unjoinable_and_counted_nowhere(repo: Repo) -> None:
    """S-0065/D-4: a cost and a duration attached to nothing is reported as
    unjoinable rather than quietly moving an average."""

    orphan = _row("T-0009", sha="")
    orphan["merge_base"] = ""
    orphan["head"] = ""

    counted, excluded = counted_rows([_row("T-0001"), orphan])

    assert len(counted) == 1
    assert excluded["no_base_sha"] == 1


# ....................... #


def test_rates_are_per_seat_and_an_unpriced_seat_reports_unreported(repo: Repo) -> None:
    """S-0065/D-1 and S-0065/D-2: one tier on two images is two seats, and the seat
    whose provider carries no price reports no cost — never zero."""

    repo.seed()
    _landing(repo, "T-0001")
    _stream(
        repo,
        [
            _row("T-0001", convicted=["scope"], at="2026-01-01T00:00:00Z"),
            _row("T-0001", at="2026-01-01T02:00:00Z"),
            # Same tier, a different image: a second seat, never blended.
            _row("T-0002", image="codex-sandbox", cost=None),
        ],
    )

    report = ledger_report(repo.root)
    seats = {(seat["tier"], seat["image"]): seat for seat in report["seats"]}

    priced = seats[("sonnet", "claude-sandbox")]
    assert priced["attempts"] == 2
    assert priced["landed_tasks"] == 1
    assert priced["cost_per_landed_task_usd"] == 2.0
    assert priced["attempts_per_landing"] == 2.0
    assert priced["convictions_before_landing"] == 1.0
    # Two hours elapsed, two minutes of agent: the ratio and both its sides.
    assert priced["span_s"] == 7200.0
    assert priced["wall_time_s"] == 120.0
    assert priced["duty_cycle"] == 120.0 / 7200.0

    unpriced = seats[("sonnet", "codex-sandbox")]
    assert unpriced["cost_usd"] is None
    assert unpriced["cost_per_landed_task_usd"] is None
    # It landed nothing, so it has no rate at all — not a zero, not infinity.
    assert unpriced["attempts_per_landing"] is None


def test_gates_report_wall_time_against_convictions(repo: Repo) -> None:
    repo.seed()
    _stream(
        repo, [_row("T-0001", convicted=["scope", "lint"]), _row("T-0002", convicted=["scope"])]
    )

    gates = {gate["gate"]: gate for gate in ledger_report(repo.root)["gates"]}

    assert gates["scope"]["convictions"] == 2
    assert gates["scope"]["runs"] == 2
    assert gates["scope"]["wall_time_s"] == 10.0
    assert gates["scope"]["seconds_per_conviction"] == 5.0
    # Convictions lead the ordering: the cheap gate that never fires sorts last.
    assert [gate["gate"] for gate in ledger_report(repo.root)["gates"]] == ["scope", "lint"]


# ....................... #


def test_the_command_prints_rates_and_says_what_it_excluded(repo: Repo) -> None:
    repo.seed()
    _landing(repo, "T-0001")
    _stream(repo, [_row("T-0001", convicted=["scope"]), _row("T-0002", adapter="fake")])

    result = CliRunner().invoke(app, ["ledger", "--root", str(repo.root)])

    assert result.exit_code == 0, result.output
    # The seat's two halves are one cell; a narrow terminal folds it, so the
    # assertion is on the halves rather than on where rich broke the line.
    assert "sonnet" in result.stdout
    assert "claude-sand" in result.stdout
    assert "1 fake-adapter" in result.stdout
    # Rates, not rows: no attempt, task or gate run is listed.
    assert "T-0001" not in result.stdout


def test_the_json_envelope_carries_the_same_arithmetic(repo: Repo) -> None:
    repo.seed()
    _landing(repo, "T-0001")
    _stream(repo, [_row("T-0001")])

    result = CliRunner().invoke(app, ["ledger", "--root", str(repo.root), "--format", "json"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)

    assert envelope["attempts"] == 1
    assert envelope["landed_tasks"] == 1
    assert envelope["seats"][0]["cost_per_landed_task_usd"] == 1.0


def test_an_empty_repository_answers_rather_than_dividing_by_nothing(
    tmp_path: Path,
) -> None:
    report = ledger_report(tmp_path)

    assert report["attempts"] == 0
    assert report["seats"] == []
    assert report["gates"] == []
