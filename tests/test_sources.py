"""The filed sources (S-0060/D-1, S-0060/D-2, S-0060/D-6, S-0060/D-7): one
file per source, identified by where it sits, carrying no rows, minted and
read by the verb that owns it, and recorded with its own kind."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from torve.cli.main import app
from torve.config.sources import (
    check_sources,
    cited_sources,
    load_sources,
    schema_header,
    source_file,
)
from torve.config.spec import SpecError

runner = CliRunner()

AUDIT = {
    "id": "audit/soc2-2026",
    "kind": "audit",
    "title": "SOC 2 control gap in session expiry",
    "ref": "https://example.invalid/audit/42",
}


def write_source(root: Path, kind: str, slug: str, body: dict, *, header: bool = True) -> Path:
    path = root / ".torve" / "sources" / kind / f"{slug}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{schema_header()}\n" if header else ""
    path.write_text(line + yaml.safe_dump(body, sort_keys=False), encoding="utf-8")

    return path


# ----------------------- #


def test_a_source_is_identified_by_where_it_sits(tmp_path):
    """S-0060/D-1: the file's `id` must equal `<kind>/<slug>` from its own
    path, and the directory is the kind."""

    write_source(tmp_path, "audit", "soc2-2026", AUDIT)

    found = load_sources(tmp_path)

    assert list(found) == ["audit/soc2-2026"]
    assert found["audit/soc2-2026"].title == AUDIT["title"]
    assert source_file(tmp_path, "audit/soc2-2026").is_file()

    write_source(tmp_path, "incident", "lease-storm", {**AUDIT, "id": "incident/lease-storm"})

    with pytest.raises(SpecError) as caught:
        load_sources(tmp_path)

    assert "says `kind: audit` under incident/" in "; ".join(caught.value.problems)


def test_a_moved_file_is_a_refusal_not_a_rename(tmp_path):
    """The identifier is the path, so a file that says otherwise is caught
    rather than silently answering to two names."""

    write_source(tmp_path, "audit", "moved", AUDIT)

    with pytest.raises(SpecError) as caught:
        load_sources(tmp_path)

    problems = "; ".join(caught.value.problems)

    assert "says `id: audit/soc2-2026` and sits at audit/moved" in problems


def test_a_source_carries_no_decisions(tmp_path):
    """S-0060/D-2: rows that stand are the corpus's alone — the model has no
    field for one, so a file that grows rules is refused rather than becoming
    a second thing to inherit from."""

    write_source(tmp_path, "audit", "soc2-2026", {**AUDIT, "decisions": [{"id": "D-1"}]})

    with pytest.raises(SpecError) as caught:
        load_sources(tmp_path)

    assert "decisions" in "; ".join(caught.value.problems)


def test_a_directory_no_kind_names_is_refused(tmp_path):
    """S-0060/D-1: `specification` is not a directory here — a document is a
    source without being filed as one."""

    write_source(tmp_path, "specification", "s-0001", {**AUDIT, "id": "specification/s-0001"})

    problems, _ = check_sources(tmp_path)

    assert any("no source kind is named 'specification'" in one for one in problems)


def test_two_files_claiming_one_identifier_are_a_problem(tmp_path):
    """One identifier, one file — the whole reason the path is the id."""

    write_source(tmp_path, "audit", "soc2-2026", AUDIT)
    other = tmp_path / ".torve" / "sources" / "audit" / "duplicate.yaml"
    other.write_text(
        f"{schema_header()}\n" + yaml.safe_dump(AUDIT, sort_keys=False), encoding="utf-8"
    )

    problems, _ = check_sources(tmp_path)

    assert any("sits at audit/duplicate" in one for one in problems)


def test_a_file_without_its_schema_line_warns(tmp_path):
    """S-0057/D-4: every minted YAML names its schema; a file that does not
    is a warning `torve init` clears, never a refusal."""

    write_source(tmp_path, "audit", "soc2-2026", AUDIT, header=False)

    problems, warnings = check_sources(tmp_path)

    assert problems == []
    assert any("does not open with its schema line" in one for one in warnings)


# ----------------------- #


def test_the_verb_mints_reads_and_refuses(tmp_path):
    """S-0060/D-6: `new` writes the file with its schema line, `list` proves
    the directory, and `show` reads one whole."""

    made = runner.invoke(
        app,
        ["source", "new", "audit", "soc2-2026", "--title", "A gap", "--root", str(tmp_path)],
    )

    assert made.exit_code == 0, made.output

    path = source_file(tmp_path, "audit/soc2-2026")
    text = path.read_text(encoding="utf-8")

    assert text.startswith(f"{schema_header()}\n")
    assert yaml.safe_load(text)["id"] == "audit/soc2-2026"

    listed = runner.invoke(app, ["source", "list", "--root", str(tmp_path)])

    assert listed.exit_code == 0, listed.output
    assert "audit/soc2-2026" in listed.output

    shown = runner.invoke(app, ["source", "show", "audit/soc2-2026", "--root", str(tmp_path)])

    assert shown.exit_code == 0 and "A gap" in shown.output

    again = runner.invoke(app, ["source", "new", "audit", "soc2-2026", "--root", str(tmp_path)])

    assert again.exit_code != 0 and "already exists" in again.output


def test_the_verb_refuses_an_unfiled_kind_and_a_bad_slug(tmp_path):
    """A document is read with `torve spec show`, so `specification` is not a
    kind this verb mints."""

    unfiled = runner.invoke(
        app, ["source", "new", "specification", "s-0001", "--root", str(tmp_path)]
    )

    assert unfiled.exit_code != 0 and "not a filed source kind" in unfiled.output

    shouting = runner.invoke(app, ["source", "new", "audit", "SOC2", "--root", str(tmp_path)])

    assert shouting.exit_code != 0 and "is not a slug" in shouting.output


def test_list_refuses_a_directory_that_does_not_load(tmp_path):
    """S-0060/I-1: `torve source list` is what proves the directory, so a
    file that does not load makes it exit non-zero."""

    write_source(tmp_path, "audit", "moved", AUDIT)

    listed = runner.invoke(app, ["source", "list", "--root", str(tmp_path)])

    assert listed.exit_code != 0
    assert "sits at audit/moved" in listed.output


def test_show_names_the_tasks_whose_contracts_cite_it(tmp_path):
    """S-0060/D-6: `show` gives the row's side of the link, the way `spec
    cites` does for a decision."""

    write_source(tmp_path, "audit", "soc2-2026", AUDIT)
    contract = tmp_path / ".torve" / "tasks" / "T-0001" / "contract.yaml"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(
        yaml.safe_dump(
            {"schema_version": 2, "id": "T-0001", "source": "audit/soc2-2026", "decisions": []},
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    assert cited_sources(tmp_path) == {"T-0001": "audit/soc2-2026"}

    shown = runner.invoke(
        app, ["source", "show", "audit/soc2-2026", "--root", str(tmp_path), "--format", "json"]
    )

    assert shown.exit_code == 0, shown.output

    import json

    assert json.loads(shown.output)["cited_by"] == ["T-0001"]


def test_list_check_resolves_every_source_a_contract_names(tmp_path):
    """S-0060/I-2: an identifier a contract names must resolve to a document
    or to a source file; `--check` is what says so."""

    contract = tmp_path / ".torve" / "tasks" / "T-0001" / "contract.yaml"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(
        yaml.safe_dump(
            {"schema_version": 2, "id": "T-0001", "source": "audit/soc2-2026", "decisions": []},
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    missing = runner.invoke(app, ["source", "list", "--check", "--root", str(tmp_path)])

    assert missing.exit_code != 0
    assert "T-0001 names audit/soc2-2026, which nothing in this tree holds" in missing.output

    write_source(tmp_path, "audit", "soc2-2026", AUDIT)
    resolved = runner.invoke(app, ["source", "list", "--check", "--root", str(tmp_path)])

    assert resolved.exit_code == 0, resolved.output


def test_show_resolves_a_document_because_the_grammar_admits_one(tmp_path):
    """S-0060/D-1: `S-NNNN` and `<kind>/<slug>` are one grammar, so they have
    one resolver — a document is a source that is not filed as one, and asking
    `show` for it answers rather than redirecting."""

    from test_decisions import corpus, document

    corpus(tmp_path, **{"0059": document("0059", [("S-0059/D-1", "LOCKED", "A rule.", "`src/**`")])})

    shown = runner.invoke(
        app, ["source", "show", "S-0059", "--root", str(tmp_path), "--format", "json"]
    )

    assert shown.exit_code == 0, shown.output
    body = json.loads(shown.stdout)
    assert body["id"] == "S-0059"
    assert body["kind"] == "specification"

    # A number the corpus does not hold is a refusal, not a traceback.
    missing = runner.invoke(app, ["source", "show", "S-9999", "--root", str(tmp_path)])

    assert missing.exit_code != 0
    assert "nothing in this tree is the source 'S-9999'" in missing.output
