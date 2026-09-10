# Sandbox image for the MiMo Code harness (S-0004, thin per S-0017/D-8):
# opencode lineage; harness baked in, never injected.
ARG BASE=sandbox-base
FROM ${BASE}
# The harness version rides a pinned-default ARG (S-0033/D-3): a bump is a
# one-line reviewed diff, never a rebuild side effect.
ARG MIMO_VERSION=0.1.13
RUN npm install -g @mimo-ai/cli@${MIMO_VERSION} && mimo --version
