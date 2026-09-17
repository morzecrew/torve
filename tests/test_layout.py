"""S-0013 resolution: one path per lookup under `.torve/`, whether or not
the file exists (S-0013/D-1, A-48). `--config` is the only override (S-0013/D-4)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

from torve.base import naming
from torve.config import layout
from torve.config.runconfig import load_runner_config


def test_every_lookup_resolves_under_torve_dir(tmp_path: Path) -> None:
    assert layout.gates_file(tmp_path) == tmp_path / ".torve" / "gates.yaml"
    assert layout.config_file(tmp_path) == tmp_path / ".torve" / "config.yaml"
    assert layout.task_dir(tmp_path, "T-1") == tmp_path / ".torve" / "tasks" / "T-1"
    assert layout.task_file(tmp_path, "T-1") == (
        tmp_path / ".torve" / "tasks" / "T-1" / "contract.yaml"
    )
    assert layout.log_file(tmp_path, "T-1") == (tmp_path / ".torve" / "tasks" / "T-1" / "log.yaml")
    # The corpus's home too (S-0057 S-0057/D-3): the default of `specs.path`,
    # with the archive and the schemas resolved as its siblings.
    assert layout.SPECS_DIR == ".torve/specs"


def test_runner_config_reads_canonical_location(tmp_path: Path) -> None:
    (tmp_path / ".torve").mkdir()
    (tmp_path / ".torve" / "config.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "poison_ceiling": 5})
    )
    assert load_runner_config(tmp_path).poison_ceiling == 5


def test_runner_config_explicit_override_wins(tmp_path: Path) -> None:
    (tmp_path / ".torve").mkdir()
    (tmp_path / ".torve" / "config.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "poison_ceiling": 5})
    )
    override = tmp_path / "elsewhere.yaml"
    override.write_text(yaml.safe_dump({"schema_version": 1, "poison_ceiling": 7}))
    assert load_runner_config(tmp_path, override).poison_ceiling == 7


def test_runner_config_missing_explicit_path_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no runner configuration"):
        load_runner_config(tmp_path, tmp_path / "absent.yaml")


def test_runner_config_rejects_unknown_keys(tmp_path: Path) -> None:
    # S-0013/D-5: a typo must not silently remove a knob.
    (tmp_path / ".torve").mkdir()
    (tmp_path / ".torve" / "config.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "poison_ceilling": 5})
    )
    with pytest.raises(ValueError):
        load_runner_config(tmp_path)


# ....................... #
# The trace store's home: one directory of the engine root, referenced
# root-relative, and one path helper every writer reaches.


def test_trace_store_home_is_under_the_root(tmp_path: Path) -> None:
    assert naming.traces_dir(tmp_path) == tmp_path / ".torve" / "traces"
    assert naming.trace_file(naming.worktree(tmp_path, "T-1"), 4) == (
        tmp_path / ".torve" / "traces" / "T-1.a4.trace.log"
    )


def test_trace_ref_is_root_relative(tmp_path: Path) -> None:
    expected = ".torve/traces/T-1.a4.trace.log"
    assert naming.trace_ref(naming.worktree(tmp_path, "T-1"), 4) == expected


def test_the_trace_path_helper_ensures_the_store_directory(tmp_path: Path) -> None:
    # The one path helper creates the home, so no writer of the store —
    # the harness adapter or the review lane's relocation — can depend on
    # another having run first.

    assert not (tmp_path / ".torve" / "traces").exists()

    trace = naming.trace_file(naming.worktree(tmp_path, "T-1"), 1)

    assert trace.parent.is_dir()

    # And it is idempotent: the helper serves a second writer into a home
    # the first one already made.
    assert naming.trace_file(naming.worktree(tmp_path, "T-2"), 1) == (
        tmp_path / ".torve" / "traces" / "T-2.a1.trace.log"
    )


# ....................... #
# The document branch (S-0083/D-3): named from the contract's own `spec`, beside
# the task branch and in the same namespace.


def test_a_document_branch_is_named_from_its_spec_and_cannot_collide_with_a_task() -> None:
    assert naming.document_branch("S-0083") == "torve/S-0083"
    assert naming.branch("T-0441") == "torve/T-0441"
    assert naming.document_branch("S-0083") != naming.branch("T-0083")


# ....................... #
# The sweep (S-0070/D-1): `.torve/.gitignore` names what this repository
# deliberately does not commit, and nothing it does commit may be a function
# of those paths. Every reference is judged once and carries its verdict here,
# so a new one is a judgement owed rather than a silent dependency.
#
# The surface is Python string literals. Prose — a comment, a docstring, a
# governance glob in a document — names a path without reading it, and every
# renderer of a committed artefact in this repository is Python, so a literal
# is the only shape a dependency can take.

_REPO_ROOT = Path(__file__).resolve().parents[1]

# `.wt` is where the engine cuts a live worktree, so every file in this repository
# appears under it again, once per attempt in flight. `intake.NOT_THE_TREE` carries
# the same exclusion for the same reason (T-0282: "every lint then reported each
# finding once per live worktree"), and without it this check's verdict depends on
# how many attempts the host happens to be holding — which is the property this
# document exists to remove.
_UNSCANNED = {
    ".git",
    ".wt",
    ".venv",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
}

_RUNTIME = "where the engine writes the record at runtime; no committed file is rendered from it"
_SCRATCH = "a path inside a scratch tree the test builds, never this repository's own"
_PROMPT = "text of a prompt naming a path in the attempt's workspace; a prompt is not committed"
_GLOB = "a governance glob in a fixture decision row — a path pattern, not a file anything reads"

_ADMISSIBLE = {
    "scripts/e3_failure_mix.py": "a query that reads the stream and prints; it writes nothing committed",
    "src/torve/adapters/agent/fake.py": _RUNTIME,
    "src/torve/adapters/agent/harness.py": _RUNTIME,
    "src/torve/application/review.py": _PROMPT,
    "src/torve/base/naming.py": _RUNTIME,
    "src/torve/cli/gates.py": _RUNTIME,
    "src/torve/config/manifest.py": _RUNTIME,
    "src/torve/gates/sabotage.py": _SCRATCH,
    "tests/test_context.py": _SCRATCH,
    "tests/test_decisions.py": _GLOB,
    "tests/test_divergence.py": _SCRATCH,
    "tests/test_gates.py": "a scratch repository's tree, and the reason the live-log check skips with none",
    "tests/test_intake.py": _SCRATCH,
    "tests/test_lane.py": _SCRATCH,
    "tests/test_layout.py": "this ledger, and the dependency its twin plants in a scratch tree",
    "tests/test_ledger.py": _SCRATCH,
    "tests/test_provenance.py": _SCRATCH,
    "tests/test_residency.py": _SCRATCH,
    "tests/test_review_run.py": _PROMPT,
    "tests/test_rfc_check.py": _GLOB,
    "tests/test_run_loop.py": _SCRATCH,
    "tests/test_runner.py": _SCRATCH,
    "tests/test_specquality.py": _SCRATCH,
    "tests/test_tiering.py": _PROMPT,
}


def _uncommitted_patterns(root: Path) -> list[re.Pattern[str]]:
    """`.torve/.gitignore`'s lines, as regexes over a repository-relative path."""
    patterns = []
    for line in (root / ".torve" / ".gitignore").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "*" in line:
            body = "".join("[^/]*" if ch == "*" else re.escape(ch) for ch in line)
        else:
            # A name matches itself and never a longer sibling: `skills/` is
            # not committed, `skills-vendor/` is.
            body = re.escape(line.rstrip("/")) + r"(?![-\w])"
        patterns.append(re.compile(r"\.torve/" + body))
    return patterns


def _literal_references(root: Path) -> dict[str, list[int]]:
    """Every Python string literal under `root` that names an uncommitted path."""
    patterns = _uncommitted_patterns(root)
    found: dict[str, list[int]] = {}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if _UNSCANNED & set(relative.parts):
            continue
        name = relative.as_posix()
        if any(pattern.match(name) for pattern in patterns):
            continue  # the file itself is not committed
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        prose = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in prose
            and any(pattern.search(node.value) for pattern in patterns)
        ]
        if lines:
            found[name] = lines
    return found


def test_every_reference_to_an_uncommitted_path_carries_a_verdict() -> None:
    if not (_REPO_ROOT / ".torve" / ".gitignore").is_file():
        pytest.skip("no `.torve/.gitignore` in this checkout — nothing names what is uncommitted")

    referencing = _literal_references(_REPO_ROOT)

    unjudged = sorted(set(referencing) - set(_ADMISSIBLE))
    assert not unjudged, (
        "these name a path the repository does not commit and carry no verdict; judge "
        "each one — move it to the context pack, make it skip, or record why it is "
        f"admissible: {unjudged}"
    )
    stale = sorted(set(_ADMISSIBLE) - set(referencing))
    assert not stale, f"a verdict outlived its reference; drop it: {stale}"


def test_the_sweep_names_a_reintroduced_dependency(tmp_path: Path) -> None:
    # The twin: a projection reading an uncommitted path is the violation the
    # sweep exists to name, and it is reachable again the moment someone writes
    # one. Prose naming the same path is not, and neither is a sibling the
    # repository does commit.
    (tmp_path / ".torve").mkdir()
    (tmp_path / ".torve" / ".gitignore").write_text("telemetry.jsonl\nskills/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "render.py").write_text(
        '"""A projection of `.torve/telemetry.jsonl`."""\n'
        "def render(root):\n"
        '    return (root / ".torve/telemetry.jsonl").read_text()\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "vendored.py").write_text(
        'SKILLS = ".torve/skills-vendor"\n', encoding="utf-8"
    )

    assert _literal_references(tmp_path) == {"src/render.py": [3]}
