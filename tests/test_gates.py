from __future__ import annotations

from pathlib import Path

import pytest
from conftest import context_for

from torve.config.manifest import Gate
from torve.gates.log_entry import check_entry, parse_log
from torve.gates.runner import run_gates
from torve.gates.sabotage import (
    BASE_MANIFEST,
    TASK_ID,
    base_task,
    entry,
    log_document,
)
from torve.gates.scope import check_scope
from torve.gates.secrets import check_secrets

GATE = Gate(name="test", run="@scope", state="blocking", origin="structural")  # any handle


def test_a_contract_may_name_both_its_document_and_its_source(tmp_path):
    """S-0060/D-3: `spec` says whose rows it inherits, `source` says what
    asked; a contract may carry both, either or neither, and a source the
    grammar refuses does not load."""

    import yaml
    from pydantic import ValidationError

    from torve.gates.context import load_task

    def contract(**extra) -> dict:
        return {"schema_version": 2, "id": "T-0001", "decisions": [], **extra}

    path = tmp_path / "contract.yaml"

    for record in (
        contract(spec="S-0060", source="audit/soc2-2026"),
        contract(source="audit/soc2-2026"),
        contract(spec="S-0060"),
        contract(),
    ):
        path.write_text(yaml.safe_dump(record), encoding="utf-8")
        loaded = load_task(path)

        assert loaded.source == record.get("source")
        assert loaded.spec == record.get("spec")

    path.write_text(yaml.safe_dump(contract(source="specification/s-0060")), encoding="utf-8")

    with pytest.raises((ValueError, ValidationError)):
        load_task(path)


def test_scope_implicitly_allows_task_and_log_files(repo):
    repo.seed()
    repo.task(base_task(allow=["src/**"]), log_document())
    repo.write("src/app.py", "print('in scope')\n")
    repo.commit("task branch")
    result = check_scope(GATE, context_for(repo))
    assert result.outcome == "pass", result.output


def test_scope_untracked_file_is_visible(repo):
    """A stray new file that was never committed is still named, as evidence
    (S-0122/D-12)."""
    repo.seed()
    repo.task(base_task(allow=["src/**"]), log_document())
    repo.commit("task branch")
    (repo.root / "stray.txt").write_text("uncommitted\n", encoding="utf-8")
    result = check_scope(GATE, context_for(repo))
    assert "outside files and tests, as evidence: stray.txt" in result.output


def test_only_the_fence_convicts(repo):
    """S-0122/D-12: an edit outside the task's files and tests is evidence,
    named on a passing gate; an edit inside the fence is the one conviction."""
    repo.seed()
    task = base_task(allow=["src/new.py"])
    task["scope"]["deny"] = ["vendor/**"]
    repo.task(task, log_document())
    repo.write("tests/test_app.py", "def test_app():\n        assert True\n")
    repo.commit("a reach beyond the files")

    reached = check_scope(GATE, context_for(repo))

    assert reached.outcome == "pass"
    assert "outside files and tests, as evidence: tests/test_app.py" in reached.output

    repo.write("vendor/lib.py", "x = 1\n")
    repo.commit("an edit inside the fence")

    fenced = check_scope(GATE, context_for(repo))

    assert fenced.outcome == "fail"
    assert "fenced path: vendor/lib.py" in fenced.output


def test_the_patch_carries_an_untracked_file(repo):
    """The reviewer reads the gate pass's patch, so a file the attempt created
    and never staged has to appear there as an added file — `git diff` alone
    leaves it out, and a review then judges the module absent."""
    repo.seed()
    repo.task(base_task(allow=["src/**"]), log_document())
    repo.commit("task branch")
    (repo.root / "src" / "fresh.py").write_text("VALUE = 1\n", encoding="utf-8")
    patch = context_for(repo).patch
    assert "diff --git a/src/fresh.py b/src/fresh.py" in patch
    assert "new file mode" in patch
    assert "+VALUE = 1" in patch


def test_secrets_reports_file_and_line(repo):
    repo.seed()
    repo.write("src/config.py", "# comment\nkey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "'\n")
    repo.commit("leak")
    result = check_secrets(GATE, context_for(repo))
    assert result.outcome == "fail"
    assert "src/config.py:2: aws access key id" in result.output


def test_secrets_allow_patterns_suppress_reviewed_false_positives(repo):
    manifest = {
        "schema_version": 1,
        "secrets": {"allow_patterns": ["EXAMPLE'"]},
        "gates": [
            {"name": "secrets", "run": "@secrets", "state": "blocking", "origin": "structural"}
        ],
    }
    repo.seed(manifest)
    repo.write("src/config.py", "key = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "'\n")
    repo.commit("documented example key")
    result = check_secrets(GATE, context_for(repo))
    assert result.outcome == "pass", result.output


def test_secrets_scans_untracked_files(repo):
    repo.seed()
    (repo.root / "notes.txt").write_text("token " + "AKIA" + "IOSFODNN7EXAMPLE" + "\n", "utf-8")
    result = check_secrets(GATE, context_for(repo))
    assert result.outcome == "fail"
    assert "notes.txt:1" in result.output


def test_decisions_skill_style_entry_is_accepted(repo):
    """flag-dont-flip logs use `class` instead of `kind`; both vocabularies
    pass, per the S-0001/D-26 reconciliation. An unlisted departure owes
    the row it puts back (S-0122/D-14)."""
    skill_entry = entry(
        decision="unlisted",
        grade="UNLISTED",
        kind=None,  # class-only, skill style
        claim="spec silent on retry budget",
        action="departed",
    )
    skill_entry["class"] = "spec-gap"
    repo.seed()
    problems = check_entry(skill_entry, repo.root)
    assert any("owes a proposal" in problem for problem in problems)  # unlisted

    with_proposal = dict(skill_entry, proposal="ASSUMED — retries capped at 3")
    assert check_entry(with_proposal, repo.root) == []


@pytest.mark.parametrize(
    ("evidence", "defect"),
    [
        ("src/app.py:1 the guard", "separator missing after the citation"),
        (
            "src/app.py:1; src/app.py:2 — the guard",
            "multiple semicolon-joined citations where prose belongs",
        ),
        ("no redis service in this deployment", "no citation at all"),
    ],
)
def test_decisions_evidence_rejection_teaches_the_repair(repo, evidence, defect):
    """T-0203: the grammar rejection quotes the offending line, prints the
    expected grammar, and diagnoses the first defect — the judgement itself
    is unchanged (still a rejection)."""
    repo.seed()
    output = "\n".join(check_entry(_halt(evidence=evidence), repo.root))
    assert f"evidence {evidence!r} is not locatable" in output
    assert "expected: a single leading `path:line — one sentence` citation" in output
    assert defect in output


def test_decisions_evidence_path_rejection_keeps_the_locators_message(repo):
    """A locatable-format line pointing nowhere is not a grammar rejection —
    it keeps the locator's message; only the grammar rejection teaches."""
    repo.seed()
    output = "\n".join(check_entry(_halt(evidence="ghost.py:1"), repo.root))
    assert "does not exist" in output
    assert "expected: a single leading" not in output


def test_decisions_evidence_with_separator_still_passes(repo):
    repo.seed()
    assert check_entry(_halt(evidence="src/app.py:1 — the guard"), repo.root) == []


def _halt(**overrides):
    """A halt on the LOCKED row, the entry a new log holds (S-0122/D-14)."""

    return entry(kind="contradicted", action="halted", **overrides)


def test_a_later_blocking_gate_still_reports_after_an_earlier_one_fails(repo):
    """T-0234: the runner short-circuited every blocking gate after the
    first blocking failure, and it orders them cheapest-timeout-first — so a
    form gate failing in under a second hid the functional verdict behind
    it. `retry_rung_for` then saw one axis where the ladder assumes several,
    and S-0034/D-7's boundary masking could never fire from under a lighter
    gate. Nothing new blocks: the exit code was already 1."""

    from torve.gates.runner import run_gates

    manifest = {
        "schema_version": 1,
        "scope": {"allow": [], "deny": []},
        "gates": [
            # Ordered cheapest-first by the runner, so `cheap` runs first.
            {
                "name": "cheap",
                "run": "sh -c 'exit 1'",
                "state": "blocking",
                "timeout": 1,
                "origin": "structural",
            },
            {
                "name": "dear",
                "run": "sh -c 'exit 1'",
                "state": "blocking",
                "timeout": 900,
                "origin": "structural",
            },
        ],
    }
    repo.seed(manifest=manifest)
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")

    report = run_gates(context_for(repo))
    outcomes = {r.name: r.outcome for r in report.results}

    # Both convictions are on the record, not just the cheap one.
    assert outcomes == {"cheap": "fail", "dear": "fail"}
    assert report.exit_code == 1
    assert not any("an earlier blocking gate failed" in (r.output or "") for r in report.results)


def test_a_gate_that_judges_nothing_the_attempt_changed_is_skipped(repo):
    """S-0071/D-6: `coverage-delta` measures `--cov=src` and spent 11,786 of its
    43,448 seconds on attempts that changed nothing under `src/`, seventeen of
    them going red over drift the attempt had not caused.

    Skipped, never passed: a gate that reports a pass it did not compute is the
    same fault as a cache whose key cannot move."""

    from torve.gates.runner import run_gates

    manifest = {
        "schema_version": 1,
        "scope": {"allow": [], "deny": []},
        "gates": [
            {
                "name": "judges-src",
                "run": "sh -c 'exit 1'",
                "state": "blocking",
                "timeout": 1,
                "origin": "structural",
                "paths": ["src/**"],
            },
            {
                "name": "judges-everything",
                "run": "sh -c 'exit 0'",
                "state": "blocking",
                "timeout": 2,
                "origin": "structural",
            },
        ],
    }
    repo.seed(manifest=manifest)
    repo.write("pages/docs/thing.md", "prose\n")
    repo.commit("nothing under src")

    outcomes = {r.name: r.outcome for r in run_gates(context_for(repo)).results}

    # It would have failed had it run, so a pass here would be a lie and a
    # green exit code would be one the battery had not earned.
    assert outcomes == {"judges-src": "skipped", "judges-everything": "pass"}
    assert run_gates(context_for(repo)).exit_code == 0

    # The same gate against a diff it does judge: it runs, and it convicts.
    repo.write("src/app.py", "print('x')\n")
    repo.commit("under src")
    report = run_gates(context_for(repo))

    assert {r.name: r.outcome for r in report.results}["judges-src"] == "fail"
    assert report.exit_code == 1


def test_repository_logs_parse_under_the_gate():
    # Every execution log this repository carries must satisfy its own gate's
    # parser (A-1 format; per-task directories per A-12).
    #
    # `.torve/tasks/` is gitignored, so a clean checkout carries none and this
    # has nothing to judge — it skips rather than failing. Asserting they exist
    # made the acceptance command of every task pass on a machine that happens
    # to hold leftover task directories and fail in the sandbox that does not,
    # which is a green that means nothing.
    root = Path(__file__).resolve().parent.parent
    logs = sorted((root / ".torve" / "tasks").glob("*/log.yaml"))

    if not logs:
        pytest.skip("no execution logs in this checkout — `.torve/tasks/` is not committed")

    for log in logs:
        document, error = parse_log(log.read_text(encoding="utf-8"))
        assert error is None, f"{log.name}: {error}"
        assert isinstance(document.get("drift_count"), int), f"{log.name}: no drift_count"
        assert document["entries"], f"{log.name}: an empty log would simply not exist (A-13)"


def test_run_gates_reports_progress_by_gate_name(repo):
    # S-0018/live-status-for-long-waits via T-0029: the live status names the gate it is inside —
    # one timer over a pass that is 95% acceptance explains nothing.
    from torve.gates.runner import run_gates

    repo.seed()
    repo.write("src/app.py", "print('progress')\n")
    repo.commit("change")
    seen: list[str] = []
    report = run_gates(context_for(repo), progress=seen.append)
    # Every gate runs now (T-0234), so every result announced itself.
    assert seen == [r.name for r in report.results]
    # (a degraded-mode gate runs and reports skipped — it still announces)
    assert "scope" in seen and "secrets" in seen


# ....................... #
# The {base} substitution: a shell command receives the battery's own
# computed base — the same context every builtin judges against — and never
# resolves a ref in shell itself.

ECHO_BASE_COMMAND = 'printf "%s" "{base}"'


def _single_gate_manifest(run: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "gates": [{"name": "g", "run": run, "state": "blocking", "origin": "S-0036"}],
    }


def test_a_shell_gate_receives_the_batterys_computed_base(repo):
    from torve.gates.runner import run_gates

    repo.seed(_single_gate_manifest(ECHO_BASE_COMMAND))
    repo.write("src/app.py", "print('substituted')\n")
    repo.commit("change")
    ctx = context_for(repo)
    report = run_gates(ctx, only={"g"})
    result = report.results[0]
    assert result.outcome == "pass", result.output
    # The SHA of the merge-base — the value every diff-input builtin judges
    # against — not the ref name it was resolved from.
    assert result.output.strip() == ctx.merge_base
    assert ctx.merge_base != ctx.base


def test_inherited_failures_pass_through_to_the_gate_result(repo, monkeypatch):
    """S-0118/D-4: a builtin's inherited ids are a fact about a green gate, so
    they ride the GateResult beside the quarantined commands rather than only
    the output a passing result drops."""

    from torve.gates import runner as gate_runner
    from torve.gates.contract import BuiltinOutcome

    def suite(gate, ctx):
        return BuiltinOutcome("pass", inherited_failures=["tests/test_a.py::test_b"])

    monkeypatch.setitem(gate_runner.BUILTINS, "suite", suite)
    repo.seed(_single_gate_manifest("@suite"))
    report = run_gates(context_for(repo), only={"g"})

    assert report.results[0].outcome == "pass"
    assert report.results[0].inherited_failures == ["tests/test_a.py::test_b"]


def test_the_flaky_record_keeps_the_declared_command(repo):
    # An embedded SHA would make each run's flaky identity unique and
    # unquarantinable; the record is the declared command, placeholder intact.
    from torve.gates.runner import run_gates

    flaky = '[ -e .seen ] || { touch .seen; printf "%s" "{base}" > .base-leak; exit 1; }'
    repo.seed(_single_gate_manifest(flaky))
    report = run_gates(context_for(repo), only={"g"})
    result = report.results[0]
    assert result.outcome == "flaky", result.output
    assert result.flaky_commands == [flaky]
    assert report.flaky_count_by_command == {flaky: 1}


def test_a_base_requesting_gate_on_an_unresolvable_base_errors(tmp_path):
    # A fresh repository has no computed base; `{base}` has nothing honest to
    # stand for, and the gate errors instead of inventing a ref.
    from torve.config.manifest import Manifest
    from torve.gates.context import GateContext
    from torve.gates.runner import run_gates

    gate = Gate(name="g", run=ECHO_BASE_COMMAND, state="blocking", origin="S-0036")
    ctx = GateContext(
        root=tmp_path,
        manifest=Manifest(gates=[gate]),
        head_sha="0" * 40,
        base=None,
        merge_base=None,
    )
    report = run_gates(ctx, only={"g"})
    assert report.results[0].outcome == "error"
    assert "no base is resolvable" in report.results[0].output


def test_a_battery_budget_bounds_every_command_and_its_rerun(tmp_path):
    # What a lease waits out while a pass records nothing: each gate's own
    # timeout (600 s for a shell gate with none), twice for the re-run a failed
    # command gets, and acceptance's once per command the contract gives it. A
    # gate whose paths the attempt did not touch never runs, so it adds nothing.
    from torve.config.manifest import Manifest
    from torve.domain.task import Scope, Task
    from torve.gates.context import GateContext
    from torve.gates.runner import battery, battery_budget

    gates = [
        Gate(name="lint", run="true", state="shadow", origin="S-0036", timeout=120),
        Gate(name="e2e", run="true", state="blocking", origin="S-0036"),
        Gate(
            name="acceptance",
            run="@task.acceptance",
            state="blocking",
            origin="S-0036",
            timeout=300,
        ),
        Gate(
            name="docs",
            run="true",
            state="shadow",
            origin="S-0036",
            timeout=900,
            paths=["docs/**"],
        ),
    ]
    task = Task(id="T-0001", decisions=[], acceptance=["one", "two"], scope=Scope(allow=["src/**"]))
    ctx = GateContext(
        root=tmp_path,
        manifest=Manifest(gates=gates),
        head_sha="0" * 40,
        base=None,
        merge_base=None,
        task=task,
    )

    assert [g.name for g in battery(ctx)] == ["lint", "acceptance", "e2e", "docs"]
    assert battery_budget(battery(ctx), ctx) == 2 * (120 + 2 * 300 + 600)


def test_a_suite_budget_counts_its_base_and_never_doubles(tmp_path):
    """S-0118/D-1: the suite and its reports, then on a red the base
    extraction, the rerun and the base run, each bounded by the gate's timeout.
    None is retried, so a lease does not wait out a second pass of each."""

    from torve.config.manifest import Manifest
    from torve.gates.context import GateContext
    from torve.gates.runner import battery, battery_budget

    suite = Gate(
        name="suite",
        run="@suite",
        state="blocking",
        origin="S-0118",
        timeout=100,
        commands=["pytest -q", "diff-cover coverage.xml"],
    )
    ctx = GateContext(
        root=tmp_path,
        manifest=Manifest(gates=[suite]),
        head_sha="0" * 40,
        base=None,
        merge_base=None,
    )

    assert battery_budget(battery(ctx), ctx) == (2 + 3) * 100


def test_the_shipped_suite_gate_runs_the_suite_and_reports_nothing():
    # The manifest is configuration the battery reads; its shape is checked
    # here so a later edit cannot silently widen the judgment surface.
    # S-0118/D-5 made the suite blocking with no `paths`; S-0122/D-4 dropped
    # the coverage report that rode it, the dearest step that convicted nothing.
    from torve.config.manifest import load_manifest

    root = Path(__file__).resolve().parent.parent
    manifest = load_manifest(root / ".torve" / "gates.yaml")
    gate = {g.name: g for g in manifest.resolved_gates()}["suite"]

    assert (gate.state, gate.paths, gate.timeout) == ("blocking", [], 600)
    assert gate.commands == ["uv run pytest -q"]
    assert manifest.tests.run.startswith("uv run pytest")


# ----------------------- #
# S-0054 phase 1: a decision with a check is a gate (S-0054/D-2, S-0054/D-4).


def _checked(check: str, state: str = "shadow", twin: str | None = None) -> list[dict]:
    return [
        {
            "id": "S-0009/D-1",
            "grade": "LOCKED",
            "text": "the check decides",
            "paths": ["src/**"],
            "check": check,
            "check_state": state,
            "check_twin": twin,
        }
    ]


def completing(repo):
    """The context the battery completing the contract's document reads: its
    rows handed in, as the lane hands the document's (S-0122/D-4)."""

    ctx = context_for(repo)
    assert ctx.task is not None
    ctx.inherited = list(ctx.task.decisions)

    return ctx


def test_a_failing_shadow_check_is_recorded_and_blocks_nothing(repo):
    """D-1: the check convicts nothing under its shadow state; its red verdict
    is the finding, recorded under the row's own state."""
    from torve.gates.runner import run_gates

    repo.seed()
    repo.task(base_task(allow=["src/**"], decisions=_checked("sh -c 'exit 1'")), log_document())
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")

    report = run_gates(completing(repo))
    by_name = {r.name: r for r in report.results}

    assert by_name["decision:S-0009/D-1"].outcome == "fail"
    assert by_name["decision:S-0009/D-1"].state == "shadow"
    assert report.exit_code == 0


def test_a_check_the_diff_does_not_reach_is_skipped(repo):
    """#241: a document's phases all carry its rows, so a row a later phase
    builds has a check that fails in every earlier one. Its paths are the
    gate's: a diff that does not reach them skips the check."""
    from torve.gates.runner import run_gates

    repo.seed()
    later = [{**_checked("sh -c 'exit 1'")[0], "paths": ["lib/issues.py"]}]
    repo.task(base_task(allow=["src/**"], decisions=later), log_document())
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")

    report = run_gates(completing(repo))
    by_name = {r.name: r for r in report.results}

    assert by_name["decision:S-0009/D-1"].outcome == "skipped"
    assert report.exit_code == 0


def test_a_decision_gate_judges_its_rows_paths_and_its_checks_files():
    """A row's gate also runs when the diff edits the test its check names,
    since that edit changes the check. A row with no paths runs on every diff."""
    from torve.domain.task import InheritedDecision
    from torve.gates.runner import row_gates

    scoped = InheritedDecision(
        id="S-0009/D-1",
        grade="LOCKED",
        text="x",
        paths=["lib/issues.py"],
        check="uv run pytest tests/test_issues.py::test_triage -k triage -p no:cacheprovider",
    )
    everywhere = InheritedDecision(
        id="S-0009/D-2", grade="ASSUMED", text="y", check="uv run pytest tests/test_x.py"
    )

    first, second = row_gates([scoped, everywhere])

    assert first.paths[0] == "lib/issues.py"
    assert "tests/test_issues.py" in first.paths
    assert second.paths == []


@pytest.mark.parametrize(
    ("check", "named", "not_named"),
    [
        ("pytest tests/a.py # tests/b.py", "tests/a.py", "tests/b.py"),
        ("pytest 'tests/test x.py'", "tests/test x.py", "tests/test"),
        ("sh -c 'cd app && pytest tests/test_issue.py'", "app/tests/test_issue.py", None),
        ("uv run pytest tests/test_issue.py;", "tests/test_issue.py", "tests/test_issue.py;"),
        ("pytest tests", "/tests", "tests"),
        ("sh -c 'uv run pytest \"tests/test x.py\" test_root.py'", "tests/test x.py", None),
    ],
)
def test_a_checks_files_are_read_as_the_shell_reads_them(check, named, not_named):
    """An edit to a file the check reads runs the check, so the gate's paths
    take every operand the command names, read as a shell would."""
    from torve.gates.runner import _check_files

    files = _check_files(check)

    assert named in files
    assert not_named is None or not_named not in files


def test_a_rename_out_of_a_gates_paths_reaches_the_gate(tmp_path):
    """A file moved out of what a gate judges is a change to it: the old path
    counts as well as the new one."""
    from torve.config.manifest import Gate, Manifest
    from torve.gates.context import DiffEntry, GateContext
    from torve.gates.runner import _judges_anything

    gate = Gate(
        name="decision:S-0009/D-1",
        run="true",
        state="shadow",
        origin="S-0009/D-1",
        paths=["lib/**"],
    )

    def context(*diff: DiffEntry) -> GateContext:
        return GateContext(
            root=tmp_path,
            manifest=Manifest(gates=[]),
            head_sha="",
            base=None,
            merge_base=None,
            diff=list(diff),
        )

    moved = DiffEntry(status="R", path="src/issues.py", old_path="lib/issues.py")
    elsewhere = DiffEntry(status="M", path="src/app.py")

    assert _judges_anything(gate, context(moved))
    assert not _judges_anything(gate, context(elsewhere))


def test_a_passing_check_covers_its_row(repo):
    """D-1: a check that runs and passes stands for its row."""
    from torve.gates.runner import run_gates

    repo.seed()
    repo.task(base_task(allow=["src/**"], decisions=_checked("true")), log_document())
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")

    report = run_gates(completing(repo))
    by_name = {r.name: r for r in report.results}

    assert by_name["decision:S-0009/D-1"].outcome == "pass"
    assert report.exit_code == 0


def test_a_decision_check_that_selects_nothing_fails_as_vacuous(tmp_path):
    """D-1: a `-k` filter that matched nothing is not a pass. pytest exits 5
    with "no tests ran", and the gate fails by its own name — still under the
    row's shadow state, so it records rather than blocks."""
    from torve.config.manifest import Manifest
    from torve.domain.task import InheritedDecision
    from torve.gates.context import DiffEntry, GateContext
    from torve.gates.runner import run_gates

    row = InheritedDecision(
        id="S-0009/D-1",
        grade="LOCKED",
        text="the check decides",
        paths=["src/**"],
        check="uv run pytest -k nothing",
    )
    ctx = GateContext(
        root=tmp_path,
        manifest=Manifest(gates=[]),
        head_sha="",
        base=None,
        merge_base=None,
        # A change under the row's paths, so its check is one the diff reaches.
        diff=[DiffEntry(status="M", path="src/app.py")],
        inherited=[row],
        execute=lambda command, timeout: (5, "no tests ran in 0.01s\n"),
    )

    report = run_gates(ctx)
    gate = next(r for r in report.results if r.name == "decision:S-0009/D-1")

    assert gate.outcome == "fail"
    assert "the check selected nothing" in gate.output
    assert gate.state == "shadow"
    assert report.exit_code == 0  # shadow still convicts nothing by itself


def test_the_no_tests_ran_reading_is_vacuous_too():
    """D-1: the second reading — a summary that ran no test — is vacuous as
    well, whether or not the command exited non-zero."""
    from torve.gates.acceptance import selected_nothing

    assert selected_nothing(5, "no tests ran in 0.01s\n")
    assert selected_nothing(0, "no tests ran in 0.01s\n")
    assert not selected_nothing(0, "12 passed in 0.30s\n")
    assert not selected_nothing(0, "Resolved 150 packages in 1.02s\n")


def test_a_promoted_checkable_row_blocks_when_its_check_is_red(repo):
    from torve.gates.runner import run_gates

    repo.seed()
    repo.task(
        base_task(
            allow=["src/**"],
            decisions=_checked("sh -c 'exit 1'", state="blocking", twin="tests/test_gates.py"),
        ),
        log_document(),
    )
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")

    report = run_gates(completing(repo))
    gate = next(r for r in report.results if r.name == "decision:S-0009/D-1")

    assert gate.outcome == "fail" and gate.state == "blocking"
    assert report.exit_code == 1


def test_decision_gates_carry_their_row_as_origin_and_the_compliance_axis(repo):
    from torve.gates.runner import row_gates

    repo.seed()
    repo.task(
        base_task(allow=["src/**"], decisions=_checked("true", twin="tests/test_x.py"))
        | {"spec": "S-0054"},
        log_document(),
    )
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")

    task = context_for(repo).task
    assert task is not None
    (gate,) = row_gates(task.decisions)

    assert gate.name == "decision:S-0009/D-1" and gate.run == "true"
    assert gate.origin == "S-0009/D-1" and gate.axis == "compliance"
    assert gate.sabotage == "tests/test_x.py" and gate.input == "worktree"


def test_an_attempt_s_battery_runs_no_row_s_check(repo):
    """S-0122/D-4: a row's check runs when its document completes. A task's
    own battery carries none, whatever its contract holds."""

    from torve.gates.runner import battery

    repo.seed()
    repo.task(
        base_task(allow=["src/**"], decisions=_checked("true", twin="tests/test_x.py")),
        log_document(),
    )
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")

    ctx = context_for(repo)

    assert ctx.task is not None and ctx.task.decisions
    assert ctx.inherited == []
    assert not [gate.name for gate in battery(ctx) if gate.name.startswith("decision:")]
    assert "decision:S-0009/D-1" in [gate.name for gate in battery(completing(repo))]


def test_a_manifest_still_naming_rows_loads_with_a_warning(tmp_path):
    """The key that chose where rows ran is gone; a manifest naming it loads
    for this release and says so."""

    import pytest

    from torve.config.manifest import load_manifest

    path = tmp_path / "gates.yaml"
    path.write_text("schema_version: 1\nrows: attempt\ngates: []\n", encoding="utf-8")

    with pytest.warns(FutureWarning, match="`rows` is ignored"):
        manifest = load_manifest(path)

    assert manifest.gates == []


# ----------------------- #
# S-0057 S-0057/D-7: the landing file is the task's own, like its log


def test_scope_implicitly_allows_the_named_documents_execution_directory(repo):
    from dataclasses import replace

    from torve.gates.context import load_task

    repo.seed()
    repo.task(base_task(allow=["src/**"]), None)
    contract = repo.root / ".torve" / "tasks" / TASK_ID / "contract.yaml"
    task = load_task(contract).model_copy(update={"spec": "S-0001"})
    repo.write(".torve/specs/S-0001/execution/T-0001-1-20260101T000000Z.yaml", "task: T-0001\n")
    repo.write(".torve/specs/S-0001/document.yaml", "id: '0001'\n")
    repo.write("src/app.py", "print('changed')\n")
    repo.commit("the work and its landing")

    outcome = check_scope(GATE, replace(context_for(repo), task=task))

    assert "execution/" not in outcome.output
    # the author's file is not the task's, and stays outside allow
    assert ".torve/specs/S-0001/document.yaml" in outcome.output


# ----------------------- #
# S-0070/D-6: the acceptance verdict names the suite it judged — a battery
# that ran fewer tests than the tree holds reports how many and why, so
# `pass` never stands for two different suites.


def _acceptance_over(tmp_path, output: str, exit_code: int = 0):
    from torve.config.manifest import Manifest
    from torve.domain.task import Task
    from torve.gates.acceptance import check_acceptance
    from torve.gates.context import GateContext

    gate = Gate(name="acceptance", run="@task.acceptance", state="blocking", origin="structural")
    task = Task(id="T-0001", role="implement", decisions=[], acceptance=["uv run pytest"])
    ctx = GateContext(
        root=tmp_path,
        manifest=Manifest(gates=[gate]),
        head_sha="",
        base=None,
        merge_base=None,
        task=task,
        execute=lambda command, timeout: (exit_code, output),
    )
    return check_acceptance(gate, ctx)


def test_acceptance_reports_how_many_tests_it_skipped_and_why(tmp_path):
    outcome = _acceptance_over(
        tmp_path,
        "SKIPPED [33] tests/test_runtime.py:12: no docker daemon\n"
        "120 passed, 33 skipped in 4.53s\n",
    )
    assert outcome.outcome == "pass"
    # the count leads the verdict, before any command log
    assert outcome.output.startswith("suite: 33 tests in the tree did not run")
    assert "suite: 120 of 153 tests ran, 33 skipped" in outcome.output
    assert "[33] tests/test_runtime.py:12: no docker daemon" in outcome.output


def test_acceptance_says_when_the_skips_carry_no_reason(tmp_path):
    # A command that was never asked for reasons has none to give; the gate
    # names that rather than inventing one (S-0070/D-3).
    outcome = _acceptance_over(tmp_path, "120 passed, 33 skipped in 4.53s\n")
    assert "33 skipped" in outcome.output
    assert "no reason reported" in outcome.output


def test_acceptance_names_a_whole_suite_and_flags_nothing(tmp_path):
    outcome = _acceptance_over(tmp_path, "153 passed in 4.53s\n")
    assert outcome.outcome == "pass"
    assert "suite: 153 of 153 tests ran, none skipped" in outcome.output
    assert "did not run" not in outcome.output


def test_acceptance_reports_an_empty_suite_rather_than_a_silent_green(tmp_path):
    # S-0089/D-4: an empty suite that exits zero is a fail, not a green.
    outcome = _acceptance_over(tmp_path, "no tests ran in 0.01s\n")
    assert outcome.outcome == "fail"
    assert outcome.output.startswith("suite: no test ran — 0 skipped")
    assert "no tests ran" in outcome.output


def test_acceptance_fails_a_green_command_whose_every_test_was_skipped(tmp_path):
    # S-0089/D-4: a lane that never executed cannot land green.
    outcome = _acceptance_over(tmp_path, "33 skipped in 0.20s\n")
    assert outcome.outcome == "fail"
    assert outcome.output.startswith("suite: no test ran — 33 skipped")

    deselected = _acceptance_over(tmp_path, "4 deselected in 0.10s\n")
    assert deselected.outcome == "fail"
    assert deselected.output.startswith("suite: no test ran")


def test_acceptance_counts_deselected_tests_as_absent_too(tmp_path):
    outcome = _acceptance_over(tmp_path, "10 passed, 4 deselected in 0.30s\n")
    assert outcome.output.startswith("suite: 4 tests in the tree did not run")
    assert "4 deselected" in outcome.output


def test_acceptance_over_a_command_with_no_summary_says_nothing_about_a_suite(tmp_path):
    # A build or a linter is not a test suite; the gate does not invent counts.
    outcome = _acceptance_over(tmp_path, "Success: no issues found in 42 source files\n")
    assert "suite:" not in outcome.output
    assert outcome.outcome == "pass"


def test_acceptance_does_not_read_a_timed_line_as_an_empty_suite(tmp_path):
    # S-0089/D-4 fails a suite in which no test ran, and a suite is a pytest
    # summary: uv's install line carries a duration too, and a command that
    # ran mypy after it must not be failed as an empty suite.
    outcome = _acceptance_over(
        tmp_path,
        "Success: no issues found in 137 source files\nInstalled 1 package in 1.02s\n",
    )
    assert outcome.outcome == "pass"
    assert "suite:" not in outcome.output


def test_acceptance_reports_the_suite_on_a_red_verdict_too(tmp_path):
    outcome = _acceptance_over(tmp_path, "1 failed, 119 passed, 33 skipped in 4.53s\n", exit_code=1)
    assert outcome.outcome == "fail"
    assert "33 skipped" in outcome.output


def test_scope_admits_the_lock_file_of_a_manifest_it_admits(repo):
    """A scope that admits `pyproject.toml` admits the `uv.lock` a dependency
    change rewrites; refusing the lock refuses the change the scope allowed
    (bloomery T-0008, 2026-09-19). A lock without its manifest in scope is
    still outside, which is evidence (S-0122/D-12)."""
    repo.seed()
    repo.task(base_task(allow=["pyproject.toml", "src/**"]), log_document())
    repo.write("pyproject.toml", "[project]\nname = 'x'\n")
    repo.write("uv.lock", "version = 1\n")
    repo.commit("a dependency and its lock")
    result = check_scope(GATE, context_for(repo))
    assert result.outcome == "pass", result.output

    repo.task(base_task(allow=["src/**"]), log_document())
    repo.write("uv.lock", "version = 2\n")
    repo.commit("the lock alone")
    result = check_scope(GATE, context_for(repo))
    assert result.outcome == "pass"
    assert "outside files and tests, as evidence:" in result.output
    assert "uv.lock" in result.output


def test_a_contingent_write_passes_the_gate_and_is_listed_as_promoted(repo):
    """S-0104/D-7: a contingent path the change was always going to reach
    passes the scope gate, and the gate names it as promoted so the widening
    is read rather than made silently."""
    repo.seed()
    task = base_task(allow=["src/**"])
    task["scope"]["contingent"] = ["docs/notes.md"]
    repo.task(task, log_document())
    repo.write("docs/notes.md", "notes\n")
    repo.commit("reaches a contingent path")

    result = check_scope(GATE, context_for(repo))

    assert result.outcome == "pass", result.output
    assert "promoted" in result.output
    assert "docs/notes.md" in result.output


def test_a_derived_golden_rides_with_its_producer_both_ways(repo):
    """S-0104/D-5: the manifest's `scope.derived` map names a golden with its
    producer; a scope that admits either admits the other, and the gate names
    the admitted one as promoted."""
    manifest = dict(
        BASE_MANIFEST,
        scope={
            "allow": [],
            "deny": [],
            "derived": {"src/model.py": ["tests/golden/model.json"]},
        },
    )
    repo.seed(manifest)
    repo.task(base_task(allow=["src/model.py"]), log_document())
    repo.write("src/model.py", "MODEL = 1\n")
    repo.write("tests/golden/model.json", "{}\n")
    repo.commit("a producer and its golden")

    result = check_scope(GATE, context_for(repo))
    assert result.outcome == "pass", result.output
    assert "tests/golden/model.json" in result.output  # promoted, not in allow

    repo.task(base_task(allow=["tests/golden/model.json"]), log_document())
    repo.write("src/model.py", "MODEL = 2\n")
    repo.commit("the golden's producer")

    result = check_scope(GATE, context_for(repo))
    assert result.outcome == "pass", result.output
    assert "src/model.py" in result.output  # promoted through the rider


def test_a_managed_agents_md_beside_a_scoped_path_passes(repo):
    """S-0104/D-6: a phase that changes governed code regenerates the managed
    projection beside it; the gate allows the `AGENTS.md` as it allows a lock,
    and names it as promoted."""
    repo.seed()
    repo.task(base_task(allow=["src/torve/gates/scope.py"]), log_document())
    repo.write("src/torve/gates/scope.py", "x = 1\n")
    repo.write("src/torve/gates/AGENTS.md", "# managed\n")
    repo.commit("the code and its projection")

    result = check_scope(GATE, context_for(repo))

    assert result.outcome == "pass", result.output
    assert "src/torve/gates/AGENTS.md" in result.output


def test_a_managed_agents_md_with_no_scoped_neighbour_is_outside(repo):
    """S-0104/D-6: *beside a path in scope*. A projection in a directory the
    contract does not reach is evidence like any other out-of-scope file."""
    repo.seed()
    repo.task(base_task(allow=["src/other.py"]), log_document())
    repo.write("src/other.py", "y = 1\n")
    repo.write("src/torve/gates/AGENTS.md", "# drifted\n")
    repo.commit("a projection nothing governs")

    result = check_scope(GATE, context_for(repo))

    assert "outside files and tests, as evidence: src/torve/gates/AGENTS.md" in result.output
    assert "promoted" not in result.output


def test_an_empty_allow_is_unconstrained_and_promotes_nothing(repo):
    """S-0002: an empty allow means unconstrained — every path passes, and
    nothing is named as promoted, because nothing needed a rule beyond the
    (absent) declared globs."""
    repo.seed()
    repo.write("anything/at/all.txt", "x\n")
    repo.commit("no task, no scope")

    result = check_scope(GATE, context_for(repo))

    assert result.outcome == "pass", result.output
    assert "promoted" not in result.output
