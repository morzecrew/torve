"""The sabotage suite (RFC 0002 §5, D-2.2): one deliberately bad change per
gate, applied to a scratch repository, asserting the gate goes red — plus a
clean twin per gate asserting green, because a gate that cannot pass is as
broken as one that cannot fail.

Without this, a gate that silently stops working looks identical to a gate
that never fires because the code is clean.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from torve.config import layout
from torve.config.manifest import load_manifest
from torve.gates.context import build_context
from torve.gates.runner import run_gates

# ----------------------- #

TASK_ID = "T-9001"
AT = "2026-08-21T00:00:00Z"

BASE_MANIFEST: dict[str, Any] = {
    "schema_version": 1,
    "scope": {"allow": [], "deny": []},
    "gates": [
        {"name": "scope", "run": "@scope", "state": "blocking", "origin": "structural"},
        {"name": "secrets", "run": "@secrets", "state": "blocking", "origin": "structural"},
        {
            "name": "no-test-tampering",
            "run": "@no-test-tampering",
            "state": "blocking",
            "origin": "structural",
        },
        {
            "name": "decisions-reported",
            "run": "@decisions-reported",
            "state": "blocking",
            "origin": "structural",
        },
        {"name": "self-audit", "run": "@self-audit", "state": "shadow", "origin": "structural"},
        {"name": "source-layout", "run": "@source-layout", "state": "shadow", "origin": "rfc/0014"},
        {
            "name": "user-facing-text",
            "run": "@user-facing-text",
            "state": "shadow",
            "origin": "rfc/0011",
        },
        {
            "name": "acceptance",
            "run": "@task.acceptance",
            "state": "blocking",
            "origin": "structural",
            "commands": ["true"],
        },
    ],
}


# ....................... #


def base_task(allow: list[str], decisions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": TASK_ID,
        "role": "implement",
        "scope": {"allow": allow, "deny": []},
        "acceptance": [],
        "decisions": decisions or [],
    }


# ....................... #

LOCKED_D1 = [
    {"id": "D-1", "grade": "LOCKED", "text": "app module layout is settled", "paths": ["src/**"]}
]


# ....................... #


def entry(**overrides: Any) -> dict[str, Any]:
    """One A-1 YAML log entry; pass field overrides, or None to drop a field."""
    fields: dict[str, Any] = {
        "decision": "D-1",
        "grade": "LOCKED",
        "kind": "resolved",
        "at": AT,
        "attempt": 1,
        "claim": "touched the governed area; the decision was honored",
        "evidence": "src/app.py:1",
        "action": "decided",
    }
    fields.update(overrides)
    return {key: value for key, value in fields.items() if value is not None}


# ....................... #


def log_document(*entries: dict[str, Any], drift_count: int | None = 0) -> str:
    document: dict[str, Any] = {"schema_version": 1, "task": TASK_ID}
    if drift_count is not None:
        document["drift_count"] = drift_count
    document["entries"] = list(entries)
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


# ....................... #


class Repo:
    def __init__(self, root: Path) -> None:
        self.root = root

    # ....................... #

    def git(self, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True
        )

    # ....................... #

    def write(self, rel: str, content: str) -> None:
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    # ....................... #

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "--no-gpg-sign", "-m", message)

    # ....................... #

    def seed(self, manifest: dict[str, Any] | None = None) -> None:
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Sabotage Human")
        self.git("config", "user.email", "human@example.invalid")
        self.write(".torve/gates.yaml", yaml.safe_dump(manifest or BASE_MANIFEST, sort_keys=False))
        # Only what the engine generates; the reviewed .torve/ artefacts stay
        # tracked (RFC 0013 §5).
        self.write(".gitignore", ".torve/telemetry.jsonl\n.torve/skills/\n.torve/tmp/\n")
        self.write("src/app.py", "print('hello')\n")
        self.write("tests/test_app.py", "def test_app():\n    assert True\n")
        self.commit("init")
        self.git("checkout", "-q", "-b", f"torve/{TASK_ID}")

    # ....................... #

    def task(self, task: dict[str, Any], log: str | None) -> None:
        # One directory per task (A-12); the log only exists if written (A-13).
        self.write(f".torve/tasks/{TASK_ID}/contract.yaml", yaml.safe_dump(task, sort_keys=False))
        if log is not None:
            self.write(f".torve/tasks/{TASK_ID}/log.yaml", log)


# ....................... #


@dataclass
class CaseOutcome:
    name: str
    gate: str
    expected: str
    got: str
    ok: bool
    detail: str = ""


# ....................... #


@dataclass
class Case:
    name: str
    gate: str
    expected: str
    build: Callable[[Repo], None]


# ----------------------- #
# Scenario builders — each seeds a repository the gate under test must judge.


def _scope_bad(repo: Repo) -> None:
    manifest = dict(BASE_MANIFEST, scope={"allow": ["src/**"], "deny": []})
    repo.seed(manifest)
    repo.write("rogue.txt", "outside the allowed area\n")
    repo.commit("rogue file")


# ....................... #


def _scope_clean(repo: Repo) -> None:
    manifest = dict(BASE_MANIFEST, scope={"allow": ["src/**"], "deny": []})
    repo.seed(manifest)
    repo.write("src/app.py", "print('changed')\n")
    repo.commit("in-scope change")


# ....................... #


def _acceptance_bad(repo: Repo) -> None:
    manifest = dict(BASE_MANIFEST)
    manifest["gates"] = [
        {
            "name": "acceptance",
            "run": "@task.acceptance",
            "state": "blocking",
            "origin": "structural",
            "commands": ["false"],
            "timeout": 30,
        }
    ]
    repo.seed(manifest)
    repo.write("src/app.py", "print('red build')\n")
    repo.commit("change")


# ....................... #


def _acceptance_clean(repo: Repo) -> None:
    repo.seed()
    repo.write("src/app.py", "print('green build')\n")
    repo.commit("change")


# ....................... #


def _acceptance_flaky(repo: Repo) -> None:
    flaky = "test -e .torve/marker || { mkdir -p .torve; touch .torve/marker; exit 1; }"
    manifest = dict(BASE_MANIFEST)
    manifest["gates"] = [
        {
            "name": "acceptance",
            "run": "@task.acceptance",
            "state": "blocking",
            "origin": "structural",
            "commands": [flaky],
            "timeout": 30,
        }
    ]
    repo.seed(manifest)
    repo.write("src/app.py", "print('flaky build')\n")
    repo.commit("change")


# ....................... #


def _tampering_bad(repo: Repo) -> None:
    repo.seed()
    repo.task(base_task(allow=["src/**"]), log_document())
    repo.write("tests/test_app.py", "def test_app():\n    assert 1 == 1  # weakened\n")
    repo.commit("edit tests without licence")


# ....................... #


def _tampering_clean(repo: Repo) -> None:
    repo.seed()
    repo.task(base_task(allow=["src/**", "tests/**"]), log_document())
    repo.write("tests/test_app.py", "def test_app():\n    assert True  # licensed edit\n")
    repo.commit("licensed test edit")


# ....................... #


def _decisions_silence(repo: Repo) -> None:
    repo.seed()
    repo.task(base_task(allow=["src/**"], decisions=LOCKED_D1), log_document())
    repo.write("src/app.py", "print('touched governed area')\n")
    repo.commit("silent touch of a LOCKED area")


# ....................... #


def _decisions_silence_no_log(repo: Repo) -> None:
    # A-13/D-3.21: absence is an empty log — the silence check still convicts.
    repo.seed()
    repo.task(base_task(allow=["src/**"], decisions=LOCKED_D1), None)
    repo.write("src/app.py", "print('touched governed area, wrote nothing')\n")
    repo.commit("silent touch with no log at all")


# ....................... #


def _decisions_no_log_untouched(repo: Repo) -> None:
    # The twin: inherited decisions whose area was never touched need no log.
    decisions = [
        {"id": "D-1", "grade": "LOCKED", "text": "docs layout is settled", "paths": ["docs/**"]}
    ]
    repo.seed()
    repo.task(base_task(allow=["src/**"], decisions=decisions), None)
    repo.write("src/app.py", "print('outside the governed area')\n")
    repo.commit("clean change, no log written")


# ....................... #


def _decisions_illegal(repo: Repo) -> None:
    repo.seed()
    repo.task(
        base_task(allow=["src/**"], decisions=LOCKED_D1),
        log_document(
            entry(kind="contradicted", action="departed", claim="departed from a locked decision")
        ),
    )
    repo.write("src/app.py", "print('flipped a lock')\n")
    repo.commit("illegal action for the grade")


# ....................... #


def _decisions_unlocatable(repo: Repo) -> None:
    repo.seed()
    repo.task(
        base_task(allow=["src/**"], decisions=LOCKED_D1),
        log_document(entry(evidence="nowhere/nothing.py:5")),
    )
    repo.write("src/app.py", "print('evidence points nowhere')\n")
    repo.commit("unlocatable evidence")


# ....................... #


def _decisions_valid(repo: Repo) -> None:
    repo.seed()
    repo.task(base_task(allow=["src/**"], decisions=LOCKED_D1), log_document(entry()))
    repo.write("src/app.py", "print('reported touch')\n")
    repo.commit("valid log")


# ....................... #


def _self_audit_bad(repo: Repo) -> None:
    repo.seed()
    repo.task(base_task(allow=["src/**"]), log_document(drift_count=None))
    repo.write("src/app.py", "print('unexamined')\n")
    repo.commit("log without drift count")


# ....................... #


def _self_audit_absent(repo: Repo) -> None:
    # A-13/D-3.21: no entries, no file — a clean run owes nobody a log.
    repo.seed()
    repo.task(base_task(allow=["src/**"]), None)
    repo.write("src/app.py", "print('clean, nothing written')\n")
    repo.commit("clean change without a log")


# ....................... #


def _self_audit_clean(repo: Repo) -> None:
    repo.seed()
    repo.task(base_task(allow=["src/**"]), log_document())
    repo.write("src/app.py", "print('examined')\n")
    repo.commit("log with drift count")


# ....................... #

FAKE_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"  # AWS's documented example key id


# ....................... #


def _secrets_bad(repo: Repo) -> None:
    repo.seed()
    repo.write("src/config.py", f"aws_key = '{FAKE_AWS_KEY}'\n")
    repo.commit("leak a credential")


# ....................... #


def _secrets_clean(repo: Repo) -> None:
    repo.seed()
    repo.write("src/config.py", "aws_key = load_from_vault()\n")
    repo.commit("no credential")


# ....................... #


def _bypass_honored(repo: Repo) -> None:
    _scope_bad(repo)
    repo.write("rogue2.txt", "second file\n")
    repo.git("add", "-A")
    repo.git(
        "commit",
        "-q",
        "--no-gpg-sign",
        "-m",
        "widen scope\n\nTorve-Bypass: scope: allow list predates this task, fix follows",
    )


# ....................... #


def _bypass_refused_for_secrets(repo: Repo) -> None:
    _secrets_bad(repo)
    repo.write("src/other.py", "x = 1\n")
    repo.git("add", "-A")
    repo.git(
        "commit",
        "-q",
        "--no-gpg-sign",
        "-m",
        "try to bypass\n\nTorve-Bypass: secrets: just this once",
    )


# ....................... #


def _layout_forbidden_name(repo: Repo) -> None:
    # D-15.5: only the module name is wrong; nothing else is checked here.
    repo.seed()
    repo.write("src/pkg/utils.py", '"""Anything goes here, which is the problem."""\n')
    repo.commit("catch-all module")


# ....................... #


def _layout_clean(repo: Repo) -> None:
    repo.seed()
    repo.write("src/pkg/sandbox_names.py", '"""A module named for what it holds."""\n')
    repo.commit("named module")


# ....................... #


def _text_help_rfc(repo: Repo) -> None:
    repo.seed()
    repo.write("src/torve/cli/thing.py", 'HELP = "Mint contracts per RFC 0007."\n')
    repo.commit("help text citing an RFC number")


# ....................... #


def _text_docstring_decision(repo: Repo) -> None:
    repo.seed()
    repo.write("src/torve/cli/thing.py", 'def thing() -> None:\n    """Size estimate (D-2.9)."""\n')
    repo.commit("command docstring citing a decision")


# ....................... #


def _text_corpus_path(repo: Repo) -> None:
    repo.seed()
    repo.write("src/torve/cli/thing.py", 'ERROR = "see rfcs/0012-migrations.md"\n')
    repo.commit("string citing a corpus path")


# ....................... #


def _text_module_docstring_passes(repo: Repo) -> None:
    # The case that matters most: the surface where references are wanted
    # must never be flagged, or the gate teaches people to strip them.
    repo.seed()
    repo.write(
        "src/torve/cli/thing.py",
        '"""The thing command (D-2.9, RFC 0007 §3); see rfcs/ for why."""\n\n'
        'def _helper() -> None:\n    """Private, editor-facing (D-2.9)."""\n',
    )
    repo.commit("module and private docstrings citing decisions")


# ....................... #

CASES: list[Case] = [
    Case("scope: file outside allow", "scope", "fail", _scope_bad),
    Case("scope: clean twin", "scope", "pass", _scope_clean),
    Case("acceptance: red command", "acceptance", "fail", _acceptance_bad),
    Case("acceptance: clean twin", "acceptance", "pass", _acceptance_clean),
    Case("acceptance: flake is flaky, not fail", "acceptance", "flaky", _acceptance_flaky),
    Case("no-test-tampering: unlicensed edit", "no-test-tampering", "fail", _tampering_bad),
    Case("no-test-tampering: clean twin", "no-test-tampering", "pass", _tampering_clean),
    Case("decisions-reported: silence", "decisions-reported", "fail", _decisions_silence),
    Case(
        "decisions-reported: silence with no log at all",
        "decisions-reported",
        "fail",
        _decisions_silence_no_log,
    ),
    Case(
        "decisions-reported: no log, nothing governed touched",
        "decisions-reported",
        "pass",
        _decisions_no_log_untouched,
    ),
    Case("decisions-reported: illegal action", "decisions-reported", "fail", _decisions_illegal),
    Case(
        "decisions-reported: unlocatable evidence",
        "decisions-reported",
        "fail",
        _decisions_unlocatable,
    ),
    Case("decisions-reported: valid log", "decisions-reported", "pass", _decisions_valid),
    Case("self-audit: no drift count", "self-audit", "fail", _self_audit_bad),
    Case("self-audit: clean twin", "self-audit", "pass", _self_audit_clean),
    Case("self-audit: absent log is an empty log", "self-audit", "pass", _self_audit_absent),
    Case("secrets: leaked key", "secrets", "fail", _secrets_bad),
    Case("secrets: clean twin", "secrets", "pass", _secrets_clean),
    Case("bypass: signed trailer converts a red scope", "scope", "bypassed", _bypass_honored),
    Case("bypass: refused for secrets", "secrets", "fail", _bypass_refused_for_secrets),
    Case("source-layout: catch-all module name", "source-layout", "fail", _layout_forbidden_name),
    Case("source-layout: named module twin", "source-layout", "pass", _layout_clean),
    Case("user-facing-text: help cites an RFC number", "user-facing-text", "fail", _text_help_rfc),
    Case(
        "user-facing-text: command docstring cites a decision",
        "user-facing-text",
        "fail",
        _text_docstring_decision,
    ),
    Case(
        "user-facing-text: string cites a corpus path",
        "user-facing-text",
        "fail",
        _text_corpus_path,
    ),
    Case(
        "user-facing-text: module docstring cites freely",
        "user-facing-text",
        "pass",
        _text_module_docstring_passes,
    ),
]


# ....................... #


def run_case(case: Case) -> CaseOutcome:
    with tempfile.TemporaryDirectory(prefix="torve-sabotage-") as tmp:
        repo = Repo(Path(tmp))
        case.build(repo)
        manifest = load_manifest(layout.gates_file(repo.root))
        ctx = build_context(repo.root, manifest, base="main")
        report = run_gates(ctx, only={case.gate})
        result = report.results[0]
        ok = result.outcome == case.expected
        return CaseOutcome(
            name=case.name,
            gate=case.gate,
            expected=case.expected,
            got=result.outcome,
            ok=ok,
            detail="" if ok else result.output,
        )


# ....................... #


def run_all() -> list[CaseOutcome]:
    return [run_case(case) for case in CASES]
