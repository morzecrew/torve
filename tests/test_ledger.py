"""`torve ledger` — the five behaviours the tests sections ask a fixture to
prove: the join through git history, the exclusions a rate rests on, the
unpriced seat that reports unreported rather than zero, the unjoinable row
that enters no denominator, and the per-line rates S-0075/D-3 divides by the
diff the landing commits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
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


# ....................... #


def _diffed_row(
    task: str,
    base: str,
    head: str,
    *,
    cost: float | None = 3.0,
    wall: float = 150.0,
    cache_read: float | None = 300000,
    tool_calls: float | None = 15,
) -> dict[str, Any]:
    """The fixture's attempt against a real base..head pair, so the diff the
    landing commits can be read at all — the one field `_row` cannot set,
    because its two shas are one."""

    agent: dict[str, Any] = {
        "tier": "sonnet",
        "image": "claude-sandbox",
        "adapter": "claude",
        "cost_usd": cost,
        "wall_time_s": wall,
    }

    if cache_read is not None:
        agent["cache_read_tokens"] = cache_read

    if tool_calls is not None:
        agent["burn"] = {"tool_calls": tool_calls}

    return {
        "schema_version": 1,
        "at": "2026-01-01T02:00:00Z",
        "task_id": task,
        "merge_base": base,
        "head": head,
        "agent": agent,
        "results": [],
    }


def test_per_line_rates_divide_by_the_diff_the_landing_commits(repo: Repo) -> None:
    """S-0075/D-3, LOCKED: cache-read tokens, wall seconds, tool calls and
    dollars are reported per changed line and per file in scope, divided by
    the diff the landing commits, beside the per-task rates."""

    repo.seed()
    repo.write(".build/base.md", ".\n")
    repo.commit("before the work")
    base = _head(repo)

    repo.write("src/new.py", "a\nb\nc\n")
    repo.write("docs/guide.md", "x\ny\n")
    repo.commit("the work")
    head = _head(repo)

    _landing(repo, "T-0001")
    _stream(repo, [_diffed_row("T-0001", base, head)])

    seat = ledger_report(repo.root)["seats"][0]
    files = {file["path"]: file for file in seat["files"]}

    assert seat["changed_lines"] == 5
    assert seat["cache_read_tokens_per_line"] == pytest.approx(300000 / 5)
    assert seat["wall_time_s_per_line"] == pytest.approx(150.0 / 5)
    assert seat["tool_calls_per_line"] == pytest.approx(15 / 5)
    assert seat["cost_usd_per_line"] == pytest.approx(3.0 / 5)

    assert files["src/new.py"]["lines"] == 3
    assert files["src/new.py"]["cache_read_tokens_per_line"] == pytest.approx(300000 / 3)
    assert files["src/new.py"]["cost_usd_per_line"] == pytest.approx(3.0 / 3)

    assert files["docs/guide.md"]["lines"] == 2
    assert files["docs/guide.md"]["cache_read_tokens_per_line"] == pytest.approx(300000 / 2)


def test_a_landing_that_changed_nothing_has_no_per_line_rate(repo: Repo) -> None:
    """S-0075's tests section: the per-line columns where the denominator is
    zero — an attempt that changed nothing (an empty base..head diff) reports
    no rate rather than an infinity."""

    repo.seed()
    repo.write(".build/base.md", ".\n")
    repo.commit("base")
    head = _head(repo)

    # The attempt landed no change at all: the diff between its own base and
    # head is empty, and that is the zero denominator.
    _landing(repo, "T-0001")
    _stream(repo, [_diffed_row("T-0001", head, head)])

    seat = ledger_report(repo.root)["seats"][0]

    assert seat["changed_lines"] == 0
    assert seat["cache_read_tokens_per_line"] is None
    assert seat["wall_time_s_per_line"] is None
    assert seat["tool_calls_per_line"] is None
    assert seat["cost_usd_per_line"] is None


def test_an_unmeasured_numerator_stays_unreported_per_line(repo: Repo) -> None:
    """S-0004/D-6's unreported-stays-unreported regime in the per-line rates: a
    seat whose harness reported no token counts, no burn profile and no price
    has no per-line rate for them, never a zero."""

    repo.seed()
    repo.write(".build/base.md", ".\n")
    repo.commit("before the work")
    base = _head(repo)

    repo.write("src/new.py", "a\nb\nc\n")
    repo.commit("the work")
    head = _head(repo)

    _landing(repo, "T-0001")
    _stream(repo, [_diffed_row("T-0001", base, head, cost=None, cache_read=None, tool_calls=None)])

    seat = ledger_report(repo.root)["seats"][0]

    assert seat["changed_lines"] == 3
    assert seat["cache_read_tokens_per_line"] is None
    assert seat["tool_calls_per_line"] is None
    assert seat["cost_usd_per_line"] is None


def _traced_row(task: str, ref: str) -> dict[str, Any]:
    """An attempt that left a trace behind, beside the cruder block it
    recorded at the time — the cache the trace is read against."""

    row = _row(task)
    row["agent"]["trace_ref"] = ref
    row["agent"]["burn"] = {"tool_calls": 2, "profile": {"calls": 2, "classes": {"other": 2}}}

    return row


# One attempt's stream as the durable store keeps it: an orientation grep and
# an edit, with the in-sandbox absolute path a harness logs.
TRACE = "".join(
    json.dumps(line) + "\n"
    for line in (
        {"type": "system", "subtype": "init", "tools": ["Grep", "Edit"]},
        {
            "type": "assistant",
            "message": {
                "id": "msg_1",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "Grep",
                        "input": {"pattern": "burn"},
                    }
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "id": "msg_2",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_2",
                        "name": "Edit",
                        "input": {"file_path": "/work/src/new.py"},
                    }
                ],
            },
        },
    )
)


def test_the_ledger_classifies_the_retained_traces_rather_than_the_record(repo: Repo) -> None:
    """S-0075/D-6, LOCKED: the profile is derived from the attempt's retained
    trace when it is read; the recorded block is a cache of that derivation,
    and the trace is what settles a disagreement between them."""

    repo.seed()
    repo.write(".torve/traces/T-0001.1.jsonl", TRACE)
    _stream(repo, [_traced_row("T-0001", ".torve/traces/T-0001.1.jsonl")])

    result = CliRunner().invoke(app, ["ledger", "--root", str(repo.root), "--format", "json"])

    assert result.exit_code == 0, result.output
    burn = json.loads(result.stdout)["burn"]

    assert burn["sources"] == {"trace": 1, "recorded": 0, "absent": 0}
    # The attempt recorded two calls it called `other`; reading its own trace
    # again with the classifier this command carries disagrees, and the
    # disagreement is counted rather than lost.
    assert burn["cached"] == 1
    assert burn["reclassified"] == 1
    assert burn["with_edit"] == 1
    assert burn["median_calls_before_first_edit_share"] == pytest.approx(0.5)


def test_an_attempt_whose_trace_is_gone_keeps_the_profile_it_recorded(repo: Repo) -> None:
    """The recorded block is the cache, and a cache is what answers once the
    trace it came from has been retained away — never a silent absence."""

    repo.seed()
    _stream(repo, [_traced_row("T-0001", ".torve/traces/T-0001.1.jsonl"), _row("T-0002")])

    burn = json.loads(
        CliRunner().invoke(app, ["ledger", "--root", str(repo.root), "--format", "json"]).stdout
    )["burn"]

    assert burn["sources"] == {"trace": 0, "recorded": 1, "absent": 1}
    assert burn["profiled"] == 1
    # Nothing was reclassified: there was no trace to settle anything with.
    assert burn["reclassified"] == 0


def test_the_command_prints_the_burn_population(repo: Repo) -> None:
    repo.seed()
    repo.write(".torve/traces/T-0001.1.jsonl", TRACE)
    _stream(repo, [_traced_row("T-0001", ".torve/traces/T-0001.1.jsonl")])

    result = CliRunner().invoke(app, ["ledger", "--root", str(repo.root)])

    assert result.exit_code == 0, result.output
    assert "burn profiles" in result.stdout
    assert "from trace" in result.stdout
    # Rates, not rows, here too: the population is printed, no attempt is.
    assert "T-0001" not in result.stdout


def test_the_command_prints_the_per_line_rates(repo: Repo) -> None:
    """The verb prints the rates D-3 adds — per changed line and per file in
    scope — beside the per-task table, without listing attempt rows."""

    repo.seed()
    repo.write(".build/base.md", ".\n")
    repo.commit("before the work")
    base = _head(repo)

    repo.write("src/new.py", "a\nb\nc\n")
    repo.commit("the work")
    head = _head(repo)

    _landing(repo, "T-0001")
    _stream(repo, [_diffed_row("T-0001", base, head)])

    result = CliRunner().invoke(app, ["ledger", "--root", str(repo.root)])

    assert result.exit_code == 0, result.output
    assert "per changed line" in result.stdout
    assert "per file in scope" in result.stdout
    assert "src/new.py" in result.stdout
    assert "100,000" in result.stdout
