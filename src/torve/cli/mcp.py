"""`torve mcp` — the planner's read surface (RFC 0007 §5, D-7.3): a
read-only MCP server over the projections, served on stdio for a planning
session on the operator's machine. Queries only — no write tool is
registered, and nothing wires this server into an execution sandbox. The
mcp package is an optional extra, lazily imported like the migrate extra
(rfcs/0012-migrations.md precedent), so a gates-only install never pays
for it.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any

from torve.cli.console import fail
from torve.cli.options import ConfigOption, RootOption, load_config
from torve.domain.states import EXIT_CONFIG

# ----------------------- #


def build_server(root: Path, rfc_dir: Path) -> Any:
    """A server exposing the projections — context, show and why — as
    read-only queries, and nothing else."""

    try:
        mcpserver = import_module("mcp.server.mcpserver")
        types = import_module("mcp.types")

    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "the mcp package is not installed — install the extra: pip install 'torve[mcp]'"
        ) from exc

    server = mcpserver.MCPServer(
        "torve",
        instructions="Read-only execution facts from this repository's task "
        "engine. Nothing here mutates state; planning writes still go "
        "through reviewed, committed documents.",
    )

    @server.tool(annotations=types.ToolAnnotations(readOnlyHint=True))  # type: ignore[untyped-decorator]
    def context(section: str = "") -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """Accumulated execution facts for a planning session. Sections:
        tasks, escalations, proposals, gates, costs, programme. Pass one
        section name to fetch just that section; empty returns the full
        report."""

        from torve.application.projections import context_report

        report = context_report(root, rfc_dir)

        if section and section not in report:
            raise ValueError(f"unknown section {section!r} — one of: {', '.join(report)}")

        if section:
            return {section: report[section]}

        return report

    @server.tool(annotations=types.ToolAnnotations(readOnlyHint=True))  # type: ignore[untyped-decorator]
    def show(identifier: str) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """Resolve one corpus identifier — a decision (D-6.8), an
        amendment (A-47) or a document (0021) — to its standing definition:
        the same lookup `torve rfc show` serves (D-7.29)."""

        from torve.config.rfc_parse import lookup

        found = lookup(rfc_dir, identifier)

        if found is None:
            raise ValueError(f"nothing defines {identifier!r} in this corpus")

        return found

    @server.tool(annotations=types.ToolAnnotations(readOnlyHint=True))  # type: ignore[untyped-decorator]
    def why(task_id: str) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """Why one task ended where it did: its execution history in one
        envelope — per-attempt verdict, tier, gate convictions, cost, clock
        and trace, the engine events and reviews around them, totals, and
        its cost against its own regime's attempt distribution — verbatim
        the envelope `torve why` renders, so a planning session reads
        exactly the facts the operator does."""

        from torve.application.projections import why_report

        # The projection verbatim: this tool derives nothing of its own, and
        # an unknown id is the found:false envelope, not an error — the exit
        # code that distinguishes a typo lives on the CLI, not the read
        # surface (D-40.1, D-40.6).
        return why_report(root, task_id)

    return server


# ....................... #


def mcp_cmd(config_path: ConfigOption = None, root: RootOption = Path(".")) -> None:
    """Serve the planning read surface over stdio. Queries only; execution
    sandboxes never get this server."""

    root = root.resolve()
    config = load_config(root, config_path)

    try:
        server = build_server(root, root / config.rfcs.path)

    except RuntimeError as exc:
        raise fail(str(exc), EXIT_CONFIG) from exc

    server.run()
