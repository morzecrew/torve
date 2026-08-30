"""RFC 0026: the too_large route's predicate (D-26.7) — `estimate_scope`'s
three rules of thumb, and `has_children`/`awaiting_decomposition`, which
decide whether a too_large verdict routes a second time once a
decomposition has already been adopted (D-26.6)."""

from __future__ import annotations

from pathlib import Path

import yaml

from torve.application import sizing
from torve.domain.task import Scope, Task

# ----------------------- #


def test_estimate_scope_is_ok_under_every_threshold():
    verdict = sizing.estimate_scope(Scope(allow=["src/a.py"]), ["true"])
    assert verdict.size == "ok"


def test_estimate_scope_too_large_on_allow_glob_count():
    allow = [f"src/m{n}.py" for n in range(sizing.MAX_ALLOW_GLOBS + 1)]
    verdict = sizing.estimate_scope(Scope(allow=allow), [])
    assert verdict.size == "too_large"
    assert "allow globs" in verdict.reasons[0]


def test_estimate_scope_too_large_on_acceptance_count():
    acceptance = [f"cmd{n}" for n in range(sizing.MAX_ACCEPTANCE + 1)]
    verdict = sizing.estimate_scope(Scope(allow=["src/a.py"]), acceptance)
    assert verdict.size == "too_large"
    assert "acceptance commands" in verdict.reasons[0]


def test_estimate_scope_too_large_on_module_count():
    verdict = sizing.estimate_scope(Scope(allow=["src/a.py", "tests/a.py"]), [])
    assert verdict.size == "too_large"
    assert "top-level modules" in verdict.reasons[0]


def test_estimate_scope_too_small_on_nothing_declared():
    verdict = sizing.estimate_scope(Scope(), [])
    assert verdict.size == "too_small"


def _write_contract(root: Path, task_id: str, parent: str | None = None) -> None:
    task_dir = root / ".torve" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    data: dict = {"schema_version": 1, "id": task_id, "role": "implement", "decisions": []}
    if parent:
        data["parent"] = parent
    (task_dir / "contract.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_has_children_true_only_once_a_child_names_the_parent(tmp_path: Path):
    assert sizing.has_children(tmp_path, "T-0100") is False
    _write_contract(tmp_path, "T-0100")
    _write_contract(tmp_path, "T-0101", parent="T-0100")
    assert sizing.has_children(tmp_path, "T-0100") is True
    assert sizing.has_children(tmp_path, "T-0101") is False


def test_awaiting_decomposition_only_for_too_large_and_childless(tmp_path: Path):
    ok_task = Task(id="T-1", scope=Scope(allow=["src/a.py"]), decisions=[])
    assert sizing.awaiting_decomposition(tmp_path, ok_task) is False

    oversized = Task(id="T-0100", scope=Scope(allow=["src/a.py", "tests/a.py"]), decisions=[])
    assert sizing.awaiting_decomposition(tmp_path, oversized) is True

    _write_contract(tmp_path, "T-0100")
    _write_contract(tmp_path, "T-0101", parent="T-0100")
    # The same oversized contract, once it has adopted children, is the
    # integration task — its verdict does not route a second time.
    assert sizing.awaiting_decomposition(tmp_path, oversized) is False
