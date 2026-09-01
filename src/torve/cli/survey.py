"""`torve survey` — the brownfield survey (RFC 0031 §5.1, phase 1). Parsing
and rendering only (D-15.6); the measurement lives in
`torve.application.survey`. Read-only and agentless by construction: no model,
no sandbox, no credentials, and nothing written into the target beyond the
report the operator names with `--output` (D-31.1).

The exit code reports the measurement, not history's fortunes: a survey is a
measurement, and a red history is a successful measurement of a red history,
so a completed survey exits 0. 3 is a configuration problem (bad manifest),
4 an infrastructure failure (git failures).
"""

from __future__ import annotations

import json
import subprocess
from functools import partial
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.text import Text

from torve.adapters.workspace.git import ShadowWorkspace, WorkspaceError, parent_of
from torve.application.survey import SurveySource, run_survey
from torve.cli.console import (
    STYLE_DIM,
    Format,
    closing,
    emit_json,
    fail,
    header,
    live_status,
    make_table,
    out,
)
from torve.cli.options import FormatOption, RootOption
from torve.domain.states import EXIT_CONFIG, EXIT_INFRASTRUCTURE, EXIT_OK
from torve.gates.context import GitError

# ----------------------- #

_FIRED = frozenset({"fail", "error", "bypassed"})
_CLEAN = frozenset({"pass", "flaky"})


def _dump(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"


# ....................... #


def _default_branch(root: Path) -> str:
    """The branch the walk runs over when the operator names none: the
    remote's default when one is advertised, else main."""

    proc = subprocess.run(
        ["git", "-C", str(root), "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )

    if proc.returncode == 0:
        ref = proc.stdout.strip()

        if ref.startswith("refs/remotes/origin/"):
            return ref.removeprefix("refs/remotes/origin/")

    return "main"


# ....................... #


def _landings(root: Path, branch: str, last: int) -> list[tuple[str, str]]:
    """The first-parent chain of `branch`, newest first: (sha, subject)
    pairs. A merge-heavy history lands merge commits, never the side
    branches' own commits — that is the walk the survey pins (RFC 0031 §5.1)."""

    proc = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "log",
            "--first-parent",
            f"-n{last}",
            "--format=%H%x09%s",
            branch,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if proc.returncode != 0:
        raise WorkspaceError(proc.stderr.strip() or f"git log {branch!r} failed")

    entries: list[tuple[str, str]] = []

    for line in proc.stdout.splitlines():
        sha, sep, subject = line.partition("\t")

        if not sha.strip():
            continue

        entries.append((sha, subject if sep else ""))

    return entries


# ....................... #


def _parent(root: Path, sha: str) -> str | None:
    try:
        return parent_of(root, sha)

    except WorkspaceError:
        # The root commit has no parent; every other sha does.
        return None


# ....................... #


def survey_cmd(
    last: Annotated[
        int, typer.Option("--last", min=1, help="How many landings to walk, newest first.")
    ] = 20,
    branch: Annotated[
        str | None, typer.Option("--branch", help="Branch to walk; the default branch when omitted.")
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Write the JSON report to this path instead of stdout."),
    ] = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Replay the last landings on a branch through the gate battery and
    report what each gate would have fired. Read-only: the repository is
    never written, and a red history is a successful measurement."""

    root = root.resolve()
    branch = branch or _default_branch(root)
    shadow_ws = ShadowWorkspace(root, depth=2)

    source = SurveySource(
        landings=partial(_landings, root),
        parent_of=partial(_parent, root),
        create_workspace=shadow_ws.create_at,
        remove_workspace=shadow_ws.remove_at,
    )

    try:
        with live_status(f"surveying {branch}", fmt):
            report = run_survey(root, source, branch=branch, last=last)

    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    except (GitError, WorkspaceError) as exc:
        raise fail(f"infrastructure failure: {exc}", EXIT_INFRASTRUCTURE) from exc

    if output is not None:
        try:
            output.write_text(_dump(report), encoding="utf-8")

        except OSError as exc:
            raise fail(f"infrastructure failure: {exc}", EXIT_INFRASTRUCTURE) from exc

    if fmt is Format.JSON:
        if output is None:
            emit_json(report)

    else:
        console = out(fmt)
        summary = report["summary"]
        header(console, "survey", f"{branch} · last {report['last']} · battery {report['manifest']}")

        for landing in report["landings"]:
            if landing["parent"] is None:
                console.print(
                    Text(
                        f"  landing {landing['short']}  {landing['subject']}  (root commit — no base)",
                        STYLE_DIM,
                    )
                )

                continue

            fired = [g["name"] for g in landing["gates"] if g["outcome"] in _FIRED]
            clean = [g["name"] for g in landing["gates"] if g["outcome"] in _CLEAN]
            no_corpus = [g["name"] for g in landing["gates"] if g["no_corpus"]]
            silent = [
                g["name"]
                for g in landing["gates"]
                if g["outcome"] == "skipped" and not g["no_corpus"]
            ]

            bits: list[str] = []

            if fired:
                bits.append("fired: " + ", ".join(fired))

            if clean:
                bits.append("clean: " + ", ".join(clean))

            if no_corpus:
                bits.append("silent (no corpus): " + ", ".join(no_corpus))

            if silent:
                bits.append("silent: " + ", ".join(silent))

            console.print(f"  landing {landing['short']}  {'   '.join(bits)}")

        console.print()

        table = make_table("gate", "fired", "clean", "skipped")
        table.title = f"summary · {summary['landings']} landing(s)"

        for name, counts in summary["by_gate"].items():
            table.add_row(Text(name), *[str(counts[column]) for column in ("fired", "clean", "skipped")])

        console.print(table)

        adds = summary["corpus_adds"]

        if adds:
            console.print(Text("a corpus would add: " + ", ".join(adds), style=STYLE_DIM))

        closing(console, "a survey is a measurement; a red history is a successful measurement of a red history — exit 0")

    raise typer.Exit(EXIT_OK)
