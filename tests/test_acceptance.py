"""The acceptance compiler (S-0129): what a compile keeps (S-0129/D-2), what
it stages, stores and records (S-0129/D-1, S-0129/D-3, S-0129/D-5)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from hypothesis import given, settings
from hypothesis import strategies as st
from test_critic import critic_config
from test_run_loop import MockRuntime

from torve.application.acceptance import (
    ACCEPTANCE_CAP,
    COMPILE_DIR,
    PROMPT_FILE,
    SCHEMA_FILE,
    CompiledProbe,
    compile_acceptance,
    compile_prompt,
    compile_schema,
    drop_reason,
    load,
    plant,
    prompt_sha,
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

    why = drop_reason(compiled, [ROW], document, text, base)

    red = exit_code is not None and exit_code not in (0, 126, 127)
    assert (why is None) == (not broken and red), why


def test_a_probe_outside_its_directory_is_never_kept():
    for name in ("probes/x.py", "/probes/acceptance/x.py", "probes/acceptance/../../x.py"):
        compiled = CompiledProbe(**{**probe(), "file": name, "command": f"python {name}"})

        why = drop_reason(compiled, [ROW], RULE, "x", CommandRun("", 1, ""))

        assert why is not None and "no file under probes/acceptance/" in why


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

    def __init__(self, exits: dict[str, int | None] | None = None):
        self.exits = exits or {}
        self.calls: list[tuple[list[str], dict[str, str | None]]] = []

    def __call__(self, commands, probes):
        self.calls.append((list(commands), dict(probes)))
        runs = []

        for command in commands:
            name = re.search(r"probes/acceptance/(\S+)", command)
            runs.append(
                CommandRun(command, self.exits.get(name.group(1) if name else "", 1), "ran")
            )

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
    # Only what survived the written checks costs a run, all in one call.
    ((commands, given),) = base.calls
    assert [c.rsplit("/", 1)[1] for c in commands] == ["p01.py", "p02.py", "p05.py"]
    assert sorted(given) == [f"probes/acceptance/p0{n}.py" for n in (1, 2, 5)]
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


def test_a_stored_unit_never_compiles_again(repo):
    agent = Compiler([probe()])
    run, state = _unit(repo, agent)
    first = compile_acceptance(run, state, Base(), lambda usage: None)

    again = compile_acceptance(run, state, Base(), lambda usage: None)

    assert agent.calls == 1 and again == first


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
    assert len(base.calls[0][0]) == ACCEPTANCE_CAP


def test_a_seat_that_answers_nothing_keeps_nothing_and_never_asks_again(repo):
    agent = Compiler([probe()], output="I could not do it")
    run, state = _unit(repo, agent)
    base = Base()

    acceptance = compile_acceptance(run, state, base, lambda usage: None)

    assert acceptance.kept == [] and "unparseable" in acceptance.fact
    assert base.calls == []
    assert compile_acceptance(run, state, base, lambda usage: None) == acceptance
    assert agent.calls == 2  # the seat's own second ask of an unreadable answer, once


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
    assert PROMPT_FILE.name == "compile-v1.md"
