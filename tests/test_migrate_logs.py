"""The single-use A-1 converter: markdown logs in, YAML logs out, originals
deleted — and the output passes the YAML-only gate's parser."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from torve.gates.decisions_reported import parse_log

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "migrate_logs.py"

MD_LOG = """# T-0142 · Session storage

Executed RFC 0014 against branch `feat/session-store`.

**Drift count: 0.**

```divergence
decision: D-3
grade: LOCKED
kind: contradicted
at: 2026-08-20T11:04:12Z
attempt: 2
claim: sessions cannot live in Redis
evidence: infra/compose.yaml:1-40 — no redis service defined
action: halted
```

Halted here; the alternative is not mine to make.

**Drift count: 1.** D-7 turned out to be drift.

```bypass
gate: scope
reason: allow list predates the task
author: A Human <human@example.invalid>
commit: abc123
at: 2026-08-20T12:00:00Z
```
"""


def load_converter():
    spec = importlib.util.spec_from_file_location("migrate_logs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_conversion_shape():
    document = load_converter().convert(MD_LOG, "T-0142")
    assert document["task"] == "T-0142"
    assert document["drift_count"] == 1  # the LAST declared count wins
    assert len(document["entries"]) == 1
    entry = document["entries"][0]
    assert entry["decision"] == "D-3"
    assert entry["attempt"] == 2  # int, not string
    assert document["bypasses"][0]["gate"] == "scope"
    assert "Halted here" in document["notes"]
    assert "Drift count" not in document["notes"]  # counts moved, not duplicated


def test_migrated_torve_logs_parse_under_the_gate():
    for log in sorted((Path(__file__).resolve().parent.parent / "logs").glob("*.yaml")):
        document, error = parse_log(log.read_text(encoding="utf-8"))
        assert error is None, f"{log.name}: {error}"
        assert isinstance(document.get("drift_count"), int), f"{log.name}: no drift_count"
        assert document["entries"], f"{log.name}: no entries survived migration"
