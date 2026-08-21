"""One JSONL record per run (RFC 0002 §8). Three fields must be right from the
first line because none can be reconstructed later: `schema_version`,
`config_hash`, and decisions denormalised into the record, not referenced.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import torve
from torve.context import GateContext
from torve.models import SCHEMA_VERSION
from torve.runner import RunReport


def build_record(ctx: GateContext, report: RunReport, config_hash: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config_hash": config_hash,
        "torve_version": torve.__version__,  # toolchain, recorded beside the regime hash
        "base": ctx.base,
        "merge_base": ctx.merge_base,
        "head": ctx.head_sha,
        "task_id": ctx.task.id if ctx.task else None,
        "decisions": [d.model_dump() for d in ctx.task.decisions] if ctx.task else [],
        "results": [r.model_dump() for r in report.results],
        "exit_code": report.exit_code,
        "bypass_count_by_gate": report.bypass_count_by_gate,
        "flaky_count_by_command": report.flaky_count_by_command,
    }


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
