"""S-0104/D-1: the contingent set — the literal paths a phase's change most
often reaches beyond its own scope, derived one hop at a time from the tree:
importers, imported first-party modules, paired tests at any depth, package
`__init__`, and the document's other phases' matches (S-0104/D-11: the last two
are what a repository with no Python still gets)."""

from __future__ import annotations

from pathlib import Path

from torve.application.neighbours import contingent_paths
from torve.domain.spec import Document, Phase

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
