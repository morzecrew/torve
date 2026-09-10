# Sandbox image for the opencode harness (S-0004, thin per S-0017/D-8):
# harness baked in, never injected; everything else comes from the base.
ARG BASE=sandbox-base
FROM ${BASE}
RUN npm install -g opencode-ai && opencode --version
