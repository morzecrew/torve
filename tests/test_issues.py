"""The intake leg (S-0119): a labelled issue becomes one drafting run.

What is asserted here is the decision the leg makes about an issue, not the
forge read itself — `GhScm.labelled_issues` is `tests/test_forge.py`'s. The
reader is fake, so the triage and edit rules are read as what they are: the
only two gates between a label somebody applied and a model reading the text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from torve.application.intake import IntakeOutcome
from torve.application.issues import issue_leg, issue_source_id
from torve.application.ports import AgentResult, LabelledIssue, SandboxHandle
from torve.config.runconfig import IntakeConfig, RunnerConfig

LABEL = "torve:ready"
INSTANT = "2026-11-01T10:00:00Z"


class FakeReader:
    """The forge as the leg reads it: a listing, and one permission answer per
    account. Both calls are recorded, because what the leg may *not* ask — a
    labeller it has already asked about, an issue it will not draft — is part
    of the decision."""

    def __init__(
        self,
        issues: list[LabelledIssue] | None = None,
        *,
        triage: dict[str, bool] | None = None,
        fails: str | None = None,
        triage_fails: str | None = None,
    ) -> None:
        self.issues = issues or []
        self.triage = triage or {}
        self.fails = fails
        self.triage_fails = triage_fails
        self.listed: list[str] = []
        self.asked: list[str] = []

    def labelled_issues(self, label: str) -> list[LabelledIssue]:
        self.listed.append(label)

        if self.fails:
            raise RuntimeError(self.fails)

        return list(self.issues)

    def may_triage(self, login: str) -> bool:
        self.asked.append(login)

        if self.triage_fails:
            raise RuntimeError(self.triage_fails)

        return self.triage.get(login, False)


class FakeDraft:
    """The drafting run, as the leg calls it: what was asked, and the one
    outcome every call answers with."""

    def __init__(self, outcome: IntakeOutcome | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.outcome = outcome or IntakeOutcome("T-0100", "1 draft(s) lint-green")

    def __call__(self, request: str, source: str) -> IntakeOutcome:
        self.calls.append((request, source))

        return self.outcome


class Unparseable:
    """A drafting agent whose every answer fails to parse, so the run spends
    its budget."""

    def run(self, ctx: Any) -> AgentResult:
        return AgentResult(exit_code=0, output="not json at all")


class Stub:
    """A runtime that hands out sandboxes and builds nothing."""

    def create(self, spec: Any, workspace: Any) -> SandboxHandle:
        return SandboxHandle(id="sbx-1", name=spec.name)

    def destroy(self, handle: SandboxHandle) -> None:
        pass

    def resolve_image(self, image: str) -> str:
        return "sha256:stub"


def issue(number: int = 142, **overrides: Any) -> LabelledIssue:
    fields: dict[str, Any] = {
        "number": number,
        "url": f"https://github.com/acme/lab/issues/{number}",
        "title": f"issue {number}",
        "body": f"body of {number}",
        "labelled_by": "alice",
        "labelled_at": INSTANT,
        "edited_at": None,
    }

    return LabelledIssue(**{**fields, **overrides})


def config(label: str = LABEL) -> RunnerConfig:
    return RunnerConfig(intake=IntakeConfig(issue_label=label))


def events(root: Path) -> list[dict[str, Any]]:
    path = root / ".torve" / "telemetry.jsonl"

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def source_path(root: Path, number: int = 142) -> Path:
    return root / ".torve" / "sources" / "operator" / f"issue-{number}.yaml"


# ....................... #
# D-1: one switch, and off means nothing is read


def test_the_leg_is_off_with_no_label_and_reads_nothing(tmp_path: Path):
    """S-0119/D-1: with `intake.issue_label` empty the leg returns off and
    reads nothing — no listing, no permission, no draft — so an adopter that
    sets nothing gets today's pass."""

    reader = FakeReader([issue()])
    draft = FakeDraft()

    detail, moved = issue_leg(tmp_path, RunnerConfig(), reader, draft)

    assert detail == "issue intake off"
    assert moved is False
    assert reader.listed == []
    assert reader.asked == []
    assert draft.calls == []
    assert not source_path(tmp_path).exists()


@pytest.mark.parametrize("surface", ["torve.cli.manager", "torve.cli.fleet"])
def test_the_pass_builds_no_planner_agent_while_the_leg_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, surface: str
):
    """S-0119/D-1: with `intake.issue_label` empty, neither the manager's nor
    the fleet's pass builds a drafting agent, so a planner tier this host
    cannot build never stops a pass with the leg off."""

    import asyncio
    import importlib

    from torve.cli import assembly

    calls: list[int] = []

    def explode(*_args: Any, **_kwargs: Any) -> None:
        calls.append(1)
        raise AssertionError("no planner agent is built with the leg off")

    monkeypatch.setattr(assembly, "build_tier_agent", explode)
    leg = importlib.import_module(surface)._issues_leg(tmp_path, RunnerConfig())

    assert asyncio.run(leg()) == ("issue intake off", False)
    # The detail line alone passes even if the agent was built eagerly and
    # the refusal swallowed, so what is asserted is the build itself.
    assert calls == []


# ....................... #
# D-8: one drafting run per pass, oldest labelling first


def test_the_leg_drafts_the_oldest_labelled_issue_once_per_pass(tmp_path: Path):
    """S-0119/D-8: a queue of labelled issues drains one per pass, and the one
    it drafts is the issue labelled earliest — not the issue number's order,
    and not the listing's."""

    newer = issue(7, labelled_at="2026-11-02T10:00:00Z")
    oldest = issue(9, labelled_at="2026-10-30T10:00:00Z")
    reader = FakeReader([newer, oldest], triage={"alice": True})
    draft = FakeDraft()

    detail, moved = issue_leg(tmp_path, config(), reader, draft)

    assert moved is True
    assert draft.calls == [("issue 9\n\nbody of 9", "operator/issue-9")]
    assert "#9" in detail
    assert "#7" not in detail
    assert reader.asked == ["alice"]


# ....................... #
# D-2: who labelled it, and whether it still says what they labelled


def test_the_leg_skips_a_labelled_issue_whose_labeller_cannot_triage(tmp_path: Path):
    """S-0119/D-2: an issue template that applies the label for its author
    puts text in front of no drafter — only triage or more lets it through,
    and the detail line says what would."""

    reader = FakeReader([issue(11, labelled_by="author")], triage={"author": False})
    draft = FakeDraft()

    detail, moved = issue_leg(tmp_path, config(), reader, draft)

    assert moved is False
    assert draft.calls == []
    assert "#11" in detail
    assert "author" in detail
    assert "no triage" in detail
    assert not source_path(tmp_path, 11).exists()


def test_the_leg_drafts_a_labelled_issue_only_when_a_triager_labelled_it(tmp_path: Path):
    """S-0119/D-2: the account behind the last labelling holds triage or more
    — write, maintain and admin answer through the reader the same way."""

    reader = FakeReader([issue(12, labelled_by="keeper")], triage={"keeper": True})
    draft = FakeDraft()

    _detail, moved = issue_leg(tmp_path, config(), reader, draft)

    assert moved is True
    assert draft.calls[0][1] == "operator/issue-12"
    assert source_path(tmp_path, 12).exists()


def test_the_leg_asks_may_triage_once_per_account_and_stops_at_the_first_that_passes(
    tmp_path: Path,
):
    """S-0119/D-2: the permission read costs one request per account per pass,
    and the leg stops asking at the first issue that passes — the ones behind
    it are not skipped because nobody looked at them."""

    same = [issue(1, labelled_by="alice"), issue(2, labelled_by="alice")]
    reader = FakeReader(same, triage={"alice": True})
    draft = FakeDraft()

    detail, moved = issue_leg(tmp_path, config(), reader, draft)

    assert moved is True
    assert reader.asked == ["alice"]
    assert draft.calls[0][1] == "operator/issue-1"

    # An account that cannot triage is asked once, and both its issues are
    # named as skips; the next account is asked only then.
    ordered = [
        issue(1, labelled_by="author", labelled_at="2026-10-30T10:00:00Z"),
        issue(2, labelled_by="author", labelled_at="2026-10-31T10:00:00Z"),
        issue(3, labelled_by="keeper", labelled_at="2026-11-02T10:00:00Z"),
    ]
    reader = FakeReader(ordered, triage={"author": False, "keeper": True})
    draft = FakeDraft()

    detail, moved = issue_leg(tmp_path, config(), reader, draft)

    assert moved is True
    assert reader.asked == ["author", "keeper"]
    assert draft.calls[0][1] == "operator/issue-3"
    assert "skipped 2" in detail


def test_the_leg_skips_an_issue_edited_after_a_labeller_labelled_it(tmp_path: Path):
    """S-0119/D-2: a maintainer who labelled, then an author who edited, is
    text no drafter reads — the skip says to label it again, and the reader
    already took the later of the body edit and the title rename."""

    edited = issue(21, edited_at="2026-11-01T11:00:00Z")
    renamed = issue(22, edited_at="2026-11-01T09:00:00Z")
    reader = FakeReader([edited, renamed], triage={"alice": True})
    draft = FakeDraft()

    detail, moved = issue_leg(tmp_path, config(), reader, draft)

    assert "#21" in detail
    assert "edited after it was labelled" in detail
    # Labelled at 10:00 and edited at 09:00 is the same text it was labelled
    # with, so it drafts.
    assert moved is True
    assert draft.calls[0][1] == "operator/issue-22"
    assert reader.asked == ["alice"]


# ....................... #
# The reader's own judgement


def test_the_leg_names_an_unreadable_issue_and_records_the_skip(tmp_path: Path):
    """S-0119/D-3 read by the leg: an issue the reader could not judge is a
    named skip and an `issue_skipped` event, never drafted and never asked
    about."""

    unreadable = issue(31, labelled_by=None, labelled_at=None, unreadable="outside the window")
    reader = FakeReader([unreadable], triage={"alice": True})
    draft = FakeDraft()

    detail, moved = issue_leg(tmp_path, config(), reader, draft)

    assert moved is False
    assert draft.calls == []
    assert reader.asked == []
    assert "#31" in detail
    assert "outside the window" in detail

    skipped = [row for row in events(tmp_path) if row.get("event") == "issue_skipped"]
    assert [(row["issue"], row["reason"]) for row in skipped] == [
        (31, "unreadable: outside the window")
    ]


def test_a_reader_error_drafts_nothing_and_the_leg_fails_closed(tmp_path: Path):
    """S-0119/the-intake-leg: a forge read that fails drafts nothing this
    pass, and the failure is the leg's answer rather than an exception the
    pass has to survive."""

    reader = FakeReader([issue()], fails="gh: no network")
    draft = FakeDraft()

    detail, moved = issue_leg(tmp_path, config(), reader, draft)

    assert moved is False
    assert draft.calls == []
    assert "gh: no network" in detail
    assert not source_path(tmp_path).exists()


def test_a_permission_read_that_fails_drafts_nothing(tmp_path: Path):
    """S-0119/D-2: the permission read is a forge read too, so it fails
    closed at the first issue it cannot judge."""

    reader = FakeReader([issue()], triage_fails="gh: rate limited")
    draft = FakeDraft()

    detail, moved = issue_leg(tmp_path, config(), reader, draft)

    assert moved is False
    assert draft.calls == []
    assert "gh: rate limited" in detail


# ....................... #
# D-5: once, and the file is how it is once


def test_the_leg_skips_a_labelled_issue_already_filed_as_a_source(tmp_path: Path):
    """S-0119/D-5: an issue drafts once. The source file the first run wrote is
    what a later pass finds, so the leg never asks about the account again."""

    source_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    source_path(tmp_path).write_text("id: operator/issue-142\n", encoding="utf-8")
    reader = FakeReader([issue()], triage={"alice": True})
    draft = FakeDraft()

    detail, moved = issue_leg(tmp_path, config(), reader, draft)

    assert moved is False
    assert draft.calls == []
    assert reader.asked == []
    assert "#142" in detail
    assert "already filed" in detail


def test_a_draft_that_spends_its_budget_is_not_retried_by_the_next_pass(tmp_path: Path):
    """S-0119/D-10: the leg starts the drafting run `torve intake` starts, so a
    run that spends its budget escalates there; the leg names the outcome and
    the next pass does not start another run for the same issue."""

    reader = FakeReader([issue()], triage={"alice": True})
    draft = FakeDraft(IntakeOutcome("T-0100", "budget exhausted after 3 attempt(s)"))

    detail, moved = issue_leg(tmp_path, config(), reader, draft)

    assert moved is True
    assert "budget exhausted" in detail

    detail, moved = issue_leg(tmp_path, config(), reader, draft)

    assert moved is False
    assert len(draft.calls) == 1
    assert "already filed" in detail


def test_a_leg_started_draft_that_spends_its_budget_escalates_and_holds_the_pause(repo):
    """S-0119/D-10: the leg's draft is the drafting run `torve intake` starts,
    so one that spends its budget escalates `budget_exhausted` in its own run
    state, and that escalation is one the pause counts, like any other."""

    from torve.application.fleet import escalated_tasks
    from torve.application.intake import draft_request
    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.domain.states import TaskState

    repo.seed()
    repo.git("checkout", "-q", "main")
    settings = config()
    agent = Unparseable()

    def draft(request: str, source: str) -> IntakeOutcome:
        return draft_request(repo.root, request, settings, agent, source=source, runtime=Stub())

    reader = FakeReader([issue()], triage={"alice": True})

    detail, moved = issue_leg(repo.root, settings, reader, draft)

    assert moved is True
    (drafted,) = [row for row in events(repo.root) if row.get("event") == "issue_drafted"]
    task_id = drafted["task"]
    state = RunState.load(naming.state_file(repo.root, task_id))
    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None
    assert state.escalation.reason == "budget_exhausted"
    assert task_id in escalated_tasks(repo.root)
    assert task_id in detail


def test_a_draft_that_raises_leaves_no_source_and_is_tried_again(tmp_path: Path):
    """A drafting run that never started drafts nothing, so its source file
    goes: kept, every later pass would skip the issue as already filed."""

    class Raising(FakeDraft):
        def __call__(self, request: str, source: str) -> IntakeOutcome:
            self.calls.append((request, source))
            raise RuntimeError("worktree add failed")

    reader = FakeReader([issue()], triage={"alice": True})
    draft = Raising()

    detail, moved = issue_leg(tmp_path, config(), reader, draft)

    assert moved is False
    assert "could not draft operator/issue-142: worktree add failed" in detail
    assert not source_path(tmp_path).exists()

    issue_leg(tmp_path, config(), reader, draft)

    assert len(draft.calls) == 2


def test_the_leg_writes_the_source_file_the_issue_drafts_from(tmp_path: Path):
    """S-0119/D-5: the filed source carries the URL as `ref`, the title, the
    labelling instant as `at` and the body as `summary`, and opens with its
    schema line like every source `torve init` accepts (S-0060/D-1)."""

    body = "One line.\n\nAnother paragraph.\n"
    reader = FakeReader(
        [issue(142, title="Draft from an issue", body=body)], triage={"alice": True}
    )
    draft = FakeDraft()

    _detail, moved = issue_leg(tmp_path, config(), reader, draft)

    assert moved is True
    text = source_path(tmp_path).read_text(encoding="utf-8")
    assert text.startswith("# yaml-language-server: $schema=../../schemas/sources.json\n")

    filed = yaml.safe_load(text)
    assert filed == {
        "id": "operator/issue-142",
        "kind": "operator",
        "ref": "https://github.com/acme/lab/issues/142",
        "title": "Draft from an issue",
        "at": INSTANT,
        "summary": body,
    }
    # The body reads as prose in the file rather than as escaped lines.
    assert "summary: |" in text


def test_a_source_file_that_cannot_be_written_drafts_nothing(tmp_path: Path):
    """S-0119/the-intake-leg: a source file that cannot be written drafts
    nothing, so the run that would have had no retry guard is never started."""

    (tmp_path / ".torve").mkdir()
    (tmp_path / ".torve" / "sources").write_text("not a directory\n", encoding="utf-8")
    reader = FakeReader([issue()], triage={"alice": True})
    draft = FakeDraft()

    detail, moved = issue_leg(tmp_path, config(), reader, draft)

    assert moved is False
    assert draft.calls == []
    assert "operator/issue-142" in detail


def test_a_source_the_model_refuses_drafts_nothing(tmp_path: Path):
    """S-0119/the-intake-leg: the source is validated before the run, so a
    labelling instant the tree cannot file drafts nothing either."""

    malformed = issue(143, labelled_at="yesterday")
    reader = FakeReader([malformed], triage={"alice": True})
    draft = FakeDraft()

    detail, moved = issue_leg(tmp_path, config(), reader, draft)

    assert moved is False
    assert draft.calls == []
    assert "operator/issue-143" in detail


# ....................... #
# D-4 and D-9: what the drafter gets, and what the record says


def test_the_leg_hands_the_drafter_the_title_a_blank_line_and_the_body(tmp_path: Path):
    """S-0119/D-4: the request is the title, a blank line and the body — never
    the comments or any other field of the thread."""

    reader = FakeReader([issue(5, title="A title", body="A body.")], triage={"alice": True})
    draft = FakeDraft()

    issue_leg(tmp_path, config(), reader, draft)

    assert draft.calls == [("A title\n\nA body.", "operator/issue-5")]
    assert issue_source_id(5) == "operator/issue-5"


def test_the_leg_records_the_drafted_issue_as_an_engine_event(tmp_path: Path):
    """S-0119/D-9: the detail line names the issue it drafted and the ledger
    gets an `issue_drafted` event; nothing decides on either."""

    draft = FakeDraft(IntakeOutcome("T-0107", "2 draft(s) lint-green"))
    reader = FakeReader([issue()], triage={"alice": True})

    detail, moved = issue_leg(tmp_path, config(), reader, draft)

    assert moved is True
    assert "#142" in detail
    assert "operator/issue-142" in detail
    assert "T-0107" in detail

    drafted = [row for row in events(tmp_path) if row.get("event") == "issue_drafted"]
    assert len(drafted) == 1
    assert drafted[0]["issue"] == 142
    assert drafted[0]["url"] == "https://github.com/acme/lab/issues/142"
    assert drafted[0]["source"] == "operator/issue-142"
    assert drafted[0]["task"] == "T-0107"
    assert drafted[0]["kind"] == "engine"
