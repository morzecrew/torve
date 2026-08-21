#!/usr/bin/env python3
"""Single-use converter for amendment A-1: `logs/<task-id>.md` (fenced
divergence blocks) -> `logs/<task-id>.yaml` (one `entries:` list).

The gate accepts only YAML; compatibility lives here and dies with this
script. Reads every `logs/*.md` under --root, writes the `.yaml` beside it,
deletes the original. Prose outside the fenced blocks becomes the top-level
`notes:`; the last declared `**Drift count: N**` becomes `drift_count`;
` ```bypass ` blocks become the `bypasses:` list.

    python3 scripts/migrate_logs.py --root . [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

BLOCK = re.compile(r"^```(?P<tag>divergence|bypass)[ \t]*$\n(?P<body>.*?)^```[ \t]*$",
                   re.M | re.S)
FIELD = re.compile(r"^([a-z_]+):[ \t]*(.*)$")
DRIFT_COUNT = re.compile(r"^\*\*Drift count:\s*(\d+)", re.M)
INT_FIELDS = {"attempt"}


def parse_fields(body: str) -> dict:
    fields: dict = {}
    for raw in body.splitlines():
        if not raw.strip():
            continue
        found = FIELD.match(raw)
        if not found:
            continue
        key, value = found.group(1), found.group(2).strip()
        if key in INT_FIELDS and value.isdigit():
            fields[key] = int(value)
        else:
            fields[key] = value
    return fields


def convert(text: str, task_id: str) -> dict:
    entries: list[dict] = []
    bypasses: list[dict] = []
    for match in BLOCK.finditer(text):
        fields = parse_fields(match.group("body"))
        (bypasses if match.group("tag") == "bypass" else entries).append(fields)

    counts = DRIFT_COUNT.findall(text)
    prose = BLOCK.sub("", text)
    prose = DRIFT_COUNT.sub("", prose)
    prose = re.sub(r"\n{3,}", "\n\n", prose).strip()

    document: dict = {"schema_version": 1, "task": task_id}
    document["drift_count"] = int(counts[-1]) if counts else 0
    if prose:
        document["notes"] = prose + "\n"
    if bypasses:
        document["bypasses"] = bypasses
    document["entries"] = entries
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logs = sorted((args.root / "logs").glob("*.md"))
    if not logs:
        print("nothing to migrate")
        return 0
    for source in logs:
        document = convert(source.read_text(encoding="utf-8"), source.stem)
        target = source.with_suffix(".yaml")
        print(f"{source} -> {target}  ({len(document['entries'])} entries, "
              f"drift_count={document['drift_count']})")
        if not args.dry_run:
            target.write_text(
                yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100),
                encoding="utf-8",
            )
            source.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
