"""The survey (RFC 0031 §5.1, phase 1): a read-only, agentless replay of a
repository's last N landings through the gate battery — one truncated
clone-at-landing per landing (D-31.4), the landing's first parent as the gate
base, per-gate outcomes collected, the clone removed. No model, no sandbox,
no credentials, nothing written into the target beyond the report the operator
names (D-31.1).

The report is the product: exit 0 on any completed measurement, because a red
history is a successful measurement of a red history. Task- and log-input
gates record their no-task skip, and the report names those gates as what a
corpus would add (D-31.4) — the silence is the corpus's absence made visible.

A target with no gate manifest is surveyed with the shipped product battery
under manifest defaults; one with a manifest is surveyed with its own
(D-31.5). House-convention gates are not part of the product battery: they
encode this corpus's conventions and run only where the target's own manifest
names them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from torve.config import layout
from torve.config.manifest import Gate, Manifest, load_manifest
from torve.domain.task import SCHEMA_VERSION
from torve.gates.context import build_context
from torve.gates.contract import NO_TASK
from torve.gates.runner import run_gates

# ----------------------- #

# The survey's outcome vocabulary (RFC 0031 §5.1): a gate that fired would
# have stopped the work; a clean gate measured nothing wrong; a skipped gate
# did not run. Bypassed counts as fired — the signature is spent, the gate
# did not measure clean.
_FIRED = frozenset({"fail", "error", "bypassed"})
_CLEAN = frozenset({"pass", "flaky"})


def _bucket(outcome: str) -> str:
    if outcome in _FIRED:
        return "fired"

    if outcome in _CLEAN:
        return "clean"

    return "skipped"


# ....................... #


@dataclass
class SurveySource:
    """Host-side git callables the CLI injects — the application layer
    orchestrates, the workspace adapter owns the history mechanics."""

    landings: Callable[[str, int], list[tuple[str, str]]]  # branch, n -> [(sha, subject)] newest first
    parent_of: Callable[[str], str | None]  # sha -> first parent, None at the root commit
    create_workspace: Callable[[str, str], Path]  # (label, landing sha) -> clone path
    remove_workspace: Callable[[str], None]  # label


# ....................... #


def default_battery() -> Manifest:
    """The shipped product battery (D-31.5): the structural builtins every
    target gets under manifest defaults. House-convention gates are excluded —
    they run only where a target's own manifest names them."""

    gates = [
        Gate(name="scope", run="@scope", state="blocking", origin="structural"),
        Gate(name="secrets", run="@secrets", state="blocking", origin="structural"),
        Gate(name="no-test-tampering", run="@no-test-tampering", state="blocking", origin="structural"),
        Gate(name="decisions-reported", run="@decisions-reported", state="blocking", origin="structural"),
        Gate(name="self-audit", run="@self-audit", state="shadow", origin="structural"),
        Gate(name="acceptance", run="@task.acceptance", state="blocking", origin="structural"),
    ]

    return Manifest(schema_version=SCHEMA_VERSION, gates=gates)


# ....................... #


def resolve_manifest(root: Path) -> tuple[Manifest, str]:
    """The battery the survey runs: the target's own gate manifest when it has
    one, else the shipped product battery under manifest defaults (D-31.5).
    The second element names the source in the report. A malformed manifest
    raises ValueError (a configuration error for the CLI to report)."""

    path = layout.gates_file(root)

    if path.is_file():
        return load_manifest(path), "target"

    return default_battery(), "product-default"


# ....................... #


def run_survey(
    root: Path,
    source: SurveySource,
    *,
    branch: str,
    last: int,
) -> dict[str, Any]:
    """Walk the first-parent chain of `branch` for the last `last` landings,
    run the battery against each (base = the landing's first parent) in a
    truncated clone, remove the clone, and return the survey report as a
    JSON-serializable document.

    Raises ValueError on configuration problems and GitError/WorkspaceError
    on infrastructure failure; a completed measurement always returns."""
    if last < 1:
        raise ValueError("last must be a positive integer")

    manifest, manifest_source = resolve_manifest(root)
    resolved = manifest.resolved_gates()
    order = [g.name for g in resolved]

    landings: list[dict[str, Any]] = []
    totals: dict[str, dict[str, int]] = {
        name: {"fired": 0, "clean": 0, "skipped": 0} for name in order
    }
    # A gate is a corpus gap only if it never measured anything across the
    # window (every landing skipped it) and its own silence is the no-task
    # skip (NO_TASK, D-31.4) — not the runner's "not run" short-circuit and
    # not acceptance's structural "no commands" skip.
    ran: dict[str, bool] = dict.fromkeys(order, False)
    no_task_skip: dict[str, bool] = dict.fromkeys(order, False)

    for sha, subject in source.landings(branch, last):
        parent = source.parent_of(sha)

        if parent is None:
            # The root commit has no base to diff against; the landing is
            # still recorded, as the beginning of the window.
            landings.append(
                {
                    "sha": sha,
                    "short": sha[:7],
                    "subject": subject,
                    "parent": None,
                    "gates": [],
                }
            )

            continue

        label = f"survey-{sha}"

        try:
            workspace = source.create_workspace(label, sha)
            ctx = build_context(workspace, manifest, base=parent)
            report = run_gates(ctx)

            gates: list[dict[str, Any]] = [
                {
                    "name": r.name,
                    "outcome": r.outcome,
                    "state": r.state,
                    "duration_s": r.duration_s,
                    "exit_code": r.exit_code,
                    "output": r.output,
                    "no_corpus": r.outcome == "skipped" and r.output == NO_TASK.output,
                }
                for r in report.results
            ]

        finally:
            source.remove_workspace(label)

        for gate in gates:
            totals[gate["name"]][_bucket(gate["outcome"])] += 1

            if gate["outcome"] != "skipped":
                ran[gate["name"]] = True

            if gate["no_corpus"]:
                no_task_skip[gate["name"]] = True

        landings.append(
            {
                "sha": sha,
                "short": sha[:7],
                "subject": subject,
                "parent": parent,
                "gates": gates,
            }
        )

    surveyed = len(landings)

    # What a corpus would add (D-31.4): the gates that never measured a
    # single landing and whose silence is the no-task skip — their silence
    # is the corpus's absence made visible.
    corpus_adds = [
        name for name in order if not ran[name] and no_task_skip[name] and surveyed > 0
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "survey",
        "branch": branch,
        "last": last,
        "manifest": manifest_source,
        "landings": landings,
        "summary": {
            "landings": surveyed,
            "by_gate": totals,
            "corpus_adds": corpus_adds,
        },
    }
