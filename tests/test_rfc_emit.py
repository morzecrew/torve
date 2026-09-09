"""`torve.config.rfc_emit` — the canonical emitter (RFC 0025 §5.1, D-25.1):
frontmatter re-rendered in fixed key order with trap scalars quoted, the
decision table and phasing fence re-serialised from their parsed models,
dated amendment headings normalised, prose untouched. Idempotence is the
property that matters most: `emit(emit(text)) == emit(text)`, pinned here
against fixtures and against every document already committed to the
corpus.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from torve.config.rfc_emit import emit, render_frontmatter
from torve.config.spec import (
    decision_table,
    load_document,
    parse_frontmatter,
    parse_phasing,
    paths_globs,
    rfc_files,
)

DOC = """---
id: "0001"
title: Widget
status: draft
depends_on: []
informed_by: []
supersedes: []
superseded_by: null
amended_by: ["A-1"]
owner: Test Owner
description: >-
  Scratch document for emitter tests.
schema_version: 1
---

# RFC 0001 — Widget

## Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-T.1 | `ASSUMED` | Something is decided | `src/thing/**` | Nothing yet |

## Phasing

```yaml
- phase: 1
  title: the-only-phase
  intent: >-
    Build the thing.
  scope: ["src/thing/**"]
  acceptance: ["make test"]
  depends_on: []
```

## Amendments

### A-1 - 2026-01-01 - first amendment

Prose that stays exactly as written.
"""


# ....................... #
# idempotence (RFC 0025 §5.1, §6): the property the whole design leans on


def test_emit_is_a_fixed_point() -> None:
    once = emit(DOC)
    twice = emit(once)
    assert once == twice


def test_emit_on_an_already_canonical_document_writes_nothing_new() -> None:
    canonical = emit(DOC)
    assert emit(canonical) == canonical


def test_the_live_corpus_round_trips_idempotently() -> None:
    repo = Path(__file__).resolve().parent.parent
    files = rfc_files(repo / "rfcs")
    assert files  # the corpus is not empty in this checkout

    for path in files.values():
        text = path.read_text(encoding="utf-8")
        once = emit(text)
        twice = emit(once)
        assert once == twice, f"{path.name}: emit() is not a fixed point"


# ....................... #
# prose passes through byte-for-byte (D-25.1)


def test_body_prose_is_untouched() -> None:
    once = emit(DOC)
    assert "Prose that stays exactly as written." in once
    assert "Something is decided" in once
    assert "Build the thing." in once


# ....................... #
# the Contract example fence (RFC 0025 §5.4, D-25.10) is not one of the
# structures D-25.1 lists — it stays body prose, byte-for-byte


CONTRACT_EXAMPLE_FENCE = (
    "## Contract example\n\n```yaml contract-example\nid: T-9999\ndecisions: []\n```\n\n"
)

DOC_WITH_CONTRACT_EXAMPLE = DOC.replace("## Phasing", CONTRACT_EXAMPLE_FENCE + "## Phasing")


def test_contract_example_fence_is_untouched_body_prose() -> None:
    once = emit(DOC_WITH_CONTRACT_EXAMPLE)
    assert CONTRACT_EXAMPLE_FENCE in once


# ....................... #
# the emitter refuses what the parser would refuse


def test_emit_raises_on_unparseable_frontmatter() -> None:
    with pytest.raises(ValueError, match="frontmatter"):
        emit("no frontmatter here\n")


def test_emit_raises_on_a_phasing_fence_that_does_not_mint() -> None:
    broken = DOC.replace("- phase: 1", "- phase: 1\n  extra_unknown_field: true")

    with pytest.raises(ValueError):
        emit(broken)


# ....................... #
# trap scalars (RFC 0025 §2): quoted exactly when YAML would misread them


def base_frontmatter(**overrides: object) -> dict[str, object]:
    fm: dict[str, object] = {
        "id": "0001",
        "title": "Widget",
        "status": "draft",
        "depends_on": [],
        "informed_by": [],
        "supersedes": [],
        "superseded_by": None,
        "amended_by": [],
        "owner": "Test Owner",
        "description": "A description.",
        "schema_version": 1,
    }
    fm.update(overrides)
    return fm


@pytest.mark.parametrize(
    ("value", "must_appear"),
    [
        ("on", 'owner: "on"'),  # a bare `on:` reads as boolean unquoted (YAML 1.1)
        ("key: value", 'owner: "key: value"'),  # a colon-space starts a mapping unquoted
        ("trailing # hash", 'owner: "trailing # hash"'),  # ` #` starts a comment unquoted
    ],
)
def test_trap_scalars_are_quoted(value: str, must_appear: str) -> None:
    rendered = render_frontmatter(base_frontmatter(owner=value))
    assert must_appear in rendered

    reparsed = parse_frontmatter(rendered)
    assert reparsed is not None
    assert reparsed["owner"] == value


def test_a_leading_zero_id_does_not_parse_as_octal() -> None:
    rendered = render_frontmatter(base_frontmatter(id="0010"))
    assert 'id: "0010"' in rendered

    reparsed = parse_frontmatter(rendered)
    assert reparsed is not None
    assert reparsed["id"] == "0010"  # unquoted, PyYAML's octal resolver reads this as 8


def test_a_plain_safe_value_stays_unquoted() -> None:
    rendered = render_frontmatter(base_frontmatter(owner="Lev Litvinov"))
    assert "owner: Lev Litvinov\n" in rendered
    assert "'" not in rendered.split("owner:")[1].splitlines()[0]


# ....................... #
# fixed frontmatter key order (D-25.1)


def test_frontmatter_key_order_is_fixed_regardless_of_input_order() -> None:
    fm = {
        "schema_version": 1,
        "description": "A description.",
        "id": "0001",
        "owner": "Test Owner",
        "title": "Widget",
        "status": "draft",
    }
    rendered = render_frontmatter(fm)
    body = rendered.splitlines()[1:-1]
    keys_in_order = [line.split(":", 1)[0] for line in body if not line.startswith(" ")]
    assert keys_in_order == ["id", "title", "status", "owner", "description", "schema_version"]


# ....................... #
# amendment headings: normalised to the em-dash dated form (D-A.5)


def test_amendment_heading_dashes_are_normalised() -> None:
    once = emit(DOC)
    assert "### A-1 — 2026-01-01 — first amendment" in once
    assert "### A-1 - 2026-01-01 - first amendment" not in once


# ....................... #
# the decision table and phasing fence re-serialise from the parsed model


def test_decision_row_paths_are_backtick_wrapped_and_consequence_dashed() -> None:
    doc = DOC.replace(
        "| D-T.1 | `ASSUMED` | Something is decided | `src/thing/**` | Nothing yet |",
        "| D-T.1 | `ASSUMED` | Something is decided | src/thing/** src/other/** | — |",
    )
    once = emit(doc)
    assert (
        "| D-T.1 | `ASSUMED` | Something is decided | `src/thing/**` `src/other/**` | — |" in once
    )


def test_phasing_scope_renders_as_a_block_list() -> None:
    once = emit(DOC)
    assert '  scope:\n    - "src/thing/**"' in once


def test_phasing_tier_variant_survives_parse_emit_parse() -> None:
    doc = DOC.replace("  depends_on: []\n```", "  tier_variant: copywriter\n  depends_on: []\n```")
    once = emit(doc)
    assert "  tier_variant: copywriter\n  depends_on: []" in once

    entries = parse_phasing(once)
    assert entries is not None
    assert entries[0].tier_variant == "copywriter"


def test_a_comma_between_paths_is_a_separator_not_a_path() -> None:
    # `a`, `b` leaves the comma standing alone once the backticks become
    # spaces. Read as a path it gives the decoration check a glob matching
    # nothing, and the emitter writes the comma back as its own path.
    doc = DOC.replace(
        "| D-T.1 | `ASSUMED` | Something is decided | `src/thing/**` | Nothing yet |",
        "| D-T.1 | `ASSUMED` | Something is decided | `src/thing/**`, `src/other/**` | — |",
    )

    assert paths_globs("`src/thing/**`, `src/other/**`") == ["src/thing/**", "src/other/**"]
    assert "`,`" not in emit(doc)


# ----------------------- #
# RFC 0053 phase 2: the amend forms write the diff, the editorial lane
# re-stamps, the archive keeps every byte, deletions ride the transaction


from torve.config.rfc_emit import (  # noqa: E402
    FINGERPRINTS_KEY,
    amend_row,
    append_amendment,
    archive_document,
    fix_row_text,
    retire_decision,
    stamp,
    write_transaction,
)


def test_amend_row_replaces_the_field_stamps_the_row_and_returns_the_diff() -> None:
    mutated, changes = amend_row(DOC, "D-T.1", grade="LOCKED", paths=["src/thing/**", "tests/**"])
    (row,) = decision_table(mutated)

    assert row.grade == "LOCKED" and row.paths == ["src/thing/**", "tests/**"]
    assert [c["field"] for c in changes] == ["grade", "paths", "fingerprint"]
    assert changes[0] == {
        "subject": "D-T.1",
        "field": "grade",
        "before": "ASSUMED",
        "after": "LOCKED",
    }
    assert changes[1]["before"] == ["src/thing/**"]
    assert changes[2]["before"] is None and changes[2]["after"] == stamp(row)

    fm = parse_frontmatter(mutated) or {}

    assert fm[FINGERPRINTS_KEY] == {"D-T.1": stamp(row)}
    assert emit(mutated) == mutated


def test_amend_row_with_nothing_to_change_is_refused() -> None:
    with pytest.raises(ValueError, match="nothing to change"):
        amend_row(DOC, "D-T.1", grade="ASSUMED")

    with pytest.raises(ValueError, match="no decision"):
        amend_row(DOC, "D-T.9", grade="OPEN")


def test_the_diff_rides_beneath_the_amendment_heading_as_a_changes_fence() -> None:
    mutated, changes = amend_row(DOC, "D-T.1", new_text="Something else is decided")
    amended = append_amendment(mutated, "A-2", "the text moved", "2026-09-09", changes)

    assert "### A-2 — 2026-09-09 — the text moved" in amended
    assert "```yaml changes" in amended
    assert '  before: "Something is decided"' in amended

    entry = next(a for a in _loaded(amended).amendments if a.id == "A-2")

    assert [c.field for c in entry.changes] == ["text", "fingerprint"]
    assert entry.changes[0].before == "Something is decided"


def _loaded(text: str):
    import tempfile

    path = Path(tempfile.mkdtemp()) / "0001-widget.md"
    path.write_text(text, encoding="utf-8")

    return load_document(path)


def test_fix_records_the_editorial_pair_under_the_table_and_never_an_amendment() -> None:
    fixed, changes = fix_row_text(DOC, "D-T.1", "Something is decided, spelt right")
    (row,) = decision_table(fixed)

    assert row.text == "Something is decided, spelt right"
    assert [c["field"] for c in changes] == ["text", "fingerprint"]
    assert "<!-- editorial changes, recorded by `torve rfc fix` -->" in fixed
    assert fixed.index("```yaml changes") < fixed.index("## Phasing")
    assert (parse_frontmatter(fixed) or {})["amended_by"] == ["A-1"]  # unchanged
    assert emit(fixed) == fixed

    again, _ = fix_row_text(fixed, "D-T.1", "Something is decided, spelt right twice")

    assert again.count("<!-- editorial changes") == 1
    assert again.count('- subject: "D-T.1"') == 4  # two fixes, two entries each

    with pytest.raises(ValueError, match="already reads that way, and it is stamped"):
        fix_row_text(again, "D-T.1", "Something is decided, spelt right twice")

    by_hand = again.replace("spelt right twice", "spelt right thrice")
    restamped, changes = fix_row_text(by_hand, "D-T.1", "Something is decided, spelt right thrice")

    assert [c["field"] for c in changes] == ["fingerprint"]  # the text stood; the stamp moved
    assert emit(restamped) == restamped


def test_retire_with_a_reason_writes_it_at_the_tombstone_and_drops_the_stamp() -> None:
    stamped, _ = amend_row(DOC, "D-T.1", grade="OPEN")
    retired = retire_decision(stamped, "D-T.1", "2026-09-09", reason="path rot")

    assert "D-T.1 was retired 2026-09-09; path rot." in retired
    assert FINGERPRINTS_KEY not in (parse_frontmatter(retired) or {})


def test_archive_document_supersedes_and_keeps_every_other_byte() -> None:
    archived = archive_document(DOC, "0054", "2026-09-09")
    fm = parse_frontmatter(archived) or {}

    assert fm["status"] == "superseded" and fm["superseded_by"] == "0054"
    assert "Prose that stays exactly as written." in archived
    assert "*Archived 2026-09-09: superseded by 0054" in archived

    with pytest.raises(ValueError, match="already superseded"):
        archive_document(archived, "0055", "2026-09-10")


def test_a_deletion_rides_the_transaction_and_a_red_check_keeps_the_file(tmp_path: Path) -> None:
    rfcs = tmp_path / "rfcs"
    rfcs.mkdir()
    (rfcs / "0001-widget.md").write_text(DOC, encoding="utf-8")
    dependent = (
        DOC.replace('id: "0001"', 'id: "0002"')
        .replace("Widget", "Gadget")
        .replace("depends_on: []", 'depends_on: ["0001"]')
        .replace("D-T.1", "D-G.1")
        .replace("# RFC 0001", "# RFC 0002")
    )
    (rfcs / "0002-gadget.md").write_text(dependent, encoding="utf-8")

    from torve.config.spec import build_index, rfc_files

    (rfcs / "INDEX.md").write_text(build_index(rfc_files(rfcs)), encoding="utf-8")

    report = write_transaction(rfcs, tmp_path, {}, deletions=("0001-widget.md",))

    assert not report.ok  # 0002 depends on the deleted document
    assert (rfcs / "0001-widget.md").exists()

    report = write_transaction(rfcs, tmp_path, {}, deletions=("0002-gadget.md",))

    assert report.ok
    assert not (rfcs / "0002-gadget.md").exists()
    assert "0002-gadget.md" not in (rfcs / "INDEX.md").read_text(encoding="utf-8")


def test_a_changes_fence_with_a_long_value_is_still_yaml() -> None:
    long_text = (
        "Only `NNNN-slug.md` and `INDEX.md` in the corpus directory, no subdirectories; the "
        "check routes offenders to `pages/` or `ops/`. A retired document's one legal "
        "destination is `archive/rfcs/` beside the corpus path: written only by the verb"
    )
    mutated, changes = amend_row(DOC, "D-T.1", new_text=long_text)
    amended = append_amendment(mutated, "A-2", "a long row", "2026-09-09", changes)
    entry = next(a for a in _loaded(amended).amendments if a.id == "A-2")

    assert entry.changes[0].after == long_text
    assert emit(amended) == amended


def test_the_transaction_checks_with_the_archive_in_view(tmp_path: Path) -> None:
    rfcs = tmp_path / "rfcs"
    rfcs.mkdir()
    citing = (
        DOC.replace('id: "0001"', 'id: "0002"')
        .replace("Widget", "Gadget")
        .replace("D-T.1", "D-G.1")
        .replace("# RFC 0001", "# RFC 0002")
        .replace("## Decisions", "Built on D-T.1.\n\n## Decisions")
    )
    (rfcs / "0001-widget.md").write_text(DOC, encoding="utf-8")
    (rfcs / "0002-gadget.md").write_text(citing, encoding="utf-8")

    from torve.config.spec import build_index, rfc_files

    (rfcs / "INDEX.md").write_text(build_index(rfc_files(rfcs)), encoding="utf-8")

    without = write_transaction(rfcs, tmp_path, {}, deletions=("0001-widget.md",))

    assert not without.ok  # 0002 cites D-T.1 and nothing would define it

    moved = write_transaction(
        rfcs, tmp_path, {}, deletions=("0001-widget.md",), archived={"0001-widget.md": DOC}
    )

    assert moved.ok, moved.problems
    assert (tmp_path / "archive" / "rfcs" / "0001-widget.md").read_text(encoding="utf-8") == DOC
    assert not (rfcs / "0001-widget.md").exists()
