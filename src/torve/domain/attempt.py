"""What a gate pass produces (S-0002/the-gate-contract): results, bypass records, size
verdicts — and what a review run produces (S-0005/what-makes-review-independent-rather-than-ceremonial): findings, the
structured output whose severities are data; configuration, never the
model, decides whether one stops the work (S-0001/D-10).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field

from torve.base.model import STRICT
from torve.domain.vocabulary import FindingSeverity, GateOutcome, GateState

# ----------------------- #

# The attempt shapes' own version — gate results, findings (T-0321).
SCHEMA_VERSION = 1

# ....................... #


class BypassRecord(BaseModel):
    """A human's Torve-Bypass commit trailer (S-0002/D-7): the signature is the
    commit's authorship, the reason is mandatory, and the record is counted."""

    model_config = STRICT

    gate: str
    reason: str
    author: str
    commit: str


# ....................... #


class GateResult(BaseModel):
    """Every result is persisted: name, exit code, duration, sha, truncated
    output and a log reference. A green with no artefact does not count."""

    model_config = STRICT

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


# ....................... #

# The `mutation` gate's report line (S-0107/D-2): `mutation score 0.75: ...`.
_MUTATION_SCORE = re.compile(r"mutation score ([0-9]*\.?[0-9]+):")


def mutation_reading(result: Mapping[str, object]) -> dict[str, object] | None:
    """The `mutation` gate's reading of one recorded result (S-0107/D-3): the
    score and the survivors the gate reported, parsed from the record a reader
    already holds. A result that produced no score — a gate skipped, or one
    that errored before the mutants ran — has no reading, so a reader shows
    nothing rather than a zero (the absent-is-absent rule of S-0004/D-6)."""

    if str(result.get("name") or "") != "mutation":
        return None

    outcome = str(result.get("outcome") or "")

    if outcome == "pass":
        # Every sampled mutant died, so the score is one and nothing survived.
        # A green result's output is not kept, but the verdict is the fact.
        return {"score": 1.0, "survivors": []}

    if outcome != "fail":
        return None

    score: float | None = None
    survivors: list[str] = []

    # Both are read by shape, not by position: the record keeps only a red
    # output's tail, so the head line carrying the score and the `survivors:`
    # marker can both fall outside what survives. The score line names the
    # gate's sums, every survivor line is `path:line — edit`, and nothing
    # else carries the em-dash separator.
    for line in str(result.get("output") or "").splitlines():
        found = _MUTATION_SCORE.match(line)

        if found:
            score = float(found.group(1))

        elif " — " in line:
            survivors.append(line)

    return None if score is None else {"score": score, "survivors": survivors}


# ....................... #

# ....................... #


class Finding(BaseModel):
    """One review finding (S-0005/what-makes-review-independent-rather-than-ceremonial): a claim with severities as data and
    evidence in the execution log's format — a leading path:line citation or
    a backticked command with output — so the same locator that checks log
    entries can discard a finding nothing can resolve (S-0005/D-4)."""

    model_config = STRICT

    schema_version: int = SCHEMA_VERSION
    severity: FindingSeverity
    claim: str
    evidence: str


# ....................... #


class SizeVerdict(BaseModel):
    """Pre-dispatch size estimate (S-0002/task-size, S-0002/D-9)."""

    model_config = STRICT

    size: Literal["ok", "too_large", "too_small"]
    reasons: list[str] = Field(default_factory=list)
