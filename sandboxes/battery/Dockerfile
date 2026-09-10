# The engine repo's gates-side socket image (RFC 0017 §2a): the acceptance
# battery is uv-driven and its conformance tests drive containers, so the
# image carries git, uv and the docker CLI; the daemon is the host's,
# mounted by runtime.docker: socket. Thin per D-17.8 — no harness.
ARG BASE=sandbox-base
FROM ${BASE}
ADD https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz /tmp/docker.tgz
RUN tar -xzf /tmp/docker.tgz -C /tmp && mv /tmp/docker/docker /usr/local/bin/docker \
 && rm -rf /tmp/docker /tmp/docker.tgz

# The lockfile-keyed dependency layer. `uv sync --all-extras
# --no-install-project` bakes pyproject.toml and uv.lock into the venv at
# the fixed UV_PROJECT_ENVIRONMENT, so an attempt's `uv run` reconciles
# the delta against a populated environment instead of rebuilding one.
# The keying is Docker's own layer cache — the copied lock bytes are part
# of the COPY'd context, so a lockfile change re-runs the sync and the
# digest change is the visible regime change. A corrupt staged lock fails
# the bake (--locked), never a stale warm layer.
#
# The context is the repository root (S-0063/D-8), so the two inputs are
# named and no guard is needed: a bake that cannot find them fails, which
# is what a missing lockfile should do rather than silently building a
# thinner image.
COPY pyproject.toml uv.lock /opt/torve/project/
ENV UV_PROJECT_ENVIRONMENT=/opt/torve/project/.venv
RUN set -eu; \
    cd /opt/torve/project; \
    uv sync --locked --no-cache --all-extras --no-install-project; \
    uv sync --check --locked --no-cache --all-extras --no-install-project; \
    chmod -R a+rwX /opt/torve/project
