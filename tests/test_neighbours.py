"""S-0104/D-1: the contingent set — the literal paths a phase's change most
often reaches beyond its own scope, derived one hop at a time from the tree:
importers, imported first-party modules, paired tests at any depth, package
`__init__`, and the document's other phases' matches (S-0104/D-11: the last two
are what a repository with no Python still gets)."""

from __future__ import annotations

import shutil
import string
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from torve.application.neighbours import acceptance_runs, contingent_paths, phase_reach
from torve.domain.spec import Corpus, Document, Phase

# ----------------------- #


def document(*phases: tuple[int, list[str]]) -> Document:
    return Document(
        id="S-0001",
        title="Widgets",
        status="accepted",
        owner="tester",
        phasing=[
            Phase(phase=number, title=f"phase-{number}", intent="Do the work.", scope=scope)
            for number, scope in phases
        ],
    )


# ....................... #


def tree(tmp_path: Path) -> Path:
    """A package, an importer, a callee, a nested test and a document union —
    and two files nothing should pull in."""

    root = tmp_path / "repo"
    for relative, text in {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": "from . import helper\n\n\ndef core():\n    return helper.help_me()\n",
        "src/pkg/helper.py": "def help_me():\n    return 1\n",
        "src/app.py": "import pkg.core\n\n\napp = pkg.core.core()\n",
        "src/other.py": "other = 1\n",
        "src/unrelated.py": "unrelated = 1\n",
        "tests/unit/test_core.py": "def test_core():\n    assert True\n",
        "docs/readme.md": "# readme\n",
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    return root


# ....................... #


def test_contingent_is_the_importers_callees_tests_init_and_union(tmp_path: Path) -> None:
    root = tree(tmp_path)
    doc = document((1, ["src/pkg/core.py"]), (2, ["src/other.py"]))

    found = contingent_paths(root, ["src/pkg/core.py"], document=doc, phase=1)

    assert found == sorted(
        [
            "src/app.py",  # the importer of the matched file
            "src/pkg/__init__.py",  # the package's own `__init__`
            "src/pkg/helper.py",  # the first-party module the matched file imports
            "src/other.py",  # the other phase's scope match
            "tests/unit/test_core.py",  # the matched module's nested test
        ]
    )


def test_contingent_omits_the_scope_it_already_covers(tmp_path: Path) -> None:
    root = tree(tmp_path)
    doc = document((1, ["src/pkg/core.py"]), (2, ["src/other.py"]))

    found = contingent_paths(root, ["src/pkg/core.py"], document=doc, phase=1)

    assert "src/pkg/core.py" not in found
    assert "src/unrelated.py" not in found
    assert "docs/readme.md" not in found


def test_a_repository_with_no_python_still_gets_the_document_union(tmp_path: Path) -> None:
    # S-0104/D-11: the import graph reads Python; the language-bound rules drop out.
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "one.md").write_text("one\n", encoding="utf-8")
    (root / "docs" / "two.md").write_text("two\n", encoding="utf-8")
    doc = document((1, ["docs/one.md"]), (2, ["docs/two.md"]))

    found = contingent_paths(root, ["docs/one.md"], document=doc, phase=1)

    assert found == ["docs/two.md"]


def test_a_derived_file_rides_with_its_source_both_ways(tmp_path: Path) -> None:
    """S-0104/D-5: the manifest's derived map adds a golden when its producer
    is scoped, and the producer when the golden is — one hop, both ways."""

    root = tree(tmp_path)
    golden = root / "tests" / "golden" / "core.json"
    golden.parent.mkdir(parents=True)
    golden.write_text("{}\n", encoding="utf-8")
    derived = {"src/pkg/core.py": ["tests/golden/core.json"]}

    forward = contingent_paths(root, ["src/pkg/core.py"], derived=derived)
    assert "tests/golden/core.json" in forward

    backward = contingent_paths(root, ["tests/golden/core.json"], derived=derived)
    assert "src/pkg/core.py" in backward


def test_the_neighbour_derivation_pairs_a_go_test_by_the_manifest(tmp_path: Path) -> None:
    """S-0106/D-7: a Go module brings the `_test.go` beside it, by the
    manifest's `pair` templates — the same pairing the intake and the pack
    read."""

    root = tmp_path / "repo"
    (root / "src" / "foo").mkdir(parents=True)
    (root / "src" / "foo" / "bar.go").write_text("package foo\n", encoding="utf-8")
    (root / "src" / "foo" / "bar_test.go").write_text("package foo\n", encoding="utf-8")

    found = contingent_paths(root, ["src/foo/bar.go"], pair=["{dir}/{stem}_test.go"])

    assert found == ["src/foo/bar_test.go"]


def test_a_nested_worktree_is_not_the_tree(tmp_path: Path) -> None:
    """A directory holding its own `.git` is another checkout — an agent
    harness keeps whole copies of the repository under `.claude/worktrees/` —
    so none of its files is a neighbour. The tree is still read from disk, not
    from git: here nothing is committed at all. A tree that itself sits under
    the engine's `.wt/` is walked like any other."""

    root = tree(tmp_path)
    expected = contingent_paths(root, ["src/pkg/core.py"])
    copy = root / ".claude" / "worktrees" / "agent-1"
    shutil.copytree(root / "src", copy / "src")
    shutil.copytree(root / "tests", copy / "tests")
    (copy / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")

    assert "src/app.py" in expected
    assert contingent_paths(root, ["src/pkg/core.py"]) == expected
    assert contingent_paths(tree(tmp_path / ".wt"), ["src/pkg/core.py"]) == expected


# ....................... #
# S-0119/D-12


@given(
    stem=st.text(alphabet=string.ascii_lowercase + string.digits, min_size=1, max_size=12),
    flags=st.lists(st.sampled_from(["-q", "-x", "--maxfail=2", "-k slow"]), max_size=3),
)
def test_acceptance_runs_counts_only_a_command_that_launches_the_test(
    stem: str, flags: list[str]
) -> None:
    """S-0119/D-12: a command counts a test only when it launches the tests,
    as `pytest`, `-m pytest` or the manifest's `tests.run`, and names the file,
    a directory above it, or no path at all; a linter mentions it and runs
    nothing."""

    path = f"tests/test_{stem}.py"
    launcher = "python3 -m pytest -q -p no:cacheprovider"
    head = " ".join(["uv run pytest", *flags])

    assert acceptance_runs(f"{head} {path}", path, launcher)
    assert acceptance_runs(f"{head} tests", path, launcher)
    assert acceptance_runs(head, path, launcher)
    assert acceptance_runs(f"uv run python3 -m pytest {path}", path, launcher)
    assert not acceptance_runs(f"{head} tests/unit/test_other.py", path, launcher)
    assert not acceptance_runs(f"uv run ruff check {path}", path, launcher)


def test_acceptance_runs_strips_a_dot_slash_and_not_a_dot_prefix() -> None:
    """S-0119/D-12: `./tests` names the same path as `tests`, while `../tests`
    and `.hidden/t.py` name paths the test does not live under."""

    launcher = "python3 -m pytest -q -p no:cacheprovider"

    assert acceptance_runs("uv run pytest ./tests/x.py", "tests/x.py", launcher)
    assert not acceptance_runs("uv run pytest ../tests", "tests/x.py", launcher)
    assert not acceptance_runs("uv run pytest .hidden/t.py", "hidden/t.py", launcher)


# ....................... #
# S-0119/D-11


def reach_tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"

    for relative, text in {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": "value = 1\n",
        "src/app.py": "import pkg.core\n",
        "src/other.py": "import pkg.core\n",
        "tests/unit/test_core.py": "import pkg.core\n",
        "tests/unit/test_other.py": "import pkg.core\n",
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    return root


def test_phase_reach_names_the_unowned_owned_and_unrun(tmp_path: Path) -> None:
    """S-0119/D-11: a phase's importers outside its scope are split by the
    phase that owns them, and its tests by the acceptance commands that run
    them — one warning line each."""

    root = reach_tree(tmp_path)
    doc = Document(
        id="S-0001",
        title="Widgets",
        status="accepted",
        owner="tester",
        phasing=[
            Phase(
                phase=1,
                title="Core",
                intent="Do the work.",
                scope=["src/pkg/core.py"],
                acceptance=["uv run pytest tests/unit/test_other.py"],
            ),
            Phase(phase=2, title="Other", intent="Do the work.", scope=["src/other.py"]),
        ],
    )

    lines = phase_reach(Corpus(documents=[doc]), root)

    assert lines == [
        "S-0001: phase 1 (Core) reaches 1 file no phase owns: src/app.py",
        "S-0001: phase 1 (Core) reaches 1 file phase 2 owns: src/other.py",
        (
            "S-0001: phase 1 (Core) leaves 1 test of its files unrun by every acceptance: "
            "tests/unit/test_core.py"
        ),
    ]


def test_phase_reach_caps_the_paths_and_skips_a_landed_document(tmp_path: Path) -> None:
    """S-0119/D-11: a line names at most ten sorted paths beside the full
    count, and a document something has landed from is not reported."""

    root = reach_tree(tmp_path)

    for number in range(12):
        (root / f"src/caller_{number:02}.py").write_text("import pkg.core\n", encoding="utf-8")

    def widgets(implementation: str) -> Document:
        return Document(
            id="S-0001",
            title="Widgets",
            status="accepted",
            owner="tester",
            implementation=implementation,
            phasing=[
                Phase(phase=1, title="Core", intent="Do the work.", scope=["src/pkg/core.py"])
            ],
        )

    lines = phase_reach(Corpus(documents=[widgets("none")]), root)
    callers = sorted(
        [*(f"src/caller_{number:02}.py" for number in range(12)), "src/app.py", "src/other.py"]
    )

    assert lines[0] == (
        f"S-0001: phase 1 (Core) reaches 14 files no phase owns: {', '.join(callers[:10])}"
    )
    assert phase_reach(Corpus(documents=[widgets("partial")]), root) == []
