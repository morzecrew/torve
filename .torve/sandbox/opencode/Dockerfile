# Sandbox image for the opencode harness (RFC 0004 §1, thin per D-17.8):
# harness baked in, never injected; git for the workspace, python3 for the
# gate commands, uv for uv-driven acceptance batteries.
FROM node:24-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates python3 \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g opencode-ai \
 && opencode --version
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
