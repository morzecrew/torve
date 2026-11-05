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


def test_the_operating_guide_documents_checks_twins_and_invariants():
    """S-0106/D-1..D-5: the page an operator reads names a row's check and its
    vacuous failure, the twin that proves a blocking check can fail, and the
    invariants the document battery runs."""

    guide = (PAGES / "operating.md").read_text(encoding="utf-8")

    assert "decision:<id>" in guide
    assert "selects nothing" in guide
    assert "check_twin" in guide
    assert "invariant:<id>" in guide


def test_the_operating_guide_documents_the_suite_gate_and_the_lane_regate():
    """S-0118/D-1..D-3, D-6, D-9, D-10: the page an operator reads says the
    whole suite runs once as `suite`, how a failure is attributed, that the
    lane re-gates with the blocking gates, and where a red re-gate's output
    is read."""

    guide = (PAGES / "operating.md").read_text(encoding="utf-8")
    text = " ".join(guide.split())

    assert "builtin `suite` gate" in text
    assert "the whole suite over the head" in text
    assert "rerun at the head" in text
    assert "run on the merge base" in text
    assert "red at the head and not red on the base" in text
    assert "`inherited_failures`" in text
    assert "A suite whose only failures are inherited passes" in text
    assert "cannot run on the base tree has no base reading and convicts" in text
    assert "output names no failing test" in text
    assert "their exit codes never convict" in text

    assert "lane re-gates with the blocking gates" in text
    assert "gates whose state is `blocking` and the decision gates" in text
    assert "`lane.judged`" in text
    assert "A red re-gate's output is read from `torve why`" in text


def test_the_configuration_reference_names_the_nested_docker_block_the_engine_parses():
    """The page that documents `.torve/config.yaml` names `runtime.docker` and
    every field of its `runtime.nested` block, and each name is one the engine
    parses — so a renamed key cannot leave the reference teaching a setting
    nothing reads."""

    from torve.config.runconfig import NestedConfig, RuntimeConfig

    page = (PAGES / "get-started.md").read_text(encoding="utf-8")

    assert "runtime.docker" in page
    assert "nested" in page

    for key in ("docker", "nested"):
        assert key in RuntimeConfig.model_fields, key
        assert key in page, f"the configuration reference does not name runtime.{key}"

    for key in NestedConfig.model_fields:
        assert key in page, f"the configuration reference does not name runtime.nested.{key}"


def test_the_upgrade_page_and_the_readme_name_only_verbs_that_exist():
    """S-0110/D-11 restates the upgrade steps on the reference page and the
    README, and those steps name verbs. Prose rots where code does not, so a
    `torve <verb>` either of them names has to resolve in the CLI."""

    from typer.main import get_command

    from torve.cli.main import app

    root = get_command(app)
    readme = PAGES.parents[1] / "README.md"
    texts = [readme.read_text(encoding="utf-8")]
    texts += [
        one.read_text(encoding="utf-8")
        for one in sorted((PAGES / "reference").glob("*.md"))
        if one.name != "AGENTS.md"
    ]

    for text in texts:
        for first, second in re.findall(r"`torve ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?", text):
            assert first in root.commands, first
            group = root.commands[first]
            if second and hasattr(group, "commands"):
                assert second in group.commands, f"{first} {second}"


def test_the_configuration_reference_names_the_published_sandbox_images():
    """S-0112/D-8: the page that documents `runtime.image` names the published
    base and a seat image, each with the `{torve}` placeholder the
    configuration resolves at load — so an upgrade moves `engine` and no image
    reference — and names no image a moving tag would reach instead."""

    page = (PAGES / "get-started.md").read_text(encoding="utf-8")

    assert "ghcr.io/morzecrew/sandbox-base:{torve}" in page
    assert "ghcr.io/morzecrew/claude-sandbox:{torve}" in page
    assert "sandbox-base:latest" not in page, "the reference still names a moving tag"

    upgrade = (PAGES / "reference" / "stability.md").read_text(encoding="utf-8")
    assert "ghcr.io/morzecrew/" in upgrade
    assert "{torve}" in upgrade


def test_no_page_claims_the_retired_truth_boundary():
    # The site taught "git holds what SHOULD be, the store holds what
    # HAPPENED" as the organising rule until S-0044/D-1 inverted it. The pages
    # kept as records of decisions still say it, correctly and in the past
    # tense; the architecture pages must not.
    for page in (PAGES / "architecture").glob("*.md"):
        body = page.read_text(encoding="utf-8")

        assert "D-27, LOCKED" not in body, f"{page.name} still cites the retired boundary"
        assert "Derive, don't record" not in body, f"{page.name} still teaches derive-don't-record"


def test_the_operating_guide_documents_the_engine_the_chain_plans_from():
    """S-0120/D-5: the page an operator reads says a chain fast-forwards and
    plans from its own checkout only when it runs an installed engine, names
    `night.engine_paths` and the exit code its fast-forward gives when it
    moves them, and ships the launcher loop — a virtualenv of its own,
    relaunched on 6, apart from the host's shared tool install."""

    from torve.config.runconfig import NightConfig
    from torve.domain.states import EXIT_ENGINE_MOVED

    guide = (PAGES / "operating.md").read_text(encoding="utf-8")
    text = " ".join(guide.split())

    # The key the engine parses, not one the page invented.
    assert "engine_paths" in NightConfig.model_fields
    assert "night.engine_paths" in text
    assert (
        "A chain fast-forwards and plans from its own checkout only when it runs an installed engine"
        in text
    )

    # The move, the event it records and the code a launcher reads.
    assert "chain_engine_moved" in text
    assert f"exits {EXIT_ENGINE_MOVED}" in text
    assert "EXIT_ENGINE_MOVED" in text

    # The loop: installed from the checkout into its own virtualenv, serving
    # that same checkout by `--root`, and relaunched on the code rather than
    # reusing the shared tool install; any other exit stops it with its code.
    assert "uv pip install" in text
    assert "$HOME/.torve-engines/torve-self" in text
    assert '--root "$root"' in text
    assert '[ "$status" -eq 6 ] || exit "$status"' in text
    assert "virtualenv of its own" in text
    assert "relaunch" in text
    assert "~/.local/bin/torve" in text
    assert "shared tool install" in text
