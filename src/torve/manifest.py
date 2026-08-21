"""gates.yaml — the reviewed gate manifest, one per consuming repository
(D-2.5), plus the config hash that stamps every telemetry record (RFC 0002 §7).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from torve.models import SCHEMA_VERSION, Gate, Scope

# Builtins: expected input and a cost proxy used for cheapest-first ordering.
# Diff-, task- and log-input builtins are near-free; only acceptance shells out.
BUILTIN_INPUTS: dict[str, str] = {
    "scope": "diff",
    "secrets": "diff",
    "no-test-tampering": "diff",
    "decisions-reported": "log",
    "self-audit": "log",
    "acceptance": "worktree",
}
BUILTIN_TIMEOUTS: dict[str, float] = {
    "scope": 30,
    "secrets": 30,
    "no-test-tampering": 30,
    "decisions-reported": 30,
    "self-audit": 30,
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


class TestsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_TEST_PATTERNS))


class SecretsConfig(BaseModel):
    """`allow_patterns` are regexes that suppress a match on the line they
    match. Reviewed configuration, not a bypass: the manifest arrives in a
    pull request, and D-2.8 stays intact because no signature at run time can
    widen it."""

    model_config = ConfigDict(extra="forbid")

    allow_patterns: list[str] = Field(default_factory=list)


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

    def resolved_gates(self) -> list[Gate]:
        """Gates with input and timeout filled in from builtin defaults."""
        resolved = []
        for gate in self.gates:
            builtin = gate.builtin
            if builtin is not None and builtin not in BUILTIN_INPUTS:
                raise ValueError(f"gate {gate.name!r}: unknown builtin {gate.run!r}")
            update: dict[str, object] = {}
            if gate.input is None:
                update["input"] = BUILTIN_INPUTS.get(builtin or "", "worktree")
            if gate.timeout is None:
                update["timeout"] = BUILTIN_TIMEOUTS.get(builtin or "", SHELL_GATE_TIMEOUT)
            resolved.append(gate.model_copy(update=update) if update else gate)
        names = [g.name for g in resolved]
        if len(names) != len(set(names)):
            raise ValueError("gate names must be unique")
        return resolved


def load_manifest(path: Path) -> Manifest:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: manifest must be a mapping")
    manifest = Manifest.model_validate(raw)
    manifest.resolved_gates()  # surface builtin/name errors at load time
    return manifest


def config_hash(manifest_path: Path, root: Path) -> str:
    """Digest of the regime a run belongs to (RFC 0002 §7, D-9.8): gates.yaml,
    the agent-skills lockfile, the Torve package version (its gates and
    shipped skills change behavior — A-3), and the pinned forze version (a
    substrate upgrade is a regime change, and possibly a migration — A-6).
    The tier mapping joins in RFC 0004.
    """
    import importlib.metadata

    import torve

    parts: dict[str, str] = {
        "gates.yaml": manifest_path.read_text(encoding="utf-8"),
        "torve": torve.__version__,
        "forze": importlib.metadata.version("forze"),
    }
    lock = root / "skills-lock.json"
    if lock.is_file():
        parts["skills-lock.json"] = lock.read_text(encoding="utf-8")
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()[:12]
