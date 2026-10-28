"""Builtin gates. Each targets a structural property (S-0002/starting-set); everything
else accumulates from observed leaks, as shell gates in the manifest."""

from __future__ import annotations

from collections.abc import Callable

from torve.config.manifest import Gate
from torve.gates.acceptance import check_acceptance
from torve.gates.context import GateContext
from torve.gates.contract import BuiltinOutcome
from torve.gates.decisions_reported import check_decisions_reported
from torve.gates.held_out import check_held_out
from torve.gates.mutation import check_mutation
from torve.gates.no_test_tampering import check_no_test_tampering
from torve.gates.red_on_base import check_red_on_base
from torve.gates.row_evidence import check_evidence
from torve.gates.scope import check_scope
from torve.gates.secrets import check_secrets
from torve.gates.self_audit import check_self_audit
from torve.gates.source_layout import check_source_layout
from torve.gates.user_facing_text import check_user_facing_text
from torve.gates.verify_tests import check_verify_tests

# ----------------------- #

Builtin = Callable[[Gate, GateContext], BuiltinOutcome]

BUILTINS: dict[str, Builtin] = {
    "scope": check_scope,
    "acceptance": check_acceptance,
    "no-test-tampering": check_no_test_tampering,
    "red-on-base": check_red_on_base,
    "evidence": check_evidence,
    "mutation": check_mutation,
    "decisions-reported": check_decisions_reported,
    "self-audit": check_self_audit,
    "secrets": check_secrets,
    "source-layout": check_source_layout,
    "user-facing-text": check_user_facing_text,
    "held-out": check_held_out,
    "verify-tests": check_verify_tests,
}

__all__ = ["BUILTINS", "Builtin", "BuiltinOutcome"]
