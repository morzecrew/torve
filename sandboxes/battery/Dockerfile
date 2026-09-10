# The engine repo's gates-side socket image (RFC 0017 §2a): the acceptance
# battery is uv-driven and its conformance tests drive containers, so the
# image carries git, uv and the docker CLI; the daemon is the host's,
# mounted by runtime.docker: socket. Thin per D-17.8 — no harness.
FROM python:3.13-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ADD https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz /tmp/docker.tgz
RUN tar -xzf /tmp/docker.tgz -C /tmp && mv /tmp/docker/docker /usr/local/bin/docker \
 && rm -rf /tmp/docker /tmp/docker.tgz
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# The lockfile-keyed dependency layer. `uv sync --all-extras
# --no-install-project` bakes pyproject.toml and uv.lock into the venv at
# the fixed UV_PROJECT_ENVIRONMENT, so an attempt's `uv run` reconciles
# the delta against a populated environment instead of rebuilding one.
# The keying is Docker's own layer cache — the copied lock bytes are part
# of the COPY'd context, so a lockfile change re-runs the sync and the
# digest change is the visible regime change. A corrupt staged lock fails
# the bake (--locked), never a stale warm layer.
#
# Guarded, because `torve sandbox build` sends the definition directory —
# and only it — as the build context, and the two project inputs are not
# definition files: the bake runs only when the context carries them.
# Build the warm image from a staged context — a directory holding this
# Dockerfile plus the repo's pyproject.toml and uv.lock, e.g.
#   mkdir ctx && cp pyproject.toml uv.lock .torve/sandbox/battery/Dockerfile ctx/ \
#     && docker build -t torve-agent:battery ctx
# An unstaged `torve sandbox build battery` builds exactly the thin image
# as before. The layer is a convenience, never a requirement: a stock
# image still works.
COPY . /opt/torve/build-context/
ENV UV_PROJECT_ENVIRONMENT=/opt/torve/project/.venv
RUN set -eu; \
    mkdir -p /opt/torve/project; \
    cd /opt/torve/build-context; \
    if [ -f pyproject.toml ] && [ -f uv.lock ]; then \
      cp pyproject.toml uv.lock /opt/torve/project/; \
      cd /opt/torve/project; \
      uv sync --locked --no-cache --all-extras --no-install-project; \
      uv sync --check --locked --no-cache --all-extras --no-install-project; \
    fi; \
    cd /; \
    rm -rf /opt/torve/build-context; \
    chmod -R a+rwX /opt/torve/project
