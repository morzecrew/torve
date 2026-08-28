# Sandbox image for harness-backed agents (RFC 0004 §1): the harness is baked
# into the image, never injected from the host. python:3.13-slim base keeps
# gate commands working; nodejs+npm carry the claude CLI.
FROM python:3.13-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates nodejs npm \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @anthropic-ai/claude-code \
 && claude --version

# uv for replaying tasks whose acceptance battery is uv-driven
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Harness plugins are identity (RFC 0017 §3, D-17.4): caveman and ponytail
# baked at pinned commits, enabled through a HOME seed the tier command
# copies into place — HOME is /tmp at run time and must stay writable by
# the invoking uid, so the image carries a seed, not a home. A plugin bump
# is a new digest, a visible regime change.
ARG CAVEMAN_REF=81536f57b330
ARG PONYTAIL_REF=v4.9.0
RUN mkdir -p /opt/claude-seed/.claude/plugins/marketplaces /opt/claude-seed/.claude/plugins/cache \
 && git clone --quiet https://github.com/JuliusBrussee/caveman /opt/claude-seed/.claude/plugins/marketplaces/caveman \
 && git -C /opt/claude-seed/.claude/plugins/marketplaces/caveman checkout --quiet "$CAVEMAN_REF" \
 && git clone --quiet https://github.com/DietrichGebert/ponytail /opt/claude-seed/.claude/plugins/marketplaces/ponytail \
 && git -C /opt/claude-seed/.claude/plugins/marketplaces/ponytail checkout --quiet "$PONYTAIL_REF" \
 && mkdir -p /opt/claude-seed/.claude/plugins/cache/caveman/caveman /opt/claude-seed/.claude/plugins/cache/ponytail/ponytail \
 && cp -r /opt/claude-seed/.claude/plugins/marketplaces/caveman /opt/claude-seed/.claude/plugins/cache/caveman/caveman/81536f57b330 \
 && cp -r /opt/claude-seed/.claude/plugins/marketplaces/ponytail /opt/claude-seed/.claude/plugins/cache/ponytail/ponytail/4.9.0
COPY seed-settings.json /opt/claude-seed/.claude/settings.json
COPY seed-installed.json /opt/claude-seed/.claude/plugins/installed_plugins.json
COPY seed-marketplaces.json /opt/claude-seed/.claude/plugins/known_marketplaces.json
RUN chmod -R a+rX /opt/claude-seed
