# Sandbox image for the codex harness (RFC 0004 §1): harness baked in,
# never injected. The seat runs on the ChatGPT-subscription route (D-4.2):
# credentials live on a per-slot auth volume seeded by the operator from
# ~/.codex — the image carries none, and per the subscription's terms the
# credential is used only through the codex CLI itself.
ARG BASE=sandbox-base
FROM ${BASE}
# Pinned-default ARG (S-0033/D-3): a bump is a one-line reviewed diff, never
# a rebuild side effect.
ARG CODEX_VERSION=0.147.0
RUN npm install -g @openai/codex@${CODEX_VERSION} && codex --version

# uv for replaying tasks whose acceptance battery is uv-driven
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
