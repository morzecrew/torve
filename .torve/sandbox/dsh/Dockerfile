# Sandbox image for the dsh harness (RFC 0004 §1): harness baked in, never
# injected. node:24-slim satisfies dsh's Node >=24 requirement; git for the
# workspace, python3 kept for parity with gate commands.
FROM node:24-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates python3 zstd \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @deepseek-ai/dsh@0.1.1-rc.2 \
 && dsh --version

# uv for replaying tasks whose acceptance battery is uv-driven
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Harness identity (RFC 0017 §3, D-17.4): the headless profile's default
# model is deepseek-v4-flash; the department runs deepseek-chat. The pin is
# identity, so it rides the image — a different model is a new overlay, a
# new digest, a visible regime change. Permission mode stays an operator
# knob in the tier command (DSH_PERMISSION_MODE), never baked here.
COPY deepseek-chat.yml /opt/dsh/deepseek-chat.yml
# qwen3.8-flash over OpenRouter: newer than the bundled pi-ai catalog, so
# the overlay declares the model as well as pinning it. NODE_USE_ENV_PROXY
# stays an operator knob in the tier command — node's fetch ignores the
# proxy environment without it, and whether a proxy exists is the host's
# business, never the image's.
COPY qwen3.8-flash.yml /opt/dsh/qwen3.8-flash.yml
COPY brokered-deepseek.yml /opt/dsh/brokered-deepseek.yml
COPY brokered-deepseek-v4-flash.yml /opt/dsh/brokered-deepseek-v4-flash.yml
# Usage extraction (D-4.6, self-reported regime): headless dsh prints no
# usage; the session log carries it. The reporter sums it and prints the
# one JSON line parse_metadata reads.
COPY report-usage.js /opt/dsh/report-usage.js
