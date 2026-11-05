"""The sabotage suite (S-0002/gates-are-themselves-verified, S-0002/D-2): one deliberately bad change per
gate, applied to a scratch repository, asserting the gate goes red — plus a
clean twin per gate asserting green, because a gate that cannot pass is as
broken as one that cannot fail.

Without this, a gate that silently stops working looks identical to a gate
that never fires because the code is clean.
"""

from __future__ import annotations

import shutil
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
    {
        "id": "S-0001/D-1",
        "grade": "LOCKED",
        "text": "app module layout is settled",
        "paths": ["src/**"],
    }
]


# ....................... #


def entry(**overrides: Any) -> dict[str, Any]:
    """One A-1-shaped YAML log entry; pass field overrides, or None to drop a field."""

    fields: dict[str, Any] = {
        "decision": "S-0001/D-1",
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
    document: dict[str, Any] = {
        "schema_version": 1,
        "task": TASK_ID,
        # The S-0001/D-36 pin the gate requires since S-0021/A-1; the sabotage tree's
        # own base commit is unknowable here, so a well-formed placeholder
        # stands in — the gate checks shape, evidence checks resolution.
        "repo": "sabotage/repo",
        "base": "0" * 40,
    }

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
        # tracked (S-0013/what-does-not-belong-in-either).
        self.write(".gitignore", ".torve/telemetry.jsonl\n.torve/skills/\n.torve/tmp/\n")
        self.write("src/app.py", "print('hello')\n")
        self.write("tests/test_app.py", "def test_app():\n    assert True\n")
        self.commit("init")
        self.git("checkout", "-q", "-b", f"torve/{TASK_ID}")

    # ....................... #

    def task(self, task: dict[str, Any], log: str | None) -> None:
        # One directory per task (S-0001/A-5); the log only exists if written (S-0003/A-2).
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
    # Executables the gate command shells out to; when one is absent — a
    # consuming repository's `torve gates check` runs without the dev tools a
    # repository-specific gate needs — the case reports skipped instead of
    # falsely convicting the environment (the layering suite's skipif, moved
    # into the shipped set's data).
    requires: tuple[str, ...] = ()


# ----------------------- #
# Scenario builders — each seeds a repository the gate under test must judge.


def _scope_bad(repo: Repo) -> None:
    # Only the fence convicts (S-0122/D-12): a file merely outside `allow` is evidence.
    manifest = dict(BASE_MANIFEST, scope={"allow": ["src/**"], "deny": ["vendor/**"]})
    repo.seed(manifest)
    repo.write("vendor/rogue.txt", "inside the fence\n")
    repo.commit("rogue file")


# ....................... #


def _scope_clean(repo: Repo) -> None:
    manifest = dict(BASE_MANIFEST, scope={"allow": ["src/**"], "deny": []})
    repo.seed(manifest)
    repo.write("src/app.py", "print('changed')\n")
    repo.commit("in-scope change")


# ....................... #


def _scope_contingent_promoted(repo: Repo) -> None:
    # S-0104/D-2, D-7: a path the contract names as a contingent neighbour is
    # a plausible reach, not a conviction — the gate promotes it and says so.
    # The holder half of the rule (a path an in-flight task owns is refused)
    # is the runner's write-time fence, not the gate's: the gate judges one
    # contract and reads no other task, so it lives in tests/test_sabotage.py
    # where the runner is reachable (gates may not import `application`).
    repo.seed()
    task = base_task(allow=["src/**"])
    task["scope"]["contingent"] = ["docs/notes.md"]
    repo.task(task, log_document())
    repo.write("docs/notes.md", "the neighbour the scope reaches\n")
    repo.commit("reaches a contingent neighbour")


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


# The `suite` builtin (S-0118/D-1): the whole suite once, then the failing
# files rerun at the head and the still-red ones run on the merge base. One
# case proves a regression convicts; the twin proves a red the base carries
# passes, and the family's remaining cases pin the flaky and fail-closed arms.
# The scratch repository is not a uv project, so the launcher is `pytest`.

SUITE_TEST_RUN = "pytest -q -p no:cacheprovider"
SUITE_REQUIRES = ("pytest",)

SUITE_MANIFEST: dict[str, Any] = {
    "schema_version": 1,
    "scope": {"allow": [], "deny": []},
    "tests": {"run": SUITE_TEST_RUN},
    "gates": [
        {
            "name": "suite",
            "run": "@suite",
            "state": "blocking",
            "origin": "S-0118",
            "timeout": 120,
            "sabotage": "suite",
            "commands": [SUITE_TEST_RUN],
        }
    ],
}

# Reads the source module as a file, so the test needs no import path and the
# same file runs on both trees.
SUITE_READING_TEST = (
    "from pathlib import Path\n\n"
    'VALUE = (Path(__file__).resolve().parents[1] / "src" / "app.py").read_text()\n\n\n'
    "def test_app():\n"
    '    assert "VALUE = 1" in VALUE\n'
)

SUITE_HEAD_CHANGE = "VALUE = 2\n"

# Fails once, leaves a marker, then passes: a red the rerun clears as flaky.
SUITE_FLAKY_TEST = (
    "from pathlib import Path\n\n"
    'MARKER = Path(__file__).resolve().parents[1] / ".torve" / "suite-flaky"\n\n\n'
    "def test_app():\n"
    "    if not MARKER.exists():\n"
    "        MARKER.parent.mkdir(parents=True, exist_ok=True)\n"
    '        MARKER.write_text("x")\n'
    "        assert False\n"
)

# The second test fails only because the first ran before it: the rerun keeps
# the order inside the file, so it convicts (S-0118/D-1).
SUITE_ORDER_BASE_TEST = (
    'STATE = {"seen": False}\n\n\n'
    "def test_first():\n"
    '    STATE["seen"] = False\n\n\n'
    "def test_second():\n"
    '    assert not STATE["seen"]\n'
)

SUITE_ORDER_HEAD_TEST = SUITE_ORDER_BASE_TEST.replace(
    'STATE["seen"] = False', 'STATE["seen"] = True'
)


def _suite_regression(repo: Repo) -> None:
    repo.seed(SUITE_MANIFEST)
    repo.write("src/app.py", SUITE_HEAD_CHANGE)
    repo.write("tests/test_app.py", SUITE_READING_TEST)
    repo.commit("a change the existing test no longer meets")


# ....................... #


def _suite_inherited(repo: Repo) -> None:
    # The base branch is already red; the attempt changes nothing the failing
    # test reads, so the red is inherited and never convicts (S-0118/D-4).
    repo.seed(SUITE_MANIFEST)
    repo.git("checkout", "-q", "main")
    repo.write("src/app.py", SUITE_HEAD_CHANGE)
    repo.write("tests/test_app.py", SUITE_READING_TEST)
    repo.commit("a red the base already carries")
    repo.git("checkout", "-q", f"torve/{TASK_ID}")
    repo.git("merge", "-q", "main")
    repo.write("src/other.py", "VALUE = 3\n")
    repo.commit("an unrelated change")


# ....................... #


def _suite_flaky(repo: Repo) -> None:
    repo.seed(SUITE_MANIFEST)
    repo.write("tests/test_app.py", SUITE_FLAKY_TEST)
    repo.commit("a test that passes on the rerun")


# ....................... #


def _suite_order(repo: Repo) -> None:
    repo.seed(SUITE_MANIFEST)
    repo.git("checkout", "-q", "main")
    repo.write("tests/test_app.py", SUITE_ORDER_BASE_TEST)
    repo.commit("an order-dependent suite, green")
    repo.git("checkout", "-q", f"torve/{TASK_ID}")
    repo.git("merge", "-q", "main")
    repo.write("tests/test_app.py", SUITE_ORDER_HEAD_TEST)
    repo.commit("the first test now poisons the second")


# ....................... #


def _suite_new_file(repo: Repo) -> None:
    # The base cannot run the test at all: no reading, so it convicts (D-2).
    repo.seed(SUITE_MANIFEST)
    repo.write("tests/test_new.py", "def test_new():\n    assert False\n")
    repo.commit("a failing test the base lacks")


# ....................... #


def _suite_collection_error(repo: Repo) -> None:
    # A red that names no failing test convicts (D-2).
    repo.seed(SUITE_MANIFEST)
    repo.write("tests/test_app.py", "def test_app(:\n    assert True\n")
    repo.commit("a file that does not collect")


# ....................... #


def _suite_reports(repo: Repo) -> None:
    manifest = dict(SUITE_MANIFEST)
    manifest["gates"] = [
        {
            **SUITE_MANIFEST["gates"][0],
            "commands": [
                "pytest -q -p no:cacheprovider; code=$?; echo HEAD > suite-artifact; exit $code",
                "cat suite-artifact",
                "exit 1",
            ],
        }
    ]
    repo.seed(manifest)
    repo.write("src/app.py", "print('changed')\n")
    repo.commit("a green suite with a report that goes red")


# ....................... #

CASES: list[Case] = [
    Case("scope: file inside the fence", "scope", "fail", _scope_bad),
    Case("scope: clean twin", "scope", "pass", _scope_clean),
    Case(
        "scope: contingent neighbour is promoted",
        "scope",
        "pass",
        _scope_contingent_promoted,
    ),
    Case("acceptance: red command", "acceptance", "fail", _acceptance_bad),
    Case("acceptance: clean twin", "acceptance", "pass", _acceptance_clean),
    Case("acceptance: flake is flaky, not fail", "acceptance", "flaky", _acceptance_flaky),
    Case("no-test-tampering: unlicensed edit", "no-test-tampering", "fail", _tampering_bad),
    Case("no-test-tampering: clean twin", "no-test-tampering", "pass", _tampering_clean),
    Case("secrets: leaked key", "secrets", "fail", _secrets_bad),
    Case("secrets: clean twin", "secrets", "pass", _secrets_clean),
    Case("bypass: signed trailer converts a red scope", "scope", "bypassed", _bypass_honored),
    Case("bypass: refused for secrets", "secrets", "fail", _bypass_refused_for_secrets),
    Case(
        "suite: a test the change broke",
        "suite",
        "fail",
        _suite_regression,
        requires=SUITE_REQUIRES,
    ),
    Case(
        "suite: an inherited red does not convict",
        "suite",
        "pass",
        _suite_inherited,
        requires=SUITE_REQUIRES,
    ),
    Case(
        "suite: a test that passes on the rerun is flaky",
        "suite",
        "flaky",
        _suite_flaky,
        requires=SUITE_REQUIRES,
    ),
    Case(
        "suite: order inside a file convicts",
        "suite",
        "fail",
        _suite_order,
        requires=SUITE_REQUIRES,
    ),
    Case(
        "suite: a failing file the base lacks convicts",
        "suite",
        "fail",
        _suite_new_file,
        requires=SUITE_REQUIRES,
    ),
    Case(
        "suite: a red naming no test convicts",
        "suite",
        "fail",
        _suite_collection_error,
        requires=SUITE_REQUIRES,
    ),
    Case(
        "suite: a report that goes red does not convict",
        "suite",
        "pass",
        _suite_reports,
        requires=SUITE_REQUIRES,
    ),
]


# ....................... #


def run_case(case: Case) -> CaseOutcome:
    missing = [tool for tool in case.requires if shutil.which(tool) is None]

    if missing:
        return CaseOutcome(
            name=case.name,
            gate=case.gate,
            expected=case.expected,
            got="skipped",
            ok=True,
            detail="absent from this environment: " + ", ".join(missing),
        )

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
