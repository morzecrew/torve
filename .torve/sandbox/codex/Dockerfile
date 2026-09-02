# Sandbox image for the codex harness (RFC 0004 §1): harness baked in,
# never injected. The seat runs on the ChatGPT-subscription route (D-4.2):
# credentials live on a per-slot auth volume seeded by the operator from
# ~/.codex — the image carries none, and per the subscription's terms the
# credential is used only through the codex CLI itself.
FROM node:24-slim
# Pinned-default ARG (RFC 0033 §5.2, D-33.3): a bump is a one-line
# reviewed diff, never a rebuild side effect.
ARG CODEX_VERSION=0.147.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates python3 zstd \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @openai/codex@${CODEX_VERSION} \
 && codex --version

# uv for replaying tasks whose acceptance battery is uv-driven
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
