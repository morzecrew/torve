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
