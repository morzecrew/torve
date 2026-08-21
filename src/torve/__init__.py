"""Torve — deterministic gates for agent and human pull requests.

A curated lazy front door (RFC 0015 §5, D-15.7): names resolve to their
canonical modules through PEP 562, so `import torve` stays cheap — the
gates-only CI path never imports the runner, the store or an adapter. The
deep paths stay reachable; this table is the public surface a newcomer sees.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # The same table, statically visible: type checkers resolve the names
    # here; at runtime the imports below never execute and PEP 562 serves.
    from torve.config.manifest import Gate, Manifest, load_manifest
    from torve.config.runconfig import RunnerConfig, load_runner_config
    from torve.domain.attempt import BypassRecord, GateResult, SizeVerdict
    from torve.domain.states import EscalationReason, TaskState
    from torve.domain.task import Budget, InheritedDecision, Scope, Task
    from torve.gates.context import GateContext, build_context, load_task
    from torve.gates.runner import run_gates

__version__ = "0.1.0"

# ----------------------- #

_EXPORTS: dict[str, str] = {
    # domain
    "Task": "torve.domain.task",
    "Scope": "torve.domain.task",
    "InheritedDecision": "torve.domain.task",
    "Budget": "torve.domain.task",
    "GateResult": "torve.domain.attempt",
    "BypassRecord": "torve.domain.attempt",
    "SizeVerdict": "torve.domain.attempt",
    "TaskState": "torve.domain.states",
    "EscalationReason": "torve.domain.states",
    # configuration
    "Gate": "torve.config.manifest",
    "Manifest": "torve.config.manifest",
    "load_manifest": "torve.config.manifest",
    "RunnerConfig": "torve.config.runconfig",
    "load_runner_config": "torve.config.runconfig",
    # the standalone gates library (RFC 0002)
    "build_context": "torve.gates.context",
    "GateContext": "torve.gates.context",
    "load_task": "torve.gates.context",
    "run_gates": "torve.gates.runner",
}

# Literal so type checkers can read it; a test asserts it matches _EXPORTS.
__all__ = [
    "Budget",
    "BypassRecord",
    "EscalationReason",
    "Gate",
    "GateContext",
    "GateResult",
    "InheritedDecision",
    "Manifest",
    "RunnerConfig",
    "Scope",
    "SizeVerdict",
    "Task",
    "TaskState",
    "__version__",
    "build_context",
    "load_manifest",
    "load_runner_config",
    "load_task",
    "run_gates",
]


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module), name)


def __dir__() -> list[str]:
    return __all__
