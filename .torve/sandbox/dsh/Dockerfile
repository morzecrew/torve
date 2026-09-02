# Sandbox image for the dsh harness (RFC 0004 §1): harness baked in, never
# injected. node:24-slim satisfies dsh's Node >=24 requirement; git for the
# workspace, python3 kept for parity with gate commands.
FROM node:24-slim
# The harness version rides a pinned-default ARG (RFC 0033 §5.2, D-33.3): a
# bump is a one-line reviewed diff, never a rebuild side effect.
ARG DSH_VERSION=0.1.1-rc.2
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates python3 zstd \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @deepseek-ai/dsh@${DSH_VERSION} \
 && dsh --version

# uv for replaying tasks whose acceptance battery is uv-driven
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Harness identity (RFC 0017 §3, D-17.4): the headless profile's default
# model is deepseek-v4-flash; the department runs deepseek-chat. The pin is
# identity, so it rides the image — a different model is a new overlay, a
# new digest, a visible regime change. Permission mode stays an operator
# knob in the tier command (DSH_PERMISSION_MODE), never baked here.
# Overlays live under the /opt/torve/ toolkit (RFC 0033 §5.3, D-33.4); the
# /opt/dsh paths stay as symlinks for one transition revision.
COPY deepseek-chat.yml /opt/torve/overlays/deepseek-chat.yml
# qwen3.8-flash over OpenRouter: newer than the bundled pi-ai catalog, so
# the overlay declares the model as well as pinning it. NODE_USE_ENV_PROXY
# stays an operator knob in the tier command — node's fetch ignores the
# proxy environment without it, and whether a proxy exists is the host's
# business, never the image's.
COPY qwen3.8-flash.yml /opt/torve/overlays/qwen3.8-flash.yml
COPY brokered-deepseek.yml /opt/torve/overlays/brokered-deepseek.yml
COPY brokered-deepseek-v4-flash.yml /opt/torve/overlays/brokered-deepseek-v4-flash.yml
# The ModelStudio token-plan seats (2026-09-01): glm-5.2, deepseek-v4-pro
# and qwen3.8-max join the roster brokered; qwen3.8-flash above is
# repointed from OpenRouter to the same plan.
COPY brokered-glm-5.2.yml /opt/torve/overlays/brokered-glm-5.2.yml
COPY brokered-deepseek-v4-pro.yml /opt/torve/overlays/brokered-deepseek-v4-pro.yml
COPY brokered-qwen3.8-max.yml /opt/torve/overlays/brokered-qwen3.8-max.yml
# Usage extraction (D-4.6, self-reported regime): headless dsh prints no
# usage; the session log carries it. The reporter sums it and prints the
# one JSON line parse_metadata reads. Named /opt/torve/report-usage so the
# command template calls the utility directly, no node prefix.
COPY report-usage.js /opt/torve/report-usage
RUN mkdir -p /opt/dsh \
 && chmod +x /opt/torve/report-usage \
 && ln -s /opt/torve/report-usage /opt/dsh/report-usage.js \
 && ln -s /opt/torve/overlays/deepseek-chat.yml /opt/dsh/deepseek-chat.yml \
 && ln -s /opt/torve/overlays/qwen3.8-flash.yml /opt/dsh/qwen3.8-flash.yml \
 && ln -s /opt/torve/overlays/brokered-deepseek.yml /opt/dsh/brokered-deepseek.yml \
 && ln -s /opt/torve/overlays/brokered-deepseek-v4-flash.yml /opt/dsh/brokered-deepseek-v4-flash.yml \
 && ln -s /opt/torve/overlays/brokered-glm-5.2.yml /opt/dsh/brokered-glm-5.2.yml \
 && ln -s /opt/torve/overlays/brokered-deepseek-v4-pro.yml /opt/dsh/brokered-deepseek-v4-pro.yml \
 && ln -s /opt/torve/overlays/brokered-qwen3.8-max.yml /opt/dsh/brokered-qwen3.8-max.yml
