"""What a gate pass produces (RFC 0002 §3): results, bypass records, size
verdicts — and what a review run produces (RFC 0005 §2): findings, the
structured output whose severities are data; configuration, never the
model, decides whether one stops the work (D-2).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from torve.domain.task import SCHEMA_VERSION

# ----------------------- #

GateInput = Literal["worktree", "diff", "task", "log"]
GateState = Literal["shadow", "blocking", "quarantined"]
# pass/fail per RFC 0002 §3; flaky per D-2.6; bypassed per D-2.7; skipped for
# gates whose input does not exist on this run (recorded, never silently green);
# error for gate-infrastructure failures, kept distinct from a red result.
GateOutcome = Literal["pass", "fail", "flaky", "skipped", "bypassed", "error"]


class BypassRecord(BaseModel):
    """A human's Torve-Bypass commit trailer (D-2.7): the signature is the
    commit's authorship, the reason is mandatory, and the record is counted."""

    model_config = ConfigDict(extra="forbid")

    gate: str
    reason: str
    author: str
    commit: str


class GateResult(BaseModel):
    """Every result is persisted: name, exit code, duration, sha, truncated
    output and a log reference. A green with no artefact does not count."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    name: str
    outcome: GateOutcome
    state: GateState
    exit_code: int | None = None
    duration_s: float = 0.0
    sha: str = ""
    output: str = ""
    log_ref: str | None = None
    bypass: BypassRecord | None = None
    flaky_commands: list[str] = Field(default_factory=list)
    quarantined_failures: list[str] = Field(default_factory=list)


# Severity discipline (RFC 0005 §5): blocker stops the run by configuration;
# major a reviewer would insist on; minor/nit are preferences, rate-limited.
FindingSeverity = Literal["blocker", "major", "minor", "nit"]


class Finding(BaseModel):
    """One review finding (RFC 0005 §2): a claim with severities as data and
    evidence in the execution log's format — a leading path:line citation or
    a backticked command with output — so the same locator that checks log
    entries can discard a finding nothing can resolve (D-5.4)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    severity: FindingSeverity
    claim: str
    evidence: str


class SizeVerdict(BaseModel):
    """Pre-dispatch size estimate (RFC 0002 §6b, D-2.9)."""

    model_config = ConfigDict(extra="forbid")

    size: Literal["ok", "too_large", "too_small"]
    reasons: list[str] = Field(default_factory=list)
