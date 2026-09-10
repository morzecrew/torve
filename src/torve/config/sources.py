"""Reading the filed sources (S-0060/D-1): one file per source under
`.torve/sources/<kind>/<slug>.yaml`, whose identifier is its own path.

The corpus's loader is `config/spec.py` and this is its much smaller
sibling: a source has no rows, no phases and no amendments, so there is
nothing to fold and nothing to number. What it does have is the same two
rules every minted YAML file here follows — the first line names the schema
(S-0057/D-4), and what the file says about itself must agree with where it
sits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from torve.config import layout
from torve.config.spec import SCHEMA_HEADER, SpecError, schemas_dir
from torve.domain.source import FILED_KINDS, SLUG, SOURCES_DIR, Source

# ----------------------- #

SOURCE_SCHEMA = "sources"


def sources_dir(root: Path) -> Path:
    """Where the filed sources live."""

    return root / layout.TORVE_DIR / SOURCES_DIR


def source_file(root: Path, identifier: str) -> Path:
    """The file one filed identifier names, whether or not it exists."""

    kind, _, slug = identifier.partition("/")

    return sources_dir(root) / kind / f"{slug}.yaml"


def source_files(root: Path) -> list[Path]:
    """Every `.yaml` under the sources directory, in path order — including
    the ones in a directory no kind names, so the check can convict them."""

    where = sources_dir(root)

    return sorted(p for p in where.glob("*/*.yaml") if p.is_file()) if where.is_dir() else []


def identifier_of(root: Path, path: Path) -> str:
    """The identifier a file's own path claims."""

    return f"{path.parent.name}/{path.stem}"


# ....................... #


def schema_text() -> str:
    """The filed source's JSON Schema, as `torve init` writes it."""

    return json.dumps(Source.model_json_schema(), indent=2, sort_keys=True) + "\n"


def schema_header() -> str:
    """The first line of a source file: its schema, two directories up."""

    return f"{SCHEMA_HEADER}../../schemas/{SOURCE_SCHEMA}.json"


def schema_file(root: Path) -> Path:
    return schemas_dir(root / layout.SPECS_DIR) / f"{SOURCE_SCHEMA}.json"


# ....................... #


def load_source(root: Path, path: Path) -> Source:
    """One source file as its model, with the problems named the way the
    corpus names them: the file, then what is wrong with it."""

    where = path.relative_to(root).as_posix()
    problems: list[str] = []

    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SpecError([f"{where}: not YAML — {str(exc).splitlines()[0]}"]) from None

    if not isinstance(raw, dict):
        raise SpecError([f"{where}: not a mapping"])

    record = cast("dict[str, Any]", raw)

    try:
        source = Source.model_validate(record)
    except ValidationError as exc:
        for one in exc.errors():
            field = ".".join(str(part) for part in one["loc"]) or "(document)"
            problems.append(f"{where}: {field} — {one['msg']}")

        raise SpecError(problems) from None

    claimed = identifier_of(root, path)

    if source.id != claimed:
        problems.append(
            f"{where}: says `id: {source.id}` and sits at {claimed} — a source is "
            "identified by where it is (S-0060/D-1)"
        )

    if source.kind != path.parent.name:
        problems.append(
            f"{where}: says `kind: {source.kind}` under {path.parent.name}/ — the "
            "directory is the kind"
        )

    if problems:
        raise SpecError(problems)

    return source


def load_sources(root: Path) -> dict[str, Source]:
    """Every filed source by identifier. Every file's problems are collected
    before anything is raised, so one broken file does not hide the rest."""

    problems: list[str] = []
    found: dict[str, Source] = {}

    for path in source_files(root):
        try:
            source = load_source(root, path)
        except SpecError as exc:
            problems.extend(exc.problems)
            continue

        if source.id in found:
            problems.append(f"{path.relative_to(root).as_posix()}: {source.id} is claimed twice")
            continue

        found[source.id] = source

    if problems:
        raise SpecError(problems)

    return found


# ....................... #


def check_sources(root: Path) -> tuple[list[str], list[str]]:
    """(problems, warnings) over the sources directory: what `load_sources`
    refuses, plus the shape rules a load cannot see — a directory no kind
    names, a slug the identifier grammar refuses, and a file that does not
    open with its schema line."""

    problems: list[str] = []
    warnings: list[str] = []
    where = sources_dir(root)

    if not where.is_dir():
        return problems, warnings

    for entry in sorted(where.iterdir()):
        if entry.is_dir() and entry.name not in FILED_KINDS:
            problems.append(
                f"{entry.relative_to(root).as_posix()}: no source kind is named "
                f"{entry.name!r} — a document is a source without being filed as one "
                f"({', '.join(FILED_KINDS)})"
            )

    for path in source_files(root):
        name = path.relative_to(root).as_posix()

        if not SLUG.match(path.stem):
            problems.append(f"{name}: {path.stem!r} is not a slug — lowercase, digits, . and -")

        if not path.read_text(encoding="utf-8").startswith(schema_header()):
            warnings.append(f"{name}: does not open with its schema line — `torve init` adds it")

    try:
        load_sources(root)
    except SpecError as exc:
        problems.extend(exc.problems)

    return problems, warnings


# ....................... #


def cited_sources(root: Path) -> dict[str, str]:
    """Task id to the source its contract names, for every local contract
    that names one (S-0060/D-3) — what `source list --check` resolves."""

    found: dict[str, str] = {}
    tasks = root / layout.TORVE_DIR / "tasks"

    if not tasks.is_dir():
        return found

    for path in sorted(tasks.glob("T-*/contract.yaml")):
        try:
            raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue

        if not isinstance(raw, dict):
            continue

        record = cast("dict[str, Any]", raw)
        named = str(record.get("source") or "")

        if named:
            found[str(record.get("id", path.parent.name))] = named

    return found
