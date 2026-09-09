"""Every closed word the engine reads from a file or writes to the record,
defined once (S-0059/D-5; S-0007/D-13's rule): a vocabulary duplicated
eventually gains a member in one copy only. A word one field alone uses stays
with that field; what stands here is shared by two models, or by a file and
the record. The tuples beside the words are the same words for a help text or
a gate's set — `get_args`, spelled once.
"""

from __future__ import annotations

from typing import Literal, get_args

# ----------------------- #
# The corpus (S-0007/D-13, S-0053).

Grade = Literal["LOCKED", "ASSUMED", "OPEN"]
Status = Literal["draft", "accepted", "superseded"]
Kind = Literal["design", "convention"]
# A judgement, never progress (S-0016/D-21): progress is store-derived and would
# diverge on the first escalation.
Implementation = Literal["none", "partial", "complete", "abandoned"]
Coverage = Literal["governed", "ungoverned", "retired"]
CheckState = Literal["shadow", "blocking"]
QuestionStatus = Literal["open", "settled"]

GRADES: tuple[Grade, ...] = get_args(Grade)
STATUSES: tuple[Status, ...] = get_args(Status)
KINDS: tuple[Kind, ...] = get_args(Kind)
IMPLEMENTATIONS: tuple[Implementation, ...] = get_args(Implementation)

# ----------------------- #
# The divergence entry (S-0001/decisions): the log's words, the record's and
# the gate's. The log grades a divergence against the corpus, so it carries
# one word the corpus itself does not — an entry may be about a decision no
# document lists.

EntryGrade = Literal["LOCKED", "ASSUMED", "OPEN", "UNLISTED"]
EntryKind = Literal["contradicted", "departed", "resolved", "blocked"]
EntryClass = Literal["discovery", "spec-gap", "drift", "irreducible"]
EntryAction = Literal["halted", "departed", "decided"]

ENTRY_GRADES: tuple[EntryGrade, ...] = get_args(EntryGrade)
ENTRY_KINDS: tuple[EntryKind, ...] = get_args(EntryKind)
ENTRY_CLASSES: tuple[EntryClass, ...] = get_args(EntryClass)
ENTRY_ACTIONS: tuple[EntryAction, ...] = get_args(EntryAction)

# ----------------------- #
# The contract (S-0001/domain): what a task is, who takes it, how it routes.

Role = Literal["implement", "review", "revert", "draft"]
Tier = Literal["planner", "executor", "reviewer"]
# S-0034/D-1: a phase's declared character, copied onto its contracts.
Character = Literal["structural", "routine"]

# ----------------------- #
# The gate pass (S-0002/the-gate-contract).

GateInput = Literal["worktree", "diff", "task", "log"]
GateState = Literal["shadow", "blocking", "quarantined"]
# pass/fail per S-0002/the-gate-contract; flaky per S-0002/D-6; bypassed per
# S-0002/D-7; skipped for gates whose input does not exist on this run
# (recorded, never silently green); error for gate-infrastructure failures,
# kept distinct from a red result.
GateOutcome = Literal["pass", "fail", "flaky", "skipped", "bypassed", "error"]
# What a conviction from a gate means (S-0034/D-4), in the order the corpus
# lists it — retry selection reads that order as a severity order, but the
# reading is the runner's rule, not this module's.
GateAxis = Literal["functional", "boundary", "compliance", "form"]
GATE_AXES: tuple[GateAxis, ...] = get_args(GateAxis)

# ----------------------- #
# Review (S-0005/calibration): blocker stops the run by configuration; major a
# reviewer would insist on; minor/nit are preferences, rate-limited.

FindingSeverity = Literal["blocker", "major", "minor", "nit"]

# ----------------------- #
# Provenance (S-0044/D-8): the shapes a source may have.

SourceKind = Literal["specification", "incident", "audit", "review", "operator"]
