# Sandbox image for the dsh harness (RFC 0004 §1): harness baked in, never
# injected. node:24-slim satisfies dsh's Node >=24 requirement; git for the
# workspace, python3 kept for parity with gate commands.
FROM node:24-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates python3 \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @deepseek-ai/dsh \
 && dsh --version

# uv for replaying tasks whose acceptance battery is uv-driven
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
