"""The acceptance compiler (S-0129): what a compile keeps (S-0129/D-2), what
it stages, stores and records (S-0129/D-1, S-0129/D-3, S-0129/D-5)."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from test_critic import critic_config
from test_run_loop import MockRuntime

from torve.application.acceptance import (
    ACCEPTANCE_CAP,
    COMPILE_DIR,
    DISPUTE_DIR,
    DISPUTE_PROMPT_FILE,
    DISPUTE_SCHEMA_FILE,
    DISPUTED_MARKER,
    PROMPT_FILE,
    SCHEMA_FILE,
    Acceptance,
    CompiledProbe,
    DisputeDocument,
    Probe,
    compile_acceptance,
    compile_prompt,
    compile_schema,
    dispute_probe,
    dispute_prompt,
    dispute_reason,
    dispute_schema,
    dispute_sha,
    drop_reason,
    load,
    plant,
    prompt_sha,
    save,
)
from torve.application.ports import AgentResult
from torve.application.runstate import RunState
from torve.base import naming
from torve.domain.task import InheritedDecision, Task
from torve.gates.evidence_package import CommandRun
from torve.gates.sabotage import TASK_ID

ROW, OTHER = "S-0900/D-1", "S-0900/D-2"
RULE = "A `reader_id` with a space is refused\n      with a 422."
DOCUMENT = f"""id: S-0900
title: Readers
summary: |
  Readers report scans.
design:
  - key: the-reader
    md: |
      {RULE} An empty one is derived from the address.
"""


def probe(
    name="p01.py", row=ROW, quote="A `reader_id` with a space is refused with a 422.", **fields
):
    return {
        "row": row,
        "quote": quote,
        "claim": "a reader_id with a space is a 422",
        "file": f"probes/acceptance/{name}",
        "command": f"uv run python probes/acceptance/{name}",
        **fields,
    }


# ....................... #
# The keep rule (S-0129/D-2).

WORDS = st.text(alphabet="abcdefgh`:-", min_size=1, max_size=6)
SPACE = st.sampled_from([" ", "  ", "\n", "\n      ", "\t"])


#: The written checks a probe can break; a probe breaking none still has to
#: be red on the start tree.
BREAKS = ("row", "quote", "text", "command")


@st.composite
def passages(draw, held: bool):
    """A document and a quote: a run of its words re-spaced as YAML folds
    them when *held*, else a run of words it does not hold."""

    words = draw(st.lists(WORDS, min_size=2, max_size=12))
    document = "".join(word + draw(SPACE) for word in words)
    start = draw(st.integers(0, len(words) - 1))
    end = draw(st.integers(start + 1, len(words)))
    run = words[start:end] if held else ["zz", *words[start:end]]

    return document, "".join(word + draw(SPACE) for word in run)


@settings(max_examples=300)
@given(
    # A third break no written check, so the base run's rule is exercised as
    # often as the rest, and a third break exactly one, so each check is seen
    # dropping a probe on its own.
    st.one_of(
        st.just(frozenset()),
        st.sampled_from([frozenset({one}) for one in BREAKS]),
        st.frozensets(st.sampled_from(BREAKS), min_size=1),
    ),
    st.data(),
    st.one_of(st.none(), st.sampled_from([0, 1, 2, 4, 42, 126, 127, 137])),
)
def test_a_probe_is_kept_exactly_when_it_is_carried_quoted_its_own_and_red(broken, data, exit_code):
    document, quote = data.draw(passages("quote" not in broken))
    compiled = CompiledProbe(
        **probe(
            row=OTHER if "row" in broken else ROW,
            quote=quote,
            command=(
                "uv run python probes/acceptance/p01.py probes/acceptance/other.py"
                if "command" in broken
                else "uv run python probes/acceptance/p01.py"
            ),
        )
    )
    base = None if exit_code is None else CommandRun(compiled.command, exit_code, "")
    text = None if "text" in broken else "print(1)\n"

    why = drop_reason(compiled, [ROW], [document], text, base)

    red = exit_code is not None and exit_code not in (0, 126, 127)
    assert (why is None) == (not broken and red), why


def test_a_probe_outside_its_directory_is_never_kept():
    for name in ("probes/x.py", "/probes/acceptance/x.py", "probes/acceptance/../../x.py"):
        compiled = CompiledProbe(**{**probe(), "file": name, "command": f"python {name}"})

        why = drop_reason(compiled, [ROW], [RULE], "x", CommandRun("", 1, ""))

        assert why is not None and "no file under probes/acceptance/" in why


#: Where a tree may hold a module, and the near misses that hold none.
HOLDS = {
    "module": lambda path: [path.with_suffix(".py")],
    "package": lambda path: [path / "__init__.py"],
    "namespace": lambda path: [path / "part.py"],
    "nested namespace": lambda path: [path / "sub" / "part.py"],
}
MISSES = {
    "nothing": lambda path: [],
    "data file": lambda path: [path.with_suffix(".txt")],
    "data folder": lambda path: [path / "rows" / "rows.csv"],
}


def traceback(line: str) -> str:
    """What Python prints for an uncaught exception ending in *line*."""

    return (
        "Traceback (most recent call last):\n"
        '  File "/work/probes/acceptance/p01.py", line 3, in <module>\n'
        "    import readers\n"
        f"{line}\n"
    )


FAILED = "AssertionError: a reader_id with a space is a 422"
#: Where the import failure sits in a base run's output, and whether it ended
#: the run: printed or caught, it did not, whatever failed after.
SHAPES = {
    "uncaught": (lambda failure: "Installed 51 packages\n" + traceback(failure), True),
    "then stdout": (lambda failure: traceback(failure) + "checked 3 readers\n", True),
    "caught, then failed": (
        lambda failure: (
            traceback(failure)
            + "\nDuring handling of the above exception, another exception occurred:\n\n"
            + traceback(FAILED)
        ),
        False,
    ),
    "printed, then failed": (lambda failure: f"{failure}\n" + traceback(FAILED), False),
    "pytest collecting": (lambda failure: collecting(failure), True),
    "pytest collecting, chained, ending on it": (
        lambda failure: collecting(ELSEWHERE, ELSEWHERE, failure),
        True,
    ),
    "pytest collecting, chained, ending elsewhere": (
        lambda failure: collecting(failure, ELSEWHERE),
        False,
    ),
    "pytest, printed, then failed": (
        lambda failure: (
            "=================================== FAILURES ===================================\n"
            "__________________________________ test_scan __________________________________\n"
            ">       assert scan() == 422\n"
            "E       AssertionError: assert 200 == 422\n"
            "----------------------------- Captured stdout call -----------------------------\n"
            f"{failure}\n"
        ),
        False,
    ),
}


#: An import failure for a module no layout writes.
ELSEWHERE = "ModuleNotFoundError: No module named 'elsewhere'"


def collecting(*lines: str) -> str:
    """What pytest prints for a test file whose import failed with each of
    *lines* in turn, each while handling the one before."""

    link = (
        "\nDuring handling of the above exception, another exception occurred:\n"
        "/usr/lib/python3.14/importlib/__init__.py:88: in import_module\n"
        "    return _bootstrap._gcd_import(name[level:], package, level)\n"
    )
    raised = [
        f"probes/acceptance/p01.py:{n + 2}: in <module>\n    import x\nE   {line}\n"
        for n, line in enumerate(lines)
    ]

    return (
        "==================================== ERRORS ====================================\n"
        "_____________________ ERROR collecting probes/acceptance/p01.py _____________________\n"
        "ImportError while importing test module '/work/probes/acceptance/p01.py'.\n"
        "Traceback:\n"
        + link.join(raised)
        + "=========================== short test summary info ============================\n"
        "ERROR probes/acceptance/p01.py\n"
    )


@settings(max_examples=80, deadline=None)
@given(
    st.lists(st.sampled_from(["readers", "scan", "api", "v1"]), min_size=1, max_size=3),
    st.sampled_from(sorted({**HOLDS, **MISSES})),
    st.sampled_from(["", "src"]),
    st.sampled_from(["ModuleNotFoundError", "ImportError"]),
    st.sampled_from(sorted(SHAPES)),
)
def test_a_probe_that_cannot_import_a_module_the_start_tree_holds_is_never_kept(
    parts, layout, under, error, shape
):
    """S-0129/A-3: a red base run that an import of a module the start tree
    holds ended, as a module, a package or a namespace package, at its root
    or under `src/`, is a probe that cannot see the project; one that could
    not import what the tree lacks is red for want of the unit's work, and
    one that printed or caught the failure and then failed otherwise is red
    for that."""

    name = ".".join(parts)
    compiled = CompiledProbe(**probe())
    output, ended = SHAPES[shape]
    base = CommandRun(compiled.command, 1, output(f"{error}: No module named '{name}'"))

    with tempfile.TemporaryDirectory() as folder:
        tree = Path(folder)

        for file in {**HOLDS, **MISSES}[layout]((tree / under).joinpath(*parts)):
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("", encoding="utf-8")

        why = drop_reason(compiled, [ROW], [RULE], "x", base, tree)

    if layout in HOLDS and error == "ModuleNotFoundError" and ended:
        assert (
            why == f"it cannot import {name}, which the start tree holds, so it fails on every tree"
        )
    else:
        assert why is None, why


# ....................... #
# The compile (S-0129/D-1).


class Compiler:
    """Writes the probes it is given into its copy and answers with them, or
    answers *output* as given."""

    kind = "harness"

    def __init__(self, probes: list[dict], files: dict[str, str] | None = None, output=None):
        self.probes = probes
        self.files = files
        self.output = output
        self.calls = 0
        self.seen: dict[str, object] = {}

    def run(self, ctx):
        self.calls += 1
        compile_dir = ctx.workspace / COMPILE_DIR
        self.seen = {
            "system": ctx.system,
            "prompt": ctx.prompt,
            "staged": sorted(
                str(path.relative_to(compile_dir))
                for path in compile_dir.rglob("*")
                if path.is_file()
            ),
            "scrubbed": not (ctx.workspace / ".torve" / "context").exists(),
        }

        for one in self.probes:
            path = ctx.workspace / one["file"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                (self.files or {}).get(one["file"], f"# {one['claim']}\n"), encoding="utf-8"
            )

        if self.output is not None:
            return AgentResult(exit_code=0, output=self.output)

        return AgentResult(
            exit_code=0,
            output="compiled\n" + json.dumps({"schema": "torve.compile/v1", "probes": self.probes}),
        )


class Base:
    """A scripted run over the start tree: each command's exit by its probe's
    name, 1 when unnamed."""

    def __init__(
        self, exits: dict[str, int | None] | None = None, tails: dict[str, str] | None = None
    ):
        self.exits = exits or {}
        self.tails = tails or {}
        self.calls: list[tuple[list[str], dict[str, str | None]]] = []

    def __call__(self, commands, probes):
        self.calls.append((list(commands), dict(probes)))
        runs = []

        for command in commands:
            name = re.search(r"probes/acceptance/(\S+)", command)
            key = name.group(1) if name else ""
            runs.append(CommandRun(command, self.exits.get(key, 1), self.tails.get(key, "ran")))

        return runs


def _unit(repo, agent) -> tuple[SimpleNamespace, RunState]:
    from torve.adapters.workspace.git import GitWorkspace

    repo.seed()
    repo.git("checkout", "-q", "main")
    repo.write(".torve/specs/S-0900/document.yaml", DOCUMENT)
    repo.write(".torve/specs/S-0900/decisions.yaml", "decisions: []\n")
    repo.commit("the document")
    worktree = GitWorkspace(repo.root).create(TASK_ID, "main")
    # An executor channel the compiler must not read (S-0122/D-2).
    (worktree / ".torve" / "context").mkdir(parents=True)
    (worktree / ".torve" / "context" / "attempts.json").write_text("{}\n", encoding="utf-8")
    task = Task(
        id=TASK_ID,
        spec="S-0900",
        intent="Readers report scans.",
        decisions=[InheritedDecision(id=ROW, grade="ASSUMED", text="readers are validated")],
    )
    run = SimpleNamespace(
        root=repo.root,
        worktree=worktree,
        task=task,
        config=critic_config(compile=True),
        deps=SimpleNamespace(taken=None, runtime=MockRuntime(), broker=None, review_agent=agent),
        broker_handle=None,
        manifest_file=worktree / ".torve" / "gates.yaml",
        image_digest=None,
    )
    state = RunState(task_id=TASK_ID, path=naming.state_file(repo.root, TASK_ID))

    return run, state


def test_a_compile_keeps_the_red_probes_and_records_why_it_dropped_the_rest(repo):
    probes = [
        probe("p01.py"),
        probe("p02.py", claim="green already"),
        probe("p03.py", quote="A reader_id may hold anything."),
        probe("p04.py", row=OTHER),
        probe("p05.py", claim="no interpreter"),
        probe("p06.py", command="uv run python probes/acceptance/p01.py"),
    ]
    agent = Compiler(probes)
    run, state = _unit(repo, agent)
    base = Base({"p02.py": 0, "p05.py": 127})

    acceptance = compile_acceptance(run, state, base, lambda usage: 0.25)

    assert [p.file for p in acceptance.kept] == ["probes/acceptance/p01.py"]
    assert acceptance.kept[0].id == "acceptance:1" and acceptance.kept[0].base_exit == 1
    assert acceptance.kept[0].text == "# a reader_id with a space is a 422\n"
    assert {d["file"].rsplit("/", 1)[1]: d["why"] for d in acceptance.dropped} == {
        "p02.py": "it passes on the start tree, so it pins nothing the unit adds",
        "p03.py": "its quote is not in the document",
        "p04.py": f"row {OTHER} is not one the contract carries",
        "p05.py": "it exits 127 on the start tree, so it cannot run",
        "p06.py": "its command does not run its own file alone",
    }
    # Only what survived the written checks costs a run, each alone with its
    # own file, as every later re-run hands it.
    assert [(commands, sorted(given)) for commands, given in base.calls] == [
        ([f"uv run python probes/acceptance/p0{n}.py"], [f"probes/acceptance/p0{n}.py"])
        for n in (1, 2, 5)
    ]
    # Its inputs, and nothing of an executor's (S-0122/D-2).
    assert agent.seen["system"] == compile_prompt()
    assert ROW in str(agent.seen["prompt"])
    assert agent.seen["staged"] == [
        "compile.schema.json",
        "document/decisions.yaml",
        "document/document.yaml",
        "rows.yaml",
    ]
    assert agent.seen["scrubbed"]
    # Stored, recorded and on the unit's history (S-0129/D-3, S-0129/D-5).
    assert load(repo.root, TASK_ID) == acceptance
    assert acceptance.spend_usd == 0.25
    (record,) = [
        json.loads(line)
        for line in (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()
        if json.loads(line).get("mode") == "compile"
    ]
    assert record["trigger"] == "compile" and record["prompt_sha"] == prompt_sha()
    assert [p["file"] for p in record["kept"]] == ["probes/acceptance/p01.py"]
    assert "text" not in record["kept"][0] and len(record["dropped"]) == 5
    assert any("acceptance compiled" in str(e.get("fact")) for e in state.history)


def test_a_probe_that_cannot_see_the_project_is_not_kept(repo):
    """S-0129/A-3: the compile reads the unit's start tree for the module a
    red base run could not import, so a probe that cannot import the
    project is dropped and one whose run ended on a module the unit adds is
    kept, whatever its output named before."""

    run, state = _unit(repo, Compiler([probe("p01.py"), probe("p02.py")]))
    (run.worktree / "readers").mkdir()
    (run.worktree / "readers" / "__init__.py").write_text("", encoding="utf-8")
    held = traceback("ModuleNotFoundError: No module named 'readers'")
    base = Base(
        tails={
            "p01.py": held,
            # Retried with the root on its path, it fails on what the unit adds.
            "p02.py": held
            + "\nDuring handling of the above exception, another exception occurred:\n\n"
            + traceback("ModuleNotFoundError: No module named 'readers.scan'"),
        }
    )

    acceptance = compile_acceptance(run, state, base, lambda usage: None)

    assert [p.file for p in acceptance.kept] == ["probes/acceptance/p02.py"]
    assert [(d["file"], d["why"]) for d in acceptance.dropped] == [
        (
            "probes/acceptance/p01.py",
            "it cannot import readers, which the start tree holds, so it fails on every tree",
        )
    ]


def test_a_stored_unit_never_compiles_again(repo):
    agent = Compiler([probe()])
    run, state = _unit(repo, agent)
    first = compile_acceptance(run, state, Base(), lambda usage: None)

    again = compile_acceptance(run, state, Base(), lambda usage: None)

    assert agent.calls == 1 and again == first


def test_a_store_from_another_document_or_other_rows_compiles_again(repo):
    """S-0129/D-7: an amended document or a refreshed contract's rows compile
    again and replace the store; the same document and rows reuse it."""

    agent = Compiler([probe()])
    run, state = _unit(repo, agent)
    compile_acceptance(run, state, Base(), lambda usage: None)
    repo.write(".torve/specs/S-0900/document.yaml", DOCUMENT + "risks: |\n  Readers drift.\n")

    amended = compile_acceptance(run, state, Base(), lambda usage: None)

    assert agent.calls == 2 and load(repo.root, TASK_ID) == amended
    run.task = run.task.model_copy(
        update={"decisions": [InheritedDecision(id=ROW, grade="ASSUMED", text="readers refuse")]}
    )

    refreshed = compile_acceptance(run, state, Base(), lambda usage: None)

    assert agent.calls == 3 and refreshed.source != amended.source
    assert compile_acceptance(run, state, Base(), lambda usage: None) == refreshed
    assert agent.calls == 3


def test_a_stale_store_is_gone_before_a_seat_that_fails(repo):
    """S-0129/D-7: a seat that cannot run after an amendment leaves the unit
    without probes, never with the old document's."""

    run, state = _unit(repo, Compiler([probe()]))
    compile_acceptance(run, state, Base(), lambda usage: None)
    repo.write(".torve/specs/S-0900/document.yaml", DOCUMENT + "risks: |\n  Readers drift.\n")

    class Broken:
        kind = "harness"

        def run(self, ctx):
            raise RuntimeError("no sandbox")

    run.deps.review_agent = Broken()

    acceptance = compile_acceptance(run, state, Base(), lambda usage: None)

    assert acceptance.kept == [] and load(repo.root, TASK_ID) is None


def test_a_compile_keeps_at_most_the_cap_in_the_seats_order(repo):
    probes = [probe(f"p{n:02d}.py") for n in range(ACCEPTANCE_CAP + 2)]
    run, state = _unit(repo, Compiler(probes))
    base = Base()

    acceptance = compile_acceptance(run, state, base, lambda usage: None)

    assert len(acceptance.kept) == ACCEPTANCE_CAP
    assert acceptance.kept[-1].file == f"probes/acceptance/p{ACCEPTANCE_CAP - 1:02d}.py"
    assert [d["why"] for d in acceptance.dropped] == [
        f"the cap of {ACCEPTANCE_CAP} was reached"
    ] * 2
    assert len(base.calls) == ACCEPTANCE_CAP


def test_a_seat_that_answers_nothing_keeps_nothing_and_is_asked_again(repo):
    """S-0129/D-2, S-0129/D-3: a seat with no readable answer keeps nothing
    and stores nothing, so the next attempt asks again; only an answer is
    paid for once."""

    agent = Compiler([probe()], output="I could not do it")
    run, state = _unit(repo, agent)
    base = Base()

    acceptance = compile_acceptance(run, state, base, lambda usage: None)

    assert acceptance.kept == [] and "unparseable" in acceptance.fact
    assert base.calls == [] and load(repo.root, TASK_ID) is None
    asked = agent.calls  # the seat's own second ask of an unreadable answer included

    compile_acceptance(run, state, base, lambda usage: None)

    assert agent.calls == 2 * asked


def test_a_command_that_is_more_than_one_simple_command_is_dropped():
    """S-0129/D-2: a command another command can fail before the probe runs
    fails on every tree, a correct one included."""

    red = CommandRun("", 1, "")

    for command in (
        "false && uv run python probes/acceptance/p01.py",
        "uv sync; uv run python probes/acceptance/p01.py",
        "uv run python probes/acceptance/p01.py | tail -1",
        "uv run python probes/acceptance/p01.py > /dev/null",
        "uv run python probes/acceptance/p01.py $(true)",
    ):
        compiled = CompiledProbe(**probe(command=command))

        assert drop_reason(compiled, [ROW], [RULE], "x", red) == (
            "its command is not one simple command"
        ), command

    simple = CompiledProbe(**probe(command="uv run pytest -q probes/acceptance/p01.py -k 'a or b'"))

    assert drop_reason(simple, [ROW], [RULE], "x", red) is None


def test_the_stored_probes_are_planted_at_their_paths(repo, tmp_path: Path):
    run, state = _unit(repo, Compiler([probe()]))
    compile_acceptance(run, state, Base(), lambda usage: None)
    tree = tmp_path / "tree"

    planted = plant(repo.root, TASK_ID, tree)

    assert planted == ["probes/acceptance/p01.py"]
    assert (tree / planted[0]).read_text() == "# a reader_id with a space is a 422\n"
    assert plant(repo.root, "T-9999", tree) == []


def test_the_compile_prompt_is_frozen_with_its_own_schema():
    assert compile_prompt().startswith("You are the acceptance compiler for a coding engine.")
    assert ".torve/compile/compile.schema.json" in compile_prompt()
    assert json.loads(SCHEMA_FILE.read_text(encoding="utf-8")) == compile_schema()
    assert PROMPT_FILE.name == "compile-v2.md"


def test_a_seat_that_fails_to_run_leaves_the_unit_without_probes_and_no_store(repo):
    """S-0129/D-2: a compile seat whose sandbox would not open runs the unit
    without probes, and stores nothing, so the next attempt compiles again."""

    class Broken:
        kind = "harness"

        def run(self, ctx):
            raise RuntimeError("no sandbox")

    run, state = _unit(repo, Broken())

    acceptance = compile_acceptance(run, state, Base(), lambda usage: None)

    assert acceptance.kept == [] and "the compile seat failed" in acceptance.fact
    assert load(repo.root, TASK_ID) is None


def test_a_base_run_that_cannot_open_drops_every_probe(repo):
    """S-0129/D-2: probes whose run on the start tree never happened measured
    nothing, so none is kept, each with the reason."""

    run, state = _unit(repo, Compiler([probe()]))

    def broken(commands, probes):
        raise OSError("checkout would not open")

    acceptance = compile_acceptance(run, state, broken, lambda usage: None)

    assert acceptance.kept == []
    assert acceptance.dropped[0]["why"].startswith("its run on the start tree measured nothing")


def test_a_quote_must_sit_within_one_of_the_documents_files():
    """S-0129/D-2: a quote spanning the end of one file and the start of the
    next is in none of them."""

    compiled = CompiledProbe(**probe(quote="the end. The start"))
    red = CommandRun(compiled.command, 1, "")

    assert drop_reason(compiled, [ROW], ["at the end.", "The start of it."], "x", red) == (
        "its quote is not in the document"
    )
    assert drop_reason(compiled, [ROW], ["at the end. The start"], "x", red) is None


def test_a_provider_error_is_not_stored_so_the_next_attempt_asks_again(repo):
    """S-0129/D-2: a seat whose answer is the provider's error kept nothing
    and is asked again; it is not the unit's compile."""

    error = json.dumps({"type": "result", "is_error": True, "result": "API Error: 529 overloaded"})
    agent = Compiler([probe()], output=error)
    run, state = _unit(repo, agent)

    acceptance = compile_acceptance(run, state, Base(), lambda usage: None)

    assert acceptance.kept == [] and "gave no answer" in acceptance.fact
    assert load(repo.root, TASK_ID) is None


def test_planting_never_writes_through_a_link(repo, tmp_path: Path):
    """S-0129/D-3: a link on a probe's path would carry the write out of the
    worktree, so that probe is not planted."""

    run, state = _unit(repo, Compiler([probe()]))
    compile_acceptance(run, state, Base(), lambda usage: None)
    tree, outside = tmp_path / "tree", tmp_path / "outside"
    tree.mkdir()
    outside.mkdir()
    (tree / "probes").symlink_to(outside)

    assert plant(repo.root, TASK_ID, tree) == []
    assert list(outside.iterdir()) == []


# ....................... #
# The dispute (S-0129/D-9).

#: The passage a counter-probe pins: the document states it, and the
#: disputed probe below asserts something else of the same reader.
PASSAGE = "An empty one is derived from the address."


def _kept(number: int, quote: str = "A `reader_id` with a space is refused with a 422.") -> Probe:
    return Probe(
        id=f"acceptance:{number}",
        row=ROW,
        quote=quote,
        claim="a reader_id with a space is a 422",
        file=f"probes/acceptance/p0{number}.py",
        command=f"uv run python probes/acceptance/p0{number}.py",
        text=f"raise SystemExit({number})\n",
        base_exit=1,
    )


#: The unit's store: a probe that stands, and the one put to the dispute.
STANDING, DISPUTED = _kept(1), _kept(2)


def answer(**fields) -> dict:
    return {
        "verdict": "disputed",
        "reason": "the probe wants a 422 for an empty reader_id, which the document derives",
        "quote": PASSAGE,
        "file": "probes/acceptance/c01.py",
        "command": "uv run python probes/acceptance/c01.py",
        **fields,
    }


#: What a dispute can get wrong, each on its own: an upheld verdict, a
#: passage the document lacks, no file, a compound command, a kept probe's
#: file, a counter-probe that fails on the candidate, one that is not red on
#: the start tree, and one red there for want of a module that tree holds.
FLAWS = ("verdict", "quote", "text", "command", "file", "candidate", "start", "held")

#: The start tree's files, as a dispute lists them from its commit.
START_FILES = frozenset({"src/app.py", "README.md"})


@settings(max_examples=300)
@given(
    st.one_of(
        st.just(frozenset()),
        st.sampled_from([frozenset({one}) for one in FLAWS]),
        st.frozensets(st.sampled_from(FLAWS), min_size=1),
    ),
    st.data(),
)
def test_a_dispute_is_kept_exactly_when_its_passage_counter_probe_and_runs_hold(flaws, data):
    """S-0129/D-9: whatever the seat argued, the engine keeps a dispute only
    when the seat disputed, its passage is in the document, its counter-probe
    obeys a compiled probe's written rules in a file no kept probe holds, and
    it passed on the candidate and executed red on the start tree."""

    document, quote = data.draw(passages("quote" not in flaws))
    fields = answer(quote=quote)

    if "verdict" in flaws:
        fields["verdict"] = "upheld"

    if "command" in flaws:
        fields["command"] = "uv run python probes/acceptance/c01.py && true"

    if "file" in flaws:
        fields["file"] = STANDING.file
        fields["command"] = STANDING.command

    candidate_exit = (
        data.draw(st.sampled_from([None, 1, 2, 126, 127])) if "candidate" in flaws else 0
    )
    # A module the work adds is missing on the start tree, which is a red
    # there; one the start tree holds is not.
    tail = (
        traceback("ModuleNotFoundError: No module named 'app'")
        if "held" in flaws
        else data.draw(
            st.sampled_from(["", traceback("ModuleNotFoundError: No module named 'readers'")])
        )
    )
    start = (
        data.draw(
            st.one_of(
                st.none(), st.sampled_from([0, 126, 127]).map(lambda e: CommandRun("", e, tail))
            )
        )
        if "start" in flaws
        else CommandRun("", data.draw(st.sampled_from([1, 2, 4, 42])), tail)
    )
    stored = Acceptance(kept=[STANDING, DISPUTED], start="a1b2")

    why = dispute_reason(
        DisputeDocument.model_validate(fields),
        DISPUTED,
        stored,
        [document],
        None if "text" in flaws else "print(1)\n",
        CommandRun("", candidate_exit, ""),
        start,
        START_FILES,
    )

    assert (why is None) == (not flaws), why


class Arbiter:
    """Writes the counter-probe its answer names into its copy and answers,
    recording what it was staged; or raises, as a seat that cannot open."""

    kind = "harness"

    def __init__(self, fields: dict | None = None, fail: bool = False):
        self.fields = fields if fields is not None else answer()
        self.fail = fail
        self.calls = 0
        self.seen: dict[str, object] = {}

    def run(self, ctx):
        self.calls += 1

        if self.fail:
            raise RuntimeError("the sandbox would not open")

        staged = ctx.workspace / DISPUTE_DIR
        self.seen = {
            "system": ctx.system,
            "prompt": ctx.prompt,
            "staged": sorted(
                str(path.relative_to(staged)) for path in staged.rglob("*") if path.is_file()
            ),
            "probe": (ctx.workspace / DISPUTED.file).read_text(encoding="utf-8"),
            "scrubbed": not (ctx.workspace / ".torve" / "context").exists(),
        }

        if self.fields.get("file"):
            path = ctx.workspace / self.fields["file"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# the address derives it\n", encoding="utf-8")

        return AgentResult(
            exit_code=0,
            output="judged\n" + json.dumps({"schema": "torve.dispute/v1", **self.fields}),
        )


def _disputing(repo, agent, start: bool = True):
    """A unit whose store holds the two probes and, when *start*, records the
    repository's head as the tree it was compiled over."""

    import subprocess

    run, state = _unit(repo, agent)
    head = subprocess.run(
        ["git", "-C", str(repo.root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    save(
        repo.root,
        TASK_ID,
        Acceptance(kept=[STANDING, DISPUTED], start=head if start else None, source="s"),
    )
    runs = [CommandRun(DISPUTED.command, 1, "AssertionError: 422 != 200")] * 3

    return run, state, runs


def _records(repo, mode: str) -> list[dict]:
    rows = [
        json.loads(line)
        for line in (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]

    return [row for row in rows if row.get("mode") == mode]


def test_a_kept_dispute_puts_its_counter_probe_in_the_probe_s_place(repo, tmp_path: Path):
    """S-0129/D-9: a kept dispute withdraws the probe from the unit's store
    and keeps the counter-probe at its place under a new id, so planting and
    every later decision read the counter-probe; the call is staged the
    document and the probe, recorded as a dispute review, priced as unit
    spend and left on the unit's history."""

    agent = Arbiter()
    run, state, runs = _disputing(repo, agent)
    # The counter-probe imports a module the unit adds: the candidate holds
    # it, the start tree does not, so its import failure there is the red.
    (run.worktree / "readers.py").write_text("", encoding="utf-8")
    (run.worktree / DISPUTED.file).parent.mkdir(parents=True, exist_ok=True)
    (run.worktree / DISPUTED.file).write_text(DISPUTED.text, encoding="utf-8")
    candidate = Base({"c01.py": 0})
    start = Base(
        {"c01.py": 1}, {"c01.py": traceback("ModuleNotFoundError: No module named 'readers'")}
    )
    spent: list[dict] = []

    settled = dispute_probe(
        run, state, DISPUTED, runs, candidate, start, lambda usage: spent.append(usage) or 0.1
    )

    assert settled.kept and settled.counter is not None
    assert (settled.counter.id, settled.counter.row, settled.counter.base_exit) == (
        "acceptance:3",
        ROW,
        1,
    )
    stored = load(repo.root, TASK_ID)
    assert stored is not None
    assert [one.id for one in stored.kept] == ["acceptance:1", "acceptance:3"]
    assert stored.kept[1].text == "# the address derives it\n"
    (withdrawn,) = stored.withdrawn
    assert withdrawn["id"] == DISPUTED.id and withdrawn["counter"] == "acceptance:3"
    assert withdrawn["review_id"] == settled.review_id and "text" not in withdrawn
    # Each run alone with the counter-probe's file, the start tree only after
    # the candidate passed it.
    assert (
        candidate.calls
        == start.calls
        == [
            (
                ["uv run python probes/acceptance/c01.py"],
                {"probes/acceptance/c01.py": stored.kept[1].text},
            )
        ]
    )
    assert agent.seen["system"] == dispute_prompt()
    assert DISPUTED.id in str(agent.seen["prompt"]) and ROW in str(agent.seen["prompt"])
    assert agent.seen["staged"] == [
        "dispute.schema.json",
        "document/decisions.yaml",
        "document/document.yaml",
        "probe.yaml",
    ]
    assert agent.seen["probe"] == DISPUTED.text and agent.seen["scrubbed"]
    (record,) = _records(repo, "dispute")
    assert record["trigger"] == "dispute" and record["prompt_sha"] == dispute_sha()
    assert (record["probe"], record["kept"], record["counter"]["id"]) == (
        DISPUTED.id,
        True,
        "acceptance:3",
    )
    assert len(spent) == 1
    assert any(str(e.get("fact")).startswith(DISPUTED_MARKER) for e in state.history)
    # The candidate's copy follows the store: the counter-probe in, the
    # withdrawn probe out, so the session's next turn meets the counter-probe.
    assert (run.worktree / "probes/acceptance/c01.py").is_file()
    assert not (run.worktree / DISPUTED.file).exists()
    tree = tmp_path / "tree"
    tree.mkdir()
    assert plant(repo.root, TASK_ID, tree) == [STANDING.file, "probes/acceptance/c01.py"]


@pytest.mark.parametrize(
    ("fields", "start_run", "why", "start_runs"),
    [
        (answer(verdict="upheld"), (1, ""), "the seat upheld the probe", 0),
        (answer(), (0, ""), "it passes on the start tree, so it pins nothing the unit adds", 1),
        (
            answer(),
            (1, traceback("ModuleNotFoundError: No module named 'app'")),
            "it cannot import app, which the start tree holds, so it fails on every tree",
            1,
        ),
    ],
    ids=["upheld", "green-on-start", "held-module"],
)
def test_a_dispute_the_engine_refuses_leaves_the_probe_standing(
    repo, fields, start_run, why, start_runs
):
    """S-0129/D-9: an upheld verdict runs nothing; a counter-probe that also
    passes on the start tree, or fails there only for want of a module that
    tree holds, is refused after both runs. The store is as it was, and the
    answered dispute is paid for and recorded with why it was not kept."""

    run, state, runs = _disputing(repo, Arbiter(fields))
    candidate = Base({"c01.py": 0})
    start = Base({"c01.py": start_run[0]}, {"c01.py": start_run[1]})
    before = load(repo.root, TASK_ID)
    spent: list[dict] = []

    settled = dispute_probe(
        run, state, DISPUTED, runs, candidate, start, lambda usage: spent.append(usage)
    )

    assert not settled.kept and settled.why == why
    assert load(repo.root, TASK_ID) == before
    assert len(start.calls) == start_runs and len(spent) == 1
    assert _records(repo, "dispute")[-1]["why"] == why


def test_a_dispute_no_start_tree_could_keep_asks_no_seat(repo):
    """S-0129/D-9: a store written before it recorded its start tree can keep
    no dispute, so none is asked or paid for, and the probe blocks."""

    agent = Arbiter()
    run, state, runs = _disputing(repo, agent, start=False)
    spent: list[dict] = []

    settled = dispute_probe(
        run, state, DISPUTED, runs, Base({"c01.py": 0}), Base(), lambda usage: spent.append(usage)
    )

    assert not settled.kept and "no start tree" in settled.why
    assert agent.calls == 0 and spent == []


def test_a_dispute_seat_that_cannot_run_keeps_nothing_and_records_nothing(repo):
    run, state, runs = _disputing(repo, Arbiter(fail=True))
    before = load(repo.root, TASK_ID)

    settled = dispute_probe(run, state, DISPUTED, runs, Base(), Base(), lambda usage: None)

    assert not settled.kept and "the dispute seat failed" in settled.why
    assert load(repo.root, TASK_ID) == before
    assert not (repo.root / ".torve" / "telemetry.jsonl").exists() or not _records(repo, "dispute")


def test_the_dispute_prompt_is_frozen_with_its_own_schema():
    assert dispute_prompt().startswith("You are the arbiter of one acceptance probe")
    assert ".torve/dispute/dispute.schema.json" in dispute_prompt()
    assert json.loads(DISPUTE_SCHEMA_FILE.read_text(encoding="utf-8")) == dispute_schema()
    assert DISPUTE_PROMPT_FILE.name == "dispute-v1.md"
