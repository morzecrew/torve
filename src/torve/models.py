"""Contracts for the gates increment (RFC 0002), inheriting RFC 0001 §3.

Pydantic models are the single source of truth (D-8); YAML files are their
serialization. Only the subset of the RFC 0001 domain that gates consume is
modelled here — Attempt and ReviewFeedback arrive with the runner (RFC 0003).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1

Grade = Literal["LOCKED", "ASSUMED", "OPEN"]
GateInput = Literal["worktree", "diff", "task", "log"]
# pass/fail per RFC 0002 §3; flaky per D-2.6; bypassed per D-2.7; skipped for
# gates whose input does not exist on this run (recorded, never silently green);
# error for gate-infrastructure failures, kept distinct from a red result.
GateState = Literal["shadow", "blocking", "quarantined"]
GateOutcome = Literal["pass", "fail", "flaky", "skipped", "bypassed", "error"]


class Scope(BaseModel):
    """allow/deny globs, gitwildmatch semantics. deny wins over allow; an empty
    allow means unconstrained (RFC 0002 §6)."""

    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class InheritedDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    grade: Grade
    text: str
    paths: list[str] = Field(default_factory=list)  # declared area; enables the silence check


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iterations: int | None = None
    wallclock_minutes: int | None = None
    tokens: int | None = None


class Task(BaseModel):
    """The task contract, `tasks/T-nnnn.yaml` (RFC 0001 §3, §6).

    `decisions` has no default on purpose: an empty list is legal but must be
    explicit (D-7.5), so `decisions-reported` can distinguish "none apply"
    from "the field was forgotten".
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    id: str
    rfc: str | None = None
    phase: int = 0
    role: Literal["implement", "review"] = "implement"
    depends_on: list[str] = Field(default_factory=list)
    scope: Scope = Field(default_factory=Scope)
    acceptance: list[str] = Field(default_factory=list)  # shell commands; exit 0 == satisfied
    decisions: list[InheritedDecision]
    budget: Budget = Field(default_factory=Budget)
    tier: Literal["planner", "executor", "reviewer"] = "executor"


class Gate(BaseModel):
    """One entry in gates.yaml (RFC 0002 §3, lifecycle §7 per A-8).

    `run` is a shell command, or an `@`-prefixed builtin reference. The RFC's
    `@task.acceptance` is the acceptance builtin; the other builtins follow the
    same convention (`@scope`, `@secrets`, ...). `commands` is the acceptance
    fallback for runs with no task file.

    `state` and `origin` are required on every entry (D-2.19): a boolean
    cannot express shadow or quarantine, and provenance is unrecoverable
    later. `shadow` and `quarantined` gates run and report but never affect
    the exit code (§7.3).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    run: str
    state: GateState
    origin: str  # structural | leak/<task> | rfc/<id> — why this gate exists
    added: date | None = None
    input: GateInput | None = None  # derived for builtins; defaults to worktree for shell gates
    timeout: float | None = None  # seconds; derived for builtins, 600 for shell gates
    commands: list[str] = Field(default_factory=list)

    @field_validator("origin")
    @classmethod
    def _origin_shape(cls, value: str) -> str:
        if value == "structural" or value.startswith(("leak/", "rfc/")):
            return value
        raise ValueError(
            f"origin {value!r} must be 'structural', 'leak/<task>' or 'rfc/<id>' (D-2.19)"
        )

    @property
    def builtin(self) -> str | None:
        if self.run == "@task.acceptance":
            return "acceptance"
        if self.run.startswith("@"):
            return self.run[1:]
        return None

    @model_validator(mode="after")
    def _commands_only_for_acceptance(self) -> Gate:
        if self.commands and self.builtin != "acceptance":
            raise ValueError(f"gate {self.name!r}: 'commands' only applies to @acceptance")
        return self


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


class SizeVerdict(BaseModel):
    """Pre-dispatch size estimate (RFC 0002 §6b, D-2.9)."""

    model_config = ConfigDict(extra="forbid")

    size: Literal["ok", "too_large", "too_small"]
    reasons: list[str] = Field(default_factory=list)
