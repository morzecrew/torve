# Sandbox image for the MiMo Code harness (RFC 0004 §1, thin per D-17.8):
# opencode lineage, identical run flags; harness baked in, never injected.
FROM node:24-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates python3 \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @mimo-ai/cli \
 && mimo --version
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
