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

# Harness plugins are identity (RFC 0017 §3, D-17.4): the clones are baked at
# pinned refs, because a fetch at dispatch would put the network inside every
# attempt. Which of them are installed and enabled is not baked — the runtime
# renders that from the profile's declaration when the sandbox exists (S-0061
# D-6), so the ref pinned here and the ref written into the bookkeeping cannot
# disagree, which is what they did while three JSON files were kept by hand
# beside these ARGs.
#
# The seed is a seed, not a home: HOME is /tmp at run time and must stay
# writable by the invoking uid, so the tier command copies this into place. It
# lives under the /opt/torve/ toolkit (RFC 0033 §5.3, D-33.4); the old
# /opt/claude-seed path stays as a symlink for one transition revision.
ARG CAVEMAN_REF=81536f57b330
ARG PONYTAIL_REF=v4.9.0
RUN set -eu; \
    seed=/opt/torve/seed/.claude/plugins; \
    mkdir -p "$seed/marketplaces" "$seed/cache"; \
    for spec in "caveman:JuliusBrussee/caveman:$CAVEMAN_REF" \
                "ponytail:DietrichGebert/ponytail:$PONYTAIL_REF"; do \
      name=${spec%%:*}; rest=${spec#*:}; repo=${rest%:*}; ref=${rest##*:}; \
      git clone --quiet "https://github.com/$repo" "$seed/marketplaces/$name"; \
      git -C "$seed/marketplaces/$name" checkout --quiet "$ref"; \
      mkdir -p "$seed/cache/$name/$name"; \
      cp -r "$seed/marketplaces/$name" "$seed/cache/$name/$name/$ref"; \
    done; \
    ln -s /opt/torve/seed /opt/claude-seed
# Readable by every uid, and the plugin directory writable by root alone —
# the runtime writes the rendered bookkeeping there as root before the agent
# runs, and nothing inside the attempt may edit what it was equipped with.
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
