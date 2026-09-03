"""gates.yaml — the reviewed gate manifest, one per consuming repository
(D-2.5): the Gate entry model and the manifest it composes into.
"""

from __future__ import annotations

import warnings
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from torve.domain.attempt import GateInput, GateState
from torve.domain.task import SCHEMA_VERSION, Scope

# ----------------------- #

# What a conviction from this gate means (D-34.4). The tuple is the vocabulary
# in the order the corpus lists it — retry selection reads it as a severity
# order, but that reading is the runner's rule, not this file's. An unlabeled
# gate resolves to UNLABELED_AXIS in `resolved_gates()`: the fail-safe routes
# its retry to the heaviest rung, so a missing label costs money, never
# correctness.
GateAxis = Literal["functional", "boundary", "compliance", "form"]
GATE_AXES: tuple[GateAxis, ...] = ("functional", "boundary", "compliance", "form")
UNLABELED_AXIS: GateAxis = "functional"


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

    `axis` is the optional conviction label (D-34.4): what a red result from
    this gate means. An entry without it reads as `functional` once
    `resolved_gates()` fills the default; the declaration itself stays absent
    so the manifest diff shows only labels the operator chose.

    `sabotage` names this gate's twin — a CASES family in the sabotage suite
    or a repository test path — the evidence that the gate can convict (D-36.3).
    The load refuses a twinless entry once the manifest names a twin for any
    gate; a manifest naming none at all predates the field and is only voiced
    by `twinless_gates()`. The value must be a non-blank reference; resolving
    it against the CASES families and the tree cannot happen here — the
    sabotage suite imports this model, so the cross-check lives in the
    repository test that pins the shipped battery.
    """

    model_config = ConfigDict(extra="forbid")
    name: str
    run: str
    state: GateState
    origin: str  # structural | leak/<task> | rfc/<id> — why this gate exists
    added: date | None = None
    input: GateInput | None = None  # derived for builtins; defaults to worktree for shell gates
    timeout: float | None = None  # seconds; derived for builtins, 600 for shell gates
    axis: GateAxis | None = None  # derived for unlabeled entries, functional
    sabotage: str | None = None  # the twin's CASES family or test path (D-36.3)
    commands: list[str] = Field(default_factory=list)

    # ....................... #

    @field_validator("origin")
    @classmethod
    def _origin_shape(cls, value: str) -> str:
        if value == "structural" or value.startswith(("leak/", "rfc/")):
            return value

        raise ValueError(
            f"origin {value!r} must be 'structural', 'leak/<task>' or 'rfc/<id>' (D-2.19)"
        )

    # ....................... #

    @field_validator("sabotage")
    @classmethod
    def _sabotage_shape(cls, value: str | None) -> str | None:
        if value is None:
            return value

        if not value.strip():
            raise ValueError("gate sabotage twin must be a non-blank reference (D-36.3)")

        return value

    # ....................... #

    @property
    def builtin(self) -> str | None:
        if self.run == "@task.acceptance":
            return "acceptance"

        if self.run.startswith("@"):
            return self.run[1:]

        return None

    # ....................... #

    @model_validator(mode="after")
    def _commands_only_for_acceptance(self) -> Gate:
        if self.commands and self.builtin != "acceptance":
            raise ValueError(f"gate {self.name!r}: 'commands' only applies to @acceptance")

        return self


# ....................... #


class TwinlessGateWarning(UserWarning):
    """A gate entry declares no sabotage twin (D-36.3), in a manifest that
    declares none at all.

    The voice on the pre-field side of the refusal: a manifest where no
    entry names a twin — scenario data in the shipped suites, a repository
    that has not adopted the field yet — loads with its twinless entries
    named. The moment any entry names one, the manifest has adopted the
    field and the load refuses the rest; the shipped battery is fully
    twinned, so this warning names only data that cannot backfill itself.
    """


# ....................... #

# Builtins: expected input and a cost proxy used for cheapest-first ordering.
# Diff-, task- and log-input builtins are near-free; only acceptance shells out.
BUILTIN_INPUTS: dict[str, str] = {
    "scope": "diff",
    "secrets": "diff",
    "no-test-tampering": "diff",
    "decisions-reported": "log",
    "self-audit": "log",
    "source-layout": "diff",
    "user-facing-text": "diff",
    "acceptance": "worktree",
}
BUILTIN_TIMEOUTS: dict[str, float] = {
    "scope": 30,
    "secrets": 30,
    "no-test-tampering": 30,
    "decisions-reported": 30,
    "self-audit": 30,
    "source-layout": 30,
    "user-facing-text": 30,
    "acceptance": 600,
}
SHELL_GATE_TIMEOUT = 600.0

DEFAULT_TEST_PATTERNS = [
    "tests/**",
    "test/**",
    "**/test_*.py",
    "**/*_test.py",
    "**/*.test.*",
    "**/*.spec.*",
]


# ....................... #


class TestsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_TEST_PATTERNS))


# ....................... #


class SecretsConfig(BaseModel):
    """`allow_patterns` are regexes that suppress a match on the line they
    match. Reviewed configuration, not a bypass: the manifest arrives in a
    pull request, and D-2.8 stays intact because no signature at run time can
    widen it."""

    model_config = ConfigDict(extra="forbid")

    allow_patterns: list[str] = Field(default_factory=list)


# ....................... #


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = SCHEMA_VERSION
    scope: Scope = Field(default_factory=Scope)
    tests: TestsConfig = Field(default_factory=TestsConfig)
    secrets: SecretsConfig = Field(default_factory=SecretsConfig)

    # Acceptance commands that flake; their failures are recorded but stop
    # blocking until fixed (RFC 0002 §6a). Maintained by humans from the flake
    # counters in telemetry until a store exists (RFC 0003).
    quarantine: list[str] = Field(default_factory=list)
    telemetry: str = ".torve/telemetry.jsonl"
    gates: list[Gate] = Field(default_factory=list)

    # ....................... #

    def twinless_gates(self) -> list[str]:
        """Names of entries declaring no sabotage twin (D-36.3)."""

        return [gate.name for gate in self.gates if gate.sabotage is None]

    # ....................... #

    def resolved_gates(self) -> list[Gate]:
        """Gates with input, timeout and axis filled in from builtin defaults
        and the unlabeled default."""

        resolved: list[Gate] = []

        for gate in self.gates:
            builtin = gate.builtin

            if builtin is not None and builtin not in BUILTIN_INPUTS:
                raise ValueError(f"gate {gate.name!r}: unknown builtin {gate.run!r}")

            update: dict[str, object] = {}

            if gate.input is None:
                update["input"] = BUILTIN_INPUTS.get(builtin or "", "worktree")

            if gate.timeout is None:
                update["timeout"] = BUILTIN_TIMEOUTS.get(builtin or "", SHELL_GATE_TIMEOUT)

            if gate.axis is None:
                update["axis"] = UNLABELED_AXIS

            resolved.append(gate.model_copy(update=update) if update else gate)

        names = [g.name for g in resolved]

        if len(names) != len(set(names)):
            raise ValueError("gate names must be unique")

        return resolved


# ....................... #


def load_manifest(path: Path) -> Manifest:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    if not isinstance(raw, dict):
        raise ValueError(f"{path}: manifest must be a mapping")

    manifest = Manifest.model_validate(raw)
    manifest.resolved_gates()  # surface builtin/name errors at load time

    # D-36.3, refusal stage: a manifest that names the twin for any gate has
    # adopted the field, and every remaining twinless entry is a load error —
    # a gate that cannot prove it convicts cannot be declared. A manifest
    # naming no twins at all predates the field and is voiced, not refused:
    # the shipped scenario data builds one twinless manifest per case, and
    # the refusal must not reach through the self-hosting boundary into it.
    # The strings carry no decision coordinate — they surface on whoever's
    # terminal is running the command.
    twinless = manifest.twinless_gates()

    if twinless:
        names = ", ".join(twinless)

        if any(gate.sabotage is not None for gate in manifest.gates):
            raise ValueError(
                f"{path}: gate entries without a declared sabotage twin: {names} — "
                "a manifest that names the twin for one gate must name it for every "
                "gate: a CASES family in the shipped sabotage suite, or the path of "
                "the test file that reddens when the gate stops working"
            )

        warnings.warn(
            f"{path}: gate entries without a declared sabotage twin "
            f"(a CASES family or a test path): {names} — "
            "a manifest that names the twin for any gate refuses these outright",
            TwinlessGateWarning,
            stacklevel=2,
        )

    return manifest
