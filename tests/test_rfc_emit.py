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
from torve.config.rfc_parse import parse_frontmatter, parse_phasing, rfc_files

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
    "## Contract example\n\n"
    "```yaml contract-example\n"
    "id: T-9999\n"
    "decisions: []\n"
    "```\n\n"
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
    assert "| D-T.1 | `ASSUMED` | Something is decided | `src/thing/**` `src/other/**` | — |" in once


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
