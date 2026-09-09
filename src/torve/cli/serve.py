"""`torve serve` — a loopback, read-only HTTP surface over the projections
the CLI already renders (S-0032): starlette and uvicorn behind the
`torve[serve]` extra, lazily imported and refused without it exactly like
the mcp and migrate extras (S-0032/D-3). Three JSON endpoints re-expose the
projection functions verbatim (S-0032/D-1) and / serves the shipped bundle
(S-0032/D-4); the bind is 127.0.0.1 unconditionally — there is no host flag to
get wrong (S-0032/D-2).

References: S-0032/A-3.
"""

from __future__ import annotations

from importlib import import_module, resources
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, Any

import typer

from torve.cli.console import fail
from torve.cli.options import (
    ConfigOption,
    DsnOption,
    PartitionOption,
    RootOption,
    dsn_for,
    load_config,
)
from torve.domain.states import EXIT_CONFIG

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.routing import BaseRoute

# ----------------------- #

# Loopback-only by construction (S-0032/D-2): BIND_HOST is the only bind this
# verb knows, and no host flag exists to override it. v1 has no auth; the
# whole security posture is the loopback interface.
BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 7433

_IMPORT_HINT = (
    "starlette and uvicorn are not installed — install the extra: pip install 'torve[serve]'"
)

# A checkout with no bundle is a build gap the 404 names (S-0032/D-4): the
# runtime never runs node, so an unbuilt tree must say what to do instead
# of failing opaquely.
_BUNDLE_HINT = (
    "the dashboard bundle is not built — this checkout ships no frontend "
    "assets; build the web bundle into torve/_web and restart the server"
)


# ....................... #


# The names a request may address this server by. It binds loopback, so
# these are the only hosts that can honestly reach it; anything else in a
# Host header is a request that arrived by a route we did not intend.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")


# ....................... #


def _http() -> SimpleNamespace:
    """The starlette surface behind the serve extra (S-0032/D-3): imported
    lazily so a gates-only install never pays for the dashboard's stack,
    and a missing extra is a config error naming the install, never a
    stack trace."""

    try:
        applications = import_module("starlette.applications")
        responses = import_module("starlette.responses")
        routing = import_module("starlette.routing")
        staticfiles = import_module("starlette.staticfiles")
        trustedhost = import_module("starlette.middleware.trustedhost")

    except ModuleNotFoundError as exc:
        raise RuntimeError(_IMPORT_HINT) from exc

    return SimpleNamespace(
        Starlette=applications.Starlette,
        Mount=routing.Mount,
        Route=routing.Route,
        JSONResponse=responses.JSONResponse,
        PlainTextResponse=responses.PlainTextResponse,
        StaticFiles=staticfiles.StaticFiles,
        TrustedHostMiddleware=trustedhost.TrustedHostMiddleware,
    )


# ....................... #


def _uvicorn() -> Any:
    """uvicorn, the second half of the serve extra (S-0032/D-3) — refused with
    the same instruction as a missing starlette, so a partially installed
    extra degrades identically."""

    try:
        return import_module("uvicorn")

    except ModuleNotFoundError as exc:
        raise RuntimeError(_IMPORT_HINT) from exc


# ....................... #


def _bundle_root() -> Path | None:
    """The shipped frontend — wheel package data at torve/_web (S-0032/D-4),
    with a development checkout's source tree as the fallback; None when
    the bundle was never built. The runtime never runs node; a missing
    bundle is a build gap the 404 names, not a server error."""

    packaged = Path(str(resources.files("torve"))) / "_web"

    if packaged.is_dir():
        return packaged

    development = Path(__file__).resolve().parents[1] / "_web"

    if development.is_dir():
        return development

    return None


# ....................... #


def _records_for(dsn: str, partition: str) -> list[Any] | None:
    """This partition's task facts, or None when none was named — the same
    selection rule every CLI reader takes (S-0050/D-2)."""

    from torve.cli.options import task_events

    return task_events(dsn, partition)


# ....................... #


def build_app(root: Path, rfc_dir: Path, *, dsn: str = "", partition: str = "") -> Any:
    """A starlette app re-exposing the projections the CLI already renders
     and serving the shipped bundle. The server derives nothing of its own:
     a shape the browser needs is added to the projection, and every
     surface renders it at once. Any, like `mcp.build_server`: starlette is
     an optional extra, so its classes never appear at runtime.

     With a partition, every endpoint reads the record the way the CLI's
     `--partition` does, and per request rather than at startup: a resident
     server that folded the log once would serve a board frozen at boot
    . Without one, the same file readers the CLI falls back to.
    """

    http = _http()

    def recorded() -> list[Any] | None:
        return _records_for(dsn, partition)

    def api_context(request: Request) -> Any:
        # The re-exposure rule (S-0032/D-1): this handler is a call into the
        # projection function, nothing more — a field the page needs is
        # added to the projection, not derived here.
        from torve.application.projections import context_report

        return http.JSONResponse(context_report(root, rfc_dir, recorded=recorded()))

    def api_status(request: Request) -> Any:
        from torve.application.manager import project
        from torve.application.projections import status_report

        events = recorded()

        return http.JSONResponse(
            status_report(root, board=project(events) if events is not None else None)
        )

    def api_why(request: Request) -> Any:
        from torve.application.projections import why_report

        # The same re-exposure rule as the other endpoints: the per-task
        # envelope arrives byte-identical to the CLI's --format json, and an
        # unknown id is the found:false envelope over HTTP 200 — the exit
        # code that catches a typo is the CLI's, not the wire's.
        task_id = str(request.path_params["task_id"])
        events = recorded()

        return http.JSONResponse(
            why_report(
                root,
                task_id,
                recorded=[e for e in events if e.subject_id == task_id] if events else None,
            )
        )

    routes: list[BaseRoute] = [
        http.Route("/api/context", api_context, methods=["GET"]),
        http.Route("/api/status", api_status, methods=["GET"]),
        http.Route("/api/why/{task_id}", api_why, methods=["GET"]),
    ]

    bundle = _bundle_root()

    if bundle is not None:
        routes.append(http.Mount("/", app=http.StaticFiles(directory=str(bundle), html=True)))

    else:

        def missing(request: Request) -> Any:
            # Instructive, not decorative (S-0032/D-4): the failure mode is a
            # build gap, and the 404 names it instead of pretending the
            # dashboard exists.
            return http.PlainTextResponse(_BUNDLE_HINT, status_code=404)

        routes.append(http.Route("/{path:path}", missing, methods=["GET"]))

    # A loopback bind is not a reachability guarantee (T-0206): a page the
    # operator visits while this runs can resolve its own name to 127.0.0.1
    # and read the whole projection — task ids, escalation text, costs.
    # Binding decides who can connect; the Host header is what decides
    # whether the request was addressed to us.
    from starlette.middleware import Middleware

    return http.Starlette(
        routes=routes,
        middleware=[
            Middleware(http.TrustedHostMiddleware, allowed_hosts=list(LOOPBACK_HOSTS)),
        ],
    )


# ....................... #


def serve_cmd(
    port: Annotated[
        int,
        typer.Option(
            "--port",
            min=1,
            max=65535,
            help="Port to bind on 127.0.0.1; the loopback bind is fixed.",
        ),
    ] = DEFAULT_PORT,
    dsn: DsnOption = "",
    partition: PartitionOption = "",
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
) -> None:
    """Serve the projections as a read-only dashboard on the loopback
    interface, plus the shipped frontend bundle when one is present.

    With a partition named, every endpoint answers from that partition's
    log; without one, from this repository's files. The envelope says which
    it used, so the page cannot show a number without its provenance."""

    root = root.resolve()
    config = load_config(root, config_path)

    try:
        server_app = build_app(
            root, root / config.specs.path, dsn=dsn_for(root, dsn), partition=partition
        )
        uvicorn = _uvicorn()

    except RuntimeError as exc:
        raise fail(str(exc), EXIT_CONFIG) from exc

    uvicorn.run(server_app, host=BIND_HOST, port=port)
