"""Builtin gates. Each targets a structural property (S-0002/starting-set); everything
else accumulates from observed leaks, as shell gates in the manifest."""

from __future__ import annotations

from collections.abc import Callable

from torve.config.manifest import Gate
from torve.gates.acceptance import check_acceptance
from torve.gates.context import GateContext
from torve.gates.contract import BuiltinOutcome
from torve.gates.no_test_tampering import check_no_test_tampering
from torve.gates.scope import check_scope
from torve.gates.secrets import check_secrets
from torve.gates.suite import check_suite

# ----------------------- #

Builtin = Callable[[Gate, GateContext], BuiltinOutcome]

BUILTINS: dict[str, Builtin] = {
    "scope": check_scope,
    "acceptance": check_acceptance,
    "no-test-tampering": check_no_test_tampering,
    "secrets": check_secrets,
    "suite": check_suite,
}

__all__ = ["BUILTINS", "Builtin", "BuiltinOutcome"]
