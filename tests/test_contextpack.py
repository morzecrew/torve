"""The context pack (RFC 0054 §5.6): every builder is a pure function of
(record snapshot, tree, contract) — golden-shaped here — and the pack
never names another task; the red of one attempt reaches the next
(D-54.12) as the gate's output tail, the rows it names and the tests it
lists; a replay omits what reads the stream as it stands now."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from test_decisions import corpus, document

from torve.application.contextpack import (
    PACK_DIR,
    attempts_file,
    build,
    contended_file,
    decisions_file,
    gates_file,
    materialize,
    render_index,
    tests_file,
)
from torve.config.spec import load_corpus
from torve.domain.task import InheritedDecision, Task

# ----------------------- #

DETAILS = {"D-1.1": {"rationale": "because", "check": "pytest tests/test_a.py"}}
AMENDMENTS = [
    {
        "id": "A-9",
        "at": "2026-09-09",
        "title": "regraded",
        "changes": [{"subject": "D-1.1", "field": "grade", "before": "ASSUMED", "after": "LOCKED"}],
        "md": "words",
    }
]


def _task(**extra: object) -> Task:
    return Task(
        id="T-0500",
        rfc="rfcs/0001-document-0001.yaml",
        decisions=[
            InheritedDecision(
                id="D-1.1",
                grade="LOCKED",
                text="A rule.",
                paths=["src/a/**"],
                consequence="it holds",
                check="pytest tests/test_a.py",
            )
        ],
        scope={"allow": ["src/a/**", "src/a/thing.py"], "deny": []},  # type: ignore[arg-type]
        **extra,  # type: ignore[arg-type]
    )


def _seed(tmp_path: Path) -> Path:
    (tmp_path / "src" / "a").mkdir(parents=True)
    (tmp_path / "src" / "a" / "thing.py").write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_thing.py").write_text("", encoding="utf-8")
    text = document(
        "0001",
        [("D-1.1", "LOCKED", "A rule.", "`src/a/**`", "it holds")],
        details=DETAILS,
        amendments=AMENDMENTS,
    )
    other = document("0002", [("D-2.1", "ASSUMED", "Another rule over a.", "`src/a/thing.py`")])

    return corpus(tmp_path, **{"0001": text, "0002": other})


# ----------------------- #


def test_decisions_carry_consequence_rationale_amendments_and_the_standing_set(
    tmp_path: Path,
) -> None:
    rfc_dir = _seed(tmp_path)

    payload = decisions_file(_task(), load_corpus(rfc_dir), rfc_dir)
    (row,) = payload["inherited"]

    assert row["consequence"] == "it holds" and row["rationale"] == "because"
    assert row["amended_by"] == [
        {
            "amendment": "A-9",
            "at": "2026-09-09",
            "field": "grade",
            "before": "ASSUMED",
            "after": "LOCKED",
        }
    ]
    assert [s["id"] for s in payload["standing_over_scope"]] == ["D-2.1"]


def test_gates_list_the_battery_with_axes_and_the_contract_gates(tmp_path: Path) -> None:
    manifest = tmp_path / "gates.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "scope": {"allow": [], "deny": []},
                "gates": [
                    {"name": "scope", "run": "@scope", "state": "blocking", "origin": "structural"},
                    {
                        "name": "lint",
                        "run": "true",
                        "state": "shadow",
                        "origin": "structural",
                        "axis": "form",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = gates_file(tmp_path, _task(), manifest)
    names = [(g["name"], g["axis"], g["state"]) for g in payload["gates"]]

    assert names == [
        ("scope", "functional", "blocking"),
        ("lint", "form", "shadow"),
        ("decision:D-1.1", "compliance", "shadow"),
    ]
    assert "scope.allow" in payload["gates"][0]["convicts_on"]


def test_tests_read_coverage_under_scope_and_the_named_test_files(tmp_path: Path) -> None:
    _seed(tmp_path)
    (tmp_path / "coverage.xml").write_text(
        '<?xml version="1.0"?><coverage><packages><package><classes>'
        '<class filename="a/thing.py"><lines><line number="1" hits="1"/><line number="2" hits="0"/></lines></class>'
        '<class filename="b/other.py"><lines><line number="1" hits="1"/></lines></class>'
        "</classes></package></packages></coverage>",
        encoding="utf-8",
    )

    payload = tests_file(tmp_path, _task())

    assert payload["coverage"] == [{"path": "src/a/thing.py", "lines": 2, "covered": 1}]
    assert payload["named_tests"] == [{"module": "src/a/thing.py", "test": "tests/test_thing.py"}]


def test_the_red_of_the_last_attempt_reaches_the_pack(tmp_path: Path) -> None:
    (tmp_path / ".torve").mkdir()
    rows = [
        {
            "schema_version": 1,
            "task_id": "T-0500",
            "at": "2026-09-09T00:00:00Z",
            "results": [
                {"name": "scope", "outcome": "pass", "state": "blocking", "output": "clean"},
                {
                    "name": "decisions-reported",
                    "outcome": "fail",
                    "state": "blocking",
                    "output": "decision D-1.1: LOCKED, and the diff touches 1 file(s) it governs (src/a/thing.py), with no entry in the log",
                },
                {
                    "name": "acceptance",
                    "outcome": "fail",
                    "state": "blocking",
                    "output": "=== FAILURES ===\nFAILED tests/test_thing.py::test_x - assert 1 == 2\n1 failed",
                },
            ],
            "agent": {"tier": "executor", "attempt": 1},
        },
        {
            "schema_version": 1,
            "task_id": "T-0501",
            "at": "2026-09-09T00:01:00Z",
            "results": [
                {
                    "name": "scope",
                    "outcome": "fail",
                    "state": "blocking",
                    "output": "not this task",
                },
            ],
        },
    ]
    (tmp_path / ".torve" / "telemetry.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )

    payload = attempts_file(tmp_path, _task())
    reds = payload["last_red_gates"]

    assert [r["gate"] for r in reds] == ["decisions-reported", "acceptance"]
    assert reds[0]["governing_decisions"] == ["D-1.1"]
    assert reds[1]["failed_tests"] == ["tests/test_thing.py::test_x"]
    assert "not this task" not in json.dumps(payload)


def test_contention_and_the_index_and_the_replay_rule(tmp_path: Path) -> None:
    rfc_dir = _seed(tmp_path)
    (tmp_path / ".torve").mkdir(exist_ok=True)
    (tmp_path / ".torve" / "telemetry.jsonl").write_text(
        json.dumps({"kind": "engine", "event": "blocked_dispatch", "path": "src/a/thing.py"})
        + "\n",
        encoding="utf-8",
    )

    assert contended_file(tmp_path)["contended"] == [
        {"path": "src/a/thing.py", "blocked_dispatches": 1}
    ]

    files = build(tmp_path, rfc_dir, _task(), tmp_path / "missing-gates.yaml")

    assert set(files) >= {
        "index.md",
        "decisions.json",
        "gates.json",
        "tests.json",
        "attempts.json",
        "contended.json",
        "schema/task.json",
        "schema/finding.json",
        "schema/draft.json",
        "schema/document.json",
    }
    assert "Nothing here outranks the contract" in files["index.md"]

    replay = build(tmp_path, rfc_dir, _task(), tmp_path / "missing-gates.yaml", replay=True)

    assert "attempts.json" not in replay and "contended.json" not in replay
    assert replay["decisions.json"] == files["decisions.json"]

    target = materialize(tmp_path / "wt", files)

    assert target == tmp_path / "wt" / PACK_DIR
    assert (target / "schema" / "task.json").is_file()

    again = materialize(tmp_path / "wt", {"index.md": "only"})

    assert sorted(p.name for p in again.iterdir()) == [".gitignore", "index.md"]
    assert (again / ".gitignore").read_text(encoding="utf-8") == "*\n"  # invisible to any diff


def test_the_index_names_the_red_first() -> None:
    files = {
        "attempts.json": json.dumps(
            {
                "attempts": [{"attempt": 1}],
                "last_red_gates": [{"gate": "scope"}, {"gate": "acceptance"}],
            }
        )
    }

    index = render_index(files, _task())

    assert "Prior attempts on this task: 1." in index
    assert (
        "The last red pass convicted on: scope, acceptance — read `attempts.json` first." in index
    )
