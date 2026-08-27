"""RFC 0007 §5: the read surface. Read-only by construction (D-7.3) — the
registered tool list is pinned so a write tool appearing reddens; the mcp
package stays an optional extra, its absence a config error (migrate-extra
precedent)."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_plan import PHASING, TABLE, plan_repo  # noqa: F401  (fixture)
from typer.testing import CliRunner

from torve.cli import app
from torve.cli import mcp as mcp_cli

# ----------------------- #


def test_surface_is_one_read_only_query(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    server = mcp_cli.build_server(root, root / "rfcs")

    tools = asyncio.run(server.list_tools())

    assert [t.name for t in tools] == ["context", "show"]
    assert all(t.annotations.read_only_hint for t in tools)


def test_context_tool_serves_the_report_and_slices(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    server = mcp_cli.build_server(root, root / "rfcs")

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
    server = mcp_cli.build_server(root, root / "rfcs")

    found = asyncio.run(server.call_tool("show", {"identifier": "0090"}))
    document = json.loads(found.content[0].text)
    assert document["kind"] == "document"

    with pytest.raises(Exception) as caught:
        asyncio.run(server.call_tool("show", {"identifier": "D-9.99"}))
    assert "nothing defines" in str(caught.value.__cause__)


def test_missing_package_is_a_config_error(plan_repo, monkeypatch):  # noqa: F811
    root, _, _ = plan_repo

    def gone(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(mcp_cli, "import_module", gone)

    result = CliRunner().invoke(app, ["mcp", "--root", str(root)])

    assert result.exit_code == 3
    assert "torve[mcp]" in result.output
