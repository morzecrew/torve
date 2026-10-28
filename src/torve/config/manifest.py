"""gates.yaml — the reviewed gate manifest, one per consuming repository
(S-0002/D-5): the Gate entry model and the manifest it composes into.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Annotated

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from torve.base.globs import matches
from torve.base.model import STRICT, SchemaVersion
from torve.config import layout as layout_module
from torve.domain.spec import is_citation
from torve.domain.task import Scope
from torve.domain.vocabulary import GateAxis, GateInput, GateState

# ----------------------- #

# The gate manifest's own shape version (T-0321).
SCHEMA_VERSION = 1

# An unlabeled gate resolves to UNLABELED_AXIS in `resolved_gates()` (S-0034/D-4):
# the fail-safe routes its retry to the heaviest rung, so a missing label
# costs money, never correctness.
UNLABELED_AXIS: GateAxis = "functional"

# The files that ride with a source out of the box (S-0104/D-5): a scope that
# admits a manifest admits the lock file a dependency change rewrites, because
# refusing the lock refuses the change the scope allowed (bloomery T-0008,
# 2026-09-19: 24 minutes of work convicted on `outside allow: uv.lock`). The
# map is the default `ManifestScope.derived`, so a repository that names its
# own generated files replaces it rather than editing a table in the engine.
DEFAULT_DERIVED: dict[str, tuple[str, ...]] = {
    "pyproject.toml": ("uv.lock", "poetry.lock", "pdm.lock"),
    "Pipfile": ("Pipfile.lock",),
    "package.json": ("package-lock.json", "pnpm-lock.yaml", "yarn.lock"),
    "Cargo.toml": ("Cargo.lock",),
    "go.mod": ("go.sum",),
}


class Gate(BaseModel):
    """One entry in gates.yaml (S-0002/the-gate-contract, lifecycle §7 per S-0002/A-2).

    `run` is a shell command, or an `@`-prefixed builtin reference. The RFC's
    `@task.acceptance` is the acceptance builtin; the other builtins follow the
    same convention (`@scope`, `@secrets`, ...). `commands` is the acceptance
    fallback for runs with no task file.

    `state` and `origin` are required on every entry (S-0002/D-19): a boolean
    cannot express shadow or quarantine, and provenance is unrecoverable
    later. `shadow` and `quarantined` gates run and report but never affect
    the exit code (§7.3).

    `axis` is the optional conviction label (S-0034/D-4): what a red result from
    this gate means. An entry without it reads as `functional` once
    `resolved_gates()` fills the default; the declaration itself stays absent
    so the manifest diff shows only labels the operator chose.

    `paths` is what the gate judges (S-0071/D-6) — the globs whose files it
    reads. Measured: `coverage-delta` measures `--cov=src` and spent 27% of its
    twelve hours on attempts whose governed paths were nowhere under `src/`,
    seventeen of which went red over drift the attempt had not caused. An
    entry declaring no paths runs always.

    `sabotage` names this gate's twin — a CASES family in the sabotage suite
    or a repository test path — the evidence that the gate can convict (S-0036/D-3).
    The load refuses a twinless entry once the manifest names a twin for any
    gate; a manifest naming none at all predates the field and is only voiced
    by `twinless_gates()`. The value must be a non-blank reference; resolving
    it against the CASES families and the tree cannot happen here — the
    sabotage suite imports this model, so the cross-check lives in the
    repository test that pins the shipped battery.
    """

    model_config = STRICT
    name: str
    """The entry's name, unique across the manifest — how a gate is reported and
    quarantined."""
    run: str
    """A shell command, or an `@`-prefixed builtin reference such as `@task.acceptance` or
    `@scope`."""
    state: GateState
    """Required on every entry (S-0002/D-19): `shadow` and `quarantined` gates run and
    report but never affect the exit code (§7.3)."""
    origin: str
    """structural | leak/<task> | a citation (S-0059/D-4) — why this gate exists. Required
    on every entry (S-0002/D-19): provenance is unrecoverable later."""
    input: GateInput | None = None
    """Derived for builtins; defaults to worktree for shell gates."""
    timeout: float | None = None
    """Seconds; derived for builtins, 600 for shell gates."""
    axis: GateAxis | None = None
    """The optional conviction label (S-0034/D-4): what a red result from this gate means.
    Derived for unlabeled entries, functional."""
    sabotage: str | None = Field(default=None, min_length=1)
    """The twin's CASES family or test path (S-0036/D-3) — the evidence that this gate can
    convict. Must be a non-blank reference."""
    commands: list[str] = Field(default_factory=list)
    """The acceptance fallback for runs with no task file; only applies to
    @acceptance."""
    paths: list[str] = Field(default_factory=list)
    """What this gate judges (S-0071/D-6). Globs; the gate runs only when the
    attempt's diff touches one, and is reported `skipped` when it does not. An
    entry declaring none runs on every attempt, which is the default a gate
    without a subject wants."""

    # ....................... #

    @field_validator("origin")
    @classmethod
    def _origin_shape(cls, value: str) -> str:
        if value == "structural" or value.startswith("leak/") or is_citation(value):
            return value

        raise ValueError(
            f"origin {value!r} must be 'structural', 'leak/<task>' or a citation such as "
            "'S-0011' or 'S-0054/D-2' (S-0002/D-19, S-0059/D-4)"
        )

    # ....................... #

    @field_validator("sabotage")
    @classmethod
    def _sabotage_shape(cls, value: str | None) -> str | None:
        if value is None:
            return value

        if not value.strip():
            raise ValueError("gate sabotage twin must be a non-blank reference (S-0036/D-3)")

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
    """A gate entry declares no sabotage twin (S-0036/D-3), in a manifest that
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
    # S-0081: runs the changed test functions against the base tree, so it needs
    # the worktree and the base, not the diff alone.
    "red-on-base": "worktree",
    # S-0115/D-2: runs each citing test function against the base tree and the
    # candidate, so it needs the worktree and the base, not the diff alone.
    "evidence": "worktree",
    # S-0107: mutates the changed source and runs the paired tests against a
    # scratch copy of the whole worktree, so it needs the worktree, not the
    # diff alone.
    "mutation": "worktree",
    # S-0116/D-10, D-11: both write the held-out files into the checkout and
    # run them, so each needs the worktree rather than the diff alone.
    "held-out": "worktree",
    "verify-tests": "worktree",
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
    "red-on-base": 300,
    # S-0115/D-2: the same two trees as `red-on-base`, run once per citing
    # function, so its timeout matches that gate's.
    "evidence": 300,
    # S-0107: a baseline run of the paired tests plus the sampled mutants,
    # each capped at twice that baseline — the dearest builtin when it scores.
    "mutation": 900,
    # S-0116/D-10, D-11: one run of the held-out files, and for `verify-tests`
    # a collect over the same set first.
    "held-out": 600,
    "verify-tests": 600,
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

# The launcher a list of test files is appended to (S-0106/D-7): the engine's
# default is pytest over the appended paths, and a repository whose tests are
# `*_test.go` or `*.spec.ts` names its own.
DEFAULT_TEST_RUN = "python3 -m pytest -q -p no:cacheprovider"

# Module -> its tests, as path templates over `{dir}` (the module's directory)
# and `{stem}` (its filename without extension) (S-0106/D-7). The default is the
# Python pairing at any depth under `tests/`: `test_<stem>.py` and every
# `test_<stem>_*.py`.
DEFAULT_TEST_PAIRS = ["tests/**/test_{stem}.py", "tests/**/test_{stem}_*.py"]


# ....................... #


class LayoutConfig(BaseModel):
    """Where the Python project sits (S-0110/D-8, S-0110/D-9). `root` is the
    project's directory relative to the repository root — empty when the
    project is the repository — and `carried` names top-level directories that
    ride with any change, beside `tests` and `.torve`."""

    model_config = STRICT

    root: str = ""
    """The Python project's directory, relative to the repository root; empty
    for a project at the root."""

    carried: list[str] = Field(default_factory=list)
    """Top-level directories that ride with any change (S-0110/D-9), beside
    `tests` and `.torve`; the sizer does not count them as modules."""

    # ....................... #

    @field_validator("root")
    @classmethod
    def _root_shape(cls, value: str) -> str:
        """One spelling per directory: `./app/` reads `app` and `.` reads the
        repository root. A path that leaves the repository is refused."""

        path = PurePosixPath(value)

        if path.is_absolute() or ".." in path.parts:
            raise ValueError(
                f"layout.root {value!r} must be a directory inside the repository, "
                "relative to its root, with no `..` segment"
            )

        return "" if str(path) == "." else str(path)

    # ....................... #

    def source_prefix(self) -> str:
        """The repository-relative prefix of the project's source root
        (S-0110/D-8): `<root>/src/`, or `src/` at the repository root."""

        return f"{self.root}/src/" if self.root else "src/"


# ....................... #


def read_layout(root: Path) -> LayoutConfig:
    """The `layout` block of the repository's manifest, the default when there
    is none (S-0110/D-8). Read without the full `load_manifest`, so a reader
    that wants only the project directory neither voices a twinless manifest's
    warning nor triggers its refusal."""

    path = layout_module.gates_file(root)

    if not path.is_file():
        return LayoutConfig()

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    except (OSError, yaml.YAMLError):
        return LayoutConfig()

    block = raw.get("layout") if isinstance(raw, dict) else None

    try:
        return LayoutConfig.model_validate(block or {})

    except ValueError:
        return LayoutConfig()


# ....................... #


class TestsConfig(BaseModel):
    """Which files count as tests, how a module pairs with them, and how they
    run (S-0106/D-7)."""

    model_config = STRICT

    patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_TEST_PATTERNS))
    """The globs that say which files are tests, for the no-test-tampering gate."""

    run: str = DEFAULT_TEST_RUN
    """The command a list of test files is appended to (S-0106/D-7) — what
    red-on-base runs a qualifying file with. Defaults to pytest over the paths."""

    pair: list[str] = Field(default_factory=lambda: list(DEFAULT_TEST_PAIRS))
    """Templates from a module to its tests over `{dir}` and `{stem}`
    (S-0106/D-7); the default is the Python pairing at any depth under
    `tests/`."""

    # ....................... #

    @classmethod
    def re_rooted(cls, root: str) -> TestsConfig:
        """The default patterns and pairing, re-rooted under a project
        directory (S-0110/D-8): a project under `app/` keeps its tests under
        `app/tests`. A `tests` block a repository declares is never re-rooted
        — only the defaults are."""

        prefix = f"{root.strip('/')}/"

        return cls(
            patterns=[f"{prefix}{pattern}" for pattern in DEFAULT_TEST_PATTERNS],
            pair=[f"{prefix}{pattern}" for pattern in DEFAULT_TEST_PAIRS],
        )

    # ....................... #

    def expansions(self, module: str) -> list[str]:
        """The path patterns this config's `pair` templates make of *module* —
        `{dir}` is its directory, `{stem}` its filename without extension."""

        directory, _, filename = str(module).rpartition("/")
        stem = filename.rsplit(".", 1)[0]
        patterns: list[str] = []

        for template in self.pair:
            # A module at the repository root has no directory, so a template's
            # leading `{dir}/` would leave a spurious `/`.
            if not directory:
                template = template.replace("{dir}/", "")

            patterns.append(template.format(dir=directory, stem=stem))

        return patterns

    # ....................... #

    def source_suffixes(self) -> set[str]:
        """The suffixes a module must carry for this config's `pair` templates
        to apply (S-0106/D-7): the suffix each template gives the test file
        after `{stem}`. The default's templates say `.py`, so `gates.json` does
        not pair as though it were `gates.py`; a Go template says `.go`, so
        only a `.go` module brings its `_test.go`."""

        suffixes: set[str] = set()

        for template in self.pair:
            _, marker, tail = template.partition("{stem}")

            if marker and (suffix := Path(tail).suffix):
                suffixes.add(suffix)

        return suffixes

    # ....................... #

    def paired(self, module: str, names: Iterable[str]) -> list[str]:
        """The existing paths among *names* that pair with *module* under this
        config's `pair` templates, sorted (S-0106/D-7). A module whose suffix
        the templates do not name pairs with nothing, so a data file under
        `tests/` is never a module's test."""

        suffixes = self.source_suffixes()

        if suffixes and Path(module).suffix not in suffixes:
            return []

        return sorted({str(name) for name in names if matches(str(name), self.expansions(module))})


# ....................... #


class SecretsConfig(BaseModel):
    """`allow_patterns` are regexes that suppress a match on the line they
    match. Reviewed configuration, not a bypass: the manifest arrives in a
    pull request, and S-0002/D-8 stays intact because no signature at run time can
    widen it."""

    model_config = STRICT

    allow_patterns: list[str] = Field(default_factory=list)
    """Regexes that suppress a secrets match on the line they match."""


# ....................... #


class ManifestScope(Scope):
    """The manifest's scope (S-0104/D-5): the task scope's `allow`, `deny` and
    `contingent`, plus `derived` — the files that ride with a source, in both
    directions. A lock file rides with its manifest, a golden with its
    producer, a generated schema with its model; a derived path is allowed
    wherever its source is, and its source wherever a derived path is. The
    default is the lock-file table the scope gate used to hard-code
    (`DEFAULT_DERIVED`)."""

    model_config = STRICT

    derived: dict[str, list[str]] = Field(
        default_factory=lambda: {
            manifest: list(locks) for manifest, locks in DEFAULT_DERIVED.items()
        }
    )
    """Source path -> the files that ride with it; applied both directions."""


# ....................... #


class Manifest(BaseModel):
    model_config = STRICT
    schema_version: Annotated[int, SchemaVersion(SCHEMA_VERSION)] = SCHEMA_VERSION
    """The engine's shape version this manifest is read under."""
    scope: ManifestScope = Field(default_factory=ManifestScope)
    """The repository-wide scope the scope gate judges a diff against when the run carries
    no task file of its own, with the `derived` map the gate reads."""
    layout: LayoutConfig = Field(default_factory=LayoutConfig)
    """Where the Python project sits (S-0110/D-8, S-0110/D-9): the directory the source
    root and the `tests` defaults are read under, and the directories that ride with any
    change."""
    tests: TestsConfig = Field(default_factory=TestsConfig)
    """Which files count as tests, for the no-test-tampering gate."""
    secrets: SecretsConfig = Field(default_factory=SecretsConfig)
    """The reviewed allowances the secrets gate honours."""

    quarantine: list[str] = Field(default_factory=list)
    """Acceptance commands that flake; their failures are recorded but stop blocking until
    fixed (S-0002/three-outcomes-gates-need-beyond-pass-and-fail). Maintained by humans
    from the flake counters in telemetry until a store exists (S-0003)."""
    telemetry: str = ".torve/telemetry.jsonl"
    """The path, relative to the repository root, the gate run appends its telemetry
    records to."""
    gates: list[Gate] = Field(default_factory=list)
    """The battery itself: every gate entry, run in the order the resolver puts them
    in."""

    # ....................... #

    @model_validator(mode="before")
    @classmethod
    def _re_root_tests(cls, data: object) -> object:
        """The `tests` defaults read under `layout.root` (S-0110/D-8): a project
        under `app/` has its tests under `app/tests` when the repository
        declares no `tests` block of its own. A declared block is left exactly
        as written."""

        if not isinstance(data, dict) or "tests" in data:
            return data

        block = data.get("layout")
        root = (
            str(block.get("root") or "").strip("/")
            if isinstance(block, dict)
            else (block.root if isinstance(block, LayoutConfig) else "")
        )

        if not root:
            return data

        return {**data, "tests": TestsConfig.re_rooted(root).model_dump()}

    # ....................... #

    def twinless_gates(self) -> list[str]:
        """Names of entries declaring no sabotage twin (S-0036/D-3)."""

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


# The manifests whose twinless entries this process has already voiced. One
# command loads the manifest from three or four places, and the warning is a
# fact about the file, not about the loader that read it — so it is said
# once per file per process, not once per read.
_voiced: set[Path] = set()


def load_manifest(path: Path) -> Manifest:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    if not isinstance(raw, dict):
        raise ValueError(f"{path}: manifest must be a mapping")

    try:
        manifest = Manifest.model_validate(raw)
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from None

    manifest.resolved_gates()  # surface builtin/name errors at load time

    # S-0036/D-3, refusal stage: a manifest that names the twin for any gate has
    # adopted the field, and every remaining twinless entry is a load error —
    # a gate that cannot prove it convicts cannot be declared. A manifest
    # naming no twins at all predates the field and is voiced, not refused:
    # the shipped scenario data builds one twinless manifest per case, and
    # the refusal must not reach through the self-hosting boundary into it.
    # The strings carry no decision coordinate — they surface on whoever's
    # terminal is running the command.
    twinless = manifest.twinless_gates()

    if twinless and path not in _voiced:
        _voiced.add(path)
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
