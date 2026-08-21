"""`secrets` — the one failure class a follow-up commit cannot repair
(RFC 0002 §6a). Blocking, and exempt from bypass (D-2.8): the runner refuses
to apply a Torve-Bypass trailer to this gate.

High-confidence patterns only: a noisy secret scanner gets muted, and a muted
gate is worse than none. Known false positives are suppressed through the
manifest's reviewed `secrets.allow_patterns`, which is configuration in a pull
request, not a run-time bypass.
"""

from __future__ import annotations

import re

from torve.context import GateContext
from torve.gates.base import BuiltinOutcome
from torve.models import Gate

# ----------------------- #

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY(?: BLOCK)?-----")),
    ("aws access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("github fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("openai key", re.compile(r"\bsk-[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20,}\b")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("stripe live key", re.compile(r"\b[sr]k_live_[A-Za-z0-9]{20,}\b")),
]

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
MAX_UNTRACKED_BYTES = 1_000_000


def _added_lines(patch: str) -> list[tuple[str, int, str]]:
    """(path, new-file line number, line text) for every added line."""
    added: list[tuple[str, int, str]] = []
    path = ""
    line_no = 0
    for raw in patch.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            path = "" if target == "/dev/null" else target.removeprefix("b/")
            continue
        match = HUNK.match(raw)
        if match:
            line_no = int(match.group(1))
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            added.append((path, line_no, raw[1:]))
            line_no += 1
        elif not raw.startswith("-") and not raw.startswith("\\"):
            line_no += 1
    return added


def _untracked_lines(ctx: GateContext) -> list[tuple[str, int, str]]:
    lines: list[tuple[str, int, str]] = []
    for rel in ctx.untracked:
        target = ctx.root / rel
        try:
            if target.stat().st_size > MAX_UNTRACKED_BYTES:
                continue
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # unreadable or binary; nothing line-scannable
        lines += [(rel, i, line) for i, line in enumerate(text.splitlines(), start=1)]
    return lines


def check_secrets(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    allow = [re.compile(p) for p in ctx.manifest.secrets.allow_patterns]
    hits: list[str] = []
    for path, line_no, text in _added_lines(ctx.patch) + _untracked_lines(ctx):
        for label, pattern in PATTERNS:
            if not pattern.search(text):
                continue
            if any(a.search(text) for a in allow):
                continue
            hits.append(f"{path}:{line_no}: {label}")

    if not hits:
        return BuiltinOutcome("pass", "no secret patterns in added lines")
    return BuiltinOutcome("fail", "\n".join(sorted(set(hits))))
