"""The task contract family (RFC 0001 §3, §6).

Pydantic models are the single source of truth (D-8); YAML files are their
serialization. Only the subset of the RFC 0001 domain that ships today is
modelled — ReviewFeedback arrives with RFC 0005.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from torve.domain.rfc import Grade

# ----------------------- #

SCHEMA_VERSION = 1


# ....................... #


class Scope(BaseModel):
    """allow/deny globs, gitwildmatch semantics. deny wins over allow; an empty
    allow means unconstrained (RFC 0002 §6)."""

    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


# ....................... #


class InheritedDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    grade: Grade
    text: str
    paths: list[str] = Field(default_factory=list)  # declared area; enables the silence check


# ....................... #


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iterations: int | None = None
    wallclock_minutes: int | None = None
    tokens: int | None = None


# ....................... #


class Task(BaseModel):
    """The task contract, `.torve/tasks/T-nnnn.yaml` (RFC 0001 §3, §6).

    `decisions` has no default on purpose: an empty list is legal but must be
    explicit (D-7.5), so `decisions-reported` can distinguish "none apply"
    from "the field was forgotten".
    """

    model_config = ConfigDict(extra="forbid")
    schema_version: int = SCHEMA_VERSION
    id: str
    rfc: str | None = None
    phase: int = 0
    role: Literal["implement", "review", "revert", "draft"] = "implement"

    # One paragraph: what changes and why — never steps (D-1.7, A-11).
    # Optional until the A-11 execution makes minting enforce it; contracts
    # minted before the amendment carry none.
    intent: str = ""
    depends_on: list[str] = Field(default_factory=list)

    # The tasks a review examines (RFC 0005 §1.1, D-5.9) or the tasks/shas a
    # revert undoes (RFC 0010 §7): the contract shape is parameterised by
    # role, no new mechanism. Only those two roles may carry targets.
    targets: list[str] = Field(default_factory=list)
    scope: Scope = Field(default_factory=Scope)
    acceptance: list[str] = Field(default_factory=list)  # shell commands; exit 0 == satisfied
    decisions: list[InheritedDecision]
    budget: Budget = Field(default_factory=Budget)
    tier: Literal["planner", "executor", "reviewer"] = "executor"

    # ....................... #

    @model_validator(mode="after")
    def _review_role_shape(self) -> Task:
        # D-5.10: a review's output is findings, not an exit code — carrying
        # acceptance commands is a contract error, not an empty pass.
        if self.role == "review":
            if self.acceptance:
                raise ValueError(
                    "a review task carries no acceptance commands — its output "
                    "is findings, and the acceptance gate is skipped for the role"
                )

            if not self.targets:
                raise ValueError("a review task names the task(s) it reviews in targets")
        elif self.role == "revert":
            if not self.targets:
                raise ValueError(
                    "a revert task names what it undoes in targets — task ids "
                    "or explicit commit shas"
                )
        elif self.role == "draft":
            # RFC 0020 D-20.3: the drafting run's gate is the contract lint,
            # not an exit code — acceptance commands are a contract error,
            # the same shape rule a review carries (D-5.10).
            if self.acceptance:
                raise ValueError(
                    "a draft task carries no acceptance commands — its gate "
                    "is the contract lint over its drafts"
                )

            if self.targets:
                raise ValueError("targets is not meaningful for role 'draft'")
        elif self.targets:
            raise ValueError(f"targets is not meaningful for role {self.role!r}")

        return self
