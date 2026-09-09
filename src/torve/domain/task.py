"""The task contract family (S-0001/domain, §6).

Pydantic models are the single source of truth (S-0001/D-21); YAML files are their
serialization. Only the subset of the S-0001 domain that ships today is
modelled — ReviewFeedback arrives with S-0005.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from torve.base.model import STRICT
from torve.domain.vocabulary import Character, CheckState, Grade, Role, Tier

# ----------------------- #

# The engine's shape version, borrowed by every envelope the engine writes —
# telemetry, run state, the manifest, the projections. Not the contract's.
SCHEMA_VERSION = 1
# S-0059/D-1: the contract's own — 2 names the document as `spec: S-NNNN`;
# 1 carried `rfc`, a path.
CONTRACT_SCHEMA_VERSION = 2

# The document grammar (S-0058/D-1), spelled here because `domain/spec.py`
# imports this module for the contract example and compiles the pattern from it.
SPEC_PATTERN = r"^S-\d{4}$"


# ....................... #


class Scope(BaseModel):
    """allow/deny globs, gitwildmatch semantics. deny wins over allow; an empty
    allow means unconstrained (S-0002/scope-in-detail)."""

    model_config = STRICT

    allow: list[str] = Field(default_factory=list)
    """Globs of what the task may touch, gitwildmatch; empty means unconstrained."""
    deny: list[str] = Field(default_factory=list)
    """Globs the task may not touch; deny wins over allow."""


# ....................... #


class InheritedDecision(BaseModel):
    """One row as a contract carries it (charter §3; S-0054 S-0054/D-1): grade,
    text and paths copied at mint and fingerprinted; `consequence` beside
    them so the executor gets the reason, and `check` — a command whose
    exit code judges the row — which the runner appends to the battery as
    a `decision:<id>` gate at `check_state`, with `check_twin` the test
    that proves the check can fail (S-0054/D-4). A contract minted before
    S-0054 loads with the four defaults, which is the old behaviour."""

    model_config = STRICT

    id: str
    """The row's global identifier, `S-NNNN/D-n`."""
    grade: Grade
    """The row's grade as it stood at mint — copied, so the executor reads what the author settled."""
    text: str
    """The decision's text as it stood at mint."""
    paths: list[str] = Field(default_factory=list)
    """The area the row governs, declared; what enables the silence check."""
    consequence: str = ""
    """Why the row exists — the reason beside the rule, so the executor gets both (S-0054/D-1)."""
    check: str | None = None
    """A command whose exit code judges the row, run as the `decision:<id>` gate (S-0054/D-4)."""
    check_state: CheckState = "shadow"
    """The check's gate state: `shadow` until an amendment promotes it (S-0054/D-4)."""
    check_twin: str | None = None
    """The test that proves the check can fail (S-0054/D-4)."""


# ....................... #


class Budget(BaseModel):
    """What an attempt may spend (S-0001/domain); None on an axis is no bound there."""

    model_config = STRICT

    iterations: int | None = None
    """The most attempts the loop makes before it escalates; a drafting run's drafts."""
    wallclock_minutes: int | None = None
    """The wall-clock bound of an attempt, in minutes — declared, read by no leg yet."""
    tokens: int | None = None
    """The tokens the broker lets an attempt spend, enforced mid-run (S-0045/D-4)."""


# ....................... #

# The roles a worker may take off the board. Review and draft contracts are
# runner-minted mid-run (S-0005/D-2, S-0020/D-2) and conclude with the run that
# minted them, so nobody claims one — they are recorded (S-0049/A-1) because the
# record is what the planning projections read, and offered to nobody.
DISPATCHABLE_ROLES = ("implement", "revert")


# ....................... #


class Task(BaseModel):
    """The task contract, `.torve/tasks/T-NNNN/contract.yaml` (S-0001/domain, §6).

    `decisions` has no default on purpose: an empty list is legal but must be
    explicit (S-0007/D-5), so `decisions-reported` can distinguish "none apply"
    from "the field was forgotten".
    """

    model_config = STRICT

    schema_version: int = CONTRACT_SCHEMA_VERSION
    """The contract's shape version: 2 names the document as `spec` (S-0059/D-1)."""
    id: str
    """`T-NNNN`, minted once and never reused."""
    spec: str | None = Field(default=None, pattern=SPEC_PATTERN)
    """The document the contract was minted from, by identifier and never by path
    (S-0059/D-1) — `document_dir` is the one lookup that finds it. None is the
    document-less lane: an operator's ask, a standing job."""
    phase: int = 0
    """The phasing entry the contract was minted from; 0 when no phase minted it."""
    role: Role = "implement"
    """What the run does: implement, review (S-0005/D-9), revert (S-0010) or draft (S-0020)."""
    title: str = ""
    """A short human name (S-0007/A-1): the landing subject and every board row read
    it; empty falls back to the intent's first line. The planner mints it from the
    phase title; a drafter may set it."""
    intent: str = ""
    """One paragraph: what changes and why — never steps (S-0001/D-7, S-0001/A-4).
    Optional until the S-0001/A-4 execution makes minting enforce it; contracts
    minted before the amendment carry none."""
    depends_on: list[str] = Field(default_factory=list)
    """The tasks that must land before this one dispatches."""
    parent: str | None = None
    """Set only at adoption of a decomposition's children (S-0026/D-5): projections
    group by it; dispatch, lane and store never read it — ordering stays
    depends_on alone."""
    targets: list[str] = Field(default_factory=list)
    """The tasks a review examines (S-0005/the-review-contract, S-0005/D-9) or the
    tasks and shas a revert undoes (S-0010/revert-as-a-role): the contract shape
    is parameterised by role, no new mechanism. Only those two roles carry targets."""
    scope: Scope = Field(default_factory=Scope)
    """What the attempt may and may not touch (S-0002/scope-in-detail)."""
    acceptance: list[str] = Field(default_factory=list)
    """Shell commands; exit 0 is satisfied."""
    decisions: list[InheritedDecision]
    """The rows inherited at mint, grade and paths copied (S-0007/D-5); an empty
    list is legal but must be explicit."""
    budget: Budget = Field(default_factory=Budget)
    """What an attempt may spend."""
    tier: Tier = "executor"
    """The seat that runs it — planner, executor or reviewer (S-0004)."""
    tier_variant: str | None = None
    """An optional dotted variant under the seat (S-0027/D-3), resolved as
    `tier.variant` in the tiers mapping — a variant refines a seat, never invents
    one, and role semantics still key on `tier` alone. Naming a variant that is
    not configured is a refused dispatch, not a fallback."""
    character: Character | None = None
    """The phase's declared structural or routine character, copied verbatim from
    the phasing entry at mint (S-0034/D-1). Absent by default — a task with no
    character routes on the seat alone, same as one with no tier_variant."""

    # ....................... #

    @model_validator(mode="before")
    @classmethod
    def _spec_not_rfc(cls, data: Any) -> Any:
        # S-0059/D-1: the key S-0057 retired, refused with the one it wants —
        # `extra="forbid"` alone would say "extra inputs are not permitted".
        if isinstance(data, dict) and "rfc" in data:
            raise ValueError(
                "`rfc` is `spec` since S-0059 (S-0059/D-1): the contract names its "
                "document by identifier, `spec: S-NNNN`"
            )

        return data

    # ....................... #

    @model_validator(mode="after")
    def _review_role_shape(self) -> Task:
        # S-0005/D-10: a review's output is findings, not an exit code — carrying
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
            # S-0020 S-0020/D-3: the drafting run's gate is the contract lint,
            # not an exit code — acceptance commands are a contract error,
            # the same shape rule a review carries (S-0005/D-10).
            if self.acceptance:
                raise ValueError(
                    "a draft task carries no acceptance commands — its gate "
                    "is the contract lint over its drafts"
                )

            # A request-driven draft names no target; a decomposition run
            # (S-0026/the-decomposition-run) names exactly one — the oversized contract it
            # decomposes, the same targets-name-what-it-acts-on shape review
            # and revert already carry.
            if len(self.targets) > 1:
                raise ValueError(
                    "a draft task names at most one target — the contract "
                    "it decomposes, when it is a decomposition run"
                )
        elif self.targets:
            raise ValueError(f"targets is not meaningful for role {self.role!r}")

        return self
