"""The context pack (S-0054/the-context-pack): every builder is a pure function of
(record snapshot, tree, contract) — golden-shaped here — and the pack
never names another task; the blocking red of one attempt reaches the next
(S-0054/D-12) as the gate's output tail, the rows it names and the tests it
lists; a replay omits what reads the stream as it stands now. The task's own
files come first and whole, the contingent ones by name only
(S-0122/the-evidence-package)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from test_decisions import corpus, document

from torve.application.contextpack import (
    PACK_DIR,
    attempts_file,
    build,
    conviction_of,
    decisions_file,
    materialize,
    render_index,
    scope_file,
    tests_file,
)
from torve.config.spec import load_corpus
from torve.domain.task import InheritedDecision, Scope, Task

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


def test_the_pack_carries_a_checked_rows_twin(tmp_path: Path) -> None:
    """S-0106/D-5: the reviewer audits a check that can fail, so the pack
    carries the twin beside the check, not only the check."""

    rfc_dir = _seed(tmp_path)
    task = _task()
    task.decisions[0].check_twin = "tests/test_a_sabotage.py"

    (row,) = decisions_file(task, load_corpus(rfc_dir), rfc_dir)["inherited"]

    assert row["check"] == "pytest tests/test_a.py"
    assert row["check_twin"] == "tests/test_a_sabotage.py"


def test_a_rows_history_carries_no_fingerprint_stamp(tmp_path: Path) -> None:
    """A fingerprint is how `spec check` catches a hand edit. To an agent reading
    why a rule says what it says it is two hashes where a rule should be, and a
    third of the change entries this corpus holds are these."""

    rfc_dir = _seed(tmp_path)
    (row,) = decisions_file(_task(), load_corpus(rfc_dir), rfc_dir)["inherited"]

    assert [change["field"] for change in row["amended_by"]] == ["grade"]


def test_the_pack_carries_no_battery_coverage_or_contention(tmp_path: Path) -> None:
    """S-0122/the-evidence-package: the battery's manifest, coverage counts and
    contended paths were handed to every attempt and changed what none of them
    did; the pack no longer writes them."""

    rfc_dir = _seed(tmp_path)
    (tmp_path / "coverage.xml").write_text(
        '<?xml version="1.0"?><coverage><packages><package><classes>'
        '<class filename="a/thing.py"><lines><line number="1" hits="1"/></lines></class>'
        "</classes></package></packages></coverage>",
        encoding="utf-8",
    )
    (tmp_path / ".torve").mkdir(exist_ok=True)
    (tmp_path / ".torve" / "telemetry.jsonl").write_text(
        json.dumps({"event": "blocked_dispatch", "path": "src/a/thing.py"}) + "\n",
        encoding="utf-8",
    )

    files = build(tmp_path, rfc_dir, _task(), tmp_path / "missing-gates.yaml")

    assert not {"gates.json", "tests.json", "contended.json"} & set(files)
    assert "coverage" not in tests_file(tmp_path, _task())


def test_tests_name_the_paired_test_files(tmp_path: Path) -> None:
    _seed(tmp_path)

    payload = tests_file(tmp_path, _task())

    assert payload["named_tests"] == [{"module": "src/a/thing.py", "test": "tests/test_thing.py"}]


def test_the_scope_does_not_pack_a_nested_test_tree_as_source(tmp_path: Path) -> None:
    """S-0110/D-10: a file `tests.patterns` names is a test, not the scope's
    source, so an allow glob that sweeps a nested test tree in does not pack
    that tree's bodies."""

    (tmp_path / "app" / "src").mkdir(parents=True)
    (tmp_path / "app" / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "app" / "tests").mkdir()
    (tmp_path / "app" / "tests" / "test_mod.py").write_text("", encoding="utf-8")
    task = Task(id="T-0500", spec="S-0001", decisions=[], scope=Scope(allow=["app/**"]))

    scope = scope_file(tmp_path, task, {"named_tests": []}, patterns=["app/tests/**"])

    assert "## `app/src/mod.py`" in scope
    assert "app/tests/test_mod.py" not in scope


@pytest.mark.parametrize("named", ["app/tests/test_allowed.py", "/app/tests/test_allowed.py"])
def test_a_test_the_scope_names_literally_is_packed(tmp_path: Path, named: str) -> None:
    """S-0110/D-10 keeps out a test an allow glob sweeps in, never one the
    scope names by path: a glob-free allow entry, root-anchored or not, is
    packed though `tests.patterns` matches it, and a contingent literal is
    named."""

    (tmp_path / "app" / "tests").mkdir(parents=True)
    for name in ("test_allowed.py", "test_contingent.py", "test_swept.py"):
        (tmp_path / "app" / "tests" / name).write_text("x = 1\n", encoding="utf-8")
    task = Task(
        id="T-0500",
        spec="S-0001",
        decisions=[],
        scope=Scope(
            allow=[named, "app/tests/*.py"],
            contingent=["app/tests/test_contingent.py"],
        ),
    )

    scope = scope_file(tmp_path, task, {"named_tests": []}, patterns=["app/tests/**"])

    assert "## `app/tests/test_allowed.py`" in scope
    # Contingent, so named and never carried (S-0122/the-evidence-package).
    assert "- `app/tests/test_contingent.py`" in scope
    assert "## `app/tests/test_contingent.py`" not in scope
    assert "app/tests/test_swept.py" not in scope


def test_the_pack_reads_no_nested_worktree(tmp_path: Path) -> None:
    """A directory holding its own `.git` is another checkout, so an allow
    glob that matches at any depth packs the tree's file and not its copy."""

    (tmp_path / "notes.md").write_text("ours\n", encoding="utf-8")
    nested = tmp_path / ".claude" / "worktrees" / "agent-1"
    nested.mkdir(parents=True)
    (nested / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    (nested / "notes.md").write_text("a copy\n", encoding="utf-8")
    task = Task(id="T-0500", spec="S-0001", decisions=[], scope=Scope(allow=["*.md"]))

    scope = scope_file(tmp_path, task, {"named_tests": []})

    assert "## `notes.md`" in scope
    assert "agent-1" not in scope


def test_tests_read_names_every_prefixed_test_file(tmp_path: Path) -> None:
    """S-0096/D-4: the pack names `test_<stem>.py` and every existing
    `test_<stem>_*.py`, and no name that merely shares a prefix."""
    _seed(tmp_path)
    (tmp_path / "tests" / "test_thing_config.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "test_thingish.py").write_text("", encoding="utf-8")

    payload = tests_file(tmp_path, _task())

    assert payload["named_tests"] == [
        {"module": "src/a/thing.py", "test": "tests/test_thing.py"},
        {"module": "src/a/thing.py", "test": "tests/test_thing_config.py"},
    ]


def test_a_contingent_path_is_named_and_neither_carried_nor_paired(tmp_path: Path) -> None:
    """S-0104/D-7 as S-0122/the-evidence-package narrows it: a contingent path
    is something the change may reach, not the task's target, so the pack
    names it and carries neither its body nor its test."""

    _seed(tmp_path)
    (tmp_path / "src" / "b.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_b.py").write_text("", encoding="utf-8")

    task = Task(
        id="T-0500",
        spec="S-0001",
        decisions=[],
        scope=Scope(allow=["src/a/**"], contingent=["src/b.py"]),
    )

    payload = tests_file(tmp_path, task)
    assert {"module": "src/b.py", "test": "tests/test_b.py"} not in payload["named_tests"]

    scope = scope_file(tmp_path, task, payload)

    assert "- `src/b.py`" in scope and "x = 1" not in scope
    assert "tests/test_b.py" not in scope


def test_the_red_of_the_last_attempt_reaches_the_pack(tmp_path: Path) -> None:
    (tmp_path / ".torve").mkdir()
    rows = [
        {
            "schema_version": 1,
            "task_id": "T-0500",
            "at": "2026-09-09T00:00:00Z",
            "results": [
                {"name": "lint", "outcome": "fail", "state": "shadow", "output": "ruff says"},
                {
                    "name": "scope",
                    "outcome": "fail",
                    "state": "blocking",
                    "output": "src/b.py is outside scope.allow, which S-0001/D-1 governs",
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

    assert [r["gate"] for r in reds] == ["scope", "acceptance"]
    assert reds[0]["governing_decisions"] == ["S-0001/D-1"]
    assert reds[1]["failed_tests"] == ["tests/test_thing.py::test_x"]
    # A shadow red convicted nothing, so the retry is not handed it.
    assert "ruff says" not in json.dumps(payload)
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

    assert [r["gate"] for r in reds] == ["acceptance"]
    assert reds[0]["touched_paths"] == ["src/a/thing.py"]

    conviction = conviction_of(tmp_path, _task())

    assert conviction is not None
    assert conviction["gate"] == "acceptance"
    assert conviction["touched_paths"] == ["src/a/thing.py"]
    assert conviction["governing_rows"] == [
        {"id": "S-0001/D-1", "grade": "LOCKED", "text": "A rule."}
    ]

    assert conviction_of(tmp_path, Task(id="T-0501", decisions=[])) is None


def test_the_index_and_the_replay_rule(tmp_path: Path) -> None:
    rfc_dir = _seed(tmp_path)

    files = build(tmp_path, rfc_dir, _task(), tmp_path / "missing-gates.yaml")

    assert set(files) >= {
        "index.md",
        "decisions.json",
        "attempts.json",
        "scope.md",
        "schema/task.json",
        "schema/finding.json",
        "schema/draft.json",
        "schema/document.json",
    }
    assert "Nothing here outranks the contract" in files["index.md"]

    replay = build(tmp_path, rfc_dir, _task(), tmp_path / "missing-gates.yaml", replay=True)

    assert "attempts.json" not in replay
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
        "attempts.json": json.dumps({"attempts": [{"attempt": 1}]}),
        "scope.md": "# The files this task changes",
        "decisions.json": "{}",
        "schema/task.json": "{}",
    }

    index = render_index(files, _task())

    assert "`decisions.json`" in index and "`schema/*.json`" in index

    for handed in ("source.json", "attempts.json", "scope.md"):
        assert handed not in index


def test_the_symbols_file_names_the_whole_tree_and_the_index_names_it(tmp_path: Path) -> None:
    """S-0076/D-5: a lookup is one grep of one file — every symbol the tree
    defines, not the scope's, with its coordinate; a file to grep, so the
    index names it and the prompt never carries it."""

    rfc_dir = _seed(tmp_path)
    (tmp_path / "src" / "a" / "thing.py").write_text(
        "LIMIT = 3\n\n\nclass Widget:\n    def spin(self):\n        def inner():\n            pass\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "b").mkdir(parents=True)  # outside the scope, still indexed
    (tmp_path / "src" / "b" / "other.py").write_text("async def fetch():\n    pass\n", "utf-8")
    (tmp_path / "src" / "b" / "broken.py").write_text("def (\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "vendored.py").write_text("def never_indexed():\n    pass\n", "utf-8")

    files = build(tmp_path, rfc_dir, _task(), tmp_path / "missing-gates.yaml")
    lines = files["symbols.txt"].splitlines()

    assert "src/a/thing.py:1 LIMIT" in lines
    assert "src/a/thing.py:4 class Widget" in lines
    assert "src/a/thing.py:5 def Widget.spin" in lines
    assert "src/a/thing.py:6 def Widget.spin.inner" in lines
    assert "src/b/other.py:1 def fetch" in lines
    assert not [line for line in lines if "vendored" in line or "broken" in line]

    assert "`symbols.txt`" in files["index.md"]


def test_the_map_names_where_things_are_and_stays_small(tmp_path: Path) -> None:
    """S-0077/D-4: every attempt in the corpus spent calls rebuilding this — a
    median of eight — and the answer is the same on all of them. Two levels,
    with a count standing in for a long directory, because a listing that grew
    with the tree would stop being small and small is the only reason it is
    handed over rather than opened."""

    rfc_dir = _seed(tmp_path)
    (tmp_path / "src" / "a" / "thing.py").write_text("LIMIT = 3\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()  # never named: not the repository's own

    for number in range(12):
        (tmp_path / ".torve" / "tasks" / f"T-{number:04}").mkdir(parents=True)

    task = _task(acceptance=["pytest tests/test_thing.py"])
    files = build(tmp_path, rfc_dir, task, tmp_path / "missing-gates.yaml")
    body = files["map.md"]

    assert "- `src/` — a" in body
    assert "`tasks/` — T-0000" in body and "and 4 more" in body
    assert ".venv" not in body
    # The acceptance is what this task faces, from the contract rather than a
    # convention: a repository's commands are not guessable from its tree.
    assert "`pytest tests/test_thing.py`" in body
    # Handed over, so the index does not tell anyone to open it.
    assert "`map.md`" not in files["index.md"]
    assert "where things are" in files["index.md"]


def test_the_scope_arrives_whole_and_past_the_budget_by_name(tmp_path: Path) -> None:
    """S-0076/D-2 as S-0122/the-evidence-package reshapes it: the scope's files
    and their tests are one document of contents; a file past what is left of
    the budget is named with its size rather than outlined, and a smaller one
    after it is still carried."""

    rfc_dir = _seed(tmp_path)
    body = "LIMIT = 3\n\n\nclass Widget:\n    def spin(self):\n        pass\n"
    (tmp_path / "src" / "a" / "thing.py").write_text(body, encoding="utf-8")
    (tmp_path / "tests" / "test_thing.py").write_text("def test_spin():\n    pass\n", "utf-8")
    (tmp_path / "src" / "b").mkdir(parents=True)  # outside the scope, never carried
    (tmp_path / "src" / "b" / "other.py").write_text("def fetch():\n    pass\n", "utf-8")

    files = build(tmp_path, rfc_dir, _task(), tmp_path / "missing-gates.yaml")
    whole = files["scope.md"]

    assert "class Widget:" in whole and "def test_spin():" in whole
    assert "src/b/other.py" not in whole
    # S-0076/A-1: handed over, so the index does not tell anyone to open it.
    assert "`scope.md`" not in files["index.md"]
    assert "the files this task changes" in files["index.md"]

    tests = tests_file(tmp_path, _task())
    cut = scope_file(tmp_path, _task(), tests, budget=len(body) - 1)

    assert "class Widget:" not in cut and "- `src/a/thing.py` — 6 lines" in cut
    assert "def test_spin():" in cut  # the smaller file after it still fits


def test_the_names_past_the_budget_are_bounded_too(tmp_path: Path) -> None:
    """A list has no size of its own: bloomery T-0173's scope named 1,107
    files and the seat refused the first message. Past the names' budget the
    rest are counted."""

    _seed(tmp_path)
    (tmp_path / "src" / "a" / "thing.py").write_text("LIMIT = 3\n", encoding="utf-8")

    for n in range(200):
        (tmp_path / "src" / "a" / f"m{n:03}.py").write_text("x = 1\n", encoding="utf-8")

    scope = scope_file(tmp_path, _task(), {"named_tests": []}, budget=0, names_budget=1_000)
    named = [line for line in scope.splitlines() if line.startswith("- ")]

    assert len(scope) <= 1_500
    assert named[0] == "- `src/a/thing.py` — 1 lines"  # the literal leads
    assert "- `src/a/m199.py`" not in scope
    assert scope.rstrip().endswith("more, not named here")


def test_every_scope_file_arrives_whole_however_it_sorts(tmp_path: Path) -> None:
    """T-0751: the pack sorted every path it carried and outlined them under
    one cut, so forty contingent neighbours that sorted first filled the
    outline and the files the task edited were "and N more, not named here".
    The task's own files and their tests now come first and whole, in the
    contract's order; the neighbours are named after them."""

    (tmp_path / "src" / "aa").mkdir(parents=True)
    (tmp_path / "src" / "zz" / "helpers").mkdir(parents=True)
    (tmp_path / "tests").mkdir()

    neighbours = [f"src/aa/m{n:02}.py" for n in range(40)]

    for rel in neighbours:
        (tmp_path / rel).write_text(
            "".join(f"def f{i}():\n    return {i}\n" for i in range(80)), encoding="utf-8"
        )

    targets = {
        "src/zz/target.py": "def target():\n    return 'the edit lands here'\n",
        "src/zz/helpers/zeta.py": "ZETA = 'a helper the glob reaches'\n",
        "tests/test_target.py": "def test_target():\n    assert target()\n",
    }

    for rel, text in targets.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")

    task = Task(
        id="T-0751",
        decisions=[],
        scope=Scope(allow=["src/zz/target.py", "src/zz/helpers/**"], contingent=neighbours),
    )

    scope = scope_file(tmp_path, task, tests_file(tmp_path, task))

    for rel, text in targets.items():
        assert f"## `{rel}`" in scope and text.rstrip("\n") in scope, rel

    assert scope.index("## `src/zz/target.py`") < scope.index("## `tests/test_target.py`")
    assert scope.index("## `tests/test_target.py`") < scope.index("- `src/aa/m00.py`")
    assert "def f79():" not in scope  # a neighbour is named, never carried
    assert all(f"- `{rel}`" in scope for rel in neighbours)


def test_a_glob_scopes_modules_bring_their_paired_tests(tmp_path: Path) -> None:
    """A module the scope reaches by glob is a target like a literal one, so
    its paired test is carried beside it; a test the glob sweeps in is not
    paired as though it were a module."""

    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "tests" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "alpha.py").write_text("ALPHA = 1\n", encoding="utf-8")
    (tmp_path / "src" / "pkg" / "test_beta.py").write_text("def test_b(): ...\n", encoding="utf-8")
    (tmp_path / "tests" / "pkg" / "test_alpha.py").write_text(
        "def test_alpha():\n    assert ALPHA\n", encoding="utf-8"
    )
    task = Task(id="T-0502", decisions=[], scope=Scope(allow=["src/pkg/**"]))

    tests = tests_file(tmp_path, task)
    scope = scope_file(tmp_path, task, tests)

    assert tests["named_tests"] == [
        {"module": "src/pkg/alpha.py", "test": "tests/pkg/test_alpha.py"}
    ]
    assert "## `tests/pkg/test_alpha.py`" in scope and "assert ALPHA" in scope


def test_a_scope_carrying_markdown_cannot_close_its_own_fence(tmp_path: Path) -> None:
    """A body that is itself markdown gets a fence longer than any run it
    holds, so the document stays one document."""

    from torve.application.contextpack import scope_file, tests_file

    _seed(tmp_path)
    (tmp_path / "src" / "a" / "thing.py").write_text('x = """\n```\n"""\n', encoding="utf-8")

    scope = scope_file(tmp_path, _task(), tests_file(tmp_path, _task()))

    assert "````python" in scope and scope.count("````") == 2


def test_an_unconstrained_scope_inlines_no_repository(tmp_path: Path) -> None:
    """An empty allow list means unconstrained (S-0002/scope-in-detail), which
    is every file in the tree — so it carries none of them."""

    from torve.application.contextpack import scope_file

    _seed(tmp_path)
    unconstrained = Task(id="T-0501", decisions=[])

    assert scope_file(tmp_path, unconstrained, {"named_tests": []}) == ""


def test_the_pack_pairs_a_go_test_by_the_manifest(tmp_path: Path) -> None:
    """S-0106/D-7: the pack's named tests read the manifest's `pair` templates,
    so a Go module names the `_test.go` beside it."""

    from torve.config.manifest import TestsConfig

    (tmp_path / "src" / "foo").mkdir(parents=True)
    (tmp_path / "src" / "foo" / "bar.go").write_text("package foo\n", encoding="utf-8")
    (tmp_path / "src" / "foo" / "bar_test.go").write_text("package foo\n", encoding="utf-8")
    task = Task(id="T-0500", decisions=[], scope={"allow": ["src/foo/bar.go"], "deny": []})  # type: ignore[arg-type]

    payload = tests_file(tmp_path, task, TestsConfig(pair=["{dir}/{stem}_test.go"]))

    assert {"module": "src/foo/bar.go", "test": "src/foo/bar_test.go"} in payload["named_tests"]


def test_an_ordinary_contracts_prompt_stays_under_35_kb(tmp_path: Path) -> None:
    """S-0122/the-evidence-package: an implement prompt measured 98.9 KB, about
    5% of it the task, and still cut the files the task edited. For an
    ordinary contract — one module and its test, a large contingent
    neighbour, four rows — the first message and the system file together
    stay under 35 KB, and the task's own files are most of it. A pack file
    handed over again, or a neighbour carried whole, breaks this."""

    from torve.adapters.agent.harness import (
        build_prompt,
        pack_handover,
        skills_handover,
        working_rules,
    )
    from torve.application.skills import materialize as materialize_skills

    module = "".join(
        f"def step_{n}(value: int) -> int:\n    return value + {n}\n\n\n" for n in range(120)
    )
    test = "".join(
        f"def test_step_{n}() -> None:\n    assert step_{n}(1) == {n + 1}\n\n\n" for n in range(220)
    )
    neighbour = "".join(f"def far_{n}():\n    return {n}\n\n\n" for n in range(1600))

    for rel, text in (
        ("src/app/service.py", module),
        ("tests/test_service.py", test),
        ("src/app/neighbour.py", neighbour),
        ("docs/index.md", "# Docs\n"),
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")

    rule = "A rule that governs how the service steps a value, stated at the length rows run to. "
    task = Task(
        id="T-0900",
        intent="Every step clamps its result to the configured ceiling before returning it.",
        decisions=[
            InheritedDecision(
                id=f"S-0001/D-{n}",
                grade="LOCKED" if n % 2 else "ASSUMED",
                text=rule * 3,
                paths=["src/app/**"],
                consequence="a step that overflows corrupts every later step",
            )
            for n in range(1, 5)
        ],
        scope=Scope(allow=["src/app/service.py"], contingent=["src/app/neighbour.py"]),
        acceptance=["uv run pytest tests/test_service.py"],
    )
    extras = "Prefer the smallest change that makes the acceptance commands pass.\n"

    materialize(tmp_path, build(tmp_path, tmp_path / "specs", task, tmp_path / "gates.yaml"))
    materialize_skills(
        "implement",
        tmp_path / ".torve" / "skills",
        {"implement": ["working-rules", "flag-dont-flip"]},
    )

    prompt = build_prompt(task, prompt_extras=extras, pack=pack_handover(tmp_path))
    system = working_rules(extras, skills_handover(tmp_path))
    size = len(prompt.encode()) + len(system.encode())

    assert module.rstrip("\n") in prompt and test.rstrip("\n") in prompt
    assert "def far_0()" not in prompt and "- `src/app/neighbour.py`" in prompt
    assert size < 35_000, size
    assert (len(module) + len(test)) / size > 0.5


def test_no_row_is_listed_as_an_attempt_gate(tmp_path: Path) -> None:
    """S-0122/D-4: an attempt's battery runs no row's check, so the battery
    `torve brief` prints names none as a gate, whatever the contract holds."""

    from torve.application.contextpack import gates_file

    manifest = tmp_path / "gates.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "gates": [
                    {"name": "scope", "run": "@scope", "state": "blocking", "origin": "structural"}
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = gates_file(manifest)

    assert [g["name"] for g in payload["gates"]] == ["scope"]
