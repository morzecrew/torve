"""The transactional outbox (RFC 0003 §5, RFC 0008 §2, D-8.2): effects are
staged durably, relayed at-least-once, and every effect carries an
idempotency key so a replay is a no-op rather than a duplicate.

The local regime's transaction: the run state file — written atomically with
every transition, history and all — IS the record effects derive from, so
"escalated but nobody was told" is unreachable: the projection re-derives
effects from state and re-stages them idempotently, and a lost outbox file
is rebuilt from the states, never invented. Staging dedupes by key; the
relay delivers a row and only then marks its key in the ledger, so a crash
between the two redelivers — at-least-once, with the destination's own
key-based dedupe as the second half of exactly-once-in-effect.

A failed delivery leaves the row pending and the relay moves on: one broken
destination must not dam the queue behind it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from torve.config import layout

# ----------------------- #

OUTBOX = "outbox.jsonl"
LEDGER = "outbox-ledger.jsonl"


# ....................... #


@dataclass
class Effect:
    key: str  # (task_id, state, attempt) by D-8.2 for tracker effects
    kind: str
    payload: dict[str, Any]
    at: str = ""


# ....................... #


@dataclass
class RelayReport:
    delivered: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # already in the ledger
    failed: dict[str, str] = field(default_factory=dict)  # key -> reason


# ....................... #


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []

    rows: list[dict[str, Any]] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(cast("dict[str, Any]", json.loads(line)))

    return rows


# ....................... #


def _append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# ....................... #


def staged_keys(root: Path) -> set[str]:
    return {str(row["key"]) for row in _rows(root / layout.TORVE_DIR / OUTBOX)}


# ....................... #


def delivered_keys(root: Path) -> set[str]:
    return {str(row["key"]) for row in _rows(root / layout.TORVE_DIR / LEDGER)}


# ....................... #


def stage(root: Path, effect: Effect) -> bool:
    """Stage one effect; a key already staged is a no-op (False)."""

    if effect.key in staged_keys(root):
        return False

    _append(
        root / layout.TORVE_DIR / OUTBOX,
        {
            "key": effect.key,
            "kind": effect.kind,
            "payload": effect.payload,
            "at": effect.at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    return True


# ....................... #


def pending(root: Path) -> list[Effect]:
    done = delivered_keys(root)
    return [
        Effect(
            key=str(row["key"]),
            kind=str(row["kind"]),
            payload=cast("dict[str, Any]", row.get("payload", {})),
            at=str(row.get("at", "")),
        )
        for row in _rows(root / layout.TORVE_DIR / OUTBOX)
        if str(row["key"]) not in done
    ]


# ....................... #


def relay(root: Path, deliver: Callable[[Effect], None]) -> RelayReport:
    """Deliver every pending effect, marking the ledger only after the
    delivery returned — the crash window between the two is what makes this
    at-least-once instead of at-most-once."""

    report = RelayReport()
    done = delivered_keys(root)

    for row in _rows(root / layout.TORVE_DIR / OUTBOX):
        key = str(row["key"])

        if key in done:
            report.skipped.append(key)
            continue

        effect = Effect(
            key=key,
            kind=str(row["kind"]),
            payload=cast("dict[str, Any]", row.get("payload", {})),
            at=str(row.get("at", "")),
        )

        try:
            deliver(effect)
        except Exception as exc:  # one destination must not dam the queue
            report.failed[key] = str(exc)
            continue

        _append(
            root / layout.TORVE_DIR / LEDGER,
            {
                "key": key,
                "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
        done.add(key)
        report.delivered.append(key)

    return report
