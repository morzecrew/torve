"""The documentation site, where it restates something the code owns.

`pages/` explains shape and reasons and links to the corpus for every graded
decision — that discipline needs no test. What does need one is the single
place the site reproduces a table the code defines: a page that disagrees
with the engine is worse than no page, and prose rots silently where code
does not.
"""

from __future__ import annotations

import re
from pathlib import Path

from torve.domain.events import AUTHORITY, EventKind

PAGES = Path(__file__).resolve().parents[1] / "pages" / "docs"
RECORD = PAGES / "architecture" / "record.md"
ROW = re.compile(r"^\| `(?P<kind>[a-z.]+)` \| (?P<actors>[a-z, ]+) \|$", re.MULTILINE)


def documented() -> dict[str, set[str]]:
    body = RECORD.read_text(encoding="utf-8")
    marked = body.split("<!-- authority-table:start -->")[1].split("<!-- authority-table:end -->")[
        0
    ]

    return {
        match["kind"]: {one.strip() for one in match["actors"].split(",")}
        for match in ROW.finditer(marked)
    }


def test_the_authority_table_on_the_page_is_the_one_the_engine_enforces():
    page = documented()

    assert page, "the marked table is missing or its rows no longer parse"
    assert set(page) == {str(kind) for kind in EventKind}

    for kind in EventKind:
        assert page[str(kind)] == {str(actor) for actor in AUTHORITY[kind]}, (
            f"{kind}: the page and src/torve/domain/events.py disagree"
        )


def test_every_page_the_nav_names_exists():
    config = (PAGES.parent / "zensical.toml").read_text(encoding="utf-8")
    named = re.findall(r'= "([a-z0-9/_-]+\.md)"', config)

    assert named
    missing = [one for one in named if not (PAGES / one).is_file()]

    assert not missing, f"the nav names pages that are not there: {missing}"


def test_the_operating_guide_documents_the_review_thread_leg_as_the_code_runs_it():
    # The one thing prose can get wrong here without anybody noticing is the
    # default: a guide that reads as though the leg is on is a guide that
    # sends somebody looking for threads nothing was ever going to answer.
    from torve.config.runconfig import ThreadsConfig

    guide = (PAGES / "operating.md").read_text(encoding="utf-8")

    assert ThreadsConfig().enabled is False
    assert "threads:" in guide, "the operating guide names no review-thread configuration"
    assert "off by default" in guide

    for key in ("enabled", "bots", "rounds_per_pass"):
        assert key in ThreadsConfig.model_fields
        assert key in guide, f"the operating guide does not name threads.{key}"


def test_no_page_claims_the_retired_truth_boundary():
    # The site taught "git holds what SHOULD be, the store holds what
    # HAPPENED" as the organising rule until S-0044/D-1 inverted it. The pages
    # kept as records of decisions still say it, correctly and in the past
    # tense; the architecture pages must not.
    for page in (PAGES / "architecture").glob("*.md"):
        body = page.read_text(encoding="utf-8")

        assert "D-27, LOCKED" not in body, f"{page.name} still cites the retired boundary"
        assert "Derive, don't record" not in body, f"{page.name} still teaches derive-don't-record"
