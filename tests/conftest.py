from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from torve.config import layout
from torve.config.manifest import load_manifest
from torve.gates.context import build_context
from torve.gates.sabotage import LOCKED_D1, Repo, base_task

# The scalar that ended three attempts of T-0245: backticked `key: value`
# text, which a hand-written log carries unquoted and YAML then reads as a
# nested mapping — or refuses outright.
HOSTILE = "src/app.py:1 — the call is `timeout: 600` here, and the overlay names it too"


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    root = tmp_path / "repo"
    root.mkdir()
    return Repo(root)


def harness(root: Path, name: str = "fake", body: str = "adapter: fake\n") -> Path:
    """A harness manifest under `.torve/harnesses/` (S-0061/D-2).

    A seat names the harness that reaches its model, so a fixture writing
    `tiers:` writes one of these too — the inline adapter it used to carry is
    refused, by name, with this file named in the refusal."""

    path = root / ".torve" / "harnesses" / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def context_for(repo: Repo, base: str = "main"):
    manifest = load_manifest(layout.gates_file(repo.root))
    return build_context(repo.root, manifest, base=base)


@pytest.fixture
def worktree(repo: Repo) -> Repo:
    """A repository with one task under work: a remote to pin against, a
    contract carrying a LOCKED decision, and a change inside its scope. The
    intake and the channel both need one, so it lives here."""

    repo.seed()
    repo.git("remote", "add", "origin", "git@github.com:morzecrew/torve.git")
    repo.task(base_task(allow=["src/**"], decisions=LOCKED_D1), None)
    repo.write("src/app.py", "print('changed')\n")
    repo.commit("the work")

    return repo


@pytest.fixture
def upstream():
    """(state, base_url) — a fake provider on loopback: reports a usage block
    and a cost, and records what it saw (authorization, path, request count)."""

    state: dict[str, object] = {
        "auth": [],
        "paths": [],
        "requests": 0,
        "usage": {"total_tokens": 5},
        "cost": 0.01,
        "body": None,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            state["body"] = body.decode("utf-8", errors="replace")
            state["auth"].append(self.headers.get("Authorization", ""))
            state["paths"].append(self.path)
            state["requests"] = int(state["requests"]) + 1
            payload = json.dumps(
                {
                    "usage": state["usage"],
                    "total_cost_usd": state["cost"],
                    "model": "fake-model-9",
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    yield state, f"http://127.0.0.1:{server.server_address[1]}"

    server.shutdown()
    server.server_close()


# ....................... #


def seam(body: str, monkeypatch) -> dict[str, str]:
    """A stand-in for the image's own `/opt/torve/equip` and `/opt/torve/run`
    (S-0063/D-1), for a test that has no image to put them in.

    The engine invokes two paths and knows nothing else about either, so a case
    that used to put a shell line on the tier hands that line to a `run` which
    is `sh -c "$TORVE_PROBE"`. Returns the `env` the seat carries, because the
    body travels the same channel every other knob does — which is the point
    being exercised, and is what a `command=` on the tier could not reach.
    """

    from torve.adapters.agent import harness as harness_mod

    monkeypatch.setattr(harness_mod, "EQUIP", "true")
    monkeypatch.setattr(harness_mod, "RUN", 'sh -c "$TORVE_PROBE"')

    return {"TORVE_PROBE": body}
