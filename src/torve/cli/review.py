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

The corpus grows from escapes, not only seeds (RFC 0036 §5.3, D-36.4):
`torve review corpus add <fixing-commit>` scaffolds an entry from the
commit pair — the defective landing located by its `Torve-Task:` trailer,
the fixing commit's parent extracted as the tree — and refuses an entry
whose finding paragraph its operator has not yet written (D-36.5). The
scaffold's git plumbing stays in this module because the decision names
this file; the review machinery it seeds stays in `torve.application.review`.
"""

from __future__ import annotations

import io
import re
import subprocess
import tarfile
from pathlib import Path
from typing import Annotated, Any, cast

import typer
import yaml
from rich.text import Text
from typer._click.core import Command as _ClickCommand
from typer._click.core import Context as _ClickContext
from typer.core import TyperGroup

from torve.cli.console import (
    STYLE_FAIL,
    STYLE_PASS,
    STYLE_WARN,
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
from torve.domain.states import EXIT_CONFIG, EXIT_GATES_RED, EXIT_INFRASTRUCTURE, EXIT_OK

# ----------------------- #

review_app = typer.Typer(no_args_is_help=True, help="The reviewer and its regression corpus.")

CORPUS_DIR = "review-corpus"

# The scaffold writes this phrase into the finding it cannot write for a
# person; the loader refuses any case whose document still carries it.
FINDING_UNWRITTEN = "FINDING UNWRITTEN"

# The spelling that links a fixing commit to the landing that shipped the
# defect. No engine-written trailer carries that link, so an escape's fix
# cites it by hand, or the operator passes --defect at the scaffold.
FIXES_TRAILER = re.compile(r"^Torve-Fixes: (T-\d{4,})$", re.MULTILINE)

TASK_ID = re.compile(r"^T-\d{4,}$")

CASE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


# ....................... #


class UnwrittenFinding(ValueError):
    """A case scaffolded by `corpus add` whose finding paragraph is still
    the placeholder — the entry is not loadable until a person writes it."""


# ....................... #


class _CorpusGroup(TyperGroup):
    """The corpus group routes a bare case name to the replay: `torve review
    corpus <case>` predates `corpus add`, and the old spelling — case as a
    positional, flags on either side of it — survives by resolving any
    unknown non-option token as the `case` subcommand's own argument."""

    def resolve_command(
        self, ctx: _ClickContext, args: list[str]
    ) -> tuple[str | None, _ClickCommand | None, list[str]]:
        if args and args[0] not in self.commands and not args[0].startswith("-"):
            args = ["case", *args]

        return super().resolve_command(ctx, args)


corpus_app = typer.Typer(
    cls=_CorpusGroup,
    no_args_is_help=False,
    invoke_without_command=True,
    help=(
        "Replay the seeded-defect corpus through the reviewer tier, or scaffold "
        "a new entry from an escape with `corpus add`."
    ),
)

review_app.add_typer(corpus_app, name="corpus")


# ....................... #


def _has_unwritten_placeholder(node: object) -> bool:
    if isinstance(node, str):
        return FINDING_UNWRITTEN in node

    if isinstance(node, dict):
        values: list[object] = list(cast("dict[object, object]", node).values())
        return any(_has_unwritten_placeholder(value) for value in values)

    if isinstance(node, list):
        items: list[object] = list(cast("list[object]", node))
        return any(_has_unwritten_placeholder(value) for value in items)

    return False


# ....................... #


def _load_case(case_dir: Path) -> dict[str, Any]:
    loaded = yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8"))

    if not isinstance(loaded, dict):
        raise ValueError(f"{case_dir.name}: case.yaml is not a mapping")

    document = cast("dict[str, Any]", loaded)

    if _has_unwritten_placeholder(document):
        raise UnwrittenFinding(
            f"{case_dir.name}: case.yaml still carries the {FINDING_UNWRITTEN!r} placeholder — "
            "the finding a reviewer should have produced is a person's to write; "
            "write it, remove the placeholder, and replay again"
        )

    return document


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


def _replay(
    root: Path,
    config_path: Path | None,
    runtime_name: RuntimeName | None,
    fmt: Format,
    case: str | None = None,
) -> None:
    """One replay body, shared by the bare group invocation (every case) and
    the `case` subcommand (one case): the verb this repository has always
    had, name, flags, exit codes and output unchanged."""

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

    def replayed(case_dir: Path) -> dict[str, Any]:
        try:
            document = _load_case(case_dir)

        except UnwrittenFinding as exc:
            raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

        return _case_outcome(case_dir, document, config, runtime, agent, root)

    results = [replayed(case_dir) for case_dir in cases]

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


@corpus_app.callback(invoke_without_command=True)
def corpus_group(
    ctx: typer.Context,
    runtime_name: Annotated[RuntimeName | None, typer.Option("--runtime")] = None,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Replay the seeded-defect corpus through the reviewer tier and report
    which expected findings were caught. A dropped catch or an invented
    blocker on a clean case is a regression — exit 1."""

    if ctx.invoked_subcommand is None:
        _replay(root, config_path, runtime_name, fmt)
        return

    # Flags parsed at the group level flow to the resolved subcommand as its
    # defaults, so `corpus --format json seeded` reads exactly as it did
    # before `add` joined; an explicit subcommand-level flag still wins.
    ctx.default_map = {
        ctx.invoked_subcommand: {
            "runtime_name": runtime_name,
            "config_path": config_path,
            "root": root,
            "fmt": fmt,
        }
    }


# ....................... #


@corpus_app.command("case")
def corpus_case(
    case: Annotated[str, typer.Argument(help="The case to replay.")],
    runtime_name: Annotated[RuntimeName | None, typer.Option("--runtime")] = None,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Replay the seeded-defect corpus through the reviewer tier and report
    which expected findings were caught. A dropped catch or an invented
    blocker on a clean case is a regression — exit 1."""

    _replay(root, config_path, runtime_name, fmt, case)


# ....................... #


def _git(root: Path, *args: str) -> str | None:
    """git's trimmed stdout for a successful call, None when it fails."""

    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


# ....................... #


def _defective_landing(
    root: Path, fixing_sha: str, message: str, defect: str | None
) -> tuple[str, str]:
    """Resolve the pair's other commit: the landing that shipped the defect,
    found by its task trailer among the fixing commit's ancestors (D-36.5).
    Returns (landing sha, defective task id); refusals carry the
    instruction — the caller's numbers are already the scaffold's."""

    defect_id = defect

    if defect_id is not None and not TASK_ID.match(defect_id):
        raise fail(
            f"configuration error: --defect {defect!r} is not a task id like T-0142",
            EXIT_CONFIG,
        )

    if defect_id is None:
        cited = FIXES_TRAILER.search(message)

        if cited is None:
            raise fail(
                f"configuration error: no shipped defective ancestor: commit {fixing_sha[:10]} "
                f"names no defective task — cite the task whose landing shipped the defect in "
                f"the commit message as 'Torve-Fixes: T-0142', or pass --defect T-0142; "
                "a commit with no shipped defective ancestor has no corpus entry to scaffold",
                EXIT_CONFIG,
            )

        defect_id = cited.group(1)

    landing = _git(
        root,
        "log",
        fixing_sha,
        "-1",
        "--format=%H",
        "--fixed-strings",
        f"--grep=Torve-Task: {defect_id}",
    )

    if not landing:
        # The hand-commit fallback the shipped history needs, same shape as
        # the workspace adapter's: the parenthesized subject citation, over
        # this commit's ancestry only — never --all, the ancestor restriction
        # is what "shipped defective ancestor" means.
        log = _git(root, "log", fixing_sha, "--format=%H%x09%s")

        for line in (log or "").splitlines():
            sha, _, subject = line.partition("\t")

            if f"{defect_id})" in subject:
                landing = sha
                break

    if not landing:
        raise fail(
            f"configuration error: no shipped defective ancestor: no commit in the history of "
            f"{fixing_sha[:10]} carries 'Torve-Task: {defect_id}' — {defect_id} landed after "
            "this commit, outside its history, or not at all; pass the commit that fixes the "
            "defect, or name the task whose landing shipped it with --defect",
            EXIT_CONFIG,
        )

    if landing == fixing_sha:
        raise fail(
            f"configuration error: {defect_id}'s own landing is the fixing commit — the "
            "defective landing must come before the commit that fixes it; name the earlier "
            "task with --defect",
            EXIT_CONFIG,
        )

    return landing, defect_id


# ....................... #


def _scaffold_case_yaml(case_dir: Path, landing_subject: str) -> None:
    """The entry the pair proves and the person completes: a document whose
    finding still carries the placeholder the loader refuses."""

    document = {
        "intent": landing_subject,
        "decisions": [],
        "finding": (
            f"{FINDING_UNWRITTEN} — write, in the words you would have put in the "
            "review, the finding a reviewer should have produced against this diff. "
            "The finding is a person's judgement; no tool writes it."
        ),
        "expect": [
            {
                "severity": "blocker",
                "claim_contains": (
                    f"{FINDING_UNWRITTEN} — replace with the phrase that identifies the finding"
                ),
            }
        ],
    }

    comment = "\n".join(
        [
            "# Scaffolded by `torve review corpus add` from the commit pair:",
            "#   the defective landing's own diff in diff.patch,",
            "#   the fixing commit's parent tree extracted under tree/,",
            f"#   and this {FINDING_UNWRITTEN} placeholder where the finding belongs.",
            "# The replay refuses this entry until a person writes the finding and",
            "# removes the placeholder.",
        ]
    )

    body = yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=88)
    (case_dir / "case.yaml").write_text(comment + "\n" + body, encoding="utf-8")


# ....................... #


@corpus_app.command("add")
def corpus_add(
    fixing_commit: Annotated[
        str,
        typer.Argument(help="The commit that fixes a defect its landing already shipped."),
    ],
    defect: Annotated[
        str | None,
        typer.Option(
            "--defect",
            help=(
                "The task id whose landing shipped the defect; read from the fixing "
                "commit's Torve-Fixes trailer when omitted."
            ),
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help="Case directory name; defaults to escape-<task id> in lowercase.",
        ),
    ] = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Scaffold a review-corpus entry from an escape: the defective landing
    found by its Torve-Task trailer, its own diff as the patch, the fixing
    commit's parent as the tree — and the finding paragraph left explicitly
    for a person, since a placeholder the loader refuses until written keeps
    an unjust entry out of the measurement. A commit with no shipped
    defective ancestor is refused."""

    root = root.resolve()

    fixing_sha = _git(root, "rev-parse", "--verify", "--quiet", f"{fixing_commit}^{{commit}}")

    if not fixing_sha:
        raise fail(
            f"configuration error: {fixing_commit!r} is not a commit in the repository at "
            f"{root} — pass a resolvable commit (or HEAD) from a git checkout",
            EXIT_CONFIG,
        )

    message = _git(root, "log", "-1", "--format=%B", fixing_sha) or ""
    landing_sha, defect_id = _defective_landing(root, fixing_sha, message, defect)

    case_name = name or f"escape-{defect_id.lower()}"

    if not CASE_NAME.match(case_name):
        raise fail(
            f"configuration error: {case_name!r} is not a case name — use letters, digits, "
            "dots, dashes and underscores, starting with a letter or digit",
            EXIT_CONFIG,
        )

    case_dir = root / ".torve" / CORPUS_DIR / case_name

    if case_dir.exists():
        raise fail(
            f"configuration error: a corpus case already lives at {case_dir} — replay it "
            f"with `torve review corpus case {case_name}`, or pass --name to scaffold beside it",
            EXIT_CONFIG,
        )

    landing_diff = _git(root, "diff", f"{landing_sha}^", landing_sha)

    if landing_diff is None:
        raise fail(
            f"configuration error: the defective landing {landing_sha[:10]} is this "
            "repository's first commit and has no diff to scaffold — write the entry by hand",
            EXIT_CONFIG,
        )

    if not landing_diff:
        raise fail(
            f"configuration error: the defective landing {landing_sha[:10]} changed nothing "
            "against its first parent — there is no defective diff to scaffold",
            EXIT_CONFIG,
        )

    landing_subject = _git(root, "show", "-s", "--format=%s", landing_sha) or ""

    case_dir.mkdir(parents=True)
    _scaffold_case_yaml(case_dir, landing_subject)
    (case_dir / "diff.patch").write_text(landing_diff + "\n", encoding="utf-8")

    archived = subprocess.run(
        ["git", "-C", str(root), "archive", "--format=tar", f"{fixing_sha}^"],
        capture_output=True,
        check=False,
    )

    if archived.returncode != 0:
        raise fail(
            "infrastructure failure: git archive could not read the tree at "
            f"{fixing_sha[:10]}^: {archived.stderr.decode(errors='replace').strip()}",
            EXIT_INFRASTRUCTURE,
        )

    (case_dir / "tree").mkdir()

    with tarfile.open(fileobj=io.BytesIO(archived.stdout)) as bundle:
        # The data filter drops member types an archive of a commit tree
        # never carries (links, devices) and refuses path escapes.
        bundle.extractall(case_dir / "tree", filter="data")

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "case": case_name,
                "defective_commit": landing_sha,
                "fixing_commit": fixing_sha,
                "entry": str(case_dir),
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "corpus add", case_name)
    console.print(Text(f"  defective landing: {landing_sha[:10]} {landing_subject}"))
    console.print(Text(f"  fixing commit:     {fixing_sha[:10]} {_subject_of(root, fixing_sha)}"))
    console.print(Text(f"  entry: {case_dir}"))

    closing(
        console,
        f"the finding is a person's to write — open {case_dir / 'case.yaml'}; the replay "
        f"refuses this entry until the {FINDING_UNWRITTEN} placeholder is gone",
        STYLE_WARN,
    )

    raise typer.Exit(EXIT_OK)


# ....................... #


def _subject_of(root: Path, sha: str) -> str:
    return _git(root, "show", "-s", "--format=%s", sha) or ""


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

    from torve.adapters.broker import build_broker

    outcome = review_pull_request(
        root,
        config,
        runtime_for(config, None),
        agent,
        GhScm(config.scm.repo, config.scm.token_env),
        GitVcs(),
        number,
        token,
        broker=build_broker(config.broker),
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
