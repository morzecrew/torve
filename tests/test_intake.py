"""Intake and the drafting run (S-0020 phase 1): the parse discipline,
the contract lint's refusals, the draft-lint loop, and adoption — ids
minted under the lock, refs rewritten, contracts committed."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from test_decisions import document as spec_document
from test_decisions import place

from torve.application.intake import (
    DraftsDocument,
    ThresholdVerdict,
    adopt,
    build_intake_prompt,
    document_threshold,
    document_threshold_warnings,
    drafts_file,
    execution_facts,
    lint_configuration_change,
    lint_contract,
    lint_decomposition,
    lint_document_threshold,
    lint_drafts,
    mint_decomposition_task,
    mint_intake_task,
    parse_drafts,
    run_intake,
    standing_warnings,
)
from torve.application.ports import AgentResult, SandboxHandle
from torve.application.runstate import RunState
from torve.base import naming
from torve.config.layout import SPECS_DIR as SPECS
from torve.config.runconfig import RunnerConfig
from torve.domain.attempt import SizeVerdict
from torve.domain.states import TaskState
from torve.domain.task import InheritedDecision, Scope, Task

# ----------------------- #


class ScriptedAgent:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    def run(self, ctx):
        self.prompts.append(ctx.prompt)
        return AgentResult(exit_code=0, output=self.outputs.pop(0))


class StubRuntime:
    def __init__(
        self, *, image_digest: str | None = "sha256:stub", build_error: str | None = None
    ) -> None:
        self.created = 0
        self.destroyed = 0
        self.built: list[tuple[Path, str]] = []
        self._image_digest = image_digest
        self._build_error = build_error

    def create(self, spec, workspace):
        self.created += 1
        assert spec.workspace_read_only  # S-0020/D-2: the drafter reads, never writes
        return SandboxHandle(id=f"sbx-{self.created}", name=spec.name)

    def destroy(self, handle):
        self.destroyed += 1

    def resolve_image(self, image):
        return self._image_digest

    def build_image(self, context, tag):
        if self._build_error is not None:
            raise RuntimeError(self._build_error)

        self.built.append((context, tag))
        return "sha256:built"


def draft_dict(
    ref: str = "DRAFT-1",
    *,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
    intent: str = "add a module",
    acceptance: list[str] | None = None,
    depends_on: list[str] | None = None,
) -> dict:
    return {
        "ref": ref,
        "intent": intent,
        "scope": {
            "allow": allow if allow is not None else ["src/newmod.py", "tests/test_newmod.py"],
            "deny": deny or [],
        },
        "acceptance": acceptance
        if acceptance is not None
        else ["python3 -m unittest discover -s tests -v"],
        "depends_on": depends_on or [],
    }


def document(*drafts: dict) -> DraftsDocument:
    return DraftsDocument.model_validate({"drafts": list(drafts), "rationale": "r"})


def output_for(*drafts: dict) -> str:
    return "chatter before\n" + json.dumps(
        {"drafts": list(drafts), "rationale": "the decomposition"}
    )


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "tests" / "test_app.py").write_text("def test(): pass\n", encoding="utf-8")
    return tmp_path


# ----------------------- #
# The parse discipline (S-0020/D-3): last document wins, unparseable is None.


def test_parse_takes_the_last_drafts_document():
    first = json.dumps({"drafts": [draft_dict("DRAFT-1")]})
    second = json.dumps({"drafts": [draft_dict("DRAFT-2")]})
    parsed = parse_drafts(f"noise {first} more noise {second} trailing")
    assert parsed is not None
    assert [d.ref for d in parsed.drafts] == ["DRAFT-2"]


def test_parse_strips_ansi_and_tolerates_chatter():
    body = json.dumps({"drafts": [draft_dict()], "rationale": "why"})
    parsed = parse_drafts(f"\x1b[32mgreen\x1b[0m {body}\nsession goodbye")
    assert parsed is not None
    assert parsed.rationale == "why"


def test_parse_unwraps_a_harness_result_envelope():
    """claude -p --output-format json returns one envelope whose `result`
    string carries the agent's answer — the drafts document arrives escaped
    inside it, found by the first live `torve decompose`."""
    import json as _json

    inner = _json.dumps({"drafts": [draft_dict()], "rationale": "the decomposition"})
    envelope = _json.dumps({"is_error": False, "result": inner, "type": "result"})
    document = parse_drafts(envelope)
    assert document is not None
    assert document.drafts[0].ref == "DRAFT-1"


def test_parse_without_a_drafts_document_is_none():
    assert parse_drafts("no json here") is None
    assert parse_drafts(json.dumps({"findings": []})) is None


def test_parse_refuses_an_invalid_shape_by_field():
    # S-0054/D-15: a drafts document that fails the model is a refusal naming
    # the field, not repaired and not "unparseable".
    from torve.application.review import SchemaRefusal

    with pytest.raises(SchemaRefusal, match=r"drafts document\.drafts\.0\.ref: Field required"):
        parse_drafts(json.dumps({"drafts": [{"no": "ref"}]}))


# ----------------------- #
# The lint (S-0020/D-3): every refusal names the draft and the field.


def test_lint_green_on_a_creatable_disjoint_batch(tree: Path):
    errors = lint_drafts(
        tree,
        document(
            draft_dict("DRAFT-1"),
            draft_dict("DRAFT-2", allow=["src/other.py", "tests/test_other.py"]),
        ),
        4,
    )
    assert errors == []


def test_lint_refuses_an_acceptance_command_that_needs_git(tree: Path):
    """A-131, A-132: the acceptance battery runs inside the sandbox, and a
    sandbox mounts the worktree without a repository — `.git` there points
    at a host path the container never sees. T-0282 burned a whole poison
    ceiling on `uv run torve gates run`, three attempts whose own tests
    passed every time, and nine phases across seven documents carry it."""

    refused = lint_drafts(tree, document(draft_dict(acceptance=["uv run torve gates run"])), 4)
    assert any("needs git" in e for e in refused)
    # S-0044/D-10's principle: a refusal a model must act on names what would
    # have been allowed. Three drafting attempts proposed `torve gates
    # check` and were refused with the reason and no alternative; all three
    # proposed it again.
    assert any("spec check" in e and "lint-imports" in e for e in refused)

    # A bare git command is the same wall, said plainly.
    assert any(
        "needs git" in e
        for e in lint_drafts(tree, document(draft_dict(acceptance=["git diff --exit-code"])), 4)
    )

    # What must stay legal: the commands that carried both of S-0052's
    # phases, none of which touches a repository.
    assert (
        lint_drafts(
            tree,
            document(
                draft_dict(
                    acceptance=[
                        "uv run pytest tests/test_newmod.py",
                        "uv run lint-imports --config pyproject.toml",
                        "uv run torve spec check",
                    ]
                )
            ),
            4,
        )
        == []
    )


def test_lint_refuses_an_empty_batch(tree: Path):
    errors = lint_drafts(tree, DraftsDocument(drafts=[]), 4)
    assert any("empty batch" in e for e in errors)


def test_lint_enforces_the_ceiling(tree: Path):
    drafts = [
        draft_dict(f"DRAFT-{n}", allow=[f"src/m{n}.py", f"tests/test_m{n}.py"]) for n in range(1, 6)
    ]
    errors = lint_drafts(tree, document(*drafts), 4)
    assert any("ceiling" in e for e in errors)


def test_lint_refuses_task_id_shaped_refs(tree: Path):
    errors = lint_drafts(tree, document(draft_dict("T-0090")), 4)
    assert any("ids exist only from adoption" in e for e in errors)


def test_lint_names_empty_fields(tree: Path):
    errors = lint_drafts(tree, document(draft_dict(intent="  ", acceptance=[], allow=[])), 4)
    assert any("intent is empty" in e for e in errors)
    assert any("acceptance is empty" in e for e in errors)
    assert any("scope.allow is empty" in e for e in errors)


def test_lint_refuses_unparseable_acceptance(tree: Path):
    errors = lint_drafts(tree, document(draft_dict(acceptance=["echo 'unclosed"])), 4)
    assert any("does not shell-parse" in e for e in errors)


def test_lint_refuses_escaping_and_dead_globs(tree: Path):
    errors = lint_drafts(
        tree, document(draft_dict(allow=["/etc/passwd", "../up.py", "src/nothing_*.py"])), 4
    )
    assert sum("escapes the tree" in e for e in errors) == 2
    assert any("matches nothing" in e for e in errors)


def test_lint_refuses_allow_deny_overlap_and_bad_deps(tree: Path):
    errors = lint_drafts(
        tree, document(draft_dict(deny=["src/newmod.py"], depends_on=["DRAFT-1", "DRAFT-9"])), 4
    )
    assert any("both allowed and denied" in e for e in errors)
    assert any("depends on itself" in e for e in errors)
    assert any("unknown draft 'DRAFT-9'" in e for e in errors)


def test_lint_t0113_rule_wants_the_existing_test_file(tree: Path):
    red = lint_drafts(tree, document(draft_dict(allow=["src/app.py"])), 4)
    assert any("T-0113" in e for e in red)
    green = lint_drafts(tree, document(draft_dict(allow=["src/app.py", "tests/test_app.py"])), 4)
    assert green == []


def test_lint_refuses_intersecting_scopes(tree: Path):
    errors = lint_drafts(tree, document(draft_dict("DRAFT-1"), draft_dict("DRAFT-2")), 4)
    assert any("scopes intersect" in e for e in errors)


# ----------------------- #
# The decomposition batch's own four rules (S-0026/the-decomposition-run), each with a red
# twin, layered on the ordinary contract lint above.


def _parent(allow: list[str], acceptance: list[str] | None = None) -> Task:
    return Task(
        id="T-0100",
        scope=Scope(allow=allow),
        acceptance=acceptance if acceptance is not None else ["true"],
        decisions=[],
    )


def test_lint_decomposition_green_on_a_proper_split(tree: Path):
    # A new, single-module file: touching both src/ and tests/ at once trips
    # sizing's own too-large rule (MAX_MODULES=1) — the per-child sizing
    # check below has its own dedicated test.
    parent = _parent(["src/**"], acceptance=["true"])
    errors = lint_decomposition(
        tree,
        document(draft_dict("DRAFT-1", allow=["src/widget.py"], acceptance=["true"])),
        parent,
        4,
    )
    assert errors == []


def test_lint_decomposition_refuses_a_child_escaping_parent_scope(tree: Path):
    parent = _parent(["src/**"])
    errors = lint_decomposition(
        tree,
        document(draft_dict("DRAFT-1", allow=["docs/x.py"], acceptance=["true"])),
        parent,
        4,
    )
    assert any("outside T-0100's scope.allow" in e for e in errors)


def test_lint_decomposition_refuses_overlap_without_a_depends_on_edge(tree: Path):
    parent = _parent(["src/**"])
    green_edge = lint_decomposition(
        tree,
        document(
            draft_dict("DRAFT-1", allow=["src/shared.py"], acceptance=["true"]),
            draft_dict(
                "DRAFT-2",
                allow=["src/shared.py"],
                acceptance=["true"],
                depends_on=["DRAFT-1"],
            ),
        ),
        parent,
        4,
    )
    assert green_edge == []

    red = lint_decomposition(
        tree,
        document(
            draft_dict("DRAFT-1", allow=["src/shared.py"], acceptance=["true"]),
            draft_dict("DRAFT-2", allow=["src/shared.py"], acceptance=["true"]),
        ),
        parent,
        4,
    )
    assert any("scopes intersect" in e for e in red)


def test_lint_decomposition_refuses_a_dropped_acceptance_command(tree: Path):
    parent = _parent(["src/**", "tests/**"], acceptance=["true", "false"])
    errors = lint_decomposition(
        tree,
        document(
            draft_dict("DRAFT-1", allow=["src/app.py", "tests/test_app.py"], acceptance=["true"]),
        ),
        parent,
        4,
    )
    assert any("acceptance command 'false' is dropped" in e for e in errors)


def test_lint_decomposition_refuses_an_oversized_child(tree: Path):
    parent = _parent(["src/**", "tests/**", "docs/**"])
    errors = lint_decomposition(
        tree,
        document(
            draft_dict(
                "DRAFT-1",
                allow=["src/app.py", "tests/test_app.py", "docs/x.py"],
                acceptance=["true"],
            ),
        ),
        parent,
        4,
    )
    assert any("too large" in e for e in errors)


# ----------------------- #
# Depth bound (S-0026/D-12): a third decomposition round is refused by name.


def test_mint_decomposition_task_refuses_a_third_round(seeded):
    config = RunnerConfig()
    root = seeded.root

    def write(task_id: str, parent: str | None) -> None:
        task_dir = root / ".torve" / "tasks" / task_id
        task_dir.mkdir(parents=True)
        data: dict = {"schema_version": 1, "id": task_id, "role": "implement", "decisions": []}
        if parent:
            data["parent"] = parent
        (task_dir / "contract.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    write("T-0100", None)  # the original, undecomposed contract
    write("T-0101", "T-0100")  # round 1 child
    write("T-0102", "T-0101")  # round 2 grandchild

    mint_decomposition_task(root, "T-0101", config)  # round 1 -> round 2: fine

    with pytest.raises(ValueError, match="already 2 decomposition round"):
        mint_decomposition_task(root, "T-0102", config)


def test_lint_contract_standalone_and_role_guard(tree: Path):
    contract = tree / "contract.yaml"
    contract.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": "T-0001",
                "role": "implement",
                "intent": "do",
                "scope": {"allow": ["src/app.py"], "deny": []},
                "acceptance": ["true"],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    errors = lint_contract(tree, contract)
    assert any("T-0113" in e and "T-0001" in e for e in errors)
    review = tree / "review.yaml"
    review.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": "T-0002",
                "role": "review",
                "targets": ["T-0001"],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    assert lint_contract(tree, review) == []


def test_standing_warnings_name_missing_rows_and_silence_carried_ones(tree: Path):
    place(tree / SPECS, "0099", _rfc_doc("0099", "S-0099/D-1", "src/**"))

    def contract(decisions: list) -> Path:
        path = tree / "contract.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "id": "T-0001",
                    "role": "implement",
                    "intent": "do",
                    "scope": {"allow": ["src/app.py"], "deny": []},
                    "acceptance": ["true"],
                    "decisions": decisions,
                }
            ),
            encoding="utf-8",
        )
        return path

    # A hand-minted contract whose scope crosses the standing row but that
    # does not carry it: the advisory names it.
    warnings = standing_warnings(tree, contract([]))
    assert len(warnings) == 1
    assert "S-0099/D-1" in warnings[0] and "LOCKED" in warnings[0]
    assert "scope.allow" in warnings[0]

    # Carrying the row silences the advisory.
    carried = [
        {
            "id": "S-0099/D-1",
            "grade": "LOCKED",
            "text": "The rule",
            "paths": ["src/**"],
            "consequence": "",
            "check": None,
            "check_state": "shadow",
            "check_twin": None,
        }
    ]
    assert standing_warnings(tree, contract(carried)) == []

    # A scope that does not intersect the standing row hears nothing.
    out = tree / "out.yaml"
    out.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": "T-0002",
                "role": "implement",
                "intent": "elsewhere",
                "scope": {"allow": ["docs/**"], "deny": []},
                "acceptance": ["true"],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    assert standing_warnings(tree, out) == []

    # Non-implement roles are outside the advisory's surface.
    review = tree / "review.yaml"
    review.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": "T-0003",
                "role": "review",
                "targets": ["T-0001"],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    assert standing_warnings(tree, review) == []


def _rfc_doc(
    number: str,
    decision_id: str,
    path: str,
    *,
    grade: str = "LOCKED",
    text: str = "The rule",
    status: str = "accepted",
    title: str = "Fixture",
) -> dict[str, str]:
    """One fixture document as its four files: a single row over one path,
    as the corpus builder writes it (S-0056 S-0056/D-1, S-0057 S-0057/D-1)."""

    return spec_document(
        number,
        [(decision_id, grade, text, f"`{path}`")],
        status=status,
        title=title,
        implementation="none",
    )


def _locked(decision_id: str, paths: list[str] | None = None) -> InheritedDecision:
    return InheritedDecision(id=decision_id, grade="LOCKED", text="rule", paths=paths or ["src/**"])


# ----------------------- #
# The threshold verdict (S-0030/D-2, S-0030/D-3): pure arithmetic over standing
# rows, document ownership and the size verdict — no file I/O.


def test_document_threshold_rides_one_documents_locked_ground():
    standing = [_locked("S-0001/D-1"), _locked("S-0001/D-2")]
    documents = {"S-0001/D-1": "S-0001", "S-0001/D-2": "S-0001"}
    verdict = document_threshold(standing, SizeVerdict(size="ok"), documents, 2)
    assert verdict == ThresholdVerdict(verdict="rides", reasons=[])


def test_document_threshold_fires_when_locked_rows_cross_two_documents():
    standing = [_locked("S-0001/D-1"), _locked("S-0002/D-1")]
    documents = {"S-0001/D-1": "S-0001", "S-0002/D-1": "S-0002"}
    verdict = document_threshold(standing, SizeVerdict(size="ok"), documents, 2)
    assert verdict.verdict == "document_required"
    assert "S-0001" in verdict.reasons[0] and "S-0002" in verdict.reasons[0]
    assert "S-0001/D-1" in verdict.reasons[0] and "S-0002/D-1" in verdict.reasons[0]


def test_document_threshold_ignores_non_locked_rows_across_documents():
    standing = [
        InheritedDecision(id="S-0001/D-1", grade="ASSUMED", text="r", paths=["src/**"]),
        InheritedDecision(id="S-0002/D-1", grade="OPEN", text="r", paths=["src/**"]),
    ]
    documents = {"S-0001/D-1": "S-0001", "S-0002/D-1": "S-0002"}
    verdict = document_threshold(standing, SizeVerdict(size="ok"), documents, 2)
    assert verdict.verdict == "rides"


def test_document_threshold_fires_on_too_large_alone():
    size = SizeVerdict(size="too_large", reasons=["touches 2 top-level modules: lib, src"])
    verdict = document_threshold([], size, {}, 2)
    assert verdict.verdict == "document_required"
    assert "too large" in verdict.reasons[0]


def test_document_threshold_honours_a_lower_configured_minimum():
    standing = [_locked("S-0001/D-1")]
    verdict = document_threshold(standing, SizeVerdict(size="ok"), {"S-0001/D-1": "S-0001"}, 1)
    assert verdict.verdict == "document_required"


def test_document_threshold_counts_documents_not_id_families():
    # A family of ids is not a document (S-0030/D-3): S-0001 alone carries
    # D-2, D-25 and D-A.* as one document — counting families would
    # overcount this single document as three, which is the bug this
    # resolution exists to rule out.
    standing = [_locked("S-0002/D-1"), _locked("S-0025/D-3"), _locked("S-0001/D-36")]
    documents = {"S-0002/D-1": "S-0001", "S-0025/D-3": "S-0001", "S-0001/D-36": "S-0001"}
    verdict = document_threshold(standing, SizeVerdict(size="ok"), documents, 2)
    assert verdict.verdict == "rides"


# ----------------------- #
# The intake lint's enforcement surface (S-0030/D-4): a batch-level check, a
# no-op absent a corpus directory, layered like the configuration lint.


def test_lint_document_threshold_rides_one_documents_locked_ground(tree: Path):
    place(tree / SPECS, "0099", _rfc_doc("0099", "S-0099/D-1", "src/**"))
    errors = lint_document_threshold(tree, document(draft_dict("DRAFT-1")), RunnerConfig())
    assert errors == []


def test_lint_document_threshold_fires_when_scope_crosses_two_documents(tree: Path):
    place(tree / SPECS, "0097", _rfc_doc("0097", "S-0097/D-1", "src/newmod.py"))
    place(tree / SPECS, "0099", _rfc_doc("0099", "S-0099/D-1", "src/newmod.py"))
    errors = lint_document_threshold(tree, document(draft_dict("DRAFT-1")), RunnerConfig())
    assert len(errors) == 1
    assert "DRAFT-1" in errors[0]
    assert "S-0097/D-1" in errors[0] and "S-0099/D-1" in errors[0]
    assert "S-0097" in errors[0] and "S-0099" in errors[0]
    for coordinate in ("S-0030", "S-0030/D-"):
        assert coordinate not in errors[0]


def test_lint_document_threshold_fires_on_too_large_alone(tree: Path):
    errors = lint_document_threshold(
        tree, document(draft_dict("DRAFT-1", allow=["src/a.py", "lib/b.py"])), RunnerConfig()
    )
    assert len(errors) == 1
    assert "too large" in errors[0]
    for coordinate in ("S-0030", "S-0030/D-"):
        assert coordinate not in errors[0]


def test_lint_document_threshold_counts_documents_not_id_families(tree: Path):
    place(
        tree / SPECS,
        "0001",
        spec_document(
            "0001",
            [
                ("S-0002/D-1", "LOCKED", "Rule one", "`src/**`"),
                ("S-0025/D-3", "LOCKED", "Rule two", "`src/**`"),
                ("S-0001/D-36", "LOCKED", "Rule three", "`src/**`"),
            ],
            title="Engine",
            implementation="none",
        ),
    )
    errors = lint_document_threshold(tree, document(draft_dict("DRAFT-1")), RunnerConfig())
    assert errors == []


def test_document_threshold_warnings_advise_without_failing_the_contract_lint(tree: Path):
    place(tree / SPECS, "0097", _rfc_doc("0097", "S-0097/D-1", "src/app.py"))
    place(tree / SPECS, "0099", _rfc_doc("0099", "S-0099/D-1", "src/app.py"))
    contract = tree / "contract.yaml"
    contract.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": "T-0001",
                "role": "implement",
                "intent": "do",
                "scope": {"allow": ["src/app.py", "tests/test_app.py"], "deny": []},
                "acceptance": ["true"],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    assert lint_contract(tree, contract) == []  # advisory, never a refusal
    warnings = document_threshold_warnings(tree, contract, RunnerConfig())
    assert len(warnings) == 1
    assert "needs its own document" in warnings[0]
    for coordinate in ("S-0030", "S-0030/D-"):
        assert coordinate not in warnings[0]


# ----------------------- #
# The role's contract shape.


def test_draft_role_carries_no_acceptance_and_at_most_one_target():
    with pytest.raises(ValueError, match="contract lint"):
        Task(id="T-1", role="draft", acceptance=["true"], decisions=[])
    # S-0026/the-decomposition-run: a decomposition run is a draft naming the one contract
    # it decomposes — the same targets-name-what-it-acts-on shape review and
    # revert already carry.
    with pytest.raises(ValueError, match="at most one target"):
        Task(id="T-1", role="draft", targets=["T-0", "T-2"], decisions=[])
    decompose = Task(id="T-1", role="draft", targets=["T-0"], decisions=[])
    assert decompose.targets == ["T-0"]
    task = Task(id="T-1", role="draft", intent="req", decisions=[])
    assert task.tier == "executor"  # the mint sets planner; the model does not care


# ----------------------- #
# The draft-lint loop.


@pytest.fixture
def seeded(repo):
    repo.seed()
    repo.git("checkout", "-q", "main")
    return repo


def test_a_real_tier_under_no_broker_drafts_without_a_provider_table(seeded):
    """The none adapter demands no broker.providers entry — the second copy
    of the landing-day regression, this one on the intake path (the runner's
    twin guard is broker_in_force at dispatch)."""
    from torve.adapters.broker import NoneBroker
    from torve.config.runconfig import TierConfig

    config = RunnerConfig(
        tiers={
            "planner": TierConfig(
                adapter="harness",
                command="run",
                provider="anthropic",
                api_key_env=["X"],
            ),
            "executor": TierConfig(),
            "reviewer": TierConfig(),
        },
    )
    task = mint_intake_task(seeded.root, "add a widget module", config)
    agent = ScriptedAgent([output_for(draft_dict())])
    outcome = run_intake(
        seeded.root,
        seeded.root,
        task,
        config,
        StubRuntime(),
        agent,
        "digest",
        broker=NoneBroker(),
    )
    assert outcome.attempts == 1


def test_run_intake_green_first_try(seeded):
    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "add a widget module", config)
    agent = ScriptedAgent([output_for(draft_dict())])
    outcome = run_intake(seeded.root, seeded.root, task, config, StubRuntime(), agent, "digest")

    assert outcome.attempts == 1
    assert [d.ref for d in outcome.drafts] == ["DRAFT-1"]
    state = RunState.load(naming.state_file(seeded.root, task.id))
    assert state.state is TaskState.READY
    assert any("awaiting adoption" in h["fact"] for h in state.history)
    stored = json.loads(drafts_file(seeded.root, task.id).read_text(encoding="utf-8"))
    assert stored["rationale"] == "the decomposition"
    assert stored["request"] == "add a widget module"


def test_run_intake_feeds_lint_refusals_into_the_retry(seeded):
    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "add things", config)
    agent = ScriptedAgent(
        [
            output_for(draft_dict(allow=["src/app.py"])),  # T-0113 red
            output_for(draft_dict()),
        ]
    )
    outcome = run_intake(seeded.root, seeded.root, task, config, StubRuntime(), agent, "digest")

    assert outcome.attempts == 2
    assert "refused by the lint" in agent.prompts[1]
    assert "T-0113" in agent.prompts[1]
    assert RunState.load(naming.state_file(seeded.root, task.id)).state is TaskState.READY


def test_run_intake_feeds_a_schema_refusal_into_the_retry(seeded):
    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "add things", config)
    agent = ScriptedAgent(
        [
            json.dumps({"drafts": [{"intent": "no ref"}]}),
            output_for(draft_dict()),
        ]
    )
    outcome = run_intake(seeded.root, seeded.root, task, config, StubRuntime(), agent, "digest")

    assert outcome.attempts == 2
    assert not outcome.unparseable
    assert "refused by the lint" in agent.prompts[1]
    assert "drafts document.drafts.0.ref: Field required" in agent.prompts[1]
    assert RunState.load(naming.state_file(seeded.root, task.id)).state is TaskState.READY


def test_the_drafter_reads_the_pack_index_not_a_listing(seeded):
    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "add a widget", config)
    agent = ScriptedAgent([output_for(draft_dict())])
    run_intake(seeded.root, seeded.root, task, config, StubRuntime(), agent, "digest")

    prompt = agent.prompts[0]
    assert "What the engine knows" in prompt
    assert "torve spec paths" in prompt
    assert "## The repository tree" not in prompt
    assert (seeded.root / ".torve" / "context" / "index.md").is_file()
    assert (seeded.root / ".torve" / "context" / "schema" / "draft.json").is_file()

    # composed bare, the prompt names the top of the tree and nothing more
    bare = build_intake_prompt("add a widget", seeded.root, 3)
    assert "## The repository tree" in bare and "- src" in bare
    assert "src/app.py" not in bare


def test_run_intake_spent_budget_escalates(seeded):
    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "add things", config)
    agent = ScriptedAgent(["not json at all"] * config.intake.iterations)
    outcome = run_intake(seeded.root, seeded.root, task, config, StubRuntime(), agent, "digest")

    assert outcome.unparseable
    assert not outcome.drafts
    state = RunState.load(naming.state_file(seeded.root, task.id))
    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None
    assert state.escalation.reason == "budget_exhausted"
    assert not drafts_file(seeded.root, task.id).exists()


# ----------------------- #
# Adoption (S-0020/D-1, S-0020/D-4).


def adopted_ready_run(seeded, *, spec: str | None = None) -> str:
    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "two modules", config, spec=spec)
    agent = ScriptedAgent(
        [
            output_for(
                draft_dict("DRAFT-1"),
                draft_dict(
                    "DRAFT-2", allow=["src/other.py", "tests/test_other.py"], depends_on=["DRAFT-1"]
                ),
            )
        ]
    )
    run_intake(seeded.root, seeded.root, task, config, StubRuntime(), agent, "digest")
    seeded.commit("intake bookkeeping")
    return task.id


def test_adopt_mints_ids_rewrites_refs_and_commits(seeded):
    source = adopted_ready_run(seeded)
    adopted = adopt(seeded.root, source, RunnerConfig())

    assert len(adopted) == 2
    first, second = adopted
    contract = yaml.safe_load(
        (seeded.root / ".torve" / "tasks" / second / "contract.yaml").read_text(encoding="utf-8")
    )
    assert contract["depends_on"] == [first]  # DRAFT-1 rewritten (S-0020/D-4)
    assert contract["role"] == "implement"
    assert contract["decisions"] == []
    Task.model_validate(contract)  # the adopted contract is a legal task
    log = subprocess.run(
        ["git", "-C", str(seeded.root), "log", "-1", "--format=%s"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "adopt" in log and source in log
    assert not drafts_file(seeded.root, source).exists()
    assert not (seeded.root / ".torve" / "tick.lock").exists()  # lock released


def test_adopt_copies_decisions_from_an_accepted_document(seeded):
    place(seeded.root / SPECS, "0099", _rfc_doc("0099", "S-0099/D-1", "src/**"))
    seeded.commit("fixture spec")
    source = adopted_ready_run(seeded, spec="S-0099")
    adopted = adopt(seeded.root, source, RunnerConfig())

    contract = yaml.safe_load(
        (seeded.root / ".torve" / "tasks" / adopted[0] / "contract.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert contract["decisions"] == [
        {
            "id": "S-0099/D-1",
            "grade": "LOCKED",
            "text": "The rule",
            "paths": ["src/**"],
            "consequence": "",
            "check": None,
            "check_state": "shadow",
            "check_twin": None,
        }
    ]


def test_adopt_without_an_rfc_line_carries_intersecting_standing_rows(seeded):
    # S-0030/standing-inheritance: adoption always merges standing rows — the cited copy
    # is not the only lane; a scope crossing another document's paths
    # inherits that row even with no spec at all.
    place(seeded.root / SPECS, "0099", _rfc_doc("0099", "S-0099/D-1", "src/**"))
    seeded.commit("fixture spec")
    source = adopted_ready_run(seeded)  # no spec — the document-less lane
    adopted = adopt(seeded.root, source, RunnerConfig())

    for task_id in adopted:  # both drafts' scope crosses src/**
        contract = yaml.safe_load(
            (seeded.root / ".torve" / "tasks" / task_id / "contract.yaml").read_text(
                encoding="utf-8"
            )
        )
        assert contract["decisions"] == [
            {
                "id": "S-0099/D-1",
                "grade": "LOCKED",
                "text": "The rule",
                "paths": ["src/**"],
                "consequence": "",
                "check": None,
                "check_state": "shadow",
                "check_twin": None,
            }
        ]


def test_adopt_prefers_the_cited_documents_copy_over_standing(seeded):
    # Deduplicated by identifier, the cited copy wins (S-0030/D-1): 0097's
    # standing row is the same identifier as 0099's, and the request was
    # written against 0099 — its grade and text stand.
    place(
        seeded.root / SPECS,
        "0097",
        _rfc_doc("0097", "S-0099/D-1", "src/**", grade="ASSUMED", text="A weaker copy"),
    )
    place(seeded.root / SPECS, "0099", _rfc_doc("0099", "S-0099/D-1", "src/**"))
    seeded.commit("fixture specs")
    source = adopted_ready_run(seeded, spec="S-0099")
    adopted = adopt(seeded.root, source, RunnerConfig())

    contract = yaml.safe_load(
        (seeded.root / ".torve" / "tasks" / adopted[0] / "contract.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert contract["decisions"] == [
        {
            "id": "S-0099/D-1",
            "grade": "LOCKED",
            "text": "The rule",
            "paths": ["src/**"],
            "consequence": "",
            "check": None,
            "check_state": "shadow",
            "check_twin": None,
        }
    ]


def test_adopt_of_a_decomposition_sets_parent_and_grows_the_integration_task(seeded):
    # S-0026 S-0026/D-6: children mint with `parent` set, and the parent's own
    # `depends_on` grows with every child — it becomes the integration task.
    seeded.write(
        ".torve/tasks/T-0100/contract.yaml",
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": "T-0100",
                "role": "implement",
                "intent": "an oversized contract",
                "scope": {"allow": ["src/**"], "deny": []},
                "acceptance": ["true"],
                "decisions": [],
            }
        ),
    )
    seeded.commit("mint T-0100")

    config = RunnerConfig()
    task = mint_decomposition_task(seeded.root, "T-0100", config)
    agent = ScriptedAgent(
        [output_for(draft_dict("DRAFT-1", allow=["src/widget.py"], acceptance=["true"]))]
    )
    run_intake(seeded.root, seeded.root, task, config, StubRuntime(), agent, "digest")
    seeded.commit("intake bookkeeping")

    adopted = adopt(seeded.root, task.id, config)
    assert len(adopted) == 1
    child_id = adopted[0]

    child = yaml.safe_load(
        (seeded.root / ".torve" / "tasks" / child_id / "contract.yaml").read_text(encoding="utf-8")
    )
    assert child["parent"] == "T-0100"

    parent = yaml.safe_load(
        (seeded.root / ".torve" / "tasks" / "T-0100" / "contract.yaml").read_text(encoding="utf-8")
    )
    assert parent["depends_on"] == [child_id]
    assert parent["scope"]["allow"] == ["src/**"]  # scope stays exactly as authored
    assert parent["acceptance"] == ["true"]  # the full battery stays


def test_adopt_refuses_a_draft_status_document(seeded):
    place(seeded.root / SPECS, "0098", _rfc_doc("0098", "S-0098/D-1", "src/**", status="draft"))
    seeded.commit("draft spec")
    source = adopted_ready_run(seeded, spec="S-0098")
    with pytest.raises(ValueError, match="not accepted"):
        adopt(seeded.root, source, RunnerConfig())


def _write_ready_drafts(seeded, task_id: str, request: str, *drafts: dict) -> None:
    # A hand-assembled drafts.json, bypassing the drafting run entirely —
    # the same shape a green run would have persisted (S-0020/D-4), used here
    # because the scenario under test is one the intake lint itself would
    # already refuse (T-0197): adoption's own refusal must stand on its
    # own, not lean on the lint catching it first.
    drafts_file(seeded.root, task_id).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request": request,
                "spec": None,
                "rationale": "",
                "drafts": list(drafts),
            }
        ),
        encoding="utf-8",
    )


def test_adopt_refuses_a_scope_crossing_two_documents_locked_ground(seeded):
    # S-0030 S-0030/D-4: refused before anything is written — no lock, no
    # minted id, no commit — since adoption is the signature and a
    # signature over two documents' settled ground belongs on one.
    place(seeded.root / SPECS, "0097", _rfc_doc("0097", "S-0097/D-1", "src/newmod.py"))
    place(seeded.root / SPECS, "0099", _rfc_doc("0099", "S-0099/D-1", "src/newmod.py"))
    seeded.commit("fixture specs")
    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "two docs", config)
    _write_ready_drafts(seeded, task.id, "two docs", draft_dict())  # no spec

    with pytest.raises(ValueError, match="needs its own document") as excinfo:
        adopt(seeded.root, task.id, config)

    for coordinate in ("S-0030", "S-0030/D-"):
        assert coordinate not in str(excinfo.value)
    assert not (seeded.root / ".torve" / "tick.lock").exists()
    assert drafts_file(seeded.root, task.id).exists()  # nothing consumed


def test_adopt_refuses_a_too_large_draft(seeded):
    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "one big thing", config)
    _write_ready_drafts(
        seeded, task.id, "one big thing", draft_dict(allow=["src/a.py", "lib/b.py"])
    )

    with pytest.raises(ValueError, match="too large"):
        adopt(seeded.root, task.id, config)


def test_adopt_rides_one_documents_locked_ground_with_bounded_size(seeded):
    place(seeded.root / SPECS, "0099", _rfc_doc("0099", "S-0099/D-1", "src/newmod.py"))
    seeded.commit("fixture spec")
    source = adopted_ready_run(seeded)
    adopted = adopt(seeded.root, source, RunnerConfig())
    assert len(adopted) == 2


def test_adopt_refuses_without_a_ready_run(seeded):
    with pytest.raises(ValueError, match="nothing to adopt"):
        adopt(seeded.root, "T-9999", RunnerConfig())
    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "req", config)
    agent = ScriptedAgent(["garbage"] * config.intake.iterations)
    run_intake(seeded.root, seeded.root, task, config, StubRuntime(), agent, "digest")
    drafts_file(seeded.root, task.id).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request": "req",
                "spec": None,
                "rationale": "",
                "drafts": [draft_dict()],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ready drafting run"):
        adopt(seeded.root, task.id, config)


# The follow-up (T-0090): a READY draft run is intake's output, not the
# lane's input, and adoption is the disposal.


def test_the_lane_skips_a_ready_draft_run(seeded):
    from torve.application.lane import ready_candidates

    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "add a widget", config)
    agent = ScriptedAgent([output_for(draft_dict())])
    run_intake(seeded.root, seeded.root, task, config, StubRuntime(), agent, "digest")

    assert ready_candidates(seeded.root) == []


def test_adopt_tolerates_a_swept_state_and_disposes_of_a_kept_one(seeded):
    source = adopted_ready_run(seeded)
    state_path = naming.state_file(seeded.root, source)
    assert state_path.exists()
    adopt(seeded.root, source, RunnerConfig())
    assert not state_path.exists()  # adoption is the disposal (S-0020/D-10)

    swept = adopted_ready_run(seeded)
    naming.state_file(seeded.root, swept).unlink()  # a pre-fix reaper's sweep
    adopted = adopt(seeded.root, swept, RunnerConfig())
    assert len(adopted) == 2


# Phase 2 (T-0091): the board as the intake surface.


def test_adopt_twice_is_refused_by_the_marker(seeded):
    from torve.application.intake import adopted_file

    source = adopted_ready_run(seeded)
    adopt(seeded.root, source, RunnerConfig())
    assert adopted_file(seeded.root, source).is_file()
    with pytest.raises(ValueError, match="already adopted"):
        adopt(seeded.root, source, RunnerConfig())


def test_execution_facts_reads_queue_and_telemetry(seeded):
    config = RunnerConfig()
    assert execution_facts(seeded.root, config) == ""  # nothing to say, no block
    state = RunState(task_id="T-0300", path=naming.state_file(seeded.root, "T-0300"))
    state.transition(TaskState.CLAIMED, "x")
    state.transition(TaskState.RUNNING, "x")
    from torve.domain.states import EscalationReason

    state.escalate(EscalationReason.POISON_CEILING, "3 attempts")
    telemetry = seeded.root / ".torve" / "telemetry.jsonl"
    telemetry.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "kind": "engine",
                        "event": "blocked_dispatch",
                        "task": "T-0301",
                        "blocked_by": "T-0300",
                        "path": "src/hot.py",
                    }
                ),
                json.dumps(
                    {
                        "kind": "engine",
                        "event": "blocked_dispatch",
                        "task": "T-0302",
                        "blocked_by": "T-0300",
                        "path": "src/hot.py",
                    }
                ),
                json.dumps(
                    {"kind": "engine", "event": "lane_landed", "task": "T-0299", "sha": "a" * 40}
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    facts = execution_facts(seeded.root, config)
    assert "T-0300 (poison_ceiling)" in facts
    assert "src/hot.py (2x)" in facts
    assert "T-0299" in facts


def test_facts_reach_the_drafter_prompt(seeded):
    telemetry = seeded.root / ".torve" / "telemetry.jsonl"
    telemetry.write_text(
        json.dumps({"kind": "engine", "event": "lane_landed", "task": "T-0299", "sha": "a" * 40})
        + "\n",
        encoding="utf-8",
    )
    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "add a widget", config)
    agent = ScriptedAgent([output_for(draft_dict())])
    run_intake(seeded.root, seeded.root, task, config, StubRuntime(), agent, "digest")
    assert "Recent execution facts" in agent.prompts[0]
    assert "T-0299" in agent.prompts[0]


# ----------------------- #
# S-0027 S-0027/D-5: harness populations widen the fact feed.


def test_execution_facts_reports_harness_populations_per_tier(seeded):
    config = RunnerConfig()
    telemetry = seeded.root / ".torve" / "telemetry.jsonl"
    telemetry.write_text(
        "\n".join(
            [
                json.dumps(
                    {"kind": "engine", "event": "lane_landed", "task": "T-0299", "sha": "a" * 40}
                ),
                json.dumps(
                    {
                        "task_id": "T-0100",
                        "agent": {
                            "tier": "executor",
                            "adapter": "api",
                            "cost_usd": 0.40,
                            "image_digest": "sha256:abc",
                        },
                    }
                ),
                json.dumps(
                    {
                        "task_id": "T-0100",
                        "agent": {
                            "tier": "executor",
                            "adapter": "api",
                            "cost_usd": 0.10,
                            "image_digest": "sha256:def",
                            "broker": {"cost_usd": 0.55},
                        },
                    }
                ),
                json.dumps(
                    {
                        "kind": "review",
                        "task_id": "T-0101",
                        "unparseable": True,
                        "agent": {"tier": "reviewer", "adapter": "api"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    facts = execution_facts(seeded.root, config)
    assert "harness tier executor: 2 run(s)" in facts
    assert "$0.55 broker-measured (n=1)" in facts
    assert "$0.40 self-reported (n=1)" in facts
    assert "digest sha256:def" in facts
    assert "harness tier reviewer: 1 run(s)" in facts
    assert "unparseable reviews: 1" in facts
    assert "harness tier planner: 0 run(s)" in facts  # an unused tier stays visible
    assert "quasi-experiment" in facts


# ----------------------- #
# S-0027 S-0027/D-6: the configuration-change lint.


def test_configuration_lint_is_a_noop_for_an_ordinary_batch(tree):
    doc = document(draft_dict())
    assert lint_configuration_change(tree, doc, RunnerConfig(), StubRuntime()) == []


def test_configuration_lint_refuses_a_mixed_scope(tree):
    doc = document(draft_dict(allow=[".torve/config.yaml", "src/app.py"]))
    errors = lint_configuration_change(tree, doc, RunnerConfig(), StubRuntime())
    assert any("mixes configuration surface" in e for e in errors)


def test_configuration_lint_parses_the_committed_schema(tree):
    (tree / ".torve").mkdir()
    (tree / ".torve" / "config.yaml").write_text("tiers: not-a-mapping\n", encoding="utf-8")
    doc = document(draft_dict(allow=[".torve/config.yaml"]))
    errors = lint_configuration_change(tree, doc, RunnerConfig(), StubRuntime())
    assert any("does not parse" in e for e in errors)


def test_configuration_lint_builds_every_touched_image(tree):
    definition = tree / ".torve" / "sandbox" / "dsh"
    definition.mkdir(parents=True)
    (definition / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    doc = document(draft_dict(allow=[".torve/sandbox/dsh/Dockerfile"]))
    runtime = StubRuntime()
    errors = lint_configuration_change(tree, doc, RunnerConfig(), runtime)
    assert errors == []
    assert runtime.built == [(definition, "torve-agent:dsh")]


def test_configuration_lint_refuses_a_broken_image_build(tree):
    definition = tree / ".torve" / "sandbox" / "dsh"
    definition.mkdir(parents=True)
    (definition / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    doc = document(draft_dict(allow=[".torve/sandbox/dsh/Dockerfile"]))
    runtime = StubRuntime(build_error="no such tool")
    errors = lint_configuration_change(tree, doc, RunnerConfig(), runtime)
    assert any("failed to build" in e for e in errors)


def test_configuration_lint_refuses_when_a_configured_image_is_missing(tree):
    doc = document(draft_dict(allow=[".torve/config.yaml"]))
    runtime = StubRuntime(image_digest=None)
    errors = lint_configuration_change(tree, doc, RunnerConfig(), runtime)
    assert any("not present in this runtime" in e for e in errors)
    # T-0194: and it no longer asserts a verdict it did not ask for.
    assert not any("torve doctor is red — image" in e for e in errors)


def test_run_intake_runs_the_configuration_lint_for_a_config_scoped_batch(seeded):
    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "widen a tier variant", config)
    draft = draft_dict(allow=[".torve/config.yaml"], acceptance=["true"])
    agent = ScriptedAgent([output_for(draft)] * config.intake.iterations)
    runtime = StubRuntime(image_digest=None)  # a configured image is missing -> doctor red
    outcome = run_intake(seeded.root, seeded.root, task, config, runtime, agent, "digest")

    assert not outcome.drafts
    assert any("not present in this runtime" in e for e in outcome.lint_errors)
