"""One JSONL record per run (RFC 0002 §8). Three fields must be right from the
first line because none can be reconstructed later: `schema_version`,
`config_hash`, and decisions denormalised into the record, not referenced.

`config_hash` lives here rather than beside the manifest model: digesting the
regime needs the forze pin from `application.migrate`, and `config` may not
import `application` (RFC 0015 §2.1).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import torve
from torve.domain.task import SCHEMA_VERSION
from torve.gates.context import GateContext
from torve.gates.runner import RunReport

# ----------------------- #


def config_hash(manifest_path: Path, root: Path) -> str:
    """Digest of the regime a run belongs to (RFC 0002 §8, D-9.8): gates.yaml,
    the agent-skills lockfile, the Torve package version (its gates and
    shipped skills change behavior — A-3), and the pinned forze version (a
    substrate upgrade is a regime change, and possibly a migration — A-6).
    The tier mapping joins in RFC 0004.
    """
    from torve.application.migrate import forze_pin

    parts: dict[str, str] = {
        "gates.yaml": manifest_path.read_text(encoding="utf-8"),
        "torve": torve.__version__,
        # The substrate pin, not the installed version: the pin names the
        # schema regime the migrations were written against (rfcs/0012-migrations.md §6);
        # torve doctor is what compares it to the installed version.
        "forze": forze_pin(),
    }
    lock = root / "skills-lock.json"
    if lock.is_file():
        parts["skills-lock.json"] = lock.read_text(encoding="utf-8")
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()[:12]


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
