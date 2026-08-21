from __future__ import annotations

import pytest
from pydantic import ValidationError

from torve.models import Gate, Task


def test_decisions_must_be_explicit():
    """D-7.5: an empty decision list is legal but the field may not be absent."""
    with pytest.raises(ValidationError):
        Task.model_validate({"id": "T-1"})
    task = Task.model_validate({"id": "T-1", "decisions": []})
    assert task.decisions == []


def test_task_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        Task.model_validate({"id": "T-1", "decisions": [], "surprise": True})


def test_builtin_resolution():
    assert Gate(name="a", run="@task.acceptance").builtin == "acceptance"
    assert Gate(name="s", run="@scope").builtin == "scope"
    assert Gate(name="t", run="mypy src").builtin is None


def test_commands_only_for_acceptance():
    with pytest.raises(ValidationError):
        Gate(name="s", run="@scope", commands=["true"])
    Gate(name="a", run="@task.acceptance", commands=["true"])
