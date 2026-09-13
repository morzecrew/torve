"""S-0007/mcp-as-the-read-surface: the read surface. Read-only by construction (S-0007/D-3) — the
registered tool list is pinned so a write tool appearing reddens; the mcp
package stays an optional extra, its absence a config error (migrate-extra
precedent). The `why` tool is pinned to the projection's envelope verbatim:
one reader, every renderer (S-0040/D-1)."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_context import seed_why_facts
from test_plan import PHASING, TABLE, plan_repo  # noqa: F401  (fixture)
from typer.testing import CliRunner

from torve.application import contextpack
from torve.application.planner import plan_document, write_contracts
from torve.application.projections import why_report
from torve.cli import app
from torve.cli import mcp as mcp_cli
from torve.config import layout

# ----------------------- #


def test_surface_is_four_read_only_queries(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    server = mcp_cli.build_server(root, root / layout.SPECS_DIR)

    tools = asyncio.run(server.list_tools())

    assert [t.name for t in tools] == ["context", "show", "why", "pack"]
    assert all(t.annotations.read_only_hint for t in tools)


def test_context_tool_serves_the_report_and_slices(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    server = mcp_cli.build_server(root, root / layout.SPECS_DIR)

    full = asyncio.run(server.call_tool("context", {}))
    report = json.loads(full.content[0].text)
    assert {"tasks", "escalations", "programme"} <= report.keys()

    sliced = asyncio.run(server.call_tool("context", {"section": "tasks"}))
    assert set(json.loads(sliced.content[0].text)) == {"tasks"}

    with pytest.raises(Exception) as caught:
        asyncio.run(server.call_tool("context", {"section": "gate_health"}))
    assert "one of:" in str(caught.value.__cause__)


def test_show_tool_resolves_and_refuses(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    server = mcp_cli.build_server(root, root / layout.SPECS_DIR)

    found = asyncio.run(server.call_tool("show", {"identifier": "0090"}))
    document = json.loads(found.content[0].text)
    assert document["kind"] == "document"

    with pytest.raises(Exception) as caught:
        asyncio.run(server.call_tool("show", {"identifier": "S-0009/D-99"}))
    assert "nothing defines" in str(caught.value.__cause__)


def test_missing_package_is_a_config_error(plan_repo, monkeypatch):  # noqa: F811
    root, _, _ = plan_repo

    def gone(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(mcp_cli, "import_module", gone)

    result = CliRunner().invoke(app, ["mcp", "--root", str(root)])

    assert result.exit_code == 3
    assert "torve[mcp]" in result.output


def test_why_tool_serves_the_envelope_verbatim(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    seed_why_facts(root)
    server = mcp_cli.build_server(root, root / layout.SPECS_DIR)

    called = asyncio.run(server.call_tool("why", {"task_id": "T-0001"}))

    assert json.loads(called.content[0].text) == why_report(root, "T-0001")


def test_why_tool_answers_an_unknown_id_with_its_envelope(plan_repo):  # noqa: F811
    """The exit code that catches a typo is the CLI's; the read surface
    answers with the same `found: false` envelope it always re-exposes."""
    root, _, _ = plan_repo
    seed_why_facts(root)
    server = mcp_cli.build_server(root, root / layout.SPECS_DIR)

    called = asyncio.run(server.call_tool("why", {"task_id": "T-9999"}))

    assert json.loads(called.content[0].text) == {
        "schema_version": 1,
        "task": "T-9999",
        "found": False,
    }


def test_pack_tool_serves_the_pack_and_writes_nothing(plan_repo):  # noqa: F811
    """The facts a sandbox is handed on disk, for a session on any harness:
    the same builder dispatch calls, materialized nowhere (S-0067/D-5)."""
    root, _, _ = plan_repo
    write_contracts(root, plan_document(root, root / layout.SPECS_DIR, "0090"))
    server = mcp_cli.build_server(root, root / layout.SPECS_DIR)

    called = asyncio.run(server.call_tool("pack", {"task_id": "T-0001"}))
    files = json.loads(called.content[0].text)

    assert {"index.md", "decisions.json", "gates.json", "schema/task.json"} <= files.keys()
    assert [row["id"] for row in files["decisions.json"]["inherited"]] == [
        "S-0090/D-1",
        "S-0090/D-2",
    ]
    assert not (root / contextpack.PACK_DIR).exists()

    sliced = asyncio.run(server.call_tool("pack", {"task_id": "T-0001", "file": "gates.json"}))
    assert set(json.loads(sliced.content[0].text)) == {"gates.json"}


def test_pack_tool_refuses_an_unknown_task_and_an_unknown_file(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    write_contracts(root, plan_document(root, root / layout.SPECS_DIR, "0090"))
    server = mcp_cli.build_server(root, root / layout.SPECS_DIR)

    with pytest.raises(Exception) as caught:
        asyncio.run(server.call_tool("pack", {"task_id": "T-9999"}))
    assert "no contract" in str(caught.value.__cause__)

    with pytest.raises(Exception) as caught:
        asyncio.run(server.call_tool("pack", {"task_id": "T-0001", "file": "nope.json"}))
    assert "one of:" in str(caught.value.__cause__)
