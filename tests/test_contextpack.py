"""The context pack (S-0054/the-context-pack): every builder is a pure function of
(record snapshot, tree, contract) — golden-shaped here — and the pack
never names another task; the red of one attempt reaches the next
(S-0054/D-12) as the gate's output tail, the rows it names and the tests it
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
    conviction_of,
    decisions_file,
    gates_file,
    materialize,
    render_index,
    tests_file,
)
from torve.config.spec import load_corpus
from torve.domain.task import InheritedDecision, Task

# ----------------------- #

DETAILS = {"S-0001/D-1": {"rationale": "because", "check": "pytest tests/test_a.py"}}
AMENDMENTS = [
    {
        "id": "A-9",
        "at": "2026-09-09",
        "title": "regraded",
        "changes": [
            {"subject": "S-0001/D-1", "field": "grade", "before": "ASSUMED", "after": "LOCKED"},
            # The stamp the amend verb re-writes on every change. It rides in the
            # document beside the real changes and says nothing to a reader.
            {
                "subject": "S-0001/D-1",
                "field": "fingerprint",
                "before": "aaaa1111/bbbb2222",
                "after": "cccc3333/dddd4444",
            },
        ],
        "md": "words",
    }
]


def _task(**extra: object) -> Task:
    return Task(
        id="T-0500",
        spec="S-0001",
        decisions=[
            InheritedDecision(
                id="S-0001/D-1",
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
        [("S-0001/D-1", "LOCKED", "A rule.", "`src/a/**`", "it holds")],
        details=DETAILS,
        amendments=AMENDMENTS,
    )
    other = document(
        "0002", [("S-0002/D-1", "ASSUMED", "Another rule over a.", "`src/a/thing.py`")]
    )

    return corpus(tmp_path, **{"0001": text, "0002": other})


# ----------------------- #


def test_the_pack_carries_what_asked_for_the_work(tmp_path):
    """S-0060/D-9: the executor reads the audit rather than its slug, and a
    contract naming no source adds no file and no index line."""

    from torve.application.contextpack import build
    from torve.config.sources import schema_header
    from torve.domain.task import Task

    filed = tmp_path / ".torve" / "sources" / "audit" / "soc2-2026.yaml"
    filed.parent.mkdir(parents=True, exist_ok=True)
    filed.write_text(
        f"{schema_header()}\n"
        + yaml.safe_dump(
            {
                "id": "audit/soc2-2026",
                "kind": "audit",
                "title": "A gap",
                "ref": "https://x.invalid/42",
                "summary": "Sessions outlive their tokens.\n",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / ".torve" / "gates.yaml"
    manifest.write_text("schema_version: 1\ngates: []\n", encoding="utf-8")
    specs = tmp_path / ".torve" / "specs"

    asked = Task(id="T-0001", source="audit/soc2-2026", decisions=[])
    files = build(tmp_path, specs, asked, manifest)
    payload = json.loads(files["source.json"])

    assert payload["title"] == "A gap" and payload["ref"] == "https://x.invalid/42"
    assert "Sessions outlive their tokens." in payload["summary"]

    plain = build(tmp_path, specs, Task(id="T-0002", decisions=[]), manifest)

    assert "source.json" not in plain


def test_decisions_carry_consequence_rationale_amendments_and_the_standing_set(
    tmp_path: Path,
) -> None:
    rfc_dir = _seed(tmp_path)

    payload = decisions_file(_task(), load_corpus(rfc_dir), rfc_dir)
    (row,) = payload["inherited"]

    assert row["consequence"] == "it holds" and row["rationale"] == "because"
    assert row["amended_by"] == [
        {
            "amendment": "S-0001/A-9",
            "at": "2026-09-09T00:00:00Z",
            "field": "grade",
            "before": "ASSUMED",
            "after": "LOCKED",
        }
    ]
    assert [s["id"] for s in payload["standing_over_scope"]] == ["S-0002/D-1"]


def test_a_rows_history_carries_no_fingerprint_stamp(tmp_path: Path) -> None:
    """A fingerprint is how `spec check` catches a hand edit. To an agent reading
    why a rule says what it says it is two hashes where a rule should be, and a
    third of the change entries this corpus holds are these."""

    rfc_dir = _seed(tmp_path)
    (row,) = decisions_file(_task(), load_corpus(rfc_dir), rfc_dir)["inherited"]

    assert [change["field"] for change in row["amended_by"]] == ["grade"]


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
        ("decision:S-0001/D-1", "compliance", "shadow"),
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
                    "output": "decision S-0001/D-1: LOCKED, and the diff touches 1 file(s) it governs (src/a/thing.py), with no entry in the log",
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
    assert reds[0]["governing_decisions"] == ["S-0001/D-1"]
    assert reds[1]["failed_tests"] == ["tests/test_thing.py::test_x"]
    # No shas on these rows: the block says nothing about a tree the record
    # cannot point at (S-0069/D-2).
    assert reds[0]["touched_paths"] == []
    assert "not this task" not in json.dumps(payload)


def test_the_conviction_names_its_paths_and_the_rows_governing_them(tmp_path: Path) -> None:
    """S-0069/D-2, phase 1: the facts that convicted the last attempt — the
    gate, its tail, the paths its diff touched and the inherited rows
    governing those paths — are one read of the pack away, and a shadow red
    is a fact, not a conviction."""
    import subprocess

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / ".torve").mkdir()
    _seed(tmp_path)
    git("add", "-A")
    git("commit", "-qm", "base")
    merge_base = git("rev-parse", "HEAD")
    (tmp_path / "src" / "a" / "thing.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "touch")
    head = git("rev-parse", "HEAD")

    (tmp_path / ".torve" / "telemetry.jsonl").write_text(
        json.dumps(
            {
                "task_id": "T-0500",
                "merge_base": merge_base,
                "head": head,
                "results": [
                    {
                        "name": "lint",
                        "outcome": "fail",
                        "state": "shadow",
                        "output": "ruff says something",
                    },
                    {
                        "name": "acceptance",
                        "outcome": "fail",
                        "state": "blocking",
                        "output": "FAILED tests/test_thing.py::test_x - assert 1 == 2",
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    reds = attempts_file(tmp_path, _task())["last_red_gates"]

    assert [r["gate"] for r in reds] == ["lint", "acceptance"]
    assert reds[1]["touched_paths"] == ["src/a/thing.py"]

    conviction = conviction_of(tmp_path, _task())

    assert conviction is not None
    assert conviction["gate"] == "acceptance"
    assert conviction["touched_paths"] == ["src/a/thing.py"]
    assert conviction["governing_rows"] == [
        {"id": "S-0001/D-1", "grade": "LOCKED", "text": "A rule."}
    ]

    assert conviction_of(tmp_path, Task(id="T-0501", decisions=[])) is None


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


def test_the_index_names_only_what_is_still_behind_a_read() -> None:
    """S-0076/D-1: the small files arrive in the attempt's first message, so
    the index points at none of them — pointing at bytes already in context
    buys a round trip that re-reads them. `decisions.json` and the schemas
    stay named, because those are still fetched."""

    files = {
        "source.json": "{}",
        "gates.json": "{}",
        "tests.json": "{}",
        "attempts.json": json.dumps({"attempts": [{"attempt": 1}]}),
        "contended.json": "{}",
        "decisions.json": "{}",
        "schema/task.json": "{}",
    }

    index = render_index(files, _task())

    assert "`decisions.json`" in index and "`schema/*.json`" in index

    for handed in ("source.json", "gates.json", "tests.json", "attempts.json", "contended.json"):
        assert handed not in index
