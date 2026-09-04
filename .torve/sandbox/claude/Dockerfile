# Sandbox image for harness-backed agents (RFC 0004 §1): the harness is baked
# into the image, never injected from the host. python:3.13-slim base keeps
# gate commands working; nodejs+npm carry the claude CLI.
FROM python:3.13-slim
# The harness version rides a pinned-default ARG (RFC 0033 §5.2, D-33.3): a
# bump is a one-line reviewed diff, never a rebuild side effect.
ARG CLAUDE_VERSION=2.1.252
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates nodejs npm \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @anthropic-ai/claude-code@${CLAUDE_VERSION} \
 && claude --version

# uv for replaying tasks whose acceptance battery is uv-driven
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Harness plugins are identity (RFC 0017 §3, D-17.4): caveman and ponytail
# baked at pinned commits, enabled through a HOME seed the tier command
# copies into place — HOME is /tmp at run time and must stay writable by
# the invoking uid, so the image carries a seed, not a home. A plugin bump
# is a new digest, a visible regime change. The seed lives under the
# /opt/torve/ toolkit (RFC 0033 §5.3, D-33.4) as /opt/torve/seed; the old
# /opt/claude-seed path stays as a symlink for one transition revision.
ARG CAVEMAN_REF=81536f57b330
ARG PONYTAIL_REF=v4.9.0
RUN mkdir -p /opt/torve/seed/.claude/plugins/marketplaces /opt/torve/seed/.claude/plugins/cache \
 && git clone --quiet https://github.com/JuliusBrussee/caveman /opt/torve/seed/.claude/plugins/marketplaces/caveman \
 && git -C /opt/torve/seed/.claude/plugins/marketplaces/caveman checkout --quiet "$CAVEMAN_REF" \
 && git clone --quiet https://github.com/DietrichGebert/ponytail /opt/torve/seed/.claude/plugins/marketplaces/ponytail \
 && git -C /opt/torve/seed/.claude/plugins/marketplaces/ponytail checkout --quiet "$PONYTAIL_REF" \
 && mkdir -p /opt/torve/seed/.claude/plugins/cache/caveman/caveman /opt/torve/seed/.claude/plugins/cache/ponytail/ponytail \
 && cp -r /opt/torve/seed/.claude/plugins/marketplaces/caveman /opt/torve/seed/.claude/plugins/cache/caveman/caveman/81536f57b330 \
 && cp -r /opt/torve/seed/.claude/plugins/marketplaces/ponytail /opt/torve/seed/.claude/plugins/cache/ponytail/ponytail/4.9.0 \
 && ln -s /opt/torve/seed /opt/claude-seed
COPY seed-settings.json /opt/torve/seed/.claude/settings.json
COPY seed-installed.json /opt/torve/seed/.claude/plugins/installed_plugins.json
COPY seed-marketplaces.json /opt/torve/seed/.claude/plugins/known_marketplaces.json
RUN chmod -R a+rX /opt/torve/seed

# The engine's own CLI, for the verbs an attempt calls from inside its
# sandbox: `torve log divergence` records a divergence through the run's
# channel, `torve log notes` reads what the engine had to say. Without it
# the prompt names commands the sandbox does not have.
#
# Its own interpreter and its own environment, under /opt/torve: the
# repository under work owns the workspace, and the intake must not depend
# on what that repository happens to install — or on it being a Python
# project at all. The interpreter is installed where every uid can read it,
# because a sandbox runs as the host's uid and uv's default install
# directory is root's home; the build proves it by running the verb as
# `nobody` rather than as the user that built it.
#
# Guarded like the battery's dependency layer, and for the same reason:
# `torve sandbox build` stages the project into the context, and a bare
# `docker build` of this directory still produces a working image without
# it.
#
# This block is byte-identical in every agent definition, copied from
# `.torve/sandbox/_torve-cli.dockerfile` and pinned against it by a test —
# edit that file, not this copy. A shared base image replaces the
# arrangement when the sandboxes are reworked.
COPY . /opt/torve/build-context/
ENV UV_PYTHON_INSTALL_DIR=/opt/torve/python
RUN set -eu; \
    if [ -f /opt/torve/build-context/pyproject.toml ] \
       && [ -f /opt/torve/build-context/uv.lock ]; then \
      uv python install 3.13; \
      uv venv --python 3.13 /opt/torve/cli; \
      UV_PROJECT_ENVIRONMENT=/opt/torve/cli \
        uv sync --locked --no-cache --no-dev --no-editable \
          --project /opt/torve/build-context; \
      ln -sf /opt/torve/cli/bin/torve /usr/local/bin/torve; \
      chmod -R a+rX /opt/torve/python /opt/torve/cli; \
      su -s /bin/sh -c "torve --version" nobody; \
    fi; \
    rm -rf /opt/torve/build-context
