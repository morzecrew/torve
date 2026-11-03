"""The intake leg (S-0119/the-intake-leg): a labelled issue becomes drafts.

A pass reads the open issues carrying `intake.issue_label` and, for the one a
maintainer labelled and nobody edited since, files a source and starts the
same drafting run `torve intake` starts. Nothing dispatches: the drafts wait
for a person's `torve adopt`, as they always have (S-0020/D-1), and this leg
never writes to the forge (S-0119/D-9).

Two things the leg refuses to do. It never drafts from text a maintainer did
not put in front of it — the account behind the last labelling must hold
triage or more, and the title and body must not have moved since that event
(S-0119/D-2). And it never drafts an issue twice — the source file it writes
before the run is what a later pass finds (S-0119/D-5), so a failed or
escalated draft is retried by a person, never by the loop.

Everything here fails closed toward not creating work, as the standing leg
does: a forge read that raises and a source file that cannot be written both
draft nothing and say why in the detail line.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from torve.application.intake import DraftNotStarted
from torve.application.telemetry import engine_event
from torve.config.sources import schema_header, source_file
from torve.domain.source import Source

if TYPE_CHECKING:
    from collections.abc import Callable

    from torve.application.intake import IntakeOutcome
    from torve.application.ports import IssueReader, LabelledIssue
    from torve.config.runconfig import RunnerConfig
    from torve.domain.vocabulary import SourceKind

# ----------------------- #

# What a filed issue is filed as: an ordinary operator source, `issue-<number>`
# its slug (S-0060/D-1). The number is stable, so a relabelled issue drafts
# again into the same file, which is how D-5 refuses it.
SOURCE_KIND: SourceKind = "operator"


def issue_source_id(number: int) -> str:
    """The source identifier one issue files as."""

    return f"{SOURCE_KIND}/issue-{number}"


class _SourceDumper(yaml.SafeDumper):
    """Multiline strings as literal blocks, as a contract's dump does
    (S-0007/A-1): an issue's body is prose, and the default style writes
    every newline as a blank-line escape."""


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)  # pyright: ignore[reportUnknownMemberType]


_SourceDumper.add_representer(str, _str_representer)


def _source_text(issue: LabelledIssue) -> str:
    """One issue filed as an ordinary source (S-0060/D-1): the URL as `ref`,
    the labelling instant as `at`, the body as `summary` (S-0119/D-5)."""

    source = Source(
        id=issue_source_id(issue.number),
        kind=SOURCE_KIND,
        ref=issue.url,
        title=issue.title,
        at=issue.labelled_at or "",
        summary=issue.body,
    )
    body = yaml.dump(
        source.model_dump(mode="json", exclude_defaults=True),
        Dumper=_SourceDumper,
        sort_keys=False,
        allow_unicode=True,
        width=88,
    )

    return f"{schema_header()}\n{body}"


# ....................... #


def _skip(root: Path, skipped: list[str], issue: LabelledIssue, detail: str, reason: str) -> None:
    """Name one issue the pass will not draft, in the detail line and in the
    engine's own stream (S-0119/D-9): nothing decides on either."""

    skipped.append(f"#{issue.number} ({detail})")
    engine_event(
        root,
        "issue_skipped",
        {"issue": issue.number, "url": issue.url, "reason": reason},
    )


def _detail(parts: list[str], skipped: list[str]) -> str:
    """The leg's one line: what it did and each issue it left, with what
    would let that issue through."""

    if skipped:
        parts.append(f"skipped {len(skipped)}: {', '.join(skipped)}")

    return "; ".join(parts) if parts else "no labelled issue to draft"


def _edited_after_labelling(issue: LabelledIssue) -> bool:
    """Whether the title or the body moved after the last labelling
    (S-0119/D-2). The reader already took the later of the body's last edit
    and the last rename; both instants are the seconds form, so they sort as
    strings (S-0058/D-7)."""

    return bool(issue.edited_at and issue.labelled_at and issue.edited_at > issue.labelled_at)


# ....................... #


def issue_leg(
    root: Path,
    config: RunnerConfig,
    reader: IssueReader,
    draft: Callable[[str, str], IntakeOutcome],
) -> tuple[str, bool]:
    """One pass of the intake leg (S-0119/D-8), returning what it did and
    whether it started a drafting run.

    With `intake.issue_label` empty the leg is off: it reads nothing, so a
    planner tier that cannot be built never stops a pass with the leg off
    (S-0119/D-1). Otherwise it lists the labelled issues and takes them
    oldest labelling first, skipping the unreadable, the already filed and
    the edited, then asking whether the labeller may triage — once per
    account, and only until one issue passes (S-0119/D-2). It drafts that one
    and stops: a queue of labelled issues drains one per pass (S-0119/D-8).

    `draft` is `(request, source id) -> outcome`, the same `draft_request`
    `torve intake` calls, so a request drafts the same way from the pass as
    from the shell (S-0119/D-7). It is called after the source file is
    written, so a run that fails or escalates — `budget_exhausted` included
    (S-0119/D-10) — is not retried on the next pass (S-0119/D-5)."""

    label = config.intake.issue_label

    if not label:
        return "issue intake off", False

    try:
        listed = reader.labelled_issues(label)

    except Exception as exc:
        # Fails closed toward not creating work: nothing is drafted from a
        # listing the forge could not answer.
        return f"issue intake: the forge read failed: {exc}", False

    skipped: list[str] = []
    candidates: list[LabelledIssue] = []

    for issue in listed:
        if issue.unreadable:
            _skip(
                root,
                skipped,
                issue,
                f"unreadable: {issue.unreadable}",
                f"unreadable: {issue.unreadable}",
            )
            continue

        identifier = issue_source_id(issue.number)

        if source_file(root, identifier).exists():
            _skip(
                root,
                skipped,
                issue,
                f"already filed as {identifier}",
                "already filed as a source",
            )
            continue

        if _edited_after_labelling(issue):
            _skip(
                root,
                skipped,
                issue,
                "title or body edited after it was labelled — label it again",
                "edited after labelling",
            )
            continue

        candidates.append(issue)

    candidates.sort(key=lambda issue: issue.labelled_at or "")

    asked: dict[str, bool] = {}

    for issue in candidates:
        login = issue.labelled_by or ""

        if login not in asked:
            try:
                asked[login] = reader.may_triage(login)

            except Exception as exc:
                # A permission read is a forge read too, and it fails closed
                # the same way (S-0119/D-2).
                return _detail(
                    [f"could not read {login or 'the labeller'}'s permission: {exc}"], skipped
                ), False

        if not asked[login]:
            _skip(
                root,
                skipped,
                issue,
                f"labelled by {login or 'nobody'}, who holds no triage — "
                "label it from an account that does",
                "labeller cannot triage",
            )
            continue

        return _draft(root, issue, draft, skipped)

    return _detail([], skipped), False


# ....................... #


def _draft(
    root: Path,
    issue: LabelledIssue,
    draft: Callable[[str, str], IntakeOutcome],
    skipped: list[str],
) -> tuple[str, bool]:
    """File the issue as a source and start one drafting run (S-0119/D-5).

    The source is written first and the run second, so the file that refuses
    a retry exists before the run that might need retrying. The request is
    the title, a blank line and the body (S-0119/D-4) — comments and every
    other field stay on the thread."""

    identifier = issue_source_id(issue.number)
    path = source_file(root, identifier)

    created = False

    try:
        text = _source_text(issue)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Created exclusively: a pass that overlaps this one finds it filed.
        with path.open("x", encoding="utf-8") as handle:
            created = True
            handle.write(text)

    except FileExistsError:
        return _detail([f"#{issue.number} already filed as {identifier}"], skipped), False

    except (OSError, ValueError) as exc:  # a failed write, or a labelling S-0058/D-7 refuses
        if created:
            # A partial file would mark the issue filed with nothing drafted.
            path.unlink(missing_ok=True)

        return _detail([f"could not file {identifier}: {exc}"], skipped), False

    try:
        outcome = draft(f"{issue.title}\n\n{issue.body}", identifier)

    except (ValueError, DraftNotStarted) as exc:
        # Nothing was minted, so nothing drafts the issue: the source goes,
        # or every later pass would skip the issue as already filed.
        path.unlink(missing_ok=True)
        return _detail([f"could not draft {identifier}: {exc}"], skipped), False

    except Exception as exc:
        # The run was minted and failed: its task is the issue's record, so
        # the source stays and later passes skip the issue, as for a spent run.
        return _detail([f"drafting {identifier} failed: {exc}"], skipped), False

    engine_event(
        root,
        "issue_drafted",
        {
            "issue": issue.number,
            "url": issue.url,
            "source": identifier,
            "task": outcome.task_id,
            "fact": outcome.fact,
        },
    )

    return (
        _detail(
            [f"drafted #{issue.number} as {identifier} ({outcome.task_id}: {outcome.fact})"],
            skipped,
        ),
        True,
    )
