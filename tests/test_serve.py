"""RFC 0032 §6: the serve surface. Endpoint tests run through starlette's
TestClient — no socket is opened; the loopback-only configuration and the
absence of a host flag are asserted against the CLI; a checkout without a
bundle gets an instructive 404 instead of a half-working dashboard; and the
serve extra stays optional, its absence a config error (the mcp-extra
precedent, D-32.3)."""

from __future__ import annotations

from types import SimpleNamespace

from starlette.testclient import TestClient
from test_context import seed_facts
from test_plan import plan_repo  # noqa: F401  (fixture)
from typer.testing import CliRunner

from torve.application.projections import context_report, status_report
from torve.cli import app
from torve.cli import serve as serve_cli

# ----------------------- #


def test_api_context_re_exposes_the_projection_verbatim(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    server = serve_cli.build_app(root, root / "rfcs")

    with TestClient(server) as client:
        response = client.get("/api/context")

    assert response.status_code == 200
    assert response.json() == context_report(root, root / "rfcs")


def test_api_status_re_exposes_the_projection_verbatim(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    seed_facts(root)
    server = serve_cli.build_app(root, root / "rfcs")

    with TestClient(server) as client:
        response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == status_report(root)


def test_api_is_read_only(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    server = serve_cli.build_app(root, root / "rfcs")

    with TestClient(server) as client:
        response = client.post("/api/context")

    assert response.status_code == 405


def test_no_bundle_is_an_instructive_404(plan_repo, monkeypatch):  # noqa: F811
    root, _, _ = plan_repo
    monkeypatch.setattr(serve_cli, "_bundle_root", lambda: None)
    server = serve_cli.build_app(root, root / "rfcs")

    with TestClient(server) as client:
        response = client.get("/")
        api = client.get("/api/status")

    assert response.status_code == 404
    assert "torve/_web" in response.text
    assert "not built" in response.text
    # The JSON surface is independent of the bundle: a checkout without a
    # frontend still serves the projections.
    assert api.status_code == 200


def test_bundle_is_served_from_package_data(plan_repo, monkeypatch, tmp_path):  # noqa: F811
    root, _, _ = plan_repo
    bundle = tmp_path / "_web"
    (bundle / "assets").mkdir(parents=True)
    (bundle / "index.html").write_text("<html>dashboard</html>", encoding="utf-8")
    (bundle / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    monkeypatch.setattr(serve_cli, "_bundle_root", lambda: bundle)
    server = serve_cli.build_app(root, root / "rfcs")

    with TestClient(server) as client:
        index = client.get("/")
        asset = client.get("/assets/app.js")
        missing = client.get("/assets/nope.js")

    assert index.status_code == 200
    assert index.text == "<html>dashboard</html>"
    assert asset.status_code == 200
    assert asset.text == "console.log(1)"
    assert missing.status_code == 404


def test_missing_extra_names_the_install_and_exit(plan_repo, monkeypatch):  # noqa: F811
    root, _, _ = plan_repo

    def gone(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(serve_cli, "import_module", gone)

    result = CliRunner().invoke(app, ["serve", "--root", str(root)])

    assert result.exit_code == 3
    assert "torve[serve]" in result.output


def test_serve_binds_loopback_unconditionally(plan_repo, monkeypatch):  # noqa: F811
    root, _, _ = plan_repo
    captured: dict[str, object] = {}

    def fake_uvicorn():
        return SimpleNamespace(run=lambda app, **kwargs: captured.update(kwargs))

    monkeypatch.setattr(serve_cli, "_uvicorn", fake_uvicorn)

    result = CliRunner().invoke(app, ["serve", "--root", str(root)])

    assert result.exit_code == 0, result.output
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 7433


def test_serve_has_no_host_flag():
    result = CliRunner().invoke(app, ["serve", "--help"])

    assert result.exit_code == 0
    assert "--port" in result.output
    assert "--host" not in result.output
