# Sandbox image for the MiMo Code harness (RFC 0004 §1, thin per D-17.8):
# opencode lineage, identical run flags; harness baked in, never injected.
FROM node:24-slim
# The harness version rides a pinned-default ARG (RFC 0033 §5.2, D-33.3): a
# bump is a one-line reviewed diff, never a rebuild side effect.
ARG MIMO_VERSION=0.1.13
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates python3 \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @mimo-ai/cli@${MIMO_VERSION} \
 && mimo --version
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
