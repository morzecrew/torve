"""`torve serve` — a loopback, read-only HTTP surface over the projections
the CLI already renders (RFC 0032 §5): starlette and uvicorn behind the
`torve[serve]` extra, lazily imported and refused without it exactly like
the mcp and migrate extras (D-32.3). Two JSON endpoints re-expose the
projection functions verbatim (D-32.1) and / serves the shipped bundle
(D-32.4); the bind is 127.0.0.1 unconditionally — there is no host flag to
get wrong (D-32.2).
"""

from __future__ import annotations

from importlib import import_module, resources
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, Any

import typer

from torve.cli.console import fail
from torve.cli.options import ConfigOption, RootOption, load_config
from torve.domain.states import EXIT_CONFIG

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.routing import BaseRoute

# ----------------------- #

# Loopback-only by construction (D-32.2): BIND_HOST is the only bind this
# verb knows, and no host flag exists to override it. v1 has no auth; the
# whole security posture is the loopback interface.
BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 7433

_IMPORT_HINT = (
    "starlette and uvicorn are not installed — install the extra: "
    "pip install 'torve[serve]'"
)

# A checkout with no bundle is a build gap the 404 names (D-32.4): the
# runtime never runs node, so an unbuilt tree must say what to do instead
# of failing opaquely.
_BUNDLE_HINT = (
    "the dashboard bundle is not built — this checkout ships no frontend "
    "assets; build the web bundle into torve/_web and restart the server"
)


# ....................... #


def _http() -> SimpleNamespace:
    """The starlette surface behind the serve extra (D-32.3): imported
    lazily so a gates-only install never pays for the dashboard's stack,
    and a missing extra is a config error naming the install, never a
    stack trace."""

    try:
        applications = import_module("starlette.applications")
        responses = import_module("starlette.responses")
        routing = import_module("starlette.routing")
        staticfiles = import_module("starlette.staticfiles")

    except ModuleNotFoundError as exc:
        raise RuntimeError(_IMPORT_HINT) from exc

    return SimpleNamespace(
        Starlette=applications.Starlette,
        Mount=routing.Mount,
        Route=routing.Route,
        JSONResponse=responses.JSONResponse,
        PlainTextResponse=responses.PlainTextResponse,
        StaticFiles=staticfiles.StaticFiles,
    )


# ....................... #


def _uvicorn() -> Any:
    """uvicorn, the second half of the serve extra (D-32.3) — refused with
    the same instruction as a missing starlette, so a partially installed
    extra degrades identically."""

    try:
        return import_module("uvicorn")

    except ModuleNotFoundError as exc:
        raise RuntimeError(_IMPORT_HINT) from exc


# ....................... #


def _bundle_root() -> Path | None:
    """The shipped frontend — wheel package data at torve/_web (D-32.4),
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


def build_app(root: Path, rfc_dir: Path) -> Any:
    """A starlette app re-exposing the projections the CLI already renders
    and serving the shipped bundle. The server derives nothing of its own:
    a shape the browser needs is added to the projection, and every
    surface renders it at once. Any, like `mcp.build_server`: starlette is
    an optional extra, so its classes never appear at runtime."""

    http = _http()

    def api_context(request: Request) -> Any:
        # The re-exposure rule (D-32.1): this handler is a call into the
        # projection function, nothing more — a field the page needs is
        # added to the projection, not derived here.
        from torve.application.projections import context_report

        return http.JSONResponse(context_report(root, rfc_dir))

    def api_status(request: Request) -> Any:
        from torve.application.projections import status_report

        return http.JSONResponse(status_report(root))

    routes: list[BaseRoute] = [
        http.Route("/api/context", api_context, methods=["GET"]),
        http.Route("/api/status", api_status, methods=["GET"]),
    ]

    bundle = _bundle_root()

    if bundle is not None:
        routes.append(http.Mount("/", app=http.StaticFiles(directory=str(bundle), html=True)))

    else:

        def missing(request: Request) -> Any:
            # Instructive, not decorative (D-32.4): the failure mode is a
            # build gap, and the 404 names it instead of pretending the
            # dashboard exists.
            return http.PlainTextResponse(_BUNDLE_HINT, status_code=404)

        routes.append(http.Route("/{path:path}", missing, methods=["GET"]))

    return http.Starlette(routes=routes)


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
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
) -> None:
    """Serve the projections as a read-only dashboard on the loopback
    interface, plus the shipped frontend bundle when one is present."""

    root = root.resolve()
    config = load_config(root, config_path)

    try:
        server_app = build_app(root, root / config.rfcs.path)
        uvicorn = _uvicorn()

    except RuntimeError as exc:
        raise fail(str(exc), EXIT_CONFIG) from exc

    uvicorn.run(server_app, host=BIND_HOST, port=port)
