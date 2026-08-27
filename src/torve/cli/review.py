"""`torve review` — the reviewer regression corpus (RFC 0005 §6, D-5.6):
seeded-defect cases under `.torve/review-corpus/<case>/`, replayed through
the reviewer tier; a prompt or model change that drops a catch is a
regression. Parsing and rendering only (D-15.6); the review machinery lives
in `torve.application.review`.

A case directory holds `case.yaml` (intent, inherited decisions, the
expected findings, an optional degraded flag), `diff.patch` (the seeded
change the reviewer judges), and `tree/` (the workspace the evidence must
locate against). A clean case expects nothing — the reviewer's permission
to say "clean" (RFC 0005 §5) is itself regression-tested.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, cast

import typer
import yaml

from torve.cli.console import (
    STYLE_FAIL,
    STYLE_PASS,
    Format,
    closing,
    emit_json,
    fail,
    header,
    make_table,
    mark,
    out,
)
from torve.cli.options import (
    ConfigOption,
    FormatOption,
    RootOption,
    RuntimeName,
    load_config,
    runtime_for,
)
from torve.domain.states import EXIT_CONFIG, EXIT_GATES_RED, EXIT_OK

# ----------------------- #

review_app = typer.Typer(no_args_is_help=True, help="The reviewer and its regression corpus.")

CORPUS_DIR = "review-corpus"


# ....................... #


def _load_case(case_dir: Path) -> dict[str, Any]:
    document = yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{case_dir.name}: case.yaml is not a mapping")
    return cast("dict[str, Any]", document)


# ....................... #


def _case_outcome(
    case_dir: Path, document: dict[str, Any], config: Any, runtime: Any, agent: Any, root: Path
) -> dict[str, Any]:
    from torve.application.review import run_review
    from torve.domain.task import Budget, Task

    expectations: list[dict[str, str]] = list(document.get("expect", []))
    name = case_dir.name
    target = Task(
        id=f"corpus-{name}",
        intent=str(document.get("intent", "")),
        decisions=document.get("decisions", []),
    )
    review = Task(
        id=f"corpus-{name}-review",
        role="review",
        targets=[target.id],
        decisions=target.decisions,
        budget=Budget(iterations=1),
        tier="reviewer",
    )
    outcome = run_review(
        root,
        case_dir / "tree",
        target,
        review,
        config,
        runtime,
        agent,
        (case_dir / "diff.patch").read_text(encoding="utf-8"),
        [],
        "corpus",
        degraded=bool(document.get("degraded", False)),
    )

    missed: list[str] = []
    for expected in expectations:
        severity = str(expected.get("severity", ""))
        needle = str(expected.get("claim_contains", "")).lower()
        caught = any(f.severity == severity and needle in f.claim.lower() for f in outcome.kept)
        if not caught:
            missed.append(f"{severity}: …{needle}…")
    false_blockers = [f.claim for f in outcome.kept if f.severity == "blocker" and not expectations]
    return {
        "case": name,
        "expected": len(expectations),
        "caught": len(expectations) - len(missed),
        "missed": missed,
        "false_blockers": false_blockers,
        "findings": [f.model_dump() for f in outcome.kept],
        "discarded": len(outcome.discarded),
        "unparseable": outcome.unparseable,
        "ok": not missed and not false_blockers and not outcome.unparseable,
    }


# ....................... #


@review_app.command("corpus")
def corpus(
    case: Annotated[
        str | None, typer.Argument(help="One case to replay; omit to replay every case.")
    ] = None,
    runtime_name: Annotated[RuntimeName | None, typer.Option("--runtime")] = None,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Replay the seeded-defect corpus through the reviewer tier and report
    which expected findings were caught. A dropped catch or an invented
    blocker on a clean case is a regression — exit 1."""
    from torve.cli.run import build_reviewer_agent

    root = root.resolve()
    config = load_config(root, config_path)
    corpus_root = root / ".torve" / CORPUS_DIR
    cases = (
        sorted(d for d in corpus_root.iterdir() if d.is_dir() and (d / "case.yaml").is_file())
        if corpus_root.is_dir()
        else []
    )
    if case is not None:
        cases = [d for d in cases if d.name == case]
        if not cases:
            raise fail(f"configuration error: no case {case!r} under {corpus_root}", EXIT_CONFIG)
    if not cases:
        raise fail(f"configuration error: no corpus cases under {corpus_root}", EXIT_CONFIG)

    try:
        agent = build_reviewer_agent(config, root)
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc
    runtime = runtime_for(config, runtime_name)

    results = [
        _case_outcome(case_dir, _load_case(case_dir), config, runtime, agent, root)
        for case_dir in cases
    ]
    passed = all(r["ok"] for r in results)

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "ok": passed, "cases": results})
        raise typer.Exit(EXIT_OK if passed else EXIT_GATES_RED)
    console = out(fmt)
    header(console, "review corpus", f"{len(results)} case(s)")
    table = make_table("", "case", "caught", "notes")
    for result in results:
        notes: list[str] = []
        if result["missed"]:
            notes.append(f"missed: {'; '.join(result['missed'])}")
        if result["false_blockers"]:
            notes.append(f"invented blocker(s): {len(result['false_blockers'])}")
        if result["unparseable"]:
            notes.append("output unparseable")
        if result["discarded"]:
            notes.append(f"{result['discarded']} discarded")
        table.add_row(
            mark("pass" if result["ok"] else "fail"),
            result["case"],
            f"{result['caught']}/{result['expected']}",
            "; ".join(notes),
        )
    console.print(table)
    closing(
        console,
        "corpus green" if passed else "regression: the reviewer dropped a catch",
        STYLE_PASS if passed else STYLE_FAIL,
    )
    raise typer.Exit(EXIT_OK if passed else EXIT_GATES_RED)


# ....................... #


@review_app.command("pr")
def pr(
    number: Annotated[int, typer.Argument(help="Pull request number on scm.repo.")],
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Review one pull request and post the findings back as a comment.
    Skips drafts, empty and closed pull requests, and configured authors;
    each head is reviewed at most once."""
    import os

    from torve.adapters.vcs.git import GhScm, GitVcs
    from torve.application.review import review_pull_request
    from torve.cli.run import build_reviewer_agent

    root = root.resolve()
    config = load_config(root, config_path)
    if not {"pr_opened", "pr_synchronized"} & set(config.review.on):
        raise fail(
            "configuration error: review.on includes neither pr_opened nor "
            "pr_synchronized — configuring nothing decides nothing",
            EXIT_CONFIG,
        )
    if not config.scm.repo:
        raise fail("configuration error: scm.repo names the forge repository", EXIT_CONFIG)
    try:
        agent = build_reviewer_agent(config, root)
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc
    token = os.environ.get(config.scm.token_env) if config.scm.token_env else None
    outcome = review_pull_request(
        root,
        config,
        runtime_for(config, None),
        agent,
        GhScm(config.scm.repo, config.scm.token_env),
        GitVcs(),
        number,
        token,
    )
    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "pr": number,
                "action": outcome.action,
                "detail": outcome.detail,
                "review": outcome.review_id,
                "findings": outcome.findings,
                "blockers": outcome.blockers,
                "comment": outcome.comment,
            }
        )
    else:
        console = out(fmt)
        header(console, "review pr", f"#{number}")
        line = f"{outcome.action}: {outcome.detail}"
        if outcome.review_id:
            line += f" ({outcome.review_id}, {outcome.findings} finding(s))"
        console.print(line)
        if outcome.comment:
            closing(console, outcome.comment)
    raise typer.Exit(EXIT_OK)
