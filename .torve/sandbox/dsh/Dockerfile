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

# Harness identity (RFC 0017 §3, D-17.4): the headless profile's default
# model is deepseek-v4-flash; the department runs deepseek-chat. The pin is
# identity, so it rides the image — a different model is a new overlay, a
# new digest, a visible regime change. Permission mode stays an operator
# knob in the tier command (DSH_PERMISSION_MODE), never baked here.
COPY deepseek-chat.yml /opt/dsh/deepseek-chat.yml
