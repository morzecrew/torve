"""`torve init` — what the code derives, written into `.torve/` (RFC 0057
D-57.5): the JSON Schema of every file torve reads from YAML, named by
the first line of each such file so an editor validates it as it is
typed, and the ignore file for what torve alone writes. Idempotent: a
schema is rewritten when it lags, a pattern the ignore file lacks is
appended below the operator's own lines, and the configuration and the
manifest get their schema line once. Never a configuration or a
manifest — those are authored.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import typer

from torve.cli.console import STYLE_DIM, STYLE_PASS, closing, out
from torve.cli.options import ConfigOption, RootOption, load_config
from torve.config import layout
from torve.config.spec import SCHEMA_HEADER, schema_file, schema_text, schemas_dir
from torve.domain.spec import FILES
from torve.domain.states import EXIT_OK

# ----------------------- #

# What torve alone writes under `.torve/` (D-57.5): the task directory
# and the pack are projections (RFC 0056), the streams are append targets,
# the rest is run state. The manifest, the configuration, the standing
# contracts, the specifications and the schemas are reviewed artefacts and
# stay tracked.
MINTED_PATTERNS = (
    "tasks/",
    "context/",
    "telemetry.jsonl",
    "feedback.jsonl",
    "regimes/",
    "traces/",
    "skills/",
    "tmp/",
)

# The task log's shape as `divergence` writes it and the gate reads it:
# the pin, the drift count and the entries as `payload_of` shapes them.
# Phase 3 of RFC 0057 types the entry as a model; until then the entry's
# keys are named here and nothing else is refused.
LOG_SCHEMA: dict[str, Any] = {
    "title": "log",
    "description": "A task's divergence log, .torve/tasks/T-NNNN/log.yaml.",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "task", "entries"],
    "properties": {
        "schema_version": {"type": "integer"},
        "task": {"type": "string"},
        "repo": {"type": "string"},
        "base_sha": {"type": "string"},
        "drift_count": {"type": "integer"},
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["decision", "grade", "claim", "evidence", "action"],
                "properties": {
                    "decision": {"type": "string"},
                    "grade": {"enum": ["LOCKED", "ASSUMED", "OPEN", "UNLISTED"]},
                    "kind": {"enum": ["contradicted", "departed", "resolved", "blocked"]},
                    "class": {"enum": ["discovery", "spec-gap", "drift", "irreducible"]},
                    "at": {"type": "string"},
                    "attempt": {"type": "integer", "minimum": 1},
                    "claim": {"type": "string"},
                    "evidence": {"type": "string"},
                    "action": {"enum": ["halted", "departed", "decided"]},
                    "proposal": {"type": "string"},
                    "notes": {"type": "string"},
                },
            },
        },
    },
}


def _json(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def expected_schemas(corpus: Path) -> dict[Path, str]:
    """Every schema `init` writes, by path: the four files of a document,
    the contract, the log, the run configuration and the gate manifest."""

    from torve.config.manifest import Manifest
    from torve.config.runconfig import RunnerConfig
    from torve.domain.task import Task

    texts = {schema_file(corpus, file_name): schema_text(file_name) for file_name in FILES}
    where = schemas_dir(corpus)
    texts[where / "contract.json"] = _json(Task.model_json_schema())
    texts[where / "log.json"] = _json(LOG_SCHEMA)
    texts[where / "config.json"] = _json(RunnerConfig.model_json_schema())
    texts[where / "gates.json"] = _json(Manifest.model_json_schema())

    return texts


def ignore_file(root: Path) -> Path:
    return root / layout.TORVE_DIR / ".gitignore"


def missing_patterns(path: Path) -> list[str]:
    """The minted patterns the ignore file does not carry as a line of its
    own; every line when there is no file."""

    present = (
        {line.strip() for line in path.read_text(encoding="utf-8").splitlines()}
        if path.is_file()
        else set()
    )

    return [pattern for pattern in MINTED_PATTERNS if pattern not in present]


def schema_line(target: Path, schema: Path) -> str:
    """The first line of a file that names its schema, relative to the
    file's own directory."""

    return f"{SCHEMA_HEADER}{os.path.relpath(schema, target.parent)}"


def _add_header(target: Path, schema: Path) -> bool:
    """The schema line prepended once; other comments stay legal there."""

    if not target.is_file():
        return False

    text = target.read_text(encoding="utf-8")

    if any(line.startswith(SCHEMA_HEADER) for line in text.splitlines()):
        return False

    target.write_text(f"{schema_line(target, schema)}\n{text}", encoding="utf-8")

    return True


# ....................... #


def init_cmd(
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Write what the code derives under .torve/: one JSON Schema per file
    torve reads from YAML (the four files of a document, the contract, the
    log, the configuration, the manifest) into the schemas directory beside
    the corpus, the ignore file for what torve alone writes, and the schema
    line at the top of the configuration and the manifest. Runs again
    without a diff; `torve doctor` reddens when any of it lags."""

    corpus = root / load_config(root, config).specs.path
    corpus.mkdir(parents=True, exist_ok=True)
    console = out()
    written: list[str] = []

    for path, text in expected_schemas(corpus).items():
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            console.print(f"  {path.name}", style=STYLE_DIM)
            continue

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        console.print(f"  {path.name}  written", style=STYLE_PASS)
        written.append(path.name)

    ignore = ignore_file(root)
    lacking = missing_patterns(ignore)

    if lacking:
        ignore.parent.mkdir(parents=True, exist_ok=True)
        current = ignore.read_text(encoding="utf-8") if ignore.is_file() else ""
        joiner = "" if not current or current.endswith("\n") else "\n"
        ignore.write_text(current + joiner + "\n".join(lacking) + "\n", encoding="utf-8")
        console.print(f"  {ignore.name}  {len(lacking)} pattern(s) added", style=STYLE_PASS)
        written.append(ignore.name)
    else:
        console.print(f"  {ignore.name}", style=STYLE_DIM)

    where = schemas_dir(corpus)

    for target, schema in (
        (layout.config_file(root), where / "config.json"),
        (layout.gates_file(root), where / "gates.json"),
    ):
        if _add_header(target, schema):
            console.print(f"  {target.name}  schema line added", style=STYLE_PASS)
            written.append(target.name)

    closing(console, f"{len(written)} file(s) written under {root / layout.TORVE_DIR}", STYLE_PASS)
    raise typer.Exit(EXIT_OK)
